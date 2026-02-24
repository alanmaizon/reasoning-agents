"""Per-request observability context helpers."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_request_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "mdt_request_id",
    default=None,
)


def set_request_id(request_id: str) -> Token:
    """Bind request ID to the current execution context."""
    return _request_id_ctx.set(request_id)


def get_request_id() -> Optional[str]:
    """Return current request ID from context, when available."""
    return _request_id_ctx.get()


def reset_request_id(token: Token) -> None:
    """Reset request ID context to previous value."""
    _request_id_ctx.reset(token)
