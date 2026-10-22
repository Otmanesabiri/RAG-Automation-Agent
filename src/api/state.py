"""In-memory session store used during early development."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4


class SessionStore:
    """Lightweight store for conversational state.

    Each session belongs to the client that created it (``owner``); lookups made
    on behalf of another client behave as if the session did not exist.
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
        self._sessions[session_id] = {
            "user_id": user_id,
            "owner": owner,
            "metadata": metadata or {},
            "messages": [],
            "created_at": datetime.utcnow().isoformat(),
        }
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
        session["messages"].append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
