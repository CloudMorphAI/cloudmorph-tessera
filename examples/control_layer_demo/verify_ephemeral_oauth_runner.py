"""Repeatable offline runner checks; synthetic children only, never the operator."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch


def main() -> int:
    path = Path(__file__).with_name("run_ephemeral_oauth_connection.py")
    spec = importlib.util.spec_from_file_location("cm104_runner_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checks: list[str] = []
    real_popen = subprocess.Popen
    network_calls = 0

    def no_network(*_args, **_kwargs):
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("NETWORK_FORBIDDEN")

    try:
        with tempfile.TemporaryDirectory(prefix="cm104-runner-offline-") as temporary, \
                patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
            audit = Path(temporary)
            module.AUDIT = audit
            fifo = audit / "offline-only-fifo"
            os.mkfifo(fifo)
            try:
                module._pinned(fifo, "0" * 64)
                raise AssertionError("FIFO_ACCEPTED")
            except ValueError:
                checks.append("nonregular_input_rejected_without_blocking")
            inputs = {"testRunId": "5e75e0d9-12dc-4278-9f14-bab5e0f3e1e0", "client": "tessera-cli",
                      "scope": "tessera:policies:read tessera:audit:write", "planSha256": "1" * 64}
            args = argparse.Namespace(approved_single_connection=True, approval_sha256="2" * 64,
                                      run_inputs_sha256="3" * 64, operator_sha256="4" * 64,
                                      run_inputs=str(audit / "connection-attempt2-inputs.json"))
            approval = {"schema": "tessera.single_connection_approval.v2", "approvalStatus": "APPROVED",
                        "runLabel": "attempt2", "testRunId": inputs["testRunId"], "runInputsSha256": args.run_inputs_sha256,
                        "operatorSha256": args.operator_sha256, "client": inputs["client"], "scope": inputs["scope"],
                        "planSha256": inputs["planSha256"], "noOrdinaryDiskTokenCache": True,
                        "residualAccessAcknowledged": True, "unknownExchangeNoRetry": True,
                        "decision": "Approve one test", "approvedAtUTC": "2026-10-18T00:00:00Z"}
            approval_path = audit / "single-connection-attempt2-founder-approval.json"
            try:
                module._execute(args, inputs)
                raise AssertionError("MISSING_APPROVAL_ACCEPTED")
            except FileNotFoundError:
                assert not (audit / "connection-launch-attempt2.started.json").exists()
                checks.append("missing_approval_no_claim")
            bad = {**approval, "residualAccessAcknowledged": False}
            approval_path.write_text(json.dumps(bad))
            args.approval_sha256 = hashlib.sha256(approval_path.read_bytes()).hexdigest()
            try:
                module._execute(args, inputs)
                raise AssertionError("BAD_APPROVAL_ACCEPTED")
            except ValueError:
                assert not (audit / "connection-launch-attempt2.started.json").exists()
                checks.append("risk_flag_rejected_before_claim")
            approval_path.write_text(json.dumps(approval))
            args.approval_sha256 = hashlib.sha256(approval_path.read_bytes()).hexdigest()
            final = dict.fromkeys(module.FINAL_KEYS)
            final.update(schema="tessera.single_connection_receipt.v2", testRunId=inputs["testRunId"],
                         runLabel="attempt2", client=inputs["client"], scope=inputs["scope"], status="BLOCKED")
            child = "import json; print(json.dumps(" + repr(final) + "), flush=True)"
            spawned = []

            def synthetic_child(*_args, **kwargs):
                process = real_popen([sys.executable, "-B", "-c", child], **kwargs)  # noqa: S603 - fixed synthetic Python source only
                spawned.append(process)
                return process

            with patch.object(module.subprocess, "Popen", synthetic_child), contextlib.redirect_stdout(io.StringIO()):
                result = module._execute(args, inputs)
            assert result == 0 and len(spawned) == 1 and spawned[0].poll() == 0
            assert json.loads((audit / "connection-attempt2-result.json").read_text()) == final
            assert json.loads((audit / "connection-attempt2-watchdog.json").read_text())["ownedProcessStopped"] is True
            checks.append("synthetic_final_persisted_owned_process_stopped")
            try:
                module._execute(args, inputs)
                raise AssertionError("REPLAY_ACCEPTED")
            except FileExistsError:
                assert len(spawned) == 1
                checks.append("launch_claim_prevents_replay")
            with tempfile.TemporaryDirectory(prefix="cm104-watchdog-offline-") as second:
                module.AUDIT = Path(second)
                second_approval = module.AUDIT / approval_path.name
                second_approval.write_bytes(approval_path.read_bytes())
                child = "import time; time.sleep(20)"
                clock = iter([0.0, 391.0, 391.1])
                module.time = types.SimpleNamespace(monotonic=lambda: next(clock), time=time.time)
                with patch.object(module.subprocess, "Popen", synthetic_child), contextlib.redirect_stdout(io.StringIO()):
                    result = module._execute(args, inputs)
                watchdog = json.loads((module.AUDIT / "connection-attempt2-watchdog.json").read_text())
                assert result == 1 and watchdog["failure"] == "EXTERNAL_390S_DEADLINE"
                assert watchdog["ownedProcessStopped"] and spawned[-1].poll() is not None
                assert not watchdog["finalReceiptPersisted"] and not watchdog["automaticReplay"]
                checks.append("deadline_stops_exact_synthetic_child_no_replay")
            module.time = time
            real_killpg = os.killpg
            for scenario in ["exit_race", "controller_stop"]:
                with tempfile.TemporaryDirectory(prefix="cm104-stop-offline-") as third:
                    module.AUDIT = Path(third)
                    (module.AUDIT / approval_path.name).write_bytes(approval_path.read_bytes())
                    calls = [0]

                    def simulated_clock(calls=calls, scenario=scenario):
                        calls[0] += 1
                        if calls[0] == 1:
                            return 0.0
                        if scenario == "controller_stop" and calls[0] == 2:
                            module._stopped(signal.SIGTERM, None)
                        return 391.0

                    def exited_race(pid, sig):
                        real_killpg(pid, sig)
                        raise ProcessLookupError("SYNTHETIC_EXIT_RACE")

                    module.time = types.SimpleNamespace(monotonic=simulated_clock, time=time.time)
                    previous_term = signal.getsignal(signal.SIGTERM)
                    previous_int = signal.getsignal(signal.SIGINT)
                    try:
                        with patch.object(module.subprocess, "Popen", synthetic_child), \
                                patch.object(module.os, "killpg", exited_race if scenario == "exit_race" else real_killpg), \
                                contextlib.redirect_stdout(io.StringIO()):
                            result = module._execute(args, inputs)
                    finally:
                        signal.signal(signal.SIGTERM, previous_term)
                        signal.signal(signal.SIGINT, previous_int)
                    watchdog = json.loads((module.AUDIT / "connection-attempt2-watchdog.json").read_text())
                    assert result == 1 and watchdog["ownedProcessStopped"] and spawned[-1].poll() is not None
                    assert not watchdog["automaticReplay"] and not watchdog["finalReceiptPersisted"]
                    if scenario == "controller_stop":
                        assert watchdog["failure"] == "CONTROLLER_STOP_REQUESTED"
                    checks.append(scenario + "_persists_watchdog_and_stops_owned_child")
            module.time = time
        assert network_calls == 0
        print(json.dumps({"schema": "tessera.runner_offline_checks.v1", "status": "PASS", "checks": checks,
                          "networkCalls": network_calls, "realOperatorLaunches": 0, "cloudWrites": 0}, sort_keys=True))
        return 0
    except Exception:
        print('{"status":"FAIL","errorCode":"OFFLINE_RUNNER_CHECK_FAILED"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
