"""Structured logging bootstrap for API/runtime components."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


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
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Configure root logger with structured output once."""
    root = logging.getLogger()
    if getattr(root, "_mdt_logging_configured", False):
        return

    level_name = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get("APP_LOG_FORMAT", "json").lower().strip()

    handler = logging.StreamHandler(sys.stdout)
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

