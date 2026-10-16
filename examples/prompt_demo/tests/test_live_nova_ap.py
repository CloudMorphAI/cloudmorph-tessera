"""Mocked-Converse regression checks for the bounded live Nova AP harness.

No test in this module calls Bedrock, launches the AWS CLI, or uses the demo
handler/DynamoDB path. Tessera runs only against prompt_demo's in-process
synthetic FastAPI fixture and SQLite audit sink.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any

import pytest

from examples.prompt_demo import live_nova_ap as harness


def _action(name: str = "get_invoice", arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if arguments is None:
        arguments = {"invoice_id": "INV-DEMO-500"}
    return {"name": name, "input": arguments}


def _response(
    name: str = "get_invoice",
    arguments: dict[str, Any] | None = None,
    *,
    tool_use_id: str = "mock-tool-use-1",
    role: str = "assistant",
    stop_reason: str = "tool_use",
    input_tokens: int = 20,
    output_tokens: int = 8,
    content: list[Any] | None = None,
) -> dict[str, Any]:
    if arguments is None:
        arguments = {"invoice_id": "INV-DEMO-500"}
    if content is None:
        content = [{"toolUse": {"toolUseId": tool_use_id, "name": name, "input": arguments}}]
    return {
        "output": {"message": {"role": role, "content": content}},
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
        "stopReason": stop_reason,
    }


def _request_kwargs(tool: str = "get_invoice") -> dict[str, Any]:
    return {
        "modelId": harness.MODEL_ID,
        "system": [{"text": "Synthetic AP test."}],
        "messages": [{"role": "user", "content": [{"text": "Look up a fictional invoice."}]}],
        "toolConfig": harness._tool_config(tool),
        "inferenceConfig": {"maxTokens": harness.MAX_OUTPUT_TOKENS_PER_CALL, "temperature": 0.0},
    }


def _mock_bedrock(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[str]:
    requests: list[str] = []

    def fake_aws_json(*args: str, **kwargs: Any) -> dict[str, Any]:
        assert args[:3] == ("bedrock-runtime", "converse", "--cli-input-json")
        assert kwargs.get("timeout") == harness.CLI_TIMEOUT_SECONDS
        requests.append(args[3])
        response = responses.pop(0)
        return response(args[3]) if callable(response) else response

    monkeypatch.setattr(harness, "_aws_json", fake_aws_json)
    return requests


def _future_deadline() -> float:
    return time.monotonic() + harness.RUN_TIMEOUT_SECONDS + 30


def test_mocked_nova_tool_proposal_must_match_the_fixed_action(monkeypatch: pytest.MonkeyPatch) -> None:
    # A valid provider-shaped response with the wrong tool/arguments is stopped
    # by the harness before it can be returned to run_prompt for fixture dispatch.
    expected = _action("get_invoice", {"invoice_id": "INV-DEMO-500"})
    budget = harness.CallBudget(_future_deadline())
    requests: list[str] = []

    def fake_aws_json(*args: str, **kwargs: Any) -> dict[str, Any]:
        assert args[:3] == ("bedrock-runtime", "converse", "--cli-input-json")
        requests.append(args[3])
        return _response("get_invoice", {"invoice_id": "INV-DEMO-025"})

    monkeypatch.setattr(harness, "_aws_json", fake_aws_json)
    converse = budget.for_run("wrong-args", [expected])
    with pytest.raises(harness.DemoError, match="did not propose"):
        converse(**_request_kwargs())

    assert len(requests) == 1
    assert budget.calls[0]["expected_proposal_matched"] is False
    assert budget.calls[0]["proposal_names"] == ["get_invoice"]


def test_mismatched_model_proposal_never_reaches_the_synthetic_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _mock_bedrock(
        monkeypatch,
        [_response("get_invoice", {"invoice_id": "INV-DEMO-025"}, tool_use_id="wrong-invoice")],
    )
    adapter_calls: list[tuple[str, dict[str, Any]]] = []

    def observe_adapter(name: str, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        adapter_calls.append((name, arguments))
        return {"synthetic": True}

    monkeypatch.setitem(harness.run_prompt.__globals__, "_synthetic_result", observe_adapter)
    budget = harness.CallBudget(_future_deadline())
    actions = [
        _action("get_invoice", {"invoice_id": "INV-DEMO-500"}),
        _action("pay_vendor", {"payee": "vendor_acme", "amount_rupees": 25000}),
    ]

    with pytest.raises(RuntimeError, match="Model service unavailable"):
        harness._run_live(
            "mismatched-proposal",
            "mismatched-proposal-no-adapter",
            "Fictional AP test only.",
            actions,
            budget,
            _future_deadline(),
            execution_mode="injected_provider_synthetic_tools",
        )

    assert len(requests) == 1
    assert budget.calls[0]["expected_proposal_matched"] is False
    assert adapter_calls == []


@pytest.mark.parametrize(
    "response",
    [
        _response(tool_use_id=""),
        _response(stop_reason="end_turn"),
        _response(content=[{"toolUse": "not-an-object"}]),
        _response(content=[{"toolUse": {"toolUseId": "id-1", "name": "get_invoice", "input": {}}},
                           {"toolUse": {"toolUseId": "id-2", "name": "get_invoice", "input": {}}}]),
    ],
)
def test_malformed_or_non_tool_converse_responses_are_rejected(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]
) -> None:
    requests = _mock_bedrock(monkeypatch, [response])
    budget = harness.CallBudget(_future_deadline())
    converse = budget.for_run("invalid-response", [_action()])

    with pytest.raises(harness.DemoError):
        converse(**_request_kwargs())

    assert len(requests) == 1
    assert budget.calls[0]["status"] == "invalid_response"


def test_unknown_action_is_rejected_before_any_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _mock_bedrock(monkeypatch, [])
    with pytest.raises(harness.DemoError, match="Unknown fixed synthetic tool"):
        harness._tool_config("delete_real_payment")
    with pytest.raises(harness.DemoError, match="Unknown fixed synthetic tool"):
        harness.CallBudget(_future_deadline()).for_run("unknown", [_action("delete_real_payment", {})])
    assert requests == []


def test_turn_and_global_model_call_limits_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    one_reply = _response()
    requests = _mock_bedrock(monkeypatch, [one_reply] * harness.MAX_MODEL_CALLS)
    budget = harness.CallBudget(_future_deadline())
    three_turns = [_action(), _action("get_invoice", {"invoice_id": "INV-DEMO-025"}), _action()]

    with pytest.raises(harness.DemoError, match="at most 2 model turn"):
        budget.for_run("too-many-turns", three_turns)
    assert requests == []

    kwargs = _request_kwargs()
    for turn in range(harness.MAX_MODEL_CALLS):
        budget.for_run(f"bounded-{turn}", [_action()])(**kwargs)
    with pytest.raises(harness.DemoError, match="six-call model budget"):
        budget.for_run("over-budget", [_action()])(**kwargs)
    assert len(requests) == harness.MAX_MODEL_CALLS


def test_model_token_budgets_are_checked_before_and_after_converse(monkeypatch: pytest.MonkeyPatch) -> None:
    no_requests = _mock_bedrock(monkeypatch, [])
    callback = harness.CallBudget(_future_deadline()).for_run("oversized-output-config", [_action()])
    oversized = _request_kwargs()
    oversized["inferenceConfig"]["maxTokens"] = harness.MAX_OUTPUT_TOKENS_PER_CALL + 1
    with pytest.raises(harness.DemoError, match="output-token limit"):
        callback(**oversized)
    assert no_requests == []

    over_input = _mock_bedrock(
        monkeypatch,
        [lambda request: _response(input_tokens=len(request.encode("utf-8")) * 2 + 1)],
    )
    budget = harness.CallBudget(_future_deadline())
    with pytest.raises(harness.DemoError, match="configured token bounds"):
        budget.for_run("over-input-token-bound", [_action()])(**_request_kwargs())
    assert len(over_input) == 1
    assert budget.calls[0]["status"] == "invalid_usage"

    over_output = _mock_bedrock(
        monkeypatch,
        [_response(output_tokens=harness.MAX_OUTPUT_TOKENS_PER_CALL + 1)],
    )
    budget = harness.CallBudget(_future_deadline())
    with pytest.raises(harness.DemoError, match="configured token bounds"):
        budget.for_run("over-output-token-bound", [_action()])(**_request_kwargs())
    assert len(over_output) == 1
    assert budget.calls[0]["status"] == "invalid_usage"


def test_mocked_model_block_never_contacts_payment_adapter_or_dynamodb(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = [
        _action("get_invoice", {"invoice_id": "INV-DEMO-500"}),
        _action("pay_vendor", {"payee": "vendor_acme", "amount_rupees": 500000}),
    ]
    requests = _mock_bedrock(
        monkeypatch,
        [
            _response("get_invoice", {"invoice_id": "INV-DEMO-500"}, tool_use_id="mock-lookup"),
            _response("pay_vendor", {"payee": "vendor_acme", "amount_rupees": 500000},
                      tool_use_id="mock-oversized-payment"),
        ],
    )

    def deny_real_cli(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Mocked harness test attempted to launch the real AWS CLI")

    monkeypatch.setattr(harness.subprocess, "run", deny_real_cli)
    budget = harness.CallBudget(_future_deadline())
    result = harness._run_live(
        "mocked-oversized-exception",
        "mocked-ap-no-dynamodb",
        "Fictional AP tool test only.",
        actions,
        budget,
        _future_deadline(),
        execution_mode="injected_provider_synthetic_tools",
    )

    assert result["mode"] == "injected_provider_synthetic_tools"
    assert result["model"] is None
    assert harness._accept_exception(result)["policy_id"] == "demo-vendor-payment-cap"
    payment_system = harness._system_for(result, "pay_vendor")
    assert payment_system["contacted"] is False
    assert payment_system["call_count"] == 0
    assert payment_system["result"] is None
    assert result["audit"]["chain_verified"] is True
    assert result["summary"]["blocked_system_contacts"] == 0
    assert len(requests) == 2
    assert all(json.loads(request)["modelId"] == harness.MODEL_ID for request in requests)
    assert "examples.prompt_demo.handler" not in sys.modules
    assert all("dynamodb" not in request.lower() for request in requests)


def test_live_run_rejects_more_than_two_turns_before_runtime_or_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _mock_bedrock(monkeypatch, [])
    budget = harness.CallBudget(_future_deadline())
    actions = [
        _action(),
        _action("get_invoice", {"invoice_id": "INV-DEMO-025"}),
        _action(),
    ]
    with pytest.raises(harness.DemoError, match="at most 2 model turn"):
        harness._run_live("too-many", "too-many", "Synthetic only.", actions, budget, _future_deadline())
    assert requests == []
    assert budget.calls == []
