"""Pydantic schemas for Flask API payload validation."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator, validator


class DocumentPayload(BaseModel):
    content: Optional[str] = Field(None, description="Raw text or base64-encoded payload.")
    filename: Optional[str] = Field(None, description="Original filename when provided by the client.")
    mime_type: Optional[str] = Field(None, description="Declared MIME type of the document.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    url: Optional[HttpUrl] = Field(None, description="Remote resource URL when ingesting via URL.")
    base64_encoded: bool = Field(False, description="Whether content is base64 encoded.")
    encoding: Optional[str] = Field("utf-8", description="Encoding used for plaintext content.")

    @validator("content")
    def validate_content_or_url(cls, value: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        if not value and not values.get("url"):
            raise ValueError("Either content or url must be provided for each document")
        return value


class IngestOptions(BaseModel):
    chunk_size: Optional[int] = Field(None, ge=100, le=2000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=1000)
    language: Optional[str] = Field(None, description="Language hint for tokenization.")

    @model_validator(mode="after")
    def check_overlap(self) -> "IngestOptions":
        if self.chunk_size and self.chunk_overlap is not None and self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class IngestRequest(BaseModel):
    source: str = Field(..., description="Origin of documents: upload or url.")
    documents: List[DocumentPayload]
    options: Optional[IngestOptions] = None


class QueryFilters(BaseModel):
    attributes: Dict[str, Any] = Field(default_factory=dict)


PromptType = Literal["default", "strict", "citation", "contradiction", "confidence", "structured"]


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = None
    top_k: int = Field(4, ge=1, le=20)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    rerank: Optional[bool] = Field(None, description="Force re-ranking on/off; null uses the server default.")
    stream: bool = Field(False, description="Stream the answer as Server-Sent Events.")
    prompt_type: Optional[PromptType] = None
    min_score: Optional[float] = Field(None, ge=0)
    max_age_days: Optional[int] = Field(None, ge=1)
    search_mode: Optional[Literal["dense", "sparse", "hybrid"]] = Field(
        None, description="kNN, BM25 or both fused with RRF; null uses SEARCH_MODE."
    )
    query_expansion: Optional[Literal["none", "multi_query", "hyde"]] = Field(
        None, description="Extra LLM-generated search queries; null uses QUERY_EXPANSION."
    )
    verify_citations: Optional[Literal["off", "lexical", "llm"]] = Field(
        None, description="Grade the answer against its sources; null uses CITATION_CHECK."
    )


class QueryResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    citation_check: Optional[Dict[str, Any]] = None


class SessionCreateRequest(BaseModel):
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    session_id: str
    metadata: Dict[str, Any]
    created_at: str
    user_id: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Optional[List[Dict[str, Any]]] = None
