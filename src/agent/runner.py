"""Multi-step agent: the LLM decides which tools to call until it can answer.

Two backends share the same tools and trace format:
- AnthropicToolBackend uses Claude's native tool use.
- JSONToolBackend works with any provider: the model replies with a JSON
  action at every step.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..llm.service import AnthropicLLMProvider, LLMRefusalError
from ..observability.metrics import stage_timer
from .tools import ToolRegistry

SYSTEM_PROMPT = """You are a research assistant answering questions from an indexed document collection.
Use the tools to find evidence before answering; search again with other wording if the first results are not relevant.
Base the answer only on what the tools returned, cite sources as [source name], and say plainly when the documents do not contain the answer.
Answer in the language of the question."""

PREVIEW_CHARS = 300


@dataclass
class AgentStep:
    tool: str
    input: Dict[str, Any]
    output_preview: str
    is_error: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "input": self.input, "output_preview": self.output_preview, "is_error": self.is_error}


@dataclass
class AgentResult:
    answer: str
    steps: List[AgentStep] = field(default_factory=list)
    iterations: int = 0
    stopped_reason: str = "answered"  # answered | max_iterations | max_tokens

    def as_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "steps": [step.as_dict() for step in self.steps],
            "iterations": self.iterations,
            "stopped_reason": self.stopped_reason,
        }


def _history_messages(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """Previous session turns as plain user/assistant text messages (last 6)."""
    messages = []
    for message in (history or [])[-6:]:
        role = "assistant" if message.get("role") == "assistant" else "user"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + message.get("content", "")
        else:
            messages.append({"role": role, "content": message.get("content", "")})
    if messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    return messages


class AnthropicToolBackend:
    """Manual tool-use loop on Claude, keeping a per-step trace and an iteration budget."""

    def __init__(self, provider: AnthropicLLMProvider) -> None:
        self.provider = provider

    def run(self, question: str, registry: ToolRegistry, max_iterations: int, history=None) -> AgentResult:
        messages: List[Dict[str, Any]] = [*_history_messages(history), {"role": "user", "content": question}]
        if len(messages) > 1 and messages[-2]["role"] == "user":
            messages[-2:] = [{"role": "user", "content": messages[-2]["content"] + "\n\n" + question}]
        result = AgentResult(answer="")
        for iteration in range(1, max_iterations + 1):
            result.iterations = iteration
            with stage_timer("agent_llm"):
                response = self.provider.client.beta.messages.create(
                    model=self.provider.model,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    tools=registry.specs(),
                    messages=messages,
                    betas=[self.provider.FALLBACK_BETA],
                    fallbacks="default",
                )
            self.provider._record_usage(response)
            if response.stop_reason == "refusal":
                raise LLMRefusalError("The model declined to answer this request")

            # Keep the full content (including thinking blocks) so the history stays valid.
            messages.append({"role": "assistant", "content": response.content})
            text = "".join(block.text for block in response.content if block.type == "text")
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                result.answer = text
                result.stopped_reason = "max_tokens" if response.stop_reason == "max_tokens" else "answered"
                return result

            tool_results = []
            for block in (b for b in response.content if b.type == "tool_use"):
                with stage_timer("agent_tool"):
                    outcome = registry.execute(block.name, dict(block.input or {}))
                result.steps.append(
                    AgentStep(block.name, dict(block.input or {}), outcome.content[:PREVIEW_CHARS], outcome.is_error)
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": outcome.content, "is_error": outcome.is_error}
                )
            # All results of one turn go back in a single user message.
            messages.append({"role": "user", "content": tool_results})

        result.stopped_reason = "max_iterations"
        result.answer = result.answer or "I could not complete the research within the allowed number of steps."
        return result


JSON_AGENT_TEMPLATE = """{system}

You can call these tools:
{tools}

Reply with exactly one JSON object and nothing else:
- to call a tool: {{"tool": "<tool name>", "input": {{...}}}}
- to finish: {{"answer": "<final answer>"}}
{history}
Question: {question}

{transcript}Your JSON reply:"""


class JSONToolBackend:
    """Provider-agnostic loop: the model picks a tool (or answers) as JSON at each step."""

    def __init__(self, llm_service) -> None:
        self.llm_service = llm_service

    def run(self, question: str, registry: ToolRegistry, max_iterations: int, history=None) -> AgentResult:
        tools = "\n".join(
            f"- {spec['name']}: {spec['description']} Input schema: {json.dumps(spec['input_schema'])}"
            for spec in registry.specs()
        )
        history_text = "".join(f"{m['role'].title()}: {m['content']}\n" for m in _history_messages(history))
        history_block = f"\nConversation so far:\n{history_text}" if history_text else ""
        transcript = ""
        result = AgentResult(answer="")
        for iteration in range(1, max_iterations + 1):
            result.iterations = iteration
            prompt = JSON_AGENT_TEMPLATE.format(
                system=SYSTEM_PROMPT, tools=tools, history=history_block, question=question, transcript=transcript
            )
            with stage_timer("agent_llm"):
                reply = self.llm_service.generate(prompt, temperature=0.0, max_tokens=2000)
            action = _parse_action(reply)
            if action is None:
                transcript += "Observation: your reply was not a valid JSON object. Reply with JSON only.\n\n"
                continue
            if "answer" in action:
                result.answer = str(action["answer"])
                return result
            name, arguments = str(action.get("tool", "")), action.get("input") or {}
            with stage_timer("agent_tool"):
                outcome = registry.execute(name, arguments if isinstance(arguments, dict) else {})
            result.steps.append(AgentStep(name, arguments, outcome.content[:PREVIEW_CHARS], outcome.is_error))
            label = "Error" if outcome.is_error else "Result"
            transcript += f"Action: {json.dumps(action, ensure_ascii=False)}\n{label}: {outcome.content}\n\n"

        result.stopped_reason = "max_iterations"
        result.answer = "I could not complete the research within the allowed number of steps."
        return result


def _parse_action(reply: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        return None
    try:
        action = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return action if isinstance(action, dict) and ("answer" in action or "tool" in action) else None


class AgentRunner:
    """Pick the backend for the configured LLM provider and run the agent."""

    def __init__(self, llm_service, registry: ToolRegistry, *, max_iterations: Optional[int] = None) -> None:
        self.registry = registry
        self.max_iterations = max_iterations or int(os.getenv("AGENT_MAX_ITERATIONS", "6"))
        provider = getattr(llm_service, "provider", None)
        if isinstance(provider, AnthropicLLMProvider):
            self.backend = AnthropicToolBackend(provider)
        else:
            self.backend = JSONToolBackend(llm_service)

    def run(self, question: str, *, max_iterations: Optional[int] = None, history=None) -> AgentResult:
        limit = min(max_iterations or self.max_iterations, 15)
        return self.backend.run(question, self.registry, limit, history=history)
