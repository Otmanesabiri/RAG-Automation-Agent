"""Startup configuration checks and HTTP hardening."""

import pytest

from src.api.app import create_app
from src.config import ConfigurationError, validate_settings

SAFE = {
    "FLASK_ENV": "production",
    "SECRET_KEY": "a-real-random-secret",
    "AUTH_ENABLED": "true",
    "API_KEYS": "k1",
    "ELASTICSEARCH_PASSWORD": "s3cret",
}


@pytest.fixture
def prod_env(monkeypatch):
    for key, value in SAFE.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_safe_production_settings_pass(prod_env):
    validate_settings()


@pytest.mark.parametrize(
    "key,value",
    [
        ("SECRET_KEY", "changeme-in-production"),
        ("API_KEYS", ""),
        ("AUTH_ENABLED", "false"),
        ("ELASTICSEARCH_PASSWORD", "changeme"),
    ],
)
def test_unsafe_production_settings_fail(prod_env, key, value):
    prod_env.setenv(key, value)
    with pytest.raises(ConfigurationError):
        validate_settings()


def test_development_skips_checks(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "changeme")
    validate_settings()


def test_cors_disabled_by_default(env):
    env.delenv("CORS_ORIGINS", raising=False)
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/metrics", headers={"Origin": "https://evil.test"})
    assert "Access-Control-Allow-Origin" not in response.headers


def test_cors_allows_configured_origin(env):
    env.setenv("CORS_ORIGINS", "https://app.example.com")
    client = create_app({"TESTING": True}).test_client()
    allowed = client.get("/metrics", headers={"Origin": "https://app.example.com"})
    denied = client.get("/metrics", headers={"Origin": "https://evil.test"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_internal_errors_do_not_leak_details(app, client, auth_headers, fakes):
    def boom(*args, **kwargs):
        raise RuntimeError("connection to 10.0.0.5:9200 failed with password=hunter2")

    fakes["rag_service"].query = boom
    response = client.post("/query", json={"query": "x"}, headers=auth_headers)
    assert response.status_code == 500
    assert "hunter2" not in response.get_data(as_text=True)
