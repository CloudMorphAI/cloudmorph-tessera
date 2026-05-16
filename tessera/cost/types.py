from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CostSource = Literal["price_table", "infracost_live", "miss"]


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
