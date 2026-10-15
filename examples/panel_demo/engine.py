"""Project measured results from the existing offline Tessera demo, in a child process."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_FILES = ("events.jsonl", "manifest.json", "summary.html", "VERIFY.md")
VERIFICATION_SCOPE = (
    "Embedded-key verification proves bundle consistency, not externally pinned signer identity. "
    "This synthetic demo uses a fresh throwaway Ed25519 key."
)


class DemoRunError(RuntimeError):
    """A bounded child run did not produce verified evidence."""


def run_isolated(output_dir: Path, *, mode: str = "recorded", timeout: int = 45) -> dict[str, Any]:
    """Run only our fixed child entrypoint; never accept caller-selected tools or inputs."""
    if mode not in {"live", "recorded"}:
        raise ValueError("Unsupported demo mode")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    environment = {
        name: value for name, value in os.environ.items()
        if name in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR"}
    }
    # A timeout kills the single Python child; the child never creates grandchildren.
    try:
        with tempfile.TemporaryDirectory(prefix="child-", dir=output_dir) as scratch:
            environment.update({"TMPDIR": scratch, "HOME": scratch})
            completed = subprocess.run(  # noqa: S603 - fixed interpreter and fixed local script
                [sys.executable, "-I", str(Path(__file__).resolve()), "--child", str(output_dir), mode],
                env=environment, capture_output=True, timeout=timeout, check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise DemoRunError("The offline engine run exceeded its time limit.") from exc
    if completed.returncode != 0:
        # Child stdout/stderr can contain local paths; never pass either to the browser.
        raise DemoRunError("The offline engine could not produce verified evidence.")
    try:
        projection = json.loads((output_dir / "demo-data.json").read_text(encoding="utf-8"))
        if projection["verification"]["ok"] is not True or not (output_dir / "evidence.zip").is_file():
            raise ValueError("Missing verified result")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DemoRunError("The offline engine returned incomplete evidence.") from exc
    return projection


def _deny_network() -> None:
    """Defense in depth: this child must never connect to an IP network."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock: socket.socket, address: Any) -> Any:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("Network access is disabled for this offline demo")
        return original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> Any:
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("Network access is disabled for this offline demo")
        return original_connect_ex(sock, address)

    def getaddrinfo(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Network name resolution is disabled for this offline demo")

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


def _run_child(output_dir: Path, mode: str) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    _deny_network()
    from tessera import demo
    from tessera.audit.verifier import verify_events_file

    started = time.monotonic()
    upstream_calls: Counter[str] = Counter()
    original_factory = demo._mock_upstream_transport

    def counted_transport() -> Any:
        import httpx

        original = original_factory()

        class CountedTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                upstream_calls[body["params"]["name"]] += 1
                return await original.handle_async_request(request)

            async def aclose(self) -> None:
                await original.aclose()

        return CountedTransport()

    demo._mock_upstream_transport = counted_transport
    result = demo.run_demo(echo=lambda *_args: None)
    bundle_dir = Path(result["bundle_dir"])
    try:
        events = [json.loads(line) for line in (bundle_dir / "events.jsonl").read_text().splitlines() if line]
        decisions = {event["eventId"]: event for event in events if event.get("eventType") == "decision"}
        bodies = [entry["body"] for entry in result["blocks"]] + [result["allow_response"]]
        descriptions = [
            ("secret", "An AI tries to pass a secret", "A synthetic token is placed in tool arguments."),
            ("force-push", "A protected branch is targeted", "A fixed force-push targets main in the fictional acme/api repository."),
            ("drop-users", "A critical table is targeted", "A fixed SQL request tries to drop the synthetic users table."),
            ("safe-read", "Safe read passes through", "A fixed list_dir call reaches the canned mock transport. The returned database text is a fixture, not a live inventory."),
        ]
        cases = []
        for body, (case_id, title, description) in zip(bodies, descriptions, strict=True):
            response = body["result"]
            receipt_id = response["_meta"]["tessera_audit_event_id"]
            event = decisions[receipt_id]
            payload = event["payload"]
            call = payload["tool_call"]
            decision = payload["decision"]
            response_text = "\n".join(item.get("text", "") for item in response.get("content", []))
            if decision not in {"allow", "block"} or bool(response.get("isError")) != (decision == "block"):
                raise DemoRunError("Audit decision and actual response disagree")
            cases.append({
                "id": case_id, "title": title, "description": description,
                "action": call["name"], "arguments": call["arguments"], "decision": decision,
                "reason": payload.get("reason"), "rule_id": payload.get("policy_id"),
                "upstream_calls": upstream_calls[call["name"]], "receipt_id": receipt_id,
                "response_text": response_text,
            })
        measured = Counter(case["decision"] for case in cases)
        manifest = result["manifest"]
        verified = result["verify_result"]
        integrity = verify_events_file(bundle_dir / "events.jsonl")
        counts = manifest["event_counts"]
        counts_match = (
            counts["total"] == len(events) == verified["events_checked"]
            and counts["allow"] == measured["allow"] and counts["block"] == measured["block"]
            and len(decisions) == len(cases)
        )
        blocked_upstream_calls = sum(case["upstream_calls"] for case in cases if case["decision"] == "block")
        # Expectations are checked against measured responses, receipts and transport traffic.
        # A failure produces no successful projection or downloadable ZIP.
        ok = (
            verified["ok"] is True and verified["signature_ok"] is True
            and verified["manifest_consistent"] is True and integrity["ok"] is True and counts_match
            and measured["block"] == 3 and measured["allow"] == 1
            and blocked_upstream_calls == 0 and sum(upstream_calls.values()) == 1
            and cases[-1]["upstream_calls"] == 1
        )
        if not ok:
            raise DemoRunError("The measured demo result did not pass its evidence checks")
        output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_dir / "evidence.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in BUNDLE_FILES:
                archive.write(bundle_dir / name, arcname=name)
        projection = {
            "schema_version": 1, "run_id": str(uuid.uuid4()),
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "mode": mode, "offline": True, "duration_ms": round((time.monotonic() - started) * 1000),
            "cases": cases,
            "summary": {
                "blocked": measured["block"], "allowed": measured["allow"],
                "upstream_calls": sum(upstream_calls.values()), "blocked_upstream_calls": blocked_upstream_calls,
                "receipts": len(decisions), "audit_events": len(events),
                "signatures_verified": verified["signature_ok"], "chain_verified": integrity["ok"],
            },
            "verification": {
                "ok": ok, "signature_ok": verified["signature_ok"], "integrity_ok": integrity["ok"],
                "event_count_ok": counts_match, "manifest_consistent": verified["manifest_consistent"],
                "events_checked": verified["events_checked"], "scope": VERIFICATION_SCOPE,
            },
            "evidence": {
                "url": "/api/evidence" if mode == "live" else "./evidence.zip",
                "sha256": hashlib.sha256((output_dir / "evidence.zip").read_bytes()).hexdigest(),
                "files": list(BUNDLE_FILES), "scope": VERIFICATION_SCOPE,
            },
        }
        (output_dir / "demo-data.json").write_text(json.dumps(projection, indent=2) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(bundle_dir)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--child" or sys.argv[3] not in {"live", "recorded"}:
        raise SystemExit("Use demo_server.py export or demo_server.py serve")
    _run_child(Path(sys.argv[2]), sys.argv[3])
