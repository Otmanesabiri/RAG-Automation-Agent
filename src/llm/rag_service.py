"""RAG retrieval service combining vector search and LLM generation."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterator, List, Optional

from ..vector_store.elasticsearch_store import ElasticsearchVectorStore, reciprocal_rank_fusion
from .prompts import PromptBuilder, StrictRAGPrompt
from .service import LLMService

logger = logging.getLogger(__name__)

# Try to import optional enhancement features
try:
    from ..retrieval.reranker import CrossEncoderReranker

    RERANKER_AVAILABLE = True
except ImportError:
    RERANKER_AVAILABLE = False
    logger.warning("Reranker not available. Install with: pip install sentence-transformers")

try:
    from ..retrieval.citation_verifier import CitationVerifier

    CITATION_VERIFIER_AVAILABLE = True
except ImportError:
    CITATION_VERIFIER_AVAILABLE = False


DEFAULT_PROMPT_TEMPLATE = """You are an AI assistant helping users find information from documents.

Use the following context snippets to answer the user's question. If the context doesn't contain relevant information, say so clearly.

Context:
{context}

Question: {question}

Answer:"""


CONDENSE_QUESTION_TEMPLATE = """Given the conversation below and a follow-up question, rewrite the follow-up as a standalone question that can be understood without the conversation. Keep the language of the follow-up. Reply with the standalone question only.

Conversation:
{history}

Follow-up question: {question}

Standalone question:"""


QUERY_EXPANSIONS = {"none", "multi_query", "hyde"}

MULTI_QUERY_TEMPLATE = """Write {count} different search queries that could retrieve documents answering the question below. Vary the wording and use synonyms. Keep the language of the question. One query per line, no numbering, no other text.

Question: {question}"""

HYDE_TEMPLATE = """Write a short factual passage (3-5 sentences) that would answer the question below, as it might appear in a reference document. Keep the language of the question. Write only the passage.

Question: {question}"""


def _history_settings() -> tuple[int, int, bool]:
    return (
        int(os.getenv("HISTORY_MAX_MESSAGES", "6")),
        int(os.getenv("HISTORY_MAX_CHARS", "4000")),
        os.getenv("QUERY_CONDENSATION", "true").lower() == "true",
    )


def format_history(history: Optional[List[Dict[str, Any]]], max_messages: int, max_chars: int) -> str:
    """Render the most recent messages, newest kept first when trimming to ``max_chars``."""
    if not history or max_messages <= 0:
        return ""
    lines: List[str] = []
    total = 0
    for message in reversed(history[-max_messages:]):
        speaker = "User" if message.get("role") == "user" else "Assistant"
        line = f"{speaker}: {message.get('content', '')}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines))


class RAGService:
    """
    Enhanced Retrieval-Augmented Generation orchestrator.

    Features:
    - Re-ranking for improved relevance
    - Citation verification to reduce hallucinations
    - Advanced filtering (metadata, permissions, age)
    - Multiple prompt strategies
    """

    def __init__(
        self,
        vector_store: ElasticsearchVectorStore,
        llm_service: LLMService,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
        prompt_type: str = "strict",
        enable_reranking: bool = True,
        enable_citation_check: bool = False,
        rerank_top_k: int = 10,
    ) -> None:
        self.vector_store = vector_store
        self.llm_service = llm_service
        self.prompt_template = prompt_template
        self.prompt_type = prompt_type
        self.rerank_top_k = rerank_top_k

        # Initialize optional features
        self.reranker = None
        if enable_reranking and RERANKER_AVAILABLE:
            try:
                self.reranker = CrossEncoderReranker()
                logger.info("Re-ranking enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize reranker: {e}")

        self.citation_verifier = None
        if enable_citation_check and CITATION_VERIFIER_AVAILABLE:
            self.citation_verifier = CitationVerifier()
            logger.info("Citation verification enabled")

    def query(
        self,
        question: str,
        *,
        top_k: int = 4,
        filters: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
        max_age_days: Optional[int] = None,
        user_permissions: Optional[List[str]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        include_sources: bool = True,
        prompt_type: Optional[str] = None,
        rerank: Optional[bool] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        search_mode: Optional[str] = None,
        query_expansion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute enhanced RAG query with optional re-ranking and citation checking.

        Args:
            question: User question
            top_k: Final number of documents to use for generation
            filters: Custom metadata filters
            min_score: Minimum similarity score threshold
            max_age_days: Maximum age of documents in days
            user_permissions: List of access levels user has
            temperature: LLM generation temperature
            max_tokens: Max tokens for generation
            include_sources: Include source documents in response
            prompt_type: Override default prompt type (strict, citation, etc.)
            rerank: Force re-ranking on/off for this query (None = service default)
            search_mode: dense, sparse or hybrid retrieval (None = SEARCH_MODE)
            query_expansion: none, multi_query or hyde (None = QUERY_EXPANSION)
            history: Previous session messages ({role, content}); follow-up
                questions are rewritten into standalone ones for retrieval

        Returns:
            Dict with answer, sources, metadata, and optional citation check
        """
        history_text = self._history_text(history)
        search_query = self.condense_question(question, history_text)
        extra_queries = self.expand_query(search_query, query_expansion)
        search_results, reranked = self.retrieve(
            search_query,
            top_k=top_k,
            filters=filters,
            min_score=min_score,
            max_age_days=max_age_days,
            user_permissions=user_permissions,
            rerank=rerank,
            search_mode=search_mode,
            extra_queries=extra_queries,
        )
        active_prompt_type = prompt_type or self.prompt_type
        prompt = self.build_prompt(question, search_results, active_prompt_type, history_text)

        # Generate answer using LLM
        answer = self.llm_service.generate(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        response = {
            "answer": answer,
            "metadata": {
                "top_k": top_k,
                "retrieved_docs": len(search_results),
                "reranking_enabled": reranked,
                "prompt_type": active_prompt_type,
                "search_query": search_query,
                "expanded_queries": extra_queries,
            },
        }

        # Add citation verification if enabled
        if self.citation_verifier and search_results:
            try:
                citation_check = self.citation_verifier.verify(answer=answer, sources=search_results, check_claims=True)
                response["citation_check"] = {
                    "is_grounded": citation_check.is_grounded,
                    "confidence": citation_check.confidence,
                    "warnings": citation_check.warnings,
                }
                if not citation_check.is_grounded:
                    logger.warning(f"Low citation confidence: {citation_check.confidence:.2%}")
            except Exception as e:
                logger.error(f"Citation verification failed: {e}")

        if include_sources:
            response["sources"] = self.format_sources(search_results)

        return response

    def stream_query(
        self,
        question: str,
        *,
        top_k: int = 4,
        filters: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
        max_age_days: Optional[int] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        prompt_type: Optional[str] = None,
        rerank: Optional[bool] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        search_mode: Optional[str] = None,
        query_expansion: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Stream a RAG answer as events: ``sources`` first, then ``token``s, then ``done``."""
        history_text = self._history_text(history)
        search_query = self.condense_question(question, history_text)
        extra_queries = self.expand_query(search_query, query_expansion)
        search_results, reranked = self.retrieve(
            search_query,
            top_k=top_k,
            filters=filters,
            min_score=min_score,
            max_age_days=max_age_days,
            rerank=rerank,
            search_mode=search_mode,
            extra_queries=extra_queries,
        )
        active_prompt_type = prompt_type or self.prompt_type
        prompt = self.build_prompt(question, search_results, active_prompt_type, history_text)
        yield {"event": "sources", "data": self.format_sources(search_results)}

        parts: List[str] = []
        for text in self.llm_service.stream_generate(prompt, temperature=temperature, max_tokens=max_tokens):
            parts.append(text)
            yield {"event": "token", "data": text}

        yield {
            "event": "done",
            "data": {
                "answer": "".join(parts),
                "metadata": {
                    "top_k": top_k,
                    "retrieved_docs": len(search_results),
                    "reranking_enabled": reranked,
                    "prompt_type": active_prompt_type,
                    "search_query": search_query,
                    "expanded_queries": extra_queries,
                },
            },
        }

    def _history_text(self, history: Optional[List[Dict[str, Any]]]) -> str:
        max_messages, max_chars, _ = _history_settings()
        return format_history(history, max_messages, max_chars)

    def expand_query(self, question: str, strategy: Optional[str]) -> List[str]:
        """Extra search queries for ``multi_query`` (reformulations) or ``hyde``
        (a hypothetical answer passage, which embeds closer to real answers than
        a short question does). Returns [] for ``none`` or on LLM failure."""
        strategy = (strategy or os.getenv("QUERY_EXPANSION", "none")).lower()
        if strategy == "none":
            return []
        if strategy not in QUERY_EXPANSIONS:
            raise ValueError(f"Unknown query expansion: {strategy}. Use one of {sorted(QUERY_EXPANSIONS)}")
        try:
            if strategy == "multi_query":
                count = int(os.getenv("MULTI_QUERY_COUNT", "3"))
                raw = self.llm_service.generate(
                    MULTI_QUERY_TEMPLATE.format(count=count, question=question), temperature=0.7, max_tokens=512
                )
                queries = [line.strip(" -*0123456789.)\t") for line in raw.splitlines()]
                return [q for q in queries if q and q.lower() != question.lower()][:count]
            passage = self.llm_service.generate(HYDE_TEMPLATE.format(question=question), temperature=0.3, max_tokens=400)
            return [passage.strip()] if passage.strip() else []
        except Exception as e:
            logger.warning(f"Query expansion ({strategy}) failed, searching the original question only: {e}")
            return []

    def condense_question(self, question: str, history_text: str) -> str:
        """Rewrite a follow-up question into a standalone one, using the conversation."""
        _, _, enabled = _history_settings()
        if not history_text or not enabled:
            return question
        try:
            standalone = self.llm_service.generate(
                CONDENSE_QUESTION_TEMPLATE.format(history=history_text, question=question),
                temperature=0.0,
                max_tokens=256,
            ).strip()
        except Exception as e:
            logger.warning(f"Question condensation failed, using the raw question: {e}")
            return question
        return standalone or question

    def retrieve(
        self,
        question: str,
        *,
        top_k: int = 4,
        filters: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
        max_age_days: Optional[int] = None,
        user_permissions: Optional[List[str]] = None,
        rerank: Optional[bool] = None,
        search_mode: Optional[str] = None,
        extra_queries: Optional[List[str]] = None,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Search the vector store, re-ranking when enabled. Returns (results, reranked).

        ``extra_queries`` (multi-query reformulations, HyDE passage) are searched
        too and fused with the main query's results by Reciprocal Rank Fusion;
        re-ranking always scores candidates against the original question.
        """
        use_reranker = self.reranker is not None and (rerank is None or rerank)
        if rerank and self.reranker is None:
            logger.warning("Re-ranking requested but no reranker is available")

        # Fetch more candidates for re-ranking
        candidates = max(self.rerank_top_k, top_k) if use_reranker else top_k
        result_lists = [
            self.vector_store.similarity_search(
                query=search_query,
                k=candidates,
                filters=filters,
                min_score=min_score,
                max_age_days=max_age_days,
                user_permissions=user_permissions,
                mode=search_mode,
            )
            for search_query in [question, *(extra_queries or [])]
        ]
        search_results = result_lists[0] if len(result_lists) == 1 else reciprocal_rank_fusion(result_lists, k=candidates)

        if use_reranker and search_results:
            original_by_id = {doc["id"]: doc for doc in search_results}
            ranked_docs = self.reranker.rerank(question, search_results, top_k=top_k)
            search_results = [
                {
                    "id": doc.document_id,
                    "document_id": (original_by_id.get(doc.document_id) or {}).get("document_id"),
                    "content": doc.content,
                    "snippet": doc.snippet,
                    "source": doc.source,
                    "score": doc.rerank_score,
                    "metadata": doc.metadata or {},
                }
                for doc in ranked_docs
            ]
            logger.info(f"Re-ranked {len(ranked_docs)} documents")
        return search_results, use_reranker

    def build_prompt(
        self,
        question: str,
        search_results: List[Dict[str, Any]],
        prompt_type: str,
        history_text: str = "",
    ) -> str:
        context = self._format_context(search_results)
        prompt = None
        if prompt_type != "default":
            try:
                from .prompts import build_prompt

                prompt = build_prompt(prompt_type=prompt_type, query=question, context=context, sources=search_results)
            except Exception as e:
                logger.warning(f"Failed to build custom prompt: {e}. Using default.")
        if prompt is None:
            prompt = self.prompt_template.format(context=context, question=question)
        if history_text:
            prompt = (
                "Previous conversation (use it only to understand the latest question; "
                f"facts must still come from the context):\n{history_text}\n\n{prompt}"
            )
        return prompt

    @staticmethod
    def format_sources(search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "document_id": doc.get("document_id") or doc["id"],
                "chunk_id": doc["id"],
                "snippet": doc.get("snippet", doc["content"][:200] + "..." if len(doc["content"]) > 200 else doc["content"]),
                "score": doc["score"],
                "source": doc.get("source", "unknown"),
            }
            for doc in search_results
        ]

    def _format_context(self, search_results: List[Dict[str, Any]]) -> str:
        """Format retrieved documents into context string."""
        if not search_results:
            return "(No relevant context found)"

        context_parts = []
        for idx, doc in enumerate(search_results, 1):
            source = doc.get("source", "unknown")
            content = doc["content"]
            context_parts.append(f"[{idx}] Source: {source}\n{content}\n")

        return "\n".join(context_parts)
