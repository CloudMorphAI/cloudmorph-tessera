"""Tessera cost estimation — price-table registry + Infracost GraphQL client.

cost_for_call() and register_price_table() are the primary API entry points.
InfracostClient is imported lazily (requires the [infracost] optional dep group).
InfracostQuery is re-exported from tessera.cost.types for backwards-compat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tessera.cost.types import CostResult, CostSource, InfracostQuery

if TYPE_CHECKING:
    from tessera.cost.price_table import PriceTable

__all__ = [
    # v0.3.0 unified API
    "cost_for_call",
    "register_price_table",
    "CostResult",
    "CostSource",
    # price_table surface
    "PriceTable",
    "CostEstimate",
    # infracost surface (lazy)
    "InfracostClient",
    "SkuResult",
    "CostBackendError",
    # InfracostQuery moved to tessera.cost.types in v0.4.0
    "InfracostQuery",
]

import logging as _logging

_logger = _logging.getLogger(__name__)

# ── Multi-provider price-table registry ───────────────────────────────────────
# Keyed by provider string ("aws", "azure", "gcp").
# Populated at startup by IntelligenceClient._load_price_tables_from_cache()
# via register_price_table(), or by callers in tests.

_PRICE_TABLE_REGISTRY: dict[str, PriceTable] = {}


def register_price_table(provider: str, table: PriceTable) -> None:
    """Register a price table for a provider in the module-level registry.

    Replaces any existing table for the same provider.
    Called by IntelligenceClient._load_price_tables_from_cache().
    """
    _PRICE_TABLE_REGISTRY[provider] = table
    _logger.debug(
        "event=price_table_registered provider=%s ops=%d",
        provider,
        table.operation_count,
    )


def cost_for_call(
    operation: str,
    args: dict[str, object],
    region: str | None = None,
) -> CostResult:
    """Resolve a cost estimate for a single tool call from the price-table registry.

    Routes by canonical operation prefix:
      * aws_*   → AWS price table
      * azure_* → Azure price table
      * gcp_*   → GCP price table
      * anything else → miss

    Returns a CostResult in all cases (never None). On a table miss the
    returned CostResult.source is "miss" and price_usd is None.

    The Infracost live-query fallback (InfracostClient.query_sku) is NOT
    attempted here — the proxy's _prefetch_cost path handles that fallback
    after checking this function first.
    """
    from tessera.cost.price_table import CostEstimate  # noqa: PLC0415

    prefix = operation.split("_", 1)[0]
    table: PriceTable | None = _PRICE_TABLE_REGISTRY.get(prefix)

    if table is None:
        return CostResult.miss(operation)

    estimate: CostEstimate | None = table.cost_for_call(operation, args, region=region)
    if estimate is None:
        return CostResult.miss(operation)

    # Map the producer confidence_band ("high"|"medium"|"ceiling") from the table.
    # Falls back to "medium" for tables that pre-date the field.
    confidence_band = table.get_operation_confidence_band(operation)

    # Derive billing unit from realm ("on_demand"→"hour", "request"→"Requests", etc.)
    _realm_to_unit: dict[str, str] = {
        "on_demand": "hour",
        "spot": "hour",
        "request": "Requests",
        "fixed_monthly": "month",
    }
    unit = _realm_to_unit.get(estimate.realm, estimate.realm)

    return CostResult(
        price_usd=estimate.price_usd,
        unit=unit,
        confidence_band=confidence_band,
        source="price_table",
        operation=operation,
    )


def __getattr__(name: str) -> object:
    """Lazy import of gql-dependent symbols to avoid ImportError at package import time."""
    if name in ("InfracostClient", "SkuResult", "CostBackendError"):
        from tessera.cost.infracost import CostBackendError, InfracostClient, SkuResult  # noqa: PLC0415
        _symbols: dict[str, object] = {
            "InfracostClient": InfracostClient,
            "SkuResult": SkuResult,
            "CostBackendError": CostBackendError,
        }
        return _symbols[name]
    if name in ("PriceTable", "CostEstimate"):
        from tessera.cost.price_table import CostEstimate, PriceTable  # noqa: PLC0415
        _symbols_pt: dict[str, object] = {
            "PriceTable": PriceTable,
            "CostEstimate": CostEstimate,
        }
        return _symbols_pt[name]
    raise AttributeError(f"module 'tessera.cost' has no attribute {name!r}")
