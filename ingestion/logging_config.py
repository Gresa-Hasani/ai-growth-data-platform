"""Structured logging setup for the ingestion framework.

Phase 1 used stdlib `logging` with `basicConfig` for the API. The ingestion
pipeline needs structured (key=value) fields per Phase 2's requirements
(run_id, source_name, batch_number, rows_*, duration_ms, status), so this
module wires up `structlog` on top of the same stdlib logging backend rather
than introducing a second, incompatible logging stack.
"""
from __future__ import annotations

import logging
import os

import structlog


def configure_logging() -> None:
    """Configure stdlib logging + structlog once per process."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
