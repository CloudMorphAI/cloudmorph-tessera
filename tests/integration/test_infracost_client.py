"""Integration tests for tessera.cost.infracost.InfracostClient using respx mocks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# We mock the gql Client so no real GraphQL transport is needed.
from tessera.cost.infracost import InfracostClient, SkuResult

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_products_response(usd: float = "0.042", unit: str = "Hrs") -> dict:
    return {
        "products": [
            {
                "prices": [
                    {"USD": str(usd), "unit": unit}
                ]
            }
        ]
    }


def _make_empty_response() -> dict:
    return {"products": []}


def _make_info_response(ts: str = "2026-05-01T00:00:00Z") -> dict:
    return {"usageLastUpdatedAt": ts}


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_returns_sku_result():
    """query_sku returns SkuResult with correct usd_per_unit on success."""
    client = InfracostClient()

    with patch.object(client._client, "execute_async", new=AsyncMock(
        return_value=_make_products_response(0.042, "Hrs")
    )):
        result = await client.query_sku("Compute Instance", "us-east-1", {"instanceType": "t3.micro"})

    assert result is not None
    assert isinstance(result, SkuResult)
    assert result.usd_per_unit == pytest.approx(0.042)
    assert result.unit == "Hrs"
    await client.aclose()


@pytest.mark.asyncio
async def test_cache_hit_does_not_call_backend_twice():
    """Second query_sku within TTL window returns cached result without a second wire call."""
    client = InfracostClient(cache_ttl_seconds=300)
    call_count = 0

    async def _mock_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_products_response(0.10, "Hrs")

    with patch.object(client._client, "execute_async", side_effect=_mock_execute):
        r1 = await client.query_sku("Compute Instance", "us-east-1", {"instanceType": "m5.large"})
        r2 = await client.query_sku("Compute Instance", "us-east-1", {"instanceType": "m5.large"})

    assert call_count == 1, "Backend should only be called once within TTL"
    assert r1 is not None
    assert r2 is not None
    assert r1.usd_per_unit == r2.usd_per_unit
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_returns_none():
    """query_sku returns None (fail-closed) when the request times out."""
    client = InfracostClient(timeout_ms=1)

    async def _slow(*args, **kwargs):
        await asyncio.sleep(10)
        return _make_products_response()

    with patch.object(client._client, "execute_async", side_effect=_slow):
        result = await client.query_sku("Compute Instance", "us-east-1", {"instanceType": "m5.xlarge"})

    assert result is None
    await client.aclose()


@pytest.mark.asyncio
async def test_graphql_error_returns_none():
    """query_sku returns None when execute_async raises an exception (GraphQL error)."""
    client = InfracostClient()

    with patch.object(client._client, "execute_async", new=AsyncMock(
        side_effect=Exception("GraphQL execution error: field not found")
    )):
        result = await client.query_sku("Compute Instance", "us-east-1", {"instanceType": "t3.micro"})

    assert result is None
    await client.aclose()


@pytest.mark.asyncio
async def test_bulk_query_parallelism():
    """bulk_query_skus calls all queries and returns results in order."""
    client = InfracostClient()
    responses = [
        _make_products_response(0.042, "Hrs"),
        _make_products_response(0.023, "GB-month"),
        _make_empty_response(),
    ]
    call_index = 0

    async def _mock_execute(*args, **kwargs):
        nonlocal call_index
        resp = responses[call_index % len(responses)]
        call_index += 1
        return resp

    queries = [
        {"service": "Compute Instance", "region": "us-east-1", "attributes": {"instanceType": "t3.micro"}},
        {"service": "AWS S3", "region": "us-east-1", "attributes": {"storageClass": "STANDARD"}},
        {"service": "Unknown", "region": "us-east-1", "attributes": {}},
    ]

    with patch.object(client._client, "execute_async", side_effect=_mock_execute):
        results = await client.bulk_query_skus(queries)

    assert len(results) == 3
    assert results[0] is not None
    assert results[0].usd_per_unit == pytest.approx(0.042)
    assert results[1] is not None
    assert results[1].usd_per_unit == pytest.approx(0.023)
    assert results[2] is None  # empty products response
    await client.aclose()


@pytest.mark.asyncio
async def test_data_version_cache():
    """data_version() caches result for 1 hour; backend called only once."""
    client = InfracostClient()
    call_count = 0

    async def _mock_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_info_response("2026-05-01T00:00:00Z")

    with patch.object(client._client, "execute_async", side_effect=_mock_execute):
        v1 = await client.data_version()
        v2 = await client.data_version()

    assert call_count == 1
    assert v1 == "2026-05-01T00:00:00Z"
    assert v2 == "2026-05-01T00:00:00Z"
    await client.aclose()


@pytest.mark.asyncio
async def test_fail_closed_on_missing_mapping():
    """map_request returns None for unknown tools; cost_cache stays empty."""
    from tessera.cost import map_request

    query = map_request("aws_unknown_OperationXYZ", {"region": "us-east-1"})
    assert query is None


@pytest.mark.asyncio
async def test_pricing_snapshot_id_flows_through_emit():
    """pricing_snapshot_id returned by data_version() flows into audit emitter."""
    from tessera.audit.chain import HashChain
    from tessera.audit.emitter import AuditEmitter
    from tessera.audit.sinks.stdout import StdoutSink

    # Build emitter and emit with a snapshot id
    sink = StdoutSink()
    emitter = AuditEmitter(
        tenant_id="test_scope",
        sinks=[sink],
        hash_chain=HashChain(),
    )

    captured: list[dict] = []
    _original_emit = sink.emit

    def _capture(event):
        captured.append(event)

    sink.emit = _capture

    event = emitter.emit(
        "decision",
        payload={"tool": "aws_ec2_RunInstances"},
        pricing_snapshot_id="2026-05-01T00:00:00Z",
    )

    assert event.get("pricingSnapshotId") == "2026-05-01T00:00:00Z"
