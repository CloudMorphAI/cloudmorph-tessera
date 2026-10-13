"""Generate loose, unsigned acceptance records using only fictional MCP tools.

Run from a development checkout with the existing FastAPI/httpx dependencies.
No listener, LLM, cloud API, real credentials, signing key or ZIP is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def deny_network() -> None:
    """MockTransport and TestClient need no IP sockets or DNS."""
    original = socket.socket.connect

    def connect(sock: socket.socket, address: Any) -> Any:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError("Synthetic pilot disables IP sockets")
        return original(sock, address)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Synthetic pilot disables DNS and network connections")

    socket.socket.connect = connect
    socket.socket.connect_ex = forbidden
    socket.getaddrinfo = forbidden
    socket.create_connection = forbidden


def run_engine(output: Path) -> int:
    deny_network()
    import httpx
    from fastapi.testclient import TestClient

    from tessera import __version__
    from tessera.audit.chain import HashChain
    from tessera.config import AuditConfig, ListenConfig, PoliciesConfig, TesseraConfig, UpstreamConfig
    from tessera.proxy import create_app

    policies = output / "policies"
    policies.mkdir()
    (policies / "block.yaml").write_text(
        'id: pilot-block-delete\nname: Block fictional deletes\n'
        'match:\n  upstream: pilot\n  tool: delete_record\n'
        'action: block\npriority: 20\nreason: Fictional deletion is outside the pilot scope\n', encoding="utf-8",
    )
    (policies / "allow.yaml").write_text(
        'id: pilot-allow-read\nname: Allow fictional read\n'
        'match:\n  upstream: pilot\n  tool: read_record\n'
        'action: allow\npriority: 10\n', encoding="utf-8",
    )
    config = TesseraConfig(
        listen=ListenConfig(allowed_origins=["https://pilot.example.invalid"]),
        audit=AuditConfig(path=str(output / "audit.db")),
        policies=PoliciesConfig(dir=str(policies), reload="none", mode="enforcement", default_action="block"),
        upstreams=[UpstreamConfig(name="pilot", url="https://synthetic.example.invalid/mcp",
                                  kind="mcp_streamable_http", request_timeout_s=5)],
    )
    contacts: list[dict[str, Any]] = []
    exchanges: list[dict[str, Any]] = []
    sessions: set[str] = set()
    records: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def journal(kind: str, data: dict[str, Any]) -> None:
        with (output / "journal.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": kind, **data}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def checkpoint(status: str = "running") -> None:
        temporary = output / "progress.json.tmp"
        temporary.write_text(json.dumps({"status": status, "cases": records, "checks": checks,
                                        "synthetic_tool_contacts": contacts, "upstream_exchanges": exchanges}, indent=2),
                             encoding="utf-8")
        temporary.replace(output / "progress.json")

    journal("start", {"scope": "offline synthetic, unsigned"})
    checkpoint()

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        session = request.headers.get("mcp-session-id")
        method = body.get("method")
        exchanges.append({"http_method": request.method, "method": method,
                          "id": body.get("id"), "synthetic_session": session,
                          "protocol": request.headers.get("mcp-protocol-version")})
        journal("upstream_exchange", exchanges[-1])
        if request.method == "DELETE":
            return httpx.Response(204)
        if method == "initialize":
            sid = f"synthetic-session-{len(sessions) + 1}"
            sessions.add(sid)
            return httpx.Response(200, headers={"Mcp-Session-Id": sid}, json={
                "jsonrpc": "2.0", "id": body["id"],
                "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fictional-pilot-upstream", "version": "1"}},
            })
        if session not in sessions or request.headers.get("mcp-protocol-version") != "2025-11-25":
            return httpx.Response(400, json={"error": "synthetic session or version missing"})
        if "id" not in body:
            return httpx.Response(202)
        if method == "tools/list":
            response = {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": "read_record", "description": "Read a fictional record", "inputSchema": {"type": "object"}},
                {"name": "delete_record", "description": "Delete a fictional record", "inputSchema": {"type": "object"}},
            ]}}
            unrelated = {"jsonrpc": "2.0", "id": "different-request", "result": {"tools": []}}
            data = f'data: {json.dumps(unrelated)}\n\ndata: {json.dumps(response)}\n\n'
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=data.encode())
        if method == "tools/call":
            contacts.append({"tool": body["params"]["name"], "session": session})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                           "result": {"content": [{"type": "text", "text": "Fictional record only"}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    original_client = httpx.AsyncClient

    def mock_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(upstream)
        return original_client(*args, **kwargs)

    headers = {"Authorization": "Bearer fictional_alpha_token_00000000",
               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}

    def check(name: str, passed: bool) -> None:
        checks.append({"name": name, "passed": bool(passed)})
        journal("check", checks[-1])
        checkpoint()

    with patch("httpx.AsyncClient", side_effect=mock_client):
        app = create_app(config)
        with TestClient(app) as client:
            def send(name: str, body: dict[str, Any], extra: dict[str, str] | None = None) -> httpx.Response:
                before = len(contacts)
                journal("case_started", {"case": name, "request": body})
                response = client.post("/mcp", headers={**headers, **(extra or {})}, json=body)
                data = response.json() if response.content else None
                records.append({"case": name, "request": body, "status": response.status_code,
                                "response": data, "synthetic_tool_contacts": len(contacts) - before})
                journal("case_completed", records[-1])
                checkpoint()
                return response

            init = send("downstream initialization", {"jsonrpc": "2.0", "id": "init", "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                   "clientInfo": {"name": "synthetic-pilot-runner", "version": "1"}}})
            check("downstream initialize is stateless", init.status_code == 200 and
                  init.json()["result"]["protocolVersion"] == "2025-11-25" and "mcp-session-id" not in init.headers)
            ack = send("initialized notification", {"jsonrpc": "2.0", "method": "notifications/initialized"})
            check("notification acknowledgement is empty202", ack.status_code == 202 and not ack.content)
            catalog = send("SSE discovery and response ID", {"jsonrpc": "2.0", "id": "catalog", "method": "tools/list"})
            data = catalog.json()
            check("discovery ignores unrelated SSE response", data.get("id") == "catalog" and
                  {t["name"] for t in data.get("result", {}).get("tools", [])} == {"pilot__read_record", "pilot__delete_record"})
            blocked = send("blocked fictional deletion", {"jsonrpc": "2.0", "id": "blocked", "method": "tools/call",
                           "params": {"name": "pilot__delete_record", "arguments": {"record": "fictional-001"}}})
            check("blocked delete makes zero contacts", blocked.json().get("result", {}).get("isError") is True
                  and blocked.json().get("result", {}).get("content", [{}])[0].get("text", "").startswith("POLICY_BLOCK\npolicy_id: pilot-block-delete\n")
                  and records[-1]["synthetic_tool_contacts"] == 0)
            allowed = send("allowed fictional read", {"jsonrpc": "2.0", "id": "allowed", "method": "tools/call",
                           "params": {"name": "pilot__read_record", "arguments": {"record": "fictional-001"}}})
            check("allowed read makes exactly one contact", allowed.status_code == 200
                  and not allowed.json().get("result", {}).get("isError") and records[-1]["synthetic_tool_contacts"] == 1)
            send("second principal fictional read", {"jsonrpc": "2.0", "id": "beta", "method": "tools/call",
                 "params": {"name": "pilot__read_record", "arguments": {}}},
                 {"Authorization": "Bearer fictional_beta_token_000000000"})
            check("principals use distinct upstream sessions", len(contacts) == 2 and
                  contacts[0]["session"] != contacts[1]["session"] and len(sessions) == 2)
            init_notices = [e for e in exchanges if e["method"] == "notifications/initialized"]
            check("each upstream session completes initialization", len(init_notices) == 2 and
                  all(e["protocol"] == "2025-11-25" for e in init_notices))
            invalid = send("untrusted browser origin", {"jsonrpc": "2.0", "id": "origin", "method": "tools/list"},
                           {"Origin": "https://untrusted.example.invalid"})
            check("untrusted Origin blocked403", invalid.status_code == 403)
            trusted = send("explicit trusted browser origin", {"jsonrpc": "2.0", "id": "trusted", "method": "tools/list"},
                           {"Origin": "https://pilot.example.invalid"})
            check("explicit trusted Origin accepted", trusted.status_code == 200 and trusted.json().get("id") == "trusted")
            invalid = send("unsupported protocol header", {"jsonrpc": "2.0", "id": "version", "method": "tools/list"},
                           {"MCP-Protocol-Version": "invalid"})
            check("unsupported protocol blocked400", invalid.status_code == 400)
            noauth = client.get("/mcp", headers={"Accept": "text/event-stream"})
            get = client.get("/mcp", headers={**headers, "Accept": "text/event-stream"})
            for name, response in (("unauthenticated GET", noauth), ("authenticated GET", get)):
                records.append({"case": name, "http_method": "GET", "status": response.status_code,
                                "response": response.json() if response.content else None,
                                "allow": response.headers.get("allow")})
                journal("case_completed", records[-1])
            check("GET stream is authenticated and explicitly unsupported", noauth.status_code == 401 and get.status_code == 405)
            events = list(app.state.sink.iter_events())

    heads: dict[str, str] = {}
    chain_ok = True
    for event in events:
        scope = event["tenantId"]
        chain_ok = chain_ok and HashChain.verify_event_hash(event) and event.get("prevEventHash", "") == heads.get(scope, "")
        heads[scope] = event["eventHash"]
    check("persisted audit hash chains verify", chain_ok and bool(events))
    decisions = {e["eventId"]: e for e in events if e.get("eventType") == "decision"}
    used_receipts: set[str] = set()
    for record in records:
        if record["case"] in {"blocked fictional deletion", "allowed fictional read", "second principal fictional read"}:
            receipt = (record["response"] or {}).get("result", {}).get("_meta", {}).get("tessera_audit_event_id")
            event = decisions.get(receipt, {})
            payload = event.get("payload", {})
            blocked_case = record["case"] == "blocked fictional deletion"
            principal = "beta" if record["case"] == "second principal fictional read" else "alpha"
            tool = record["request"]["params"]["name"].split("__", 1)[1]
            valid = (receipt not in used_receipts and bool(event)
                     and payload.get("decision") == ("block" if blocked_case else "allow")
                     and payload.get("policy_id") == ("pilot-block-delete" if blocked_case else "pilot-allow-read")
                     and payload.get("canonical_tool_name") == tool
                     and payload.get("tool_call", {}).get("arguments") == record["request"]["params"]["arguments"]
                     and payload.get("principal_id") == principal and event.get("tenantId") == principal
                     and payload.get("upstream") == "pilot" and payload.get("mode") == "enforcement")
            check(record["case"] + " receipt matches unique decision/tool/principal", valid)
            if receipt:
                used_receipts.add(receipt)
    (output / "events.jsonl").write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events), encoding="utf-8")
    source_files = ["tessera/proxy.py", "tessera/config.py", "tessera/mcp_http.py",
                    "tessera/integrations/streamable_http/upstream.py", "examples/synthetic_pilot/run.py"]
    report = {"scope": "Offline synthetic HTTP MCP acceptance; no real client, external review or deployment claim",
              "runtime_version": __version__, "signed": False, "network": "denied", "model": "none",
              "passed": all(c["passed"] for c in checks), "checks": checks, "cases": records,
              "synthetic_tool_contacts": contacts, "upstream_exchanges": exchanges,
              "audit_events": len(events), "audit_chains_verified": chain_ok,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
              "policy_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in policies.glob("*.yaml")},
              "remaining": ["Named desktop/client versions and real transcript capture", "Selected content/schema/pricing approval",
                            "External reviewer and partner consent", "Procurement/award terms", "Pilot retention and access decisions"]}
    (output / "run.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = "# Synthetic pilot acceptance\n\n" + ("PASS" if report["passed"] else "FAIL")
    summary += "\n\nOffline fictional systems; unsigned records. No real client compatibility, certification or live deployment is claimed.\n\n"
    summary += "\n".join(f'- {"PASS" if c["passed"] else "FAIL"}: {c["name"]}' for c in checks)
    summary += "\n\nSee run.json for measured request/response/contact evidence and source hashes; events.jsonl/audit.db retain synthetic audit records.\n"
    (output / "SUMMARY.md").write_text(summary, encoding="utf-8")
    checkpoint("passed" if report["passed"] else "failed")
    journal("complete", {"passed": report["passed"], "checks": len(checks)})
    print(json.dumps({"passed": report["passed"], "checks": len(checks), "output": str(output)}))
    return 0 if report["passed"] else 1


def isolated_environment(scratch: str) -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL") if key in os.environ}
    env.update({"HOME": scratch, "USERPROFILE": scratch, "TMPDIR": scratch, "TMP": scratch, "TEMP": scratch,
                "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "AWS_EC2_METADATA_DISABLED": "true",
                "TESSERA_AUDIT_SYNC": "1", "TESSERA_BEARER_TOKENS":
                "alpha:fictional_alpha_token_00000000,beta:fictional_beta_token_000000000"})
    return env


def worker(output: Path) -> int:
    if output.exists() and any(output.iterdir()):
        print("Worker refuses a nonempty evidence directory", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tessera-pilot-worker-") as scratch:
        env = isolated_environment(scratch)
        os.environ.clear()
        os.environ.update(env)
        sys.path.insert(0, str(ROOT))
        try:
            return run_engine(output)
        except BaseException as exc:
            (output / "failure.json").write_text(json.dumps({
                "passed": False, "reason": type(exc).__name__,
                "evidence": "Completed stages remain in journal.jsonl/progress.json",
            }), encoding="utf-8")
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.worker:
        return worker(output)
    if output.exists():
        parser.error("Use a new output directory; existing evidence is never overwritten")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="tessera-synthetic-pilot-") as scratch:
        env = isolated_environment(scratch)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--output-dir", str(output)],  # noqa: S603 -- own interpreter and script, no shell
                                    env=env, capture_output=True, text=True, timeout=45, check=False)
        except subprocess.TimeoutExpired:
            (output / "failure.json").write_text('{"passed":false,"reason":"45 second offline runner deadline"}\n', encoding="utf-8")
            print("Synthetic pilot exceeded its deadline", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            (output / "failure.json").write_text('{"passed":false,"reason":"interrupted; journal/progress retained"}\n', encoding="utf-8")
            print("Interrupted; completed stages remain in journal.jsonl/progress.json", file=sys.stderr)
            return 130
        if result.returncode:
            (output / "failure.json").write_text(json.dumps({"passed": False, "stderr": result.stderr[-8000:]}), encoding="utf-8")
            print(result.stderr, file=sys.stderr)
        print(result.stdout, end="")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
