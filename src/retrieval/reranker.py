"""
Re-ranking service using cross-encoder models for improved retrieval quality.

The cross-encoder re-ranks retrieved documents by computing relevance scores
between the query and each document, providing better results than semantic
similarity alone.
"""

import os
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RankedDocument:
    """Document with re-ranking score."""
    document_id: str
    content: str
    snippet: str
    source: str
    original_score: float
    rerank_score: float
    metadata: dict = None


class CrossEncoderReranker:
    """
    Re-ranks documents using a cross-encoder model.
    
    Cross-encoders compute direct relevance scores between query-document pairs,
    typically providing better quality than bi-encoder (embedding) similarity.
    """
    
    def __init__(
        self, 
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        device: Optional[str] = None
    ):
        """
        Initialize the cross-encoder reranker.
        
        Args:
            model_name: Name of the cross-encoder model from sentence-transformers
            device: Device to run on ('cuda', 'cpu', or None for auto-detect)
        """
        self.model_name = model_name
        self.device = device or self._detect_device()
        self._model = None
        
        logger.info(f"Initializing CrossEncoderReranker with model: {model_name} on {self.device}")
    
    def _detect_device(self) -> str:
        """Detect available device (CUDA or CPU)."""
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"
    
    @property
    def model(self):
        """Lazy load the model to avoid loading during import."""
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_name, device=self.device)
                logger.info(f"Cross-encoder model loaded successfully on {self.device}")
            except Exception as e:
                logger.error(f"Failed to load cross-encoder model: {e}")
                raise
        return self._model
    
    def rerank(
        self,
        query: str,
        documents: List[dict],
        top_k: Optional[int] = None
    ) -> List[RankedDocument]:
        """
        Re-rank documents based on query relevance.
        
        Args:
            query: User query
            documents: List of documents from vector store (with 'content', 'score', etc.)
            top_k: Number of top documents to return (None = return all)
        
        Returns:
            List of RankedDocument objects sorted by rerank_score (descending)
        """
        if not documents:
            logger.warning("No documents provided for re-ranking")
            return []
        
        logger.info(f"Re-ranking {len(documents)} documents for query: '{query[:50]}...'")
        
        try:
            # Prepare query-document pairs
            pairs = []
            for doc in documents:
                content = doc.get('content', '') or doc.get('snippet', '')
                pairs.append([query, content])
            
            # Compute cross-encoder scores
            scores = self.model.predict(pairs)
            
            # Create ranked documents
            ranked_docs = []
            for idx, (doc, rerank_score) in enumerate(zip(documents, scores)):
                ranked_doc = RankedDocument(
                    document_id=doc.get('document_id', f"doc_{idx}"),
                    content=doc.get('content', ''),
                    snippet=doc.get('snippet', doc.get('content', '')[:200]),
                    source=doc.get('source', 'unknown'),
                    original_score=doc.get('score', 0.0),
                    rerank_score=float(rerank_score),
                    metadata=doc.get('metadata', {})
                )
                ranked_docs.append(ranked_doc)
            
            # Sort by rerank score (descending)
            ranked_docs.sort(key=lambda x: x.rerank_score, reverse=True)
            
            # Apply top_k limit
            if top_k is not None:
                ranked_docs = ranked_docs[:top_k]
            
            logger.info(
                f"Re-ranking complete. Top score: {ranked_docs[0].rerank_score:.4f}, "
                f"Bottom score: {ranked_docs[-1].rerank_score:.4f}"
            )
            
            return ranked_docs
            
        except Exception as e:
            logger.error(f"Error during re-ranking: {e}", exc_info=True)
            # Fallback: return original documents without re-ranking
            return [
                RankedDocument(
                    document_id=doc.get('document_id', f"doc_{i}"),
                    content=doc.get('content', ''),
                    snippet=doc.get('snippet', '')[:200],
                    source=doc.get('source', 'unknown'),
                    original_score=doc.get('score', 0.0),
                    rerank_score=doc.get('score', 0.0),  # Use original score as fallback
                    metadata=doc.get('metadata', {})
                )
                for i, doc in enumerate(documents)
            ]
    
    def batch_rerank(
        self,
        queries: List[str],
        document_lists: List[List[dict]],
        top_k: Optional[int] = None
    ) -> List[List[RankedDocument]]:
        """
        Re-rank multiple queries in batch for efficiency.
        
        Args:
            queries: List of user queries
            document_lists: List of document lists (one per query)
            top_k: Number of top documents to return per query
        
        Returns:
            List of ranked document lists
        """
        results = []
        for query, docs in zip(queries, document_lists):
            ranked = self.rerank(query, docs, top_k)
            results.append(ranked)
        return results


class HybridScorer:
    """
    Combines semantic similarity scores with cross-encoder scores.
    
    Useful for balancing fast semantic search with accurate re-ranking.
    """
    
    def __init__(
        self,
        semantic_weight: float = 0.3,
        rerank_weight: float = 0.7
    ):
        """
        Initialize hybrid scorer.
        
        Args:
            semantic_weight: Weight for original semantic similarity (0-1)
            rerank_weight: Weight for cross-encoder re-rank score (0-1)
        """
        if not (0 <= semantic_weight <= 1 and 0 <= rerank_weight <= 1):
            raise ValueError("Weights must be between 0 and 1")
        
        if abs(semantic_weight + rerank_weight - 1.0) > 0.01:
            logger.warning(
                f"Weights don't sum to 1.0 (semantic={semantic_weight}, "
                f"rerank={rerank_weight}). Normalizing..."
            )
            total = semantic_weight + rerank_weight
            semantic_weight /= total
            rerank_weight /= total
        
        self.semantic_weight = semantic_weight
        self.rerank_weight = rerank_weight
        
        logger.info(
            f"HybridScorer initialized: semantic={semantic_weight:.2f}, "
            f"rerank={rerank_weight:.2f}"
        )
    
    def compute_hybrid_score(self, ranked_doc: RankedDocument) -> float:
        """
        Compute weighted combination of semantic and rerank scores.
        
        Args:
            ranked_doc: Document with both original_score and rerank_score
        
        Returns:
            Hybrid score (weighted average)
        """
        # Normalize scores to 0-1 range if needed
        semantic_norm = self._normalize_score(ranked_doc.original_score)
        rerank_norm = self._normalize_score(ranked_doc.rerank_score)
        
        hybrid = (
            self.semantic_weight * semantic_norm +
            self.rerank_weight * rerank_norm
        )
        
        return hybrid
    
    def _normalize_score(self, score: float) -> float:
        """Normalize score to 0-1 range."""
        # Cosine similarity is already -1 to 1, map to 0-1
        if -1 <= score <= 1:
            return (score + 1) / 2
        # If already in 0-1 range or positive, assume normalized
        return max(0, min(1, score))
    
    def rerank_with_hybrid(
        self,
        ranked_docs: List[RankedDocument]
    ) -> List[RankedDocument]:
        """
        Re-sort documents using hybrid scores.
        
        Args:
            ranked_docs: Documents with both semantic and rerank scores
        
        Returns:
            Documents sorted by hybrid score
        """
        # Compute hybrid scores
        for doc in ranked_docs:
            doc.hybrid_score = self.compute_hybrid_score(doc)
        
        # Sort by hybrid score
        ranked_docs.sort(key=lambda x: getattr(x, 'hybrid_score', 0), reverse=True)
        
        return ranked_docs
