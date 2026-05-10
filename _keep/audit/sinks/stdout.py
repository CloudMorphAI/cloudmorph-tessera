"""Stdout audit sink. Always-on default. Container log driver collects."""

from __future__ import annotations

import json
import sys
from typing import Any


class StdoutSink:
    """Writes one JSON line per event to stdout (or a configured stream).

    Args:
        stream: Output stream. Defaults to sys.stdout.
    """

    name: str = "stdout"

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream if stream is not None else sys.stdout

    def emit(self, event: dict[str, Any]) -> None:
        # ensure_ascii=False so non-ASCII payload values aren't escaped.
        # default=str so dates / Decimal / etc. don't crash the emit.
        print(json.dumps(event, ensure_ascii=False, default=str), file=self.stream, flush=True)  # noqa: T201

    def close(self) -> None:
        # stdout never closes
        pass
