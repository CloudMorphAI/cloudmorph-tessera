"""Reusable offline workload adapter boundary check; never opens a port.

Run: python examples/control_layer_demo/verify_workload_read_boundary.py
Native MCP sessions, transport and HTTP server construction are fakes. Socket,
DNS, credential-provider and model calls are tripwired. Synthetic metadata-only
ledgers live in private RAM and are removed. This grants no live-run permission.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import logging
import os
import signal
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch


class OfflineBoundaryError(Exception):
    """Finite check failure without payload/error output."""


def require(value):
    if not value:
        raise OfflineBoundaryError


def run():
    checks = []
    attempts = []
    os.umask(0o077)
    logging.disable(logging.CRITICAL)

    def forbidden(*_args, **_kwargs):
        attempts.append(1)
        raise OfflineBoundaryError

    def profile(frame, event, _arg):
        module = frame.f_globals.get("__name__", "")
        if event == "call" and (
            (module.startswith(("boto3", "botocore", "aiobotocore", "aioboto3"))
             and frame.f_code.co_name in {"client", "resource", "create_client", "get_credentials",
                                          "get_frozen_credentials", "_make_api_call"})
            or module.startswith(("anthropic", "openai", "google.genai"))
        ):
            forbidden()

    mounts = Path("/proc/mounts").read_text().splitlines()
    require(any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in mounts))  # noqa: S108 - verified RAM
    with contextlib.ExitStack() as guards:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.socket.bind",
                       "socket.socket.listen", "socket.create_connection", "socket.getaddrinfo"):
            guards.enter_context(patch(target, forbidden))
        sys.setprofile(profile)
        guards.callback(sys.setprofile, None)
        path = Path(__file__).with_name("aws_readiness_server.py")
        spec = importlib.util.spec_from_file_location("offline_workload_adapter", path)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        expected = {"tessera-ui": {"d2nxbpvv53qeqc": "84"}, "labs-ui": {"d3s9n6ffim4722": "60"}}
        require(expected == adapter.WORKLOAD_JOBS)
        require(hashlib.sha256(adapter.READONLY_SCRIPT.encode()).hexdigest()
                == "48420fd0d2837249a5db0f578a4f21f327339914ddd97ecaed8cef8a3853a18f")
        for workload, target in expected.items():
            app, job = next(iter(target.items()))
            tree = ast.parse(adapter.WORKLOAD_SCRIPTS[workload])
            native = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "call_boto3"]
            require(len(native) == 1 and not native[0].args)
            require({kw.arg: ast.literal_eval(kw.value) for kw in native[0].keywords} == {
                "service_name": "amplify", "operation_name": "GetJob", "region_name": "us-east-1",
                "params": {"appId": app, "branchName": "main", "jobId": job}})
            require(not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)))
        checks.append("TWO_EXACT_SINGLE_GETJOB_MAPPINGS")

        schemas = [
            {"name": "aws___run_script", "inputSchema": {"type": "object", "required": ["code"],
                "properties": {"code": {"type": "string"}}}},
            {"name": "aws___list_regions", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "aws___get_regional_availability", "inputSchema": {"type": "object",
                "required": ["resource_type"], "properties": {
                    "regions": {"type": "array", "items": {"type": "string"}},
                    "resource_type": {"type": "string"},
                    "filters": {"type": "array", "items": {"type": "string"}}}}},
        ]
        catalog, _ = adapter._remote_tools(schemas, True)
        require(len(catalog) == 1 and catalog[0]["name"] == "read_workload_status")
        require(catalog[0]["inputSchema"] == {"type": "object",
            "required": ["operation", "app_id", "branch", "job_id"], "properties": {
                "operation": {"type": "string", "enum": ["amplify.GetJob"]},
                "app_id": {"type": "string", "enum": ["d2nxbpvv53qeqc", "d3s9n6ffim4722"]},
                "branch": {"type": "string", "enum": ["main"]},
                "job_id": {"type": "string", "enum": ["84", "60"]}},
            "additionalProperties": False})
        checks.append("ONE_TOOL_EXACT_ENUM_CATALOG")

        allowed = adapter._workload_arguments("tessera-ui")
        invalid = [{}, {"workload": "tessera-ui"}, {**allowed, "operation": "amplify.StartJob"},
                   {**allowed, "region": "us-west-2"}, {**allowed, "app_id": None},
                   {**allowed, "app_id": ["d2nxbpvv53qeqc"]}, {**allowed, "job_id": True},
                   {**allowed, "branch": "main "}]
        for args in invalid:
            require(not adapter._accepts(catalog[0]["inputSchema"], args))
            try:
                adapter._read_plan("read_workload_status", args, True)
            except ValueError:
                pass
            else:
                raise OfflineBoundaryError
        checks.append("INVALID_ARGUMENTS_REJECTED_BEFORE_TRANSPORT")
        mismatched = [{**allowed, "job_id": "60"}, {**allowed, "app_id": "d3s9n6ffim4722"}]
        for args in mismatched:
            require(adapter._accepts(catalog[0]["inputSchema"], args))
            require(adapter._workload_for(args) is None)
            try:
                adapter._read_plan("read_workload_status", args, True)
            except ValueError:
                pass
            else:
                raise OfflineBoundaryError
        invalid.extend(mismatched)
        checks.append("CROSS_PAIRED_TARGETS_PHYSICALLY_REJECTED")

        def native_result(workload=None):
            targets = adapter.PINNED_JOBS if workload is None else expected[workload]
            return {"status": "success", "result": {"jobs": [
                {"appId": app, "jobId": job, "status": "SUCCEED", "commitId": "offline-fixture"}
                for app, job in targets.items()]}, "api_calls": [
                {"service": "amplify", "operation": "GetJob", "status": "success"} for _ in targets]}

        class NativeResult(types.SimpleNamespace):
            def model_dump(self, **_kwargs):
                return vars(self)

        def envelope(value):
            return NativeResult(isError=False, content=[], structuredContent=value)

        calls, transports = [], []

        class Session:
            def __init__(self, *_args):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def initialize(self):
                return types.SimpleNamespace(protocolVersion=adapter.PROTOCOLS[0])

            async def list_tools(self):
                return types.SimpleNamespace(tools=schemas)

            async def call_tool(self, name, args, **_kwargs):
                calls.append((name, args))
                workload = next((key for key, script in adapter.WORKLOAD_SCRIPTS.items()
                                 if args == {"code": script}), None)
                require(name == "aws___run_script")
                require(workload is not None or args == {"code": adapter.READONLY_SCRIPT})
                return envelope(native_result(workload))

        @contextlib.asynccontextmanager
        async def transport(**kwargs):
            transports.append(1)
            require(kwargs["endpoint"] == adapter.AWS_ENDPOINT and kwargs["aws_region"] == "us-east-1")
            yield None, None, None

        mcp = types.ModuleType("mcp")
        mcp.ClientSession = Session
        client = types.ModuleType("mcp_proxy_for_aws.client")
        client.aws_iam_streamablehttp_client = transport
        guards.enter_context(patch.dict(sys.modules, {"mcp": mcp, "mcp_proxy_for_aws.client": client}))
        aws = adapter.AWSReadOnly(True)
        require(aws.discover() == catalog)
        entered = len(transports)
        for args in invalid:
            try:
                aws.call("read_workload_status", args)
            except ValueError:
                pass
            else:
                raise OfflineBoundaryError
        for name in ("aws___run_script", "aws___list_regions", "fixture__request_access_change"):
            try:
                aws.call(name, {})
            except ValueError:
                pass
            else:
                raise OfflineBoundaryError
        require(len(transports) == entered and not calls)
        checks.append("NATIVE_ENTRY_REJECTS_INVALID_AND_LEGACY_NAMES_BEFORE_TRANSPORT")
        for workload in expected:
            raw = aws.call("read_workload_status", adapter._workload_arguments(workload))
            safe = adapter._safe_result("read_workload_status", raw, workload)
            require(safe["execution"] == "read_only_complete" and safe["workload"] == workload)
            require(calls[-1] == ("aws___run_script", {"code": adapter.WORKLOAD_SCRIPTS[workload]}))
            for mutation in ("wrong_job", "wrong_app", "extra_job", "missing_receipt", "extra_receipt", "write_receipt"):
                bad = native_result(workload)
                if mutation == "wrong_job":
                    bad["result"]["jobs"][0]["jobId"] = "999"
                elif mutation == "wrong_app":
                    bad["result"]["jobs"][0]["appId"] = "outside"
                elif mutation == "extra_job":
                    bad["result"]["jobs"] *= 2
                elif mutation == "missing_receipt":
                    bad["api_calls"] = []
                elif mutation == "extra_receipt":
                    bad["api_calls"] *= 2
                else:
                    bad["api_calls"][0]["operation"] = "StartJob"
                require(adapter._safe_result("read_workload_status", envelope(bad), workload)["execution"] == "read_only_incomplete")
        checks.append("FAKE_NATIVE_TRANSPORT_AND_STRICT_RECEIPT_REJECTIONS")
        # Completed-task selection must bind jobs and receipt under the same task.
        task = {"task_id": "offline-task", "status": "completed", **native_result("tessera-ui")}
        task["status"] = "completed"
        wrapped = {"_cm_readiness_result": envelope({"tasks": [task]}), "_cm_expected_task_id": "offline-task"}
        require(adapter._safe_result("read_workload_status", wrapped, "tessera-ui")["execution"] == "read_only_complete")
        wrong = copy.deepcopy(wrapped)
        wrong["_cm_expected_task_id"] = "wrong-task"
        require(adapter._safe_result("read_workload_status", wrong, "tessera-ui")["execution"] == "read_only_incomplete")
        sibling = copy.deepcopy(wrapped)
        payload = sibling["_cm_readiness_result"].structuredContent
        payload["api_calls"] = payload["tasks"][0].pop("api_calls")
        require(adapter._safe_result("read_workload_status", sibling, "tessera-ui")["execution"] == "read_only_incomplete")
        checks.append("ASYNC_TASK_ID_AND_RECEIPT_SCOPE_BOUND")

        legacy, _ = adapter._remote_tools(schemas)
        require([tool["name"] for tool in legacy] == ["aws___run_script", "aws___list_regions",
                "aws___get_regional_availability", "fixture__request_access_change", "fixture__request_diagnostics_review"])
        require(adapter._safe_result("aws___run_script", adapter.AWSReadOnly().call(
            "aws___run_script", {"code": adapter.READONLY_SCRIPT}))["execution"] == "read_only_complete")
        require(adapter._safe_result("aws___run_script", envelope(native_result("tessera-ui")))["execution"] == "read_only_incomplete")
        checks.append("LEGACY_CATALOG_AND_TWO_RECEIPT_BOUNDARY_PRESERVED")

        class FakeServer:
            def __init__(self, _address, handler):
                self.handler = handler
                self.server_port = 0

        guards.enter_context(patch.object(adapter, "ThreadingHTTPServer", FakeServer))
        with tempfile.TemporaryDirectory(prefix="tessera-offline-workload-", dir="/dev/shm") as tmp:
            ram = Path(tmp)
            require(ram.stat().st_mode & 0o077 == 0)
            server = adapter.make_server(run_id="offline-workload", ledger_path=ram / "ledger.jsonl",
                                         token="public_offline_fixture_token_32chars", port=0, workload_mode=True)  # noqa: S106 - public fake, no listener
            handler = server.handler.__new__(server.handler)
            try:
                before = len(calls)
                for index, args in enumerate(invalid):
                    result, failed = handler.dispatch({"name": "read_workload_status", "arguments": args,
                        "_meta": {"run_id": "offline-workload", "request_id": f"invalid-{index}", "tool_use_id": f"use-{index}"}})
                    require(failed)
                require(len(calls) == before)
                for index, workload in enumerate(expected):
                    result, failed = handler.dispatch({"name": "read_workload_status", "arguments": adapter._workload_arguments(workload),
                        "_meta": {"run_id": "offline-workload", "request_id": f"valid-{index}", "tool_use_id": f"valid-use-{index}"}})
                    require(not failed and result["workload"] == workload)
                rows = server.ledger.snapshot()
                require(len(rows) == len(invalid) + 2)
                require(all(row["workload"] in {*expected, None} for row in rows))
                require(all(len(row["argument_digest"]) == 64 for row in rows))
                require(all({key: row[key] for key in allowed} == (
                    adapter._workload_arguments(row["workload"]) if row["workload"] else dict.fromkeys(allowed))
                    for row in rows))
                handler.connection = types.SimpleNamespace(settimeout=lambda _value: None)
                handler.auth = lambda: True
                handler.path = "/evidence"
                replies = []
                handler.reply = lambda status, value: replies.append((status, value))
                handler.do_GET()
                evidence = replies[0][1]
                require(replies[0][0] == 200 and set(evidence["workload_jobs"]) == set(expected))
                for workload, jobs in expected.items():
                    app, job = next(iter(jobs.items()))
                    require(evidence["workload_jobs"][workload] == {"appId": app, "jobId": job,
                            "status": "SUCCEED", "commitId": "offline-fixture"})
            finally:
                server.ledger.close()
        require(not ram.exists() and not attempts)
        checks.extend(["HANDLER_REJECTS_WITH_ZERO_NATIVE_CALLS", "NAMED_EVIDENCE_AND_FINITE_LEDGER",
                       "PRIVATE_RAM_REMOVED_AND_NETWORK_PROVIDER_MODEL_TRIPWIRES_CLEAR"])
    return {"schema": "tessera.offline_workload_boundary.v1", "status": "PASS",
            "checks": checks, "check_count": len(checks), "forbidden_attempts": len(attempts)}


def main():
    signal.signal(signal.SIGALRM, lambda *_args: (_ for _ in ()).throw(OfflineBoundaryError()))
    signal.alarm(20)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run()
    except BaseException:
        result = {"schema": "tessera.offline_workload_boundary.v1", "status": "FAIL",
                  "reason_code": "OFFLINE_WORKLOAD_BOUNDARY_FAILED"}
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
