"""Offline preparation check or one approved connection under an owned watchdog.

No credentials or private manifest are read here. The operator performs fresh
cloud checks only after its independent approval guard. Preparation is not
authorization. A launch marker is never removed or retried automatically.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import datetime
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path("/mnt/c/Users/found/Desktop/CloudMorph")
AUDIT = ROOT / "workspace-support/audit/2026-10-18-cm095-resume"
BASE = ROOT / "cloudmorph-tessera/examples/control_layer_demo"
PYTHON = "/home/sainath/tessera-demo-venv/bin/python"
HASH = re.compile(r"[a-f0-9]{64}")
RUN_KEYS = {"schema", "runLabel", "testRunId", "account", "region", "client", "scope", "planSha256",
            "seedManifestSha256", "receiverSha256", "acceptanceUseSha256", "consumerCoreManifestSha256"}
APPROVAL_KEYS = {"schema", "approvalStatus", "runLabel", "testRunId", "runInputsSha256", "operatorSha256",
                 "client", "scope", "planSha256", "noOrdinaryDiskTokenCache", "residualAccessAcknowledged",
                 "unknownExchangeNoRetry", "decision", "approvedAtUTC"}
FINAL_KEYS = {
    "schema", "testRunId", "runLabel", "account", "region", "tableArn", "client", "scope",
    "mintOutcome", "cleanupOutcome", "updateAttemptCount", "confirmedRevoked", "confirmedRevokedAt",
    "compoundOriginalRowCommitmentSha256", "cleanupErrorCode", "originalCreatedAt", "originalExpiresAt",
    "revokeOperationPlannedEpoch", "accessExpiresAt", "accessLifetimeVerified", "normalFlowGeneratedCodeConsumed",
    "accessRevoked", "status", "errorCode", "connectionUse", "sourcePins",
}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("ARGUMENTS_INVALID")


class _StopRequestedError(Exception):
    """A controller stop must still enter the owned-child cleanup path."""


def _stopped(_signum: int, _frame: object) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise _StopRequestedError("STOP_REQUESTED")


def _require(condition: object) -> None:
    if not condition:
        raise ValueError("PREPARATION_BLOCKED")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _pinned(path: Path, expected: str, limit: int = 1_048_576) -> bytes:
    _require(HASH.fullmatch(expected))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        _require(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == expected)
        return raw
    finally:
        os.close(descriptor)


def _save(path: Path, value: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii"))
            stream.flush()
            os.fsync(descriptor)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(descriptor)


def _prepare(args: argparse.Namespace) -> dict:
    _require(sys.platform == "linux")
    path = Path(args.run_inputs)
    _require(path == AUDIT / "connection-attempt2-inputs.json")
    inputs = json.loads(_pinned(path, args.run_inputs_sha256), object_pairs_hook=_pairs)
    _require(type(inputs) is dict and set(inputs) == RUN_KEYS
             and inputs.get("schema") == "tessera.single_connection_run_inputs.v1"
             and inputs.get("runLabel") == "attempt2")
    _require(str(uuid.UUID(inputs["testRunId"], version=4)) == inputs["testRunId"])
    _require(inputs.get("client") == "tessera-cli"
             and inputs.get("scope") == "tessera:policies:read tessera:audit:write")
    source = _pinned(BASE / "ephemeral_oauth_connection.py", args.operator_sha256)
    names = {"_ACCOUNT", "_REGION", "_SCOPE", "_PLAN_SHA", "_SEED_SHA", "_RECEIVER_SHA", "_USE_SHA", "_CORE_SHA",
             "_CORE_PINS"}
    constants = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in names:
                constants[name] = ast.literal_eval(node.value)
    _require(set(constants) == names)
    for field, constant in {"account": "_ACCOUNT", "region": "_REGION", "scope": "_SCOPE",
                            "planSha256": "_PLAN_SHA", "seedManifestSha256": "_SEED_SHA",
                            "receiverSha256": "_RECEIVER_SHA", "acceptanceUseSha256": "_USE_SHA",
                            "consumerCoreManifestSha256": "_CORE_SHA"}.items():
        _require(inputs[field] == constants[constant])
    for relative, expected in constants["_CORE_PINS"].items():
        _require(not Path(relative).is_absolute() and ".." not in Path(relative).parts)
        _pinned(ROOT / "cloudmorph-tessera" / relative, expected)
    _pinned(BASE / "ephemeral_oauth_receiver.py", inputs["receiverSha256"])
    _pinned(BASE / "ephemeral_oauth_acceptance_use.py", inputs["acceptanceUseSha256"])
    for name in ["connection-launch-attempt2.started.json", "connection-flow-attempt2.started.json",
                 "connection-exchange-attempt2.started.json", "connection-attempt2-result.json",
                 "connection-attempt2-watchdog.json"]:
        _require(not (AUDIT / name).exists() and not (AUDIT / name).is_symlink())
    return inputs


def _ready(value: dict) -> int:
    _require(set(value) == {"status", "authorizationURL"} and value["status"] == "READY")
    parsed = urlsplit(value["authorizationURL"])
    _require(parsed.scheme == "https" and parsed.netloc == "auth.tessera.cloudmorph.ai"
             and parsed.path == "/oauth/authorize" and not parsed.fragment)
    query = parse_qs(parsed.query, strict_parsing=True)
    _require(set(query) == {"response_type", "client_id", "redirect_uri", "scope", "state",
                            "code_challenge", "code_challenge_method"}
             and all(len(values) == 1 for values in query.values()))
    _require(query["client_id"] == ["tessera-cli"] and query["response_type"] == ["code"]
             and query["scope"] == ["tessera:policies:read tessera:audit:write"]
             and query["code_challenge_method"] == ["S256"])
    redirect = urlsplit(query["redirect_uri"][0])
    _require(redirect.scheme == "http" and redirect.hostname == "127.0.0.1" and redirect.port
             and redirect.path == "/callback" and not redirect.query and not redirect.fragment)
    _require(all(re.fullmatch(r"[A-Za-z0-9_-]{43}", query[key][0])
                 for key in ["state", "code_challenge"]))
    return redirect.port


def _final(value: dict, inputs: dict) -> None:
    # All nested data is validated by the exact hash-reviewed operator before
    # output. Reject an unexpected output envelope instead of printing it.
    _require(set(value) == FINAL_KEYS and value.get("schema") == "tessera.single_connection_receipt.v2"
             and value.get("testRunId") == inputs["testRunId"] and value.get("runLabel") == "attempt2"
             and value.get("client") == inputs["client"] and value.get("scope") == inputs["scope"])


def _execute(args: argparse.Namespace, inputs: dict) -> int:
    _require(args.approved_single_connection and args.approval_sha256 is not None)
    approval = json.loads(_pinned(AUDIT / "single-connection-attempt2-founder-approval.json",
                                  args.approval_sha256), object_pairs_hook=_pairs)
    _require(type(approval) is dict and set(approval) == APPROVAL_KEYS
             and approval.get("schema") == "tessera.single_connection_approval.v2"
             and approval.get("approvalStatus") == "APPROVED" and approval.get("runLabel") == "attempt2"
             and approval.get("runInputsSha256") == args.run_inputs_sha256
             and approval.get("operatorSha256") == args.operator_sha256
             and approval.get("testRunId") == inputs["testRunId"]
             and approval.get("client") == inputs["client"] and approval.get("scope") == inputs["scope"]
             and approval.get("planSha256") == inputs["planSha256"] and approval.get("decision") == "Approve one test"
             and all(approval.get(key) is True for key in ["noOrdinaryDiskTokenCache", "residualAccessAcknowledged",
                                                          "unknownExchangeNoRetry"]))
    timestamp = approval.get("approvedAtUTC")
    _require(type(timestamp) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp))
    datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    # This durable additional claim prevents two launchers racing to save one
    # result. The operator independently verifies the full approval and claims.
    _save(AUDIT / "connection-launch-attempt2.started.json", {
        "status": "LAUNCH_STARTED", "testRunId": inputs["testRunId"],
        "runInputsSha256": args.run_inputs_sha256, "approvalSha256": args.approval_sha256,
        "operatorSha256": args.operator_sha256, "at": int(time.time()),
    })
    command = [PYTHON, "-B", str(BASE / "ephemeral_oauth_connection.py"), "--approved-single-connection",
               "--run-inputs", args.run_inputs, "--run-inputs-sha256", args.run_inputs_sha256,
               "--approval-sha256", args.approval_sha256]
    environment = os.environ.copy()
    environment.pop("TESSERA_DEBUG", None)
    environment.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    process = None
    selector = selectors.DefaultSelector()
    buffer = b""
    ready = final = False
    failure = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,  # noqa: S603 - fixed reviewed interpreter/script, no shell
                                   stderr=subprocess.DEVNULL, start_new_session=True, env=environment)
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() - started < 390:
            for key, _ in selector.select(timeout=1):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer += chunk
                _require(len(buffer) <= 32_768)
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    _require(len(line) <= 16_384)
                    if not line:
                        continue
                    value = json.loads(line, object_pairs_hook=_pairs)
                    _require(type(value) is dict)
                    if value.get("status") == "READY" and not ready:
                        _ready(value)
                        ready = True
                        print(json.dumps(value), flush=True)  # Public initial URL only; never persisted.
                    else:
                        _require(not final)
                        _final(value, inputs)
                        _save(AUDIT / "connection-attempt2-result.json", value)
                        final = True
                        print(json.dumps(value, sort_keys=True), flush=True)
            if process.poll() is not None and not selector.get_map():
                break
        if process.poll() is None:
            failure = "EXTERNAL_390S_DEADLINE"
        elif not final:
            failure = "FINAL_RECEIPT_MISSING"
    except _StopRequestedError:
        failure = "CONTROLLER_STOP_REQUESTED"
    except BaseException:
        failure = "WATCHDOG_CONTROL_OR_OUTPUT_FAILURE"
    finally:
        try:
            if process is not None and process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        failure = "OWNED_PROCESS_STOP_UNKNOWN"
        except OSError:
            failure = "OWNED_PROCESS_STOP_UNKNOWN"
        selector.close()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        receipt = {"schema": "tessera.owned_watchdog.v2", "testRunId": inputs["testRunId"],
                   "operatorSha256": args.operator_sha256, "elapsedSeconds": round(time.monotonic() - started, 3),
                   "processExitCode": None if process is None else process.returncode,
                   "ownedProcessStopped": process is None or process.poll() is not None,
                   "readyObserved": ready, "finalReceiptPersisted": final, "failure": failure,
                   "automaticReplay": False}
        _save(AUDIT / "connection-attempt2-watchdog.json", receipt)
        print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0 if final and failure is None and process is not None and process.returncode == 0 else 1


def main() -> int:
    previous_term = signal.signal(signal.SIGTERM, _stopped)
    previous_int = signal.getsignal(signal.SIGINT)
    parser = _Parser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--approved-single-connection", action="store_true")
    parser.add_argument("--run-inputs", required=True)
    parser.add_argument("--run-inputs-sha256", required=True)
    parser.add_argument("--operator-sha256", required=True)
    parser.add_argument("--approval-sha256")
    try:
        args = parser.parse_args()
        _require(args.check != args.approved_single_connection)
        inputs = _prepare(args)
        if args.check:
            _require(args.approval_sha256 is None)
            print(json.dumps({"status": "PASS_PREPARATION_APPROVAL_REQUIRED", "schema": "tessera.offline_preparation.v1",
                              "runLabel": "attempt2", "testRunId": inputs["testRunId"], "cloudCalls": 0,
                              "liveAuthorization": False, "runInputsSha256": args.run_inputs_sha256,
                              "operatorSha256": args.operator_sha256}), flush=True)
            return 0
        return _execute(args, inputs)
    except BaseException:
        print('{"status":"BLOCKED","errorCode":"PREPARATION_OR_APPROVAL_FAILED"}', flush=True)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


if __name__ == "__main__":
    raise SystemExit(main())
