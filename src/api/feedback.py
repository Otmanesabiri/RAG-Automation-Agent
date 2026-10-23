"""User feedback on answers (thumbs up/down + comment)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from prometheus_client import Counter

FEEDBACK = Counter("rag_agent_feedback_total", "User feedback on answers", ["rating"])


class FeedbackStore:
    """Keeps the most recent feedback entries in Redis (shared) or memory."""

    def __init__(self, redis_client=None, *, key: str = "rag:feedback", max_entries: int = 10000) -> None:
        self.redis = redis_client
        self.key = key
        self.max_entries = max_entries
        self._memory: List[Dict[str, Any]] = []

    def add(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        entry = {**entry, "created_at": datetime.now(timezone.utc).isoformat()}
        FEEDBACK.labels(rating=entry["rating"]).inc()
        if self.redis is None:
            self._memory.insert(0, entry)
            del self._memory[self.max_entries :]
        else:
            pipe = self.redis.pipeline()
            pipe.lpush(self.key, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(self.key, 0, self.max_entries - 1)
            pipe.execute()
        return entry

    def recent(self, limit: int = 50, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.redis is None:
            entries = list(self._memory)
        else:
            entries = [json.loads(raw) for raw in self.redis.lrange(self.key, 0, self.max_entries - 1)]
        if tenant is not None:
            entries = [e for e in entries if e.get("tenant") == tenant]
        return entries[:limit]
