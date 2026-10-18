"""Offline original-flow wiring checks; no Hermes import or live services.

Exercises actual canonical emitter/SQLite/hash chain, orchestration correlations
and one-shot uploader with MockTransport; connector execution is inert. Reuses
the existing frozen-cloud ASGI activation verifier. All fixtures stay in RAM.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import socket
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify():
    sys.dont_write_bytecode = True
    here = Path(__file__).resolve().parent
    flow = _load("_offline_original_flow", here / "original_flow.py")
    worker = _load("_offline_original_child", here / "original_flow_hermes_child.py")
    checks, forbidden = [], {"network": 0, "provider": 0, "model": 0}

    def ok(value, name):
        if not value:
            raise AssertionError("ORIGINAL_OFFLINE_CHECK_FAILED")
        checks.append(name)

    def reject(action, name):
        try:
            action()
        except Exception:
            checks.append(name)
        else:
            raise AssertionError("ORIGINAL_OFFLINE_REJECTION_MISSING")

    def no_network(*args, **kwargs):
        forbidden["network"] += 1
        raise AssertionError("NETWORK_FORBIDDEN")

    def no_provider(frame, event, arg):
        if event != "call":
            return
        name = frame.f_globals.get("__name__", "")
        function = frame.f_code.co_name
        if name.startswith(("boto3", "botocore")) and function in {"client", "resource", "create_client", "get_credentials", "_make_api_call"}:
            forbidden["provider"] += 1
            raise AssertionError("PROVIDER_FORBIDDEN")
        if function in {"run_agent", "session_login"}:
            forbidden["model"] += 1
            raise AssertionError("MODEL_OR_LOGIN_FORBIDDEN")

    with (patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network),
          patch.object(socket, "create_connection", no_network), patch.object(socket, "getaddrinfo", no_network),
          tempfile.TemporaryDirectory(prefix="cm095-original-offline-", dir="/dev/shm") as directory):
        sys.setprofile(no_provider)
        try:
            with patch.object(flow, "_run_original_flow", side_effect=ValueError("synthetic-private-error-marker")):
                try:
                    flow.run_original_flow(oauth_token=None, verified_access_claims=None, output=None, run_id=None, admission=None)
                except flow.OriginalFlowError as error:
                    ok(error.code == "FLOW_FAILED" and "synthetic-private" not in str(error), "finite_library_failure_projection")
                else:
                    raise AssertionError("FINITE_PROJECTION_REQUIRED")
            reject(lambda: flow.validate_admission(None, runtime_admitted=False), "default_no_admission")
            reject(lambda: flow.validate_admission({"schema": "tessera.original_flow_admission.v1"}, runtime_admitted=True), "incomplete_admission")
            raw_admission = {"schema": "tessera.original_flow_admission.v1", "sourcePins": {}, "hermesPython": "unused",
                "hermesSource": directory, "hermesFullSourceReceiptSha256": "0" * 64, "hermesDependencyReceiptSha256": "0" * 64,
                "hermesFullSourceAuthenticated": False, "hermesDependencyReviewed": True}
            reject(lambda: flow.validate_admission(raw_admission, runtime_admitted=True), "partial_source_not_admitted")

            # Actual existing ASGI/runtime startup check, wholly offline.
            activation = _load("_offline_original_activation", here / "verify_readiness_activation.py").run()
            ok(activation["status"] == "PASS", "existing_strict_snapshot_and_activation_verifier")
            sys.setprofile(no_provider)
            orch = flow._load_orchestrator(hashlib.sha256((here / "orchestrator.py").read_bytes()).hexdigest())
            import httpx

            from tessera.audit.canonical_json import canonical_json
            from tessera.audit.chain import HashChain
            from tessera.audit.cloud_uploader import AuditCloudUploader
            from tessera.audit.emitter import AuditEmitter
            from tessera.audit.sinks.sqlite import SqliteSink

            root = Path(directory)
            temporary = root / "tmp"
            temporary.mkdir(mode=0o700)
            temp_env = {"HOME": str(root), "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary)}
            with patch.dict(os.environ, temp_env):
                ok(worker._private_tmp(root) == temporary, "worker_private_ram_temp")
                for key in ("TMPDIR", "TMP", "TEMP"):
                    with patch.dict(os.environ, {key: "/tmp"}):  # noqa: S108 -- rejection-only; no write
                        reject(lambda: worker._private_tmp(root), "worker_wrong_" + key + "_rejected")
                temporary.chmod(0o755)
                reject(lambda: worker._private_tmp(root), "worker_public_temp_mode_rejected")
                temporary.chmod(0o700)
                temporary.rmdir()
                temporary.symlink_to(root, target_is_directory=True)
                reject(lambda: worker._private_tmp(root), "worker_symlink_temp_rejected")
                temporary.unlink()
                temporary.mkdir(mode=0o700)
            db = root / "audit.db"
            tenant, run_id = "offline_original", "original1-abcdefab-cdef-4abc-8def-abcdefabcdef"
            sink = SqliteSink(db)
            emitter = AuditEmitter(tenant_id=tenant, sinks=[sink])
            receipts = []
            plans = [("read-and-deny", "aws___list_regions", {}, "allow", "original-regions-only", "RESPONSE"),
                     ("read-and-deny", "fixture__request_access_change", {"request": "production_access_change"}, "block", None, "DENIED"),
                     ("review-stop", "fixture__request_diagnostics_review", {"request": "read_only_diagnostics_review"}, "require_approval", flow.POLICY_ID, "REVIEW_STOP")]
            for index, (task, name, arguments, decision, policy_id, status) in enumerate(plans):
                use_id = "hermes-" + task + "-" + str(0 if task == "review-stop" else index)
                meta = {"run_id": run_id, "conversation_id": run_id, "request_id": "req-" + str(index), "tool_use_id": use_id}
                event = emitter.emit("tool_call", {"tool_call": {"name": name, "arguments": arguments, "_meta": meta},
                                                     "decision": decision, "policy_id": policy_id, "reason": "offline fixture"})
                ok(HashChain.verify_event_hash(event), "actual_canonical_event_" + str(index))
                result, error = None, None
                if status == "REVIEW_STOP":
                    error = {"code": -32604, "message": "Review required", "data": {"_meta": {"tessera_audit_event_id": event["eventId"]}}}
                else:
                    result = {"isError": status == "DENIED", "_meta": {"tessera_audit_event_id": event["eventId"]},
                        "content": [{"type": "text", "text": "POLICY_BLOCK\nOffline fixture" if status == "DENIED" else "Offline regions"}]}
                    if status == "RESPONSE":
                        result["structuredContent"] = {"source": "AWS_MCP", "execution": "read_only_complete", "result": '{"regions":["us-east-1"]}'}
                receipts.append({**meta, "tool_name": name, "arguments": arguments, "status": status,
                    "audit_event_id": event["eventId"], "native_dispatch_attempted": True, "sdk_result": result, "sdk_error": error})
            sink.close()
            child_values = {
                task: {"schema": "tessera.original_hermes_task_result.v1", "task": task,
                       "receipts": receipts[:2] if task == "read-and-deny" else receipts[2:], "summary": {}, "guard_clean": True}
                for task in flow.TASK_PROBES}
            trace = flow.normalize_task(child_values["read-and-deny"], "read-and-deny", run_id)
            trace += flow.normalize_task(child_values["review-stop"], "review-stop", run_id)
            service = {"contacts": [{"request_id": "req-0", "tool_use_id": "hermes-read-and-deny-0", "tool_name": "aws___list_regions",
                "source": "aws_mcp_readonly", "outcome_type": "read_only_complete", "argument_digest": hashlib.sha256(b"{}").hexdigest()}], "fixture_noop_count": 0, "successful_aws_reads": 1}
            correlated = orch.evidence_correlations(db, tenant, {"tool_trace": trace}, service)
            outcome = flow.verify_original_outcome(trace, service, correlated, complete=True)
            ok(outcome["accepted"] and correlated["denied_contacts"] == 0, "actual_canonical_three_decisions_and_one_contact")
            for key, value in (("tool_use_id", "wrong-use"), ("tool_name", "wrong-tool"),
                               ("argument_digest", "0" * 64), ("arguments", {"wrong": True}), ("operation", "wrong-operation")):
                mismatched = copy.deepcopy(service)
                mismatched["contacts"][0][key] = value
                ok(not flow.verify_original_outcome(trace, mismatched, correlated, complete=True)["accepted"], "contact_" + key + "_rejected")
            unsafe_guard = copy.deepcopy(child_values["read-and-deny"])
            unsafe_guard["guard_clean"] = False
            reject(lambda: flow.normalize_task(unsafe_guard, "read-and-deny", run_id), "unclean_child_guard_rejected")
            bad = copy.deepcopy(service)
            bad["contacts"].append({"request_id": "req-1"})
            reject(lambda: orch.evidence_correlations(db, tenant, {"tool_trace": trace}, bad), "denied_contact_rejected")
            bad = copy.deepcopy(trace)
            bad[0]["audit_event_id"] = "unknown-event"
            reject(lambda: orch.evidence_correlations(db, tenant, {"tool_trace": bad}, service), "unknown_audit_event_rejected")
            for field, value in (("arguments", {"request": "wrong"}), ("status", "RESPONSE"), ("native_dispatch_attempted", False)):
                bad = copy.deepcopy(child_values["read-and-deny"])
                bad["receipts"][1][field] = value
                reject(lambda bad=bad: flow.normalize_task(bad, "read-and-deny", run_id), "bad_child_" + field)
            bad = copy.deepcopy(correlated)
            bad["correlations"][2]["policy_id"] = "wrong-policy"
            ok(not flow.verify_original_outcome(trace, service, bad, complete=True)["accepted"], "wrong_hosted_review_rejected")
            bad = {**service, "fixture_noop_count": 1}
            ok(not flow.verify_original_outcome(trace, bad, correlated, complete=True)["accepted"], "inert_execution_rejected")

            # Worker exercises its fixed task sequence with an inert connector.
            for task in worker.TASKS:
                observed = []
                class Connector:
                    def __init__(self, **kwargs):
                        self.kwargs, self.stopped, self.rows = kwargs, False, []
                    def connect(self, *, runtime_admitted, task=task):
                        ok(runtime_admitted is True, "worker_explicit_admission_" + task)
                        return [{"name": probe["name"]} for probe in worker.TASKS[task]]
                    def call(self, name, arguments, tool_use_id, task=task, observed=observed):
                        index = len(self.rows)
                        row = copy.deepcopy(child_values[task]["receipts"][index])
                        self.rows.append(row)
                        self.stopped = row["status"] != "RESPONSE"
                        observed.append(name)
                        return row
                    def public_summary(self):
                        return {"status": "SYNTHETIC_ONLY"}
                    def close(self, observed=observed):
                        observed.append("closed")
                request = {"schema": "tessera.original_hermes_task.v1", "task": task, "source": directory,
                    "url": "http://127.0.0.1:8787/mcp/control", "bearer": "synthetic-inert-loopback", "run_id": run_id}
                @contextlib.contextmanager
                def guarded_runtime(*, source, profile_parent, observed=observed):
                    ok(source == root and profile_parent == root, "worker_guard_paths")
                    yield types.SimpleNamespace(assert_clean=lambda: observed.append("guard_clean"))
                with patch.dict(os.environ, temp_env):
                    result = worker.execute(request, types.SimpleNamespace(HermesClient=Connector, guarded_loopback_runtime=guarded_runtime))
                ok(result["task"] == task and observed[-2:] == ["closed", "guard_clean"] and result["guard_clean"] is True and request["bearer"] == "", "worker_stops_closes_" + task)

            # The real one-shot uploader verifies the real chain before a mocked
            # HTTP POST and accepts only exact canonical/run-projection ACKs.
            mock_posts = []
            tamper = False
            evidence = {"run_id": run_id, "provenance": "client_reported", "verification": outcome}
            def respond(request):
                body = json.loads(request.content)
                mock_posts.append(body)
                return httpx.Response(200, json={"accepted_event_ids": [part["event"]["eventId"] for part in body["events"]],
                    "skipped": 0, "tenant_id": tenant, "stream_id": run_id, "chain_head": "0" * 64 if tamper else body["chain_head"],
                    "verification": "canonical_verified", "written": len(body["events"]), "duplicates": 0,
                    "run_evidence_stored": True, "run_evidence_digest": hashlib.sha256(canonical_json(body["run_evidence"])).hexdigest()})
            real_client = httpx.AsyncClient
            def mocked_client(*args, **kwargs):
                return real_client(*args, **kwargs, transport=httpx.MockTransport(respond))
            with patch.object(httpx, "AsyncClient", mocked_client):
                uploader = AuditCloudUploader(endpoint="https://offline.invalid", oauth_token="synthetic-inert", batch_size=50)  # noqa: S106 -- inert offline fixture
                ack = asyncio.run(uploader.upload_sqlite_once(db, tenant_id=tenant, stream_id=run_id, run_evidence=evidence))
                ok(ack["verification"] == "canonical_verified" and len(mock_posts) == 1, "actual_uploader_exact_mock_ack")
                tamper = True
                reject(lambda: asyncio.run(uploader.upload_sqlite_once(db, tenant_id=tenant, stream_id=run_id, run_evidence=evidence)),
                       "actual_uploader_rejects_tampered_chain_ack")
            ok(all(value == 0 for value in forbidden.values()), "zero_external_actions")
        finally:
            sys.setprofile(None)
    return {"schema": "tessera.original_flow_offline_checks.v1", "status": "PASS", "checks": checks,
        "actualNetworkCalls": 0, "actualProviderCalls": 0, "actualModelCalls": 0, "actualHermesImports": 0,
        "actualOAuthCalls": 0, "actualProcessLaunches": 0, "actualCloudWrites": 0, "ramFixturesCleaned": True,
        "scope": "Offline scripted wiring and canonical protocol; not live Hermes or hosted acceptance"}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True), flush=True)
    except BaseException:
        print('{"status":"FAIL","reason_code":"ORIGINAL_OFFLINE_CHECK_FAILED"}', flush=True)
        raise SystemExit(1) from None
