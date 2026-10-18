"""One-command offline run-input/approval/replay verifier; never runs OAuth.

Uses only isolated, automatically removed /dev/shm fixtures and network-denying
spies. It imports adjacent operator definitions without executing its CLI. All
preflight calls below stop at local guard failures: actual preflight is never run.
No real approval/claim file, private seed, provider, listener or token is accessed.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch


class _Checks:
    def __init__(self) -> None:
        self.count = 0
        self.guard_preflight_calls = 0
        self.calls = {"sdk": 0, "http": 0, "network": 0, "unexpectedRead": 0}

    def ok(self, condition: object) -> None:
        if not condition:
            raise AssertionError("OFFLINE_CHECK_FAILED")
        self.count += 1

    def reject(self, module, action, code: str, *, symlink: bool = False) -> None:
        try:
            action()
        except module._OperatorError as error:
            if error.code != code or symlink:
                raise AssertionError("OFFLINE_REJECTION_CODE_MISMATCH") from None
        except OSError as error:
            if not symlink or error.errno != errno.ELOOP:
                raise AssertionError("OFFLINE_UNEXPECTED_IO_ERROR") from None
        else:
            raise AssertionError("OFFLINE_EXPECTED_REJECTION")
        self.count += 1

    def deny(self, kind: str):
        def call(*args, **kwargs):
            self.calls[kind] += 1
            raise AssertionError("OFFLINE_EXTERNAL_ACTION_FORBIDDEN")
        return call

    def preflight(self, module, args) -> None:
        self.guard_preflight_calls += 1
        module._Operator(args).preflight()


def _write(path: Path, value: object) -> str:
    raw = value if type(value) is bytes else json.dumps(value, separators=(",", ":")).encode("ascii")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def verify() -> dict:
    sys.dont_write_bytecode = True
    filename = Path(__file__).with_name("ephemeral_oauth_connection.py")
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 131072:
            raise ValueError("OFFLINE_SOURCE_INVALID")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(131073)
    finally:
        os.close(descriptor)
    source_sha = hashlib.sha256(raw).hexdigest()
    module = types.ModuleType("_ephemeral_oauth_operator_offline")
    module.__file__ = str(filename)
    sys.modules[module.__name__] = module
    exec(compile(raw, str(filename), "exec"), module.__dict__)  # noqa: S102 - adjacent reviewed code, CLI disabled
    checks = _Checks()
    run_id = "abcdefab-cdef-4abc-8def-abcdefabcdef"
    with (
        tempfile.TemporaryDirectory(prefix="cm095-inert-runinputs-", dir="/dev/shm") as directory,
        patch.object(module.boto3, "Session", checks.deny("sdk")),
        patch.object(module.httpx, "Client", checks.deny("http")),
        patch.object(socket, "create_connection", checks.deny("network")),
        patch.object(socket, "socket", checks.deny("network")),
    ):
        module._AUDIT = Path(directory)
        input_path = module._AUDIT / "connection-attempt2-inputs.json"
        approval_path = module._AUDIT / "single-connection-attempt2-founder-approval.json"
        original_read = module._read_pinned

        def limited_read(path: Path, expected: str, limit: int = 1048576) -> bytes:
            if path not in {input_path, approval_path, filename}:
                checks.calls["unexpectedRead"] += 1
                raise AssertionError("OFFLINE_GUARD_ESCAPED_TO_REAL_PREFLIGHT")
            return original_read(path, expected, limit)

        module._read_pinned = limited_read
        base = {"schema": "tessera.single_connection_run_inputs.v1", "runLabel": "attempt2", "testRunId": run_id,
                "account": module._ACCOUNT, "region": module._REGION, "client": "tessera-cli", "scope": module._SCOPE,
                "planSha256": module._PLAN_SHA, "seedManifestSha256": module._SEED_SHA,
                "receiverSha256": module._RECEIVER_SHA, "acceptanceUseSha256": module._USE_SHA,
                "consumerCoreManifestSha256": module._CORE_SHA}

        def setup(inputs=None, approval_update=None, approval_replace=None) -> dict:
            inputs_sha = _write(input_path, copy.deepcopy(base if inputs is None else inputs))
            approval = {"schema": "tessera.single_connection_approval.v2", "approvalStatus": "APPROVED",
                        "runLabel": "attempt2", "testRunId": run_id, "runInputsSha256": inputs_sha,
                        "operatorSha256": source_sha, "client": "tessera-cli", "scope": module._SCOPE,
                        "planSha256": module._PLAN_SHA, "noOrdinaryDiskTokenCache": True,
                        "residualAccessAcknowledged": True, "unknownExchangeNoRetry": True,
                        "decision": "Approve one test", "approvedAtUTC": "2026-10-18T11:00:00Z"}
            if approval_update:
                approval.update(approval_update)
            if approval_replace is not None:
                approval = approval_replace
            approval_sha = _write(approval_path, approval)
            return {"run_inputs": str(input_path), "run_inputs_sha256": inputs_sha, "approval_sha256": approval_sha}

        for args in [None, {}, {"run_inputs": "old"}]:
            checks.reject(module, lambda args=args: checks.preflight(module, args), "RUN_INPUTS_REQUIRED")
        args = setup()
        operator = module._Operator(args)
        operator.validate_run_inputs()
        checks.ok(operator.run_validated and operator.receipt["testRunId"] == run_id and operator.claim_names() == (
            "connection-flow-attempt2.started.json", "connection-exchange-attempt2.started.json"))
        for key in ["account", "region", "client", "scope", "planSha256", "seedManifestSha256", "receiverSha256",
                    "acceptanceUseSha256", "consumerCoreManifestSha256", "schema"]:
            bad = {**base, key: "INVALID"}
            args = setup(bad)
            checks.reject(module, lambda args=args: checks.preflight(module, args), "RUN_INPUTS_INVALID")
        for key, value in [("runLabel", "attempt1"), ("runLabel", "attempt20"), ("testRunId", run_id.upper()),
                           ("testRunId", "abcdefab-cdef-1abc-8def-abcdefabcdef"), ("testRunId", True), ("extra", "UNKNOWN")]:
            args = setup({**base, key: value})
            checks.reject(module, lambda args=args: checks.preflight(module, args), "RUN_INPUTS_INVALID")
        for change in [{"schema": "tessera.single_connection_approval.v1"}, {"approvalStatus": "PENDING"},
                       {"runLabel": "attempt1"}, {"testRunId": "abcdefab-cdef-4abc-8def-abcdefabcdea"},
                       {"runInputsSha256": "0" * 64}, {"operatorSha256": "0" * 64}, {"decision": "anything"},
                       {"noOrdinaryDiskTokenCache": 1}, {"residualAccessAcknowledged": False},
                       {"unknownExchangeNoRetry": False}, {"approvedAtUTC": "2026-02-30T11:00:00Z"},
                       {"approvedAtUTC": "2026-10-18T11:00:00+00:00"}, {"extra": "UNKNOWN"}]:
            args = setup(approval_update=change)
            code = "SOURCE_PIN_MISMATCH" if "operatorSha256" in change else "RUN_APPROVAL_INVALID"
            checks.reject(module, lambda args=args: checks.preflight(module, args), code)
        args = setup(approval_replace={"approvalStatus": "APPROVED", "noOrdinaryDiskTokenCache": True})
        checks.reject(module, lambda: checks.preflight(module, args), "RUN_APPROVAL_INVALID")
        for key in ["run_inputs_sha256", "approval_sha256"]:
            args = setup()
            args[key] = "0" * 64
            checks.reject(module, lambda args=args: checks.preflight(module, args), "SOURCE_PIN_MISMATCH")
        for path in [str(module._AUDIT / "sub/connection-attempt2-inputs.json"),
                     str(module._AUDIT / "../connection-attempt2-inputs.json"),
                     str(module._AUDIT / "connection-attempt1-inputs.json"), "relative.json"]:
            args = setup()
            args["run_inputs"] = path
            checks.reject(module, lambda args=args: checks.preflight(module, args), "RUN_INPUTS_INVALID")
        args = setup()
        duplicate = json.dumps(base)[:-1] + ',"testRunId":"' + run_id + '"}'
        args["run_inputs_sha256"] = _write(input_path, duplicate.encode("ascii"))
        checks.reject(module, lambda: checks.preflight(module, args), "PRIVATE_INPUT_INVALID")
        args = setup()
        input_path.unlink()
        input_path.symlink_to(approval_path)
        checks.reject(module, lambda: checks.preflight(module, args), "", symlink=True)
        input_path.unlink()
        args = setup()
        approval_raw = approval_path.read_bytes()
        approval_path.unlink()
        target = module._AUDIT / "approval-inert.json"
        target.write_bytes(approval_raw)
        approval_path.symlink_to(target)
        checks.reject(module, lambda: checks.preflight(module, args), "", symlink=True)
        approval_path.unlink()
        attempt1 = module._AUDIT / "connection-flow-attempt1.started.json"
        attempt1.write_bytes(b"UNCHANGED_INERT_ATTEMPT1")
        old = attempt1.read_bytes()
        args = setup()
        operator = module._Operator(args)
        operator.validate_run_inputs()
        operator.claim_once(exchange=False)
        marker = json.loads((module._AUDIT / operator.claim_names()[0]).read_text())
        checks.ok(marker["runLabel"] == "attempt2" and marker["runInputsSha256"] == args["run_inputs_sha256"]
                  and marker["approvalSha256"] == args["approval_sha256"])
        checks.reject(module, lambda: operator.claim_once(exchange=False), "EXCHANGE_CLAIM_EXISTS")
        operator.claim_once(exchange=True)
        checks.reject(module, lambda: operator.claim_once(exchange=True), "EXCHANGE_CLAIM_EXISTS")
        checks.ok(attempt1.read_bytes() == old)
        checks.reject(module, lambda: checks.preflight(module, args), "EXCHANGE_CLAIM_EXISTS")
        for argv in [[], ["--approved-single-connection"],
                     ["--approved-single-connection", "--run-inputs", "relative", "--run-inputs-sha256", "0" * 64,
                      "--approval-sha256", "0" * 64],
                     ["--approved-single-connection", "--run-inputs", str(input_path), "--run-inputs-sha256", "bad",
                      "--approval-sha256", "0" * 64]]:
            checks.reject(module, lambda argv=argv: module._cli_run_args(argv), "RUN_INPUTS_ARGUMENTS_INVALID")
        parsed = module._cli_run_args(["--approved-single-connection", "--run-inputs", str(input_path),
                                       "--run-inputs-sha256", "0" * 64, "--approval-sha256", "0" * 64])
        checks.ok(parsed["run_inputs"] == str(input_path))
        _verify_diagnostics(module, checks)
        checks.ok(checks.calls == {"sdk": 0, "http": 0, "network": 0, "unexpectedRead": 0})
    sys.modules.pop(module.__name__, None)
    return {"status": "PASS", "checks": checks.count, "sdkCalls": 0, "httpClientCalls": 0, "networkCalls": 0,
            "actualPreflightCalls": 0, "localGuardPreflightCalls": checks.guard_preflight_calls,
            "realClaimsTouched": False, "realApprovalTouched": False, "fixtures": "ISOLATED_RAM_REMOVED",
            "sourceSha256": source_sha}


def _verify_diagnostics(module, checks: _Checks) -> None:
    base = {"method": "GET", "phase": "POLICY_JSON", "started": True, "headers_received": True,
            "body_complete": True, "elapsed_ms": 7, "http_status": 200, "exception_class": None,
            "request_id_sha256": {"apigw-requestid": "a" * 64}}
    for records in [[], [base], [base, {**base, "method": "POST", "phase": "AUDIT_JSON"}],
                    [{**base, "http_status": 201}, {**base, "method": "POST", "phase": "AUDIT_JSON"}],
                    [{**base, "phase": "POLICY_HTTP", "headers_received": False, "body_complete": False,
                      "http_status": None, "exception_class": "ReadTimeout", "request_id_sha256": {}}]]:
        module._safe_diagnostics(copy.deepcopy(records))
        checks.ok(True)
    for changes in [{"method": "POST"}, {"phase": "UNKNOWN"}, {"extra": "NEVER_EXPORT"},
                    {"exception_class": "PrivateDynamicError"}, {"elapsed_ms": True}, {"elapsed_ms": 30001},
                    {"elapsed_ms": -1}, {"http_status": True}, {"http_status": 600}, {"headers_received": False},
                    {"started": False}, {"request_id_sha256": {"authorization": "a" * 64}},
                    {"request_id_sha256": {"apigw-requestid": "A" * 64}}, {"request_id_sha256": {"x-amzn-requestid": 42}}]:
        checks.reject(module, lambda changes=changes: module._safe_diagnostics([{**base, **changes}]), "CONSUMER_INVALID")
    for invalid in [[base, base, base], [None], {}, [{**base, "body_complete": False}, {**base, "method": "POST"}]]:
        checks.reject(module, lambda invalid=invalid: module._safe_diagnostics(invalid), "CONSUMER_INVALID")
    value = {"schema": "tessera.scripted_connection_test.v2", "status": "BLOCKED", "reason_code": "NOT_STARTED",
             "snapshot_digest": None, "policy_count": None, "ack_written": None, "stream_id": None, "chain_head": None,
             "label": "SCRIPTED CONNECTION TEST", "proxy_runtime_active": False, "independent_agent_observed": False,
             "diagnostics": []}
    checks.ok(module._safe_consumer(value) == value)
    for changes in [{"schema": "tessera.scripted_connection_test.v1"}, {"diagnostics": {}},
                    {"extra": "NEVER_EXPORT"}, {"reason_code": "UNKNOWN_SECRET_TEXT"}]:
        checks.reject(module, lambda changes=changes: module._safe_consumer({**value, **changes}), "CONSUMER_INVALID")


if __name__ == "__main__":
    if sys.argv[1:]:
        print('{"status":"FAIL","code":"NO_ARGUMENTS_ALLOWED","actualPreflightCalls":0}', flush=True)
        raise SystemExit(2)
    try:
        result = verify()
    except BaseException:
        print('{"status":"FAIL","code":"OFFLINE_GUARD_CHECK_FAILED","actualPreflightCalls":0}', flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True), flush=True)
