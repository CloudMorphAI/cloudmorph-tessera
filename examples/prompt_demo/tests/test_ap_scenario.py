"""Offline adversarial checks for the synthetic AP fixture only."""
from __future__ import annotations

from typing import Any

import pytest

from examples.prompt_demo.runtime import (
    MakerError,
    _validate_tool,
    run_offline_ap_scenario,
    run_prompt,
)


def _tool(tool_use_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": args}}


def _scripted_batch(uses: list[dict[str, Any]]):
    turns = [uses, [{"text": "Offline scripted boundary check."}]]
    calls = 0

    def converse(**_: Any) -> dict[str, Any]:
        nonlocal calls
        content = turns[calls]
        calls += 1
        return {"output": {"message": {"role": "assistant", "content": content}},
                "usage": {"inputTokens": 0, "outputTokens": 0}}

    return converse


def _event_group(run: dict[str, Any], tool_use_id: str) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, Any]]:
    matching = [(index, event) for index, event in enumerate(run["events"])
                if event.get("tool_use_id") == tool_use_id]
    maker_index, maker = next((i, e) for i, e in matching if e["type"] == "maker")
    checker_index, checker = next((i, e) for i, e in matching if e["type"] == "checker")
    system_index, system = next((i, e) for i, e in matching if e["type"] == "system")
    assert maker_index < checker_index < system_index
    return checker_index, maker, checker, system


def test_ap_flow_uses_real_tessera_decisions_and_fake_fixtures() -> None:
    receipt = run_offline_ap_scenario()

    assert receipt["mode"] == "scripted_connector_tool_flow_offline"
    assert receipt["model"] is None
    assert receipt["model_provider_requests"] == 0
    assert receipt["financial_service_requests"] == 0
    assert receipt["summary"]["proposed"] == 6
    assert receipt["summary"]["allowed"] == 5
    assert receipt["summary"]["blocked"] == 1
    assert receipt["summary"]["synthetic_adapter_contacts"] == 5
    assert receipt["summary"]["blocked_payment_adapter_contacts"] == 0
    assert receipt["summary"]["audit_chains_verified"] is True

    small_run, exception_run = receipt["runs"]
    assert small_run["mode"] == exception_run["mode"] == "scripted_connector_tool_flow_offline"
    assert small_run["audit"]["chain_verified"] is True
    assert exception_run["audit"]["chain_verified"] is True

    _, invoice_maker, invoice_checker, invoice_system = _event_group(small_run, "ap-small-invoice")
    assert invoice_maker["arguments"] == {"invoice_id": "INV-DEMO-025"}
    assert invoice_checker["decision"] == "allow"
    assert invoice_system["call_count"] == 1
    assert invoice_system["result"]["invoice"]["total_rupees"] == 25000

    _, small_maker, small_checker, small_system = _event_group(small_run, "ap-small-pay")
    assert small_maker["arguments"]["amount_rupees"] == 25000
    assert small_checker["decision"] == "allow"
    assert small_system["call_count"] == 1
    assert small_system["result"]["ledger_entry"] == {
        "payment_id": "PAY-DEMO-001", "invoice_id": "INV-DEMO-025", "payee": "vendor_acme",
        "amount_rupees": 25000, "status": "recorded_in_fake_ledger",
    }

    _, _, blocked_checker, blocked_system = _event_group(exception_run, "ap-large-pay")
    assert blocked_checker["decision"] == "block"
    assert blocked_checker["policy_id"] == "demo-vendor-payment-cap"
    assert "150,000" in blocked_checker["reason"]
    assert blocked_checker["receipt_id"]
    assert blocked_system["contacted"] is False
    assert blocked_system["call_count"] == 0
    assert blocked_system["result"] is None

    _, draft_maker, draft_checker, draft_system = _event_group(exception_run, "ap-create-draft")
    assert draft_maker["arguments"] == {"invoice_id": "INV-DEMO-500"}
    assert draft_checker["decision"] == "allow"
    assert draft_checker["receipt_id"]
    assert draft_system["call_count"] == 1
    _, _, read_checker, read_system = _event_group(exception_run, "ap-read-draft")
    assert read_checker["decision"] == "allow"
    assert read_checker["receipt_id"]
    assert read_system["result"]["found"] is True
    assert read_system["result"]["draft"]["status"] == "awaiting_human_approval"
    assert read_system["result"]["draft"]["amount_rupees"] == 500000
    assert read_system["result"]["draft"]["payment_authorized"] is False
    assert read_system["result"]["draft"]["automatic_payment"] is False

    large_pay_makers = [event for event in exception_run["events"]
                        if event["type"] == "maker" and event["tool"] == "pay_vendor"]
    assert len(large_pay_makers) == 1
    assert large_pay_makers[0]["arguments"]["amount_rupees"] == 500000
    blocked_index = next(i for i, event in enumerate(exception_run["events"])
                         if event.get("tool_use_id") == "ap-large-pay" and event["type"] == "checker")
    draft_index = next(i for i, event in enumerate(exception_run["events"])
                       if event.get("tool_use_id") == "ap-create-draft" and event["type"] == "maker")
    assert blocked_index < draft_index


def test_per_action_cap_boundary_uses_tessera_and_zero_contact_on_block() -> None:
    result = run_prompt(
        "Check the exact payment cap boundary with synthetic proposals.",
        converse_fn=_scripted_batch([
            _tool("cap-150000", "pay_vendor", {"payee": "vendor_acme", "amount_rupees": 150000}),
            _tool("over-150001", "pay_vendor", {"payee": "vendor_acme", "amount_rupees": 150001}),
        ]),
        request_id="ap-boundary-test",
        execution_mode="scripted_connector_tool_flow_offline",
    )
    checkers = [event for event in result["events"] if event["type"] == "checker"]
    system = [event for event in result["events"] if event["type"] == "system"]
    assert [(event["tool_use_id"], event["decision"]) for event in checkers] == [
        ("cap-150000", "allow"), ("over-150001", "block")]
    assert [event["call_count"] for event in system] == [1, 0]
    assert result["summary"]["system_contacts"] == 1
    assert result["summary"]["blocked_system_contacts"] == 0
    assert result["audit"]["chain_verified"] is True


@pytest.mark.parametrize("arguments", [
    {"payee": "vendor_acme", "amount_rupees": True},
    {"payee": "vendor_acme", "amount_rupees": 25000.5},
    {"payee": "vendor_acme", "amount_rupees": 0},
    {"payee": "vendor_acme", "amount_rupees": -1},
    {"payee": "vendor_acme", "amount_rupees": 10000001},
    {"payee": "vendor_attacker", "amount_rupees": 25000},
    {"payee": "vendor_acme", "amount_rupees": 25000, "invoice_id": "INV-DEMO-500"},
])
def test_adversarial_payment_arguments_fail_before_checker_forwarding(arguments: dict[str, Any]) -> None:
    message = _validate_tool("pay_vendor", arguments)
    assert message
    with pytest.raises(MakerError, match="Invalid synthetic tool or arguments"):
        run_prompt(
            "Reject this malformed synthetic payment proposal.",
            converse_fn=_scripted_batch([_tool("invalid-payment", "pay_vendor", arguments)]),
            request_id="ap-invalid-test",
            execution_mode="scripted_connector_tool_flow_offline",
        )


def test_draft_schema_cannot_lower_or_change_the_invoice_amount() -> None:
    assert _validate_tool("create_payment_draft", {"invoice_id": "INV-DEMO-500"}) is None
    assert _validate_tool("create_payment_draft", {
        "invoice_id": "INV-DEMO-500", "amount_rupees": 25000,
    })
    assert _validate_tool("create_payment_draft", {"invoice_id": "INV-DEMO-025"})
    assert _validate_tool("get_invoice", {"invoice_id": "INV-OTHER"})
