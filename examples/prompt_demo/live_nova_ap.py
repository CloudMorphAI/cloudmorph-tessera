"""Run a tightly bounded live Nova prompt-maker against local Tessera fixtures.

The Windows AWS CLI owns credential resolution through the existing Karn
profile. Only synthetic Converse JSON crosses the WSL/Windows boundary.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = Path(__file__).resolve().parent
for import_root in (ROOT, DEMO_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from runtime import _SPECS, _validate_tool, run_prompt  # noqa: E402

AWS_CLI = Path("/mnt/c/Program Files/Amazon/AWSCLIV2/aws.exe")
AWS_PROFILE = "Karn"
AWS_REGION = "us-east-1"
EXPECTED_ACCOUNT = "237509402889"
EXPECTED_ARN = "arn:aws:iam::237509402889:user/Karn"
MODEL_ID = "amazon.nova-micro-v1:0"
MAX_MODEL_CALLS = 6
MAX_TURNS_PER_RUN = 2
MAX_OUTPUT_TOKENS_PER_CALL = 256
MAX_REQUEST_JSON_BYTES = 12_000
MAX_ESTIMATED_RUN_COST_USD = Decimal("0.01")
CLI_TIMEOUT_SECONDS = 20
RUN_TIMEOUT_SECONDS = 150


class DemoError(RuntimeError):
    """A safe, bounded live demo failure."""


def _safe_error(stderr: str) -> str:
    """Keep useful AWS service status without ever echoing credential fields."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "no error details returned"
    chosen = next((line for line in lines if "An error occurred" in line), lines[-1])
    normalized = re.sub(r"[^a-z0-9]", "", chosen.lower())
    if any(
        key in normalized
        for key in (
            "secretaccesskey",
            "accesskeyid",
            "sessiontoken",
            "securitytoken",
            "authorization",
            "credential",
            "password",
            "clientsecret",
        )
    ):
        return "AWS CLI returned an error containing a credential field; details suppressed"
    return chosen[:500]


def _aws_json(*args: str, timeout: int = CLI_TIMEOUT_SECONDS) -> Any:
    if not AWS_CLI.is_file():
        raise DemoError(f"Windows AWS CLI is missing at the configured path: {AWS_CLI}")
    # This is deliberately a child-environment allowlist, not a copy of WSL's
    # environment. Windows interop keeps the Windows user's normal home/profile
    # resolution; AWS credentials, profile/config overrides, and endpoint
    # variables are selected neither from WSL nor copied into the child.
    env = {
        "AWS_MAX_ATTEMPTS": "1",
        "AWS_RETRY_MODE": "standard",
        "AWS_PAGER": "",
    }
    command = [
        str(AWS_CLI),
        *args,
        "--profile",
        AWS_PROFILE,
        "--region",
        AWS_REGION,
        "--cli-connect-timeout",
        "5",
        "--cli-read-timeout",
        "15",
        "--no-cli-pager",
        "--output",
        "json",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable/arguments and shell is explicitly disabled.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DemoError(f"AWS CLI {args[0]} timed out after {timeout}s; no retry was attempted.") from exc
    if completed.returncode:
        raise DemoError(
            f"AWS CLI {args[0]} failed with exit code {completed.returncode}: "
            f"{_safe_error(completed.stderr)}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DemoError(f"AWS CLI {args[0]} returned non-JSON output.") from exc


def _price_for(inference_type: str) -> dict[str, str]:
    products = _aws_json(
        "pricing",
        "get-products",
        "--service-code",
        "AmazonBedrock",
        "--filters",
        "Type=TERM_MATCH,Field=model,Value=Nova Micro",
        f"Type=TERM_MATCH,Field=regionCode,Value={AWS_REGION}",
        "Type=TERM_MATCH,Field=feature,Value=On-demand Inference",
        f"Type=TERM_MATCH,Field=inferenceType,Value={inference_type}",
        "--max-results",
        "5",
        "--query",
        "PriceList",
    )
    for raw in products:
        product = json.loads(raw)
        attrs = product.get("product", {}).get("attributes", {})
        if (
            attrs.get("model") != "Nova Micro"
            or attrs.get("regionCode") != AWS_REGION
            or attrs.get("feature") != "On-demand Inference"
            or attrs.get("inferenceType") != inference_type
        ):
            continue
        for term in product.get("terms", {}).get("OnDemand", {}).values():
            for dimension in term.get("priceDimensions", {}).values():
                price = dimension.get("pricePerUnit", {}).get("USD")
                if price is not None and dimension.get("unit") == "1K tokens":
                    return {
                        "model": attrs["model"],
                        "region": attrs["regionCode"],
                        "inference_type": inference_type,
                        "unit": dimension["unit"],
                        "usd": price,
                        "effective_date": term.get("effectiveDate", "")[:10],
                    }
    raise DemoError(f"AWS Price List has no Nova Micro on-demand {inference_type} rate for {AWS_REGION}.")


def _preflight() -> dict[str, Any]:
    identity = _aws_json(
        "sts",
        "get-caller-identity",
        "--query",
        "{Account:Account,Arn:Arn}",
    )
    if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_ARN:
        raise DemoError("Karn profile STS identity did not match the expected account and user.")

    catalog = _aws_json(
        "bedrock",
        "list-foundation-models",
        "--by-provider",
        "amazon",
        "--query",
        "modelSummaries[?modelId=='amazon.nova-micro-v1:0'].{id:modelId,name:modelName,inferenceTypes:inferenceTypesSupported}",
    )
    if len(catalog) != 1 or "ON_DEMAND" not in catalog[0].get("inferenceTypes", []):
        raise DemoError("Nova Micro v1 on-demand model ID is not in the current us-east-1 catalog.")

    availability = _aws_json(
        "bedrock",
        "get-foundation-model-availability",
        "--model-id",
        MODEL_ID,
    )
    if (
        availability.get("authorizationStatus") != "AUTHORIZED"
        or availability.get("entitlementAvailability") != "AVAILABLE"
        or availability.get("regionAvailability") != "AVAILABLE"
        or availability.get("agreementAvailability", {}).get("status") != "AVAILABLE"
    ):
        raise DemoError("Nova Micro v1 is not authorized, entitled, and available for this profile/region.")

    prices = {
        "input": _price_for("Input tokens"),
        "output": _price_for("Output tokens"),
    }
    input_per_1k = Decimal(prices["input"]["usd"])
    output_per_1k = Decimal(prices["output"]["usd"])
    max_input_tokens = MAX_MODEL_CALLS * MAX_REQUEST_JSON_BYTES * 2
    max_output_tokens = MAX_MODEL_CALLS * MAX_OUTPUT_TOKENS_PER_CALL
    worst_cost = (
        Decimal(max_input_tokens) * input_per_1k / Decimal(1000)
        + Decimal(max_output_tokens) * output_per_1k / Decimal(1000)
    )
    if worst_cost > MAX_ESTIMATED_RUN_COST_USD:
        raise DemoError("The conservative per-run token cost bound exceeds the configured USD 0.01 limit.")
    return {
        "caller_identity": identity,
        "catalog": catalog[0],
        "availability": availability,
        "prices": prices,
        "price_source": "AWS Price List API via Windows AWS CLI",
        "conservative_cost_bound": {
            "assumption": "at most 2 input tokens per serialized request byte plus max output tokens",
            "max_calls": MAX_MODEL_CALLS,
            "max_request_json_bytes_per_call": MAX_REQUEST_JSON_BYTES,
            "max_output_tokens_per_call": MAX_OUTPUT_TOKENS_PER_CALL,
            "max_input_tokens_estimate": max_input_tokens,
            "max_output_tokens": max_output_tokens,
            "usd": str(worst_cost.quantize(Decimal("0.00000001"))),
            "limit_usd": str(MAX_ESTIMATED_RUN_COST_USD),
        },
    }


def _tool_config(name: str) -> dict[str, Any]:
    for tool_name, description, schema in _SPECS:
        if tool_name == name:
            return {
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool_name,
                            "description": description,
                            "inputSchema": {"json": schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": tool_name}},
            }
    raise DemoError(f"Unknown fixed synthetic tool requested by the harness: {name}")


class CallBudget:
    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.calls: list[dict[str, Any]] = []

    def for_run(self, run_name: str, expected: list[dict[str, Any]]):
        if not isinstance(expected, list) or not 1 <= len(expected) <= MAX_TURNS_PER_RUN:
            raise DemoError(f"A scenario may contain at most {MAX_TURNS_PER_RUN} model turn(s).")
        for action in expected:
            if not isinstance(action, dict) or set(action) != {"name", "input"}:
                raise DemoError("A scenario action must contain only a fixed name and input object.")
            name, arguments = action.get("name"), action.get("input")
            _tool_config(name)  # Reject unknown fixture actions before the provider call.
            if _validate_tool(name, arguments):
                raise DemoError("A scenario action did not match the synthetic fixture schema.")
        call_index = 0

        def converse(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_index
            if len(self.calls) >= MAX_MODEL_CALLS:
                raise DemoError("The six-call model budget is exhausted.")
            if time.monotonic() + CLI_TIMEOUT_SECONDS >= self.deadline:
                raise DemoError("The overall 150-second model-run deadline is too close; no call was made.")
            if call_index >= len(expected):
                raise DemoError("No additional model turn is allowed for this scenario.")
            if kwargs.get("modelId") != MODEL_ID:
                raise DemoError("The Converse request did not use the preverified Nova Micro model ID.")
            inference = kwargs.get("inferenceConfig", {})
            if not isinstance(inference, dict) or inference.get("maxTokens") != MAX_OUTPUT_TOKENS_PER_CALL:
                raise DemoError("The Converse request exceeded the configured output-token limit.")

            request = {
                "modelId": MODEL_ID,
                "system": kwargs["system"],
                "messages": kwargs["messages"],
                "toolConfig": kwargs["toolConfig"],
                "inferenceConfig": inference,
            }
            request_json = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
            request_bytes = len(request_json.encode("utf-8"))
            if request_bytes > MAX_REQUEST_JSON_BYTES:
                raise DemoError("The synthetic Converse request exceeded the 12,000-byte input bound.")

            entry: dict[str, Any] = {
                "sequence": len(self.calls) + 1,
                "run": run_name,
                "model_id": MODEL_ID,
                "region": AWS_REGION,
                "request_json_bytes": request_bytes,
                "max_output_tokens": MAX_OUTPUT_TOKENS_PER_CALL,
                "status": "attempted",
                "input_tokens": None,
                "output_tokens": None,
            }
            self.calls.append(entry)
            try:
                response = _aws_json(
                    "bedrock-runtime",
                    "converse",
                    "--cli-input-json",
                    request_json,
                    timeout=CLI_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                entry["status"] = "failed"
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
                raise

            if not isinstance(response, dict):
                entry["status"] = "invalid_response"
                raise DemoError("Nova returned a non-object Converse response.")
            usage = response.get("usage")
            if not isinstance(usage, dict):
                entry["status"] = "invalid_usage"
                raise DemoError("Nova response usage was missing or outside the configured token bounds.")
            input_tokens, output_tokens = usage.get("inputTokens"), usage.get("outputTokens")
            if (
                type(input_tokens) is not int
                or input_tokens < 0
                or input_tokens > request_bytes * 2
                or type(output_tokens) is not int
                or not 0 <= output_tokens <= MAX_OUTPUT_TOKENS_PER_CALL
            ):
                entry["status"] = "invalid_usage"
                raise DemoError("Nova response usage was missing or outside the configured token bounds.")

            output = response.get("output")
            assistant = output.get("message") if isinstance(output, dict) else None
            content = assistant.get("content") if isinstance(assistant, dict) else None
            if (
                not isinstance(assistant, dict)
                or assistant.get("role") != "assistant"
                or not isinstance(content, list)
                or len(content) > 16
                or response.get("stopReason") != "tool_use"
                or any(not isinstance(item, dict) for item in content)
            ):
                entry["status"] = "invalid_response"
                raise DemoError("Nova returned an invalid or non-tool Converse response.")

            proposals = []
            for item in content:
                if "toolUse" not in item:
                    continue
                proposal = item["toolUse"]
                if (
                    not isinstance(proposal, dict)
                    or not isinstance(proposal.get("toolUseId"), str)
                    or not 1 <= len(proposal["toolUseId"]) <= 128
                    or not isinstance(proposal.get("name"), str)
                    or not isinstance(proposal.get("input"), dict)
                ):
                    entry["status"] = "invalid_response"
                    raise DemoError("Nova returned a malformed tool proposal.")
                proposals.append(proposal)
            if len(proposals) != 1:
                entry["status"] = "invalid_response"
                raise DemoError("Nova must return exactly one fixed tool proposal for this turn.")
            expected_action = expected[call_index]
            matched = (
                proposals[0].get("name") == expected_action["name"]
                and proposals[0].get("input") == expected_action["input"]
            )
            entry.update(
                status="response_received",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stop_reason=response.get("stopReason"),
                proposal_names=[proposal.get("name") for proposal in proposals],
                proposal_tool_use_ids=[proposal["toolUseId"] for proposal in proposals],
                expected_tool=expected_action["name"],
                expected_proposal_matched=matched,
            )
            call_index += 1
            if not matched:
                raise DemoError(
                    f"Nova did not propose the one allowed {expected_action['name']} action with the fixed fixture arguments; "
                    "the response was stopped before Tessera or a mock adapter received it."
                )
            return response

        return converse


def _run_live(
    name: str,
    request_id: str,
    message: str,
    actions: list[dict[str, Any]],
    budget: CallBudget,
    deadline: float,
    execution_mode: str = "live_model_synthetic_tools",
) -> dict[str, Any]:
    if execution_mode not in ("live_model_synthetic_tools", "injected_provider_synthetic_tools"):
        raise DemoError("Unsupported execution mode for the bounded provider harness.")
    result = run_prompt(
        message,
        converse_fn=budget.for_run(name, actions),
        request_id=request_id,
        deadline=deadline,
        execution_mode=execution_mode,
        model_id=MODEL_ID,
        tool_configs=[_tool_config(action["name"]) for action in actions],
        max_turns=MAX_TURNS_PER_RUN,
        max_tokens=MAX_OUTPUT_TOKENS_PER_CALL,
        temperature=0.0,
    )
    if result.get("mode") != execution_mode:
        raise DemoError(f"{name} was not recorded with the configured provider execution mode.")
    if execution_mode == "live_model_synthetic_tools" and result.get("model") != MODEL_ID:
        raise DemoError(f"{name} was not recorded with the live Nova model ID.")
    if execution_mode == "injected_provider_synthetic_tools" and result.get("model") is not None:
        raise DemoError(f"{name} did not retain the injected-provider model-null label.")
    if not result.get("audit", {}).get("chain_verified"):
        raise DemoError(f"{name} did not produce a verified local Tessera audit chain.")
    return result


def _checker_for(result: dict[str, Any], tool: str) -> dict[str, Any]:
    return next(
        (event for event in result["events"] if event.get("type") == "checker" and event.get("tool") == tool),
        {},
    )


def _system_for(result: dict[str, Any], tool: str) -> dict[str, Any]:
    return next(
        (event for event in result["events"] if event.get("type") == "system" and event.get("tool") == tool),
        {},
    )


def _accept_small(result: dict[str, Any]) -> None:
    lookup = _checker_for(result, "get_invoice")
    payment = _checker_for(result, "pay_vendor")
    payment_system = _system_for(result, "pay_vendor")
    ledger = payment_system.get("result", {}).get("ledger_entry", {})
    if not (
        lookup.get("decision") == "allow"
        and _system_for(result, "get_invoice").get("call_count") == 1
        and payment.get("decision") == "allow"
        and payment_system.get("call_count") == 1
        and ledger.get("invoice_id") == "INV-DEMO-025"
        and ledger.get("amount_rupees") == 25000
        and len([event for event in result["events"] if event.get("type") == "checker"]) == 2
    ):
        raise DemoError("Small-invoice Nova run did not match the fixed allowed fake-ledger scenario.")


def _accept_exception(result: dict[str, Any]) -> dict[str, Any]:
    lookup = _checker_for(result, "get_invoice")
    payment = _checker_for(result, "pay_vendor")
    payment_system = _system_for(result, "pay_vendor")
    if not (
        lookup.get("decision") == "allow"
        and _system_for(result, "get_invoice").get("call_count") == 1
        and payment.get("decision") == "block"
        and payment.get("policy_id") == "demo-vendor-payment-cap"
        and payment_system.get("call_count") == 0
        and result["summary"].get("blocked_system_contacts") == 0
        and len([event for event in result["events"] if event.get("type") == "checker"]) == 2
    ):
        raise DemoError("The oversized payment did not produce the required block with zero adapter calls.")
    return payment


def _accept_draft(result: dict[str, Any]) -> dict[str, Any]:
    draft = _system_for(result, "create_payment_draft").get("result", {}).get("draft", {})
    readback = _system_for(result, "get_payment_draft").get("result", {}).get("draft", {})
    if not (
        _checker_for(result, "create_payment_draft").get("decision") == "allow"
        and _system_for(result, "create_payment_draft").get("call_count") == 1
        and _checker_for(result, "get_payment_draft").get("decision") == "allow"
        and _system_for(result, "get_payment_draft").get("call_count") == 1
        and draft.get("amount_rupees") == 500000
        and draft.get("status") == "awaiting_human_approval"
        and draft.get("payment_authorized") is False
        and draft.get("automatic_payment") is False
        and readback == draft
        and len([event for event in result["events"] if event.get("type") == "checker"]) == 2
    ):
        raise DemoError("The separate approval-draft run did not match its fixed non-payment acceptance checks.")
    return readback


def _write_receipt(receipt: dict[str, Any]) -> Path:
    directory = Path(__file__).with_name("live_nova_receipts")
    directory.mkdir(exist_ok=True)
    path = directory / f"{receipt['scenario_id']}.json"
    receipt["receipt_path"] = path.relative_to(ROOT).as_posix()
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(path)
    return path


def main() -> int:
    scenario_id = "ap-nova-" + uuid.uuid4().hex
    started_at = datetime.now(UTC).isoformat()
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    budget = CallBudget(deadline)
    receipt: dict[str, Any] = {
        "scenario": "synthetic_ap_payment_exception_live_nova",
        "scenario_id": scenario_id,
        "started_at_utc": started_at,
        "mode": "live_model_synthetic_tools",
        "model": MODEL_ID,
        "provider": "Amazon Bedrock Converse via Windows AWS CLI",
        "aws_profile": AWS_PROFILE,
        "aws_region": AWS_REGION,
        "prompts_are_synthetic": True,
        "financial_actions_are_fake_local_fixtures": True,
        "payment_service_requests": 0,
        "credential_transfer_to_wsl": False,
        "handler_or_dynamodb_path_used": False,
        "runs": [],
    }
    stage = "read-only-preflight"
    try:
        preflight = _preflight()
        receipt["preflight"] = preflight
        receipt["limits"] = {
            "max_model_calls": MAX_MODEL_CALLS,
            "max_turns_per_run": MAX_TURNS_PER_RUN,
            "max_output_tokens_per_call": MAX_OUTPUT_TOKENS_PER_CALL,
            "max_request_json_bytes_per_call": MAX_REQUEST_JSON_BYTES,
            "cli_retries": 0,
            "cli_timeout_seconds_per_call": CLI_TIMEOUT_SECONDS,
            "overall_timeout_seconds": RUN_TIMEOUT_SECONDS,
        }

        stage = "small-invoice"
        small_actions = [
            {"name": "get_invoice", "input": {"invoice_id": "INV-DEMO-025"}},
            {"name": "pay_vendor", "input": {"payee": "vendor_acme", "amount_rupees": 25000}},
        ]
        small = _run_live(
            "small-invoice",
            f"{scenario_id}-small",
            "This is a fictional AP demo. First look up the synthetic invoice INV-DEMO-025. After its result, propose one pay_vendor call to vendor_acme for exactly INR 25,000. Do not use any other tool or make any other payment.",
            small_actions,
            budget,
            deadline,
        )
        _accept_small(small)
        receipt["runs"].append(small)

        stage = "oversized-exception"
        exception_actions = [
            {"name": "get_invoice", "input": {"invoice_id": "INV-DEMO-500"}},
            {"name": "pay_vendor", "input": {"payee": "vendor_acme", "amount_rupees": 500000}},
        ]
        exception = _run_live(
            "oversized-exception",
            f"{scenario_id}-exception",
            "This is a fictional AP demo. First look up synthetic invoice INV-DEMO-500. After its result, propose its exact full amount once as pay_vendor: vendor_acme, INR 500,000. If Tessera blocks it, stop this conversation; never retry, lower, split, disguise, schedule, or otherwise evade the payment cap.",
            exception_actions,
            budget,
            deadline,
        )
        blocked_payment = _accept_exception(exception)
        receipt["runs"].append(exception)

        stage = "separate-approval-draft"
        draft_prompt = (
            "This is a new, separate fictional AP review request after an actual Tessera payment-policy block. "
            "The prior check blocked the single full INR 500,000 pay_vendor proposal for invoice INV-DEMO-500 "
            f"with policy {blocked_payment.get('policy_id')} and reason: {blocked_payment.get('reason')}. "
            "Do not retry or split payment. Create a review-only approval draft for INV-DEMO-500, then read it back. "
            "This request cannot authorize or send a payment."
        )
        draft_actions = [
            {"name": "create_payment_draft", "input": {"invoice_id": "INV-DEMO-500"}},
            {"name": "get_payment_draft", "input": {"draft_id": "DRAFT-DEMO-500"}},
        ]
        draft = _run_live(
            "separate-approval-draft",
            f"{scenario_id}-draft",
            draft_prompt,
            draft_actions,
            budget,
            deadline,
        )
        draft_readback = _accept_draft(draft)
        receipt["runs"].append(draft)
        all_checkers = [event for run in receipt["runs"] for event in run["events"] if event["type"] == "checker"]
        all_system = [event for run in receipt["runs"] for event in run["events"] if event["type"] == "system"]
        ledger_rows = [
            event["result"]["ledger_entry"]
            for event in all_system
            if event["tool"] == "pay_vendor" and event["contacted"]
        ]
        receipt["summary"] = {
            "model_calls": len(budget.calls),
            "proposals": sum(run["summary"]["proposed"] for run in receipt["runs"]),
            "allowed": sum(event["decision"] == "allow" for event in all_checkers),
            "blocked": sum(event["decision"] == "block" for event in all_checkers),
            "synthetic_adapter_contacts": sum(event["call_count"] for event in all_system),
            "blocked_payment_adapter_contacts": _system_for(exception, "pay_vendor").get("call_count"),
            "fake_ledger_payment_rows": ledger_rows,
            "approval_draft_readback": draft_readback,
            "audit_chains_verified": all(run["audit"]["chain_verified"] for run in receipt["runs"]),
            "all_tool_use_ids_correlated_to_tessera_receipts": all(
                _checker_for(run, tool).get("tool_use_id")
                and _checker_for(run, tool).get("receipt_id")
                for run, tool in [
                    (small, "get_invoice"),
                    (small, "pay_vendor"),
                    (exception, "get_invoice"),
                    (exception, "pay_vendor"),
                    (draft, "create_payment_draft"),
                    (draft, "get_payment_draft"),
                ]
            ),
        }
        if (
            receipt["summary"]["model_calls"] > MAX_MODEL_CALLS
            or receipt["summary"]["proposals"] != 6
            or receipt["summary"]["allowed"] != 5
            or receipt["summary"]["blocked"] != 1
            or receipt["summary"]["synthetic_adapter_contacts"] != 5
            or receipt["summary"]["blocked_payment_adapter_contacts"] != 0
            or not receipt["summary"]["audit_chains_verified"]
            or not receipt["summary"]["all_tool_use_ids_correlated_to_tessera_receipts"]
        ):
            raise DemoError("The aggregate live AP scenario failed its fixed acceptance checks.")
        receipt["status"] = "verified"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["failure"] = {
            "stage": stage,
            "error_type": type(exc).__name__,
            "message": str(exc)[:700],
        }

    usage_records = [call for call in budget.calls if call.get("status") == "response_received"]
    total_input = sum(call["input_tokens"] for call in usage_records)
    total_output = sum(call["output_tokens"] for call in usage_records)
    all_usage_known = len(usage_records) == len(budget.calls)
    receipt["provider_calls"] = budget.calls
    receipt["usage"] = {
        "responses_with_usage": len(usage_records),
        "attempted_calls": len(budget.calls),
        "input_tokens": total_input if all_usage_known else None,
        "output_tokens": total_output if all_usage_known else None,
        "usage_complete": all_usage_known,
    }
    if all_usage_known and "preflight" in receipt:
        input_rate = Decimal(receipt["preflight"]["prices"]["input"]["usd"])
        output_rate = Decimal(receipt["preflight"]["prices"]["output"]["usd"])
        actual_estimate = (
            Decimal(total_input) * input_rate / Decimal(1000)
            + Decimal(total_output) * output_rate / Decimal(1000)
        )
        receipt["usage"]["estimated_model_cost_usd"] = str(actual_estimate.quantize(Decimal("0.00000001")))
    else:
        receipt["usage"]["estimated_model_cost_usd"] = None
    receipt["finished_at_utc"] = datetime.now(UTC).isoformat()
    _write_receipt(receipt)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt.get("status") == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
