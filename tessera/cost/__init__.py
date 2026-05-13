"""Tessera cost estimation — Infracost GraphQL client + AWS mapping shim.

InfracostClient is imported lazily (requires the [infracost] optional dep group).
aws_mapping, map_request, and load_extended_mappings are always available.
"""

from tessera.cost.aws_mapping import InfracostQuery, aws_mapping, load_extended_mappings, map_request

__all__ = [
    "InfracostClient",
    "SkuResult",
    "CostBackendError",
    "InfracostQuery",
    "aws_mapping",
    "map_request",
    "load_extended_mappings",
]


def __getattr__(name: str):
    """Lazy import of gql-dependent symbols to avoid ImportError at package import time."""
    if name in ("InfracostClient", "SkuResult", "CostBackendError"):
        from tessera.cost.infracost import CostBackendError, InfracostClient, SkuResult  # noqa: PLC0415
        _symbols = {
            "InfracostClient": InfracostClient,
            "SkuResult": SkuResult,
            "CostBackendError": CostBackendError,
        }
        return _symbols[name]
    raise AttributeError(f"module 'tessera.cost' has no attribute {name!r}")
