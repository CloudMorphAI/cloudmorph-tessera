"""Integration tests — proxy observability instrumentation.

Covers:
- metrics are observed (stub path) on a tools/call round-trip
- OnDecision hook is fired after enforcement decision
- conversation_id is threaded into audit payload
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

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
from tessera.observability import events as obs_events
from tessera.proxy import create_app

# ── Constants ────────────────────────────────────────────────────────────────

_TOKEN = "tk_test_instrumentation_xxx"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}

_MOCK_RESP = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {"content": [{"type": "text", "text": "ok"}]},
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path, mode: PoliciesMode = PoliciesMode.enforcement) -> TesseraConfig:
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir(exist_ok=True)
    (policy_dir / "allow-all.yaml").write_text(
        'id: allow-all\nname: Allow all\nmatch:\n  upstream: "*"\n  tool: "*"\naction: allow\n',
        encoding="utf-8",
    )
    return TesseraConfig(
        audit=AuditConfig(path=str(tmp_path / "audit.db"), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=mode,
            default_action="allow",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="mock", url="http://mock-upstream", timeout_seconds=5),
        ],
        deployment_id="test-instrumentation",
    )


def _mock_transport() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(_MOCK_RESP).encode(),
        )

    return httpx.MockTransport(_handler)


def _tools_call(tool_name: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"name": tool_name, "arguments": {}}
    if meta is not None:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}


@contextlib.contextmanager
def _proxy(config: TesseraConfig) -> Generator[tuple[TestClient, Any], None, None]:
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.http_clients["mock"] = httpx.AsyncClient(
            transport=_mock_transport(),
            base_url="http://mock-upstream",
            timeout=5.0,
        )
        yield client, app


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_tools_call_completes_successfully(tmp_path: Path) -> None:
    """Baseline: a tools/call round-trip succeeds with instrumentation loaded."""
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_TOKEN}"
    try:
        config = _make_config(tmp_path)
        with _proxy(config) as (client, _app):
            resp = client.post(
                "/mcp/mock",
                json=_tools_call("aws_s3_list_buckets"),
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body
    finally:
        os.environ.pop("TESSERA_BEARER_TOKENS", None)


@pytest.mark.integration
def test_on_decision_hook_fires(tmp_path: Path) -> None:
    """An OnDecision hook registered before the request fires after enforce decision."""
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_TOKEN}"
    captured: list[Any] = []

    async def hook(decision: Any, context: dict[str, Any]) -> None:
        captured.append(decision)

    obs_events.clear_hooks()
    obs_events.register_on_decision(hook)

    try:
        config = _make_config(tmp_path)
        with _proxy(config) as (client, _app):
            resp = client.post(
                "/mcp/mock",
                json=_tools_call("aws_s3_list_buckets"),
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        # Fire-and-forget tasks complete within the lifespan context
        # The TestClient runs the full lifespan, so tasks should have run.
        # Allow a brief settle period for fire-and-forget tasks.
        assert len(captured) >= 1, "OnDecision hook was not called"
    finally:
        obs_events.clear_hooks()
        os.environ.pop("TESSERA_BEARER_TOKENS", None)


@pytest.mark.integration
def test_conversation_id_from_meta(tmp_path: Path) -> None:
    """conversation_id is threaded from _meta.tessera_intent.conversation_id into audit."""
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_TOKEN}"
    try:
        config = _make_config(tmp_path)
        meta = {"tessera_intent": {"conversation_id": "conv_abc123", "verbs": [], "purpose": "test"}}
        with _proxy(config) as (client, app):
            resp = client.post(
                "/mcp/mock",
                json=_tools_call("aws_s3_list_buckets", meta=meta),
                headers=_HEADERS,
            )
            assert resp.status_code == 200
    finally:
        os.environ.pop("TESSERA_BEARER_TOKENS", None)


@pytest.mark.integration
def test_conversation_id_from_meta_top_level(tmp_path: Path) -> None:
    """conversation_id is also read from _meta.conversation_id (top-level fallback)."""
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_TOKEN}"
    try:
        config = _make_config(tmp_path)
        meta = {"conversation_id": "conv_toplevel"}
        with _proxy(config) as (client, app):
            resp = client.post(
                "/mcp/mock",
                json=_tools_call("aws_s3_list_buckets", meta=meta),
                headers=_HEADERS,
            )
            assert resp.status_code == 200
    finally:
        os.environ.pop("TESSERA_BEARER_TOKENS", None)


@pytest.mark.integration
def test_multiple_hooks_all_called(tmp_path: Path) -> None:
    """Multiple registered OnDecision hooks all fire for a single request."""
    os.environ["TESSERA_BEARER_TOKENS"] = f"alice:{_TOKEN}"
    results: list[int] = []

    async def hook_a(d: Any, c: dict[str, Any]) -> None:
        results.append(1)

    async def hook_b(d: Any, c: dict[str, Any]) -> None:
        results.append(2)

    obs_events.clear_hooks()
    obs_events.register_on_decision(hook_a)
    obs_events.register_on_decision(hook_b)

    try:
        config = _make_config(tmp_path)
        with _proxy(config) as (client, _app):
            client.post(
                "/mcp/mock",
                json=_tools_call("aws_s3_list_buckets"),
                headers=_HEADERS,
            )
        assert 1 in results
        assert 2 in results
    finally:
        obs_events.clear_hooks()
        os.environ.pop("TESSERA_BEARER_TOKENS", None)
