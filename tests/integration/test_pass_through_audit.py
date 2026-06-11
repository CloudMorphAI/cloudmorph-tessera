"""Integration tests for passthrough_data_leak_candidate audit events (A-PRE-4, OQ-1).

Verifies:
- Each of the 5 data-leak risk methods emits a passthrough_data_leak_candidate event
  when audit.flag_data_leak_passthrough is True (the default).
- Non-data-leak pass-through methods (tools/list, initialize, etc.) do NOT emit the
  new event type.
- When audit.flag_data_leak_passthrough is False, no extra event is emitted.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tessera.config import AuditConfig, PoliciesConfig, TesseraConfig, UpstreamConfig
from tessera.proxy import _DATA_LEAK_PASSTHROUGH_METHODS, create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADERS = {"Authorization": "Bearer tk_test_audit_xxxxxxxxxxxxxxxxxx"}
_UPSTREAM_OK = {"jsonrpc": "2.0", "id": 1, "result": {}}


def _make_transport(resp: dict[str, Any] | None = None) -> httpx.MockTransport:
    body = resp or _UPSTREAM_OK

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=json.dumps(body).encode())

    return httpx.MockTransport(_handler)


def _build_config(
    tmp_db: str,
    flag_data_leak: bool = True,
    policy_dir: str | None = None,
    engine_eval_data_methods: bool = False,
) -> TesseraConfig:
    return TesseraConfig(
        audit=AuditConfig(path=tmp_db, flag_data_leak_passthrough=flag_data_leak),
        # v0.2.0 fix: tests must point policies.dir at a tmp path; the default
        # /etc/tessera/policies doesn't exist on dev machines (esp. Windows).
        # v0.9.0: engine_eval_data_methods defaults False here so that
        # resources/read and sampling/createMessage flow through the
        # pass-through path (which emits the data_leak_candidate event).
        # The default-True behaviour is tested in test_proxy_round_trip.py.
        policies=PoliciesConfig(
            dir=policy_dir or tempfile.mkdtemp(prefix="tessera_policies_"),
            reload="none",
            engine_eval_data_methods=engine_eval_data_methods,
        ),
        upstreams=[UpstreamConfig(name="test-upstream", url="http://mock-upstream")],
    )


def _call_method(client: TestClient, method: str, params: dict[str, Any] | None = None) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp/test-upstream", json=body, headers=_HEADERS)


def _get_audit_events(db_path: str) -> list[dict[str, Any]]:
    from tessera.audit.sinks.sqlite import SqliteSink

    sink = SqliteSink(path=db_path)
    events = list(sink.iter_events())
    sink.close()
    return events


# ---------------------------------------------------------------------------
# Tests: data-leak methods emit passthrough_data_leak_candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "prompts/get",
        "resources/read",
        "resources/subscribe",
        "completion/complete",
        "sampling/createMessage",
    ],
)
def test_data_leak_method_emits_candidate_event(method: str) -> None:
    """Each of the 5 risky pass-through methods must emit a data_leak_candidate event."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cfg = _build_config(db_path, flag_data_leak=True)
        app = create_app(cfg)
        transport = _make_transport()

        with TestClient(app, raise_server_exceptions=False) as client:
            # Inject mock transport
            for http_client in client.app.state.http_clients.values():
                http_client._transport = transport

            _call_method(client, method, params={"name": "test", "arg": "value"})

        events = _get_audit_events(db_path)
        event_types = [e.get("eventType") for e in events]
        assert "passthrough_data_leak_candidate" in event_types, (
            f"Expected passthrough_data_leak_candidate for method {method!r}, got: {event_types}"
        )

        # Find the candidate event and check payload fields
        candidate = next(e for e in events if e.get("eventType") == "passthrough_data_leak_candidate")
        payload = candidate.get("payload", {})
        assert payload.get("method") == method
        assert "principal_id" in payload
        assert "scope" in payload
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Tests: non-data-leak methods do NOT emit candidate event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "tools/list",
        "initialize",
        "ping",
        "prompts/list",
        "resources/list",
    ],
)
def test_non_data_leak_method_does_not_emit_candidate_event(method: str) -> None:
    """Non-risky pass-through methods must NOT emit a passthrough_data_leak_candidate event."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cfg = _build_config(db_path, flag_data_leak=True)
        app = create_app(cfg)
        transport = _make_transport()

        with TestClient(app, raise_server_exceptions=False) as client:
            for http_client in client.app.state.http_clients.values():
                http_client._transport = transport
            _call_method(client, method)

        events = _get_audit_events(db_path)
        event_types = [e.get("eventType") for e in events]
        assert "passthrough_data_leak_candidate" not in event_types, (
            f"Unexpected passthrough_data_leak_candidate for method {method!r}: {event_types}"
        )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Tests: flag_data_leak_passthrough=False suppresses extra event
# ---------------------------------------------------------------------------


def test_flag_disabled_suppresses_candidate_event() -> None:
    """When audit.flag_data_leak_passthrough is False, no candidate event is emitted."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cfg = _build_config(db_path, flag_data_leak=False)
        app = create_app(cfg)
        transport = _make_transport()

        with TestClient(app, raise_server_exceptions=False) as client:
            for http_client in client.app.state.http_clients.values():
                http_client._transport = transport
            _call_method(client, "prompts/get", params={"name": "my_prompt"})

        events = _get_audit_events(db_path)
        event_types = [e.get("eventType") for e in events]
        assert "passthrough_data_leak_candidate" not in event_types, (
            f"flag=False should suppress candidate event, got: {event_types}"
        )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Constant check: the 5 methods are in _DATA_LEAK_PASSTHROUGH_METHODS
# ---------------------------------------------------------------------------


def test_data_leak_set_contains_all_five_methods() -> None:
    expected = {
        "prompts/get",
        "resources/read",
        "resources/subscribe",
        "completion/complete",
        "sampling/createMessage",
    }
    assert expected.issubset(_DATA_LEAK_PASSTHROUGH_METHODS)
