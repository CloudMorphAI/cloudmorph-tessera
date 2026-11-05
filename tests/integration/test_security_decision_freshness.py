"""Authorization must use each request's upstream and current policy context."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tessera.auth.base import AuthContext
from tessera.config import UpstreamConfig
from tessera.policy.engine import PolicyEngine
from tessera.policy.schema import Action, CumulativeSpendToday, MatchSpec, Policy
from tessera.proxy import create_app


@pytest.mark.parametrize("unified", [False, True])
def test_upstream_allow_cannot_authorize_another_upstream(test_config, tmp_path, monkeypatch, unified):
    monkeypatch.setenv("TESSERA_STATE_DIR", str(tmp_path / "state"))
    test_config.upstreams.append(UpstreamConfig(name="restricted", url="http://restricted.invalid"))
    allowed = Policy(id="allow-mock", name="Allow mock", match=MatchSpec(upstream="mock"), action=Action.allow)
    blocked = Policy(id="block-restricted", name="Block restricted", match=MatchSpec(upstream="restricted"), action=Action.block)
    forwarded = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
    monkeypatch.setattr("tessera.proxy._forward_upstream", forwarded)
    app = create_app(test_config)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.authenticator, "authenticate", lambda _: AuthContext("tester", "test", {}))
        app.state.engine = PolicyEngine([allowed, blocked])
        for upstream in ("mock", "restricted"):
            name = f"{upstream}__same_tool" if unified else "same_tool"
            response = client.post("/mcp" if unified else f"/mcp/{upstream}", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            })
            assert response.status_code == 200
            if upstream == "restricted":
                assert response.json()["result"].get("isError") is True
        assert forwarded.await_count == 1


@pytest.mark.parametrize("unified", [False, True])
def test_identical_call_rechecks_current_spend(test_config, tmp_path, monkeypatch, unified):
    monkeypatch.setenv("TESSERA_STATE_DIR", str(tmp_path / "state"))
    blocked = Policy(
        id="spend-cap", name="Spend cap", match=MatchSpec(upstream="mock"), action=Action.block,
        when=[CumulativeSpendToday(usd_threshold=10, operator="greater_than")],
    )
    forwarded = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
    monkeypatch.setattr("tessera.proxy._forward_upstream", forwarded)
    app = create_app(test_config)
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.authenticator, "authenticate", lambda _: AuthContext("tester", "test", {}))
        app.state.engine = PolicyEngine([blocked], default_action=Action.allow)
        name = "mock__same_tool" if unified else "same_tool"
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
        route = "/mcp" if unified else "/mcp/mock"
        assert not client.post(route, json=body).json()["result"].get("isError")
        app.state.state_backend.add_spend("test", 11)
        assert client.post(route, json=body).json()["result"].get("isError") is True
        assert forwarded.await_count == 1
