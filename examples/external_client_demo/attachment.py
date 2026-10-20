"""Attach real external MCP requests; never select calls or synthesize results."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from tessera.audit.chain import HashChain
from tessera.errors import UnauthorizedError
from tessera.mcp_http import AUDIT_ID_HEADER


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Attachment:
    """Bounded transport metadata; trusted identity comes only from canonical auth."""

    def __init__(self, app, run_id, ledger):
        self.app, self.run_id, self.ledger = app, run_id, ledger
        self.requests = dict.fromkeys(("opencode", "codex"), 0)
        self.calls = dict.fromkeys(("opencode", "codex"), 0)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/healthz" and scope["method"] == "GET":
            return await self.app(scope, receive, send)
        if scope["path"] != "/mcp/control":
            return await JSONResponse({"error": "Not found"}, 404)(scope, receive, send)
        try:
            identity = self.app.state.authenticator.authenticate(Request(scope))
        except UnauthorizedError:
            return await JSONResponse({"error": "Unauthorized"}, 401)(scope, receive, send)
        principal = identity.principal_id
        if principal not in self.requests or self.requests[principal] >= 24:
            return await JSONResponse({"error": "Client request limit"}, 429)(scope, receive, send)
        self.requests[principal] += 1
        if scope["method"] != "POST":
            return await self.app(scope, receive, send)
        raw = bytearray()
        while True:
            part = await asyncio.wait_for(receive(), timeout=5)
            if part["type"] == "http.disconnect":
                return None
            raw.extend(part.get("body", b""))
            if len(raw) > 65536:
                return await JSONResponse({"error": "Request too large"}, 413)(scope, receive, send)
            if not part.get("more_body", False):
                break
        row = None
        try:
            body = json.loads(raw, object_pairs_hook=pairs)
            if type(body) is not dict:
                raise ValueError("OBJECT_REQUIRED")
            if body.get("method") == "tools/call":
                if self.calls[principal] >= 2:
                    return await JSONResponse({"error": "Client tool-call limit"}, 429)(scope, receive, send)
                params = body.get("params")
                if type(params) is not dict or type(params.get("arguments", {})) is not dict:
                    raise ValueError("PARAMS_REQUIRED")
                meta = params.get("_meta", {})
                if type(meta) is not dict:
                    raise ValueError("META_REQUIRED")
                # No caller-supplied identity/intent is promoted into canonical
                # audit. Standard progressToken stays usable but cannot carry
                # a raw high-entropy credential or arbitrary free-form text.
                if set(meta) - {"progressToken"}:
                    raise ValueError("UNAPPROVED_CLIENT_METADATA")
                progress = meta.get("progressToken")
                if progress is not None and not (
                        type(progress) is int and 0 <= progress < 2**63
                        or type(progress) is str and re.fullmatch(r"[0-9]{1,20}|[a-f0-9]{32}|[a-f0-9-]{36}", progress)):
                    raise ValueError("UNAPPROVED_PROGRESS_TOKEN")
                allowed_args = {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc", "branch": "main", "job_id": "84"}
                denied_args = {**allowed_args, "app_id": "d3s9n6ffim4722", "job_id": "60"}
                if params.get("name") != "read_workload_status" or params.get("arguments") not in (allowed_args, denied_args):
                    raise ValueError("OUTSIDE_APPROVED_TWO_READ_SCOPE")
                request_id = uuid.uuid4().hex
                use_id = principal + "-" + uuid.uuid4().hex
                self.calls[principal] += 1
                # Preserve the real client-selected tool/arguments/intent. Only
                # correlation identities are supplied by this trusted attachment.
                params["_meta"] = {**meta, "run_id": self.run_id, "request_id": request_id,
                    "tool_use_id": use_id, "conversation_id": self.run_id + "-" + principal,
                    "external_client": principal}
                row = {"principal_id": principal, "request_id": request_id, "tool_use_id": use_id,
                       "argument_digest": digest(params.get("arguments", {})),
                       "tool_name": params.get("name"), "audit_event_id": None, "http_status": None}
            encoded = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return await JSONResponse({"error": "Invalid JSON-RPC envelope"}, 400)(scope, receive, send)
        sent = False
        response_bytes = bytearray()

        async def replacement_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": encoded, "more_body": False}
            return await receive()

        async def capture_send(message):
            if row is not None and message["type"] == "http.response.start":
                row["http_status"] = message["status"]
                headers = dict(message.get("headers", []))
                row["audit_event_id"] = headers.get(AUDIT_ID_HEADER.lower().encode("ascii"), b"").decode("ascii") or None
                row["audit_event_id_source"] = "canonical_header" if row["audit_event_id"] else None
            if row is not None and message["type"] == "http.response.body":
                response_bytes.extend(message.get("body", b""))
                if len(response_bytes) > 262144:
                    raise ValueError("RESPONSE_BOUND_EXCEEDED")
                if not message.get("more_body", False) and row["audit_event_id"] is None:
                    actual = json.loads(response_bytes, object_pairs_hook=pairs)
                    target = actual.get("result", actual.get("error", {}))
                    if "error" in actual:
                        target = target.get("data", {})
                    row["audit_event_id"] = target.get("_meta", {}).get("tessera_audit_event_id")
                    row["audit_event_id_source"] = "canonical_body_metadata" if row["audit_event_id"] else None
            await send(message)

        # Content length must describe the trusted metadata-enriched body.
        headers = [(name, value) for name, value in scope["headers"] if name.lower() != b"content-length"]
        headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        try:
            await self.app({**scope, "headers": headers}, replacement_receive, capture_send)
        finally:
            if row is not None:
                self.ledger.add(row)
            response_bytes.clear()


def project_evidence(*, audit_path, tenant, ingress, contacts, run_id):
    """Join actual principal→returned audit ID→canonical event→adapter contact."""
    with sqlite3.connect("file:" + str(Path(audit_path)) + "?mode=ro", uri=True) as connection:
        stored = connection.execute("SELECT payload_json FROM audit_events WHERE scope=? ORDER BY seq LIMIT 257", (tenant,)).fetchall()
    if not 1 <= len(stored) <= 256:
        raise ValueError("AUDIT_BOUND_INVALID")
    events = [json.loads(raw) for (raw,) in stored]
    previous = ""
    for event in events:
        if (event.get("tenantId") != tenant or event.get("prevEventHash") != previous
                or not HashChain.verify_event_hash(event)):
            raise ValueError("AUDIT_CHAIN_INVALID")
        previous = event["eventHash"]
    indexed = {event["eventId"]: event for event in events}
    rows = []
    for item in ingress:
        event = indexed.get(item["audit_event_id"])
        if event is None:
            raise ValueError("AUDIT_CORRELATION_MISSING")
        payload = event["payload"]
        call, principal = payload.get("tool_call", {}), item["principal_id"]
        meta = call.get("_meta", {})
        matched = [row for row in contacts if row.get("request_id") == item["request_id"]]
        decision = payload.get("decision")
        if (payload.get("principal_id") != principal or meta.get("external_client") != principal
                or meta.get("run_id") != run_id or meta.get("request_id") != item["request_id"]
                or meta.get("tool_use_id") != item["tool_use_id"] or call.get("name") != item["tool_name"]
                or digest(call.get("arguments", {})) != item["argument_digest"]
                or decision not in {"allow", "block"}
                or len(matched) != (1 if decision == "allow" else 0)
                or any(contact.get("tool_use_id") != item["tool_use_id"]
                       or contact.get("argument_digest") != item["argument_digest"]
                       or contact.get("principal_id") != principal or contact.get("source") != "aws_sdk_readonly"
                       for contact in matched)):
            raise ValueError("AUDIT_CORRELATION_INVALID")
        expected_args = {"operation": "amplify.GetJob", "app_id": "d2nxbpvv53qeqc" if decision == "allow" else "d3s9n6ffim4722",
                         "branch": "main", "job_id": "84" if decision == "allow" else "60"}
        expected_probe = call.get("name") == "read_workload_status" and call.get("arguments") == expected_args
        finite = {key: value for key, value in item.items() if key != "tool_name"}
        rows.append({**finite, "tool_name": "read_workload_status" if call.get("name") == "read_workload_status" else "UNEXPECTED_TOOL",
                     "expected_probe": expected_probe, "canonical_request_id": payload["request_id"], "decision": decision,
                     "policy_id": payload.get("policy_id"), "upstream_contacts": len(matched),
                     "actual_read_complete": bool(matched) and matched[0].get("outcome_type") == "read_only_complete"})
    if any(contact.get("request_id") not in {row["request_id"] for row in rows} for contact in contacts):
        raise ValueError("UNATTRIBUTED_UPSTREAM_CONTACT")
    accepted = all(sum(row["principal_id"] == name and row["decision"] == decision
                       and row["expected_probe"] and (decision == "block" or row["actual_read_complete"]) for row in rows) == 1
                   for name in ("opencode", "codex") for decision in ("allow", "block"))
    export = safe_original_events(events, tenant=tenant, run_id=run_id)
    return {"schema": "tessera.external_client_evidence.v1", "run_id": run_id, "accepted": accepted,
            "provenance": "server_observed_authenticated_principals_and_client_reported_model_identity",
            "model_execution_proven_by_server": False, "hosted_upload_acknowledged": False,
            "audit_chain_valid": True, "event_count": len(events), "event_head_sha256": previous,
            "correlations": rows, "denied_contacts": 0, "adapter_contacts": contacts,
            "canonical_export_status": "ORIGINAL_UNCHANGED_VALIDATED" if export is not None else "UNPROVED",
            "canonical_events": export}


def safe_original_events(events, *, tenant, run_id):
    """Export original stamped objects only; unexpected content is never scrubbed."""
    top = {"schemaVersion", "eventId", "tenantId", "eventType", "occurredAt", "payload", "prevEventHash", "eventHash"}
    decision_keys = {"mode", "decision", "policy_id", "reason", "upstream", "tool_call", "principal_id", "request_id",
                     "decision_error", "canonical_tool_name", "effective_tool_name", "conversation_id", "cost_source", "cost_band"}
    identifier = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
    try:
        for event in events:
            if (not set(event) <= top | {"pricingSnapshotId"} or not top <= set(event)
                    or event["schemaVersion"] != "v0.1" or event["tenantId"] != tenant
                    or not identifier.fullmatch(event["eventId"])
                    or not re.fullmatch(r"[0-9TZ:.+-]{20,40}", event["occurredAt"])
                    or "pricingSnapshotId" in event and not identifier.fullmatch(event["pricingSnapshotId"])):
                return None
            payload, kind = event["payload"], event["eventType"]
            if kind == "startup":
                if payload != {"deployment_id": tenant, "mode": "enforcement"}:
                    return None
                continue
            if payload.get("principal_id") not in {"opencode", "codex"}:
                return None
            if not re.fullmatch(r"[a-f0-9-]{32,36}", payload.get("request_id", "")):
                return None
            if kind == "passthrough":
                if (set(payload) != {"upstream", "method", "principal_id", "request_id"}
                        or not (payload["upstream"] == "control" and payload["method"] in {
                            "initialize", "notifications/initialized", "tools/list", "ping"}
                            or payload["upstream"] == "tessera" and payload["method"] in {
                                "initialize", "notifications/initialized", "server/discover"})):
                    return None
                continue
            if (payload.get("upstream") != "control" or kind != "decision"
                    or set(payload) - decision_keys or payload.get("mode") != "enforcement"
                    or payload.get("decision") not in {"allow", "block"} or payload.get("decision_error") is not None
                    or payload.get("policy_id") not in {"default", "workload-tessera-read"}
                    or payload.get("reason") not in {None, "Permit the pinned read-only Tessera UI deployment query; other workloads remain blocked."}
                    or payload.get("canonical_tool_name") != "read_workload_status"
                    or payload.get("effective_tool_name") != "read_workload_status"
                    or payload.get("conversation_id") != run_id + "-" + payload["principal_id"]
                    or payload.get("cost_source") not in {None, "price_table", "infracost_live", "miss"}
                    or payload.get("cost_band") not in {None, "", "high", "medium", "ceiling"}):
                return None
            call = payload["tool_call"]
            if set(call) != {"name", "arguments", "_meta"} or call["name"] != "read_workload_status":
                return None
            expected = {"operation": "amplify.GetJob", "branch": "main",
                        "app_id": "d2nxbpvv53qeqc" if payload["decision"] == "allow" else "d3s9n6ffim4722",
                        "job_id": "84" if payload["decision"] == "allow" else "60"}
            meta = call["_meta"]
            if (call["arguments"] != expected or set(meta) - {"run_id", "request_id", "tool_use_id", "conversation_id", "external_client", "progressToken"}
                    or meta.get("run_id") != run_id or meta.get("external_client") != payload["principal_id"]
                    or meta.get("conversation_id") != payload["conversation_id"]
                    or not identifier.fullmatch(meta.get("request_id", "")) or not identifier.fullmatch(meta.get("tool_use_id", ""))):
                return None
            progress = meta.get("progressToken")
            if progress is not None and not (type(progress) is int and 0 <= progress < 2**63
                    or type(progress) is str and re.fullmatch(r"[0-9]{1,20}|[a-f0-9]{32}|[a-f0-9-]{36}", progress)):
                return None
        if len(json.dumps(events, separators=(",", ":")).encode()) > 196608:
            return None
        return events
    except (KeyError, TypeError, ValueError):
        return None
