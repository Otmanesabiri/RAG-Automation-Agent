"""Tool-calling agent: registry, both backends and the /agent endpoint."""

import json
from types import SimpleNamespace

from src.agent import AgentRunner, Tool, ToolRegistry, build_document_tools
from src.agent.runner import AnthropicToolBackend, JSONToolBackend
from src.llm.service import AnthropicLLMProvider


def _registry():
    registry = ToolRegistry()
    registry.register(
        Tool(
            "search_documents",
            "search",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            lambda query: [{"source": "faq.md", "content": f"result for {query}"}],
        )
    )
    registry.register(Tool("boom", "fails", {"type": "object", "properties": {}}, lambda: 1 / 0))
    return registry


def test_registry_executes_and_reports_errors():
    registry = _registry()
    assert json.loads(registry.execute("search_documents", {"query": "x"}).content)[0]["source"] == "faq.md"
    assert registry.execute("search_documents", {}).is_error
    assert registry.execute("nope", {}).is_error
    failure = registry.execute("boom", {})
    assert failure.is_error and "ZeroDivisionError" in failure.content


class _ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_json_backend_calls_tools_then_answers():
    llm = _ScriptedLLM(
        [
            'Sure: {"tool": "search_documents", "input": {"query": "refund policy"}}',
            "not json at all",
            '{"answer": "Premium customers get a refund within 30 days [faq.md]."}',
        ]
    )
    result = JSONToolBackend(llm).run("Can I get a refund?", _registry(), max_iterations=5)

    assert result.answer.startswith("Premium customers")
    assert [step.tool for step in result.steps] == ["search_documents"]
    assert result.iterations == 3
    assert "result for refund policy" in llm.prompts[1]
    assert "not a valid JSON object" in llm.prompts[2]


def test_json_backend_stops_at_iteration_budget():
    llm = _ScriptedLLM(['{"tool": "search_documents", "input": {"query": "x"}}'] * 3)
    result = JSONToolBackend(llm).run("q", _registry(), max_iterations=3)
    assert result.stopped_reason == "max_iterations"
    assert len(result.steps) == 3


def _block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # Snapshot the conversation as sent (the list is mutated afterwards).
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def _anthropic(responses):
    provider = AnthropicLLMProvider.__new__(AnthropicLLMProvider)
    provider.model = "claude-opus-5-5"
    messages = _FakeMessages(responses)
    provider.client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return provider, messages


def test_anthropic_backend_runs_native_tool_loop():
    provider, messages = _anthropic(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                usage=None,
                content=[
                    _block("thinking", thinking=""),
                    _block("tool_use", id="t1", name="search_documents", input={"query": "refunds"}),
                    _block("tool_use", id="t2", name="boom", input={}),
                ],
            ),
            SimpleNamespace(
                stop_reason="end_turn", usage=None, content=[_block("text", text="Refunds within 30 days [faq.md].")]
            ),
        ]
    )
    result = AnthropicToolBackend(provider).run("refund?", _registry(), max_iterations=4)

    assert result.answer == "Refunds within 30 days [faq.md]."
    assert [(s.tool, s.is_error) for s in result.steps] == [("search_documents", False), ("boom", True)]
    first, second = messages.calls
    assert {tool["name"] for tool in first["tools"]} == {"search_documents", "boom"}
    assert first["fallbacks"] == "default" and "tool_choice" not in first
    tool_results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in tool_results] == ["t1", "t2"]  # one user message with both results
    assert tool_results[1]["is_error"] is True
    assert second["messages"][-2]["content"][0].type == "thinking"  # assistant content kept unchanged


def test_runner_picks_backend_by_provider():
    provider, _ = _anthropic([])
    assert isinstance(AgentRunner(SimpleNamespace(provider=provider), _registry()).backend, AnthropicToolBackend)
    assert isinstance(AgentRunner(SimpleNamespace(provider=object()), _registry()).backend, JSONToolBackend)


def test_document_tools_use_the_rag_stack():
    class _Rag:
        def retrieve(self, query, top_k, search_mode=None, **scope):
            return [
                {"id": "c1", "document_id": "d1", "source": "a.md", "content": "text", "metadata": {"section": "S"}}
            ], False

    class _Store:
        def get_document(self, document_id, **scope):
            return {"source": "a.md", "chunks": [{"content": "one"}, {"content": "two"}]} if document_id == "d1" else None

        def list_documents(self, size, **scope):
            return {"documents": [{"document_id": "d1"}]}

    registry = build_document_tools(_Rag(), _Store(), _ScriptedLLM(["- summary"]))
    assert json.loads(registry.execute("search_documents", {"query": "q"}).content)[0]["section"] == "S"
    assert json.loads(registry.execute("get_document", {"document_id": "d1"}).content)["text"] == "one\ntwo"
    assert registry.execute("get_document", {"document_id": "zz"}).is_error
    assert registry.execute("summarize_document", {"document_id": "d1"}).content == "- summary"
    assert json.loads(registry.execute("list_sources", {}).content) == [{"document_id": "d1"}]


def test_agent_endpoint(app, client, auth_headers):
    class _Runner:
        def run(self, question, max_iterations=None, history=None):
            self.history = history
            return SimpleNamespace(
                answer=f"answer to {question}",
                steps=[],
                iterations=1,
                as_dict=lambda: {
                    "answer": f"answer to {question}",
                    "steps": [],
                    "iterations": 1,
                    "stopped_reason": "answered",
                },
            )

    runner = _Runner()
    app.extensions["agent_runner_factory"] = lambda: runner
    body = client.post("/agent", json={"question": "hello"}, headers=auth_headers).get_json()
    assert body["answer"] == "answer to hello" and body["session_id"]

    client.post("/agent", json={"question": "again", "session_id": body["session_id"]}, headers=auth_headers)
    assert [m["content"] for m in runner.history] == ["hello", "answer to hello"]
    assert client.post("/agent", json={"question": "x"}).status_code == 401
    assert client.post("/agent", json={"question": "x", "max_iterations": 99}, headers=auth_headers).status_code == 422
