"""Tools the agent can call, and the registry that executes them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

MAX_TOOL_OUTPUT_CHARS = 6000


@dataclass
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Any]

    def spec(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class ToolRegistry:
    """Named tools with JSON-schema inputs; results are JSON strings capped in size."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> List[str]:
        return list(self._tools)

    def specs(self) -> List[Dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool '{name}'. Available tools: {', '.join(self._tools)}", is_error=True)
        required = tool.input_schema.get("required", [])
        missing = [field for field in required if field not in (arguments or {})]
        if missing:
            return ToolResult(f"Missing required argument(s): {', '.join(missing)}", is_error=True)
        try:
            output = tool.handler(**(arguments or {}))
        except Exception as exc:  # the model sees the error and can adapt
            return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
        if len(text) > MAX_TOOL_OUTPUT_CHARS:
            text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n[truncated]"
        return ToolResult(text)


def build_document_tools(rag_service, vector_store, llm_service, scope_provider=None) -> ToolRegistry:
    """Default toolset over the indexed documents.

    `scope_provider()` returns the caller's mandatory (filters, access levels), so
    the agent can only see what the caller is allowed to see.
    """
    registry = ToolRegistry()

    def scope():
        filters, levels = scope_provider() if scope_provider else ({}, None)
        return {"filters": filters or None, "user_permissions": levels}

    def search_documents(query: str, top_k: int = 5, search_mode: str = None) -> List[Dict[str, Any]]:
        results, _ = rag_service.retrieve(query, top_k=max(1, min(int(top_k), 10)), search_mode=search_mode, **scope())
        return [
            {
                "chunk_id": r["id"],
                "document_id": r.get("document_id"),
                "source": r.get("source"),
                "section": (r.get("metadata") or {}).get("section"),
                "content": r.get("content", "")[:1200],
            }
            for r in results
        ]

    def get_document(document_id: str) -> Dict[str, Any]:
        document = vector_store.get_document(document_id, **scope())
        if document is None:
            raise LookupError(f"No document with id {document_id}")
        return {
            "document_id": document_id,
            "source": document.get("source"),
            "text": "\n".join(chunk["content"] for chunk in document["chunks"]),
        }

    def list_sources(size: int = 20) -> List[Dict[str, Any]]:
        return vector_store.list_documents(size=max(1, min(int(size), 100)), **scope())["documents"]

    def summarize_document(document_id: str, focus: str = "") -> str:
        text = get_document(document_id)["text"][:20000]
        instruction = f"Focus on: {focus}\n" if focus else ""
        return llm_service.generate(
            f"Summarize the following document in 5-8 bullet points. {instruction}Keep its language.\n\n{text}",
            temperature=0.2,
            max_tokens=800,
        )

    registry.register(
        Tool(
            "search_documents",
            "Semantic + keyword search over the indexed documents. Returns the most relevant passages with their "
            "document_id and source. Call it several times with different queries for multi-part questions.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for"},
                    "top_k": {"type": "integer", "description": "Number of passages (1-10)", "default": 5},
                    "search_mode": {"type": "string", "enum": ["dense", "sparse", "hybrid"]},
                },
                "required": ["query"],
            },
            search_documents,
        )
    )
    registry.register(
        Tool(
            "get_document",
            "Full text of one document, by the document_id returned by search_documents or list_sources.",
            {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]},
            get_document,
        )
    )
    registry.register(
        Tool(
            "list_sources",
            "List the indexed documents (id, source name, number of chunks).",
            {"type": "object", "properties": {"size": {"type": "integer", "default": 20}}},
            list_sources,
        )
    )
    registry.register(
        Tool(
            "summarize_document",
            "Bullet-point summary of one document, optionally focused on a topic.",
            {
                "type": "object",
                "properties": {"document_id": {"type": "string"}, "focus": {"type": "string"}},
                "required": ["document_id"],
            },
            summarize_document,
        )
    )
    return registry
