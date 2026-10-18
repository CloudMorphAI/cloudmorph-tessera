"""One-command offline CM095 checks; no live approval, credentials or launch.

Run with the prepared Tessera demo Python in WSL. Each maintained verifier owns
its synthetic fixtures/tripwires. This driver stops on failure, bounds every
child, and emits only hashes, finite result codes and check counts. It does not
install dependencies, write an approval or replace fresh live acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = (
    "verify_ephemeral_oauth_diagnostics.py", "verify_ephemeral_oauth_operator.py",
    "verify_ephemeral_oauth_runner.py", "verify_readiness_activation.py",
    "verify_nova_agent.py", "verify_workload_read_boundary.py", "verify_workload_policy.py",
    "verify_ephemeral_oauth_workload.py", "verify_ephemeral_oauth_workload_runner.py",
)


def main():
    rows, started = [], time.monotonic()
    failure = "UNSUPPORTED_ENVIRONMENT" if os.name != "posix" or sys.argv[1:] else None
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
                       AWS_EC2_METADATA_DISABLED="true", AWS_CONFIG_FILE=os.devnull,
                       AWS_SHARED_CREDENTIALS_FILE=os.devnull)
    for name in STEPS if failure is None else ():
        path = HERE / name
        row = {"verifier": name, "status": "FAIL"}
        try:
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            row["source_sha256"] = before
            completed = subprocess.run([sys.executable, "-B", str(path)], cwd=HERE.parents[1],  # noqa: S603 -- fixed local paths/no shell
                                       env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                                       timeout=45, check=False)
            if len(completed.stdout) > 65536 or len(completed.stderr) > 65536:
                raise ValueError("OUTPUT_BOUND")
            result = json.loads(completed.stdout)
            if (type(result) is not dict or result.get("status") != "PASS" or completed.returncode != 0
                    or hashlib.sha256(path.read_bytes()).hexdigest() != before):
                raise ValueError("VERIFIER_FAILED")
            row["status"] = "PASS"
            for key in ("check_count", "checksPassed", "checks_passed", "mockConverseCalls"):
                if type(result.get(key)) is int and 0 <= result[key] <= 100000:
                    row[key] = result[key]
        except subprocess.TimeoutExpired:
            failure = "VERIFIER_TIMEOUT"
        except BaseException:
            failure = "VERIFIER_OR_SOURCE_FAILED"
        rows.append(row)
        if failure:
            break
    print(json.dumps({"schema": "tessera.offline_prepared_flow.v1", "status": "FAIL" if failure else "PASS",
                      "failure": failure, "steps": rows, "step_count": len(rows),
                      "elapsed_seconds": round(time.monotonic() - started, 3),
                      "scope": "synthetic_offline_only", "live_acceptance": False}, sort_keys=True))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
