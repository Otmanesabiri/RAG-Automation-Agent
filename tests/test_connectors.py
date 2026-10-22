"""Connectors, incremental sync, background jobs and their endpoints."""

import io
import json
import os
import time
from types import SimpleNamespace

import fakeredis
import pytest

from src.connectors import (
    ConfluenceConnector,
    JobManager,
    LocalFolderConnector,
    S3Connector,
    SyncService,
    SyncStateStore,
    build_connector,
    load_connectors,
)
from src.ingestion.config import LoaderConfig
from src.ingestion.pipeline import IngestionPipeline
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore


class _Store:
    document_id_for = staticmethod(ElasticsearchVectorStore.document_id_for)

    def __init__(self):
        self.chunks = {}
        self.deleted = []

    def add_documents(self, chunks):
        for chunk in chunks:
            self.chunks.setdefault(chunk.metadata["source"], []).append(chunk)
        return ["id"] * len(chunks)

    def delete_document(self, document_id):
        self.deleted.append(document_id)
        return 1


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A\n\nalpha", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    (root / "ignored.bin").write_bytes(b"\x00")
    return root


@pytest.fixture
def sync_env(tmp_path):
    store = _Store()
    service = SyncService(IngestionPipeline(loader_config=LoaderConfig(temp_dir=tmp_path / "tmp")), store, SyncStateStore())
    return service, store


def test_local_connector_lists_matching_files(folder):
    items = list(LocalFolderConnector("share", str(folder)).list_items())
    assert [i.source for i in items] == ["share:a.md", "share:sub/b.txt"]
    assert LocalFolderConnector("share", str(folder)).fetch(items[1]) == b"beta"


def test_local_connector_refuses_path_escape(folder):
    connector = LocalFolderConnector("share", str(folder))
    evil = SimpleNamespace(metadata={"path": "../../etc/passwd"})
    with pytest.raises(PermissionError):
        connector.fetch(evil)


def test_incremental_sync(folder, sync_env):
    service, store = sync_env
    connector = LocalFolderConnector("share", str(folder), metadata={"team": "ops"})

    first = service.sync(connector)
    assert (first.added, first.unchanged) == (2, 0)
    assert store.chunks["share:a.md"][0].metadata["team"] == "ops"
    assert store.chunks["share:a.md"][0].metadata["connector"] == "share"

    second = service.sync(connector)
    assert (second.added, second.updated, second.unchanged) == (0, 0, 2)

    (folder / "a.md").write_text("# A\n\nalpha v2, longer", encoding="utf-8")
    os.utime(folder / "a.md", (time.time() + 10, time.time() + 10))
    (folder / "sub" / "b.txt").unlink()
    third = service.sync(connector)
    assert (third.updated, third.unchanged, third.deleted) == (1, 0, 1)
    assert store.deleted == [ElasticsearchVectorStore.document_id_for("share:sub/b.txt")]

    assert service.sync(connector, full=True).added == 1


def test_failed_items_are_reported_and_retried(folder, sync_env):
    service, _ = sync_env
    connector = LocalFolderConnector("share", str(folder))
    original_fetch = connector.fetch
    connector.fetch = lambda item: (
        (_ for _ in ()).throw(IOError("disk error")) if item.filename == "b.txt" else original_fetch(item)
    )

    report = service.sync(connector)
    assert report.failed == 1 and "disk error" in report.errors[0]
    connector.fetch = original_fetch
    assert service.sync(connector).added == 1  # retried on the next run


def test_sync_state_in_redis():
    state = SyncStateStore(fakeredis.FakeRedis())
    state.save("share", {"share:a.md": "v1"})
    assert state.load("share") == {"share:a.md": "v1"}
    state.save("share", {})
    assert state.load("share") == {}


class _S3:
    def get_paginator(self, name):
        pages = [
            {
                "Contents": [
                    {"Key": "docs/a.pdf", "ETag": '"e1"'},
                    {"Key": "docs/", "ETag": '"x"'},
                    {"Key": "docs/c.exe", "ETag": '"e3"'},
                ]
            }
        ]
        return SimpleNamespace(paginate=lambda **kwargs: pages)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(b"pdf bytes")}


def test_s3_connector():
    connector = S3Connector("bucket", bucket="b", prefix="docs/", client=_S3())
    items = list(connector.list_items())
    assert [(i.source, i.version) for i in items] == [("s3://b/docs/a.pdf", "e1")]
    assert connector.fetch(items[0]) == b"pdf bytes"


class _ConfluenceSession:
    auth = None

    def get(self, url, params=None, timeout=None):
        if url.endswith("/rest/api/content"):
            data = {"results": [{"id": "42", "title": "Runbook", "version": {"number": 7}}], "size": 1}
        else:
            data = {"title": "Runbook", "body": {"storage": {"value": "<p>Restart the service.</p>"}}}
        return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)


def test_confluence_connector(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "token")
    session = _ConfluenceSession()
    connector = ConfluenceConnector("wiki", "https://x.atlassian.net/wiki", "ENG", "bot@x.com", session=session)
    items = list(connector.list_items())
    assert items[0].version == "7" and items[0].filename == "Runbook.html"
    assert b"Restart the service." in connector.fetch(items[0])
    assert session.auth == ("bot@x.com", "token")


def test_load_connectors_from_config(tmp_path, folder):
    config = tmp_path / "connectors.json"
    config.write_text(json.dumps({"share": {"type": "local", "path": str(folder)}}), encoding="utf-8")
    connectors = load_connectors(str(config))
    assert isinstance(connectors["share"], LocalFolderConnector)
    with pytest.raises(ValueError):
        build_connector("x", {"type": "ftp"})


@pytest.mark.parametrize("redis_client", [None, fakeredis.FakeRedis()])
def test_job_manager(redis_client):
    jobs = JobManager(redis_client)
    ok = jobs.submit("demo", lambda progress: (progress({"done": 1}), {"value": 42})[1], owner="key:a")
    bad = jobs.submit("demo", lambda progress: 1 / 0, owner="key:a")
    jobs.wait()

    job = jobs.get(ok, owner="key:a")
    assert job["status"] == "succeeded" and job["result"] == {"value": 42} and job["progress"] == {"done": 1}
    assert jobs.get(bad, owner="key:a")["status"] == "failed"
    assert jobs.get(ok, owner="key:b") is None


def test_sync_and_job_endpoints(app, client, auth_headers, folder, sync_env):
    service, store = sync_env
    app.extensions["connectors_factory"] = lambda: {"share": LocalFolderConnector("share", str(folder))}
    app.extensions["sync_service_factory"] = lambda: service
    jobs = JobManager()
    app.extensions["job_manager"] = jobs

    assert client.get("/connectors", headers=auth_headers).get_json()["connectors"] == [
        {"name": "share", "type": "LocalFolderConnector"}
    ]
    assert client.post("/connectors/unknown/sync", headers=auth_headers).status_code == 404

    job_id = client.post("/connectors/share/sync", headers=auth_headers).get_json()["job_id"]
    jobs.wait()
    job = client.get(f"/jobs/{job_id}", headers=auth_headers).get_json()
    assert job["status"] == "succeeded" and job["result"]["added"] == 2
    assert client.get("/jobs/nope", headers=auth_headers).status_code == 404


def test_async_ingest(app, client, auth_headers, fakes):
    jobs = JobManager()
    app.extensions["job_manager"] = jobs
    payload = {"source": "upload", "documents": [{"content": "hello", "filename": "a.txt"}]}
    body = client.post("/ingest?async=true", json=payload, headers=auth_headers).get_json()
    assert body["status"] == "queued"
    jobs.wait()
    job = client.get(f"/jobs/{body['job_id']}", headers=auth_headers).get_json()
    assert job["status"] == "succeeded"
    assert job["result"]["documents"][0]["source"] == "a.txt"
    assert len(fakes["vector_store"].indexed) == 1
