"""
Citation verification service to validate LLM answers against source documents.

Helps reduce hallucinations by checking if generated answers are grounded in
the retrieved context.
"""

import re
import logging
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


@dataclass
class CitationCheck:
    """Result of citation verification."""
    is_grounded: bool
    confidence: float
    grounded_claims: List[str]
    ungrounded_claims: List[str]
    source_matches: Dict[str, List[int]]  # claim -> list of source indices
    warnings: List[str]


class CitationVerifier:
    """
    Verifies that LLM-generated answers are grounded in source documents.
    
    Uses multiple strategies:
    1. Direct text matching (substring search)
    2. Fuzzy matching (for paraphrasing)
    3. Semantic similarity (optional, with embeddings)
    """
    
    def __init__(
        self,
        similarity_threshold: float = 0.75,
        strict_mode: bool = False
    ):
        """
        Initialize citation verifier.
        
        Args:
            similarity_threshold: Minimum similarity for fuzzy matching (0-1)
            strict_mode: If True, requires exact/near-exact matches
        """
        self.similarity_threshold = similarity_threshold
        self.strict_mode = strict_mode
        
        logger.info(
            f"CitationVerifier initialized: threshold={similarity_threshold}, "
            f"strict_mode={strict_mode}"
        )
    
    def verify(
        self,
        answer: str,
        sources: List[dict],
        check_claims: bool = True
    ) -> CitationCheck:
        """
        Verify that answer is grounded in source documents.
        
        Args:
            answer: LLM-generated answer
            sources: List of source documents (with 'content' or 'snippet')
            check_claims: Whether to extract and check individual claims
        
        Returns:
            CitationCheck with verification results
        """
        if not sources:
            logger.warning("No sources provided for citation verification")
            return CitationCheck(
                is_grounded=False,
                confidence=0.0,
                grounded_claims=[],
                ungrounded_claims=[answer] if check_claims else [],
                source_matches={},
                warnings=["No sources available for verification"]
            )
        
        # Extract source texts
        source_texts = []
        for src in sources:
            text = src.get('content', '') or src.get('snippet', '')
            source_texts.append(text.lower())
        
        answer_lower = answer.lower()
        
        # Strategy 1: Check if entire answer is grounded
        overall_grounded = self._check_text_grounding(answer_lower, source_texts)
        
        grounded_claims = []
        ungrounded_claims = []
        source_matches = {}
        
        if check_claims:
            # Strategy 2: Extract and verify individual claims
            claims = self._extract_claims(answer)
            
            for claim in claims:
                claim_lower = claim.lower()
                matching_sources = []
                
                for idx, source_text in enumerate(source_texts):
                    if self._is_claim_in_source(claim_lower, source_text):
                        matching_sources.append(idx)
                
                if matching_sources:
                    grounded_claims.append(claim)
                    source_matches[claim] = matching_sources
                else:
                    ungrounded_claims.append(claim)
        
        # Calculate confidence
        if check_claims and claims:
            confidence = len(grounded_claims) / len(claims)
        else:
            confidence = 1.0 if overall_grounded else 0.0
        
        # Determine if answer is grounded
        is_grounded = confidence >= 0.7 or overall_grounded
        
        # Generate warnings
        warnings = []
        if confidence < 0.7:
            warnings.append(f"Low grounding confidence: {confidence:.2%}")
        if ungrounded_claims:
            warnings.append(f"{len(ungrounded_claims)} ungrounded claims detected")
        if self.strict_mode and not overall_grounded:
            warnings.append("Strict mode: answer not directly found in sources")
        
        logger.info(
            f"Citation check complete: grounded={is_grounded}, "
            f"confidence={confidence:.2%}, claims={len(grounded_claims)}/{len(claims) if claims else 0}"
        )
        
        return CitationCheck(
            is_grounded=is_grounded,
            confidence=confidence,
            grounded_claims=grounded_claims,
            ungrounded_claims=ungrounded_claims,
            source_matches=source_matches,
            warnings=warnings
        )
    
    def _check_text_grounding(self, text: str, sources: List[str]) -> bool:
        """
        Check if text appears in any source (with fuzzy matching).
        
        Args:
            text: Text to check (already lowercased)
            sources: Source texts to search in (already lowercased)
        
        Returns:
            True if text is found in sources
        """
        # Strategy 1: Direct substring match
        for source in sources:
            if text in source or source in text:
                return True
        
        # Strategy 2: Fuzzy matching for paraphrasing
        if not self.strict_mode:
            for source in sources:
                similarity = self._fuzzy_similarity(text, source)
                if similarity >= self.similarity_threshold:
                    return True
        
        return False
    
    def _is_claim_in_source(self, claim: str, source: str) -> bool:
        """
        Check if a claim is supported by a source.
        
        Args:
            claim: Individual claim (already lowercased)
            source: Source text (already lowercased)
        
        Returns:
            True if claim is found in source
        """
        # Remove common phrases that don't add meaning
        claim_cleaned = self._clean_claim(claim)
        
        if len(claim_cleaned) < 10:  # Too short to verify reliably
            return True  # Give benefit of doubt for short claims
        
        # Direct match
        if claim_cleaned in source:
            return True
        
        # Fuzzy match
        if not self.strict_mode:
            # Check similarity with sliding window
            claim_words = claim_cleaned.split()
            source_words = source.split()
            
            if len(claim_words) <= 3:  # Short claim, check full similarity
                return self._fuzzy_similarity(claim_cleaned, source) >= self.similarity_threshold
            
            # For longer claims, use sliding window
            window_size = min(len(claim_words), 20)
            for i in range(len(source_words) - window_size + 1):
                window = ' '.join(source_words[i:i + window_size])
                similarity = self._fuzzy_similarity(claim_cleaned, window)
                if similarity >= self.similarity_threshold:
                    return True
        
        return False
    
    def _extract_claims(self, answer: str) -> List[str]:
        """
        Extract individual claims from answer.
        
        Simple strategy: split by sentences and filter noise.
        
        Args:
            answer: Full answer text
        
        Returns:
            List of individual claims
        """
        # Split by sentence boundaries
        sentences = re.split(r'[.!?]+', answer)
        
        claims = []
        for sentence in sentences:
            sentence = sentence.strip()
            
            # Filter out non-claims
            if len(sentence) < 15:  # Too short
                continue
            if sentence.startswith(('I ', 'However', 'Note that', 'Please')):  # Meta-statements
                continue
            if '?' in sentence:  # Questions
                continue
            
            claims.append(sentence)
        
        return claims
    
    def _clean_claim(self, claim: str) -> str:
        """
        Clean claim by removing filler words and punctuation.
        
        Args:
            claim: Raw claim text
        
        Returns:
            Cleaned claim
        """
        # Remove common filler phrases
        fillers = [
            'according to the document',
            'based on the information',
            'the source states',
            'it is mentioned that',
            'the text says',
        ]
        
        claim_cleaned = claim
        for filler in fillers:
            claim_cleaned = claim_cleaned.replace(filler, '')
        
        # Remove extra whitespace
        claim_cleaned = ' '.join(claim_cleaned.split())
        
        return claim_cleaned
    
    def _fuzzy_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate fuzzy similarity between two texts.
        
        Uses SequenceMatcher for approximate string matching.
        
        Args:
            text1: First text
            text2: Second text
        
        Returns:
            Similarity score (0-1)
        """
        return SequenceMatcher(None, text1, text2).ratio()
    
    def format_citation_report(self, check: CitationCheck) -> str:
        """
        Format citation check results as human-readable report.
        
        Args:
            check: Citation check results
        
        Returns:
            Formatted report string
        """
        lines = [
            "=== Citation Verification Report ===",
            f"Overall Grounded: {check.is_grounded}",
            f"Confidence: {check.confidence:.2%}",
            "",
            f"Grounded Claims ({len(check.grounded_claims)}):",
        ]
        
        for claim in check.grounded_claims[:5]:  # Show first 5
            sources_str = ', '.join(f"[{i+1}]" for i in check.source_matches.get(claim, []))
            lines.append(f"  ✓ {claim[:80]}... (sources: {sources_str})")
        
        if len(check.grounded_claims) > 5:
            lines.append(f"  ... and {len(check.grounded_claims) - 5} more")
        
        if check.ungrounded_claims:
            lines.append("")
            lines.append(f"Ungrounded Claims ({len(check.ungrounded_claims)}):")
            for claim in check.ungrounded_claims[:3]:  # Show first 3
                lines.append(f"  ✗ {claim[:80]}...")
        
        if check.warnings:
            lines.append("")
            lines.append("Warnings:")
            for warning in check.warnings:
                lines.append(f"  ⚠ {warning}")
        
        return '\n'.join(lines)
