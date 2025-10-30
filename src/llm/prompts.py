"""
Enhanced prompt templates for RAG with anti-hallucination measures.

These prompts enforce strict grounding, citation requirements, and
proper handling of edge cases (contradictions, insufficient info, etc.).
"""

from typing import List, Optional


class PromptTemplate:
    """Base class for prompt templates."""
    
    def format(self, **kwargs) -> str:
        """Format the prompt with provided variables."""
        raise NotImplementedError


class StrictRAGPrompt(PromptTemplate):
    """
    Strict RAG prompt that enforces grounding in source documents.
    
    Features:
    - Explicit rules against hallucination
    - Citation requirements
    - Handling of contradictions
    - Clear "I don't know" instructions
    """
    
    TEMPLATE = """You are a helpful AI assistant that answers questions based ONLY on the provided context.

STRICT RULES - YOU MUST FOLLOW THESE:
1. ❌ NEVER invent, assume, or add information not present in the context
2. ✅ ALWAYS cite your sources using [Source 1], [Source 2], etc.
3. ✅ If information is NOT in the context, say "I don't have enough information to answer this"
4. ✅ If sources contradict each other, explicitly mention: "The sources provide conflicting information..."
5. ✅ Be precise and factual - use exact quotes when possible
6. ✅ If you're uncertain about something, express that uncertainty clearly

CONTEXT (Retrieved Documents):
{context}

QUESTION: {query}

ANSWER (following all rules above):"""

    def format(self, query: str, context: str, **kwargs) -> str:
        """Format the strict RAG prompt."""
        return self.TEMPLATE.format(query=query, context=context)


class CitationEnforcedPrompt(PromptTemplate):
    """
    Prompt that requires inline citations for every claim.
    
    Best for: Legal, medical, financial domains where accuracy is critical.
    """
    
    TEMPLATE = """You are a precise AI assistant that provides well-cited answers.

YOUR TASK:
Answer the question using ONLY the information in the provided sources.
CITE every factual statement with [Source N] immediately after.

CITATION FORMAT EXAMPLE:
"Artificial Intelligence is the simulation of human intelligence [Source 1]. 
It has applications in healthcare, finance, and transportation [Source 2, Source 3]."

IMPORTANT RULES:
- Every claim must have a citation
- If no source supports a claim, DON'T make that claim
- Use exact wording from sources when possible
- If sources are insufficient, say: "The provided sources do not contain enough information to answer this question."

SOURCES:
{sources_formatted}

QUESTION: {query}

YOUR CITED ANSWER:"""

    def format(
        self,
        query: str,
        sources: List[dict],
        **kwargs
    ) -> str:
        """Format with numbered sources."""
        sources_formatted = self._format_sources(sources)
        return self.TEMPLATE.format(
            query=query,
            sources_formatted=sources_formatted
        )
    
    def _format_sources(self, sources: List[dict]) -> str:
        """Format sources with numbers for citation."""
        formatted = []
        for idx, source in enumerate(sources, 1):
            content = source.get('content', '') or source.get('snippet', '')
            source_name = source.get('source', 'unknown')
            formatted.append(f"[Source {idx}] ({source_name}):\n{content}\n")
        return '\n'.join(formatted)


class ContradictionHandlingPrompt(PromptTemplate):
    """
    Prompt designed to handle contradictory information in sources.
    
    Useful when: Multiple documents may have conflicting information.
    """
    
    TEMPLATE = """You are a critical-thinking AI assistant that analyzes information carefully.

YOUR TASK:
Answer the question using the provided context. Pay special attention to:
1. Consistency across sources
2. Contradictions or disagreements
3. Strength of evidence

IF SOURCES AGREE:
Provide a clear answer citing all supporting sources.

IF SOURCES CONTRADICT:
1. Acknowledge the contradiction explicitly
2. Present both viewpoints fairly
3. Note if one source seems more authoritative/recent
4. DO NOT pick a side unless evidence strongly supports it

FORMAT YOUR ANSWER:
- Start with direct answer (if sources agree) OR "The sources provide conflicting information" (if they don't)
- Explain the evidence
- Cite sources: [Source 1], [Source 2], etc.

CONTEXT:
{context}

QUESTION: {query}

ANALYSIS AND ANSWER:"""

    def format(self, query: str, context: str, **kwargs) -> str:
        """Format the contradiction-handling prompt."""
        return self.TEMPLATE.format(query=query, context=context)


class ConfidenceAwarePrompt(PromptTemplate):
    """
    Prompt that asks LLM to express confidence levels.
    
    Helps users understand answer reliability.
    """
    
    TEMPLATE = """You are an honest AI assistant that expresses uncertainty when appropriate.

YOUR TASK:
Answer the question based on the provided context, and indicate your confidence level.

CONFIDENCE LEVELS:
- HIGH: Information is clearly stated in multiple sources
- MEDIUM: Information is present but limited or from a single source
- LOW: Information requires inference or is partially covered
- NONE: Information is not available in the sources

FORMAT:
**Confidence: [HIGH/MEDIUM/LOW/NONE]**

**Answer:** 
[Your answer here, citing sources with [Source N]]

**Reasoning:**
[Brief explanation of why you chose this confidence level]

CONTEXT:
{context}

QUESTION: {query}

YOUR RESPONSE:"""

    def format(self, query: str, context: str, **kwargs) -> str:
        """Format the confidence-aware prompt."""
        return self.TEMPLATE.format(query=query, context=context)


class StructuredRAGPrompt(PromptTemplate):
    """
    Prompt that requests structured output (JSON-like format).
    
    Useful for: API responses, structured data extraction.
    """
    
    TEMPLATE = """You are an AI assistant that provides structured, well-formatted answers.

YOUR TASK:
Answer the question using the provided context and structure your response as follows:

STRUCTURE:
1. **Direct Answer:** [One sentence summary]
2. **Detailed Explanation:** [2-3 paragraphs with full details]
3. **Sources Used:** [List of source citations: Source 1, Source 2, etc.]
4. **Confidence:** [HIGH/MEDIUM/LOW]
5. **Limitations:** [Any gaps in the information or caveats]

RULES:
- Base answer ONLY on provided context
- If information is missing, state it clearly in "Limitations"
- Cite sources inline using [Source N]

CONTEXT:
{context}

QUESTION: {query}

STRUCTURED ANSWER:"""

    def format(self, query: str, context: str, **kwargs) -> str:
        """Format the structured prompt."""
        return self.TEMPLATE.format(query=query, context=context)


class PromptBuilder:
    """
    Factory for creating and customizing prompts.
    
    Makes it easy to select and configure the right prompt for each use case.
    """
    
    PROMPT_TYPES = {
        'strict': StrictRAGPrompt,
        'citation': CitationEnforcedPrompt,
        'contradiction': ContradictionHandlingPrompt,
        'confidence': ConfidenceAwarePrompt,
        'structured': StructuredRAGPrompt
    }
    
    @classmethod
    def get_prompt(
        cls,
        prompt_type: str = 'strict',
        **kwargs
    ) -> PromptTemplate:
        """
        Get a prompt template by type.
        
        Args:
            prompt_type: One of 'strict', 'citation', 'contradiction', 
                        'confidence', 'structured'
            **kwargs: Additional prompt configuration
        
        Returns:
            PromptTemplate instance
        """
        if prompt_type not in cls.PROMPT_TYPES:
            raise ValueError(
                f"Unknown prompt type: {prompt_type}. "
                f"Available: {list(cls.PROMPT_TYPES.keys())}"
            )
        
        return cls.PROMPT_TYPES[prompt_type]()
    
    @classmethod
    def create_custom_prompt(
        cls,
        system_message: str,
        context_template: str = "{context}",
        query_template: str = "{query}"
    ) -> PromptTemplate:
        """
        Create a custom prompt template.
        
        Args:
            system_message: Instructions for the AI
            context_template: How to format context
            query_template: How to format query
        
        Returns:
            Custom PromptTemplate
        """
        class CustomPrompt(PromptTemplate):
            def format(self, query: str, context: str, **kwargs) -> str:
                return f"{system_message}\n\n{context_template.format(context=context)}\n\n{query_template.format(query=query)}"
        
        return CustomPrompt()


# Convenience function
def build_prompt(
    prompt_type: str = 'strict',
    query: str = '',
    context: str = '',
    sources: Optional[List[dict]] = None,
    **kwargs
) -> str:
    """
    Build a complete prompt in one call.
    
    Args:
        prompt_type: Type of prompt to use
        query: User query
        context: Retrieved context (for most prompts)
        sources: Source documents (for citation prompt)
        **kwargs: Additional formatting arguments
    
    Returns:
        Formatted prompt string
    """
    prompt_template = PromptBuilder.get_prompt(prompt_type)
    
    if prompt_type == 'citation' and sources:
        return prompt_template.format(query=query, sources=sources, **kwargs)
    else:
        return prompt_template.format(query=query, context=context, **kwargs)
