"""Evaluate retrieval (and optionally answer) quality on the reference dataset.

Ingests the corpus into a temporary Elasticsearch index, runs every question of
eval/dataset.jsonl, writes report.json / report.md and exits with status 1 when
a metric falls below eval/thresholds.json.

    python scripts/evaluate.py                       # retrieval metrics, configured embedding model
    python scripts/evaluate.py --embeddings hash     # smoke run without model download
    python scripts/evaluate.py --generate            # + answer correctness and faithfulness (needs an LLM key)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evaluation import check_thresholds, evaluate, load_dataset, markdown_report  # noqa: E402
from src.evaluation.hash_embeddings import HashEmbeddings  # noqa: E402
from src.ingestion.pipeline import IngestionPipeline  # noqa: E402
from src.llm.rag_service import RAGService  # noqa: E402
from src.vector_store.client import create_es_client  # noqa: E402
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore  # noqa: E402


class _NoLLM:
    """Retrieval-only runs must not call an LLM."""

    def generate(self, *args, **kwargs):
        raise RuntimeError("No LLM configured: run with --generate to evaluate answers")

    stream_generate = generate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=ROOT / "eval" / "dataset.jsonl")
    parser.add_argument("--corpus", type=Path, nargs="+", default=[ROOT / "test_docs", ROOT / "eval" / "corpus"])
    parser.add_argument("--thresholds", type=Path, default=ROOT / "eval" / "thresholds.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval-report")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--search-mode", choices=["dense", "sparse", "hybrid"])
    parser.add_argument("--rerank", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--query-expansion", choices=["none", "multi_query", "hyde"])
    parser.add_argument("--generate", action="store_true", help="Also generate and grade answers (uses the LLM)")
    parser.add_argument("--embeddings", choices=["model", "hash"], default="model")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    cases = load_dataset(args.dataset)

    if args.embeddings == "hash":
        embeddings = HashEmbeddings()
    else:
        from src.embeddings.service import EmbeddingService

        embeddings = EmbeddingService()

    client = create_es_client()
    index = f"rag-eval-{uuid.uuid4().hex[:8]}"
    store = ElasticsearchVectorStore(index, embeddings, client=client, refresh="true")
    store.ensure_index()
    try:
        pipeline = IngestionPipeline()
        for folder in args.corpus:
            for path in sorted(p for p in folder.iterdir() if p.is_file()):
                result = pipeline.ingest_bytes(content=path.read_bytes(), filename=path.name)
                store.add_documents(result.chunks)
        print(f"Indexed corpus into '{index}' ({embeddings.model_name})")

        if args.generate:
            from src.llm.service import LLMService

            llm = LLMService()
        else:
            llm = _NoLLM()
        rag = RAGService(store, llm, enable_reranking=args.rerank)

        options = {
            key: value
            for key, value in {
                "search_mode": args.search_mode,
                "rerank": args.rerank,
                "query_expansion": args.query_expansion,
            }.items()
            if value is not None
        }
        report = evaluate(rag, cases, top_k=args.top_k, generate=args.generate, query_options=options)
        report["embeddings"] = embeddings.model_name
    finally:
        client.indices.delete(index=index, ignore_unavailable=True)

    thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
    # Hash embeddings are a smoke test: only enforce thresholds for real models.
    failures = check_thresholds(report["metrics"], thresholds) if args.embeddings == "model" else []
    markdown = markdown_report(report, failures)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.output_dir / "report.md").write_text(markdown, encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    print(markdown)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
