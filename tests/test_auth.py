"""Authentication, rate limiting and session ownership."""

from src.auth import rate_limiter


def test_missing_key_is_rejected(client):
    response = client.post("/query", json={"query": "hello"})
    assert response.status_code == 401


def test_invalid_key_is_rejected(client):
    response = client.post("/query", json={"query": "hello"}, headers={"X-API-Key": "nope"})
    assert response.status_code == 403


def test_valid_key_is_accepted(client, auth_headers):
    response = client.post("/query", json={"query": "hello"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.get_json()["answer"] == "answer to hello"


def test_invalid_keys_do_not_get_their_own_rate_limit_bucket(client, auth_headers, env):
    env.setenv("RATE_LIMIT_RPM", "2")
    env.setattr(rate_limiter, "_global_limiter", None)

    for i in range(5):
        client.post("/query", json={"query": "x"}, headers={"X-API-Key": f"random-{i}"})

    # Rejected requests never reached the limiter, so the real client still has its quota.
    assert client.post("/query", json={"query": "x"}, headers=auth_headers).status_code == 200
    assert client.post("/query", json={"query": "x"}, headers=auth_headers).status_code == 200
    assert client.post("/query", json={"query": "x"}, headers=auth_headers).status_code == 429


def test_sessions_require_auth(client):
    assert client.post("/sessions", json={}).status_code == 401
    assert client.get("/sessions/whatever").status_code == 401


def test_session_is_private_to_its_owner(client, auth_headers, env):
    env.setenv("API_KEYS", "test-key-123,other-key")
    session_id = client.post("/sessions", json={}, headers=auth_headers).get_json()["session_id"]

    assert client.get(f"/sessions/{session_id}", headers=auth_headers).status_code == 200
    other = {"X-API-Key": "other-key"}
    assert client.get(f"/sessions/{session_id}", headers=other).status_code == 404
    assert client.post("/query", json={"query": "x", "session_id": session_id}, headers=other).status_code == 404


def test_unknown_session_id_is_rejected(client, auth_headers):
    response = client.post("/query", json={"query": "x", "session_id": "does-not-exist"}, headers=auth_headers)
    assert response.status_code == 404


def test_query_records_conversation(client, auth_headers):
    body = client.post("/query", json={"query": "first"}, headers=auth_headers).get_json()
    session_id = body["metadata"]["session_id"]
    client.post("/query", json={"query": "second", "session_id": session_id}, headers=auth_headers)

    messages = client.get(f"/sessions/{session_id}", headers=auth_headers).get_json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
