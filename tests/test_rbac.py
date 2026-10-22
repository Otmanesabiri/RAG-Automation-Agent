"""JWT identity, roles, tenant isolation, access levels and audit log."""

import hashlib
import json
import time
from unittest.mock import MagicMock

import jwt
import pytest
from loguru import logger

from src.auth.identity import Principal, principal_for_api_key, principal_for_jwt, search_scope
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore

SECRET = "test-jwt-secret-with-enough-length-0123456789"


@pytest.fixture
def jwt_env(env):
    env.setenv("JWT_SECRET", SECRET)
    env.setenv("JWT_AUDIENCE", "rag-agent")
    env.setenv("MULTI_TENANT", "true")
    return env


def _token(**claims):
    payload = {"sub": "user-1", "aud": "rag-agent", "exp": int(time.time()) + 300, "tenant": "acme", **claims}
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _bearer(**claims):
    return {"Authorization": f"Bearer {_token(**claims)}"}


def test_jwt_claims_map_to_principal(jwt_env):
    principal = principal_for_jwt(_token(roles=["editor"], access_levels="public internal"))
    assert principal.tenant == "acme"
    assert principal.roles == ("editor",)
    assert principal.access_levels == ("public", "internal")
    assert principal.client_id.startswith("jwt:") and principal.subject == "user-1"


def test_nested_roles_claim(jwt_env):
    jwt_env.setenv("JWT_ROLES_CLAIM", "realm_access.roles")
    assert principal_for_jwt(_token(realm_access={"roles": ["admin"]})).roles == ("admin",)


@pytest.mark.parametrize(
    "claims",
    [{"exp": int(time.time()) - 10}, {"aud": "other-service"}, {"tenant": None}],
)
def test_invalid_tokens_are_rejected(client, jwt_env, claims):
    assert client.post("/query", json={"query": "q"}, headers=_bearer(**claims)).status_code == 401


def test_bad_signature_is_rejected(client, jwt_env):
    forged = jwt.encode(
        {"sub": "x", "aud": "rag-agent", "exp": int(time.time()) + 60, "tenant": "acme"}, "x" * 40, algorithm="HS256"
    )
    assert client.post("/query", json={"query": "q"}, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_roles_gate_endpoints(client, jwt_env):
    doc = {"source": "upload", "documents": [{"content": "x", "filename": "a.txt"}]}
    assert client.post("/query", json={"query": "q"}, headers=_bearer()).status_code == 200  # reader by default
    assert client.post("/ingest", json=doc, headers=_bearer()).status_code == 403
    assert client.post("/ingest", json=doc, headers=_bearer(roles=["editor"])).status_code == 202
    assert client.delete("/documents/x", headers=_bearer(roles=["editor"])).status_code == 403
    assert client.delete("/documents/x", headers=_bearer(roles=["admin"])).status_code == 404
    assert client.post("/connectors/any/sync", headers=_bearer(roles=["editor"])).status_code == 403


def test_tenant_is_stamped_by_the_server(client, jwt_env, fakes):
    doc = {"source": "upload", "documents": [{"content": "x", "filename": "a.txt", "metadata": {"tenant": "evil"}}]}
    body = client.post("/ingest", json=doc, headers=_bearer(roles=["editor"])).get_json()
    assert fakes["vector_store"].indexed[0].metadata["tenant"] == "acme"
    assert body["documents"][0]["document_id"] == ElasticsearchVectorStore.document_id_for("a.txt", "acme")


def test_query_is_restricted_to_tenant_and_access_levels(client, jwt_env, fakes):
    payload = {"query": "q", "filters": {"attributes": {"category": "hr", "tenant": "other"}}}
    client.post("/query", json=payload, headers=_bearer(access_levels=["internal"]))
    call = fakes["rag_service"].calls[-1]
    assert call["filters"] == {"category": "hr", "tenant": "acme"}
    assert call["user_permissions"] == ["internal"]


def test_document_endpoints_pass_the_scope(client, jwt_env, fakes):
    client.get("/documents", headers=_bearer(access_levels=["public"]))
    assert fakes["vector_store"].last_scope == {"filters": {"tenant": "acme"}, "user_permissions": ["public"]}


def test_api_keys_default_to_admin_and_can_be_scoped(env):
    assert principal_for_api_key("key:x", "k").has_role("admin")
    digest = hashlib.sha256(b"k2").hexdigest()
    env.setenv("API_KEY_SCOPES", json.dumps({digest: {"tenant": "beta", "roles": ["reader"], "access_levels": ["public"]}}))
    scoped = principal_for_api_key("key:y", "k2")
    assert (scoped.tenant, scoped.roles, scoped.access_levels) == ("beta", ("reader",), ("public",))
    assert not scoped.has_role("editor")


def test_search_scope(env):
    admin = Principal(client_id="c")
    assert search_scope(admin) == ({}, None)
    env.setenv("MULTI_TENANT", "true")
    assert search_scope(Principal(client_id="c", tenant="t", access_levels=("a",))) == ({"tenant": "t"}, ["a"])


def test_vector_store_scopes_document_queries():
    store = ElasticsearchVectorStore("idx", MagicMock(), client=MagicMock())
    store.client.search.return_value = {"hits": {"hits": []}}
    store.get_document("d1", filters={"tenant": "acme"}, user_permissions=["public"])
    query = store.client.search.call_args.kwargs["query"]
    assert query["bool"]["filter"][0] == {"term": {"document_id": "d1"}}
    clauses = query["bool"]["filter"][1]["bool"]["must"]
    assert {"term": {"metadata.tenant": "acme"}} in clauses
    assert any("should" in c.get("bool", {}) for c in clauses)  # access-level clause

    store.client.delete_by_query.return_value = {"deleted": 0}
    store.delete_document("d1")
    assert store.client.delete_by_query.call_args.kwargs["query"] == {"term": {"document_id": "d1"}}


def test_document_ids_differ_per_tenant():
    assert ElasticsearchVectorStore.document_id_for("a.txt", "t1") != ElasticsearchVectorStore.document_id_for("a.txt", "t2")
    assert ElasticsearchVectorStore.document_id_for("a.txt") == ElasticsearchVectorStore.document_id_for("a.txt", None)


def test_audit_entries(client, jwt_env):
    entries = []
    sink = logger.add(lambda message: entries.append(message.record), filter=lambda r: r["extra"].get("audit"))
    try:
        client.post("/query", json={"query": "q"}, headers=_bearer())
    finally:
        logger.remove(sink)
    entry = json.loads(entries[-1]["message"])
    assert entry["action"] == "query" and entry["tenant"] == "acme" and entry["subject"] == "user-1"
