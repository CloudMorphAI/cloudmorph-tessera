"""Actual Tessera MCP gate in front of isolated synthetic Node adapters.

This example has no vendor transport and no policy-administration endpoint.
The fixed operator YAML is not evidence of authenticated policy administration.
"""
from __future__ import annotations

import copy
import json
import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from tessera.audit.sinks.sqlite import SqliteSink
from tessera.audit.verifier import verify_chain
from tessera.config import (
    AuditConfig,
    IntentConfig,
    MetricsConfig,
    PoliciesConfig,
    PoliciesMode,
    RuntimeConfig,
    TesseraConfig,
    UpstreamConfig,
)
from tessera.demo import _neutralized_environment, _quiet_tessera_logging
from tessera.proxy import create_app

HERE = Path(__file__).resolve().parent
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN_LOCK = threading.Lock()
DEAL_ID = "DEAL-ACME-001"
CALL_ID = "CALL-ACME-001"


class IntegrationError(RuntimeError):
    """Safe failure; never converted into a successful policy receipt."""


class ContractError(ValueError):
    """Malformed input rejected before the policy gate or adapter."""


def policy_description() -> dict[str, Any]:
    return {
        "policy_id": "post-call-discount-cap", "discount_cap_pct": 10,
        "description": "Local operator YAML; not authenticated policy-admin proof. Integer discounts only.",
        "tools": ["meetings.get_call", "crm.get_deal", "crm.update_deal", "tasks.create",
                  "tasks.read", "drafts.create", "drafts.read"],
        "rules": [{"id": "post-call-discount-cap", "action": "block above 10%"},
                  {"id": "post-call-known-tools", "action": "allow validated synthetic tools"}],
    }


class FixtureBridge:
    def __init__(self, run_id: str):
        node = shutil.which("node")
        if not node or os.name == "nt":
            raise IntegrationError("Use the documented WSL Node/Python environment for the local demo")
        self.process = subprocess.Popen(  # noqa: S603 - fixed bridge, validated run ID, shell=False.
            [node, str(HERE / "fixture_bridge.mjs"), run_id], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            env={"PATH": str(Path(node).parent), "LANG": "C.UTF-8"}, shell=False,
        )
        self.buffer = b""

    def call(self, op: str, **kwargs: Any) -> Any:
        raw = json.dumps({"op": op, **kwargs}, allow_nan=False).encode() + b"\n"
        if len(raw) > 32768 or self.process.poll() is not None:
            raise IntegrationError("Local fixture bridge unavailable")
        try:
            self.process.stdin.write(raw)
            while b"\n" not in self.buffer:
                ready, _, _ = select.select([self.process.stdout], [], [], 3)
                if not ready:
                    raise IntegrationError("Local fixture bridge timed out")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk or len(self.buffer) + len(chunk) > 262144:
                    raise IntegrationError("Invalid fixture bridge response")
                self.buffer += chunk
            line, self.buffer = self.buffer.split(b"\n", 1)
            response = json.loads(line)
            if response.get("ok") is not True:
                raise IntegrationError("Local fixture bridge rejected operation")
            return response["result"]
        except (OSError, ValueError, KeyError) as exc:
            raise IntegrationError("Local fixture bridge failed") from exc

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe:
                pipe.close()


class PolicyGateway:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or "post-call-" + uuid.uuid4().hex
        if not IDENTIFIER.fullmatch(self.run_id):
            raise ContractError("Invalid run identifier")
        self.events: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._active = False

    def __enter__(self):
        if not RUN_LOCK.acquire(blocking=False):
            raise IntegrationError("Local demo worker is busy")
        self.stack = ExitStack()
        self.stack.callback(RUN_LOCK.release)
        try:
            self.bridge = FixtureBridge(self.run_id)
            self.stack.callback(self.bridge.close)
            self.catalog = self.bridge.call("catalog")
            # Publish the gateway's narrower contract to the model, rather than
            # advertising the adapter's broader two-decimal numeric capability.
            integer_pct = {"type": "integer", "minimum": 0, "maximum": 100}
            for entry in self.catalog:
                properties = entry["inputSchema"]["properties"]
                if entry["name"] == "crm.update_deal":
                    properties["fields"]["properties"]["discount_pct"] = dict(integer_pct)
                elif entry["name"] == "tasks.create":
                    properties["requested_discount_pct"] = dict(integer_pct)
                elif entry["name"] == "drafts.create":
                    properties["commercial_terms"]["properties"]["requested_discount_pct"] = dict(integer_pct)
            self.schemas = {entry["name"]: entry["inputSchema"] for entry in self.catalog}
            self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="post-call-")))
            self.audit_db = self.root / "audit.db"
            self.stack.enter_context(_neutralized_environment(self.root / "state"))
            self.stack.enter_context(_quiet_tessera_logging())
            config = TesseraConfig(
                audit=AuditConfig(path=str(self.audit_db), also_stdout=False),
                policies=PoliciesConfig(dir=str(HERE / "policies"), reload="none",
                                        mode=PoliciesMode.enforcement, default_action="block"),
                intent=IntentConfig(meta_key="tessera_intent", required=False),
                metrics=MetricsConfig(enabled=False), runtime=RuntimeConfig(lockdown=False),
                upstreams=[UpstreamConfig(name="post_call", url="http://synthetic.invalid", timeout_seconds=3)],
                deployment_id=self.run_id,
            )
            self.app = create_app(config)
            self.client_context = TestClient(self.app, raise_server_exceptions=False)
            self.client = self.client_context.__enter__()
            self.client_open = True
            self.stack.callback(self._close_client)
            self.client.portal.call(self.app.state.http_clients["post_call"].aclose)
            self.app.state.http_clients["post_call"] = httpx.AsyncClient(
                transport=httpx.MockTransport(self._transport), base_url="http://synthetic.invalid", timeout=3)
            self._active = True
            return self
        except BaseException:
            self.stack.close()
            raise

    def _close_client(self):
        if self.client_open:
            self.client_context.__exit__(None, None, None)
            self.client_open = False

    def __exit__(self, *args):
        self._active = False
        self.stack.close()

    def _transport(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        invocation = {"request_id": self.current_request_id, "tool_use_id": self.current_tool_id,
                      "tool_name": body["params"]["name"], "arguments": body["params"]["arguments"]}
        self.adapter_response = self.bridge.call("dispatch", invocation=invocation)
        self.contact_count += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(self.adapter_response)}],
            "isError": self.adapter_response["status"] != "ok"}})

    def validate(self, tool_name: str, arguments: Any):
        if not isinstance(tool_name, str) or not IDENTIFIER.fullmatch(tool_name):
            raise ContractError("Invalid tool name")
        if not isinstance(arguments, dict):
            raise ContractError("Tool arguments must be an object")
        try:
            if len(json.dumps(arguments, allow_nan=False).encode()) > 8192:
                raise ContractError("Tool arguments are too large")
        except (ValueError, TypeError) as exc:
            raise ContractError("Arguments must be finite JSON") from exc
        if tool_name in self.schemas:
            if not Draft202012Validator(self.schemas[tool_name]).is_valid(arguments):
                raise ContractError("Arguments do not match the fixed synthetic tool schema")
            # The fixture accepts two-decimal percentages. This bounded example
            # deliberately requires integers before its integer policy regex.
            candidates = [arguments.get("fields", {}).get("discount_pct"),
                          arguments.get("requested_discount_pct"),
                          arguments.get("commercial_terms", {}).get("requested_discount_pct")]
            if any(value is not None and (type(value) is not int or not 0 <= value <= 100)
                   for value in candidates):
                raise ContractError("This example requires integer percentages from 0 through 100")

    def invoke(self, tool_name: str, arguments: dict[str, Any], tool_use_id: str,
               request_id: str | None = None) -> dict[str, Any]:
        if not self._active:
            raise IntegrationError("Policy gateway is not active")
        self.validate(tool_name, arguments)
        request_id = request_id or self.run_id
        if (not isinstance(tool_use_id, str) or not IDENTIFIER.fullmatch(tool_use_id)
                or tool_use_id in self._seen_ids or not isinstance(request_id, str)
                or not IDENTIFIER.fullmatch(request_id)):
            raise ContractError("Invalid or repeated tool/request identifier")
        self._seen_ids.add(tool_use_id)
        self.current_request_id, self.current_tool_id = request_id, tool_use_id
        self.contact_count, self.adapter_response = 0, None
        reply = self.client.post("/mcp/post_call", json={"jsonrpc": "2.0", "id": tool_use_id,
            "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}})
        try:
            result = reply.json()["result"]
            audit_id = result["_meta"]["tessera_audit_event_id"]
            if reply.status_code != 200 or not isinstance(audit_id, str) or not audit_id:
                raise ValueError("missing receipt")
            if self.contact_count not in (0, 1):
                raise ValueError("invalid adapter count")
            if self.contact_count == 0 and result.get("isError") is not True:
                raise ValueError("missing block")
            event = {"tool_name": tool_name, "arguments": copy.deepcopy(arguments),
                     "tool_use_id": tool_use_id, "request_id": request_id,
                     "audit_id": audit_id, "decision": "allow" if self.contact_count else "block",
                     "adapter_contacts": self.contact_count,
                     "adapter_writes": self.adapter_response["adapter"]["writes"] if self.adapter_response else 0,
                     "result": self.adapter_response.get("result") if self.adapter_response else None,
                     "error": self.adapter_response.get("error") if self.adapter_response else None,
                     "gate_result": result,
                     "reason": "\n".join(item.get("text", "") for item in result.get("content", []))
                               if not self.contact_count else "Policy receipt pending audit reconciliation"}
            self.events.append(event)
            return event
        except (ValueError, KeyError, TypeError) as exc:
            raise IntegrationError("Actual Tessera gate did not return a valid correlated receipt") from exc

    def finish(self) -> dict[str, Any]:
        snapshot = self.bridge.call("snapshot")
        self._close_client()  # Drain the actual asynchronous audit sink.
        self._active = False
        sink = SqliteSink(path=str(self.audit_db))
        try:
            stored = list(sink.iter_events(self.run_id))
            verification = verify_chain(sink, self.run_id)
        finally:
            sink.close()
        decisions = {row["eventId"]: row for row in stored if row.get("eventType") == "decision"}
        if not verification["ok"] or len(decisions) != len(self.events):
            raise IntegrationError("Actual audit chain/decision count did not verify")
        for event in self.events:
            row = decisions.get(event["audit_id"], {})
            payload = row.get("payload", {})
            if (payload.get("decision") != event["decision"]
                    or payload.get("tool_call", {}).get("name") != event["tool_name"]
                    or payload.get("tool_call", {}).get("arguments") != event["arguments"]
                    or not payload.get("reason")):
                raise IntegrationError("Actual audit correlation failed")
            event.update(reason=payload["reason"], policy_id=payload.get("policy_id"),
                         tessera_request_id=payload.get("request_id"))
        state = snapshot["state"]
        return {"run_id": self.run_id, "fixture_version": snapshot["fixture_version"],
                "policy": policy_description(), "events": self.events,
                "readback": {"deal": state["deals"][DEAL_ID], "tasks": list(state["tasks"].values()),
                             "drafts": list(state["drafts"].values())},
                "adapter_totals": snapshot["counters"],
                "summary": {"allowed": sum(e["decision"] == "allow" for e in self.events),
                            "blocked": sum(e["decision"] == "block" for e in self.events),
                            "adapter_contacts": sum(e["adapter_contacts"] for e in self.events),
                            "blocked_adapter_contacts": sum(e["adapter_contacts"] for e in self.events
                                                            if e["decision"] == "block")},
                "audit": {"count": len(stored), "chain_verified": True, "verification": verification,
                          "events": stored, "receipts": [{"tool": e["tool_name"], "audit_id": e["audit_id"]}
                                                        for e in self.events]}}


def run_scripted(gateway: PolicyGateway):
    """Deterministic fixture proposals, explicitly no model execution."""
    actions = [
        ("meetings.get_call", {"call_id": CALL_ID}),
        ("crm.get_deal", {"deal_id": DEAL_ID}),
        ("crm.update_deal", {"deal_id": DEAL_ID, "fields": {
            "notes": "Synthetic Acme call: evaluate the engineering integration and requested 30% commercial exception.",
            "next_step": "Engineering evaluation; commercial exception requires separate internal review."}}),
        ("tasks.create", {"task_kind": "engineering_follow_up", "deal_id": DEAL_ID,
            "title": "Evaluate synthetic Acme integration", "details": "Prepare the engineering evaluation discussed in the fictional call.",
            "idempotency_key": gateway.run_id + ":engineering"}),
        ("drafts.create", {"draft_kind": "follow_up", "deal_id": DEAL_ID,
            "title": "Acme evaluation follow-up (unsent)",
            "body": "Thanks for the fictional call. We will prepare an engineering evaluation. Commercial terms remain subject to internal review.",
            "idempotency_key": gateway.run_id + ":followup"}),
        ("crm.update_deal", {"deal_id": DEAL_ID, "fields": {"discount_pct": 30}}),
        ("tasks.create", {"task_kind": "internal_discount_review", "deal_id": DEAL_ID,
            "title": "Review requested 30% discount", "details": "Request only: preserve 30%; do not apply or approve commercial terms.",
            "requested_discount_pct": 30, "idempotency_key": gateway.run_id + ":review"}),
        ("crm.get_deal", {"deal_id": DEAL_ID}),
    ]
    for index, (name, args) in enumerate(actions):
        gateway.invoke(name, args, f"scripted-{index + 1}",
                       request_id=gateway.run_id + ("-review" if index == 6 else ""))


def require_scenario(receipt: dict[str, Any]):
    events, readback = receipt["events"], receipt["readback"]
    blocked = [e for e in events if e["decision"] == "block"]
    reviews = [t for t in readback["tasks"] if t["task_kind"] == "internal_discount_review"]
    engineering = [t for t in readback["tasks"] if t["task_kind"] == "engineering_follow_up"]
    final_reads = [e for e in events if e["tool_name"] == "crm.get_deal" and e["decision"] == "allow"]
    review_events = [e for e in events if e["tool_name"] == "tasks.create"
                     and e["arguments"].get("task_kind") == "internal_discount_review"]
    if (len(events) != 8 or len(blocked) != 1 or blocked[0]["tool_name"] != "crm.update_deal"
            or blocked[0]["arguments"].get("fields", {}).get("discount_pct") != 30
            or blocked[0]["policy_id"] != "post-call-discount-cap" or blocked[0]["adapter_contacts"] != 0
            or readback["deal"]["discount_pct"] != 0 or not readback["deal"]["notes"]
            or not readback["deal"]["next_step"] or len(engineering) != 1 or len(reviews) != 1
            or reviews[0]["requested_discount_pct"] != 30 or reviews[0]["approval_status"] != "pending"
            or len(readback["drafts"]) != 1 or readback["drafts"][0]["delivery_status"] != "not_sent"
            or any(e.get("error") for e in events)
            or len(final_reads) != 2 or final_reads[0]["result"]["discount_pct"] != 0
            or final_reads[-1]["result"] != readback["deal"]
            or len(review_events) != 1 or events.index(review_events[0]) <= events.index(blocked[0])
            or review_events[0]["request_id"] == blocked[0]["request_id"]
            or review_events[0]["audit_id"] == blocked[0]["audit_id"]
            or not receipt["audit"]["chain_verified"] or receipt["summary"]["blocked_adapter_contacts"] != 0):
        raise IntegrationError("The selected synthetic post-call acceptance checks did not pass")
