"""Reusable, network-denied verification of the maintained OAuth diagnostics helper.

Run with the existing Python environment. Uses only public synthetic fixtures,
real canonical policy/audit code, fake HTTP, and private temporary RAM SQLite.
It never performs OAuth, AWS, models, or real HTTP. Prints one finite JSON result.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_HELPER_SHA = "1b4da623d9d1fe8ea229d702d3964476df53a42ff42baa7043cf375587d7c9f0"
_SCHEMA = "tessera.offline_oauth_diagnostics_checks.v1"
_PHASES = {
    "INPUT", "RAM_SETUP", "CACHE_INIT", "HTTP_CLIENT_INIT", "POLICY_HTTP", "POLICY_JSON",
    "SNAPSHOT_SHAPE", "STRICT_SNAPSHOT", "CACHE_VERIFY", "STRICT_POLICY_LOAD", "AUDIT_LOCAL",
    "AUDIT_HTTP", "AUDIT_JSON", "AUDIT_ACK", "AUDIT_CHECKPOINT", "RAM_CLEANUP",
    "HTTP_START", "HTTP_HEADERS", "HTTP_BODY_COMPLETE",
}
_EXCEPTIONS = {
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ConnectError", "ReadError",
    "WriteError", "CloseError", "LocalProtocolError", "RemoteProtocolError", "ProxyError",
    "UnsupportedProtocol", "TimeoutError", "JSONDecodeError", "ValueError", "TypeError",
    "ValidationError", "Other",
}
_HEADERS = {"apigw-requestid", "x-amzn-requestid", "x-amz-apigw-id"}
_RECORD_KEYS = {"method", "phase", "started", "headers_received", "body_complete", "elapsed_ms",
                "http_status", "exception_class", "request_id_sha256"}
_RECEIPT_KEYS = {"schema", "status", "reason_code", "label", "snapshot_digest", "policy_count",
                 "ack_written", "stream_id", "chain_head", "proxy_runtime_active",
                 "independent_agent_observed", "diagnostics"}
_CASES = {
    "valid": "STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
    "get_read_timeout": "POLICY_HTTP_TIMEOUT", "get_connect_timeout": "POLICY_HTTP_TIMEOUT",
    "get_transport": "POLICY_HTTP_TRANSPORT_FAILED", "get_401": "HTTP_RESPONSE_REJECTED",
    "get_non_json": "POLICY_JSON_INVALID", "snapshot_id_mismatch": "STRICT_SNAPSHOT_REJECTED",
    "snapshot_digest_mismatch": "STRICT_SNAPSHOT_REJECTED",
    "snapshot_validation_error": "STRICT_SNAPSHOT_REJECTED",
    "snapshot_yaml_error": "STRICT_SNAPSHOT_FAILED", "post_read_timeout": "AUDIT_HTTP_TIMEOUT",
    "post_transport": "AUDIT_HTTP_TRANSPORT_FAILED", "post_non_json": "AUDIT_ACK_JSON_INVALID",
    "outer_get_timeout": "POLICY_HTTP_DEADLINE", "outer_post_timeout": "AUDIT_HTTP_DEADLINE",
    "client_init_error": "HTTP_CLIENT_INIT_FAILED", "cache_init_error": "POLICY_CACHE_INIT_FAILED",
    "header_too_long": "STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
    "header_duplicate": "STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
    "header_non_ascii": "STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
    "header_unknown_only": "STRICT_SNAPSHOT_AND_TEST_AUDIT_ACKNOWLEDGED",
}
_PUBLIC_IDS = [(b"apigw-requestid", b"public-offline-api-id"),
               (b"x-amzn-requestid", b"public-offline-request-id"),
               (b"x-amz-apigw-id", b"public-offline-gateway-id")]


class _CheckError(Exception):
    pass


def _check(condition: Any, code: str) -> None:
    if not condition:
        raise _CheckError(code)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    _check(spec is not None and spec.loader is not None, "HELPER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _check(module.DIAGNOSTIC_PHASES == _PHASES, "PHASE_ENUM_MISMATCH")
    _check(module.DIAGNOSTIC_EXCEPTION_CLASSES == _EXCEPTIONS, "EXCEPTION_ENUM_MISMATCH")
    return module


def _validate_receipt(result: dict[str, Any]) -> None:
    _check(set(result) == _RECEIPT_KEYS, "RECEIPT_KEYS")
    _check(result["schema"] == "tessera.scripted_connection_test.v2", "CONSUMER_SCHEMA")
    _check(result["proxy_runtime_active"] is False and result["independent_agent_observed"] is False,
           "TRUTHFUL_LIMITS")
    records = result["diagnostics"]
    _check(isinstance(records, list) and len(records) <= 2, "RECORD_BOUND")
    _check([r["method"] for r in records] in [[], ["GET"], ["GET", "POST"]], "METHOD_ORDER")
    for record in records:
        _check(set(record) == _RECORD_KEYS, "RECORD_KEYS")
        _check(record["phase"] in _PHASES, "RECORD_PHASE")
        _check(all(type(record[k]) is bool for k in ["started", "headers_received", "body_complete"]),
               "RECORD_BOOLEAN_TYPES")
        _check(not record["body_complete"] or record["headers_received"], "BODY_IMPLIES_HEADERS")
        _check(not record["headers_received"] or record["started"], "HEADERS_IMPLIES_STARTED")
        _check(type(record["elapsed_ms"]) is int and 0 <= record["elapsed_ms"] <= 30_000,
               "ELAPSED_BOUND")
        status = record["http_status"]
        _check(status is None or type(status) is int and 100 <= status <= 599, "STATUS_BOUND")
        _check(record["exception_class"] is None or record["exception_class"] in _EXCEPTIONS,
               "EXCEPTION_BOUND")
        hashes = record["request_id_sha256"]
        _check(isinstance(hashes, dict) and set(hashes) <= _HEADERS, "HEADER_NAMES")
        _check(all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()),
               "HEADER_HASHES")
        if not record["headers_received"]:
            _check(status is None and hashes == {}, "NO_HEADERS_NO_EVIDENCE")
    encoded = json.dumps(result)
    _check(all(value not in encoded for value in ["offline_fixture", "OFFLINE_SYNTHETIC_NOT_A_TOKEN",
               "public-offline-api-id", "public-offline-request-id", "public-offline-gateway-id",
               "id: offline-connection-check", "private-offline-error-message"]), "REDACTION")


def _fake_client(mode, module, fixture, calls, response_headers, httpx, canonical_json):
    class FakeClient:
        def __init__(self, *_args, **kwargs):
            _check(kwargs.get("timeout") is None or kwargs["timeout"].read == 10.0, "HTTP_TIMEOUT_LIMIT")
            if mode == "client_init_error":
                raise OSError("private-offline-error-message")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        @contextlib.asynccontextmanager
        async def stream(self, method, url, headers, content=None):
            calls.append(method)
            _check(url.startswith(module.ENDPOINT + "/api/"), "FIXED_ENDPOINT")
            if (method == "GET" and mode == "get_read_timeout") or (method == "POST" and mode == "post_read_timeout"):
                raise httpx.ReadTimeout("private-offline-error-message")
            if method == "GET" and mode == "get_connect_timeout":
                raise httpx.ConnectTimeout("private-offline-error-message")
            if (method == "GET" and mode == "get_transport") or (method == "POST" and mode == "post_transport"):
                raise httpx.ConnectError("private-offline-error-message")
            if (method == "GET" and mode == "outer_get_timeout") or (method == "POST" and mode == "outer_post_timeout"):
                raise TimeoutError("private-offline-error-message")
            if method == "GET":
                data = b"not-json" if mode == "get_non_json" else canonical_json(fixture)
            else:
                body = json.loads(content)
                event = body["events"][0]["event"]
                data = b"not-json" if mode == "post_non_json" else canonical_json({
                    "accepted_event_ids": [event["eventId"]], "skipped": 0,
                    "tenant_id": "offline_fixture", "stream_id": body["stream_id"],
                    "chain_head": body["chain_head"], "verification": "canonical_verified",
                    "written": 1, "duplicates": 0,
                })
            yield httpx.Response(401 if mode == "get_401" else 200, content=data,
                                 headers=response_headers, request=httpx.Request(method, url))

    return FakeClient


def _verify(path: Path) -> list[dict[str, Any]]:
    import httpx

    from tessera.audit.canonical_json import canonical_json
    from tessera.cloud_sync import snapshot_digest

    items = [{"policy_id": "offline-connection-check", "enabled": True,
              "yaml_text": "id: offline-connection-check\nname: Offline connection check\naction: log_only\n"}]
    payload = {"complete": True, "tenant_id": "offline_fixture", "items": items,
               "snapshot_digest": snapshot_digest(items)}
    checks = []
    for mode, expected in _CASES.items():
        module = _load(path, "offline_diagnostics_" + mode)
        fixture = json.loads(json.dumps(payload))
        if mode == "snapshot_id_mismatch":
            fixture["items"][0]["policy_id"] = "different-id"
        if mode == "snapshot_validation_error":
            fixture["items"][0]["yaml_text"] = "id: offline-connection-check\naction: log_only\n"
        if mode == "snapshot_yaml_error":
            fixture["items"][0]["yaml_text"] = "[invalid: yaml"
        fixture["snapshot_digest"] = snapshot_digest(fixture["items"])
        if mode == "snapshot_digest_mismatch":
            fixture["snapshot_digest"] = "0" * 64
        calls: list[str] = []
        headers = _PUBLIC_IDS
        if mode == "header_too_long":
            headers = [(b"apigw-requestid", b"a" * 257)]
        if mode == "header_duplicate":
            headers = [(b"apigw-requestid", b"public-offline-api-id"), (b"ApIgW-ReQuEsTiD", b"duplicate")]
        if mode == "header_non_ascii":
            headers = [(b"apigw-requestid", b"\xff")]
        if mode == "header_unknown_only":
            headers = [(b"x-unapproved-header", b"not-exportable")]

        fake_client_type = _fake_client(mode, module, fixture, calls, headers, httpx, canonical_json)
        module.httpx = SimpleNamespace(AsyncClient=fake_client_type, Timeout=httpx.Timeout, Limits=httpx.Limits,
                                       TimeoutException=httpx.TimeoutException, TransportError=httpx.TransportError)
        if mode == "cache_init_error":
            def bad_cache(*_args, **_kwargs):
                raise OSError("private-offline-error-message")
            module.CloudPolicySync = bad_cache
        result = module.run_connection_use("OFFLINE_SYNTHETIC_NOT_A_TOKEN", {
            "tenant_id": "offline_fixture", "scope": "tessera:policies:read tessera:audit:write"})
        _validate_receipt(result)
        _check(result["reason_code"] == expected, "CASE_REASON_" + mode.upper())
        _check(calls.count("GET") <= 1 and calls.count("POST") <= 1, "CALL_BOUND")
        records = result["diagnostics"]
        if mode in {"get_read_timeout", "get_connect_timeout"}:
            _check(len(records) == 1 and records[0]["started"] and not records[0]["headers_received"],
                   "TIMEOUT_BEFORE_HEADERS")
        if mode == "get_401":
            _check(records[0]["http_status"] == 401 and records[0]["headers_received"]
                   and not records[0]["body_complete"], "REJECTED_STATUS_RETAINED")
        if mode == "get_non_json":
            _check(records[0]["http_status"] == 200 and records[0]["body_complete"]
                   and records[0]["exception_class"] == "JSONDecodeError", "BAD_JSON_DIAGNOSTIC")
        if mode.startswith("snapshot_"):
            _check(records[0]["phase"] == "STRICT_SNAPSHOT" and records[0]["http_status"] == 200
                   and records[0]["body_complete"], "SNAPSHOT_PHASE")
        if mode == "snapshot_validation_error":
            _check(records[0]["exception_class"] == "ValidationError", "VALIDATION_CLASS")
        if mode.startswith("post_") or mode == "outer_post_timeout":
            _check(result["status"] == "UNKNOWN" and len(records) == 2, "UNKNOWN_POST")
        if mode.startswith("header_"):
            _check(all(r["request_id_sha256"] == {} for r in records), "OMIT_UNSAFE_HEADERS")
        if mode == "valid":
            wanted = {k.decode(): hashlib.sha256(v).hexdigest() for k, v in _PUBLIC_IDS}
            _check(result["status"] == "ACKNOWLEDGED" and result["ack_written"] == 1
                   and all(r["request_id_sha256"] == wanted for r in records), "HASH_ONLY_HEADERS")
            _check(all(r["exception_class"] is None for r in records), "SUCCESS_HAS_NO_EXCEPTION")
        count = len(calls)
        once = module.run_connection_use("OFFLINE_SYNTHETIC_NOT_A_TOKEN", {})
        _check(once["reason_code"] == "CONNECTION_USE_ALREADY_ATTEMPTED" and len(calls) == count, "ONCE_GUARD")
        _validate_receipt(once)
        checks.append({"case": mode, "status": "PASS", "diagnostic_count": len(records)})

    # Exercise the lower-level two-record guard with fake HTTP only.
    module = _load(path, "offline_diagnostics_record_bound")
    fake_client_type = _fake_client("valid", module, payload, [], _PUBLIC_IDS, httpx, canonical_json)
    module.httpx = SimpleNamespace(AsyncClient=fake_client_type, Timeout=httpx.Timeout, Limits=httpx.Limits,
                                   TimeoutException=httpx.TimeoutException, TransportError=httpx.TransportError)
    state = {"get_attempted": False, "post_attempted": False, "phase": "POLICY_HTTP", "diagnostics": []}

    async def check_bound():
        async with fake_client_type() as client:
            await module._bounded_json(client, "GET", module.CloudPolicySync.REMOTE_PATH,
                                       "OFFLINE_SYNTHETIC_NOT_A_TOKEN", state)
            try:
                await module._bounded_json(client, "GET", module.CloudPolicySync.REMOTE_PATH,
                                           "OFFLINE_SYNTHETIC_NOT_A_TOKEN", state)
            except module._BlockedError:
                pass
            else:
                raise _CheckError("DUPLICATE_GET_ALLOWED")
            body = canonical_json({"events": [{"event": {"eventId": "offline-public-event"}}],
                                   "stream_id": "offline-public-stream", "chain_head": "0" * 64})
            await module._bounded_json(client, "POST", module.AuditCloudUploader.REMOTE_PATH,
                                       "OFFLINE_SYNTHETIC_NOT_A_TOKEN", state, body)
            try:
                await module._bounded_json(client, "POST", module.AuditCloudUploader.REMOTE_PATH,
                                           "OFFLINE_SYNTHETIC_NOT_A_TOKEN", state, body)
            except module._BlockedError:
                pass
            else:
                raise _CheckError("THIRD_RECORD_ALLOWED")
        _check(len(state["diagnostics"]) == 2, "DUPLICATE_RECORD_BOUND")

    asyncio.run(check_bound())
    checks.append({"case": "duplicate_get_post_two_record_bound", "status": "PASS", "diagnostic_count": 2})
    return checks


def main() -> int:
    logging.disable(logging.CRITICAL)
    path = Path(__file__).with_name("ephemeral_oauth_acceptance_use.py")
    attempted: list[bool] = []
    original = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

    def deny(*_args, **_kwargs):
        attempted.append(True)
        raise _CheckError("NETWORK_ATTEMPTED")

    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    socket.getaddrinfo = deny
    try:
        _check(hashlib.sha256(path.read_bytes()).hexdigest() == _HELPER_SHA, "HELPER_HASH_MISMATCH")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        checks = _verify(path)
        _check(not attempted, "NETWORK_ATTEMPTED")
        print(json.dumps({"schema": _SCHEMA, "status": "PASS", "helper_sha256": _HELPER_SHA,
                          "checks": checks, "actual_network_attempts": 0,
                          "actual_oauth_or_aws_or_model_calls": 0}, sort_keys=True))
        return 0
    except _CheckError as error:
        print(json.dumps({"schema": _SCHEMA, "status": "FAIL", "check": str(error)}, sort_keys=True))
    except Exception:
        print(json.dumps({"schema": _SCHEMA, "status": "FAIL", "check": "VERIFICATION_INTERNAL_ERROR"}, sort_keys=True))
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = original
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
