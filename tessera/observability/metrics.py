"""Tessera Prometheus metrics — optional; zero-cost when prometheus_client not installed."""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

    class _StubMetric:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def labels(self, **kwargs: object) -> _StubMetric:
            return self

        def observe(self, amount: float, *args: object) -> None:
            pass

        def inc(self, amount: float = 1) -> None:
            pass

    Histogram = Counter = _StubMetric  # noqa: N816

_LATENCY_BUCKETS = [0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5]

tessera_decision_latency_seconds = Histogram(
    "tessera_decision_latency_seconds",
    "Latency of policy.evaluate() per request",
    labelnames=["upstream", "mode"],
    buckets=_LATENCY_BUCKETS,
)

tessera_audit_emit_latency_seconds = Histogram(
    "tessera_audit_emit_latency_seconds",
    "Latency of audit event emission per request",
    labelnames=["upstream", "mode"],
    buckets=_LATENCY_BUCKETS,
)

tessera_blast_radius_prefetch_latency_seconds = Histogram(
    "tessera_blast_radius_prefetch_latency_seconds",
    "Latency of blast-radius prefetch per request",
    labelnames=["upstream"],
    buckets=_LATENCY_BUCKETS,
)

tessera_cost_prefetch_latency_seconds = Histogram(
    "tessera_cost_prefetch_latency_seconds",
    "Latency of cost prefetch per request",
    labelnames=["upstream", "cost_source"],
    buckets=_LATENCY_BUCKETS,
)

tessera_decisions_total = Counter(
    "tessera_decisions_total",
    "Total policy decisions made",
    labelnames=["upstream", "mode", "action"],
)

tessera_audit_emit_failures_total = Counter(
    "tessera_audit_emit_failures_total",
    "Total audit emit failures",
)
