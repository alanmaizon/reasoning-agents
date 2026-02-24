"""Structured logging bootstrap for API/runtime components."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from .context import get_request_id

_BASE_LOG_RECORD_FIELDS = set(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
)


class _RequestContextFilter(logging.Filter):
    """Inject request-scoped context into logs when missing."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = getattr(record, "request_id", None)
        if request_id is None:
            ctx_request_id = get_request_id()
            if ctx_request_id:
                record.request_id = ctx_request_id
        return True


class _JsonFormatter(logging.Formatter):
    """Emit compact JSON log lines with common request fields."""

    _fields = (
        "event",
        "request_id",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "client_ip",
        "user_agent",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self._fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        for key, value in record.__dict__.items():
            if (
                key in _BASE_LOG_RECORD_FIELDS
                or key in payload
                or key.startswith("_")
            ):
                continue
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure root logger with structured output once."""
    root = logging.getLogger()
    if getattr(root, "_mdt_logging_configured", False):
        return

    level_name = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get("APP_LOG_FORMAT", "json").lower().strip()

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestContextFilter())
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Keep uvicorn output in same stream/formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    root._mdt_logging_configured = True
