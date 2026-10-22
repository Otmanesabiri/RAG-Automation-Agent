"""Evaluation harness metrics."""

import json
from pathlib import Path

import pytest

from src.evaluation import check_thresholds, evaluate, load_dataset, markdown_report
from src.evaluation.harness import EvalCase, fact_coverage, first_hit_rank

ROOT = Path(__file__).resolve().parent.parent


def test_first_hit_rank_ignores_case_accents_and_spacing():
    results = [{"content": "rien"}, {"content": "Conservés   PENDANT 12 mois."}]
    assert first_hit_rank(results, "conserves pendant 12 mois") == 2
    assert first_hit_rank(results, "absent") is None


def test_fact_coverage():
    assert fact_coverage("Delay is 72 heures for the CNIL", ["72 heures", "CNIL", "SOC"]) == pytest.approx(2 / 3)
    assert fact_coverage("anything", []) == 1.0


class _FakeRag:
    def __init__(self):
        self.corpus = {
            "q1": [{"content": "nothing", "source": "a.txt"}, {"content": "the answer is here", "source": "/tmp/b.txt"}],
            "q2": [{"content": "unrelated", "source": "c.txt"}],
        }

    def retrieve(self, question, top_k, **kwargs):
        return self.corpus[question][:top_k], False

    def query(self, question, top_k, verify_citations=None, **kwargs):
        return {"answer": "the answer mentions alpha", "citation_check": {"confidence": 0.5}}


CASES = [
    EvalCase("one", "q1", "b.txt", "answer is here", ["alpha", "beta"]),
    EvalCase("two", "q2", "d.txt", "missing", ["alpha"]),
]


def test_evaluate_retrieval_metrics():
    report = evaluate(_FakeRag(), CASES, top_k=4)
    assert report["metrics"] == {"hit_rate": 0.5, "mrr": 0.25, "source_recall": 0.5}
    assert report["cases"][0]["rank"] == 2
    assert report["cases"][0]["retrieved_sources"] == ["a.txt", "b.txt"]


def test_evaluate_generation_metrics():
    metrics = evaluate(_FakeRag(), CASES, top_k=4, generate=True)["metrics"]
    assert metrics["answer_correctness"] == pytest.approx(0.75)
    assert metrics["faithfulness"] == 0.5


def test_thresholds_and_report():
    report = evaluate(_FakeRag(), CASES, top_k=4)
    failures = check_thresholds(report["metrics"], {"hit_rate": 0.8, "mrr": 0.1, "faithfulness": 0.9})
    assert failures == ["hit_rate = 0.500 < 0.800"]
    markdown = markdown_report(report, failures)
    assert "| hit_rate | 0.500 |" in markdown
    assert "`two`" in markdown and "FAILED" in markdown


def test_reference_dataset_is_consistent():
    cases = load_dataset(ROOT / "eval" / "dataset.jsonl")
    corpus = {p.name: p.read_text(encoding="utf-8") for d in ("test_docs", "eval/corpus") for p in (ROOT / d).iterdir()}
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert case.expected_source in corpus, case.id
        assert case.expected_snippet in corpus[case.expected_source], case.id
    thresholds = json.loads((ROOT / "eval" / "thresholds.json").read_text())
    assert set(thresholds) == {"hit_rate", "mrr", "source_recall", "answer_correctness", "faithfulness"}
