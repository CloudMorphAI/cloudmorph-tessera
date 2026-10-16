"""Mocked-Converse regression checks for the bounded live Nova AP harness.

No test in this module calls Bedrock, launches the AWS CLI, or uses the demo
handler/DynamoDB path. Tessera runs only against prompt_demo's in-process
synthetic FastAPI fixture and SQLite audit sink.
"""
from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace
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


def test_aws_cli_uses_allowlisted_environment_and_explicit_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attacker_markers = {
        "AWS_ACCESS_KEY_ID": "DUMMY_ACCESS_ID_MARKER",
        "AWS_SECRET_ACCESS_KEY": "DUMMY_SECRET_KEY_MARKER",
        "AWS_SESSION_TOKEN": "DUMMY_SESSION_TOKEN_MARKER",
        "AWS_SECURITY_TOKEN": "DUMMY_SECURITY_TOKEN_MARKER",
        "AWS_PROFILE": "attacker-profile-marker",
        "AWS_DEFAULT_PROFILE": "attacker-default-profile-marker",
        "AWS_CONFIG_FILE": "DUMMY_CONFIG_PATH_MARKER",
        "AWS_SHARED_CREDENTIALS_FILE": "DUMMY_CREDENTIAL_PATH_MARKER",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "DUMMY_WEB_IDENTITY_PATH_MARKER",
        "AWS_ROLE_ARN": "dummy-role-arn-marker",
        "AWS_ROLE_SESSION_NAME": "dummy-role-session-marker",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/dummy-container-relative-marker",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1/dummy-container-uri-marker",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN": "DUMMY_CONTAINER_TOKEN_MARKER",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "DUMMY_CONTAINER_TOKEN_PATH_MARKER",
        "AWS_ENDPOINT_URL": "http://127.0.0.1/dummy-endpoint-marker",
        "AWS_ENDPOINT_URL_STS": "http://127.0.0.1/dummy-sts-endpoint-marker",
        "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "http://127.0.0.1/dummy-bedrock-endpoint-marker",
        "AWS_CA_BUNDLE": "DUMMY_CA_PATH_MARKER",
        "HOME": "DUMMY_HOME_PATH_MARKER",
        "UNRELATED_MARKER": "DUMMY_UNRELATED_ENV_MARKER",
    }
    for key, value in attacker_markers.items():
        monkeypatch.setenv(key, value)

    class FakeAwsCli:
        def is_file(self) -> bool:
            return True

        def __str__(self) -> str:
            return "/mock/windows/aws.exe"

    monkeypatch.setattr(harness, "AWS_CLI", FakeAwsCli())
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"Account":"237509402889"}', stderr="")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    shell_like_argument = "Account; echo DUMMY_SHELL_MARKER"
    result = harness._aws_json("sts", "get-caller-identity", "--query", shell_like_argument)

    assert result == {"Account": "237509402889"}
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        "/mock/windows/aws.exe",
        "sts",
        "get-caller-identity",
        "--query",
        shell_like_argument,
        "--profile",
        "Karn",
        "--region",
        "us-east-1",
        "--cli-connect-timeout",
        "5",
        "--cli-read-timeout",
        "15",
        "--no-cli-pager",
        "--output",
        "json",
    ]
    assert options["shell"] is False
    assert options["timeout"] == harness.CLI_TIMEOUT_SECONDS
    assert options["env"] == {
        "AWS_MAX_ATTEMPTS": "1",
        "AWS_RETRY_MODE": "standard",
        "AWS_PAGER": "",
    }
    assert not set(attacker_markers).intersection(options["env"])
    assert "DUMMY_SHELL_MARKER" in command[4]


@pytest.mark.parametrize(
    "stderr",
    [
        '{"SecretAccessKey":"DUMMY_SECRET_MARKER","SessionToken":"DUMMY_TOKEN_MARKER"}',
        '{"accessKeyId":"DUMMY_ACCESS_ID_MARKER","AWS_SESSION_TOKEN":"DUMMY_SESSION_MARKER"}',
        '{"secret_access_key":"DUMMY_SNAKE_CASE_SECRET_MARKER","authorization":"DUMMY_AUTH_MARKER"}',
    ],
)
def test_cli_error_redacts_snake_and_camel_case_credentials(stderr: str) -> None:
    rendered = harness._safe_error(stderr)
    assert rendered == "AWS CLI returned an error containing a credential field; details suppressed"
    for marker in (
        "DUMMY_SECRET_MARKER",
        "DUMMY_TOKEN_MARKER",
        "DUMMY_ACCESS_ID_MARKER",
        "DUMMY_SESSION_MARKER",
        "DUMMY_SNAKE_CASE_SECRET_MARKER",
        "DUMMY_AUTH_MARKER",
    ):
        assert marker not in rendered


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


def test_exception_acceptance_requires_the_payment_cap_policy_id() -> None:
    result = {
        "events": [
            {"type": "checker", "tool": "get_invoice", "decision": "allow"},
            {"type": "system", "tool": "get_invoice", "call_count": 1},
            {"type": "checker", "tool": "pay_vendor", "decision": "block", "policy_id": "unrelated-policy"},
            {"type": "system", "tool": "pay_vendor", "call_count": 0},
        ],
        "summary": {"blocked_system_contacts": 0},
    }

    with pytest.raises(harness.DemoError, match="oversized payment"):
        harness._accept_exception(result)


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
