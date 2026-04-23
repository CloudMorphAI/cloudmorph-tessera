"""ArtifactWriter base interface."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from cloudmorph_common.errors import ArtifactUploadError


class ArtifactWriter(ABC):
    """Write a job's outputs (summary, result, logs) to remote storage.

    Subclasses implement `write` for their cloud's storage backend.
    The base class handles the common artifact shape.
    """

    @abstractmethod
    def write(
        self,
        job_id: str,
        result: dict[str, Any],
        logs: str | None = None,
    ) -> list[dict[str, Any]]:
        """Write a job's artifacts. Returns list of pointer dicts.

        Each pointer has the shape returned by `cloudmorph_common.storage_pointers.build_pointer`.

        Raises:
            ArtifactUploadError: on any partial or full failure. Caller should
                still attempt to post_complete with status=failed and the
                partial pointer list (in exc.partial_failures).
        """
        ...

    @staticmethod
    def _serialize_summary(result: dict[str, Any]) -> bytes:
        text = str(result.get("summary") or f"Job {result.get('status', 'completed')}")[: 8 * 1024]
        return text.encode("utf-8")

    @staticmethod
    def _serialize_result(result: dict[str, Any]) -> bytes:
        body = result.get("result", result)
        return json.dumps(body, default=str).encode("utf-8")

    @staticmethod
    def _serialize_logs(logs: str | None) -> bytes | None:
        if not logs:
            return None
        return str(logs).encode("utf-8")


class NoOpArtifactWriter(ArtifactWriter):
    """Writer that returns an empty pointer list. For tests, dry-runs, and offline mode."""

    def write(
        self,
        job_id: str,
        result: dict[str, Any],
        logs: str | None = None,
    ) -> list[dict[str, Any]]:
        return []


# Re-export for convenience; the import shape `from cloudmorph_common.errors import ...` is preferred.
__all__ = ["ArtifactUploadError", "ArtifactWriter", "NoOpArtifactWriter"]
