"""Credential-free security boundaries for the fixed external-client example."""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from tessera.audit.chain import HashChain
from tessera.config import load_config_from_dict
from tessera.errors import UnauthorizedError
from tessera.proxy import create_app

DEMO = Path(__file__).resolve().parents[1] / "examples/external_client_demo"
# Public synthetic strings are test fixtures, never usable live credentials.
TOKENS = {"opencode": "o" * 43, "codex": "c" * 43}
ALLOWED = {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc", "branch": "main", "job_id": "84"}
BLOCKED = {**ALLOWED, "app_id": "d3s9n6ffim4722", "job_id": "60"}


@pytest.fixture()
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(DEMO))
    for name in list(__import__("os").environ):
        if name.startswith("TESSERA_"):
            monkeypatch.delenv(name)
    auth = importlib.import_module("external_client_auth")
    monkeypatch.setattr(auth, "_BINDING", None)
    auth.configure(tenant="synthetic_tenant", clients={name: hashlib.sha256(value.encode()).hexdigest()
        for name, value in TOKENS.items()}, expires_at=int(time.time()) + 600)
    return auth, importlib.import_module("attachment")


def request(headers):
    return Request({"type": "http", "headers": [(name.lower().encode(), value.encode()) for name, value in headers]})


def test_distinct_principals_share_only_tenant_and_ignore_spoofed_header(modules):
    auth, _ = modules
    verifier = auth.DigestBearerAuthenticator("synthetic_tenant")
    for name, token in TOKENS.items():
        identity = verifier.authenticate(request([("Authorization", "Bearer " + token), ("X-Principal-Id", "admin")]))
        assert identity.principal_id == name
        assert identity.scope == "synthetic_tenant"


@pytest.mark.parametrize("headers", [[], [("Authorization", "Bearer " + "x" * 43)],
    [("Authorization", "Bearer " + TOKENS["opencode"])] * 2,
    [("Authorization", "Bearer " + hashlib.sha256(TOKENS["opencode"].encode()).hexdigest())]])
def test_missing_duplicate_unknown_or_verifier_itself_cannot_authenticate(modules, headers):
    auth, _ = modules
    with pytest.raises(UnauthorizedError):
        auth.DigestBearerAuthenticator("synthetic_tenant").authenticate(request(headers))


def test_expiry_boundary_rejects_previously_valid_token(modules, monkeypatch):
    auth, _ = modules
    verifier = auth.DigestBearerAuthenticator("synthetic_tenant")
    monkeypatch.setattr(auth.time, "time", lambda: verifier.expires_at)
    with pytest.raises(UnauthorizedError):
        verifier.authenticate(request([("Authorization", "Bearer " + TOKENS["opencode"])]))


@pytest.fixture()
def proxy(modules, monkeypatch, tmp_path):
    _, attachment = modules
    monkeypatch.setenv("TESSERA_AUTHENTICATOR", "external_client_auth:DigestBearerAuthenticator")
    cfg = load_config_from_dict({"deployment_id": "synthetic_tenant", "log_level": "CRITICAL",
        "audit": {"sink": "sqlite", "path": str(tmp_path / "audit.db"), "payload_mode": "full"},
        "policies": {"dir": str(DEMO), "reload": "none", "mode": "enforcement", "default_action": "block"},
        "upstreams": [{"name": "control", "url": "http://synthetic.invalid"}],
        "metrics": {"enabled": False}, "intelligence": {"enabled": False}})
    app = create_app(cfg)

    class Ledger:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    ledger, forwarded = Ledger(), []

    def upstream(req):
        value = json.loads(req.content)
        forwarded.append(value)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": value["id"], "result": {"content": []}})

    attached = attachment.Attachment(app, "synthetic-run", ledger)
    with TestClient(attached) as client:
        app.state.http_clients["control"] = httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://synthetic.invalid")
        yield client, forwarded, ledger, tmp_path / "audit.db"


def call(client, *, name="opencode", args=None, meta=None, raw=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "read_workload_status", "arguments": ALLOWED if args is None else args}}
    if meta is not None:
        body["params"]["_meta"] = meta
    return client.post("/mcp/control", content=raw if raw is not None else json.dumps(body),
                       headers={"Authorization": "Bearer " + TOKENS[name], "Content-Type": "application/json"})


@pytest.mark.parametrize("meta", [{"external_client": "codex"}, {"request_id": "forged"},
    {"tessera_intent": "unauthorized instruction"}, {"progressToken": True}, {"progressToken": "x" * 43}])
def test_spoofed_metadata_is_rejected_before_canonical_dispatch(proxy, meta):
    client, forwarded, ledger, _ = proxy
    assert call(client, meta=meta).status_code == 400
    assert forwarded == []
    assert ledger.rows == []


@pytest.mark.parametrize("args", [{**ALLOWED, "job_id": "85"}, {**ALLOWED, "operation": "amplify.StartJob"},
    {**ALLOWED, "extra": "unapproved"}, {}])
def test_outside_two_job_contract_has_zero_forwarding(proxy, args):
    client, forwarded, ledger, _ = proxy
    assert call(client, args=args).status_code == 400
    assert forwarded == []
    assert ledger.rows == []


def test_default_deny_has_canonical_audit_and_zero_upstream_contacts(proxy):
    client, forwarded, ledger, audit = proxy
    response = call(client, args=BLOCKED)
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert forwarded == []
    assert len(ledger.rows) == 1
    # The response contains the ID before async persistence completes. Drain
    # after this test's final request, as production shutdown does; do not race
    # the queue or force synchronous emission merely to make the test pass.
    client.portal.call(client.app.app.state.audit_queue.drain)
    with sqlite3.connect(audit) as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM audit_events WHERE scope=? ORDER BY seq", ("synthetic_tenant",))]
    previous = ""
    for event in events:
        assert event["tenantId"] == "synthetic_tenant"
        assert event["prevEventHash"] == previous and HashChain.verify_event_hash(event)
        previous = event["eventHash"]
    decisions = [event for event in events if event["eventType"] == "decision"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["decision"] == "block"
    assert decisions[0]["payload"]["principal_id"] == "opencode"
    assert ledger.rows[0]["audit_event_id"] == decisions[0]["eventId"]


def test_allowed_call_identity_and_two_call_limit_are_independent(proxy):
    client, forwarded, ledger, _ = proxy
    assert call(client, name="opencode").status_code == 200
    assert call(client, name="opencode", args=BLOCKED).status_code == 200
    assert call(client, name="opencode").status_code == 429
    assert call(client, name="codex").status_code == 200
    assert [row["params"]["_meta"]["external_client"] for row in forwarded] == ["opencode", "codex"]
    assert len({row["request_id"] for row in ledger.rows}) == 3


def test_duplicate_json_arguments_cannot_override_admitted_call(proxy):
    client, forwarded, ledger, _ = proxy
    raw = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"arguments":{},"arguments":{}}}'
    assert call(client, raw=raw).status_code == 400
    assert forwarded == [] and ledger.rows == []


def test_payload_rejects_changed_local_source_and_raw_credential_key(modules):
    payload = importlib.import_module("task_payload")
    regression = importlib.import_module("startup_regression")
    manifest = dict.fromkeys(payload.CANONICAL, "a" * 64) | payload.local_source_hashes()
    plan = regression.synthetic_plan(manifest)
    assert set(payload.build_payload(plan)["files"]) == set(payload.FILES)
    plan["sourceSha256"][payload.PREFIX + "attachment.py"] = "0" * 64
    with pytest.raises(ValueError):
        payload.build_payload(plan)
    plan = regression.synthetic_plan(manifest)
    plan["rawBearerToken"] = "forbidden"
    with pytest.raises(ValueError):
        payload.build_payload(plan)


@pytest.mark.parametrize("missing", ["server", "payload", "failure", "portsClosed"])
def test_health_alone_never_passes_startup_without_real_cleanup_receipts(modules, missing):
    regression = importlib.import_module("startup_regression")
    records = [{"synthetic_final": True, "health200": True, "ports_closed": True, "owned_ram_absent": True,
                "synthetic_sts_calls": 1, "synthetic_amplify_calls": 0, "real_sdk_calls": 0},
               {"schema": "tessera.external_client_server_receipt.v1", "failure": None, "serverStopped": True,
                "portsClosed": True, "privateRamRemoved": True},
               {"schema": "cloudmorph.two_client_payload_cleanup.v1", "ownedRootRemoved": True, "samePythonPid": True}]
    assert regression.case_passed("synthetic_startup", 0, records)
    if missing == "server":
        records.pop(1)
    elif missing == "payload":
        records.pop(2)
    else:
        records[1].pop(missing)
    assert not regression.case_passed("synthetic_startup", 0, records)


@pytest.fixture()
def observed_processes(modules, monkeypatch):
    regression = importlib.import_module("startup_regression")
    real_popen, started = regression.subprocess.Popen, []

    def observe(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(regression.subprocess, "Popen", observe)
    return regression, started


@pytest.mark.parametrize("stream", [1, 2])
def test_streaming_output_overflow_kills_and_reaps_owned_process(observed_processes, stream):
    regression, started = observed_processes
    script = f"import os,time\nwhile True: os.write({stream},b'x'*4096)"
    with pytest.raises(regression.OutputLimitExceeded, match="LOCAL_CLI_.*_LIMIT_EXCEEDED"):
        regression.bounded_run([sys.executable, "-c", script], timeout=5, limit=1024)
    assert len(started) == 1 and started[0].returncode is not None
    assert started[0].stdout.closed and started[0].stderr.closed


@pytest.mark.parametrize("close_pipes", [False, True])
def test_streaming_timeout_kills_process_even_after_output_pipes_close(observed_processes, close_pipes):
    regression, started = observed_processes
    script = "import os,time;" + ("os.close(1);os.close(2);" if close_pipes else "") + "time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        regression.bounded_run([sys.executable, "-c", script], timeout=0.3)
    assert len(started) == 1 and started[0].returncode is not None
    assert started[0].stdout.closed and started[0].stderr.closed


def test_streaming_capture_drains_both_pipes_and_preserves_bytes_api(modules):
    regression = importlib.import_module("startup_regression")
    result = regression.bounded_run([sys.executable, "-c", "import os;os.write(1,b'a'*65536);os.write(2,b'b'*65536)"], timeout=5)
    assert result.returncode == 0
    assert result.stdout == b"a" * 65536 and result.stderr == b"b" * 65536


@pytest.mark.parametrize("timed_out", [False, True])
def test_cli_interruption_still_removes_exact_owned_container(modules, monkeypatch, timed_out):
    regression = importlib.import_module("startup_regression")
    isolated = object.__new__(regression.IsolatedDocker)
    isolated.image_id, isolated.owned_containers_absent = "sha256:" + "1" * 64, True
    calls = []

    def interrupted_then_cleanup(args, timeout):
        calls.append(args)
        if args[0] == "run":
            if timed_out:
                raise subprocess.TimeoutExpired("owned local CLI", timeout)
            raise regression.OutputLimitExceeded("LOCAL_CLI_STDOUT_LIMIT_EXCEEDED")
        if args[:2] == ["container", "inspect"]:
            return subprocess.CompletedProcess([], 1, b"[]\n",
                ("Error response from daemon: No such container: " + args[2] + "\n").encode())
        return subprocess.CompletedProcess([], 0, b"healthy\n", b"")

    monkeypatch.setattr(isolated, "call", interrupted_then_cleanup)
    with pytest.raises(subprocess.SubprocessError):
        isolated.execute("unused synthetic command")
    owned = next(arg.removeprefix("--name=") for arg in calls[0] if arg.startswith("--name="))
    assert calls[1] == ["rm", "-f", owned]
    assert calls[2] == ["container", "inspect", owned]
    assert calls[3] == ["info", "--format", "{{.ServerVersion}}"]
    assert isolated.owned_containers_absent is True


def test_unpublished_image_id_is_exact_and_never_substitutes_for_manifest_digest(modules, monkeypatch):
    regression = importlib.import_module("startup_regression")
    local_id, digest = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    response = [{"Id": local_id, "RepoDigests": []}]
    monkeypatch.setattr(regression.IsolatedDocker, "call", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 0, json.dumps(response).encode(), b""))
    assert regression.IsolatedDocker("synthetic:local", None, "docker", image_id=local_id).image_id == local_id
    with pytest.raises(ValueError):
        regression.IsolatedDocker("synthetic:local", None, "docker", image_id=digest)
    with pytest.raises(ValueError):
        regression.IsolatedDocker("synthetic:local", digest, "docker")
    with pytest.raises(ValueError):
        regression.IsolatedDocker("synthetic:local", digest, "docker", image_id=local_id)
    response[0]["RepoDigests"] = ["registry.invalid/example@" + digest]
    assert regression.IsolatedDocker("synthetic:local", digest, "docker").image_id == local_id
