"""Tests for v0.8.0 unified MCP routing (POST /mcp).

Covers:
- tools/list aggregates from 2 mock upstreams with namespaced names
- tools/call with 'aws__s3_PutObject' dispatches to aws upstream with canonical 's3_PutObject'
- policy match works on canonical tool name (not namespaced)
- POST /mcp/{upstream_name} still works alongside POST /mcp (backwards compat)
- JSON-RPC -32602 returned when tool name has no namespace
- D4: unified_mode_disabled flag causes error response on tools/list
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from tessera.proxy import TOOL_NAMESPACE_SEPARATOR, namespace_tool, parse_namespaced_tool

# ── Helpers ──────────────────────────────────────────────────────────────────

_ALLOW_ALL_YAML = """\
id: allow-all
name: Allow all
action: allow
priority: 0
"""

_BLOCK_IAM_YAML = """\
id: block-iam-create-user
name: Block IAM CreateUser
match:
  tool: "IAM_CreateUser"
action: block
reason: "iam creation blocked"
priority: 10
"""


@pytest.fixture()
def policy_dir_unified(tmp_path: Path) -> Path:
    (tmp_path / "allow-all.yaml").write_text(_ALLOW_ALL_YAML, encoding="utf-8")
    (tmp_path / "block-iam.yaml").write_text(_BLOCK_IAM_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def audit_db_unified(tmp_path: Path) -> Path:
    return tmp_path / "audit_unified.db"


@pytest.fixture()
def two_upstream_config(policy_dir_unified: Path, audit_db_unified: Path) -> TesseraConfig:
    """Config with two mock upstreams: 'aws' and 'gcp'."""
    return TesseraConfig(
        audit=AuditConfig(path=str(audit_db_unified), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir_unified),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="allow",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[
            UpstreamConfig(name="aws", url="http://mock-aws-upstream", timeout_seconds=5),
            UpstreamConfig(name="gcp", url="http://mock-gcp-upstream", timeout_seconds=5),
        ],
        deployment_id="test-unified",
    )


def _make_tools_list_response(tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": "1", "result": {"tools": tools}}


def _make_tools_call_response(result_text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": result_text}]},
    }


# ── Unit tests: namespacing helpers ──────────────────────────────────────────


def test_namespace_tool() -> None:
    assert namespace_tool("aws", "s3_PutObject") == "aws__s3_PutObject"
    assert namespace_tool("gcp", "storage_buckets_insert") == "gcp__storage_buckets_insert"
    assert namespace_tool("github", "create_pull_request") == "github__create_pull_request"


def test_parse_namespaced_tool_happy() -> None:
    upstream, tool = parse_namespaced_tool("aws__s3_PutObject")
    assert upstream == "aws"
    assert tool == "s3_PutObject"


def test_parse_namespaced_tool_multiple_separators() -> None:
    """Split on FIRST __ only — tool name may contain further underscores."""
    upstream, tool = parse_namespaced_tool("aws__s3__PutObject")
    assert upstream == "aws"
    assert tool == "s3__PutObject"


def test_parse_namespaced_tool_missing_separator() -> None:
    with pytest.raises(ValueError, match="missing upstream namespace"):
        parse_namespaced_tool("s3_PutObject")


def test_tool_namespace_separator_value() -> None:
    assert TOOL_NAMESPACE_SEPARATOR == "__"


# ── Integration tests: POST /mcp ─────────────────────────────────────────────


@pytest.fixture()
def unified_client(two_upstream_config: TesseraConfig):
    """TestClient for create_app with two mock upstreams."""
    os.environ["TESSERA_AUDIT_SYNC"] = "1"
    from tessera.proxy import create_app
    app = create_app(two_upstream_config)

    # Patch httpx.AsyncClient.post so both upstreams return predictable tool lists.
    aws_tools = [
        {"name": "s3_PutObject", "description": "Put an S3 object"},
        {"name": "IAM_CreateUser", "description": "Create IAM user"},
    ]
    gcp_tools = [
        {"name": "storage_buckets_insert", "description": "Insert GCS bucket"},
    ]

    def _make_mock_client(base_url: str, **kwargs: Any) -> MagicMock:
        mock = MagicMock()
        mock.aclose = AsyncMock()

        async def _post(path: str, json: dict[str, Any], **kw: Any) -> MagicMock:  # noqa: A002
            method = json.get("method", "")
            resp = MagicMock()
            resp.status_code = 200
            if method == "tools/list":
                if "aws" in base_url:
                    resp.json.return_value = _make_tools_list_response(aws_tools)
                else:
                    resp.json.return_value = _make_tools_list_response(gcp_tools)
            elif method == "tools/call":
                tool = json.get("params", {}).get("name", "unknown")
                resp.json.return_value = _make_tools_call_response(f"called:{tool}")
            else:
                resp.json.return_value = {"jsonrpc": "2.0", "id": json.get("id"), "result": {}}
            return resp

        mock.post = _post
        return mock

    with patch("httpx.AsyncClient", side_effect=_make_mock_client):
        with TestClient(app) as client:
            yield client

    os.environ.pop("TESSERA_AUDIT_SYNC", None)


def test_unified_tools_list_aggregates_and_namespaces(unified_client: TestClient) -> None:
    """tools/list on /mcp returns tools from both upstreams, all namespaced."""
    resp = unified_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    tools = data["result"]["tools"]
    names = [t["name"] for t in tools]
    # aws upstream tools are namespaced
    assert "aws__s3_PutObject" in names
    assert "aws__IAM_CreateUser" in names
    # gcp upstream tools are namespaced
    assert "gcp__storage_buckets_insert" in names
    # no raw (un-namespaced) names
    assert "s3_PutObject" not in names
    assert "storage_buckets_insert" not in names


def test_unified_tools_call_dispatches_to_correct_upstream(unified_client: TestClient) -> None:
    """tools/call with 'aws__s3_PutObject' dispatches to aws upstream with canonical name."""
    resp = unified_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "aws__s3_PutObject", "arguments": {"bucket": "my-bucket"}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # The upstream received canonical name 's3_PutObject', not 'aws__s3_PutObject'
    assert "result" in data
    content_text = data["result"]["content"][0]["text"]
    assert content_text == "called:s3_PutObject"


def test_unified_tools_call_gcp_upstream(unified_client: TestClient) -> None:
    """tools/call with 'gcp__storage_buckets_insert' dispatches to gcp upstream."""
    resp = unified_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "gcp__storage_buckets_insert", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    content_text = data["result"]["content"][0]["text"]
    assert content_text == "called:storage_buckets_insert"


def test_unified_policy_match_uses_canonical_name(unified_client: TestClient) -> None:
    """Policy matches on canonical 'IAM_CreateUser', not 'aws__IAM_CreateUser'.

    The block-iam policy matches tool 'IAM_CreateUser'. The unified route must
    strip the namespace before policy evaluation so this policy fires correctly.
    """
    resp = unified_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "aws__IAM_CreateUser", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # block returns result.isError=true (not a JSON-RPC error code)
    assert data["result"]["isError"] is True
    assert "POLICY_BLOCK" in data["result"]["content"][0]["text"]
    assert "block-iam-create-user" in data["result"]["content"][0]["text"]


def test_unified_tools_call_no_namespace_returns_32602(unified_client: TestClient) -> None:
    """tools/call without namespace returns JSON-RPC -32602 Invalid params."""
    resp = unified_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "s3_PutObject", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"]["code"] == -32602


def test_per_upstream_route_still_works(unified_client: TestClient) -> None:
    """POST /mcp/{upstream_name} still works alongside POST /mcp (backwards compat D3)."""
    resp = unified_client.post(
        "/mcp/aws",
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "s3_PutObject", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    content_text = data["result"]["content"][0]["text"]
    assert content_text == "called:s3_PutObject"


def test_unified_tools_list_disabled_when_tool_has_double_underscore(
    two_upstream_config: TesseraConfig,
) -> None:
    """D4: if an upstream has a tool with '__' in its name, unified mode is disabled."""
    os.environ["TESSERA_AUDIT_SYNC"] = "1"
    from tessera.proxy import create_app

    # An upstream that returns a tool with __ in its name
    colliding_tools = [{"name": "aws__native__collision", "description": "bad tool"}]

    def _make_mock_client(base_url: str, **kwargs: Any) -> MagicMock:
        mock = MagicMock()
        mock.aclose = AsyncMock()

        async def _post(path: str, json: dict[str, Any], **kw: Any) -> MagicMock:  # noqa: A002
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _make_tools_list_response(colliding_tools)
            return resp

        mock.post = _post
        return mock

    app = create_app(two_upstream_config)
    try:
        with patch("httpx.AsyncClient", side_effect=_make_mock_client):
            with TestClient(app) as client:
                resp = client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                )
                assert resp.status_code == 200
                data = resp.json()
                # Should return error, not result
                assert "error" in data
                assert "-32603" in str(data["error"]["code"]) or data["error"]["code"] == -32603
    finally:
        os.environ.pop("TESSERA_AUDIT_SYNC", None)
