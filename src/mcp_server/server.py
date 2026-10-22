"""MCP server exposing the document search of a running RAG Automation Agent API.

It is a thin client of the REST API, so the API's authentication, roles,
tenant isolation and rate limits apply unchanged. Configure with:

- RAG_API_URL: base URL of the API (default http://localhost:8000)
- RAG_API_KEY: an API key, or RAG_API_TOKEN: a JWT
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

INSTRUCTIONS = (
    "Search and read the organisation's indexed documents. Use search_documents first, then get_document "
    "for the full text of a promising result. Cite sources by their 'source' field."
)


class RAGApiClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, token: Optional[str] = None) -> None:
        self.base_url = (base_url or os.getenv("RAG_API_URL", "http://localhost:8000")).rstrip("/")
        self.session = requests.Session()
        api_key = api_key or os.getenv("RAG_API_KEY")
        token = token or os.getenv("RAG_API_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif api_key:
            self.session.headers["X-API-Key"] = api_key
        self.timeout = float(os.getenv("RAG_API_TIMEOUT", "120"))

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            response = self.session.request(method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise ToolError(f"RAG API unreachable at {self.base_url}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                message = response.text
            # ToolError messages are shown to the client (other exceptions are hidden)
            raise ToolError(f"RAG API {response.status_code}: {message}")
        return response.json()


def create_server(client: Optional[RAGApiClient] = None) -> MCPServer:
    api = client or RAGApiClient()
    server = MCPServer("rag-automation-agent", instructions=INSTRUCTIONS)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def search_documents(query: str, top_k: int = 5, search_mode: str = "hybrid") -> str:
        """Search the indexed documents (hybrid keyword + semantic). Returns matching passages with
        their source and document_id. search_mode: hybrid, dense or sparse."""
        body = {"query": query, "top_k": max(1, min(top_k, 20)), "search_mode": search_mode}
        result = api.request("POST", "/search", json=body)
        return json.dumps(result["results"], ensure_ascii=False)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def ask_documents(question: str) -> str:
        """Ask a question; the server retrieves passages and answers with citations."""
        result = api.request("POST", "/query", json={"query": question})
        return json.dumps({"answer": result["answer"], "sources": result.get("sources", [])}, ensure_ascii=False)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def get_document(document_id: str) -> str:
        """Full text of a document, by the document_id returned by search_documents or list_documents."""
        document = api.request("GET", f"/documents/{requests.utils.quote(document_id, safe='')}")
        text = "\n".join(chunk["content"] for chunk in document["chunks"])
        return json.dumps({"document_id": document_id, "source": document.get("source"), "text": text}, ensure_ascii=False)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_documents(size: int = 20) -> str:
        """List indexed documents (document_id, source, number of chunks)."""
        return json.dumps(api.request("GET", "/documents", params={"size": max(1, min(size, 100))}), ensure_ascii=False)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    def ingest_url(url: str) -> str:
        """Download a public web page or file and add it to the index (requires the editor role)."""
        result = api.request("POST", "/ingest", json={"source": "url", "documents": [{"url": url}]})
        return json.dumps(result["documents"], ensure_ascii=False)

    return server


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    create_server().run(transport=transport)


if __name__ == "__main__":
    main()
