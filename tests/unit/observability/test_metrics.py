"""Unit tests for tessera.observability.metrics."""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock, patch


def _reload_metrics_without_prom() -> types.ModuleType:
    """Reload metrics module with prometheus_client hidden."""
    # Remove cached module so reimport picks up the patched builtins
    for key in list(sys.modules):
        if "tessera.observability" in key:
            del sys.modules[key]

    with patch.dict(sys.modules, {"prometheus_client": None}):
        mod = importlib.import_module("tessera.observability.metrics")
    return mod


def _reload_metrics_with_prom() -> types.ModuleType:
    """Reload metrics module with a mock prometheus_client."""
    for key in list(sys.modules):
        if "tessera.observability" in key:
            del sys.modules[key]

    fake_prom = types.ModuleType("prometheus_client")
    fake_prom.Histogram = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    fake_prom.Counter = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"prometheus_client": fake_prom}):
        mod = importlib.import_module("tessera.observability.metrics")
    return mod


class TestStubFallback:
    """When prometheus_client is missing, all calls are silent no-ops."""

    def test_histogram_observe_noop(self) -> None:
        mod = _reload_metrics_without_prom()
        # Should not raise
        mod.tessera_decision_latency_seconds.labels(upstream="aws", mode="enforcement").observe(0.001)

    def test_counter_inc_noop(self) -> None:
        mod = _reload_metrics_without_prom()
        mod.tessera_decisions_total.labels(upstream="aws", mode="enforcement", action="allow").inc()

    def test_audit_emit_failures_counter_noop(self) -> None:
        mod = _reload_metrics_without_prom()
        mod.tessera_audit_emit_failures_total.inc()

    def test_blast_radius_histogram_noop(self) -> None:
        mod = _reload_metrics_without_prom()
        mod.tessera_blast_radius_prefetch_latency_seconds.labels(upstream="aws").observe(0.005)

    def test_cost_histogram_noop(self) -> None:
        mod = _reload_metrics_without_prom()
        mod.tessera_cost_prefetch_latency_seconds.labels(
            upstream="aws", cost_source="price_table"
        ).observe(0.0002)


class TestWithPrometheusClient:
    """When prometheus_client is available, Histogram/Counter are constructed properly."""

    def test_histogram_constructed(self) -> None:
        mod = _reload_metrics_with_prom()
        # The module-level metric objects should be Mock instances (returned by the fake)
        assert mod.tessera_decision_latency_seconds is not None

    def test_counter_constructed(self) -> None:
        mod = _reload_metrics_with_prom()
        assert mod.tessera_decisions_total is not None

    def test_latency_buckets_count(self) -> None:
        mod = _reload_metrics_without_prom()
        # Access the bucket constant
        assert len(mod._LATENCY_BUCKETS) == 10

    def test_observe_returns_none(self) -> None:
        """Stub .observe() must return None (no-op, doesn't raise)."""
        mod = _reload_metrics_without_prom()
        result = mod.tessera_audit_emit_latency_seconds.labels(upstream="x", mode="log_only").observe(1.0)
        assert result is None

    def test_inc_returns_none(self) -> None:
        mod = _reload_metrics_without_prom()
        result = mod.tessera_audit_emit_failures_total.inc()
        assert result is None
