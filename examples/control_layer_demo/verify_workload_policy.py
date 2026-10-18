"""Offline canonical-proxy check for strict workload read policy enforcement.

Runs the real FastAPI ASGI lifespan and ``/mcp/control`` route with synthetic
SQLite policy snapshot data and a fake Streamable HTTP upstream. It opens no
listener, performs no network/provider/model calls, and stores temporary audit
data only in verified tmpfs.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import signal
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import yaml


class OfflineCheckError(Exception):
    """A bounded offline acceptance check failed."""


def require(condition: Any) -> None:
    if not condition:
        raise OfflineCheckError


def _snapshot_cache(cache_path: Path, policy_id: str, yaml_text: str, tenant: str,
                    endpoint: str) -> str:
    from tessera.cloud_sync import snapshot_digest

    items = [{"policy_id": policy_id, "enabled": True, "yaml_text": yaml_text}]
    digest = snapshot_digest(items)
    now = time.time()
    with sqlite3.connect(cache_path) as conn:
        conn.execute("""CREATE TABLE policies (
            policy_id TEXT PRIMARY KEY, pack_id TEXT, enabled INTEGER, priority INTEGER,
            action_summary TEXT, match_summary TEXT, yaml_text TEXT, fetched_at_unix INTEGER NOT NULL
        )""")
        conn.execute("""CREATE TABLE cloud_snapshot (
            singleton INTEGER PRIMARY KEY, metadata_json TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?)",
                     (policy_id, "offline", 1, 100, "allow", "exact-target", yaml_text, int(now)))
        meta = {"tenant_id": tenant, "endpoint": endpoint,
                "snapshot_digest": digest, "fetched_at": now}
        conn.execute("INSERT INTO cloud_snapshot VALUES (1,?)", (json.dumps(meta),))
    return digest


def _verify_audit_chain(db_path: Path, tenant: str) -> list[dict[str, Any]]:
    from tessera.audit.chain import HashChain

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT payload_json, prev_event_hash, event_hash FROM audit_events "
            "WHERE scope=? ORDER BY seq", (tenant,)
        ).fetchall()
    events = [json.loads(row[0]) for row in rows]
    require(len(events) >= 2)
    previous = ""
    for event, row in zip(events, rows, strict=True):
        require(event.get("prevEventHash", "") == previous)
        require(event.get("prevEventHash", "") == row[1])
        require(event.get("eventHash") == row[2] and HashChain.verify_event_hash(event))
        previous = event["eventHash"]
    return events


async def _exercise(app: Any, token: str, tool_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calls = [
        ("allowed", {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc",
                      "branch": "main", "job_id": "84"}),
        ("blocked", {"operation": "amplify.GetJob", "app_id": "d3s9n6ffim4722",
                      "branch": "main", "job_id": "60"}),
        ("bad-operation", {"operation": "amplify.StartJob", "app_id": "d2nxbpvv53qeqc",
                            "branch": "main", "job_id": "84"}),
        ("bad-branch", {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc",
                         "branch": "feature", "job_id": "84"}),
        ("bad-job", {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc",
                      "branch": "main", "job_id": "85"}),
        ("missing-operation", {"app_id": "d2nxbpvv53qeqc", "branch": "main", "job_id": "84"}),
    ]
    traces: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://offline.invalid") as client:
            for label, arguments in calls:
                request_id, use_id = f"offline-{label}-request", f"offline-{label}-use"
                body = {"jsonrpc": "2.0", "id": label, "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments,
                                   "_meta": {"request_id": request_id, "tool_use_id": use_id}}}
                response = await client.post("/mcp/control", json=body,
                    headers={"Authorization": f"Bearer {token}"})
                require(response.status_code == 200)
                wire = response.json()
                audit_id = response.headers.get("x-tessera-audit-event-id")
                if not audit_id:
                    result = wire.get("result", {})
                    error = wire.get("error", {})
                    audit_id = (result.get("_meta", {}).get("tessera_audit_event_id")
                                or error.get("data", {}).get("_meta", {}).get("tessera_audit_event_id"))
                require(isinstance(audit_id, str) and audit_id)
                traces.append({"request_id": request_id, "tool_use_id": use_id,
                               "tool_name": tool_name, "arguments": arguments,
                               "audit_event_id": audit_id})
            contacts.extend(app.state.streamable_http_clients["control"].contacts)
    return traces, contacts


def run() -> dict[str, Any]:
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    mounts = Path("/proc/mounts").read_text().splitlines()
    require(any(row.split()[1:3] == ["/dev/shm", "tmpfs"] for row in mounts))  # noqa: S108 - verified RAM

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise OfflineCheckError

    def provider_tripwire(frame: Any, event: str, _arg: Any) -> None:
        module = frame.f_globals.get("__name__", "")
        name = frame.f_code.co_name
        if event == "call" and (
            (module.startswith(("boto3", "botocore", "aiobotocore", "aioboto3"))
             and name in {"client", "resource", "create_client", "get_credentials",
                          "get_frozen_credentials", "_make_api_call"})
            or (module.startswith(("tessera.cloud_sync", "tessera.audit.cloud_uploader"))
                and name in {"fetch_and_cache", "upload", "send"})
            or (module.startswith(("anthropic", "openai", "google.genai"))
                and name in {"create", "create_and_stream", "generate_content"})
        ):
            forbidden()

    demo_dir = Path(__file__).resolve().parent
    old_path = list(sys.path)
    sys.path.insert(0, str(demo_dir))
    policy_path = demo_dir / "workload-read-allow.yaml"
    policy_bytes = policy_path.read_bytes()
    policy_doc = yaml.safe_load(policy_bytes)
    require(isinstance(policy_doc, dict) and policy_doc.get("id") == "workload-tessera-read")
    policy_text = policy_bytes.decode("utf-8")
    policy_hash = hashlib.sha256(policy_bytes).hexdigest()
    tenant, token = "offline-workload-tenant", "offline-workload-token-000000000000"
    endpoint = "https://offline-policy.invalid"
    policy_id = policy_doc["id"]
    calls: list[dict[str, Any]] = []

    class FakeStreamableHttpUpstream:
        def __init__(self, **_kwargs: Any) -> None:
            self.contacts: list[dict[str, Any]] = []

        async def __aenter__(self) -> FakeStreamableHttpUpstream:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def forward(self, body: dict[str, Any], *, session_scope: str = "default") -> dict[str, Any]:
            require(session_scope and body.get("method") == "tools/call")
            params = body.get("params", {})
            meta = params.get("_meta", {})
            arguments = dict(params.get("arguments", {}))
            contact = {"request_id": meta.get("request_id"),
                       "tool_use_id": meta.get("tool_use_id"),
                       "tool_name": params.get("name"),
                       "argument_digest": hashlib.sha256(json.dumps(
                           arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                       **{key: arguments.get(key) for key in ("operation", "app_id", "branch", "job_id")}}
            self.contacts.append(contact)
            calls.append(arguments)
            result = {"content": [{"type": "text", "text": "synthetic GetJob result"}],
                      "structuredContent": {"job": {"summary": {"status": "SUCCEED"}}},
                      "isError": False}
            return {"jsonrpc": "2.0", "id": body["id"], "result": result}

    async def execute() -> dict[str, Any]:
        import orchestrator
        from orchestrator import evidence_correlations

        from tessera import proxy
        from tessera.config import TesseraConfig
        from tessera.integrations.streamable_http import upstream as streamable_module

        require(proxy.__file__ == str(demo_dir.parents[1] / "tessera" / "proxy.py"))
        require(orchestrator.__file__ == str(demo_dir / "orchestrator.py"))

        with tempfile.TemporaryDirectory(prefix="tessera-workload-policy-", dir="/dev/shm") as raw:
            ram = Path(raw)
            require(os.stat(raw).st_mode & 0o077 == 0)
            policies = ram / "policies"
            policies.mkdir(mode=0o700)
            (policies / "local-default-block.yaml").write_text(
                "id: offline-default-block\nname: Block unmatched calls\npriority: -1000\n"
                "match:\n  upstream: control\n  tool: '*'\naction: block\nreason: no hosted exact match\n",
                encoding="utf-8")
            cache = ram / "policy-cache.db"
            audit = ram / "audit.db"
            digest = _snapshot_cache(cache, policy_id, policy_text, tenant, endpoint)
            env = {
                "TESSERA_BEARER_TOKENS": f"{tenant}:{token}",
                "TESSERA_POLICY_LOADER": "tessera.policy.cloud_loader:CloudBackedPolicyLoader",
                "TESSERA_CLOUD_STRICT": "1", "TESSERA_CLOUD_TENANT": tenant,
                "TESSERA_CLOUD_ENDPOINT": endpoint, "TESSERA_EXPECTED_POLICY_ID": policy_id,
                "TESSERA_EXPECTED_POLICY_SHA256": policy_hash,
                "TESSERA_EXPECTED_SNAPSHOT": digest, "TESSERA_POLICY_CACHE": str(cache),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            upstream_patch = patch.object(streamable_module, "StreamableHttpUpstream",
                                          FakeStreamableHttpUpstream)
            config = TesseraConfig.model_validate({
                "deployment_id": tenant,
                "auth": {"type": "bearer"},
                "audit": {"sink": "sqlite", "path": str(audit), "payload_mode": "full"},
                "policies": {"dir": str(policies), "reload": "none", "mode": "enforcement",
                             "default_action": "block"},
                "intelligence": {"enabled": False},
                "upstreams": [{"name": "control", "url": "http://offline.invalid/mcp",
                               "kind": "mcp_streamable_http", "request_timeout_s": 2}],
            })
            try:
                with patch.dict(os.environ, env, clear=False), upstream_patch:
                    for name in tuple(os.environ):
                        if name.startswith("TESSERA_") and name not in env:
                            os.environ.pop(name, None)
                    with contextlib.ExitStack() as guards:
                        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                                       "socket.socket.bind", "socket.socket.listen",
                                       "socket.create_connection", "socket.getaddrinfo"):
                            guards.enter_context(patch(target, forbidden))
                        sys.setprofile(provider_tripwire)
                        guards.callback(sys.setprofile, None)
                        app = proxy.create_app(config)
                        traces, contacts = await _exercise(app, token, "read_workload_status")
                require(len(calls) == 1)
                require(calls[0] == {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc",
                                     "branch": "main", "job_id": "84"})
                events = _verify_audit_chain(audit, tenant)
                corr = evidence_correlations(audit, tenant, {"tool_trace": traces}, {"contacts": contacts})
                rows = corr["correlations"]
                require(len(rows) == 6 and [row["decision"] for row in rows] ==
                        ["allow", "block", "block", "block", "block", "block"])
                require([row["upstream_contacts"] for row in rows] == [1, 0, 0, 0, 0, 0])
                require(rows[0]["policy_id"] == policy_id)
                require(rows[0]["normalized_operation"] == "amplify.GetJob")
                require(rows[0]["target"] == {"app_id": "d2nxbpvv53qeqc", "branch": "main", "job_id": "84"})
                require(corr["denied_contacts"] == 0 and corr["allow_and_block_observed"])
                snapshot_verified = app.state.loader.state().get("activation", {}).get("verified") is True
                require(snapshot_verified)
                for mutation in (
                    {"tool_use_id": "wrong-use"},
                    {"tool_name": "different_tool"},
                    {"argument_digest": "0" * 64},
                    {"job_id": "85"},
                ):
                    try:
                        evidence_correlations(audit, tenant, {"tool_trace": traces},
                                              {"contacts": [{**contacts[0], **mutation}]})
                    except ValueError:
                        pass
                    else:
                        raise OfflineCheckError
                return {"status": "PASS", "policy_id": policy_id, "policy_sha256": policy_hash,
                        "strict_snapshot_verified": snapshot_verified,
                        "decision_count": len(rows), "allowed_contacts": rows[0]["upstream_contacts"],
                        "blocked_contacts": sum(row["upstream_contacts"] for row in rows[1:]),
                        "blocked_requests": len(rows) - 1, "audit_events": len(events),
                        "hash_chain_verified": True, "exact_tool_arguments_correlated": True}
            finally:
                pass

    try:
        with contextlib.ExitStack() as guards:
            for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.socket.bind",
                           "socket.socket.listen", "socket.create_connection", "socket.getaddrinfo"):
                guards.enter_context(patch(target, forbidden))
            sys.setprofile(provider_tripwire)
            guards.callback(sys.setprofile, None)
            result = asyncio.run(execute())
    finally:
        sys.path[:] = old_path
    return {"schema": "tessera.offline_workload_policy.v1", **result,
            "network_calls": 0, "provider_calls": 0, "model_calls": 0,
            "listeners_opened": 0, "evidence_kind": "synthetic_offline"}


def main() -> int:
    signal.signal(signal.SIGALRM, lambda *_args: (_ for _ in ()).throw(OfflineCheckError()))
    signal.alarm(30)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run()
    except BaseException:
        result = {"schema": "tessera.offline_workload_policy.v1", "status": "FAIL",
                  "reason_code": "OFFLINE_CANONICAL_POLICY_CHECK_FAILED",
                  "network_calls": "unknown", "provider_calls": "unknown",
                  "model_calls": "unknown", "listeners_opened": "unknown",
                  "evidence_kind": "synthetic_offline"}
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
