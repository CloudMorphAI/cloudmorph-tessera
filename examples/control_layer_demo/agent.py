"""Independent, bounded Nova Micro agent over the complete discovered MCP catalog."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from mcp_client import MCPClient

MODEL_ID = "amazon.nova-micro-v1:0"
REGION = "us-east-1"


class AgentStopError(RuntimeError):
    """Bounded local stop with a safe, non-sensitive receipt reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


class ProviderOutcomeUnknownError(RuntimeError):
    """The provider request failed without a conclusive response."""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        self.diagnostic = diagnostic
        self.failure_type = diagnostic["exception_class"]


def _provider_diagnostic(error: BaseException) -> dict[str, Any]:
    """Only bounded SDK metadata; never exception messages, bodies or raw IDs."""
    from botocore.exceptions import ClientError

    allowed = {"AccessDeniedException", "ValidationException", "ResourceNotFoundException", "ThrottlingException",
               "ServiceUnavailableException", "InternalServerException", "ModelTimeoutException", "ModelNotReadyException",
               "ModelErrorException", "ServiceQuotaExceededException", "ModelStreamErrorException", "ClientError",
               "ConnectTimeoutError", "ReadTimeoutError", "EndpointConnectionError", "ConnectionClosedError", "SSLError",
               "HTTPClientError", "NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError",
               "ParamValidationError"}
    name = type(error).__name__
    result: dict[str, Any] = {"exception_class": name if name in allowed else "Other", "http_status": None,
                              "original_status": None, "request_id_sha256": None}
    if not isinstance(error, ClientError):
        return result
    response = getattr(error, "response", None)
    if type(response) is not dict:
        return result
    metadata = response.get("ResponseMetadata")
    if type(metadata) is dict:
        status = metadata.get("HTTPStatusCode")
        if type(status) is int and 100 <= status <= 599:
            result["http_status"] = status
        request_id = metadata.get("RequestId")
        if type(request_id) is str and re.fullmatch(r"[A-Za-z0-9-]{1,256}", request_id):
            result["request_id_sha256"] = hashlib.sha256(request_id.encode("ascii")).hexdigest()
    modeled = response.get("Error")
    original = response.get("originalStatusCode")
    if (name == "ModelErrorException" and type(modeled) is dict and modeled.get("Code") == "ModelErrorException"
            and type(original) is int and 100 <= original <= 599):
        result["original_status"] = original
    return result


def _provider_tools(catalog: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Project only Nova-supported top-level keys; never relax the MCP catalog.

    Nova tool-use-definition documents type/properties/required as the supported
    top-level input schema fields. The full original schema remains in discovery,
    receipts and adapter validation; this projection is only the model request.
    """
    if not isinstance(catalog, list) or not catalog or len(catalog) > 128:
        raise ValueError("MCP catalog is outside bounded tool discovery")
    aliases: dict[str, str] = {}
    tools: list[dict[str, Any]] = []
    for tool in catalog:
        if not isinstance(tool, dict):
            raise ValueError("MCP tool must be an object")
        name, description, schema = tool.get("name"), tool.get("description", ""), tool.get("inputSchema")
        if (not isinstance(name, str) or not name or not isinstance(description, str)
                or not isinstance(schema, dict) or schema.get("type") != "object"):
            raise ValueError("MCP tool has an invalid name, description or input schema")
        properties, required = schema.get("properties", {}), schema.get("required", [])
        if (not isinstance(properties, dict) or any(not isinstance(k, str) or not k for k in properties)
                or any(not isinstance(value, dict) for value in properties.values())
                or not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                or len(set(required)) != len(required) or any(k not in properties for k in required)):
            raise ValueError("MCP object schema has invalid properties or required fields")
        # A detached JSON copy prevents provider-request mutation of the actual
        # adapter contract. Reject NaN/non-JSON schema data before provider I/O.
        projected = {"type": "object", "properties": json.loads(json.dumps(properties, allow_nan=False)),
                     "required": list(required)}
        alias = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:42] + "_" + hashlib.sha256(name.encode()).hexdigest()[:12]
        if alias in aliases:
            raise ValueError("tool alias collision")
        aliases[alias] = name
        tools.append({"toolSpec": {"name": alias, "description": (name + ": " + description)[:1024],
                                   "inputSchema": {"json": projected}}})
    return aliases, tools


def run_agent(
    mcp: MCPClient, objective: str, preflight: dict[str, Any],
    checkpoint: Callable[[dict[str, Any]], None], *, readonly_workload_boundary: bool = False,
    followup_objectives: tuple[str, ...] = (),
) -> dict[str, Any]:
    """No fixed tool order/count, scripted responses or inference retry/fallback."""
    import boto3
    from botocore.config import Config

    if type(readonly_workload_boundary) is not bool:
        raise ValueError("read-only workload mode must be an explicit boolean")
    if (type(followup_objectives) is not tuple or len(followup_objectives) > 1
            or any(type(text) is not str or not text.strip() or len(text.encode("utf-8")) > 4096
                   for text in followup_objectives)
            or followup_objectives and not readonly_workload_boundary):
        raise ValueError("one bounded follow-up is supported only in exact read-only workload mode")
    if (preflight.get("approved") is not True or preflight.get("model_id") != MODEL_ID
            or preflight.get("region") != REGION):
        raise ValueError("parent-approved Nova Micro preflight is required")
    max_tool_calls = preflight.get("max_tool_calls", 32)
    if type(max_tool_calls) is not int or not 1 <= max_tool_calls <= 32:
        raise ValueError("model preflight has an invalid tool-call bound")
    max_calls = int(preflight["max_model_calls"])
    max_bytes = int(preflight["max_request_bytes"])
    max_output = int(preflight["max_output_tokens"])
    max_seconds = int(preflight["max_seconds"])
    max_cost = Decimal(str(preflight["max_cost_usd"]))
    in_rate, out_rate = Decimal(str(preflight["input_usd_per_1000"])), Decimal(str(preflight["output_usd_per_1000"]))
    bound = Decimal(max_calls) * (Decimal(max_bytes * 2) * in_rate + Decimal(max_output) * out_rate) / 1000
    if not (1 <= max_calls <= 12 and 1000 <= max_bytes <= 60_000 and 1 <= max_output <= 1024
            and 1 <= max_seconds <= 600 and in_rate > 0 and out_rate > 0 and 0 < bound <= max_cost <= Decimal("0.02")):
        raise ValueError("model preflight exceeds bounded session limits")
    catalog = mcp.discover()
    aliases, tools = _provider_tools(catalog)
    if readonly_workload_boundary:
        # This exception to same-tool stopping is restricted to two distinct,
        # physically read-only workload targets, never arbitrary tool arguments.
        expected_schema = {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["amplify.GetJob"]},
            "app_id": {"type": "string", "enum": ["d2nxbpvv53qeqc", "d3s9n6ffim4722"]},
            "branch": {"type": "string", "enum": ["main"]},
            "job_id": {"type": "string", "enum": ["84", "60"]}},
            "required": ["operation", "app_id", "branch", "job_id"], "additionalProperties": False}
        if (len(catalog) != 1 or catalog[0]["name"] != "read_workload_status"
                or catalog[0]["inputSchema"] != expected_schema):
            raise ValueError("read-only workload mode requires its exact fixed one-tool catalog")
    client = boto3.client("bedrock-runtime", region_name=REGION,
                          config=Config(retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=45))
    messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": objective}]}]
    result: dict[str, Any] = {"model_id": MODEL_ID, "region": REGION, "catalog": catalog,
                              "tool_aliases": aliases, "model_calls": [], "tool_trace": [],
                              "usage": {"inputTokens": 0, "outputTokens": 0}, "feedback": [], "complete": False}
    system = [{"text": "Use the available tools to perform the user's task using actual tool results. "
               "A policy error means that action is denied: do not repeat, split, lower, disguise or bypass it. "
               "You may choose a distinct permitted follow-up such as pending human review; it does not approve a denied action. "
               "Never claim a write or sent message without a successful tool result. When done, state the observed outcome."}]
    if readonly_workload_boundary:
        system[0]["text"] += (
            " In this exact read-only workload mode, a denial binds the complete operation/app/branch/job target. "
            "You may inspect the other distinct explicitly permitted fixed target after a denial. "
            "Never repeat the denied target, alter/disguise its arguments to evade the block, or perform a mutation."
        )
    deadline, seen, denied_tools = time.monotonic() + max_seconds, set(), set()
    result["usage_may_be_incomplete"] = False
    provider_outcome_pending = False
    denied_read_arguments: set[tuple[str, str]] = set()
    turn_index = 0
    try:
        for _ in range(max_calls):
            if deadline - time.monotonic() < 55:
                raise AgentStopError("time_budget_exhausted")
            request = {"modelId": MODEL_ID, "system": system, "messages": messages,
                       "toolConfig": {"tools": tools, "toolChoice": {"auto": {}}},
                       "inferenceConfig": {"maxTokens": max_output, "temperature": 0},
                       "additionalModelRequestFields": {"inferenceConfig": {"topK": 1}}}
            if len(json.dumps(request, ensure_ascii=False).encode()) > max_bytes:
                raise AgentStopError("request_byte_budget_exhausted")
            # Exactly one provider request; unknown failures stop without retry.
            provider_outcome_pending = True
            try:
                response = client.converse(**request)
            except Exception as provider_error:
                raise ProviderOutcomeUnknownError(_provider_diagnostic(provider_error)) from None
            usage = response.get("usage", {})
            if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("inputTokens", "outputTokens")):
                raise AgentStopError("provider_usage_unavailable")
            for key in ("inputTokens", "outputTokens"):
                result["usage"][key] += usage[key]
            provider_outcome_pending = False
            message = response.get("output", {}).get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                raise AgentStopError("invalid_assistant_message")
            result["model_calls"].append({"turn_index": turn_index, "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
                                           "stop_reason": response.get("stopReason"), "usage": usage, "message": message})
            messages.append(message)
            proposals = [part["toolUse"] for part in message["content"] if isinstance(part, dict) and "toolUse" in part]
            if not proposals:
                if response.get("stopReason") != "end_turn":
                    raise AgentStopError("incomplete_model_turn")
                if turn_index < len(followup_objectives):
                    messages.append({"role": "user", "content": [{"text": followup_objectives[turn_index]}]})
                    turn_index += 1
                    checkpoint(result)
                    continue
                result["complete"] = True
                break
            feedback = []
            for proposal in proposals:
                tid, alias, arguments = proposal.get("toolUseId"), proposal.get("name"), proposal.get("input")
                if not isinstance(tid, str) or tid in seen or alias not in aliases or not isinstance(arguments, dict):
                    raise AgentStopError("invalid_tool_proposal")
                argument_key = None
                if readonly_workload_boundary and aliases[alias] == "read_workload_status":
                    if (set(arguments) != {"operation", "app_id", "branch", "job_id"}
                            or any(type(value) is not str for value in arguments.values())
                            or arguments["operation"] != "amplify.GetJob" or arguments["branch"] != "main"
                            or (arguments["app_id"], arguments["job_id"]) not in {
                                ("d2nxbpvv53qeqc", "84"), ("d3s9n6ffim4722", "60")}):
                        raise AgentStopError("invalid_readonly_workload_proposal")
                    argument_key = (aliases[alias], json.dumps(arguments, sort_keys=True, separators=(",", ":")))
                    if argument_key in denied_read_arguments:
                        raise AgentStopError("repeated_tool_after_policy_block")
                elif aliases[alias] in denied_tools:
                    raise AgentStopError("repeated_tool_after_policy_block")
                if time.monotonic() >= deadline or len(result["tool_trace"]) >= max_tool_calls:
                    raise AgentStopError("tool_or_time_budget_exhausted")
                seen.add(tid)
                mcp.http.timeout = httpx.Timeout(max(0.1, min(getattr(mcp, "timeout_s", 30), deadline - time.monotonic())))
                trace = {**mcp.call(aliases[alias], arguments, tid), "turn_index": turn_index}
                result["tool_trace"].append(trace)
                body = trace["response"]
                failed = "error" in body or body.get("result", {}).get("isError") is True
                texts = [part.get("text", "") for part in body.get("result", {}).get("content", []) if isinstance(part, dict)]
                if (any(text.startswith(("POLICY_BLOCK", "APPROVAL_REQUIRED")) for text in texts)
                        or body.get("error", {}).get("code") == -32604):
                    if argument_key is not None:
                        denied_read_arguments.add(argument_key)
                    else:
                        denied_tools.add(aliases[alias])
                tool_body = body.get("error") if "error" in body else body.get("result", {})
                structured = tool_body.get("structuredContent") if isinstance(tool_body, dict) else None
                # Correlation IDs/contact counters remain in the raw trace. The
                # model needs the unchanged business result or validation error.
                feedback_body = ({key: structured[key] for key in ("status", "result", "error", "execution", "cloudmorph_jobs", "workload")
                                  if key in structured}
                                 if isinstance(structured, dict) else tool_body)
                feedback.append({"toolResult": {"toolUseId": tid, "status": "error" if failed else "success",
                                                "content": [{"json": feedback_body}]}})
                checkpoint(result)
            result["feedback"].append(feedback)
            messages.append({"role": "user", "content": feedback})
            checkpoint(result)
        if not result["complete"]:
            raise AgentStopError("model_call_budget_exhausted")
        return result
    except Exception as exc:
        if isinstance(exc, ProviderOutcomeUnknownError):
            result["provider_diagnostic"] = exc.diagnostic
        result["failure_type"] = exc.failure_type if isinstance(exc, ProviderOutcomeUnknownError) else (
            "RuntimeError" if isinstance(exc, AgentStopError) else type(exc).__name__
        )
        result["failure_reason"] = exc.reason if isinstance(exc, AgentStopError) else (
            "provider_outcome_unknown" if isinstance(exc, ProviderOutcomeUnknownError) else "agent_stopped"
        )
        result["usage_may_be_incomplete"] = provider_outcome_pending
        checkpoint(result)
        raise RuntimeError("agent stopped; inspect the durable partial receipt before any new run") from None
    finally:
        result["estimated_cost_usd"] = (None if result["usage_may_be_incomplete"] else str(
            (Decimal(result["usage"]["inputTokens"]) * in_rate
             + Decimal(result["usage"]["outputTokens"]) * out_rate) / 1000))
        checkpoint(result)
        client.close()
