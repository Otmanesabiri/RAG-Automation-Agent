"""Pydantic schemas for Flask API payload validation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, validator


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


class IngestRequest(BaseModel):
    source: str = Field(..., description="Origin of documents: upload or url.")
    documents: List[DocumentPayload]
    options: Optional[IngestOptions] = None


class QueryFilters(BaseModel):
    attributes: Dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    top_k: int = Field(4, ge=1, le=20)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    rerank: bool = False
    stream: bool = False


class QueryResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
