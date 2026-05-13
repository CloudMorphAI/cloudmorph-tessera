"""Infracost GraphQL client for Tessera cost estimation."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport


class CostBackendError(Exception):
    """Raised when the Infracost backend returns a hard error."""


@dataclass
class SkuResult:
    """Pricing result for a single SKU lookup."""

    usd_per_unit: float
    unit: str
    currency: str = "USD"
    pricing_snapshot_id: str | None = None
    confidence_band: Literal["high", "medium", "ceiling"] = "high"


_SKU_QUERY = gql("""
query Products($productFamily: String!, $vendorName: String!, $region: String!, $attributeFilters: [AttributeFilter!]) {
  products(
    filter: {
      productFamily: $productFamily,
      vendorName: $vendorName,
      region: $region,
      attributeFilters: $attributeFilters
    }
  ) {
    prices(filter: { purchaseOption: "on_demand" }) {
      USD
      unit
    }
  }
}
""")

_INFO_QUERY = gql("""
query {
  usageLastUpdatedAt
}
""")

_ONE_HOUR = 3600.0


class InfracostClient:
    """Async Infracost Cloud Pricing API GraphQL client.

    Wraps the self-hosted Infracost pricing container at `backend_url`.
    On timeout or HTTP error, methods return None (fail-closed: don't block
    the policy engine when pricing data is unavailable).
    """

    def __init__(
        self,
        backend_url: str = "http://localhost:4000/graphql",
        api_key: str | None = None,
        cache_ttl_seconds: int = 300,
        timeout_ms: int = 200,
    ) -> None:
        self._backend_url = backend_url
        self._api_key = api_key
        self._cache_ttl = cache_ttl_seconds
        self._timeout_s = timeout_ms / 1000.0
        self._cache: dict[str, tuple[SkuResult, float]] = {}
        self._version_cache: tuple[str, float] | None = None

        headers: dict[str, str] = {}
        if api_key:
            headers["X-Api-Key"] = api_key

        transport = AIOHTTPTransport(url=backend_url, headers=headers)
        self._client = Client(transport=transport, fetch_schema_from_transport=False)

    def _cache_key(self, service: str, region: str, attributes: dict[str, str]) -> str:
        return json.dumps({"service": service, "region": region, "attributes": attributes}, sort_keys=True)

    async def query_sku(
        self,
        service: str,
        region: str,
        attributes: dict[str, str],
    ) -> SkuResult | None:
        """Look up unit price for a single SKU. Returns None on any error (fail-closed)."""
        key = self._cache_key(service, region, attributes)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[1] > now:
            return cached[0]

        # Build attribute filter list for GraphQL
        attr_filters = [{"key": k, "value": v} for k, v in attributes.items()]

        try:
            result: dict[str, Any] = await asyncio.wait_for(
                self._client.execute_async(
                    _SKU_QUERY,
                    variable_values={
                        "productFamily": service,
                        "vendorName": "aws",
                        "region": region,
                        "attributeFilters": attr_filters,
                    },
                ),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            return None
        except Exception:  # noqa: BLE001
            return None

        products = result.get("products") or []
        if not products:
            return None

        prices = products[0].get("prices") or []
        if not prices:
            return None

        price_entry = prices[0]
        try:
            usd = float(price_entry["USD"])
        except (KeyError, TypeError, ValueError):
            return None

        unit = price_entry.get("unit", "unknown")
        sku_result = SkuResult(usd_per_unit=usd, unit=unit)
        self._cache[key] = (sku_result, now + self._cache_ttl)
        return sku_result

    async def bulk_query_skus(self, queries: list[dict[str, Any]]) -> list[SkuResult | None]:
        """Query multiple SKUs in parallel. Each dict: {service, region, attributes}."""
        tasks = [
            self.query_sku(q["service"], q["region"], q.get("attributes", {}))
            for q in queries
        ]
        results: list[SkuResult | None] = await asyncio.gather(*tasks)
        return results

    async def data_version(self) -> str:
        """Return the Infracost pricing data snapshot identifier. Cached for 1 hour."""
        now = time.monotonic()
        if self._version_cache is not None and self._version_cache[1] > now:
            return self._version_cache[0]

        try:
            result: dict[str, Any] = await asyncio.wait_for(
                self._client.execute_async(_INFO_QUERY),
                timeout=self._timeout_s,
            )
            version: str = result.get("usageLastUpdatedAt") or "unknown"
        except Exception:  # noqa: BLE001
            version = "unknown"

        self._version_cache = (version, now + _ONE_HOUR)
        return version

    async def aclose(self) -> None:
        """Close the underlying GQL client transport."""
        try:
            await self._client.close_async()
        except Exception:  # noqa: BLE001
            pass
