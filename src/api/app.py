"""Flask application factory."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

from .dependencies import register_dependencies
from .error_handlers import register_error_handlers
from .routes import api_bp
from ..observability import configure_logging, register_metrics


def create_app(config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__)
    app.config.update(
        {
            "APP_VERSION": os.getenv("APP_VERSION", "0.1.0"),
            "SECRET_KEY": os.getenv("SECRET_KEY", "changeme-in-production"),
            "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
            "LOG_FILE": os.getenv("LOG_FILE"),
        }
    )
    if config:
        app.config.update(config)

    # Configure structured logging
    configure_logging(
        log_level=app.config["LOG_LEVEL"],
        log_file=app.config.get("LOG_FILE"),
    )

    CORS(app)
    register_dependencies(app)
    register_error_handlers(app)
    app.register_blueprint(api_bp)

    # Register Prometheus metrics
    register_metrics(app)

    return app


def main() -> None:  # pragma: no cover - CLI entrypoint
    app = create_app()
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "8000")),
        debug=os.getenv("FLASK_ENV") == "development",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
