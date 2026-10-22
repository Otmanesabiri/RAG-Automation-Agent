"""Digests, watches, structured extraction, notifications and signed webhooks."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from src.automations import Notifier, WatchStore, build_digest, extract, run_watches, validate, verify_webhook_signature
from src.connectors import JobManager

NOW = datetime.now(timezone.utc)


class _Store:
    def __init__(self, documents):
        self.documents = documents

    def list_documents(self, size, after=None, **scope):
        return {"documents": self.documents, "next": None}

    def get_document(self, document_id, **scope):
        return {"source": "x", "chunks": [{"content": f"content of {document_id}"}]}


class _LLM:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "A short summary."


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body, data=None):
        self.sent.append((subject, body, data))
        return ["webhook"]


def test_digest_lists_only_new_documents():
    store = _Store(
        [
            {"document_id": "old", "source": "old.md", "chunks": 1, "indexed_at": (NOW - timedelta(days=3)).isoformat()},
            {"document_id": "new", "source": "new.md", "chunks": 4, "indexed_at": (NOW - timedelta(hours=1)).isoformat()},
        ]
    )
    llm = _LLM()
    digest = build_digest(store, llm, since=NOW - timedelta(days=1))
    assert [d["document_id"] for d in digest["documents"]] == ["new"]
    assert "**new.md** (4 chunks)" in digest["body"] and "A short summary." in digest["body"]
    assert "content of new" in llm.prompts[0]


def test_empty_digest_makes_no_llm_call():
    llm = _LLM()
    assert build_digest(_Store([]), llm)["documents"] == []
    assert llm.prompts == []


@pytest.mark.parametrize("redis_client", [None, fakeredis.FakeRedis()])
def test_watch_store(redis_client):
    store = WatchStore(redis_client)
    watch = store.create("acme", "GDPR", "données personnelles", {"category": "legal"})
    assert [w["name"] for w in store.list("acme")] == ["GDPR"]
    assert store.list("other") == []
    assert "acme" in store.tenants()
    assert store.delete("acme", watch["id"]) and not store.delete("acme", watch["id"])


def test_watches_alert_only_on_passages_indexed_since_last_run():
    store = WatchStore()
    watch = store.create("acme", "Incidents", "incident")
    store.mark_run("acme", watch, NOW - timedelta(hours=1))

    class _Rag:
        def retrieve(self, query, **kwargs):
            self.kwargs = kwargs
            return [
                {"source": "new.md", "content": "new incident", "created_at": NOW.isoformat()},
                {"source": "old.md", "content": "old incident", "created_at": (NOW - timedelta(days=2)).isoformat()},
            ], False

    rag, notifier = _Rag(), _Notifier()
    alerts = run_watches(store, rag, notifier, scope_for_tenant=lambda t: {"tenant": t})
    assert alerts == [{"tenant": "acme", "watch": watch["id"], "matches": 1, "delivered": ["webhook"]}]
    assert "new.md" in notifier.sent[0][1] and "old.md" not in notifier.sent[0][1]
    assert rag.kwargs["filters"] == {"tenant": "acme"}
    # The watch moved forward: the same passage does not alert twice.
    assert run_watches(store, _Rag(), _Notifier()) == []


def test_schema_validation():
    schema = {
        "type": "object",
        "required": ["invoice_number", "total"],
        "properties": {
            "invoice_number": {"type": "string"},
            "total": {"type": "number"},
            "currency": {"type": "string", "enum": ["EUR", "USD"]},
            "lines": {"type": "array", "items": {"type": "object", "required": ["label"]}},
        },
    }
    assert validate({"invoice_number": "F-1", "total": 12.5, "currency": "EUR", "lines": [{"label": "x"}]}, schema) == []
    errors = validate({"invoice_number": 7, "currency": "GBP", "lines": [{}]}, schema)
    assert set(errors) == {
        "$.total is required",
        "$.invoice_number should be string",
        "$.currency should be one of ['EUR', 'USD']",
        "$.lines[0].label is required",
    }


SCHEMA = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}


def test_extraction_retries_with_validation_errors():
    llm = _LLM(['{"age": "forty"}', 'Here it is: {"name": "Ada", "age": 40}'])
    result = extract(llm, "Ada is 40.", SCHEMA)
    assert result == {"data": {"name": "Ada", "age": 40}, "valid": True, "errors": [], "attempts": 2}
    assert "$.name is required" in llm.prompts[1]


def test_extraction_reports_invalid_output():
    result = extract(_LLM(["nope", "still nope"]), "text", SCHEMA)
    assert not result["valid"] and "invalid JSON" in result["errors"][0]


def test_notifier_webhook_payload_and_failures(monkeypatch):
    class _Session:
        def __init__(self, fail=False):
            self.fail, self.posts = fail, []

        def post(self, url, json, timeout):
            self.posts.append((url, json))
            if self.fail:
                raise ConnectionError("down")
            return type("R", (), {"raise_for_status": lambda self: None})()

    session = _Session()
    assert Notifier(webhook_url="https://hooks.example.com/x", email_to=[], session=session).send("Subj", "Body") == [
        "webhook"
    ]
    assert session.posts[0][1]["text"] == "*Subj*\nBody"
    assert Notifier(webhook_url="https://h", email_to=[], session=_Session(fail=True)).send("s", "b") == []


def _sign(body: bytes, secret="s3cret"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_signature():
    assert verify_webhook_signature(b"{}", _sign(b"{}"), "s3cret")
    assert not verify_webhook_signature(b"{}", _sign(b"{}", "other"), "s3cret")
    assert not verify_webhook_signature(b"{}", None, "s3cret")
    assert not verify_webhook_signature(b"{}", _sign(b"{}"), "")  # no secret configured = webhooks disabled


def test_webhook_endpoint(app, client, env):
    env.setenv("WEBHOOK_SECRET", "s3cret")
    jobs = JobManager()
    app.extensions["job_manager"] = jobs
    body = json.dumps({"connector": "missing"}).encode()
    assert client.post("/webhooks/ingest", data=body, content_type="application/json").status_code == 401
    response = client.post(
        "/webhooks/ingest", data=body, content_type="application/json", headers={"X-Signature": _sign(body)}
    )
    assert response.status_code == 404
    empty = b"{}"
    assert (
        client.post(
            "/webhooks/ingest", data=empty, content_type="application/json", headers={"X-Signature": _sign(empty)}
        ).status_code
        == 422
    )


def test_watch_and_extract_endpoints(app, client, auth_headers, fakes):
    created = client.post("/watches", json={"name": "W", "query": "q"}, headers=auth_headers).get_json()
    assert [w["id"] for w in client.get("/watches", headers=auth_headers).get_json()["watches"]] == [created["id"]]
    assert client.delete(f"/watches/{created['id']}", headers=auth_headers).status_code == 200

    app.extensions["llm_service_factory"] = lambda: _LLM(['{"name": "Ada"}'])
    result = client.post("/extract", json={"text": "Ada", "schema": SCHEMA}, headers=auth_headers).get_json()
    assert result["valid"] and result["data"] == {"name": "Ada"}
    bad = client.post("/extract", json={"text": "x", "schema": {"type": "string"}}, headers=auth_headers)
    assert bad.status_code == 422
    both = client.post("/extract", json={"text": "x", "document_id": "d", "schema": SCHEMA}, headers=auth_headers)
    assert both.status_code == 422


def test_digest_endpoint_runs_as_job(app, client, auth_headers, fakes):
    jobs = JobManager()
    app.extensions["job_manager"] = jobs
    notifier = _Notifier()
    app.extensions["notifier"] = notifier
    app.extensions["llm_service_factory"] = lambda: _LLM()
    fakes["vector_store"].list_documents = lambda size, after=None, **scope: {
        "documents": [{"document_id": "d", "source": "n.md", "chunks": 1, "indexed_at": NOW.isoformat()}],
        "next": None,
    }
    fakes["vector_store"].get_document = lambda document_id, **scope: {"source": "n.md", "chunks": [{"content": "c"}]}

    job_id = client.post("/automations/digest", json={"since_hours": 2}, headers=auth_headers).get_json()["job_id"]
    jobs.wait()
    job = client.get(f"/jobs/{job_id}", headers=auth_headers).get_json()
    assert job["status"] == "succeeded" and job["result"]["delivered"] == ["webhook"]
    assert notifier.sent[0][0].startswith("1 new document(s)")
