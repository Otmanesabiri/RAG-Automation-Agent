"""Flask error handlers returning structured JSON responses."""

from __future__ import annotations

from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from .schemas import ErrorResponse


def register_error_handlers(app) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        response = ErrorResponse(
            code=error.name.lower().replace(" ", "_"),
            message=error.description,
            details=[{"status_code": error.code}],
        )
        return jsonify({"error": response.model_dump()}), error.code

    @app.errorhandler(422)
    def handle_unprocessable(error):  # pragma: no cover - handled similarly to HTTPException
        response = ErrorResponse(
            code="validation_error",
            message="Request payload validation failed",
            details=[{"reason": getattr(error, "description", "invalid payload")}],
        )
        return jsonify({"error": response.model_dump()}), 422

    @app.errorhandler(Exception)
    def handle_generic_error(error: Exception):  # pragma: no cover - fallback handler
        response = ErrorResponse(
            code="internal_server_error",
            message=str(error),
        )
        return jsonify({"error": response.model_dump()}), 500
