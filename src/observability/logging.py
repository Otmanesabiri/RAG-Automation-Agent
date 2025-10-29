"""Structured logging configuration with Loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def configure_logging(log_level: str = "INFO", log_file: str | None = None) -> None:
    """Configure Loguru structured logging.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for log persistence
    """
    # Remove default handler
    logger.remove()

    # Console handler with colorized output
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # File handler with JSON serialization for production
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_file,
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="100 MB",
            retention="30 days",
            compression="zip",
            serialize=False,  # Set to True for JSON output
        )

    logger.info(f"Logging configured with level={log_level}, file={log_file}")


def get_logger(name: str):
    """Get a logger instance bound to a specific module name.

    Args:
        name: Module name (typically __name__)

    Returns:
        Loguru logger with module context
    """
    return logger.bind(module=name)
