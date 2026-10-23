"""Digest of the documents indexed since the previous run."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..observability.logging import get_logger

logger = get_logger(__name__)

MAX_DIGEST_DOCUMENTS = 20
SUMMARY_PROMPT = (
    "Summarize this document in 2-3 sentences for a digest of newly published documents. "
    "Keep its language.\n\nTitle: {source}\n\n{text}"
)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def new_documents(
    vector_store, since: datetime, *, scope: Optional[Dict[str, Any]] = None, page_size: int = 100
) -> List[Dict[str, Any]]:
    """Documents whose last indexing is after ``since`` (newest first)."""
    found, after = [], None
    while True:
        page = vector_store.list_documents(size=page_size, after=after, **(scope or {}))
        for document in page["documents"]:
            indexed_at = _parse_time(document.get("indexed_at"))
            if indexed_at and indexed_at > since:
                found.append({**document, "indexed_at": indexed_at.isoformat()})
        after = page.get("next")
        if not after:
            break
    return sorted(found, key=lambda d: d["indexed_at"], reverse=True)


def build_digest(vector_store, llm_service, *, since: Optional[datetime] = None, scope=None) -> Dict[str, Any]:
    """Subject, Markdown body and the documents of a digest (no LLM call when nothing is new)."""
    since = since or datetime.now(timezone.utc) - timedelta(days=1)
    documents = new_documents(vector_store, since, scope=scope)
    if not documents:
        return {"subject": "No new documents", "body": "", "documents": [], "since": since.isoformat()}

    lines = []
    for document in documents[:MAX_DIGEST_DOCUMENTS]:
        summary = ""
        full = vector_store.get_document(document["document_id"], **(scope or {}))
        if full:
            text = "\n".join(chunk["content"] for chunk in full["chunks"])[:8000]
            try:
                summary = llm_service.generate(
                    SUMMARY_PROMPT.format(source=document["source"], text=text), temperature=0.2, max_tokens=300
                ).strip()
            except Exception as exc:  # keep the digest even if one summary fails
                logger.warning(f"Digest summary failed for {document['source']}: {exc}")
        lines.append(f"- **{document['source']}** ({document['chunks']} chunks)\n  {summary}".rstrip())
    if len(documents) > MAX_DIGEST_DOCUMENTS:
        lines.append(f"- ... and {len(documents) - MAX_DIGEST_DOCUMENTS} more")

    subject = f"{len(documents)} new document(s) since {since:%Y-%m-%d %H:%M} UTC"
    return {"subject": subject, "body": "\n".join(lines), "documents": documents, "since": since.isoformat()}
