"""Citation verification with an LLM judge.

The lexical :class:`CitationVerifier` misses paraphrases; here an LLM reads the
answer and the numbered sources and decides, claim by claim, whether each
claim is supported.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from .citation_verifier import CitationCheck

logger = logging.getLogger(__name__)

JUDGE_TEMPLATE = """You check whether an answer is supported by its sources.

Sources:
{sources}

Answer:
{answer}

Split the answer into its factual claims (ignore greetings and statements that information is missing). For each claim, decide whether the sources explicitly support it.

Reply with JSON only, in this exact shape:
{{"claims": [{{"claim": "<claim text>", "supported": true, "sources": [1]}}]}}
"sources" lists the numbers of the supporting sources (empty when unsupported)."""

GROUNDED_THRESHOLD = 0.7
MAX_SOURCE_CHARS = 2000


class LLMCitationVerifier:
    """Claim-level grounding check performed by an LLM."""

    def __init__(self, llm_service, *, threshold: float = GROUNDED_THRESHOLD) -> None:
        self.llm_service = llm_service
        self.threshold = threshold

    def verify(self, answer: str, sources: List[Dict[str, Any]], check_claims: bool = True) -> CitationCheck:
        if not sources:
            return CitationCheck(
                is_grounded=False,
                confidence=0.0,
                grounded_claims=[],
                ungrounded_claims=[answer],
                source_matches={},
                warnings=["No sources available for verification"],
            )

        numbered = "\n\n".join(
            f"[{i}] {(src.get('content') or src.get('snippet') or '')[:MAX_SOURCE_CHARS]}" for i, src in enumerate(sources, 1)
        )
        raw = self.llm_service.generate(
            JUDGE_TEMPLATE.format(sources=numbered, answer=answer), temperature=0.0, max_tokens=2000
        )
        claims = _parse_claims(raw)

        grounded, ungrounded, matches = [], [], {}
        for item in claims:
            claim = str(item.get("claim", "")).strip()
            if not claim:
                continue
            if item.get("supported"):
                grounded.append(claim)
                # Judge uses 1-based numbering; CitationCheck uses source indices.
                matches[claim] = [int(n) - 1 for n in item.get("sources", []) if str(n).isdigit()]
            else:
                ungrounded.append(claim)

        total = len(grounded) + len(ungrounded)
        confidence = len(grounded) / total if total else 1.0
        warnings = [f"Unsupported claim: {claim}" for claim in ungrounded]
        if confidence < self.threshold:
            warnings.insert(0, f"Only {confidence:.0%} of the claims are supported by the sources")
        return CitationCheck(
            is_grounded=confidence >= self.threshold,
            confidence=confidence,
            grounded_claims=grounded,
            ungrounded_claims=ungrounded,
            source_matches=matches,
            warnings=warnings,
        )


def _parse_claims(raw: str) -> List[Dict[str, Any]]:
    """Extract the claims list from the judge reply, tolerating code fences or prose around the JSON."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise ValueError("Citation judge did not return JSON")
    data = json.loads(match.group(0))
    claims = data.get("claims")
    if not isinstance(claims, list):
        raise ValueError("Citation judge JSON has no 'claims' list")
    return [c for c in claims if isinstance(c, dict)]
