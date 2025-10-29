"""RAG retrieval service combining vector search and LLM generation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..vector_store.elasticsearch_store import ElasticsearchVectorStore
from .service import LLMService


DEFAULT_PROMPT_TEMPLATE = """You are an AI assistant helping users find information from documents.

Use the following context snippets to answer the user's question. If the context doesn't contain relevant information, say so clearly.

Context:
{context}

Question: {question}

Answer:"""


class RAGService:
    """Retrieval-Augmented Generation orchestrator."""

    def __init__(
        self,
        vector_store: ElasticsearchVectorStore,
        llm_service: LLMService,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
    ) -> None:
        self.vector_store = vector_store
        self.llm_service = llm_service
        self.prompt_template = prompt_template

    def query(
        self,
        question: str,
        *,
        top_k: int = 4,
        filters: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        include_sources: bool = True,
    ) -> Dict[str, Any]:
        """Execute RAG query: retrieve context and generate answer."""
        # Retrieve relevant documents
        search_results = self.vector_store.similarity_search(
            query=question,
            k=top_k,
            filters=filters,
        )

        # Build context from retrieved snippets
        context = self._format_context(search_results)

        # Generate answer using LLM
        prompt = self.prompt_template.format(context=context, question=question)
        answer = self.llm_service.generate(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        response = {"answer": answer, "metadata": {"top_k": top_k, "retrieved_docs": len(search_results)}}

        if include_sources:
            response["sources"] = [
                {
                    "document_id": doc["id"],
                    "snippet": doc["content"][:200] + "..." if len(doc["content"]) > 200 else doc["content"],
                    "score": doc["score"],
                    "source": doc.get("source", "unknown"),
                }
                for doc in search_results
            ]

        return response

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
