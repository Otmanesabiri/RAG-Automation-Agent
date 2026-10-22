"""RAG retrieval service combining vector search and LLM generation."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

from ..vector_store.elasticsearch_store import ElasticsearchVectorStore
from .service import LLMService
from .prompts import PromptBuilder, StrictRAGPrompt

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

        Returns:
            Dict with answer, sources, metadata, and optional citation check
        """
        search_results, reranked = self.retrieve(
            question,
            top_k=top_k,
            filters=filters,
            min_score=min_score,
            max_age_days=max_age_days,
            user_permissions=user_permissions,
            rerank=rerank,
        )
        active_prompt_type = prompt_type or self.prompt_type
        prompt = self.build_prompt(question, search_results, active_prompt_type)

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
            }
        }

        # Add citation verification if enabled
        if self.citation_verifier and search_results:
            try:
                citation_check = self.citation_verifier.verify(
                    answer=answer,
                    sources=search_results,
                    check_claims=True
                )
                response["citation_check"] = {
                    "is_grounded": citation_check.is_grounded,
                    "confidence": citation_check.confidence,
                    "warnings": citation_check.warnings,
                }
                if not citation_check.is_grounded:
                    logger.warning(
                        f"Low citation confidence: {citation_check.confidence:.2%}"
                    )
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
    ) -> Iterator[Dict[str, Any]]:
        """Stream a RAG answer as events: ``sources`` first, then ``token``s, then ``done``."""
        search_results, reranked = self.retrieve(
            question,
            top_k=top_k,
            filters=filters,
            min_score=min_score,
            max_age_days=max_age_days,
            rerank=rerank,
        )
        active_prompt_type = prompt_type or self.prompt_type
        prompt = self.build_prompt(question, search_results, active_prompt_type)
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
                },
            },
        }

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
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Search the vector store, re-ranking when enabled. Returns (results, reranked)."""
        use_reranker = self.reranker is not None and (rerank is None or rerank)
        if rerank and self.reranker is None:
            logger.warning("Re-ranking requested but no reranker is available")

        search_results = self.vector_store.similarity_search(
            query=question,
            # Fetch more candidates for re-ranking
            k=max(self.rerank_top_k, top_k) if use_reranker else top_k,
            filters=filters,
            min_score=min_score,
            max_age_days=max_age_days,
            user_permissions=user_permissions,
        )

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
                    "metadata": doc.metadata or {}
                }
                for doc in ranked_docs
            ]
            logger.info(f"Re-ranked {len(ranked_docs)} documents")
        return search_results, use_reranker

    def build_prompt(self, question: str, search_results: List[Dict[str, Any]], prompt_type: str) -> str:
        context = self._format_context(search_results)
        if prompt_type != "default":
            try:
                from .prompts import build_prompt
                return build_prompt(
                    prompt_type=prompt_type,
                    query=question,
                    context=context,
                    sources=search_results
                )
            except Exception as e:
                logger.warning(f"Failed to build custom prompt: {e}. Using default.")
        return self.prompt_template.format(context=context, question=question)

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
