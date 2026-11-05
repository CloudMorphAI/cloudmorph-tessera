"""Synthetic Hermes CRM proof using the real Tessera policy engine and proxy."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tessera.audit.sinks.sqlite import SqliteSink
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
from tessera.proxy import create_app

_POLICY_SOURCE = Path(__file__).resolve().parents[1] / "examples" / "hermes_crm" / "policy.yaml"


def _body(tool_name: str, arguments: dict[str, Any], req_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _run_demo(
    tmp_path: Path,
    actions: list[tuple[str, dict[str, Any]]],
) -> tuple[list[dict], list[dict], list[int], list[dict]]:
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    shutil.copyfile(_POLICY_SOURCE, policy_dir / "hermes-crm.yaml")
    (policy_dir / "allow-call-notes.yaml").write_text(
        """\
id: hermes-crm-allow-call-notes-update
name: Allow call notes updates
match:
  upstream: crm
  tool: crm.update_contact
when:
  - condition: arg_path_matches_regex
    arg_path: fields.call_notes
    pattern: '(?s).*'
action: allow
reason: call_notes_update_allowed
priority: 10
""",
        encoding="utf-8",
    )
    (policy_dir / "allow-contact-read.yaml").write_text(
        """\
id: hermes-crm-allow-contact-read
name: Allow contact reads
match:
  upstream: crm
  tool: crm.get_contact
action: allow
reason: contact_read_allowed
priority: 10
""",
        encoding="utf-8",
    )
    audit_path = tmp_path / "audit.db"
    config = TesseraConfig(
        audit=AuditConfig(path=str(audit_path), also_stdout=False),
        policies=PoliciesConfig(
            dir=str(policy_dir),
            reload="none",
            mode=PoliciesMode.enforcement,
            default_action="block",
        ),
        intent=IntentConfig(meta_key="tessera_intent", required=False),
        metrics=MetricsConfig(enabled=False),
        runtime=RuntimeConfig(lockdown=False),
        upstreams=[UpstreamConfig(name="crm", url="http://mock-crm", timeout_seconds=5)],
        deployment_id="cm093-synthetic-test",
    )

    forwarded: list[dict[str, Any]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        forwarded.append(body)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": "synthetic CRM response"}]},
            },
        )

    app = create_app(config)
    forwarded_counts: list[int] = []
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.http_clients["crm"] = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url="http://mock-crm",
            timeout=5.0,
        )
        responses = []
        for index, (tool_name, args) in enumerate(actions, start=1):
            responses.append(client.post("/mcp/crm", json=_body(tool_name, args, index)))
            forwarded_counts.append(len(forwarded))

    sink = SqliteSink(path=audit_path)
    try:
        events = list(sink.iter_events())
    finally:
        sink.close()
    bodies = [response.json() for response in responses]
    decisions = [event for event in events if event.get("eventType") == "decision"]
    assert len(decisions) == len(bodies)
    for body, event in zip(bodies, decisions, strict=True):
        target = body.get("result") or body.get("error", {}).get("data", {})
        assert target["_meta"]["tessera_audit_event_id"] == event["eventId"]
    return bodies, forwarded, forwarded_counts, events


def test_nested_policy_blocks_paid_update_atomically_and_audits(tmp_path: Path) -> None:
    actions = [
        (
            "crm.update_contact",
            {"record_id": "crm-demo-001", "fields": {"call_notes": "Approved follow-up"}},
        ),
        ("crm.get_contact", {"record_id": "crm-demo-001"}),
        (
            "crm.update_contact",
            {"record_id": "crm-demo-001", "fields": {"billing_status": "paid"}},
        ),
        ("crm.get_contact", {"record_id": "crm-demo-001"}),
        (
            "crm.update_contact",
            {
                "record_id": "crm-demo-001",
                "fields": {"call_notes": "Approved follow-up", "billing_status": "paid"},
            },
        ),
    ]
    responses, forwarded, forwarded_counts, audit_events = _run_demo(tmp_path, actions)

    assert forwarded_counts == [1, 2, 2, 3, 3]
    assert [call["params"]["name"] for call in forwarded] == [
        "crm.update_contact", "crm.get_contact", "crm.get_contact"
    ]
    decision_events = [event for event in audit_events if event.get("eventType") == "decision"]
    assert [event["payload"]["decision"] for event in decision_events] == [
        "allow", "allow", "block", "allow", "block"
    ]
    assert [event["payload"]["policy_id"] for event in decision_events] == [
        "hermes-crm-allow-call-notes-update",
        "hermes-crm-allow-contact-read",
        "hermes-crm-block-paid-billing",
        "hermes-crm-allow-contact-read",
        "hermes-crm-block-paid-billing",
    ]
    assert responses[2]["result"]["isError"] is True
    assert responses[4]["result"]["isError"] is True


def test_nested_leaf_rule_ignores_billing_like_text_in_call_notes(tmp_path: Path) -> None:
    actions = [
        (
            "crm.update_contact",
            {
                "record_id": "crm-demo-001",
                "fields": {"call_notes": 'Customer text: "billing_status": "paid"'},
            },
        ),
        (
            "crm.update_contact",
            {"record_id": "crm-demo-001", "fields": {"billing_status": "paid"}},
        ),
    ]
    responses, forwarded, forwarded_counts, audit_events = _run_demo(tmp_path, actions)

    assert forwarded_counts == [1, 1]
    assert len(forwarded) == 1
    assert forwarded[0]["params"]["arguments"]["fields"]["call_notes"].startswith("Customer text")
    assert "result" in responses[0]
    assert responses[1]["result"]["isError"] is True
    decisions = [event for event in audit_events if event.get("eventType") == "decision"]
    assert [event["payload"]["decision"] for event in decisions] == ["allow", "block"]
