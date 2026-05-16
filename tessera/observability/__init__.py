"""Tessera observability primitives — metrics + tracing + structured-event hooks.

All three subsystems are off-by-default and zero-cost when disabled. Operators
opt-in via env vars (TESSERA_OTEL_ENABLED, TESSERA_METRICS_ENABLED, etc.) or
config. Metrics use prometheus_client (optional [observability] extra); tracing
uses OpenTelemetry; events use a simple Protocol-based hook registry.
"""

from tessera.observability import events, metrics, tracing

__all__ = ["events", "metrics", "tracing"]
