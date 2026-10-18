"""Offline owned-runner checks: fake child, private RAM fixtures, no live calls."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import logging
import os
import shutil
import signal
import tempfile
import types
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode


def run():
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    attempts, checks = [], []

    def forbidden(*_args, **_kwargs):
        attempts.append(1)
        raise ValueError("FORBIDDEN_LIVE_CALL")

    mounts = Path("/proc/mounts").read_text().splitlines()
    assert any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in mounts)  # noqa: S108 -- verified RAM
    with contextlib.ExitStack() as guards:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.socket.bind",
                       "socket.socket.listen", "socket.create_connection", "socket.getaddrinfo"):
            guards.enter_context(patch(target, forbidden))
        spec = importlib.util.spec_from_file_location("offline_workload_runner", Path(__file__).with_name("run_ephemeral_oauth_workload.py"))
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        primitives = runner.load("offline_pinned_primitives", runner.HERE / "run_ephemeral_oauth_connection.py", runner.PRIMITIVES_SHA)
        from tessera.audit.canonical_json import canonical_json

        def read(path, expected=None, limit=1_048_576):
            # Exercise the exact reviewed regular-file reader without importing
            # any SDK/operator. Expected hash is optional for finite RAM files.
            if expected is None:
                expected = hashlib.sha256(path.read_bytes()).hexdigest()
            return primitives._pinned(path, expected, limit)

        policy_sha = "c201005df8d1d0985099515a7419e4b3ada539f3704b2a4eb79ccca240b44500"
        expected_outcomes = dict.fromkeys((
            "independent_model_completed", "allowed_target_uses_selected_hosted_policy",
            "second_target_blocked_before_adapter", "one_named_aws_deployment_result_observed",
            "answer_quotes_observed_deployment_fields", "denied_contacts_zero"), True)
        model = {"complete": True, "model_calls": [{"private": "MODEL_TEXT_MUST_NOT_COPY"}],
                 "usage_may_be_incomplete": False,
                 "usage": {"inputTokens": 100, "outputTokens": 1}, "estimated_cost_usd": "0.00000364",
                 "provider_diagnostic": {"exception_class": "ModelErrorException", "http_status": 424,
                    "original_status": 400, "request_id_sha256": "a" * 64}}

        class Admitted:
            def __init__(self, _args):
                pass

            def check(self):
                return {"status": "LOCAL_PACKET_VALID"}

        operator = types.SimpleNamespace(_read=read, _json=json.loads, _POLICY_SHA=policy_sha,
                                         WorkloadOperator=Admitted)
        inputs = {"testRunId": str(uuid.uuid4())}
        ram = Path("/dev/shm") / ("cm095-workload1-" + inputs["testRunId"])  # noqa: S108 -- exact owned test tree
        ram.mkdir(mode=0o700)
        guards.callback(lambda: shutil.rmtree(ram) if ram.exists() else None)
        run_id = "workload1-" + inputs["testRunId"]
        evidence = {"run_id": run_id, "model": model, "tool_trace": [{"private": "TRACE_MUST_NOT_COPY"}],
                    "active_policy": {"verified": True, "tenant_id": "PRIVATE_IDENTITY_MUST_NOT_COPY",
                                      "policy_hashes": {"workload-tessera-read": policy_sha}},
                    "verification": {"accepted": True, "outcome_checks": expected_outcomes, "denied_contacts": 0}}
        finalization = {"local_evidence_stored": True, "model_complete": True, "cloud_upload_acknowledged": True}

        def fixture(changed=None, ack_changed=None):
            value = copy.deepcopy(evidence if changed is None else changed)
            ack = {"stream_id": run_id, "verification": "canonical_verified", "run_evidence_stored": True,
                   "run_evidence_digest": hashlib.sha256(canonical_json(value)).hexdigest()}
            ack.update(ack_changed or {})
            for name, data in {"finalization.json": finalization,
                               "shutdown.json": {"all_children_stopped": True, "shutdown_error_types": []},
                               "run-evidence.json": value, "cloud-ack.json": ack}.items():
                (ram / name).write_text(json.dumps(data))

        fixture()
        projection, owned = runner.finite_evidence(operator, inputs)
        assert owned == ram and projection["accepted"] and projection["denied_contacts"] == 0
        assert projection["provider_diagnostic"] == model["provider_diagnostic"]
        assert "MUST_NOT_COPY" not in json.dumps(projection)
        checks.append("FINITE_PROJECTION_EXCLUDES_IDENTITY_MODEL_AND_TRACE")
        for mutation in ("wrong_policy", "too_many_calls", "bad_diagnostic", "extra_outcome", "bad_denied_count"):
            bad = copy.deepcopy(evidence)
            if mutation == "wrong_policy":
                bad["active_policy"]["policy_hashes"]["workload-tessera-read"] = "0" * 64
            elif mutation == "too_many_calls":
                bad["model"]["model_calls"] *= 7
            elif mutation == "bad_diagnostic":
                bad["model"]["provider_diagnostic"]["request_id_sha256"] = "RAW_ID_MUST_NOT_COPY"
            elif mutation == "extra_outcome":
                bad["verification"]["outcome_checks"]["private"] = True
            else:
                bad["verification"]["denied_contacts"] = False
            fixture(bad)
            try:
                runner.finite_evidence(operator, inputs)
            except ValueError:
                pass
            else:
                raise AssertionError
        fixture(ack_changed={"run_evidence_digest": "0" * 64})
        try:
            runner.finite_evidence(operator, inputs)
        except ValueError:
            pass
        else:
            raise AssertionError
        checks.append("BAD_POLICY_BUDGET_DIAGNOSTIC_ACK_AND_OUTCOMES_REJECTED")
        contacted_ports = []

        class ClosedSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def settimeout(self, _timeout):
                pass

            def connect_ex(self, address):
                contacted_ports.append(address[1])
                return 111

        with patch.object(runner.socket, "socket", ClosedSocket):
            assert runner.ports_closed(51437)
            assert contacted_ports == [51437, 8766, 8767]
            contacted_ports.clear()
            assert not runner.ports_closed(None) and not contacted_ports
        checks.append("ACTUAL_DYNAMIC_CALLBACK_PORT_CHECKED_AND_UNKNOWN_REJECTED")
        fixture()
        receipt = dict.fromkeys(primitives.FINAL_KEYS)
        receipt.update(schema="tessera.workload_connection_receipt.v1", workload={}, testRunId=inputs["testRunId"],
                       runLabel="workload1", status="WORKLOAD_ACKNOWLEDGED_REFRESH_REVOKED_ACCESS_SURVIVES")
        ready = {"status": "READY", "authorizationURL": "https://auth.tessera.cloudmorph.ai/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": "tessera-cli",
            "redirect_uri": "http://127.0.0.1:51437/callback",
            "scope": "tessera:policies:read tessera:audit:write", "state": "s" * 43,
            "code_challenge": "c" * 43, "code_challenge_method": "S256"})}

        class FakeProcess:
            def __init__(self, command, **kwargs):
                assert command[:3] == [runner.PYTHON, "-B", str(runner.HERE / "ephemeral_oauth_workload.py")]
                assert command[3] == "--approved-workload" and kwargs["start_new_session"] is True
                assert not any(name.startswith("TESSERA_") for name in kwargs["env"])
                reader, writer = os.pipe()
                os.write(writer, (json.dumps(ready) + "\n" + json.dumps(receipt) + "\n").encode())
                os.close(writer)
                self.stdout, self.returncode = os.fdopen(reader, "rb", buffering=0), 0

            def poll(self):
                return self.returncode

        def closed_dynamic_port(port):
            assert port == 51437
            return True

        with tempfile.TemporaryDirectory(dir="/dev/shm", prefix="workload-runner-check-") as temporary:
            audit = Path(temporary)
            args = {"run_inputs": "public-inputs", "run_inputs_sha256": "a" * 64, "approval_sha256": "b" * 64}
            with patch.object(runner, "AUDIT", audit), patch.object(runner.subprocess, "Popen", FakeProcess), \
                    patch.object(runner, "ports_closed", closed_dynamic_port), patch.dict(os.environ, {"TESSERA_UNUSED": "NOT_COPIED"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                assert runner.execute(args, operator, primitives, inputs) == 0
                assert not ram.exists()
                watchdog = json.loads((audit / "workload1-watchdog.json").read_text())
                assert watchdog["ownedProcessStopped"] and watchdog["privateRamRemoved"]
                assert watchdog["finiteEvidencePreserved"] and not watchdog["automaticReplay"]
                try:
                    runner.execute(args, operator, primitives, inputs)
                except ValueError:
                    pass
                else:
                    raise AssertionError
            checks.extend(["OWNED_FAKE_CHILD_PERSISTENCE_AND_RAM_REMOVAL", "REPLAY_REJECTED_BEFORE_PROCESS"])
        for broken in ("missing", "malformed"):
            ram.mkdir(mode=0o700)
            fixture()
            if broken == "missing":
                (ram / "finalization.json").unlink()
            else:
                (ram / "run-evidence.json").write_text("not JSON")
            with tempfile.TemporaryDirectory(dir="/dev/shm", prefix="workload-partial-check-") as temporary:
                with patch.object(runner, "AUDIT", Path(temporary)), patch.object(runner.subprocess, "Popen", FakeProcess), \
                        patch.object(runner, "ports_closed", closed_dynamic_port), contextlib.redirect_stdout(io.StringIO()):
                    assert runner.execute(args, operator, primitives, inputs) == 1
                watchdog = json.loads((Path(temporary) / "workload1-watchdog.json").read_text())
                failure = json.loads((Path(temporary) / "workload1-evidence.json").read_text())
                assert watchdog["privateRamRemoved"] and not ram.exists()
                assert not watchdog["finiteEvidencePreserved"] and watchdog["failure"] == "RAM_EVIDENCE_UNPROVED"
                assert failure == {"available": False, "failure": "EVIDENCE_PROJECTION_FAILED"}
        checks.append("MISSING_AND_MALFORMED_EVIDENCE_STILL_CLEAN_OWNED_RAM")
        ram.mkdir(mode=0o700)
        fixture()
        save = primitives._save

        def evidence_write_fails(path, value):
            if path.name == "workload1-evidence.json":
                raise OSError("OFFLINE_SIMULATED_STORAGE_FAILURE")
            return save(path, value)

        with tempfile.TemporaryDirectory(dir="/dev/shm", prefix="workload-storage-check-") as temporary:
            with patch.object(runner, "AUDIT", Path(temporary)), patch.object(runner.subprocess, "Popen", FakeProcess), \
                    patch.object(primitives, "_save", evidence_write_fails), \
                    patch.object(runner, "ports_closed", closed_dynamic_port), contextlib.redirect_stdout(io.StringIO()):
                assert runner.execute(args, operator, primitives, inputs) == 1
            watchdog = json.loads((Path(temporary) / "workload1-watchdog.json").read_text())
            assert watchdog["privateRamRemoved"] and not watchdog["finiteEvidencePreserved"] and not ram.exists()
            assert watchdog["failure"] == "RAM_EVIDENCE_OR_CLEANUP_UNPROVED"
        checks.append("PRESERVATION_WRITE_FAILURE_STILL_CLEANS_OWNED_RAM")
        signals = []

        class HangingProcess:
            def __init__(self, _command, **kwargs):
                assert kwargs["start_new_session"] is True
                reader, self.writer = os.pipe()
                self.stdout, self.returncode, self.pid, self.waits = os.fdopen(reader, "rb", buffering=0), None, 1234567, 0
                guards.callback(os.close, self.writer)

            def poll(self):
                return self.returncode

            def wait(self, timeout):
                self.waits += 1
                assert timeout == 5
                if self.waits == 1:
                    raise runner.subprocess.TimeoutExpired("offline-fake", timeout)
                self.returncode = -9
                return self.returncode

        with tempfile.TemporaryDirectory(dir="/dev/shm", prefix="workload-deadline-check-") as temporary:
            clock = iter((0, 751, 752))
            with patch.object(runner, "AUDIT", Path(temporary)), patch.object(runner.subprocess, "Popen", HangingProcess), \
                    patch.object(runner.time, "monotonic", lambda: next(clock)), \
                    patch.object(runner.os, "killpg", lambda pid, sig: signals.append((pid, sig))), \
                    patch.object(runner, "ports_closed", lambda port: port is None), contextlib.redirect_stdout(io.StringIO()):
                assert runner.execute(args, operator, primitives, inputs) == 1
            watchdog = json.loads((Path(temporary) / "workload1-watchdog.json").read_text())
            assert signals == [(1234567, signal.SIGTERM), (1234567, signal.SIGKILL)]
            assert watchdog["ownedProcessStopped"] and watchdog["failure"] == "EXTERNAL_750S_DEADLINE"
            assert not watchdog["finalReceiptPersisted"] and not watchdog["finiteEvidencePreserved"]
        checks.append("OWNED_DEADLINE_TERM_KILL_AND_UNKNOWN_EVIDENCE")
        assert not attempts
        checks.append("NO_NETWORK_LISTENER_SDK_OR_ACTUAL_OPERATOR")
    return {"schema": "tessera.offline_workload_runner.v1", "status": "PASS", "checks": checks,
            "check_count": len(checks), "actual_processes": 0, "actual_claims": 0, "network_calls": len(attempts)}


def main():
    signal.signal(signal.SIGALRM, lambda *_args: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(25)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run()
    except BaseException:
        result = {"schema": "tessera.offline_workload_runner.v1", "status": "FAIL", "reason": "OFFLINE_CHECK_FAILED"}
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
