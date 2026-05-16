"""Tessera OpenTelemetry tracing — off by default (Q3 lock).

Set TESSERA_OTEL_ENABLED=1 (or "true" / "yes") to enable. When disabled,
all instrumentation is a no-op with zero per-call overhead.
"""

from __future__ import annotations

import asyncio
import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar, cast

_OTEL_ENABLED: bool = os.environ.get("TESSERA_OTEL_ENABLED", "").lower() in ("1", "true", "yes")

_tracer: Any = None

T = TypeVar("T")


def is_enabled() -> bool:
    """Return True when TESSERA_OTEL_ENABLED is set."""
    return _OTEL_ENABLED


def init_tracer() -> None:
    """Initialise the OTel tracer. No-op when TESSERA_OTEL_ENABLED is unset or opentelemetry missing."""
    global _tracer
    if not _OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace as _ot_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        endpoint = os.environ.get("TESSERA_OTEL_ENDPOINT", "http://localhost:4318/v1/traces")
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        _ot_trace.set_tracer_provider(provider)
        _tracer = _ot_trace.get_tracer("tessera")
    except ImportError:
        # opentelemetry not installed — remain disabled
        pass


def trace(span_name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that opens a span; no-op when tracing is disabled.

    Zero per-call cost when TESSERA_OTEL_ENABLED is unset — early return
    before any span creation. Handles both sync and async functions.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                if _tracer is None:
                    return await fn(*args, **kwargs)
                with _tracer.start_as_current_span(span_name):
                    return await fn(*args, **kwargs)

            return cast("Callable[..., T]", awrapper)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _tracer is None:
                return fn(*args, **kwargs)
            with _tracer.start_as_current_span(span_name):
                return fn(*args, **kwargs)

        return cast("Callable[..., T]", wrapper)

    return decorator
