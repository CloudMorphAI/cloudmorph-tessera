"""Common exceptions used across the executor lifecycle."""

from __future__ import annotations

from typing import Any


class CloudMorphCommonError(Exception):
    """Base for all common-py errors."""


class ConfigError(CloudMorphCommonError):
    """Raised when env / settings are invalid or missing."""


class BaseExecutorError(CloudMorphCommonError):
    """Raised by BaseExecutor for lifecycle problems (claim, heartbeat, complete)."""


class ArtifactUploadError(CloudMorphCommonError):
    """Raised when one or more artifact writes fail."""

    def __init__(self, message: str, partial_failures: dict[str, Any] | None = None):
        super().__init__(message)
        self.partial_failures: dict[str, Any] = partial_failures or {}


class AuditSinkError(CloudMorphCommonError):
    """Raised when an audit sink fails to emit. Should be caught and trigger buffered fallback."""

    def __init__(self, sink: str, message: str):
        super().__init__(f"audit sink '{sink}' failed: {message}")
        self.sink = sink
