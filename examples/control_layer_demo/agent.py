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

    def __init__(self, failure_type: str) -> None:
        self.failure_type = failure_type


def run_agent(
    mcp: MCPClient, objective: str, preflight: dict[str, Any],
    checkpoint: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """No fixed tool order/count, scripted responses or inference retry/fallback."""
    import boto3
    from botocore.config import Config

    if (preflight.get("approved") is not True or preflight.get("model_id") != MODEL_ID
            or preflight.get("region") != REGION):
        raise ValueError("parent-approved Nova Micro preflight is required")
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
    if not catalog:
        raise ValueError("MCP returned no tools")
    aliases, tools = {}, []
    for tool in catalog:
        alias = re.sub(r"[^A-Za-z0-9_-]", "_", tool["name"])[:42] + "_" + hashlib.sha256(tool["name"].encode()).hexdigest()[:12]
        if alias in aliases:
            raise ValueError("tool alias collision")
        aliases[alias] = tool["name"]
        tools.append({"toolSpec": {"name": alias, "description": (tool["name"] + ": " + tool.get("description", ""))[:1024],
                                   "inputSchema": {"json": tool["inputSchema"]}}})
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
    deadline, seen, denied_tools = time.monotonic() + max_seconds, set(), set()
    result["usage_may_be_incomplete"] = False
    provider_outcome_pending = False
    try:
        for _ in range(max_calls):
            if deadline - time.monotonic() < 55:
                raise AgentStopError("time_budget_exhausted")
            request = {"modelId": MODEL_ID, "system": system, "messages": messages,
                       "toolConfig": {"tools": tools, "toolChoice": {"auto": {}}},
                       "inferenceConfig": {"maxTokens": max_output, "temperature": 0}}
            if len(json.dumps(request, ensure_ascii=False).encode()) > max_bytes:
                raise AgentStopError("request_byte_budget_exhausted")
            # Exactly one provider request; unknown failures stop without retry.
            provider_outcome_pending = True
            try:
                response = client.converse(**request)
            except Exception as provider_error:
                raise ProviderOutcomeUnknownError(type(provider_error).__name__) from None
            usage = response.get("usage", {})
            if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("inputTokens", "outputTokens")):
                raise AgentStopError("provider_usage_unavailable")
            for key in ("inputTokens", "outputTokens"):
                result["usage"][key] += usage[key]
            provider_outcome_pending = False
            message = response.get("output", {}).get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                raise AgentStopError("invalid_assistant_message")
            result["model_calls"].append({"request_id": response.get("ResponseMetadata", {}).get("RequestId"),
                                           "stop_reason": response.get("stopReason"), "usage": usage, "message": message})
            messages.append(message)
            proposals = [part["toolUse"] for part in message["content"] if isinstance(part, dict) and "toolUse" in part]
            if not proposals:
                if response.get("stopReason") != "end_turn":
                    raise AgentStopError("incomplete_model_turn")
                result["complete"] = True
                break
            feedback = []
            for proposal in proposals:
                tid, alias, arguments = proposal.get("toolUseId"), proposal.get("name"), proposal.get("input")
                if not isinstance(tid, str) or tid in seen or alias not in aliases or not isinstance(arguments, dict):
                    raise AgentStopError("invalid_tool_proposal")
                if aliases[alias] in denied_tools:
                    raise AgentStopError("repeated_tool_after_policy_block")
                if time.monotonic() >= deadline or len(result["tool_trace"]) >= 32:
                    raise AgentStopError("tool_or_time_budget_exhausted")
                seen.add(tid)
                mcp.http.timeout = httpx.Timeout(max(0.1, min(getattr(mcp, "timeout_s", 30), deadline - time.monotonic())))
                trace = mcp.call(aliases[alias], arguments, tid)
                result["tool_trace"].append(trace)
                body = trace["response"]
                failed = "error" in body or body.get("result", {}).get("isError") is True
                texts = [part.get("text", "") for part in body.get("result", {}).get("content", []) if isinstance(part, dict)]
                if (any(text.startswith(("POLICY_BLOCK", "APPROVAL_REQUIRED")) for text in texts)
                        or body.get("error", {}).get("code") == -32604):
                    denied_tools.add(aliases[alias])
                tool_body = body.get("error") if "error" in body else body.get("result", {})
                structured = tool_body.get("structuredContent") if isinstance(tool_body, dict) else None
                # Correlation IDs/contact counters remain in the raw trace. The
                # model needs the unchanged business result or validation error.
                feedback_body = ({key: structured[key] for key in ("status", "result", "error") if key in structured}
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
        result["estimated_cost_usd"] = str((Decimal(result["usage"]["inputTokens"]) * in_rate + Decimal(result["usage"]["outputTokens"]) * out_rate) / 1000)
        checkpoint(result)
        client.close()
