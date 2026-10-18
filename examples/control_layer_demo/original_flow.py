"""Scripted Hermes -> canonical Tessera -> one bounded AWS regions read.

Two explicitly staged operator tasks: read then inert access denial; separately
admitted inert review stop. No reasoning model, login, policy writer or refresh.
The top-level reviewed operator owns fresh OAuth, admission, absolute watchdog,
exact-row revocation and copying safe evidence before deleting private RAM.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import re
import secrets
import selectors
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[1]
ENDPOINT = "https://auth.tessera.cloudmorph.ai"
POLICY_ID = "readiness-diagnostic-review"
POLICY_SHA256 = "772a7f7db2b018839153e74d094f7605d793944ee10335c09baa4561b7895112"
HERMES_PYTHON = Path("/tmp/nakul-cm104-hermes-venv-o2wxq6ao/venv/bin/python")  # noqa: S108 -- fixed isolated environment
MAX_SECONDS = 240
SOURCE_NAMES = frozenset({"original_flow.py", "original_flow_hermes_child.py", "hermes_client.py",
                          "orchestrator.py", "aws_readiness_server.py", "readiness-review.yaml"})
TASK_PROBES = {
    "read-and-deny": [("aws___list_regions", {}),
                      ("fixture__request_access_change", {"request": "production_access_change"})],
    "review-stop": [("fixture__request_diagnostics_review", {"request": "read_only_diagnostics_review"})],
}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}\Z")


class OriginalFlowError(Exception):
    """Fixed reason only; callers must not render raw private exception text."""
    def __init__(self, code: str):
        safe = {"ADMISSION_REQUIRED", "SOURCE_NOT_AUTHENTICATED", "INPUT_REJECTED", "SOURCE_PIN_MISMATCH",
                "RAM_REQUIRED", "DEADLINE", "HERMES_TASK_FAILED", "HERMES_OUTPUT_REJECTED",
                "ACTIVATION_REJECTED", "OUTCOME_REJECTED", "ACK_REJECTED", "CLEANUP_REQUIRED", "FLOW_FAILED"}
        self.code = code if code in safe else "INPUT_REJECTED"
        super().__init__(self.code)


def _need(value, code="INPUT_REJECTED"):
    if not value:
        raise OriginalFlowError(code)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _need(key not in value)
        value[key] = item
    return value


def _json(raw: bytes):
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _need(False))
    _need(type(value) is dict)
    return value


def _read(path: Path, expected=None, limit=1_048_576):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        _need(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        _need(len(raw) <= limit and (expected is None or hashlib.sha256(raw).hexdigest() == expected), "SOURCE_PIN_MISMATCH")
        return raw
    finally:
        os.close(descriptor)


def _remaining(deadline, reserve=0):
    _need(time.monotonic() + reserve < deadline, "DEADLINE")


def validate_admission(admission, *, runtime_admitted):
    _need(runtime_admitted is True, "ADMISSION_REQUIRED")
    keys = {"schema", "sourcePins", "hermesPython", "hermesSource", "hermesFullSourceReceiptSha256",
            "hermesDependencyReceiptSha256", "hermesFullSourceAuthenticated", "hermesDependencyReviewed"}
    _need(type(admission) is dict and set(admission) == keys
          and admission["schema"] == "tessera.original_flow_admission.v1")
    # These facts come from separately reviewed, approval-bound receipts. This
    # module does not mint them or substitute seven critical pins for full source.
    _need(admission["hermesFullSourceAuthenticated"] is True and admission["hermesDependencyReviewed"] is True,
          "SOURCE_NOT_AUTHENTICATED")
    _need(all(type(admission[k]) is str and re.fullmatch(r"[a-f0-9]{64}", admission[k])
              for k in ("hermesFullSourceReceiptSha256", "hermesDependencyReceiptSha256")))
    pins = admission["sourcePins"]
    _need(type(pins) is dict and set(pins) == SOURCE_NAMES
          and all(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) for value in pins.values()))
    for name, expected in pins.items():
        _read(HERE / name, expected)
    _need(pins["readiness-review.yaml"] == POLICY_SHA256, "SOURCE_PIN_MISMATCH")
    _need(admission["hermesPython"] == str(HERMES_PYTHON))
    source = Path(admission["hermesSource"])
    _need(source.is_absolute() and source.resolve(strict=True).is_relative_to(Path("/dev/shm")), "RAM_REQUIRED")  # noqa: S108 -- required private RAM
    _need(source.is_dir() and not source.is_symlink() and stat.S_IMODE(source.stat().st_mode) == 0o700
          and source.stat().st_uid == os.getuid(), "RAM_REQUIRED")
    return source


def _load_orchestrator(expected):
    path = HERE / "orchestrator.py"
    source = _read(path, expected)
    # Parent imports only source-pinned runtime; Hermes remains in fresh children.
    for root in (RUNTIME, HERE):
        sys.path.insert(0, str(root))
    prior = sys.modules.get("orchestrator")
    if prior is not None:
        _need(Path(prior.__file__).resolve() == path, "SOURCE_PIN_MISMATCH")
    spec = importlib.util.spec_from_file_location("_original_orchestrator_helpers", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 -- exact reviewed helper bytes
    return module


def _child(*, task, bearer, run_id, admission, output, deadline):
    """Bounded private IPC; never argv/env/log the loopback bearer."""
    _remaining(deadline, 10)
    home = output / ("hermes-" + task)
    home.mkdir(mode=0o700)
    temporary = home / "tmp"
    temporary.mkdir(mode=0o700)
    env = {"PATH": str(HERMES_PYTHON.parent) + ":/usr/bin:/bin", "HOME": str(home),
           "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary),
           "XDG_CACHE_HOME": str(home / "cache"), "XDG_CONFIG_HOME": str(home / "config"),
           "XDG_STATE_HOME": str(home / "state"), "PYTHONDONTWRITEBYTECODE": "1",
           "CM095_HERMES_CONNECTOR_SHA256": admission["sourcePins"]["hermes_client.py"]}
    # No inherited credentials, provider/plugin settings or external HTTP proxy.
    request = {"schema": "tessera.original_hermes_task.v1", "task": task, "source": admission["hermesSource"],
               "url": "http://127.0.0.1:8787/mcp/control", "bearer": bearer, "run_id": run_id}
    raw = json.dumps(request, separators=(",", ":")).encode("ascii")
    _need(len(raw) <= 16384)
    process = subprocess.Popen([str(HERMES_PYTHON), "-B", str(HERE / "original_flow_hermes_child.py")],  # noqa: S603 -- fixed reviewed executable/module
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    received = bytearray()
    try:
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        stop = min(deadline - 5, time.monotonic() + (55 if task == "read-and-deny" else 35))
        sent = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            while True:
                _need(time.monotonic() < stop, "HERMES_TASK_FAILED")
                for key, _ in selector.select(timeout=min(0.2, max(0, stop - time.monotonic()))):
                    if key.data == "stdin":
                        sent += os.write(key.fileobj.fileno(), raw[sent:sent + 4096])
                        if sent == len(raw):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                            request["bearer"], raw = "", b""
                    else:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if chunk:
                            received.extend(chunk)
                            _need(len(received) <= 1_048_576, "HERMES_OUTPUT_REJECTED")
                        else:
                            selector.unregister(key.fileobj)
                if not selector.get_map() and process.poll() is not None:
                    break
        _need(process.returncode == 0, "HERMES_TASK_FAILED")
        return _json(bytes(received))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        _need(process.poll() is not None, "CLEANUP_REQUIRED")
        for stream in (process.stdin, process.stdout):
            with contextlib.suppress(Exception):
                stream.close()
        received.clear()


def normalize_task(value, task, run_id):
    _need(type(value) is dict and set(value) == {"schema", "task", "receipts", "summary", "guard_clean"}
          and value["schema"] == "tessera.original_hermes_task_result.v1" and value["task"] == task
          and value["guard_clean"] is True,
          "HERMES_OUTPUT_REJECTED")
    rows = value["receipts"]
    _need(type(rows) is list and len(rows) == len(TASK_PROBES[task]), "HERMES_OUTPUT_REJECTED")
    trace = []
    for index, (row, (name, arguments)) in enumerate(zip(rows, TASK_PROBES[task], strict=True)):
        expected = "REVIEW_STOP" if task == "review-stop" else ("RESPONSE" if index == 0 else "DENIED")
        _need(type(row) is dict and row.get("status") == expected and row.get("tool_name") == name
              and row.get("arguments") == arguments and row.get("run_id") == run_id
              and row.get("conversation_id") == run_id and row.get("native_dispatch_attempted") is True
              and row.get("tool_use_id") == "hermes-" + task + "-" + str(index)
              and all(type(row.get(key)) is str and ID_RE.fullmatch(row[key])
                      for key in ("request_id", "audit_event_id")), "HERMES_OUTPUT_REJECTED")
        result, error = row.get("sdk_result"), row.get("sdk_error")
        _need((type(result) is dict) != (type(error) is dict), "HERMES_OUTPUT_REJECTED")
        if expected == "REVIEW_STOP":
            _need(type(error) is dict and error.get("code") == -32604, "HERMES_OUTPUT_REJECTED")
        elif expected == "DENIED":
            _need(result.get("isError") is True, "HERMES_OUTPUT_REJECTED")
        else:
            _need(result.get("isError") is False, "HERMES_OUTPUT_REJECTED")
        trace.append({"request_id": row["request_id"], "tool_use_id": row["tool_use_id"], "tool_name": name,
                      "arguments": arguments, "audit_event_id": row["audit_event_id"],
                      "response": {"result": result} if type(result) is dict else {"error": error}})
    return trace


def verify_original_outcome(trace, service, correlations, *, complete):
    rows = {row["request_id"]: row for row in correlations["correlations"]}
    expected = (("aws___list_regions", "allow"), ("fixture__request_access_change", "block"),
                ("fixture__request_diagnostics_review", "require_approval"))
    checks = {"three_scripted_probes_completed": complete is True and len(trace) == 3,
              "canonical_decisions_correlated": len(rows) == 3 and all(
                  call["tool_name"] == name and rows.get(call["request_id"], {}).get("decision") == decision
                  for call, (name, decision) in zip(trace, expected, strict=False)),
              "review_uses_exact_hosted_policy": len(trace) == 3 and rows.get(trace[2]["request_id"], {}).get("policy_id") == POLICY_ID,
              "zero_denied_contacts": correlations.get("denied_contacts") == 0,
              "inert_fixture_executions_zero": type(service.get("fixture_noop_count")) is int and service["fixture_noop_count"] == 0}
    contacts = service.get("contacts", [])
    checks["one_real_named_read_only_contact"] = (type(contacts) is list and len(contacts) == 1 and bool(trace)
        and contacts[0].get("request_id") == trace[0].get("request_id")
        and contacts[0].get("tool_use_id") == trace[0].get("tool_use_id")
        and contacts[0].get("tool_name") == trace[0].get("tool_name") == "aws___list_regions"
        and trace[0].get("arguments") == {} and contacts[0].get("argument_digest") == hashlib.sha256(b"{}").hexdigest()
        and ("arguments" not in contacts[0] or contacts[0]["arguments"] == {})
        and all(contacts[0].get(k) is None for k in ("operation", "app_id", "branch", "job_id"))
        and contacts[0].get("source") == "aws_mcp_readonly"
        and contacts[0].get("outcome_type") == "read_only_complete" and service.get("successful_aws_reads") == 1)
    if trace:
        result = trace[0].get("response", {}).get("result", {})
        content = result.get("structuredContent", {})
        body = content.get("result") if type(content) is dict else None
        checks["actual_regions_result_received"] = (type(content) is dict and content.get("source") == "AWS_MCP"
            and content.get("execution") == "read_only_complete" and type(body) is str and 0 < len(body) <= 65536)
    else:
        checks["actual_regions_result_received"] = False
    return {"accepted": all(checks.values()), "outcome_checks": checks,
            "label": "TWO SCRIPTED OPERATOR TASKS; HERMES CONNECTOR; NO REASONING MODEL",
            "control_probes": "Synthetic inert access and diagnostics proposals; no protected executor exists",
            "provenance": "client_reported"}


def _run_original_flow(*, oauth_token, verified_access_claims, output, run_id, admission, runtime_admitted=False):
    """Called once by the reviewed OAuth owner; returns only a finite safe summary.

    Admission must bind independently reviewed complete Hermes source/dependency
    receipts. Their authentication is a caller precondition, not established here.
    An exception is a stop before any subsequent Nova run; never retry this flow.
    """
    overall_deadline = time.monotonic() + MAX_SECONDS
    deadline = overall_deadline - 60
    validate_admission(admission, runtime_admitted=runtime_admitted)
    _need(not any(key.startswith("TESSERA_") for key in os.environ))
    _need(type(oauth_token) is str and 1 <= len(oauth_token) <= 16384)
    _need(type(verified_access_claims) is dict and verified_access_claims.get("scope") == "tessera:policies:read tessera:audit:write"
          and verified_access_claims.get("client_id") == "tessera-cli")
    tenant = verified_access_claims.get("tenant_id")
    _need(type(tenant) is str and re.fullmatch(r"[a-z0-9_-]{1,64}", tenant) and not tenant.startswith("__"))
    _need(type(run_id) is str and run_id.startswith("original1-"))
    try:
        parsed_id = uuid.UUID(run_id[len("original1-"):])
    except (ValueError, AttributeError):
        raise OriginalFlowError("INPUT_REJECTED") from None
    _need(parsed_id.version == 4 and parsed_id.variant == uuid.RFC_4122)
    _need(run_id == "original1-" + str(parsed_id))
    output = Path(output)
    owned_root = Path("/dev/shm") / ("cm095-workload1-" + str(parsed_id))  # noqa: S108 -- outer watchdog owns this exact root
    _need(output == owned_root / "original" and owned_root.resolve(strict=True) == owned_root
          and not owned_root.is_symlink() and stat.S_IMODE(owned_root.stat().st_mode) == 0o700
          and owned_root.stat().st_uid == os.getuid(), "RAM_REQUIRED")
    _need(Path(admission["hermesSource"]).resolve(strict=True).is_relative_to(owned_root), "RAM_REQUIRED")
    _need(any(row.split()[1:3] == [str(owned_root.parent), "tmpfs"] for row in Path("/proc/mounts").read_text().splitlines()), "RAM_REQUIRED")
    output.mkdir(mode=0o700, exist_ok=False)
    _need(stat.S_IMODE(output.stat().st_mode) == 0o700 and output.stat().st_uid == os.getuid(), "RAM_REQUIRED")
    orch = _load_orchestrator(admission["sourcePins"]["orchestrator.py"])
    import yaml

    from tessera.audit.cloud_uploader import AuditCloudUploader
    from tessera.cloud_sync import CloudPolicySync

    children, logs = [], []
    proxy_token, sandbox_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    env = {}
    trace = []
    complete = False
    summary = None
    try:
        cache, audit = output / "policy-cache.db", output / "audit.db"
        policy = _read(HERE / "readiness-review.yaml", POLICY_SHA256).decode()
        sync = CloudPolicySync(endpoint=ENDPOINT, oauth_token=oauth_token, cache_path=cache,
                               strict=True, expected_tenant_id=tenant)
        items = asyncio.run(asyncio.wait_for(sync.fetch_and_cache(), timeout=20))
        active = [row for row in items if row["policy_id"] == POLICY_ID and row["enabled"]]
        _need(len(active) == 1 and active[0]["yaml_text"] == policy and sync.snapshot is not None, "ACTIVATION_REJECTED")
        policies = output / "policies"
        policies.mkdir(mode=0o700)
        (policies / "known-tools.yaml").write_text(yaml.safe_dump({"id": "original-regions-only", "name": "One bounded region read",
            "priority": 0, "match": {"upstream": "control", "tool": "aws___list_regions"}, "action": "allow",
            "reason": "Operator permits the no-argument AWS MCP region catalogue read"}), encoding="utf-8")
        config = {"listen": {"host": "127.0.0.1", "port": 8787}, "deployment_id": tenant,
            "auth": {"type": "bearer"}, "audit": {"sink": "sqlite", "path": str(audit), "payload_mode": "full"},
            "policies": {"dir": str(policies), "reload": "none", "mode": "enforcement", "default_action": "block"},
            "upstreams": [{"name": "control", "kind": "mcp_streamable_http", "url": "http://127.0.0.1:8786/mcp",
                           "auth_header": "Bearer ${CM_SANDBOX_TOKEN}", "request_timeout_s": 40}], "intelligence": {"enabled": False}}
        config_path = output / "tessera.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(RUNTIME),
               "CM_READINESS_UPSTREAM_TOKEN": sandbox_token, "CM_SANDBOX_TOKEN": sandbox_token,
               "TESSERA_BEARER_TOKENS": tenant + ":" + proxy_token, "TESSERA_POLICY_CACHE": str(cache),
               "TESSERA_POLICY_LOADER": "tessera.policy.cloud_loader:CloudBackedPolicyLoader", "TESSERA_CLOUD_STRICT": "1",
               "TESSERA_CLOUD_TENANT": tenant, "TESSERA_CLOUD_ENDPOINT": ENDPOINT, "TESSERA_EXPECTED_POLICY_ID": POLICY_ID,
               "TESSERA_EXPECTED_POLICY_SHA256": POLICY_SHA256, "TESSERA_EXPECTED_SNAPSHOT": sync.snapshot["snapshot_digest"]}
        for name, command in (
            ("adapter", [sys.executable, "-B", str(HERE / "aws_readiness_server.py"), "--run-id", run_id,
                         "--ledger", str(output / "adapter.jsonl"), "--port", "8786", "--max-requests", "3"]),
            ("proxy", [sys.executable, "-B", "-m", "tessera.cli", "serve", "--config", str(config_path), "--cloud"]),
        ):
            _remaining(deadline, 35)
            log = (output / (name + ".log")).open("w", encoding="utf-8")
            os.chmod(log.name, 0o600)
            logs.append(log)
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603 -- fixed source commands
            children.append(process)
            if name == "adapter":
                orch.wait_http("http://127.0.0.1:8786/evidence", process, sandbox_token)
        activation = orch.wait_http("http://127.0.0.1:8787/healthz", children[-1]).get("policy_state", {}).get("activation", {})
        _need(activation.get("verified") is True and activation.get("tenant_id") == tenant
              and activation.get("snapshot_digest") == sync.snapshot["snapshot_digest"]
              and activation.get("policy_hashes", {}).get(POLICY_ID) == POLICY_SHA256, "ACTIVATION_REJECTED")
        orch.write_private(output / "activation.json", activation)
        for task in ("read-and-deny", "review-stop"):
            _remaining(deadline, 30)
            task_result = _child(task=task, bearer=proxy_token, run_id=run_id, admission=admission, output=output, deadline=deadline)
            trace += normalize_task(task_result, task, run_id)
            orch.write_private(output / (task + ".json"), task_result)
        complete = True
    finally:
        # Proxy drains canonical audit before correlation/upload; all children are
        # attempted even after an earlier task failure. Root is the outer watchdog.
        failures = []
        for process in reversed(children):
            try:
                orch.stop_process(process)
            except BaseException:
                failures.append("PROCESS_STOP_FAILED")
        for log in logs:
            with contextlib.suppress(Exception):
                log.close()
        env.clear()
        proxy_token = ""
        stopped = all(process.poll() is not None for process in children)
        orch.write_private(output / "shutdown.json", {"all_children_stopped": stopped, "failure_codes": failures})
        if failures or not stopped:
            raise OriginalFlowError("CLEANUP_REQUIRED") from None
    _remaining(overall_deadline, 25)
    # Adapter is stopped; read its finite metadata-only ledger, never call AWS or
    # restart a stopped child to obtain final evidence.
    ledger = _read(output / "adapter.jsonl", limit=1_048_576)
    contacts = [_json(line) for line in ledger.splitlines() if line]
    service = {"contacts": contacts, "fixture_noop_count": sum(row.get("source") == "inert_fixture" for row in contacts),
               "successful_aws_reads": sum(row.get("source") == "aws_mcp_readonly" and row.get("outcome_type") == "read_only_complete"
                                           for row in contacts)}
    correlated = orch.evidence_correlations(audit, tenant, {"tool_trace": trace}, service)
    _need(type(correlated.get("event_count")) is int and 0 < correlated["event_count"] <= 32)
    verification = verify_original_outcome(trace, service, correlated, complete=complete)
    identity = orch.runtime_identity(policies / "known-tools.yaml", readiness=True)
    evidence = {"run_id": run_id, "label": verification["label"], "provenance": "client_reported",
        "client": {"kind": "Hermes scripted connector", "reasoning_model_used": False, "scripted_tasks": 2},
        "active_policy": activation, "runtime": identity, "tool_trace": trace, "sandbox": service,
        "verification": {**correlated, **verification}}
    orch.write_private(output / "run-evidence.json", evidence)
    uploader = AuditCloudUploader(endpoint=ENDPOINT, oauth_token=oauth_token, batch_size=50)
    ack = asyncio.run(asyncio.wait_for(uploader.upload_sqlite_once(audit, tenant_id=tenant, stream_id=run_id,
                                                                 run_evidence=evidence), timeout=20))
    orch.write_private(output / "cloud-ack.json", ack)
    acknowledged = (ack.get("verification") == "canonical_verified" and ack.get("tenant_id") == tenant
        and ack.get("stream_id") == run_id and ack.get("run_evidence_stored") is True
        and ack.get("run_evidence_digest") == hashlib.sha256(orch.canonical_json(evidence)).hexdigest())
    summary = {"schema": "tessera.original_flow_result.v1", "status": "ACKNOWLEDGED" if acknowledged and verification["accepted"] else "BLOCKED",
        "run_id": run_id, "scripted_tasks": 2, "tool_calls": len(trace), "model_calls": 0,
        "canonical_upload_acknowledged": acknowledged, "all_children_stopped": True,
        "accepted": verification["accepted"], "label": verification["label"], "provenance": "client_reported"}
    orch.write_private(output / "finalization.json", summary)
    oauth_token = sandbox_token = ""
    _need(acknowledged, "ACK_REJECTED")
    _need(verification["accepted"], "OUTCOME_REJECTED")
    return summary


def run_original_flow(*, oauth_token, verified_access_claims, output, run_id, admission, runtime_admitted=False):
    """Public in-process seam; raw library errors never escape to the operator."""
    try:
        return _run_original_flow(oauth_token=oauth_token, verified_access_claims=verified_access_claims,
            output=output, run_id=run_id, admission=admission, runtime_admitted=runtime_admitted)
    except OriginalFlowError:
        raise
    except Exception:
        raise OriginalFlowError("FLOW_FAILED") from None
