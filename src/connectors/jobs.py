"""Background jobs (thread pool) with status shared through Redis when available."""

from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from ..observability.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    """Runs callables in background threads and records their status.

    Jobs run inside the API process that accepted them; their status is stored
    in Redis (when configured) so any worker can answer GET /jobs/<id>. Jobs in
    flight are lost if that process stops.
    """

    def __init__(self, redis_client=None, *, max_workers: Optional[int] = None, ttl_seconds: int = 7 * 86400) -> None:
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers or int(os.getenv("JOB_WORKERS", "2")))

    def submit(self, kind: str, func: Callable[[Callable[[Dict[str, Any]], None]], Dict[str, Any]], *, owner: str) -> str:
        """Run ``func(progress)`` in the background; ``progress(dict)`` updates the job's progress."""
        job_id = uuid.uuid4().hex
        self._save(job_id, {"id": job_id, "kind": kind, "owner": owner, "status": "queued", "created_at": _now()})

        def run() -> None:
            self._update(job_id, status="running", started_at=_now())
            try:
                result = func(lambda progress: self._update(job_id, progress=progress))
                self._update(job_id, status="succeeded", result=result, finished_at=_now())
            except Exception as exc:
                logger.error(f"Job {job_id} ({kind}) failed: {exc}\n{traceback.format_exc()}")
                self._update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=_now())

        self._executor.submit(run)
        return job_id

    def get(self, job_id: str, *, owner: str) -> Optional[Dict[str, Any]]:
        job = self._load(job_id)
        if job is None or job.get("owner") != owner:
            return None
        return {k: v for k, v in job.items() if k != "owner"}

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until queued jobs finish (tests and CLI use)."""
        self._executor.shutdown(wait=True)
        self._executor = ThreadPoolExecutor(max_workers=self._executor._max_workers)

    # -- storage ---------------------------------------------------------
    def _key(self, job_id: str) -> str:
        return f"rag:job:{job_id}"

    def _load(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.redis is None:
            with self._lock:
                job = self._memory.get(job_id)
                return dict(job) if job else None
        raw = self.redis.get(self._key(job_id))
        return json.loads(raw) if raw else None

    def _save(self, job_id: str, job: Dict[str, Any]) -> None:
        if self.redis is None:
            with self._lock:
                self._memory[job_id] = job
        else:
            self.redis.set(self._key(job_id), json.dumps(job, default=str), ex=self.ttl_seconds)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._load(job_id) or {"id": job_id}
            job.update(changes)
            self._save(job_id, job)
