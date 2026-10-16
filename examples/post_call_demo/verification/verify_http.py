"""HTTP-level regression checks for the local synthetic post-call demo.

Starts the actual DemoService and HTTP handler on an ephemeral loopback port.
Scripted mode exercises the real Tessera gateway and isolated fixture adapters;
no provider is enabled and no customer system is contacted.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO_DIR))
import server as demo_server  # noqa: E402


class CheckFailureError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailureError(message)


def request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = "application/json",
    idempotency_key: str | None = None,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "Host": host or f"127.0.0.1:{port}"}
    if origin is not None:
        headers["Origin"] = origin
    if content_type is not None:
        headers["Content-Type"] = content_type
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        if not raw:
            payload: Any = None
        else:
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = raw.decode("utf-8", errors="replace")
        return response.status, payload
    finally:
        connection.close()


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")


def checked(status: int, expected: int, label: str, payload: Any) -> None:
    require(status == expected, f"{label}: expected HTTP {expected}, received {status}: {payload!r}")


def verify_completed_receipt(receipt: dict[str, Any]) -> None:
    require(receipt.get("mode") == "scripted", "Completed run was not labeled scripted")
    require(receipt.get("replayed") is False, "First request was not labeled as a fresh run")
    require(receipt.get("synthetic") is True, "Completed run is not explicitly synthetic")
    require(receipt.get("model") is None and receipt.get("provider") is None,
            "Scripted mode unexpectedly reports a model/provider")
    require(receipt.get("usage") == {"input_tokens": 0, "output_tokens": 0, "model_calls": 0},
            "Scripted mode unexpectedly reports provider usage")

    events = receipt.get("events")
    expected_tools = [
        "meetings.get_call", "crm.get_deal", "crm.update_deal", "tasks.create",
        "drafts.create", "crm.update_deal", "tasks.create", "crm.get_deal",
    ]
    require(isinstance(events, list) and len(events) == len(expected_tools),
            "Receipt does not contain the expected eight tool decisions")
    require([event.get("tool_name") for event in events] == expected_tools,
            "Tool decision sequence differs from the scripted post-call flow")
    expected_decisions = ["allow", "allow", "allow", "allow", "allow", "block", "allow", "allow"]
    require([event.get("decision") for event in events] == expected_decisions,
            "Tool decisions differ from the expected seven-allow/one-block flow")

    blocked = events[5]
    require(blocked.get("policy_id") == "post-call-discount-cap",
            "30% discount was not blocked by the configured cap policy")
    require(blocked.get("arguments", {}).get("fields", {}).get("discount_pct") == 30,
            "Blocked event did not preserve the requested 30% value")
    require(blocked.get("result") is None, "Blocked event contains an adapter result")
    meta = blocked.get("gate_result", {}).get("_meta", {})
    blocked_audit_id = meta.get("tessera_audit_event_id")
    require(isinstance(blocked_audit_id, str) and blocked_audit_id,
            "Blocked decision is missing its Tessera audit event ID")

    summary = receipt.get("summary", {})
    require(summary.get("allowed") == 7 and summary.get("blocked") == 1,
            "Summary does not report seven allows and one block")
    require(summary.get("adapter_contacts") == 7 and summary.get("blocked_adapter_contacts") == 0,
            "Blocked action contacted an adapter or the adapter totals differ")

    readback = receipt.get("readback", {})
    deal = readback.get("deal", {})
    require(deal.get("discount_pct") == 0, "Blocked discount changed the deal readback")
    require(deal.get("notes") and deal.get("next_step"), "Allowed notes/next-step update is missing from readback")
    review_tasks = [task for task in readback.get("tasks", [])
                    if task.get("task_kind") == "internal_discount_review"]
    require(len(review_tasks) == 1, "Separate internal discount-review task is missing")
    review = review_tasks[0]
    require(review.get("requested_discount_pct") == 30
            and review.get("approval_status") == "pending"
            and review.get("status") == "awaiting_internal_review",
            "Review task did not preserve the requested value as pending")
    require(all(draft.get("delivery_status") == "not_sent" for draft in readback.get("drafts", [])),
            "A follow-up draft is not explicitly marked unsent")

    audit = receipt.get("audit", {})
    require(audit.get("chain_verified") is True, "Local Tessera audit chain was not verified")
    require(audit.get("count") == 9 and audit.get("verification", {}).get("events_checked") == 9,
            "Audit verification did not cover startup plus all eight tool decisions")
    audit_events = audit.get("events", [])
    blocked_rows = [event for event in audit_events
                    if event.get("eventType") == "decision"
                    and event.get("payload", {}).get("decision") == "block"]
    require(len(blocked_rows) == 1, "Audit chain does not contain exactly one block decision")
    require(blocked_rows[0].get("eventId") == blocked_audit_id,
            "Blocked UI event does not correlate to the actual audit row")
    require(blocked_rows[0].get("payload", {}).get("policy_id") == "post-call-discount-cap",
            "Actual audit row does not identify the discount cap policy")
    receipts = audit.get("receipts", [])
    expected_ids = [event.get("gate_result", {}).get("_meta", {}).get("tessera_audit_event_id")
                    for event in events]
    require([row.get("audit_id") for row in receipts] == expected_ids,
            "Receipt index does not correlate every tool event to its gate audit ID")


def main() -> int:
    service = demo_server.DemoService(enable_nova=False, output_dir=None)
    scripted_runs = {"count": 0}
    actual_run_scripted = demo_server.run_scripted

    def counted_run_scripted(gateway):
        scripted_runs["count"] += 1
        return actual_run_scripted(gateway)

    # Instrument only the call count. The actual scripted runner, Tessera gateway,
    # audit chain, and fixture adapters remain unmocked.
    demo_server.run_scripted = counted_run_scripted
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), demo_server.make_handler(service))
    port = httpd.server_port
    origin = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, name="post-call-verifier-http", daemon=True)
    thread.start()
    checks: list[str] = []
    try:
        status, health = request(port, "GET", "/api/health")
        checked(status, 200, "health", health)
        require(health.get("status") == "ready" and health.get("synthetic") is True,
                "Health response does not identify the ready synthetic service")
        require(health.get("modes") == ["scripted"], "Verifier server did not keep Nova disabled")
        require(health.get("policy_admin_auth_verified") is False,
                "Health response made an unsupported policy-admin-auth claim")
        checks.append("health_and_nova_disabled")

        status, policy = request(port, "GET", "/api/policy")
        checked(status, 200, "read-only policy", policy)
        require(policy.get("discount_cap_pct") == 10, "Policy readback has an unexpected discount cap")
        tools = policy.get("tools", [])
        require("crm.update_deal" in tools and not any(name.startswith("policy.") for name in tools),
                "Read-only policy surface exposed a policy-mutation tool")
        checks.append("read_only_policy_surface")

        key = "http-verifier-" + uuid.uuid4().hex
        payload = {"mode": "scripted", "idempotency_key": key}
        status, receipt = request(port, "POST", "/api/run", body=json_body(payload),
                                  origin=origin, idempotency_key=key)
        checked(status, 200, "scripted run", receipt)
        require(isinstance(receipt, dict), "Scripted run returned a non-object receipt")
        verify_completed_receipt(receipt)
        require(scripted_runs["count"] == 1, "Scripted runner did not execute exactly once")
        checks.append("actual_scripted_tessera_run_and_receipt")

        status, replay = request(port, "POST", "/api/run", body=json_body(payload),
                                 origin=origin, idempotency_key=key)
        checked(status, 200, "exact idempotent replay", replay)
        require(replay.get("replayed") is True, "Idempotent response was not labeled replayed")
        first_without_replay_flag = {k: v for k, v in receipt.items() if k != "replayed"}
        replay_without_flag = {k: v for k, v in replay.items() if k != "replayed"}
        require(replay_without_flag == first_without_replay_flag,
                "Idempotent replay changed the run/audit/readback receipt")
        require(scripted_runs["count"] == 1 and len(service.cache) == 1,
                "Idempotency replay repeated a policy/adapter run")
        checks.append("exact_replay_no_duplicate_gate_or_adapter_run")

        status, conflict = request(port, "POST", "/api/run",
            body=json_body({"mode": "nova", "idempotency_key": key}),
            origin=origin, idempotency_key=key)
        checked(status, 409, "same key different mode", conflict)
        require(scripted_runs["count"] == 1 and service.live_attempted is False,
                "Conflicting mode consumed a Nova attempt or reran the scenario")
        checks.append("idempotency_mode_conflict")

        invalid_requests = [
            ("malformed_json", b"{bad", origin, f"{port}"),
            ("unknown_request_field", json_body({**payload, "tool_name": "policy.update"}), origin, key),
            ("duplicate_json_field", b'{"mode":"scripted","mode":"nova","idempotency_key":"dup"}', origin, "dup"),
            ("nonfinite_json_number", b'{"mode":"scripted","idempotency_key":"nan","unused":NaN}', origin, "nan"),
            ("invalid_mode", json_body({"mode": "unknown", "idempotency_key": "invalid-mode"}), origin, "invalid-mode"),
            ("missing_idempotency_header", json_body(payload), origin, None),
            ("header_body_key_mismatch", json_body({"mode": "scripted", "idempotency_key": "body-key"}), origin, "header-key"),
        ]
        for label, body, request_origin, header_key in invalid_requests:
            status, error = request(port, "POST", "/api/run", body=body,
                origin=request_origin, idempotency_key=header_key)
            checked(status, 400, label, error)
        checks.append("malformed_unknown_and_idempotency_request_rejection")

        status, error = request(port, "POST", "/api/run", body=json_body(payload),
                                idempotency_key=key)
        checked(status, 403, "missing Origin", error)
        status, error = request(port, "POST", "/api/run", body=json_body(payload),
                                origin="https://attacker.invalid", idempotency_key=key)
        checked(status, 403, "cross Origin", error)
        status, error = request(port, "POST", "/api/run", body=json_body(payload),
                                origin=origin, host=f"localhost:{port}", idempotency_key=key)
        checked(status, 403, "invalid Host", error)
        checks.append("loopback_origin_and_host_guards")

        status, error = request(port, "POST", "/api/policy", body=b'{"policy":{"action":"allow"}}',
                                origin=origin)
        checked(status, 404, "policy mutation endpoint", error)
        status, error = request(port, "POST", "/api/run", body=json_body({**payload, "policy": {"action": "allow"}}),
                                origin=origin, idempotency_key=key)
        checked(status, 400, "policy injection into run request", error)
        checks.append("no_agent_policy_mutation_surface")

        unused_nova_key = "nova-disabled-" + uuid.uuid4().hex
        status, error = request(port, "POST", "/api/run",
            body=json_body({"mode": "nova", "idempotency_key": unused_nova_key}),
            origin=origin, idempotency_key=unused_nova_key)
        checked(status, 409, "Nova disabled", error)
        require(service.live_attempted is False and scripted_runs["count"] == 1 and len(service.cache) == 1,
                "Disabled Nova request attempted a provider call or changed run state")
        checks.append("nova_disabled_without_provider_attempt")

        report = {
            "status": "passed",
            "base_url": origin,
            "mode_tested": "scripted",
            "provider_calls": 0,
            "scripted_gate_executions": scripted_runs["count"],
            "checks": checks,
            "claims_limit": "Local synthetic fixture only; does not verify policy-admin authentication, a real customer adapter, or Nova availability.",
        }
        print(json.dumps(report, indent=2))
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        demo_server.run_scripted = actual_run_scripted


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CheckFailureError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from None
