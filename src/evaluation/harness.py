"""Offline evaluation of retrieval and answer quality against a reference dataset.

Each dataset line is a JSON object::

    {"id": "...", "question": "...", "expected_source": "file.md",
     "expected_snippet": "text that must appear in a retrieved chunk",
     "expected_facts": ["facts", "the answer should mention"]}

Retrieval metrics (always computed):
- hit_rate: share of questions whose expected snippet is in the top-k chunks
- mrr: mean reciprocal rank of the first chunk containing the snippet
- source_recall: share of questions whose expected source is in the top-k

Generation metrics (with an LLM, ``generate=True``):
- answer_correctness: share of expected facts mentioned in the answer
- faithfulness: citation-check confidence (claims supported by the sources)
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional

RETRIEVAL_METRICS = ("hit_rate", "mrr", "source_recall")
GENERATION_METRICS = ("answer_correctness", "faithfulness")


@dataclass
class EvalCase:
    id: str
    question: str
    expected_source: str
    expected_snippet: str
    expected_facts: List[str] = field(default_factory=list)


def load_dataset(path: Path) -> List[EvalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(EvalCase(**json.loads(line)))
    return cases


def _normalise(text: str) -> str:
    # Case-, accent- and whitespace-insensitive matching
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def first_hit_rank(results: List[Dict[str, Any]], snippet: str) -> Optional[int]:
    """1-based rank of the first result containing the snippet, or None."""
    wanted = _normalise(snippet)
    for rank, result in enumerate(results, start=1):
        if wanted in _normalise(result.get("content", "")):
            return rank
    return None


def fact_coverage(answer: str, facts: Iterable[str]) -> float:
    facts = list(facts)
    if not facts:
        return 1.0
    normalised = _normalise(answer)
    return sum(1 for fact in facts if _normalise(fact) in normalised) / len(facts)


def evaluate(
    rag_service,
    cases: List[EvalCase],
    *,
    top_k: int = 4,
    generate: bool = False,
    query_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run every case and return aggregate metrics plus per-case details."""
    options = query_options or {}
    details = []
    for case in cases:
        results, _ = rag_service.retrieve(case.question, top_k=top_k, **_retrieve_options(options))
        rank = first_hit_rank(results, case.expected_snippet)
        sources = [Path(str(r.get("source", ""))).name for r in results]
        detail: Dict[str, Any] = {
            "id": case.id,
            "hit": rank is not None,
            "rank": rank,
            "reciprocal_rank": 1.0 / rank if rank else 0.0,
            "source_found": case.expected_source in sources,
            "retrieved_sources": sources,
        }
        if generate:
            response = rag_service.query(case.question, top_k=top_k, verify_citations="llm", **options)
            detail["answer"] = response["answer"]
            detail["answer_correctness"] = fact_coverage(response["answer"], case.expected_facts)
            check = response.get("citation_check")
            detail["faithfulness"] = check["confidence"] if check else None
        details.append(detail)

    metrics = {
        "hit_rate": mean(d["hit"] for d in details),
        "mrr": mean(d["reciprocal_rank"] for d in details),
        "source_recall": mean(d["source_found"] for d in details),
    }
    if generate:
        metrics["answer_correctness"] = mean(d["answer_correctness"] for d in details)
        faithfulness = [d["faithfulness"] for d in details if d["faithfulness"] is not None]
        metrics["faithfulness"] = mean(faithfulness) if faithfulness else None
    return {"metrics": metrics, "cases": details, "top_k": top_k, "options": options}


def _retrieve_options(options: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"rerank", "search_mode", "filters", "min_score", "max_age_days"}
    return {key: value for key, value in options.items() if key in allowed}


def check_thresholds(metrics: Dict[str, Any], thresholds: Dict[str, float]) -> List[str]:
    """Return a failure message for every metric below its threshold."""
    failures = []
    for name, minimum in thresholds.items():
        value = metrics.get(name)
        if value is not None and value < minimum:
            failures.append(f"{name} = {value:.3f} < {minimum:.3f}")
    return failures


def markdown_report(report: Dict[str, Any], failures: List[str]) -> str:
    lines = ["## RAG evaluation", "", f"Options: `{json.dumps(report['options'])}`, top_k={report['top_k']}", ""]
    lines += ["| Metric | Value |", "|---|---|"]
    for name, value in report["metrics"].items():
        lines.append(f"| {name} | {'n/a' if value is None else f'{value:.3f}'} |")
    misses = [c for c in report["cases"] if not c["hit"]]
    if misses:
        lines += ["", "Missed questions: " + ", ".join(f"`{c['id']}`" for c in misses)]
    lines += ["", "**Result:** " + ("FAILED: " + "; ".join(failures) if failures else "passed")]
    return "\n".join(lines) + "\n"
