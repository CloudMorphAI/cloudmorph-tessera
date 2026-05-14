"""Tessera exception hierarchy."""

from __future__ import annotations


class TesseraError(Exception):
    """Base class for all Tessera errors."""


class ConfigError(TesseraError):
    """Raised when configuration is invalid or missing."""


class PolicyError(TesseraError):
    """Raised when a policy file fails validation."""

    def __init__(self, message: str, *, reason: str | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.path = path


class AuditSinkError(TesseraError):
    """Raised when an audit sink fails to persist an event."""


class UpstreamError(TesseraError):
    """Raised when an upstream MCP server returns an error or times out."""


class UnauthorizedError(TesseraError):
    """Raised when a request lacks valid authentication."""


class TamperDetected(TesseraError):  # noqa: N818 — public API name; renaming would break callers
    """Raised when a downloaded artifact's hash or signature does not match the manifest."""
