"""JSON-line structured logger shared across executors.

Replaces the 5 per-executor `_log` helpers. Output is one JSON line to stdout
per call; container log drivers route to CloudWatch / Cloud Logging /
Stackdriver / Datadog from there.

Severity field is set per Cloud Logging convention so GCP routing parses
levels correctly. Other backends ignore it.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

LEVELS: dict[str, int] = {
    "debug": 10,
    "info": 20,
    "warn": 30,
    "warning": 30,
    "error": 40,
    "silent": 100,
}

SEVERITY_MAP: dict[str, str] = {
    "debug": "DEBUG",
    "info": "INFO",
    "warn": "WARNING",
    "warning": "WARNING",
    "error": "ERROR",
}


class StructuredLogger:
    """JSON-line structured logger.

    Args:
        service: Service identifier emitted on every line (e.g., "aws-executor").
        min_level: Minimum level to emit. Default "info".
        stream: Output stream. Defaults to stdout.

    Example::

        log = StructuredLogger("aws-executor")
        log.info("executor.starting", host="ip-10-0-0-1", pid=12345)
    """

    def __init__(
        self,
        service: str,
        min_level: str = "info",
        stream: Any = None,
    ):
        self.service = service
        self.min_threshold = LEVELS.get(min_level.lower(), LEVELS["info"])
        self.stream = stream if stream is not None else sys.stdout

    def _emit(self, level: str, message: str, fields: dict[str, Any]) -> None:
        if LEVELS.get(level, 0) < self.min_threshold:
            return
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level,
            "severity": SEVERITY_MAP.get(level, "DEFAULT"),
            "service": self.service,
            "message": message,
        }
        if fields:
            payload.update(fields)
        # Emit a single line; flush so log drivers see it immediately.
        print(json.dumps(payload, default=str), file=self.stream, flush=True)  # noqa: T201

    def debug(self, message: str, **fields: Any) -> None:
        self._emit("debug", message, fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit("info", message, fields)

    def warn(self, message: str, **fields: Any) -> None:
        self._emit("warn", message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit("warning", message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit("error", message, fields)
