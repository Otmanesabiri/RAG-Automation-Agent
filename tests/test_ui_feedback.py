"""Chat UI page and answer feedback."""

import fakeredis
from prometheus_client import REGISTRY

from src.api.feedback import FeedbackStore


def test_ui_is_served_with_security_headers(client):
    response = client.get("/ui")
    assert response.status_code == 200
    assert "<title>RAG Assistant</title>" in response.get_data(as_text=True)
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    # Untrusted answers must be rendered as text, never as HTML.
    assert "innerHTML" not in response.get_data(as_text=True)


def test_feedback_is_recorded_and_listed(client, auth_headers):
    before = REGISTRY.get_sample_value("rag_agent_feedback_total", {"rating": "down"}) or 0.0
    payload = {"rating": "down", "session_id": "s1", "question": "q", "answer": "a", "comment": "wrong source"}
    assert client.post("/feedback", json=payload, headers=auth_headers).status_code == 201
    assert REGISTRY.get_sample_value("rag_agent_feedback_total", {"rating": "down"}) == before + 1

    entries = client.get("/feedback", headers=auth_headers).get_json()["feedback"]
    assert entries[0]["comment"] == "wrong source" and entries[0]["client_id"].startswith("key:")


def test_feedback_validation_and_auth(client, auth_headers):
    assert client.post("/feedback", json={"rating": "meh"}, headers=auth_headers).status_code == 422
    assert client.post("/feedback", json={"rating": "up"}).status_code == 401


def test_feedback_store_in_redis_keeps_newest_first():
    store = FeedbackStore(fakeredis.FakeRedis(), max_entries=2)
    for i in range(3):
        store.add({"rating": "up", "comment": str(i), "tenant": "a" if i else "b"})
    assert [e["comment"] for e in store.recent()] == ["2", "1"]
    assert [e["comment"] for e in store.recent(tenant="a")] == ["2", "1"]
