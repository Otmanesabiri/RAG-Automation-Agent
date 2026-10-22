"""Conversational session stores (in-memory for development, Redis for multi-worker)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ..redis_client import get_redis_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session(user_id: Optional[str], metadata: Optional[Dict[str, Any]], owner: Optional[str]) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "owner": owner,
        "metadata": metadata or {},
        "messages": [],
        "created_at": _now(),
    }


def _message(role: str, content: str) -> Dict[str, Any]:
    return {"role": role, "content": content, "timestamp": _now()}


class SessionStore:
    """In-memory store for conversational state.

    Each session belongs to the client that created it (``owner``); lookups made
    on behalf of another client behave as if the session did not exist. State is
    local to the process: use :class:`RedisSessionStore` with several workers.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def create_session(
        self,
        user_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
        *,
        owner: str | None = None,
    ) -> str:
        session_id = str(uuid4())
        self._sessions[session_id] = _new_session(user_id, metadata, owner)
        return session_id

    def get_session(self, session_id: str, *, owner: str | None = None) -> Dict[str, Any] | None:
        session = self._sessions.get(session_id)
        if session is None or session.get("owner") != owner:
            return None
        return session

    def list_sessions(self) -> List[str]:
        return list(self._sessions.keys())

    def append_message(self, session_id: str, role: str, content: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")
        session["messages"].append(_message(role, content))


class RedisSessionStore:
    """Redis-backed session store shared by every worker, with a sliding TTL.

    A session is a hash holding its metadata plus a list of JSON messages.
    """

    def __init__(self, client, *, ttl_seconds: int = 86400, prefix: str = "rag:session:") -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.prefix = prefix

    def _key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"

    def _messages_key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}:messages"

    def _touch(self, pipe, session_id: str) -> None:
        pipe.expire(self._key(session_id), self.ttl_seconds)
        pipe.expire(self._messages_key(session_id), self.ttl_seconds)

    def create_session(
        self,
        user_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
        *,
        owner: str | None = None,
    ) -> str:
        session_id = str(uuid4())
        session = _new_session(user_id, metadata, owner)
        pipe = self.client.pipeline()
        pipe.hset(
            self._key(session_id),
            mapping={
                "user_id": json.dumps(session["user_id"]),
                "owner": json.dumps(session["owner"]),
                "metadata": json.dumps(session["metadata"]),
                "created_at": session["created_at"],
            },
        )
        self._touch(pipe, session_id)
        pipe.execute()
        return session_id

    def get_session(self, session_id: str, *, owner: str | None = None) -> Dict[str, Any] | None:
        raw = self.client.hgetall(self._key(session_id))
        if not raw:
            return None
        raw = {_text(k): _text(v) for k, v in raw.items()}
        if json.loads(raw["owner"]) != owner:
            return None
        messages = [json.loads(_text(m)) for m in self.client.lrange(self._messages_key(session_id), 0, -1)]
        return {
            "user_id": json.loads(raw["user_id"]),
            "owner": owner,
            "metadata": json.loads(raw["metadata"]),
            "messages": messages,
            "created_at": raw["created_at"],
        }

    def list_sessions(self) -> List[str]:
        sessions = []
        for key in self.client.scan_iter(match=f"{self.prefix}*"):
            key = _text(key)
            if not key.endswith(":messages"):
                sessions.append(key[len(self.prefix) :])
        return sessions

    def append_message(self, session_id: str, role: str, content: str) -> None:
        if not self.client.exists(self._key(session_id)):
            raise KeyError(f"Unknown session: {session_id}")
        pipe = self.client.pipeline()
        pipe.rpush(self._messages_key(session_id), json.dumps(_message(role, content)))
        self._touch(pipe, session_id)
        pipe.execute()


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def build_session_store():
    client = get_redis_client()
    if client is None:
        return SessionStore()
    return RedisSessionStore(client, ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "86400")))
