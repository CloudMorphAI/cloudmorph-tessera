"""Owned watchdog and finite evidence preservation for one approved workload run.

The operator independently admits the fresh approval and claims. This controller
adds a durable launch claim, a 750s control bound plus two 5s stop waits, and
copies only finite nonsecret evidence before deleting its own private RAM tree.
It never retries a consumed or uncertain flow. --check cannot launch anything.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE.parents[2] / "workspace-support/audit/2026-10-18-cm095-resume"
PYTHON = "/home/sainath/tessera-demo-venv/bin/python"
OPERATOR_SHA = "63333a14eb6d6eee680ae760a2e73b6bd4b845609f198e44da12c2ed821abe21"
PRIMITIVES_SHA = "b70c5f59d876c625c9c942e31fafc651b701ae8a7f6e2f86384d92acf91f8b2f"


def load(name, path, expected):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1_048_576:
            raise ValueError("SOURCE_INVALID")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(1_048_577)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("SOURCE_INVALID")
    finally:
        os.close(descriptor)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102 -- exact pinned local bytes
    return module


def owned_ram(inputs):
    """Admit only this run's private tmpfs tree, independently of its contents."""
    ram = Path("/dev/shm") / ("cm095-workload1-" + inputs["testRunId"])  # noqa: S108 -- owned private tmpfs
    if not ram.exists() and not ram.is_symlink():
        return None
    if (ram.is_symlink() or ram.resolve() != ram or ram.parent != Path("/dev/shm")  # noqa: S108 -- exact bound
            or not any(row.split()[1:3] == [str(ram.parent), "tmpfs"]
                       for row in Path("/proc/mounts").read_text().splitlines())):
        raise ValueError("RAM_INVALID")
    info = ram.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or not ram.is_dir():
        raise ValueError("RAM_INVALID")
    return ram


def finite_evidence(operator, inputs):
    """Do not copy policy YAML, tenant identity, model text, logs or raw traces."""
    ram = owned_ram(inputs)
    if ram is None:
        return {"available": False}, None
    def read(name):
        return operator._json(operator._read(ram / name, limit=1_048_576))
    final, shutdown = read("finalization.json"), read("shutdown.json")
    if (set(final) != {"local_evidence_stored", "model_complete", "cloud_upload_acknowledged"}
            or not all(type(value) is bool for value in final.values())
            or shutdown.get("all_children_stopped") is not True
            or shutdown.get("shutdown_error_types") != []):
        raise ValueError("EVIDENCE_INCOMPLETE")
    evidence, ack = read("run-evidence.json"), read("cloud-ack.json")
    activation = evidence.get("active_policy", {})
    expected_run = "workload1-" + inputs["testRunId"]
    if (evidence.get("run_id") != expected_run or ack.get("stream_id") != expected_run
            or ack.get("verification") != "canonical_verified"
            or activation.get("verified") is not True
            or activation.get("policy_hashes", {}).get("workload-tessera-read") != operator._POLICY_SHA):
        raise ValueError("EVIDENCE_MISMATCH")
    from tessera.audit.canonical_json import canonical_json

    evidence_digest = hashlib.sha256(canonical_json(evidence)).hexdigest()
    if ack.get("run_evidence_digest") != evidence_digest or ack.get("run_evidence_stored") is not True:
        raise ValueError("EVIDENCE_MISMATCH")
    model = evidence.get("model", {})
    calls, traces = model.get("model_calls", []), evidence.get("tool_trace", [])
    if not isinstance(calls, list) or len(calls) > 6 or not isinstance(traces, list) or len(traces) > 4:
        raise ValueError("EVIDENCE_MISMATCH")
    verification = evidence.get("verification", {})
    outcomes = verification.get("outcome_checks", {})
    expected_outcomes = {"independent_model_completed", "allowed_target_uses_selected_hosted_policy",
                         "second_target_blocked_before_adapter", "one_named_aws_deployment_result_observed",
                         "answer_quotes_observed_deployment_fields", "denied_contacts_zero"}
    if (not isinstance(outcomes, dict) or set(outcomes) != expected_outcomes
            or not all(type(value) is bool for value in outcomes.values())):
        raise ValueError("EVIDENCE_MISMATCH")
    # All identifiers/digests below come from the fixed approved run and exact
    # acknowledged canonical projection. No free-form provider data is retained.
    result = {"available": True, "run_id": expected_run, **final,
              "all_children_stopped": True, "model_calls": len(calls), "tool_calls": len(traces),
              "model_complete": model.get("complete") is True,
              "usage_may_be_incomplete": model.get("usage_may_be_incomplete") is True,
              "accepted": verification.get("accepted") is True,
              "outcome_checks": outcomes, "denied_contacts": verification.get("denied_contacts"),
              "policy_id": "workload-tessera-read", "policy_sha256": operator._POLICY_SHA,
              "run_evidence_sha256": evidence_digest}
    if type(result["denied_contacts"]) is not int or not 0 <= result["denied_contacts"] <= 4:
        raise ValueError("EVIDENCE_MISMATCH")
    usage = model.get("usage")
    if (type(usage) is not dict or set(usage) != {"inputTokens", "outputTokens"}
            or type(usage["inputTokens"]) is not int or not 0 <= usage["inputTokens"] <= 144000
            or type(usage["outputTokens"]) is not int or not 0 <= usage["outputTokens"] <= 6144):
        raise ValueError("EVIDENCE_MISMATCH")
    cost = model.get("estimated_cost_usd")
    try:
        valid_cost = (cost is None and result["usage_may_be_incomplete"]
                      or type(cost) is str and len(cost) <= 40 and Decimal(cost).is_finite()
                      and 0 <= Decimal(cost) <= Decimal("0.01"))
    except InvalidOperation:
        valid_cost = False
    if not valid_cost:
        raise ValueError("EVIDENCE_MISMATCH")
    result.update(provenance="client_reported", usage=usage, estimated_cost_usd=cost)
    diagnostic = model.get("provider_diagnostic")
    if diagnostic is not None:
        if (type(diagnostic) is not dict
                or set(diagnostic) != {"exception_class", "http_status", "original_status", "request_id_sha256"}
                or type(diagnostic["exception_class"]) is not str
                or not re.fullmatch(r"[A-Za-z]{1,64}", diagnostic["exception_class"])
                or any(diagnostic[key] is not None
                       and (type(diagnostic[key]) is not int or not 100 <= diagnostic[key] <= 599)
                       for key in ("http_status", "original_status"))
                or diagnostic["request_id_sha256"] is not None
                and (type(diagnostic["request_id_sha256"]) is not str
                     or not re.fullmatch(r"[a-f0-9]{64}", diagnostic["request_id_sha256"]))):
            raise ValueError("EVIDENCE_MISMATCH")
        result["provider_diagnostic"] = diagnostic
    return result, ram


def ports_closed(callback_port):
    for port in (callback_port, 8766, 8767):
        if type(port) is not int or not 1 <= port <= 65535:
            return False  # No READY means callback closure cannot be proved.
        with socket.socket() as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return False
    return True


def execute(args, operator, primitives, inputs):
    operator.WorkloadOperator(args).check()  # Pure local admission before claim/Popen.
    for name in ("workload1-result.json", "workload1-watchdog.json", "workload1-evidence.json"):
        if (AUDIT / name).exists() or (AUDIT / name).is_symlink():
            raise ValueError("REPLAY_BLOCKED")
    primitives._save(AUDIT / "connection-launch-workload1.started.json", {
        "status": "LAUNCH_STARTED", "testRunId": inputs["testRunId"],
        "runInputsSha256": args["run_inputs_sha256"], "approvalSha256": args["approval_sha256"],
        "operatorSha256": OPERATOR_SHA, "at": int(time.time())})
    command = [PYTHON, "-B", str(HERE / "ephemeral_oauth_workload.py"), "--approved-workload",
               "--run-inputs", args["run_inputs"], "--run-inputs-sha256", args["run_inputs_sha256"],
               "--approval-sha256", args["approval_sha256"]]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TESSERA_")}
    environment.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    started, process, failure, final, ready = time.monotonic(), None, None, None, False
    callback_port = None
    selector, buffer = selectors.DefaultSelector(), b""
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,  # noqa: S603 -- fixed argv/no shell
                                   stderr=subprocess.DEVNULL, start_new_session=True, env=environment)
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() - started < 750:
            for key, _ in selector.select(timeout=1):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer += chunk
                if len(buffer) > 32768:
                    raise ValueError("OUTPUT_INVALID")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line or len(line) > 16384:
                        raise ValueError("OUTPUT_INVALID")
                    value = operator._json(line)
                    if value.get("status") == "READY" and not ready and final is None:
                        callback_port = primitives._ready(value)
                        ready = True
                        print(json.dumps(value), flush=True)  # Public URL, never persisted.
                    else:
                        expected = primitives.FINAL_KEYS | {"workload"}
                        if (final is not None or set(value) != expected
                                or value.get("schema") != "tessera.workload_connection_receipt.v1"
                                or value.get("testRunId") != inputs["testRunId"]
                                or value.get("runLabel") != "workload1" or value.get("connectionUse") is not None):
                            raise ValueError("OUTPUT_INVALID")
                        final = value
                        primitives._save(AUDIT / "workload1-result.json", value)
            if process.poll() is not None and not selector.get_map():
                break
        if process.poll() is None:
            failure = "EXTERNAL_750S_DEADLINE"
        elif final is None:
            failure = "FINAL_RECEIPT_MISSING"
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
    stopped = process is None or process.poll() is not None
    closed, preserved, removed = ports_closed(callback_port) if stopped else False, False, False
    if stopped and closed:
        try:
            ram = owned_ram(inputs)
            try:
                try:
                    evidence, _ = finite_evidence(operator, inputs)
                except BaseException:
                    failure = failure or "RAM_EVIDENCE_UNPROVED"
                    evidence = {"available": False, "failure": "EVIDENCE_PROJECTION_FAILED"}
                primitives._save(AUDIT / "workload1-evidence.json", evidence)
                preserved = evidence["available"]
            finally:
                if ram is not None:
                    # Revalidate immediately before removal, including failed preservation.
                    if owned_ram(inputs) != ram:
                        raise ValueError("RAM_INVALID")
                    shutil.rmtree(ram)  # exact resolved private owned tmpfs tree verified above
                    removed = not ram.exists()
                else:
                    removed = True  # Exact owned tree was never created or already removed.
        except BaseException:
            failure = failure or "RAM_EVIDENCE_OR_CLEANUP_UNPROVED"
    receipt = {"schema": "tessera.workload_watchdog.v1", "testRunId": inputs["testRunId"],
               "elapsedSeconds": round(time.monotonic() - started, 3),
               "ownedProcessStopped": stopped, "portsClosed": closed,
               "processExitCode": None if process is None else process.returncode,
               "finalReceiptPersisted": final is not None, "finiteEvidencePreserved": preserved,
               "privateRamRemoved": removed, "failure": failure, "automaticReplay": False}
    primitives._save(AUDIT / "workload1-watchdog.json", receipt)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0 if final and stopped and closed and preserved and removed and failure is None and process.returncode == 0 else 1


def main():
    launch_attempted = False
    try:
        primitives = load("_workload_watchdog_primitives", HERE / "run_ephemeral_oauth_connection.py", PRIMITIVES_SHA)
        operator = load("_workload_reviewed_operator", HERE / "ephemeral_oauth_workload.py", OPERATOR_SHA)
        check, args = operator._args(sys.argv[1:])
        current = operator.WorkloadOperator(args)
        checked = current.check()
        if check:
            print(json.dumps(checked, sort_keys=True), flush=True)
            return 0
        signal.signal(signal.SIGTERM, primitives._stopped)
        signal.signal(signal.SIGINT, primitives._stopped)
        launch_attempted = True
        return execute(args, operator, primitives, current.inputs)
    except BaseException:
        print(json.dumps({"status": "BLOCKED", "errorCode": "WORKLOAD_LAUNCH_BLOCKED",
                          "executionPerformed": "UNKNOWN" if launch_attempted else False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
