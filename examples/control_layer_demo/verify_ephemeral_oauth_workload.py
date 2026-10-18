"""Offline workload1 admission/replay checks using automatically removed RAM fixtures.

No real approval or claims are created. No actual preflight, OAuth, listener,
provider, model, browser or private seed is used. SDK/HTTP/socket tripwires apply.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch


def verify() -> dict:
    sys.dont_write_bytecode = True
    filename = Path(__file__).with_name("ephemeral_oauth_workload.py")
    spec = importlib.util.spec_from_file_location("_offline_workload", filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    checks = 0
    calls = {"sdk": 0, "http": 0, "network": 0}

    def ok(value):
        nonlocal checks
        if not value:
            raise AssertionError("OFFLINE_CHECK_FAILED")
        checks += 1

    def denied(kind):
        def action(*args, **kwargs):
            calls[kind] += 1
            raise AssertionError("OFFLINE_EXTERNAL_ACTION_FORBIDDEN")
        return action

    def reject(action):
        try:
            action()
        except (module.WorkloadError, module._base._OperatorError, OSError, ValueError):
            ok(True)
        else:
            raise AssertionError("OFFLINE_EXPECTED_REJECTION")

    def write(path, value):
        raw = value if type(value) is bytes else json.dumps(value, separators=(",", ":")).encode("ascii")
        path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    public_audit = module._AUDIT
    source_sha = hashlib.sha256(filename.read_bytes()).hexdigest()
    base = module._base
    with (
        tempfile.TemporaryDirectory(prefix="cm095-workload-offline-", dir="/dev/shm") as directory,
        patch.object(base.boto3, "Session", denied("sdk")),
        patch.object(base.httpx, "Client", denied("http")),
        patch.object(socket, "socket", denied("network")),
        patch.object(socket, "create_connection", denied("network")),
        patch.object(module, "_AUDIT", Path(directory)),
        patch.object(base, "_AUDIT", Path(directory)),
    ):
        root = Path(directory)
        for name in ("oauth-one-test-revocation-plan-v2.json", "workload1-fresh-readonly-preflight.json"):
            write(root / name, (public_audit / name).read_bytes())
        runtime_paths = module._RUNTIME_FILES | module._tracked_runtime_paths()
        files = [{"root": "runtime", "path": path, "sha256": hashlib.sha256((module._RUNTIME / path).read_bytes()).hexdigest()}
                 for path in sorted(runtime_paths)]
        files += [{"root": "backend", "path": path, "sha256": sha} for path, sha in sorted(base._SOURCE_PINS.items())]
        manifest = {"schema": "tessera.workload_source_manifest.v1", "runtimeBaseline": module._RUNTIME_BASELINE,
                    "runtimeHead": subprocess.check_output(["/usr/bin/git", "-C", str(module._RUNTIME), "rev-parse", "HEAD"]).decode().strip(), "files": files,  # noqa: S603 -- fixed local Git read
                    "dependencies": {name: {"version": importlib.metadata.version(name),
                        "distributionRoot": str(Path(importlib.metadata.distribution(name).locate_file("")).resolve())}
                        for name in module._DEPENDENCIES}}
        budget = {**module._BUDGET, "schema": "tessera.workload_model_budget.v1",
                  "freshReadOnlyPreflightSha256": hashlib.sha256((root / "workload1-fresh-readonly-preflight.json").read_bytes()).hexdigest()}
        inputs = {"schema": "tessera.workload_run_inputs.v1", "runLabel": "workload1",
                  "testRunId": "abcdefab-cdef-4abc-8def-abcdefabcdef", "account": base._ACCOUNT,
                  "region": base._REGION, "client": "tessera-cli", "scope": base._SCOPE,
                  "planSha256": base._PLAN_SHA, "seedManifestSha256": base._SEED_SHA,
                  "baseOperatorSha256": module._BASE_SHA, "policySha256": module._POLICY_SHA,
                  "objective": module._PROMPTS[0], "followupObjective": module._PROMPTS[1]}

        def setup(input_change=None, approval_change=None, manifest_change=None, budget_change=None):
            next_manifest, next_budget = copy.deepcopy(manifest), copy.deepcopy(budget)
            if manifest_change:
                manifest_change(next_manifest)
            if budget_change:
                budget_change(next_budget)
            value = {**inputs,
                     "sourceManifestSha256": write(root / "workload1-source-manifest.json", next_manifest),
                     "modelBudgetSha256": write(root / "workload1-model-budget.json", next_budget)}
            if input_change:
                value.update(input_change)
            input_sha = write(root / "workload1-run-inputs.json", value)
            approval = {"schema": "tessera.workload_approval.v1", "approvalStatus": "APPROVED", "runLabel": "workload1",
                        "testRunId": value["testRunId"], "runInputsSha256": input_sha,
                        "operatorSha256": source_sha, "baseOperatorSha256": module._BASE_SHA,
                        "sourceManifestSha256": value["sourceManifestSha256"], "modelBudgetSha256": value["modelBudgetSha256"],
                        "policySha256": module._POLICY_SHA, "client": "tessera-cli", "scope": base._SCOPE,
                        "planSha256": base._PLAN_SHA, "noOrdinaryDiskTokenCache": True,
                        "residualAccessAcknowledged": True, "unknownExchangeNoRetry": True,
                        "interruptedCleanupMayRemainUnproved": True, "oneBoundedModelSession": True,
                        "decision": "Approve one workload test", "approvedAtUTC": "2026-10-18T00:00:00Z"}
            if approval_change:
                approval.update(approval_change)
            approval_sha = write(root / "workload1-founder-approval.json", approval)
            return {"run_inputs": str(root / "workload1-run-inputs.json"),
                    "run_inputs_sha256": input_sha, "approval_sha256": approval_sha}

        reject(lambda: module.WorkloadOperator().check())
        # Inherited actual preflight stops at local admission before SDK/seed/JWKS.
        reject(lambda: module.WorkloadOperator().preflight())
        args = setup()
        result = module.WorkloadOperator(args).check()
        ok(result["status"] == "LOCAL_PACKET_VALID" and result["authorizationGranted"] is False)
        ok(list(root.glob("*.started.json")) == [])
        for change in ({"runLabel": "attempt2"}, {"testRunId": "abcdefab-cdef-1abc-8def-abcdefabcdef"},
                       {"testRunId": "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF"}, {"account": "000000000000"},
                       {"scope": "tessera:policies:read"}, {"baseOperatorSha256": "0" * 64},
                       {"policySha256": "0" * 64}, {"objective": "another task"}, {"unexpected": True}):
            args = setup(input_change=change)
            reject(lambda args=args: module.WorkloadOperator(args).check())
        for change in ({"schema": "tessera.single_connection_approval.v2"}, {"approvalStatus": "PREPARED"},
                       {"runLabel": "attempt2"}, {"decision": "Approve one test"}, {"operatorSha256": "0" * 64},
                       {"baseOperatorSha256": "0" * 64}, {"runInputsSha256": "0" * 64},
                       {"noOrdinaryDiskTokenCache": False}, {"residualAccessAcknowledged": 1},
                       {"unknownExchangeNoRetry": False}, {"interruptedCleanupMayRemainUnproved": False},
                       {"oneBoundedModelSession": False}, {"approvedAtUTC": "2026-99-99T00:00:00Z"}, {"extra": True}):
            args = setup(approval_change=change)
            reject(lambda args=args: module.WorkloadOperator(args).check())
        for action in (lambda m: m.update(runtimeHead="0" * 40), lambda m: m["files"].pop(),
                       lambda m: m["files"].append(m["files"][0]),
                       lambda m: m["files"][0].update(sha256="0" * 64),
                       lambda m: m["files"][0].update(path="../private"),
                       lambda m: m["dependencies"]["httpx"].update(version="0.0"),
                       lambda m: m["dependencies"]["httpx"].update(distributionRoot="/unapproved-distribution-root")):
            args = setup(manifest_change=action)
            reject(lambda args=args: module.WorkloadOperator(args).check())
        for action in (lambda b: b.update(approved=True), lambda b: b.update(max_model_calls=7),
                       lambda b: b.update(max_tool_calls=5), lambda b: b.update(max_seconds=301),
                       lambda b: b.update(max_cost_usd="1.00"), lambda b: b.update(retry_or_fallback_authorized=True)):
            args = setup(budget_change=action)
            reject(lambda args=args: module.WorkloadOperator(args).check())
        args = setup()
        wrong_path = {**args, "run_inputs": str(root / "../workload1-run-inputs.json")}
        reject(lambda: module.WorkloadOperator(wrong_path).check())
        for file_name in ("workload1-run-inputs.json", "workload1-founder-approval.json"):
            args = setup()
            path = root / file_name
            saved = root / "inert-saved.json"
            saved.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(saved)
            reject(lambda args=args: module.WorkloadOperator(args).check())
            path.unlink()
        args = setup()
        operator = module.WorkloadOperator(args)
        operator.check()
        for exchange, marker in ((False, "connection-flow-workload1.started.json"),
                                 (True, "connection-exchange-workload1.started.json")):
            operator.claim_once(exchange=exchange)
            public_marker = json.loads((root / marker).read_text())
            ok(public_marker["runInputsSha256"] == args["run_inputs_sha256"]
               and public_marker["approvalSha256"] == args["approval_sha256"])
            reject(lambda exchange=exchange: operator.claim_once(exchange=exchange))
            reject(lambda args=args: module.WorkloadOperator(args).check())
            (root / marker).unlink()
        args = setup()
        with patch.dict(os.environ, {"TESSERA_AUDIT_SINK": "inert-unapproved-plugin"}):
            reject(lambda: module.WorkloadOperator(args).check())
        operator = module.WorkloadOperator(args)
        operator.check()
        fake = types.ModuleType("agent")
        fake.__file__ = "/unapproved-origin/agent.py"
        with patch.dict(sys.modules, {"agent": fake}):
            reject(operator._origins)
        fifo = root / "inert-fifo"
        os.mkfifo(fifo)
        reject(lambda: module._read(fifo))
        # Exercise lifecycle control using only inert in-memory collaborators.
        # No base preflight, receiver listener, JWT verifier or row writer runs.
        for fail_consumer in (False, True):
            operator = module.WorkloadOperator()
            observed = {"flow": 0, "exchange": 0, "consumer": 0, "cleanup": 0}
            claims = {"tenant_id": "synthetic", "sub": "synthetic", "principal_pool": "synthetic",
                      "binding_version": 1, "scope": base._SCOPE, "client_id": "tessera-cli", "tier": "free", "exp": 1}
            class Receiver:
                def __init__(self, timeout):
                    ok(timeout == 300)
                def __enter__(self):
                    self.redirect_uri = "http://127.0.0.1:1/callback"
                    self.authorization_url = "https://synthetic.invalid/authorize"
                    return self
                def __exit__(self, *args):
                    return False
                def wait_for_callback(self):
                    return types.SimpleNamespace(outcome="VALIDATED_CALLBACK", code="inert", verifier="inert")
            def claim(*, exchange, observed=observed):
                observed["exchange" if exchange else "flow"] += 1
            def consume(token, observed=observed, fail_consumer=fail_consumer, operator=operator):
                observed["consumer"] += 1
                ok(token == "inert-access")  # noqa: S105 -- inert synthetic value, never a credential
                if fail_consumer:
                    raise module.WorkloadError("WORKLOAD_NOT_COMPLETED")
                operator.receipt["workload"] = {"status": "SYNTHETIC_ONLY"}
            def cleanup(observed=observed, operator=operator):
                observed["cleanup"] += 1
                operator.receipt["confirmedRevoked"] = True  # inert receipt only, no actual revocation
            with (patch.object(operator, "preflight", return_value=(types.SimpleNamespace(EphemeralOAuthReceiver=Receiver), None)),
                  patch.object(operator, "claim_once", side_effect=claim),
                  patch.object(operator, "_verify_local_sources"), patch.object(module, "_read", return_value=b"{}"),
                  patch.object(operator, "_http_json", return_value={"access_token": "inert-access", "refresh_token": "inert-refresh",
                      "token_type": "Bearer", "expires_in": 3600, "scope": base._SCOPE}),
                  patch.object(operator, "verify_token", side_effect=lambda *args, claims=claims: dict(claims)),
                  patch.object(operator, "read_row", return_value={}), patch.object(operator, "remember_original"),
                  patch.object(operator, "_workload", side_effect=consume), patch.object(operator, "cleanup", side_effect=cleanup),
                  contextlib.redirect_stdout(io.StringIO())):
                result = operator.run()
            ok(observed == {"flow": 1, "exchange": 1, "consumer": 1, "cleanup": 1})
            ok((result["errorCode"] == "WORKLOAD_NOT_COMPLETED") == fail_consumer)
            ok(operator.refresh is None and operator.access is None and operator.keys == {})
        for name in ("verify_token", "live_binding", "row_key", "read_row", "remember_original", "cleanup", "claim_once", "preflight"):
            ok(getattr(module.WorkloadOperator, name) is getattr(base._Operator, name))
        for argv in ([], ["--approved-workload"], ["--approved-single-connection"] * 7):
            reject(lambda argv=argv: module._args(argv))
        ok(all(count == 0 for count in calls.values()))
    return {"status": "PASS", "checks": checks, "actualPreflightCalls": 0, "guardPreflightCalls": 1,
            "actualSdkCalls": calls["sdk"], "actualHttpCalls": calls["http"], "actualNetworkCalls": calls["network"],
            "realApprovalWrites": 0, "realClaimWrites": 0, "oauthExchanges": 0, "modelCalls": 0,
            "operatorSha256": source_sha, "fixtureRamCleaned": True}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True), flush=True)
    except BaseException:
        print('{"status":"FAIL","errorCode":"OFFLINE_WORKLOAD_CHECK_FAILED","actualPreflightCalls":0}', flush=True)
        raise SystemExit(1) from None
