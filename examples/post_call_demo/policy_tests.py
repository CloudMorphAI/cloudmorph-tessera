"""Actual proxy/fixture/audit integration checks; no provider or cloud calls."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
from policy_integration import (  # noqa: E402
    DEAL_ID,
    ContractError,
    PolicyGateway,
    require_scenario,
    run_scripted,
)


def test_selected_scenario_uses_actual_gate_and_correlated_audit():
    with PolicyGateway() as gate:
        run_scripted(gate)
        receipt = gate.finish()
    require_scenario(receipt)
    assert receipt["summary"] == {"allowed": 7, "blocked": 1, "adapter_contacts": 7,
                                  "blocked_adapter_contacts": 0}
    assert len({event["audit_id"] for event in receipt["events"]}) == 8
    review = receipt["events"][6]
    assert review["tessera_request_id"] != receipt["events"][5]["tessera_request_id"]
    assert all(row["eventHash"] for row in receipt["audit"]["events"])


def test_every_valid_integer_discount_enforces_exact_boundary_and_atomicity():
    with PolicyGateway() as gate:
        for value in range(101):
            before = gate.bridge.call("snapshot")
            event = gate.invoke("crm.update_deal", {"deal_id": DEAL_ID,
                "fields": {"notes": f"valid-mixed-{value}", "discount_pct": value}}, f"boundary-{value}")
            after = gate.bridge.call("snapshot")
            assert event["decision"] == ("block" if value > 10 else "allow")
            assert event["adapter_contacts"] == (0 if value > 10 else 1)
            if value > 10:
                assert before == after  # Notes and discount are both unchanged; no partial write.
            else:
                assert after["state"]["deals"][DEAL_ID]["discount_pct"] == value
        receipt = gate.finish()
    assert receipt["audit"]["chain_verified"] is True
    assert receipt["summary"]["blocked"] == 90


@pytest.mark.parametrize("value", [True, False, None, "30", "10.0", 10.0, 10.5, -1, 101, float("nan"), float("inf"), [], {}])
def test_malformed_discount_is_contract_error_before_policy_or_adapter(value):
    with PolicyGateway() as gate:
        before = gate.bridge.call("snapshot")
        with pytest.raises(ContractError):
            gate.invoke("crm.update_deal", {"deal_id": DEAL_ID,
                "fields": {"notes": "must not persist", "discount_pct": value}}, "bad-discount")
        assert gate.bridge.call("snapshot") == before
        assert gate.events == []


@pytest.mark.parametrize("args", [
    {"deal_id": DEAL_ID, "fields": {"notes": "okay", "owner": "agent"}},
    {"deal_id": DEAL_ID, "fields": {"notes": "okay"}, "discount_cap_pct": 100},
    {"deal_id": DEAL_ID, "fields": {}},
    {"deal_id": "OTHER-DEAL", "fields": {"notes": "okay"}},
])
def test_unknown_fields_and_identifiers_do_not_reach_adapter(args):
    with PolicyGateway() as gate:
        before = gate.bridge.call("snapshot")
        with pytest.raises(ContractError):
            gate.invoke("crm.update_deal", args, "unknown-field")
        assert before == gate.bridge.call("snapshot")


def test_unknown_tool_and_policy_mutation_default_block_without_adapter():
    with PolicyGateway() as gate:
        before = gate.bridge.call("snapshot")
        for index, tool in enumerate(("policy.update", "crm.delete_deal", "drafts.send")):
            event = gate.invoke(tool, {"discount_cap_pct": 100}, f"unknown-{index}")
            assert event["decision"] == "block"
            assert event["adapter_contacts"] == 0
        assert before == gate.bridge.call("snapshot")
        receipt = gate.finish()
    assert all(event["policy_id"] is None for event in receipt["events"])
    assert receipt["audit"]["chain_verified"]


def test_adapter_error_remains_actual_allowed_policy_and_error_no_write():
    with PolicyGateway() as gate:
        event = gate.invoke("tasks.read", {"task_id": "TASK-0000000000000000"}, "missing-task")
        assert event["decision"] == "allow"
        assert event["error"]["code"] == "FIXTURE_RECORD_NOT_FOUND"
        assert event["adapter_contacts"] == 1 and event["adapter_writes"] == 0
        receipt = gate.finish()
    assert receipt["events"][0]["decision"] == "allow"


def test_same_key_replay_is_separately_checked_and_produces_no_extra_entry_or_write():
    args = {"task_kind": "internal_discount_review", "deal_id": DEAL_ID,
            "title": "Review30", "details": "Request only", "requested_discount_pct": 30,
            "idempotency_key": "review-unique"}
    with PolicyGateway() as gate:
        first = gate.invoke("tasks.create", args, "review-first")
        replay = gate.invoke("tasks.create", copy.deepcopy(args), "review-replay")
        assert first["result"] == replay["result"]
        assert replay["adapter_contacts"] == 1  # The bridge was contacted after a fresh gate evaluation.
        assert replay["adapter_writes"] == 0
        changed = {**args, "title": "Different request"}
        conflict = gate.invoke("tasks.create", changed, "review-conflict")
        assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert conflict["adapter_writes"] == 0
        receipt = gate.finish()
    assert len(receipt["readback"]["tasks"]) == 1
    assert len({event["audit_id"] for event in receipt["events"]}) == 3


def test_handler_failure_is_cached_without_a_successful_transcript(monkeypatch):
    import server
    attempts = []

    def fail(_):
        attempts.append(True)
        raise RuntimeError("Unexpected internal detail that must not be displayed")

    monkeypatch.setattr(server, "run_scripted", fail)
    service = server.DemoService()
    status, result = service.run("scripted", "failed-script")
    assert status == 502 and "events" not in result
    assert "Unexpected internal detail" not in str(result)
    replay_status, replay = service.run("scripted", "failed-script")
    assert replay_status == 502 and replay["replayed"] is True and len(attempts) == 1


def test_model_failure_preserves_actual_partial_audit_and_consumes_one_attempt(monkeypatch):
    import model_runner
    import server
    calls = []

    def unavailable(gateway):
        calls.append(True)
        gateway.invoke("crm.get_deal", {"deal_id": DEAL_ID}, "before-provider-failure")
        raise model_runner.ModelRunError("Injected offline provider failure", partial={"attempted_calls": 1})

    monkeypatch.setattr(model_runner, "run_model", unavailable)
    service = server.DemoService(enable_nova=True)
    status, result = service.run("nova", "failed-nova")
    assert status == 502 and "events" not in result
    assert result["partial_evidence"]["audit"]["chain_verified"] is True
    assert result["partial_evidence"]["summary"]["allowed"] == 1
    assert service.run("nova", "failed-nova")[1]["replayed"] is True
    assert service.run("nova", "different-attempt")[0] == 409
    assert len(calls) == 1  # Injected offline failure, never an actual provider call.
