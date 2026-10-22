"""Flask error handlers returning structured JSON responses."""

from __future__ import annotations

from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from ..ingestion.loaders import UnsupportedDocumentTypeError
from ..ingestion.url_fetcher import UnsafeURLError
from ..llm.service import LLMRefusalError
from ..observability import get_logger
from .schemas import ErrorResponse

logger = get_logger(__name__)


def register_error_handlers(app) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        response = ErrorResponse(
            code=error.name.lower().replace(" ", "_"),
            message=error.description,
            details=[{"status_code": error.code}],
        )
        return jsonify({"error": response.model_dump()}), error.code

    @app.errorhandler(UnsafeURLError)
    def handle_unsafe_url(error: UnsafeURLError):
        response = ErrorResponse(code="unsafe_url", message=str(error))
        return jsonify({"error": response.model_dump()}), 400

    @app.errorhandler(UnsupportedDocumentTypeError)
    def handle_unsupported_document(error: UnsupportedDocumentTypeError):
        response = ErrorResponse(code="unsupported_document_type", message=str(error))
        return jsonify({"error": response.model_dump()}), 415

    @app.errorhandler(LLMRefusalError)
    def handle_refusal(error: LLMRefusalError):
        response = ErrorResponse(code="llm_refusal", message=str(error))
        return jsonify({"error": response.model_dump()}), 422

    @app.errorhandler(422)
    def handle_unprocessable(error):  # pragma: no cover - handled similarly to HTTPException
        response = ErrorResponse(
            code="validation_error",
            message="Request payload validation failed",
            details=[{"reason": getattr(error, "description", "invalid payload")}],
        )
        return jsonify({"error": response.model_dump()}), 422

    @app.errorhandler(Exception)
    def handle_generic_error(error: Exception):
        # Internal details (hosts, stack traces, provider errors) stay in the logs.
        logger.exception("Unhandled error: {}", error)
        response = ErrorResponse(
            code="internal_server_error",
            message="An internal error occurred",
        )
        return jsonify({"error": response.model_dump()}), 500
