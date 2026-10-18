"""One-shot scripted connection check; imported only by the approved RAM operator.

Caller must verify both JWTs and the connection identity before calling once.
This module does not authorize OAuth, refresh, revocation, models, or tool calls.
Private cache/audit bytes remain in a private /dev/shm directory until cleanup.
Only fixed sanitized receipts leave this function. No network occurs on import.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from tessera.audit.canonical_json import canonical_json
from tessera.audit.chain import HashChain
from tessera.audit.cloud_uploader import AuditCloudUploader
from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.sqlite import SqliteSink
from tessera.auth.base import SCOPE_RE
from tessera.cloud_sync import CloudPolicySync, snapshot_digest
from tessera.policy.cloud_loader import _parse_policy_yaml

ENDPOINT = "https://auth.tessera.cloudmorph.ai"
SCHEMA = "tessera.scripted_connection_test.v2"
LABEL = "SCRIPTED CONNECTION TEST"
MAX_TOTAL_SECONDS = 22.0
MAX_HTTP_SECONDS = 10.0
MAX_POLICY_BYTES = 524_288
MAX_ACK_BYTES = 16_384
MAX_POST_BYTES = 16_384
MAX_POLICIES = 32
MAX_YAML_BYTES = 65_536
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PHASE_FAILURE_CODES = {
    "INPUT": "VERIFIED_INPUT_PROCESSING_FAILED",
    "RAM_SETUP": "PRIVATE_RAM_SETUP_FAILED",
    "CACHE_INIT": "POLICY_CACHE_INIT_FAILED",
    "HTTP_CLIENT_INIT": "HTTP_CLIENT_INIT_FAILED",
    "POLICY_HTTP": "POLICY_HTTP_FAILED",
    "POLICY_JSON": "POLICY_JSON_INVALID",
    "SNAPSHOT_SHAPE": "SNAPSHOT_VALIDATION_FAILED",
    "STRICT_SNAPSHOT": "STRICT_SNAPSHOT_FAILED",
    "CACHE_VERIFY": "POLICY_CACHE_VERIFICATION_FAILED",
    "STRICT_POLICY_LOAD": "STRICT_POLICY_LOAD_REJECTED",
    "AUDIT_LOCAL": "LOCAL_CANONICAL_AUDIT_FAILED",
    "AUDIT_HTTP": "AUDIT_HTTP_FAILED",
    "AUDIT_JSON": "AUDIT_ACK_JSON_INVALID",
    "AUDIT_ACK": "CANONICAL_ACK_VALIDATION_FAILED",
    "AUDIT_CHECKPOINT": "LOCAL_AUDIT_CHECKPOINT_FAILED",
    "RAM_CLEANUP": "PRIVATE_RAM_CLEANUP_FAILED",
}
DIAGNOSTIC_PHASES = frozenset((*_PHASE_FAILURE_CODES, "HTTP_START", "HTTP_HEADERS", "HTTP_BODY_COMPLETE"))
_SAFE_EXCEPTION_TYPES = (
    (httpx.ConnectTimeout, "ConnectTimeout"), (httpx.ReadTimeout, "ReadTimeout"),
    (httpx.WriteTimeout, "WriteTimeout"), (httpx.PoolTimeout, "PoolTimeout"),
    (httpx.ConnectError, "ConnectError"), (httpx.ReadError, "ReadError"),
    (httpx.WriteError, "WriteError"), (httpx.CloseError, "CloseError"),
    (httpx.LocalProtocolError, "LocalProtocolError"),
    (httpx.RemoteProtocolError, "RemoteProtocolError"),
    (httpx.ProxyError, "ProxyError"), (httpx.UnsupportedProtocol, "UnsupportedProtocol"),
    (TimeoutError, "TimeoutError"), (json.JSONDecodeError, "JSONDecodeError"),
    (ValueError, "ValueError"), (TypeError, "TypeError"), (ValidationError, "ValidationError"),
)
DIAGNOSTIC_EXCEPTION_CLASSES = frozenset((*(name for _kind, name in _SAFE_EXCEPTION_TYPES), "Other"))
_REQUEST_ID_HEADERS = {
    b"apigw-requestid": "apigw-requestid", b"x-amzn-requestid": "x-amzn-requestid",
    b"x-amz-apigw-id": "x-amz-apigw-id",
}
_USED = False
_USE_LOCK = threading.Lock()


class _BlockedError(Exception):
    """Carries a fixed code, never server or identity material."""


def _require(condition: Any, code: str) -> None:
    if not condition:
        raise _BlockedError(code)


def _receipt() -> dict[str, Any]:
    return {"schema": SCHEMA, "status": "BLOCKED", "reason_code": "NOT_STARTED",
            "label": LABEL, "snapshot_digest": None, "policy_count": None,
            "ack_written": None, "stream_id": None, "chain_head": None,
            "proxy_runtime_active": False, "independent_agent_observed": False, "diagnostics": []}


def _ram_available() -> bool:
    root = Path("/dev/shm")  # noqa: S108 - fixed root verified as tmpfs; no disk fallback
    if not root.is_dir() or root.resolve() != root:
        return False
    # Refuse a disk-backed fallback; do not export mount or filesystem details.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, separator, after = line.partition(" - ")
        fields = before.split()
        if separator and len(fields) > 4 and fields[4] == "/dev/shm":  # noqa: S108 - verify the exact fixed tmpfs mountpoint
            return after.split()[0] == "tmpfs"
    return False


def _safe_exception_class(error: BaseException) -> str:
    # Exact types only: never expose arbitrary class names or exception values.
    return next((name for kind, name in _SAFE_EXCEPTION_TYPES if type(error) is kind), "Other")


def _request_id_hashes(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, value in raw_headers:
        name = _REQUEST_ID_HEADERS.get(raw_name.lower())
        if name is None:
            continue
        if name in seen:
            hashes.pop(name, None)  # Ambiguous duplicate values are never projected.
            continue
        seen.add(name)
        if 0 < len(value) <= 256 and value.isascii():
            hashes[name] = hashlib.sha256(value).hexdigest()
    return hashes


def _note_failure(state: dict[str, Any], error: BaseException) -> None:
    records = state["diagnostics"]
    if not records:
        return
    record = records[-1]
    if state["phase"] in DIAGNOSTIC_PHASES:
        record["phase"] = state["phase"]
    # Preserve the precise original classification when wrapping a validator error.
    if type(error) is not _BlockedError or record["exception_class"] is None:
        record["exception_class"] = _safe_exception_class(error)


async def _bounded_json(client: httpx.AsyncClient, method: str, path: str,
                        token: str, state: dict[str, Any], body: bytes | None = None) -> Any:
    """One bounded attempt; diagnostics retain only the approved finite projection.

    started means a client stream attempt, not proof of server contact. Elapsed
    time covers this HTTP/body/JSON scope only, clamped to 0..30000 ms. Later
    snapshot/cache failures may update phase/class but never extend HTTP elapsed.
    """
    allowed = {"GET": CloudPolicySync.REMOTE_PATH, "POST": AuditCloudUploader.REMOTE_PATH}
    _require(method in allowed and path == allowed[method], "HTTP_SCOPE_INVALID")
    key = "get_attempted" if method == "GET" else "post_attempted"
    records = state["diagnostics"]
    ordered = (method == "GET" and not records) or (
        method == "POST" and len(records) == 1 and records[0]["method"] == "GET")
    _require(not state[key] and len(records) < 2 and ordered, "HTTP_CALL_LIMIT")
    _require(body is None or len(body) <= MAX_POST_BYTES, "HTTP_REQUEST_TOO_LARGE")
    record = {"method": method, "phase": "HTTP_START", "started": False,
              "headers_received": False, "body_complete": False, "elapsed_ms": 0,
              "http_status": None, "exception_class": None, "request_id_sha256": {}}
    records.append(record)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    state["phase"] = "POLICY_HTTP" if method == "GET" else "AUDIT_HTTP"
    state[key] = True
    limit = MAX_POLICY_BYTES if method == "GET" else MAX_ACK_BYTES
    started_at = time.monotonic()
    record["started"] = True
    try:
        async with client.stream(method, ENDPOINT + path, headers=headers, content=body) as response:
            # Preserve response evidence even when status or body checks reject it.
            record["headers_received"] = True
            status = response.status_code
            record["http_status"] = status if type(status) is int and 100 <= status <= 599 else None
            record["request_id_sha256"] = _request_id_hashes(response.headers.raw)
            record["phase"] = "HTTP_HEADERS"
            _require(200 <= status < 300, "HTTP_RESPONSE_REJECTED")
            _require(sum(len(k) + len(v) for k, v in response.headers.raw) <= 16_384,
                     "HTTP_HEADERS_TOO_LARGE")
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                _require(len(data) + len(chunk) <= limit, "HTTP_RESPONSE_TOO_LARGE")
                data.extend(chunk)
            record["body_complete"] = True
            record["phase"] = "HTTP_BODY_COMPLETE"
        state["phase"] = "POLICY_JSON" if method == "GET" else "AUDIT_JSON"
        record["phase"] = state["phase"]
        return json.loads(data)
    except Exception as error:
        record["exception_class"] = _safe_exception_class(error)
        raise
    finally:
        record["elapsed_ms"] = max(0, min(30_000, int((time.monotonic() - started_at) * 1000)))


def _validate_snapshot_payload(payload: Any) -> None:
    _require(isinstance(payload, dict) and payload.get("complete") is True,
             "SNAPSHOT_INCOMPLETE")
    rows = payload.get("items")
    _require(isinstance(rows, list) and len(rows) <= MAX_POLICIES, "SNAPSHOT_LIMIT")
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get("yaml_text"), str)
                 and len(row["yaml_text"].encode()) <= MAX_YAML_BYTES,
                 "SNAPSHOT_POLICY_LIMIT")


def _verify_ack(ack: Any, event: dict[str, Any], tenant: str, stream: str) -> None:
    # Exact single-batch checks copied from canonical
    # tessera/audit/cloud_uploader.py:AuditCloudUploader.upload_sqlite_once.
    accepted = ack.get("accepted_event_ids") if isinstance(ack, dict) else None
    _require(isinstance(accepted, list) and accepted == [event["eventId"]]
             and ack.get("skipped", 0) == 0 and ack.get("tenant_id") == tenant
             and ack.get("stream_id") == stream and ack.get("chain_head") == event["eventHash"]
             and ack.get("verification") == "canonical_verified"
             and type(ack.get("written")) is int and type(ack.get("duplicates")) is int
             and ack["written"] >= 0 and ack["duplicates"] >= 0
             and ack["written"] + ack["duplicates"] == 1, "CANONICAL_ACK_INVALID")
    # A new, random stream must acknowledge the one authorized new test write.
    _require(ack["written"] == 1 and ack["duplicates"] == 0, "NEW_TEST_WRITE_NOT_ACKNOWLEDGED")


async def _run(token: str, tenant: str, directory: Path,
               result: dict[str, Any], state: dict[str, Any]) -> None:
    state["phase"] = "CACHE_INIT"
    cache = directory / "policy-cache.db"
    cache.touch(mode=0o600, exist_ok=False)
    sync = CloudPolicySync(endpoint=ENDPOINT, oauth_token=token, cache_path=cache,
                           strict=True, expected_tenant_id=tenant)
    # httpx's default transport has zero retries. Preserve the operator's
    # existing proxy/certificate environment; explicit TLS verification remains on.
    state["phase"] = "HTTP_CLIENT_INIT"
    async with httpx.AsyncClient(verify=True, follow_redirects=False,
                                timeout=httpx.Timeout(MAX_HTTP_SECONDS),
                                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
        payload = await _bounded_json(client, "GET", sync.REMOTE_PATH, token, state)
        state["phase"] = "SNAPSHOT_SHAPE"
        _validate_snapshot_payload(payload)
        # Reuse the existing strict complete/tenant/YAML/digest verifier verbatim.
        state["phase"] = "STRICT_SNAPSHOT"
        try:
            items = sync._cache_verified_snapshot(payload)
        except (ValueError, TypeError) as error:
            _note_failure(state, error)
            # Exact validator messages may contain private YAML/identity.
            raise _BlockedError("STRICT_SNAPSHOT_REJECTED") from None
        state["phase"] = "CACHE_VERIFY"
        cached = sync.load_from_cache()
        def exact(rows: list[dict[str, Any]]) -> dict[str, tuple[bytes, bool]]:
            return {r["policy_id"]: (r["yaml_text"].encode(), r["enabled"]) for r in rows}

        _require(exact(cached) == exact(items) and snapshot_digest(cached) == payload["snapshot_digest"],
                 "CACHE_EXACT_BYTES_MISMATCH")
        state["phase"] = "STRICT_POLICY_LOAD"
        for row in cached:
            parsed = _parse_policy_yaml(row["yaml_text"], source="scripted connection snapshot")
            _require(parsed.id == row["policy_id"], "STRICT_POLICY_ID_MISMATCH")
        digest = payload["snapshot_digest"]
        _require(isinstance(digest, str) and _HASH.fullmatch(digest), "SNAPSHOT_DIGEST_INVALID")
        result.update(snapshot_digest=digest, policy_count=len(cached))
        state["phase"] = "AUDIT_LOCAL"
        stream = "scripted-connection-test-" + uuid4().hex
        result["stream_id"] = stream
        sink = SqliteSink(directory / "audit.db")
        failures: list[bool] = []
        emitter = AuditEmitter(tenant_id=tenant, sinks=[sink], hash_chain=HashChain(),
                               on_sink_failure=lambda _name, _error: failures.append(True))
        try:
            event = emitter.emit("audit_self_check", session_id=stream, payload={
                "label": LABEL, "kind": "SCRIPTED_CONNECTION_TEST",
                "purpose": "OAuth policy snapshot and canonical audit transport check",
                "snapshot_digest": digest, "policy_count": len(cached),
                "strict_snapshot_verified": True, "proxy_runtime_active": False,
                "model_calls": 0, "tool_calls": 0, "business_actions": 0,
                "independent_agent_observed": False,
            })
            stored = list(sink.iter_events(scope=tenant))
            _require(not failures and stored == [event] and HashChain.verify_event_hash(event)
                     and event.get("prevEventHash") == "" and sink.head_hash(tenant) == event["eventHash"],
                     "LOCAL_CANONICAL_AUDIT_INVALID")
            # Same canonical envelope as upload_sqlite_once; never legacy enqueue/flush.
            body = canonical_json({"format": "tessera.audit.canonical.v1", "stream_id": stream,
                                   "scope": tenant, "events": [{"seq": 1, "event": stored[0]}],
                                   "chain_head": event["eventHash"]})
            ack = await _bounded_json(client, "POST", AuditCloudUploader.REMOTE_PATH,
                                      token, state, body)
            state["phase"] = "AUDIT_ACK"
            _verify_ack(ack, event, tenant, stream)
            result.update(ack_written=1, chain_head=event["eventHash"])
            # Canonical SQLite checkpoint, matching the existing uploader's exact ACK path.
            state["phase"] = "AUDIT_CHECKPOINT"
            with sqlite3.connect(directory / "audit.db") as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS cloud_upload_checkpoint ("
                             "endpoint TEXT, tenant_id TEXT, stream_id TEXT, seq INTEGER NOT NULL, "
                             "event_hash TEXT NOT NULL, PRIMARY KEY(endpoint,tenant_id,stream_id))")
                conn.execute("INSERT INTO cloud_upload_checkpoint VALUES (?,?,?,?,?)",
                             (ENDPOINT, tenant, stream, 1, event["eventHash"]))
            result.update(status="ACKNOWLEDGED", reason_code="STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
                          ack_written=1, chain_head=event["eventHash"])
        finally:
            emitter.close()
            sync._oauth_token = ""


def run_connection_use(access_token: str, verified_access_claims: dict[str, Any]) -> dict[str, Any]:
    """Use one already-verified connection exactly once; return no private identity.

    This function never verifies or refreshes JWTs; the approved caller must do
    both verifications first and revoke the exact refresh token in its finally.
    The receipt proves a strict snapshot and scripted audit ACK only, never proxy
    activation, model use, business tool use, or independent-agent behavior.
    """
    global _USED
    result = _receipt()
    with _USE_LOCK:
        if _USED:
            result["reason_code"] = "CONNECTION_USE_ALREADY_ATTEMPTED"
            return result
        _USED = True
    state = {"get_attempted": False, "post_attempted": False, "phase": "INPUT",
             "diagnostics": result["diagnostics"]}
    try:
        _require(isinstance(access_token, str) and 0 < len(access_token) <= 16_384
                 and "\r" not in access_token and "\n" not in access_token, "VERIFIED_INPUT_REQUIRED")
        _require(isinstance(verified_access_claims, dict), "VERIFIED_INPUT_REQUIRED")
        tenant = verified_access_claims.get("tenant_id")
        scope = verified_access_claims.get("scope")
        _require(isinstance(tenant, str) and SCOPE_RE.fullmatch(tenant)
                 and isinstance(scope, str)
                 and {"tessera:policies:read", "tessera:audit:write"} <= set(scope.split()),
                 "VERIFIED_SCOPE_OR_TENANT_REQUIRED")
        state["phase"] = "RAM_SETUP"
        _require(_ram_available(), "PRIVATE_RAM_UNAVAILABLE")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise _BlockedError("SYNCHRONOUS_OPERATOR_REQUIRED")
        with tempfile.TemporaryDirectory(prefix="tessera-connection-test-", dir="/dev/shm") as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            asyncio.run(asyncio.wait_for(_run(access_token, tenant, directory, result, state),
                                         timeout=MAX_TOTAL_SECONDS))
            state["phase"] = "RAM_CLEANUP"
        _require(not directory.exists(), "PRIVATE_RAM_CLEANUP_FAILED")
    except _BlockedError as exc:
        _note_failure(state, exc)
        result.update(status="UNKNOWN" if state["post_attempted"] else "BLOCKED", reason_code=str(exc))
    except httpx.TimeoutException as error:
        _note_failure(state, error)
        code = "AUDIT_HTTP_TIMEOUT" if state["post_attempted"] else "POLICY_HTTP_TIMEOUT"
        result.update(status="UNKNOWN" if state["post_attempted"] else "BLOCKED", reason_code=code)
    except httpx.TransportError as error:
        _note_failure(state, error)
        code = "AUDIT_HTTP_TRANSPORT_FAILED" if state["post_attempted"] else "POLICY_HTTP_TRANSPORT_FAILED"
        result.update(status="UNKNOWN" if state["post_attempted"] else "BLOCKED", reason_code=code)
    except json.JSONDecodeError as error:
        _note_failure(state, error)
        code = "AUDIT_ACK_JSON_INVALID" if state["post_attempted"] else "POLICY_JSON_INVALID"
        result.update(status="UNKNOWN" if state["post_attempted"] else "BLOCKED", reason_code=code)
    except TimeoutError as error:
        _note_failure(state, error)
        code = {"POLICY_HTTP": "POLICY_HTTP_DEADLINE", "AUDIT_HTTP": "AUDIT_HTTP_DEADLINE"}.get(
            state["phase"], "CONNECTION_USE_TIMEOUT")
        result.update(status="UNKNOWN" if state["post_attempted"] else "BLOCKED", reason_code=code)
    except Exception as error:
        _note_failure(state, error)
        code = _PHASE_FAILURE_CODES.get(state["phase"], "CONNECTION_USE_FAILED")
        result.update(status="UNKNOWN" if state["post_attempted"] else "BLOCKED", reason_code=code)
    return result
