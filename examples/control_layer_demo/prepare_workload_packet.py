"""Prepare the fixed Nova-only packet; never create approval or launch a flow.

Run with the existing demo Python in WSL and a new maintained read-only preflight
receipt. Output files are finite nonsecret source/input/budget/request records.
Existing approval, claims or packet inputs stop preparation without replacement.
The separate reviewed launcher still requires an actual native human decision.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[1]
AUDIT = RUNTIME.parent / "workspace-support/audit/2026-10-18-cm095-resume"
HEAD = "29af3519163648d61d216c2404175a3e5c509934"
OPERATOR_SHA = "63333a14eb6d6eee680ae760a2e73b6bd4b845609f198e44da12c2ed821abe21"
RUNNER_SHA = "6cf460b5f962887b0244687044d4f0b621712ac4373c58cbe772071b40f9198b"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def save(name: str, raw: bytes) -> str:
    descriptor = os.open(AUDIT / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return digest(raw)


def prepare(preflight: Path) -> dict:
    # Only this already reviewed source family can be prepared by this helper.
    if digest((HERE / "ephemeral_oauth_workload.py").read_bytes()) != OPERATOR_SHA:
        raise ValueError("OPERATOR_MISMATCH")
    if digest((HERE / "run_ephemeral_oauth_workload.py").read_bytes()) != RUNNER_SHA:
        raise ValueError("RUNNER_MISMATCH")
    actual = subprocess.check_output(  # noqa: S603 -- fixed local Git read
        ["/usr/bin/git", "--no-optional-locks", "-C", str(RUNTIME), "rev-parse", "HEAD"], timeout=10,
    ).decode("ascii").strip()
    if actual != HEAD:
        raise ValueError("HEAD_MISMATCH")
    for name in ("workload1-run-inputs.json", "workload1-source-manifest.json", "workload1-model-budget.json",
                 "workload1-founder-approval.json", "workload1-approval-request.json",
                 "connection-launch-workload1.started.json", "connection-flow-workload1.started.json",
                 "connection-exchange-workload1.started.json", "workload1-result.json", "workload1-watchdog.json"):
        if (AUDIT / name).exists() or (AUDIT / name).is_symlink():
            raise ValueError("EXISTING_PACKET_OR_CLAIM")
    if preflight.resolve().parent != AUDIT.resolve() or preflight.is_symlink():
        raise ValueError("PREFLIGHT_PATH_INVALID")
    raw = preflight.read_bytes()
    if len(raw) > 65536:
        raise ValueError("PREFLIGHT_TOO_LARGE")
    proof = json.loads(raw)
    at = datetime.fromisoformat(proof["at"])
    age = (datetime.now(UTC) - at).total_seconds()
    if not -30 <= age <= 3600 or proof.get("status") != "PASS":
        raise ValueError("PREFLIGHT_NOT_FRESH_PASS")
    if any(type(proof.get(k)) is not int or proof[k] != 0 for k in ("inference_calls", "oauth_calls", "cloud_writes")):
        raise ValueError("PREFLIGHT_SCOPE_INVALID")
    spec = importlib.util.spec_from_file_location("_nova_packet_operator", HERE / "ephemeral_oauth_workload.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    manifest = {
        "schema": "tessera.workload_source_manifest.v1", "runtimeBaseline": module._RUNTIME_BASELINE,
        "runtimeHead": HEAD,
        "files": [{"root": "runtime", "path": path, "sha256": digest((RUNTIME / path).read_bytes())}
                  for path in sorted(module._RUNTIME_FILES | module._tracked_runtime_paths())]
        + [{"root": "backend", "path": path, "sha256": sha} for path, sha in sorted(module._base._SOURCE_PINS.items())],
        "dependencies": {
            name: {"version": importlib.metadata.version(name),
                   "distributionRoot": str(Path(importlib.metadata.distribution(name).locate_file("")).resolve())}
            for name in module._DEPENDENCIES
        },
    }
    budget = {**module._BUDGET, "schema": "tessera.workload_model_budget.v1",
              "freshReadOnlyPreflightSha256": digest(raw)}
    inputs = {"schema": "tessera.workload_run_inputs.v1", "runLabel": "workload1", "testRunId": str(uuid.uuid4()),
              "account": module._base._ACCOUNT, "region": module._base._REGION, "client": "tessera-cli",
              "scope": module._base._SCOPE, "planSha256": module._base._PLAN_SHA,
              "seedManifestSha256": module._base._SEED_SHA, "baseOperatorSha256": module._BASE_SHA,
              "policySha256": module._POLICY_SHA, "objective": module._PROMPTS[0], "followupObjective": module._PROMPTS[1],
              "sourceManifestSha256": digest(encoded(manifest)), "modelBudgetSha256": digest(encoded(budget))}
    # Archive prior read-only evidence. Never touch consumed approvals/claims.
    prior = AUDIT / "workload1-fresh-readonly-preflight.json"
    if prior.is_symlink():
        raise ValueError("PREFLIGHT_PATH_INVALID")
    if prior.exists() and prior.read_bytes() != raw:
        old = prior.read_bytes()
        archive = "workload-preflight-superseded-" + digest(old) + ".json"
        if not (AUDIT / archive).exists():
            save(archive, old)
    staged = "workload1-preflight-prepared-new.json"
    save(staged, raw)
    os.replace(AUDIT / staged, prior)
    save("workload1-source-manifest.json", encoded(manifest))
    save("workload1-model-budget.json", encoded(budget))
    save("workload1-run-inputs.json", encoded(inputs))
    # Pure source/budget validation only; do not synthesize approval or call check().
    operator = module.WorkloadOperator()
    operator.inputs = inputs
    operator.receipt["sourcePins"]["operatorSha256"] = OPERATOR_SHA
    operator._verify_local_sources()
    request = {
        "schema": "tessera.workload_approval_request.v1", "status": "PENDING_HUMAN_DECISION",
        "testRunId": inputs["testRunId"], "runInputsSha256": digest(encoded(inputs)),
        "operatorSha256": OPERATOR_SHA, "runnerSha256": RUNNER_SHA,
        "sourceManifestSha256": inputs["sourceManifestSha256"], "modelBudgetSha256": inputs["modelBudgetSha256"],
        "runtimeHead": HEAD, "decisionRequired": "Approve one workload test",
        "policySave": {"id": "workload-tessera-read", "sha256": module._POLICY_SHA,
                       "operation": "normal hosted UI review/save/readback/export; preserve unrelated policies"},
        "oauth": {"client": "tessera-cli", "scope": module._base._SCOPE, "grants": 1,
                  "normalNonceCodeCreationConsumptionIncluded": True, "accessSeconds": 3600,
                  "refreshSeconds": 2592000, "refreshNeverUsed": True},
        "model": module._BUDGET, "objectives": list(module._PROMPTS),
        "operations": {"strictPolicySnapshot": True, "canonicalAuditUpload": True,
                       "allowedAdapterTarget": "amplify.GetJob d2nxbpvv53qeqc/main/84",
                       "blockedTarget": "amplify.GetJob d3s9n6ffim4722/main/60", "blockedDownstreamExpected": 0},
        "cleanup": {"account": module._base._ACCOUNT, "region": module._base._REGION,
                    "table": module._base._TABLE, "exactGeneratedRefreshConditionalRevokeAndReadback": 1,
                    "accessSurvivesRefreshRevocation": True, "unknownExchangeOrCleanupCanLeaveRefreshDays": 30,
                    "watchdogSeconds": 750, "termWaitSeconds": 5, "killWaitSeconds": 5},
        "limits": {"noRetryRemintRefreshScan": True, "noNewResourcesSettingsCredentials": True,
                   "noOrdinaryDiskTokenCache": True, "hermesOriginalExcluded": True},
        "freshReadOnlyPreflightSha256": digest(raw), "preflightAt": proof["at"],
        "preparedAtUTC": datetime.now(UTC).isoformat(), "authorizationGranted": False,
    }
    save("workload1-approval-request.json", encoded(request))
    return {"status": "PREPARED_APPROVAL_REQUIRED", "testRunId": inputs["testRunId"],
            "runInputsSha256": digest(encoded(inputs)), "requestSha256": digest(encoded(request)),
            "sourceFiles": len(manifest["files"]), "sdkCalls": 0, "listeners": 0, "claims": 0,
            "authorizationGranted": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.preflight), sort_keys=True))
        return 0
    except BaseException:
        print(json.dumps({"status": "PACKET_PREPARATION_FAILED", "authorizationGranted": False}))
        return 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
