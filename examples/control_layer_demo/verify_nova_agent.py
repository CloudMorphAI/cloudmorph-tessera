"""Offline Nova request/protocol/boundary checks; no credentials or provider calls.

Run with existing WSL Python -B. All Converse/MCP replies are labeled mocks;
actual network/socket creation and boto3 client construction are denied. Local
botocore service-shape validation reads installed package data only. PASS proves
these source contracts, never a successful model, cloud, or policy activation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import sys
import types
from pathlib import Path
from unittest.mock import patch

import boto3
import botocore.session
from botocore.errorfactory import ClientExceptionsFactory
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

_SCHEMA = {"type": "object", "properties": {
    "operation": {"type": "string", "enum": ["amplify.GetJob"]},
    "app_id": {"type": "string", "enum": ["d2nxbpvv53qeqc", "d3s9n6ffim4722"]},
    "branch": {"type": "string", "enum": ["main"]}, "job_id": {"type": "string", "enum": ["84", "60"]}},
    "required": ["operation", "app_id", "branch", "job_id"], "additionalProperties": False}
_CATALOG = [{"name": "read_workload_status", "description": "Offline fixed readonly fixture", "inputSchema": _SCHEMA}]
_A = {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc", "branch": "main", "job_id": "84"}
_B = {"operation": "amplify.GetJob", "app_id": "d3s9n6ffim4722", "branch": "main", "job_id": "60"}
_PREFLIGHT = {"approved": True, "model_id": "amazon.nova-micro-v1:0", "region": "us-east-1",
              "max_model_calls": 6, "max_request_bytes": 12000, "max_output_tokens": 1024,
              "max_seconds": 300, "max_tool_calls": 4, "max_cost_usd": "0.01",
              "input_usd_per_1000": "0.000035", "output_usd_per_1000": "0.00014"}


class _Checks:
    def __init__(self) -> None:
        self.count = 0
        self.mock_calls = 0
        self.network_calls = 0

    def ok(self, value: object) -> None:
        if not value:
            raise AssertionError("OFFLINE_CHECK_FAILED")
        self.count += 1

    def deny_network(self, *args, **kwargs):
        self.network_calls += 1
        raise AssertionError("OFFLINE_NETWORK_FORBIDDEN")


class _MCP:
    def __init__(self, catalog) -> None:
        self.catalog = copy.deepcopy(catalog)
        self.calls = []
        self.http = types.SimpleNamespace()
        self.timeout_s = 30

    def discover(self):
        return self.catalog

    def call(self, name, arguments, tool_id):
        self.calls.append((name, copy.deepcopy(arguments)))
        body = ({"result": {"isError": True, "content": [{"text": "POLICY_BLOCK: offline fixture"}]}}
                if arguments == _B else {"result": {"content": [{"text": "offline allowed fixture"}],
                    "structuredContent": {"source": "AWS_MCP", "execution": "read_only_complete",
                        "cloudmorph_jobs": [{"appId": arguments["app_id"], "jobId": arguments["job_id"],
                                             "status": "SUCCEED", "commitId": "1" * 40}],
                        "workload": "tessera-ui", "internal_correlation": "OFFLINE_NOT_FOR_MODEL"}}})
        return {"response": body, "tool_use_id": tool_id}


class _Provider:
    def __init__(self, replies) -> None:
        self.replies = iter(replies)
        self.requests = []
        self.closed = False

    def converse(self, **request):
        self.requests.append(copy.deepcopy(request))
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply)

    def close(self) -> None:
        self.closed = True


def _response(content, stop="tool_use") -> dict:
    return {"usage": {"inputTokens": 1, "outputTokens": 1}, "output": {"message": {
        "role": "assistant", "content": content}}, "stopReason": stop,
        "ResponseMetadata": {"RequestId": "offline-response-id"}}


def verify() -> dict:
    sys.dont_write_bytecode = True
    filename = Path(__file__).with_name("agent.py")
    raw = filename.read_bytes()
    module = types.ModuleType("_nova_agent_offline")
    exec(compile(raw, str(filename), "exec"), module.__dict__)  # noqa: S102 - reviewed adjacent source, no CLI
    checks = _Checks()
    service = botocore.session.get_session().get_service_model("bedrock-runtime")
    operation = service.operation_model("Converse")
    exceptions = ClientExceptionsFactory().create_client_exceptions(service)
    model_error_shape = next(shape for shape in operation.error_shapes if shape.name == "ModelErrorException")
    checks.ok(model_error_shape.members["originalStatusCode"].type_name == "integer")
    alias = next(iter(module._provider_tools(_CATALOG)[0]))
    end = _response([{"text": "offline mock completion"}], "end_turn")

    def proposal(arguments, tool_id):
        return _response([{"toolUse": {"toolUseId": tool_id, "name": alias, "input": arguments}}])

    def run(replies, *, mode=True, followups=(), catalog=_CATALOG, changes=None, expected=None):
        client, proxy, partial, constructed = _Provider(replies), _MCP(catalog), {}, []

        def factory(*args, **kwargs):
            if args != ("bedrock-runtime",):
                raise AssertionError("OFFLINE_UNEXPECTED_SDK_CLIENT")
            constructed.append((args, kwargs))
            return client

        with (patch.object(boto3, "client", factory), patch.object(boto3, "Session", checks.deny_network),
              patch.object(socket, "create_connection", checks.deny_network),
              patch.object(socket, "socket", checks.deny_network)):
            try:
                result = module.run_agent(proxy, "Inspect first fixed target using tools.",
                    {**_PREFLIGHT, **(changes or {})}, lambda value: partial.update(copy.deepcopy(value)),
                    readonly_workload_boundary=mode, followup_objectives=followups)
            except (RuntimeError, ValueError):
                if expected is None:
                    raise
                if expected == "preflight":
                    checks.ok(not constructed)
                else:
                    checks.ok(partial.get("failure_reason") == expected)
                result = partial
            else:
                checks.ok(expected is None and result["complete"])
        if constructed:
            checks.ok(constructed[0][1]["config"].retries["total_max_attempts"] == 1 and client.closed)
        for request in client.requests:
            validate_parameters(request, operation.input_shape)
            checks.ok(request["additionalModelRequestFields"] == {"inferenceConfig": {"topK": 1}}
                      and request["inferenceConfig"] == {"maxTokens": 1024, "temperature": 0}
                      and request["toolConfig"]["toolChoice"] == {"auto": {}}
                      and all(set(item["toolSpec"]["inputSchema"]["json"]) == {"type", "properties", "required"}
                              for item in request["toolConfig"]["tools"]))
        checks.mock_calls += len(client.requests)
        return proxy, result, client

    original = copy.deepcopy(_CATALOG)
    original[0]["inputSchema"].update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "read"})
    before = copy.deepcopy(original)
    _, projected = module._provider_tools(original)
    checks.ok(original == before and set(projected[0]["toolSpec"]["inputSchema"]["json"]) == {"type", "properties", "required"})
    projected[0]["toolSpec"]["inputSchema"]["json"]["properties"]["app_id"]["enum"].append("changed")
    checks.ok(original == before)
    proxy, _, _ = run([end], mode=False)
    checks.ok(not proxy.calls)
    for first, second in [(_B, _A), (_A, _B)]:
        proxy, result, _ = run([proposal(first, "1"), proposal(second, "2"), end])
        checks.ok([args for _, args in proxy.calls] == [first, second] and result["catalog"] == _CATALOG)
    proxy, result, client = run([proposal(_A, "feedback"), end])
    feedback = client.requests[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["json"]
    checks.ok(feedback == {"execution": "read_only_complete", "workload": "tessera-ui", "cloudmorph_jobs": [
        {"appId": _A["app_id"], "jobId": _A["job_id"], "status": "SUCCEED", "commitId": "1" * 40}]}
        and result["tool_trace"][0]["response"]["result"]["structuredContent"]["internal_correlation"]
        == "OFFLINE_NOT_FOR_MODEL")
    reversed_b = dict(reversed(list(_B.items())))
    proxy, _, _ = run([proposal(_B, "1"), proposal(reversed_b, "2")], expected="repeated_tool_after_policy_block")
    checks.ok(len(proxy.calls) == 1)
    proxy, _, _ = run([proposal(_B, "1"), proposal(_A, "2")], mode=False, expected="repeated_tool_after_policy_block")
    checks.ok(len(proxy.calls) == 1)
    for invalid in [{**_A, "job_id": "60"}, {**_A, "operation": "amplify.DeleteApp"},
                    {**_A, "branch": "other"}, {**_A, "extra": "unknown"}, {**_A, "job_id": 84}]:
        proxy, _, _ = run([proposal(invalid, "1")], expected="invalid_readonly_workload_proposal")
        checks.ok(not proxy.calls)
    proxy, result, client = run([proposal(_A, "1"), end, proposal(_B, "2"), end], followups=("Inspect the second fixed target.",))
    checks.ok([item["turn_index"] for item in result["model_calls"]] == [0, 0, 1, 1]
              and [item["turn_index"] for item in result["tool_trace"]] == [0, 1]
              and client.requests[2]["messages"][-1] == {"role": "user", "content": [{"text": "Inspect the second fixed target."}]})
    proxy, _, _ = run([end, end], followups=("No forced tool choice.",))
    checks.ok(not proxy.calls)
    run([end], followups=("Second turn cannot exceed global budget.",), changes={"max_model_calls": 1}, expected="model_call_budget_exhausted")
    proxy, _, _ = run([proposal(_A, "1"), proposal(_B, "2")], changes={"max_tool_calls": 1}, expected="tool_or_time_budget_exhausted")
    checks.ok(len(proxy.calls) == 1)
    for followups, mode in [(("one",), False), (["one"], True), (("",), True), (("one", "two"), True), (("x" * 4097,), True)]:
        run([], mode=mode, followups=followups, expected="preflight")
    for bound in [0, 33, True, "4"]:
        run([], changes={"max_tool_calls": bound}, expected="preflight")
    for change in ["second", "name", "enum", "extra", "additional"]:
        bad = copy.deepcopy(_CATALOG)
        if change == "second":
            bad.append({"name": "other", "inputSchema": {"type": "object"}})
        elif change == "name":
            bad[0]["name"] = "control__read_workload_status"
        elif change == "enum":
            bad[0]["inputSchema"]["properties"]["job_id"]["enum"].append("other")
        elif change == "extra":
            bad[0]["inputSchema"]["properties"]["other"] = {"type": "string"}
        else:
            bad[0]["inputSchema"]["additionalProperties"] = True
        run([], catalog=bad, expected="preflight")
    for schema in [{"type": "array"}, {"type": "object", "properties": []},
                   {"type": "object", "properties": {"x": {}}, "required": ["x", "x"]},
                   {"type": "object", "required": ["missing"]}, {"type": "object", "properties": {"x": False}},
                   {"type": "object", "properties": {"x": {"const": float("nan")}}}]:
        try:
            module._provider_tools([{"name": "bad", "inputSchema": schema}])
        except (ValueError, TypeError):
            checks.ok(True)
        else:
            raise AssertionError("OFFLINE_SCHEMA_REJECTION_EXPECTED")
    _diagnostics(module, exceptions, run, checks)
    checks.ok(checks.network_calls == 0)
    return {"status": "PASS", "checks": checks.count, "mockConverseCalls": checks.mock_calls,
            "actualProviderCalls": 0, "actualMcpCalls": 0, "networkCalls": 0, "sourceSha256": hashlib.sha256(raw).hexdigest(),
            "botocoreConverseParametersValidated": True, "liveModelOrPolicyProof": False}


def _diagnostics(module, exceptions, run, checks: _Checks) -> None:
    request_id = "01234567-89ab-cdef-0123-456789abcdef"
    response = {"Error": {"Code": "ModelErrorException", "Message": "NEVER_EXPORT_PRIVATE_SENTINEL"},
                "originalStatusCode": 500, "originalMessage": "NEVER_EXPORT_PRIVATE_SENTINEL",
                "ResponseMetadata": {"HTTPStatusCode": 424, "RequestId": request_id,
                                     "HTTPHeaders": {"authorization": "NEVER_EXPORT_PRIVATE_SENTINEL"}}}
    error = exceptions.ModelErrorException(response, "Converse")
    expected = {"exception_class": "ModelErrorException", "http_status": 424, "original_status": 500,
                "request_id_sha256": hashlib.sha256(request_id.encode()).hexdigest()}
    checks.ok(module._provider_diagnostic(error) == expected)
    proxy, result, client = run([error], expected="provider_outcome_unknown")
    checks.ok(not proxy.calls and len(client.requests) == 1 and result["provider_diagnostic"] == expected
              and result["usage_may_be_incomplete"] is True and result["estimated_cost_usd"] is None
              and not result["complete"] and not result["model_calls"]
              and "NEVER_EXPORT_PRIVATE_SENTINEL" not in json.dumps(result) and request_id not in json.dumps(result))
    for value in [None, [], {"ResponseMetadata": []}, {"ResponseMetadata": {
            "HTTPStatusCode": True, "RequestId": ["first", "second"]}, "originalStatusCode": "500"}]:
        bad = exceptions.ModelErrorException(copy.deepcopy(response), "Converse")
        bad.response = value
        diagnostic = module._provider_diagnostic(bad)
        checks.ok(diagnostic == {"exception_class": "ModelErrorException", "http_status": None,
                                 "original_status": None, "request_id_sha256": None})
    for invalid in ["", "x" * 257, "with space", "with\nnewline", "nonascii-" + chr(233), None, 42, True]:
        altered = copy.deepcopy(response)
        altered["ResponseMetadata"]["RequestId"] = invalid
        checks.ok(module._provider_diagnostic(exceptions.ModelErrorException(altered, "Converse"))["request_id_sha256"] is None)
    for invalid in [99, 600, True, "424", 424.0, None]:
        altered = copy.deepcopy(response)
        altered["ResponseMetadata"]["HTTPStatusCode"] = invalid
        altered["originalStatusCode"] = invalid
        diagnostic = module._provider_diagnostic(exceptions.ModelErrorException(altered, "Converse"))
        checks.ok(diagnostic["http_status"] is None and diagnostic["original_status"] is None)
    misplaced = copy.deepcopy(response)
    misplaced.pop("originalStatusCode")
    misplaced["Error"]["originalStatusCode"] = 500
    checks.ok(module._provider_diagnostic(exceptions.ModelErrorException(misplaced, "Converse"))["original_status"] is None)
    checks.ok(module._provider_diagnostic(ClientError(response, "Converse"))["original_status"] is None)
    arbitrary = type("NEVER_EXPORT_PRIVATE_CLASS", (Exception,), {})("NEVER_EXPORT_PRIVATE_SENTINEL")
    checks.ok(module._provider_diagnostic(arbitrary) == {"exception_class": "Other", "http_status": None,
                                                       "original_status": None, "request_id_sha256": None})


if __name__ == "__main__":
    if sys.argv[1:]:
        print('{"status":"FAIL","code":"NO_ARGUMENTS_ALLOWED","actualProviderCalls":0}', flush=True)
        raise SystemExit(2)
    try:
        result = verify()
    except BaseException:
        print('{"status":"FAIL","code":"OFFLINE_NOVA_CHECK_FAILED","actualProviderCalls":0}', flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True), flush=True)
