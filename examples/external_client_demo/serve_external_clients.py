"""Bounded attachment server for two separately authorized real external clients.

--check is local source/config validation only. --serve requires an actual pinned
plan, source manifest, digest-only client bindings and approved AWS task role.
No model client, OAuth grant, publication, ECS creation or hosted upload is here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import stat
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[1]
sys.path.insert(0, str(RUNTIME))
POLICY_SHA = "c201005df8d1d0985099515a7419e4b3ada539f3704b2a4eb79ccca240b44500"
SOURCE_FILES = ("tessera/proxy.py", "tessera/mcp_http.py", "tessera/auth/base.py", "tessera/auth/bearer.py",
                "tessera/config.py", "tessera/policy/engine.py", "tessera/policy/loader.py", "tessera/policy/schema.py",
                "tessera/audit/chain.py", "tessera/audit/canonical_json.py", "tessera/audit/emitter.py",
                "tessera/audit/sinks/sqlite.py", "tessera/integrations/streamable_http/upstream.py",
                *["examples/external_client_demo/" + name for name in (
                    "external_client_auth.py", "attachment.py", "deployment_adapter.py", "serve_external_clients.py", "external-read-allow.yaml")])


def need(value):
    if not value:
        raise ValueError("EXTERNAL_PACKET_REJECTED")


def read(path, expected=None, limit=1_048_576):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) <= limit and (expected is None or hashlib.sha256(raw).hexdigest() == expected))
        return raw
    finally:
        os.close(descriptor)


def source_hashes():
    import tessera
    canonical_root = Path(tessera.__file__).resolve().parent.parent
    return {name: hashlib.sha256(read(canonical_root / name if name.startswith("tessera/") else HERE / Path(name).name)).hexdigest()
            for name in SOURCE_FILES}


def validate(plan, expected_sources):
    keys = {"schema", "runId", "tenant", "publicOrigin", "listenHost", "listenPort", "adapterPort",
            "maxSeconds", "expiresAtEpoch", "clientTokenSha256", "expectedCallerArnPrefix", "sourceSha256"}
    need(type(plan) is dict and set(plan) == keys and plan["schema"] == "tessera.external_client_plan.v1")
    need(type(plan["runId"]) is str and plan["runId"].startswith("external2-"))
    value = uuid.UUID(plan["runId"][10:])
    need(value.version == 4 and plan["runId"] == "external2-" + str(value))
    need(type(plan["tenant"]) is str and re.fullmatch(r"[a-z0-9_-]{1,64}", plan["tenant"]))
    origin = urlsplit(plan["publicOrigin"])
    need(origin.scheme == "https" and origin.hostname and origin.path in {"", "/"}
         and not origin.username and not origin.password and not origin.query and not origin.fragment)
    need(plan["listenHost"] in {"127.0.0.1", "0.0.0.0"}  # noqa: S104 -- explicitly approved ECS private ingress only
         and all(type(plan[k]) is int and 1024 <= plan[k] <= 65535 for k in ("listenPort", "adapterPort"))
         and plan["listenPort"] != plan["adapterPort"])
    need(type(plan["maxSeconds"]) is int and 60 <= plan["maxSeconds"] <= 900
         and type(plan["expiresAtEpoch"]) is int
         and time.time() + plan["maxSeconds"] <= plan["expiresAtEpoch"] <= time.time() + 1800)
    clients = plan["clientTokenSha256"]
    need(type(clients) is dict and set(clients) == {"opencode", "codex"}
         and all(type(v) is str and re.fullmatch(r"[a-f0-9]{64}", v) for v in clients.values())
         and len(set(clients.values())) == 2)
    need(type(plan["expectedCallerArnPrefix"]) is str
         and re.fullmatch(r"arn:aws:sts::237509402889:assumed-role/[A-Za-z0-9+=,.@_-]{1,64}/", plan["expectedCallerArnPrefix"]))
    need(plan["sourceSha256"] == expected_sources)


def closed(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def startup_guard(plan=None):
    # These are injectable extension points or raw-credential sources that would
    # change this reviewed fixed attachment; fail instead of silently inheriting.
    need(not any(name.startswith("TESSERA_") for name in os.environ))
    need(any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in Path("/proc/mounts").read_text().splitlines()))  # noqa: S108 -- verify required tmpfs
    if plan is None:
        return None
    root = Path("/dev/shm") / ("cm-" + plan["runId"])  # noqa: S108 -- required owned private tmpfs
    need(not os.path.lexists(root) and closed(plan["listenPort"]) and closed(plan["adapterPort"]))
    return root


def serve(plan):
    root = startup_guard(plan)
    import secrets

    import boto3
    import deployment_adapter as adapter
    import uvicorn
    from attachment import Attachment, project_evidence
    from botocore.config import Config
    from external_client_auth import configure

    from tessera.config import load_config_from_dict
    from tessera.proxy import create_app

    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    server = upstream = worker = ingress = None
    request_workers = {"active": 0}
    request_lock = threading.Lock()
    stopped, removed, ports = False, False, False
    evidence, failure = {"available": False}, None
    started = time.monotonic()

    def stop(_signum, _frame):
        if server is None:
            raise TimeoutError("SERVER_START_DEADLINE")
        server.should_exit = True

    prior_alarm = signal.signal(signal.SIGALRM, stop)
    # Uvicorn restores and replays captured signals before returning. Keep our
    # graceful handler installed through evidence collection and RAM cleanup,
    # including when Python is a child of the ECS container init process.
    prior_term = signal.signal(signal.SIGTERM, stop)
    signal.setitimer(signal.ITIMER_REAL, plan["maxSeconds"])
    try:
        config = Config(region_name="us-east-1", retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=3)
        sts = boto3.Session(region_name="us-east-1").client("sts", config=config)
        try:
            identity = sts.get_caller_identity()
            need(identity.get("Account") == "237509402889"
                 and identity.get("Arn", "").startswith(plan["expectedCallerArnPrefix"]))
        finally:
            sts.close()
        configure(tenant=plan["tenant"], clients=plan["clientTokenSha256"], expires_at=plan["expiresAtEpoch"])
        internal = secrets.token_urlsafe(48)
        upstream = adapter.make_server(run_id=plan["runId"], ledger_path=root / "contacts.jsonl",
            port=plan["adapterPort"], token=internal, max_requests=2, workload_mode=True)
        original_handler = upstream.RequestHandlerClass

        class TrackedHandler(original_handler):
            def __init__(self, *args, **kwargs):
                with request_lock:
                    request_workers["active"] += 1
                try:
                    super().__init__(*args, **kwargs)
                finally:
                    with request_lock:
                        request_workers["active"] -= 1

        upstream.RequestHandlerClass = TrackedHandler
        worker = threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        worker.start()
        policy_dir = root / "policies"
        policy_dir.mkdir(mode=0o700)
        (policy_dir / "allow.yaml").write_bytes(read(HERE / "external-read-allow.yaml", POLICY_SHA))
        cfg = load_config_from_dict({"deployment_id": plan["tenant"], "log_level": "CRITICAL",
            "listen": {"host": plan["listenHost"], "port": plan["listenPort"], "allowed_origins": [plan["publicOrigin"].rstrip("/")]},
            "auth": {"type": "bearer"}, "audit": {"sink": "sqlite", "path": str(root / "audit.db"), "payload_mode": "full"},
            "policies": {"dir": str(policy_dir), "reload": "none", "mode": "enforcement", "default_action": "block"},
            "upstreams": [{"name": "control", "kind": "mcp_streamable_http", "url": f"http://127.0.0.1:{plan['adapterPort']}/mcp",
                "auth_header": "Bearer " + internal, "request_timeout_s": 40}], "intelligence": {"enabled": False}})
        os.environ["TESSERA_AUTHENTICATOR"] = "external_client_auth:DigestBearerAuthenticator"
        ingress = adapter.Ledger(root / "ingress.jsonl")
        app = create_app(cfg)
        attached = Attachment(app, plan["runId"], ingress)
        server = uvicorn.Server(uvicorn.Config(attached, host=plan["listenHost"], port=plan["listenPort"],
            log_config=None, log_level="critical", access_log=False, timeout_graceful_shutdown=10, timeout_keep_alive=5))
        print(json.dumps({"status": "SERVER_STARTING", "run_id": plan["runId"], "principals": ["opencode", "codex"],
                          "tenant_policy_shared": True, "credentials_emitted": False}), flush=True)
        server.run()
        stopped = not server.started or server.should_exit
        upstream.shutdown()
        worker.join(timeout=5)
        need(not worker.is_alive())
        wait_until = time.monotonic() + 45
        while request_workers["active"] and time.monotonic() < wait_until:
            time.sleep(0.05)
        need(request_workers["active"] == 0)
        evidence = project_evidence(audit_path=root / "audit.db", tenant=plan["tenant"], ingress=ingress.snapshot(),
                                    contacts=upstream.ledger.snapshot(), run_id=plan["runId"])
        if evidence["canonical_export_status"] != "ORIGINAL_UNCHANGED_VALIDATED":
            failure = "CANONICAL_EXPORT_UNPROVED"
    except BaseException:
        failure = "SERVER_OR_EVIDENCE_FAILED"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            os.environ.pop("TESSERA_AUTHENTICATOR", None)
            if upstream is not None:
                if worker is not None and worker.is_alive():
                    upstream.shutdown()
                    worker.join(timeout=5)
                upstream.server_close()
                upstream.ledger.close()
                upstream.aws_client.close()
            if ingress is not None:
                ingress.close()
            ports = closed(plan["listenPort"]) and closed(plan["adapterPort"])
            stopped = (server is None or server.should_exit or not server.started) and (worker is None or not worker.is_alive())
            stopped = stopped and request_workers["active"] == 0
            if ports and stopped:
                need(root.parent == Path("/dev/shm") and root.resolve() == root and not root.is_symlink()  # noqa: S108 -- exact owned bound
                     and root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700)
                shutil.rmtree(root)
                removed = not root.exists()
        finally:
            signal.signal(signal.SIGALRM, prior_alarm)
            signal.signal(signal.SIGTERM, prior_term)
    result = {"schema": "tessera.external_client_server_receipt.v1", "run_id": plan["runId"],
              "elapsedSeconds": round(time.monotonic() - started, 3), "failure": failure,
              "serverStopped": stopped, "portsClosed": ports, "privateRamRemoved": removed,
              "evidence": evidence, "automaticRetry": False}
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if failure is None and ports and stopped and removed and evidence.get("accepted") is True else 1


def main():
    try:
        # Parse mode separately because argparse treats leading '--' as options.
        mode = sys.argv[1]
        need(mode in {"--check", "--serve"})
        rest = argparse.ArgumentParser()
        rest.add_argument("--plan")
        rest.add_argument("--plan-sha256")
        args = rest.parse_args(sys.argv[2:])
        hashes = source_hashes()
        need(hashes["examples/external_client_demo/external-read-allow.yaml"] == POLICY_SHA)
        if args.plan:
            from attachment import pairs
            need(args.plan_sha256 is not None and re.fullmatch(r"[a-f0-9]{64}", args.plan_sha256))
            plan = json.loads(read(Path(args.plan), args.plan_sha256), object_pairs_hook=pairs)
            validate(plan, hashes)
        else:
            need(mode == "--check" and args.plan_sha256 is None)
            plan = None
        startup_guard(plan)
        if mode == "--check":
            print(json.dumps({"status": "LOCAL_COMPONENTS_VALID", "planValidated": plan is not None,
                              "remoteBindingsVerified": False, "SDKCalls": 0, "networkCalls": 2 if plan else 0,
                              "loopbackProbes": 2 if plan else 0, "externalNetworkCalls": 0, "listeners": 0,
                              "sourceSha256": hashes, "missingActualBindings": [] if plan else
                              ["runUUID", "tenant", "HTTPSOrigin", "approvedTaskRoleArnPrefix", "twoClientTokenDigests", "expiry"]}, sort_keys=True))
            return 0
        logging.disable(logging.CRITICAL)
        return serve(plan)
    except BaseException:
        print('{"status":"BLOCKED","errorCode":"EXTERNAL_PACKET_OR_SERVER_FAILED","executionPerformed":"UNKNOWN"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
