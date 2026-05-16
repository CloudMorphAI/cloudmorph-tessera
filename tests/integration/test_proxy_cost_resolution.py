"""Integration tests: proxy cost resolution paths.

Tests three paths:
1. price_table  — pre-registered PriceTable hit → CostResult.source == "price_table"
2. infracost_live — price table miss, mock InfracostClient hit → "infracost_live"
3. double miss  — no price table, no infracost → "miss" (call allowed, no cost_source in audit)
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from tessera.config import (
    AuditConfig,
    IntentConfig,
    MetricsConfig,
    PoliciesConfig,
    PoliciesMode,
    RuntimeConfig,
    TesseraConfig,
    UpstreamConfig,
)
from tessera.cost import register_price_table
from tessera.cost.price_table import PriceTable
from tessera.proxy import create_app

# ── Fixtures / helpers ────────────────────────────────────────────────────────


_MOCK_UPSTREAM_RESPONSE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {"content": [{"type": "text", "text": "upstream ok"}]},
}

_HEADERS = {"Authorization": "Bearer tk_test_integration_cost"}


def _tools_call_body(tool_name: str, arguments: dict | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }


def _mock_transport(response_json: dict | None = None) -> httpx.MockTransport:
    data = response_json if response_json is not None else _MOCK_UPSTREAM_RESPONSE

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(data).encode(),
        )

    return httpx.MockTransport(_handler)


@contextlib.contextmanager
def _proxy_client(config: TesseraConfig):
    app = create_app(config)
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(
            transport=_mock_transport(),
            base_url="http://mock-upstream",
            timeout=5.0,
        )
        yield client, app


def _write_price_table(tmp_path: Path, op_name: str, price_usd: float) -> Path:
    """Write a minimal price-table JSON artifact to tmp_path and return its path."""
    artifact = {
        "schema_version": "1",
        "bundle_version": "v1.0.0",
        "provider": "aws",
        "generated_at": "2026-05-16T00:00:00Z",
        "operations": {
            op_name: {
                "price_realms": ["on_demand"],
                "confidence_band": "high",
                "lookups": [
                    {"params": {}, "price_usd_per_hour": price_usd}
                ],
            }
        },
        "ceiling_bands": {
            "default": {"warn_usd": 1.0, "block_usd": 10.0}
        },
    }
    p = tmp_path / "aws-prices-v1.0.0.json"
    p.write_text(json.dumps(artifact), encoding="utf-8")
    return p


def _cost_policy_yaml(op_name: str, threshold: float) -> str:
    return f"""\
id: test-cost-block
name: Block expensive EC2 calls
match:
  upstream: "*"
  tool: "{op_name}"
when:
  - condition: predicted_cost
    usd_threshold: {threshold}
    band: high
    operator: greater_than
action: block
reason: "cost_threshold_exceeded"
priority: 50
"""


def _allow_all_yaml() -> str:
    return """\
id: test-allow-all
name: Allow everything else
match:
  upstream: "*"
  tool: "*"
action: allow
priority: 1
"""


def _config(policy_dir: Path, audit_db: Path) -> TesseraConfig:
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test-cost",
    )


_DEPLOYMENT_ID = "test-cost"
# In dev mode (no TESSERA_BEARER_TOKENS), scope = deployment_id.


def _read_last_audit_event(audit_db: Path, scope: str = _DEPLOYMENT_ID) -> dict | None:
    """Read the most recent audit event for a scope from the SQLite audit DB."""
    from tessera.audit.sinks.sqlite import SqliteSink

    sink = SqliteSink(path=audit_db)
    events = list(sink.iter_recent(scope=scope, limit=10))
    sink.close()
    # iter_recent returns chronological order (oldest-first within window).
    # Filter to decision events only (skip startup/other event types).
    decision_events = [e for e in events if e.get("eventType") == "decision"]
    return decision_events[-1] if decision_events else None


# ── Test 1: price-table path ──────────────────────────────────────────────────


def test_price_table_path_blocks_with_cost_source(tmp_path: Path) -> None:
    """Call is BLOCKED by a predicted_cost policy; audit event has cost_source=price_table."""
    op = "aws_ec2_RunInstances"
    threshold = 1.00  # $1/hr threshold
    price = 5.00  # $5/hr in the price table → above threshold → block

    # Set up policy dir
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "cost-block.yaml").write_text(_cost_policy_yaml(op, threshold), encoding="utf-8")
    (policy_dir / "allow-all.yaml").write_text(_allow_all_yaml(), encoding="utf-8")

    audit_db = tmp_path / "audit.db"

    # Register a price table with the operation at $5/hr
    pt_path = _write_price_table(tmp_path, op, price)
    pt = PriceTable(pt_path, signature_verified=False)
    register_price_table("aws", pt)

    config = _config(policy_dir, audit_db)

    with _proxy_client(config) as (client, app):
        # Clear any pre-existing price_table on app.state — test relies on module registry
        # (app.state.price_table is not set; cost_for_call reads _PRICE_TABLE_REGISTRY)
        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body(op),
            headers=_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body, f"Expected block, got: {body}"
    assert body["error"]["code"] == -32603
    assert body["error"]["data"]["reason"] == "cost_threshold_exceeded"

    # Check audit event
    event = _read_last_audit_event(audit_db)
    assert event is not None, "No audit event found"
    payload = event.get("payload", {})
    assert payload.get("cost_source") == "price_table", f"cost_source wrong: {payload}"
    assert payload.get("cost_band") == "high", f"cost_band wrong: {payload}"


# ── Test 2: infracost-live fallback path ──────────────────────────────────────


def test_infracost_live_fallback_blocks_with_cost_source(tmp_path: Path) -> None:
    """price-table miss → InfracostClient returns $10/hr → BLOCKED; audit has cost_source=infracost_live."""

    from tessera import cost as _cost_mod
    from tessera.cost.aws_mapping import InfracostQuery

    op = "aws_ec2_RunInstances"
    threshold = 1.00

    # Ensure no price table registered for aws (clear the registry for this test)
    _cost_mod._PRICE_TABLE_REGISTRY.pop("aws", None)

    # Set up policy dir
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "cost-block.yaml").write_text(_cost_policy_yaml(op, threshold), encoding="utf-8")
    (policy_dir / "allow-all.yaml").write_text(_allow_all_yaml(), encoding="utf-8")

    audit_db = tmp_path / "audit.db"
    config = _config(policy_dir, audit_db)

    # Build a mock InfracostClient that returns $10/hr.
    # Use spec-compatible AsyncMock for aclose so lifespan teardown doesn't crash.
    mock_sku = MagicMock()
    mock_sku.usd_per_unit = 10.0
    mock_sku.unit = "Hrs"
    mock_sku.confidence_band = "high"

    mock_backend = AsyncMock()
    mock_backend.query_sku = AsyncMock(return_value=mock_sku)
    mock_backend.aclose = AsyncMock(return_value=None)

    # Build a mock aws_mapping that returns a valid query for the op
    mock_query = InfracostQuery(
        service="Compute Instance",
        region="us-east-1",
        attributes={"instanceType": "t3.micro"},
        confidence_band="high",
    )
    mock_mapping = MagicMock()
    mock_mapping.map_request = MagicMock(return_value=mock_query)

    with _proxy_client(config) as (client, app):
        # Inject the mock cost backend and mapping into app.state
        app.state.cost_backend = mock_backend
        app.state.aws_mapping = mock_mapping

        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body(op),
            headers=_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body, f"Expected block, got: {body}"
    assert body["error"]["code"] == -32603
    assert body["error"]["data"]["reason"] == "cost_threshold_exceeded"

    # Check audit event
    event = _read_last_audit_event(audit_db)
    assert event is not None, "No audit event found"
    payload = event.get("payload", {})
    assert payload.get("cost_source") == "infracost_live", f"cost_source wrong: {payload}"
    assert payload.get("cost_band") == "high", f"cost_band wrong: {payload}"


# ── Test 3: double miss path ──────────────────────────────────────────────────


def test_double_miss_allows_and_no_cost_source(tmp_path: Path) -> None:
    """No price table, no infracost configured → call ALLOWED, audit has no cost_source."""
    from tessera import cost as _cost_mod

    op = "aws_ec2_RunInstances"
    threshold = 1.00

    # Ensure no price table registered
    _cost_mod._PRICE_TABLE_REGISTRY.pop("aws", None)

    # Policy would block IF cost resolved, but with miss it should not fire
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "cost-block.yaml").write_text(_cost_policy_yaml(op, threshold), encoding="utf-8")
    (policy_dir / "allow-all.yaml").write_text(_allow_all_yaml(), encoding="utf-8")

    audit_db = tmp_path / "audit.db"
    config = _config(policy_dir, audit_db)

    with _proxy_client(config) as (client, app):
        # Ensure no cost backend configured
        app.state.cost_backend = None
        app.state.aws_mapping = None

        resp = client.post(
            "/mcp/mock",
            json=_tools_call_body(op),
            headers=_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    # allow-all policy fires since predicted_cost condition fails-closed (miss → False)
    assert "result" in body, f"Expected allow (upstream pass-through), got: {body}"

    # Audit event should have cost_source == "miss" (we always set cost_cache entry)
    event = _read_last_audit_event(audit_db)
    assert event is not None, "No audit event found"
    payload = event.get("payload", {})
    # The proxy now always emits cost_source when cost_cache is non-empty
    # With a "miss" result the cost_source will be "miss" and cost_band will be ""
    assert payload.get("cost_source") == "miss", f"cost_source should be 'miss', got: {payload}"
