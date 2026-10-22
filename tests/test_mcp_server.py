"""MCP server tools (against a fake API client) and the /search endpoint."""

import asyncio
import json

import pytest

from src.mcp_server.server import RAGApiClient, create_server


class _FakeApi:
    def __init__(self):
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/search":
            return {"results": [{"source": "faq.md", "content": "Refunds within 30 days", "document_id": "d1"}]}
        if path.startswith("/documents/"):
            return {"source": "faq.md", "chunks": [{"content": "one"}, {"content": "two"}]}
        if path == "/documents":
            return {"documents": [{"document_id": "d1"}], "next": None}
        if path == "/query":
            return {"answer": "30 days", "sources": [{"source": "faq.md"}]}
        if path == "/ingest":
            return {"documents": [{"document_id": "d2", "source": kwargs["json"]["documents"][0]["url"]}]}
        raise AssertionError(path)


def _call(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    content = result.content if hasattr(result, "content") else result[0]
    return json.loads(content[0].text)


@pytest.fixture
def server_and_api():
    api = _FakeApi()
    return create_server(api), api


def test_tools_are_listed_with_annotations(server_and_api):
    server, _ = server_and_api
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {"search_documents", "ask_documents", "get_document", "list_documents", "ingest_url"}
    assert tools["search_documents"].annotations.read_only_hint is True
    assert tools["ingest_url"].annotations.read_only_hint is False


def test_tools_call_the_api(server_and_api):
    server, api = server_and_api
    assert _call(server, "search_documents", {"query": "refund", "top_k": 50})[0]["source"] == "faq.md"
    assert api.calls[-1] == ("POST", "/search", {"json": {"query": "refund", "top_k": 20, "search_mode": "hybrid"}})
    assert _call(server, "get_document", {"document_id": "a/b"})["text"] == "one\ntwo"
    assert api.calls[-1][1] == "/documents/a%2Fb"
    assert _call(server, "ask_documents", {"question": "refund?"})["answer"] == "30 days"
    assert _call(server, "ingest_url", {"url": "https://example.com/p"})[0]["document_id"] == "d2"


def test_client_auth_headers(monkeypatch):
    monkeypatch.setenv("RAG_API_KEY", "k")
    assert RAGApiClient().session.headers["X-API-Key"] == "k"
    monkeypatch.setenv("RAG_API_TOKEN", "a.b.c")
    assert RAGApiClient().session.headers["Authorization"] == "Bearer a.b.c"


def test_search_endpoint(client, auth_headers, fakes):
    def retrieve(question, **kwargs):
        fakes["rag_service"].calls.append({"question": question, **kwargs})
        return [
            {
                "id": "c1",
                "document_id": "d1",
                "source": "faq.md",
                "content": "text",
                "score": 0.5,
                "metadata": {"section": "S"},
            }
        ], False

    fakes["rag_service"].retrieve = retrieve
    body = client.post("/search", json={"query": "q", "search_mode": "sparse"}, headers=auth_headers).get_json()
    assert body["results"][0] == {
        "chunk_id": "c1",
        "document_id": "d1",
        "source": "faq.md",
        "section": "S",
        "score": 0.5,
        "content": "text",
    }
    assert fakes["rag_service"].calls[-1]["search_mode"] == "sparse"
    assert client.post("/search", json={"query": "q"}).status_code == 401
