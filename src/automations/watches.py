"""Watches: saved queries that raise an alert when new matching passages are indexed."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .digest import _parse_time


class WatchStore:
    """Watches per tenant, in Redis (shared) or memory."""

    def __init__(self, redis_client=None, prefix: str = "rag:watches:") -> None:
        self.redis = redis_client
        self.prefix = prefix
        self._memory: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _load(self, tenant: str) -> Dict[str, Dict[str, Any]]:
        if self.redis is None:
            return self._memory.setdefault(tenant, {})
        return {self._text(k): json.loads(v) for k, v in self.redis.hgetall(self.prefix + tenant).items()}

    def _store(self, tenant: str, watch: Dict[str, Any]) -> None:
        if self.redis is None:
            self._memory.setdefault(tenant, {})[watch["id"]] = watch
        else:
            self.redis.hset(self.prefix + tenant, watch["id"], json.dumps(watch, ensure_ascii=False))

    @staticmethod
    def _text(value) -> str:
        return value.decode() if isinstance(value, bytes) else value

    def create(
        self, tenant: str, name: str, query: str, filters: Optional[Dict[str, Any]] = None, min_score=None
    ) -> Dict[str, Any]:
        watch = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "query": query,
            "filters": filters or {},
            "min_score": min_score,
            "last_run": datetime.now(timezone.utc).isoformat(),  # only passages indexed after creation alert
        }
        self._store(tenant, watch)
        return watch

    def list(self, tenant: str) -> List[Dict[str, Any]]:
        return sorted(self._load(tenant).values(), key=lambda w: w["name"])

    def delete(self, tenant: str, watch_id: str) -> bool:
        if self.redis is None:
            return self._memory.get(tenant, {}).pop(watch_id, None) is not None
        return bool(self.redis.hdel(self.prefix + tenant, watch_id))

    def tenants(self) -> List[str]:
        if self.redis is None:
            return list(self._memory)
        return [self._text(k)[len(self.prefix) :] for k in self.redis.scan_iter(match=self.prefix + "*")]

    def mark_run(self, tenant: str, watch: Dict[str, Any], when: datetime) -> None:
        self._store(tenant, {**watch, "last_run": when.isoformat()})


def run_watches(store: WatchStore, rag_service, notifier, *, scope_for_tenant=None, top_k: int = 10) -> List[Dict[str, Any]]:
    """Check every watch for passages indexed since its last run; notify the matches."""
    alerts = []
    for tenant in store.tenants():
        scope_filters = scope_for_tenant(tenant) if scope_for_tenant else {}
        for watch in store.list(tenant):
            started = datetime.now(timezone.utc)
            since = _parse_time(watch.get("last_run")) or started
            results, _ = rag_service.retrieve(
                watch["query"],
                top_k=top_k,
                filters={**watch.get("filters", {}), **scope_filters} or None,
                min_score=watch.get("min_score"),
                rerank=False,
            )
            fresh = [r for r in results if (_parse_time(r.get("created_at")) or since) > since]
            if fresh:
                body = "\n".join(f"- **{r.get('source')}**: {r.get('content', '')[:200]}" for r in fresh)
                subject = f"Watch '{watch['name']}': {len(fresh)} new passage(s)"
                channels = notifier.send(subject, body, data={"watch": watch["id"], "tenant": tenant})
                alerts.append({"tenant": tenant, "watch": watch["id"], "matches": len(fresh), "delivered": channels})
            store.mark_run(tenant, watch, started)
    return alerts
