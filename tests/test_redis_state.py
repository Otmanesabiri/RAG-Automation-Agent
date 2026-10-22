"""Redis-backed sessions and rate limiting (shared across workers)."""

import fakeredis
import pytest

from src.api.app import create_app
from src.api.state import RedisSessionStore
from src.auth import rate_limiter
from src.auth.rate_limiter import RedisRateLimiter


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


def test_session_round_trip_and_ownership(redis_client):
    store = RedisSessionStore(redis_client, ttl_seconds=60)
    session_id = store.create_session(user_id="u1", metadata={"lang": "fr"}, owner="key:a")
    store.append_message(session_id, "user", "bonjour")

    session = store.get_session(session_id, owner="key:a")
    assert session["user_id"] == "u1"
    assert session["metadata"] == {"lang": "fr"}
    assert [m["content"] for m in session["messages"]] == ["bonjour"]
    assert store.get_session(session_id, owner="key:b") is None
    assert store.list_sessions() == [session_id]
    assert 0 < redis_client.ttl(f"rag:session:{session_id}") <= 60


def test_sessions_are_shared_between_store_instances(redis_client):
    worker_a = RedisSessionStore(redis_client)
    worker_b = RedisSessionStore(redis_client)
    session_id = worker_a.create_session(owner="key:a")
    worker_b.append_message(session_id, "user", "hi")
    assert len(worker_a.get_session(session_id, owner="key:a")["messages"]) == 1


def test_append_to_unknown_session_fails(redis_client):
    with pytest.raises(KeyError):
        RedisSessionStore(redis_client).append_message("missing", "user", "x")


def test_redis_rate_limit_is_shared(redis_client):
    worker_a = RedisRateLimiter(redis_client, requests_per_minute=3)
    worker_b = RedisRateLimiter(redis_client, requests_per_minute=3)
    results = [worker_a.is_allowed("c"), worker_b.is_allowed("c"), worker_a.is_allowed("c"), worker_b.is_allowed("c")]
    assert results == [True, True, True, False]
    assert worker_a.is_allowed("other")


def test_app_uses_redis_when_configured(env, redis_client, monkeypatch, fakes, auth_headers):
    env.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setattr("src.api.state.get_redis_client", lambda: redis_client)
    monkeypatch.setattr(rate_limiter, "get_redis_client", lambda: redis_client)

    app = create_app({"TESTING": True})
    app.extensions["rag_service_factory"] = lambda: fakes["rag_service"]
    client = app.test_client()

    body = client.post("/query", json={"query": "x"}, headers=auth_headers).get_json()
    assert redis_client.exists(f"rag:session:{body['metadata']['session_id']}")
    assert isinstance(rate_limiter.get_rate_limiter(), RedisRateLimiter)
