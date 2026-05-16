from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CostSource = Literal["price_table", "infracost_live", "miss"]

__all__ = ["CostSource", "CostResult", "InfracostQuery"]


@dataclass
class InfracostQuery:
    """Parameters for a single Infracost SKU query.

    Moved from the (now-removed) tessera.cost.aws_mapping in v0.4.0.
    """

    service: str
    region: str
    attributes: dict[str, str]
    confidence_band: Literal["high", "medium", "ceiling"] = "high"
    args_used: list[str] = field(default_factory=list)
    official_mcp_tool_name: str | None = None
    official_mcp_server: str | None = None


@dataclass(frozen=True)
class CostResult:
    price_usd: float | None
    unit: str
    confidence_band: str
    source: CostSource
    operation: str

    @classmethod
    def miss(cls, operation: str) -> CostResult:
        return cls(
            price_usd=None,
            unit="",
            confidence_band="",
            source="miss",
            operation=operation,
        )
