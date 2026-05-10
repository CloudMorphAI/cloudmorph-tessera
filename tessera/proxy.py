"""Tessera MCP proxy — FastAPI application."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections import defaultdict
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tessera.audit.chain import HashChain
from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.base import AuditSink
from tessera.audit.sinks.sqlite import SqliteSink
from tessera.auth.bearer import BearerTokenAuthenticator
from tessera.config import PoliciesMode, TesseraConfig, load_config
from tessera.errors import PolicyError, UnauthorizedError
from tessera.intent import extract_intent
from tessera.policy.engine import PolicyEngine
from tessera.policy.loader import FilesystemPolicyLoader
from tessera.policy.schema import Action

logger = logging.getLogger(__name__)

# ── Metrics counters ─────────────────────────────────────────────────────────
# Simple in-memory counters (no prometheus_client dependency).

_METRICS: dict[str, int] = defaultdict(int)

_PASS_THROUGH_METHODS = {
    "tools/list",
    "prompts/list",
    "resources/list",
    "initialize",
    "ping",
}


# ── JSON-RPC helpers ─────────────────────────────────────────────────────────


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    reason: str | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if reason is not None:
        error["data"] = {"reason": reason}
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": error})


def _inject_audit_id(body: dict[str, Any], event_id: str) -> dict[str, Any]:
    """Inject tessera_audit_event_id into JSON-RPC response body."""
    result = body.get("result")
    if isinstance(result, dict):
        meta = result.setdefault("_meta", {})
        meta["tessera_audit_event_id"] = event_id
    else:
        body.setdefault("_meta", {})["tessera_audit_event_id"] = event_id
    return body


# ── App factory ──────────────────────────────────────────────────────────────


def create_app(config: TesseraConfig | None = None) -> FastAPI:
    """Create and configure the Tessera FastAPI app."""
    app = FastAPI(title="Tessera MCP Proxy", version="0.1.0")

    # ── Startup ──────────────────────────────────────────────────────────────

    @app.on_event("startup")
    async def _startup() -> None:
        cfg = config if config is not None else load_config()
        app.state.config = cfg

        # Auth
        app.state.authenticator = BearerTokenAuthenticator(
            deployment_id=cfg.deployment_id,
        )

        # Audit sink
        audit_path = cfg.audit.path
        sink = SqliteSink(path=audit_path)
        app.state.sink = sink

        # Hash chain (shared across scopes)
        app.state.hash_chain = HashChain()

        # Emitter map: scope → AuditEmitter (created on demand)
        app.state.emitter_map = {}

        # Policy loader
        loader = FilesystemPolicyLoader(
            cfg.policies.dir,
            reload_mode=cfg.policies.reload,
        )
        try:
            policies = loader.load_all("default")
        except Exception as exc:
            logger.error("event=startup_policy_load_failed error=%s", exc)
            raise

        default_action = Action(cfg.policies.default_action)
        engine = PolicyEngine(policies, default_action=default_action)
        app.state.loader = loader
        app.state.engine = engine

        # Watch for policy changes if configured
        if cfg.policies.reload == "watch":

            def _on_reload(updated_policies: list[Any]) -> None:
                new_engine = PolicyEngine(updated_policies, default_action=default_action)
                app.state.engine = new_engine
                logger.info("event=policy_reloaded count=%d", len(updated_policies))

            loader.watch("default", _on_reload)

        # HTTP clients per upstream
        app.state.http_clients = {}
        for upstream in cfg.upstreams:
            headers: dict[str, str] = {}
            if upstream.credentials:
                headers[upstream.credentials.header] = upstream.credentials.value
            app.state.http_clients[upstream.name] = httpx.AsyncClient(
                base_url=upstream.url,
                headers=headers,
                timeout=upstream.timeout_seconds,
            )

        # Emit startup audit event (use deployment scope)
        _get_or_create_emitter(app.state, cfg.deployment_id)
        _emit(
            app.state,
            cfg.deployment_id,
            "startup",
            {"deployment_id": cfg.deployment_id, "mode": cfg.policies.mode.value},
        )

        logger.info(
            "event=startup mode=%s policies_loaded=%d",
            cfg.policies.mode.value,
            len(policies),
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        # Close HTTP clients
        for client in getattr(app.state, "http_clients", {}).values():
            await client.aclose()
        # Close audit sink
        sink = getattr(app.state, "sink", None)
        if sink is not None:
            sink.close()
        # Stop policy watcher
        loader = getattr(app.state, "loader", None)
        if loader is not None:
            loader.stop()

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        loader = getattr(app.state, "loader", None)
        policy_state = loader.state() if loader is not None else {"loaded": 0, "errored": []}
        return JSONResponse({"status": "ok", "policy_state": policy_state})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        cfg = getattr(app.state, "config", None)
        if cfg is None:
            return JSONResponse({"status": "not_ready", "reason": "not_initialized"}, status_code=503)

        loader = getattr(app.state, "loader", None)
        if loader is None or loader.state()["loaded"] == 0:
            return JSONResponse({"status": "not_ready", "reason": "no_policies_loaded"}, status_code=503)

        # Quick reachability check on at least one upstream
        http_clients: dict[str, httpx.AsyncClient] = getattr(app.state, "http_clients", {})
        if not http_clients:
            # No upstreams configured — still ready (valid for tests)
            return JSONResponse({"status": "ok"})

        for _upstream_name, client in http_clients.items():
            try:
                await asyncio.wait_for(
                    client.get("/healthz"),
                    timeout=2.0,
                )
                return JSONResponse({"status": "ok"})
            except Exception:  # noqa: BLE001
                continue

        return JSONResponse({"status": "not_ready", "reason": "no_upstream_reachable"}, status_code=503)

    @app.post("/mcp/{upstream_name}")
    async def proxy(upstream_name: str, request: Request) -> Response:
        cfg: TesseraConfig = app.state.config
        request_id = str(uuid.uuid4())

        # Step 1 — Authenticate
        try:
            auth_ctx = app.state.authenticator.authenticate(request)
        except UnauthorizedError as exc:
            _METRICS["requests_total{outcome=unauthorized}"] += 1
            return JSONResponse({"error": str(exc)}, status_code=401)

        # Step 2 — Parse JSON-RPC body
        try:
            body = await request.json()
        except Exception:
            _METRICS["requests_total{outcome=parse_error}"] += 1
            return _jsonrpc_error(None, -32700, "Parse error")

        jsonrpc_id = body.get("id", 1)
        method: str = body.get("method", "")

        # Step 3 — Branch on method
        if method.startswith("notifications/") or method in _PASS_THROUGH_METHODS:
            return await _handle_pass_through(
                app.state, cfg, auth_ctx, upstream_name, body, jsonrpc_id, request_id
            )

        if method != "tools/call":
            _METRICS["requests_total{outcome=unknown_method}"] += 1
            return _jsonrpc_error(jsonrpc_id, -32601, "Method not found")

        # Step 4 — Extract params
        params = body.get("params", {})
        tool_name: str = params.get("name", "")
        arguments: dict[str, Any] = params.get("arguments", {}) or {}
        meta: dict[str, Any] | None = params.get("_meta")

        # Step 5 — Extract intent
        intent: dict[str, Any] | None = None
        try:
            intent = extract_intent(
                meta,
                meta_key=cfg.intent.meta_key,
                intent_required=cfg.intent.required,
            )
        except PolicyError:
            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": cfg.policies.mode.value,
                    "decision": "block",
                    "policy_id": None,
                    "reason": "intent_required",
                    "upstream": upstream_name,
                    "tool_call": {"name": tool_name, "arguments": arguments, "_meta": meta},
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": None,
                },
            )
            resp_body: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {"reason": "intent_required"},
                },
            }
            _inject_audit_id(resp_body, audit_event["eventId"])
            return JSONResponse(resp_body)

        # Step 6 — Build context
        context: dict[str, Any] = {
            "tool_call": {"name": tool_name, "arguments": arguments, "_meta": meta},
            "intent": intent,
            "upstream": upstream_name,
            "runtime": {"lockdown": cfg.runtime.lockdown},
            "mode": cfg.policies.mode.value,
            "policy_id": None,
        }

        # Step 7 — Lockdown check (before mode branch)
        if cfg.runtime.lockdown:
            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": cfg.policies.mode.value,
                    "decision": "block",
                    "policy_id": None,
                    "reason": "lockdown_active",
                    "upstream": upstream_name,
                    "tool_call": context["tool_call"],
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": None,
                },
            )
            _METRICS["requests_total{outcome=lockdown}"] += 1
            resp_body = {
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {"reason": "lockdown_active"},
                },
            }
            _inject_audit_id(resp_body, audit_event["eventId"])
            return JSONResponse(resp_body)

        # Step 8 — Mode branch
        mode = cfg.policies.mode
        engine: PolicyEngine = app.state.engine

        if mode == PoliciesMode.observation:
            # Skip engine — always forward
            upstream_response = await _forward_upstream(app.state, upstream_name, body, jsonrpc_id)
            if isinstance(upstream_response, JSONResponse):
                return upstream_response

            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": "observation",
                    "policy_id": None,
                    "reason": None,
                    "upstream": upstream_name,
                    "tool_call": context["tool_call"],
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": None,
                },
            )
            _inject_audit_id(upstream_response, audit_event["eventId"])
            _METRICS["requests_total{outcome=observation}"] += 1
            return JSONResponse(upstream_response)

        # Evaluate engine (enforcement + log_only)
        decision = engine.evaluate(context)
        _METRICS[f"decisions_total{{action={decision.action.value},mode={mode.value}}}"] += 1

        if mode == PoliciesMode.log_only:
            # Always forward upstream (even on upstream error — return with headers)
            upstream_response = await _forward_upstream(app.state, upstream_name, body, jsonrpc_id)

            # Map decision to would_* header value
            if decision.policy_id is None:
                tessera_decision_header = "no_match"
            elif decision.action == Action.block:
                tessera_decision_header = "would_block"
            else:
                tessera_decision_header = "would_allow"

            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": "log_only",
                    "would_decision": decision.action.value,
                    "policy_id": decision.policy_id,
                    "reason": decision.reason,
                    "upstream": upstream_name,
                    "tool_call": context["tool_call"],
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": decision.decision_error,
                },
            )
            _METRICS["requests_total{outcome=log_only_forwarded}"] += 1

            log_only_headers: dict[str, str] = {
                "X-Tessera-Mode": "log_only",
                "X-Tessera-Decision": tessera_decision_header,
            }
            if tessera_decision_header == "would_block":
                if decision.policy_id:
                    log_only_headers["X-Tessera-Policy-Id"] = decision.policy_id
                if decision.reason:
                    log_only_headers["X-Tessera-Reason"] = decision.reason

            if isinstance(upstream_response, JSONResponse):
                # Upstream failed — return the error response with X-Tessera headers
                for k, v in log_only_headers.items():
                    upstream_response.headers[k] = v
                return upstream_response

            _inject_audit_id(upstream_response, audit_event["eventId"])
            return JSONResponse(upstream_response, headers=log_only_headers)

        # enforcement mode
        if decision.action in (Action.allow, Action.log_only):
            upstream_response = await _forward_upstream(app.state, upstream_name, body, jsonrpc_id)
            if isinstance(upstream_response, JSONResponse):
                return upstream_response

            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": "enforcement",
                    "decision": decision.action.value,
                    "policy_id": decision.policy_id,
                    "reason": decision.reason,
                    "upstream": upstream_name,
                    "tool_call": context["tool_call"],
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": decision.decision_error,
                },
            )
            _inject_audit_id(upstream_response, audit_event["eventId"])
            _METRICS["requests_total{outcome=allow}"] += 1
            return JSONResponse(upstream_response)

        if decision.action == Action.block:
            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": "enforcement",
                    "decision": "block",
                    "policy_id": decision.policy_id,
                    "reason": decision.reason,
                    "upstream": upstream_name,
                    "tool_call": context["tool_call"],
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": decision.decision_error,
                },
            )
            _METRICS["requests_total{outcome=block}"] += 1
            resp_body = {
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {"reason": decision.reason or "blocked"},
                },
            }
            _inject_audit_id(resp_body, audit_event["eventId"])
            return JSONResponse(resp_body)

        if decision.action == Action.require_approval:
            reason_str = f"approval_required: {decision.reason or ''}"
            audit_event = _emit(
                app.state,
                auth_ctx.scope,
                "decision",
                {
                    "mode": "enforcement",
                    "decision": "require_approval",
                    "policy_id": decision.policy_id,
                    "reason": reason_str,
                    "upstream": upstream_name,
                    "tool_call": context["tool_call"],
                    "principal_id": auth_ctx.principal_id,
                    "request_id": request_id,
                    "decision_error": decision.decision_error,
                },
            )
            _METRICS["requests_total{outcome=require_approval}"] += 1
            resp_body = {
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "error": {
                    "code": -32604,
                    "message": "Approval required",
                    "data": {"reason": reason_str},
                },
            }
            _inject_audit_id(resp_body, audit_event["eventId"])
            return JSONResponse(resp_body)

        # Fallback — should not happen
        return _jsonrpc_error(jsonrpc_id, -32603, "Internal error", reason="unknown_decision")

    # ── Metrics endpoint ─────────────────────────────────────────────────────

    cfg_for_metrics = config  # captured in closure

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> Response:
        # Resolve current config (may differ if app.state populated on startup)
        current_cfg: TesseraConfig | None = getattr(app.state, "config", cfg_for_metrics)
        if current_cfg is None or not current_cfg.metrics.enabled:
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Auth check
        auth_header: str | None = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        incoming_token = parts[1]

        # Check dedicated metrics token first
        import secrets as _secrets

        metrics_token = os.environ.get(current_cfg.metrics.bearer_token_env)
        if metrics_token:
            if _secrets.compare_digest(metrics_token, incoming_token):
                return _build_metrics_response()
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        # Fall back to main token list
        authenticator: BearerTokenAuthenticator | None = getattr(app.state, "authenticator", None)
        if authenticator is not None:
            for candidate in authenticator._tokens:
                if _secrets.compare_digest(candidate.token, incoming_token):
                    return _build_metrics_response()

        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return app


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_or_create_emitter(state: Any, scope: str) -> AuditEmitter:
    """Lazily create an AuditEmitter for a scope, restoring hash chain head."""
    emitter_map: dict[str, AuditEmitter] = state.emitter_map
    if scope in emitter_map:
        return emitter_map[scope]

    sink: SqliteSink = state.sink
    hash_chain: HashChain = state.hash_chain

    # Restore head from sink if available
    head = sink.head_hash(scope)
    if head:
        try:
            hash_chain.restore_head(scope, head)
        except ValueError:
            logger.warning("event=bad_chain_head scope=%s head=%r", scope, head)

    sinks: list[AuditSink] = [sink]
    cfg: TesseraConfig = state.config
    if cfg.audit.also_stdout:
        from tessera.audit.sinks.stdout import StdoutSink

        sinks.append(StdoutSink())

    emitter = AuditEmitter(
        tenant_id=scope,
        sinks=sinks,
        hash_chain=hash_chain,
    )
    emitter_map[scope] = emitter
    return emitter


def _emit(state: Any, scope: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Emit an audit event for the given scope. Swallows sink errors."""
    emitter = _get_or_create_emitter(state, scope)
    try:
        return emitter.emit(event_type, payload=payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("event=audit_emit_failed scope=%s error=%s", scope, exc)
        _METRICS["audit_emit_failures_total"] += 1
        # Return a stub so callers can still inject an event id
        return {"eventId": f"evt_failed_{uuid.uuid4().hex[:10]}"}


async def _forward_upstream(
    state: Any,
    upstream_name: str,
    body: dict[str, Any],
    jsonrpc_id: Any,
) -> dict[str, Any] | JSONResponse:
    """Forward a request to the named upstream. Returns parsed response dict or JSONResponse on error."""
    http_clients: dict[str, httpx.AsyncClient] = state.http_clients
    client = http_clients.get(upstream_name)
    if client is None:
        _METRICS["requests_total{outcome=unknown_upstream}"] += 1
        return _jsonrpc_error(
            jsonrpc_id, -32001, "Upstream error", reason=f"unknown upstream: {upstream_name!r}"
        )

    try:
        response = await client.post("/", json=body)
        if response.status_code >= 500:
            _METRICS["requests_total{outcome=upstream_5xx}"] += 1
            return _jsonrpc_error(jsonrpc_id, -32001, "Upstream error")
        return cast("dict[str, Any]", response.json())
    except httpx.TimeoutException:
        _METRICS["requests_total{outcome=upstream_timeout}"] += 1
        return _jsonrpc_error(jsonrpc_id, -32000, "Upstream timeout")
    except Exception as exc:  # noqa: BLE001
        logger.error("event=upstream_error upstream=%s error=%s", upstream_name, exc)
        _METRICS["requests_total{outcome=upstream_error}"] += 1
        return _jsonrpc_error(jsonrpc_id, -32001, "Upstream error")


async def _handle_pass_through(
    state: Any,
    cfg: TesseraConfig,
    auth_ctx: Any,
    upstream_name: str,
    body: dict[str, Any],
    jsonrpc_id: Any,
    request_id: str,
) -> Response:
    """Handle pass-through methods (tools/list, initialize, etc.)."""
    upstream_response = await _forward_upstream(state, upstream_name, body, jsonrpc_id)
    if isinstance(upstream_response, JSONResponse):
        return upstream_response

    audit_event = _emit(
        state,
        auth_ctx.scope,
        "passthrough",
        {
            "upstream": upstream_name,
            "method": body.get("method", ""),
            "principal_id": auth_ctx.principal_id,
            "request_id": request_id,
        },
    )
    _inject_audit_id(upstream_response, audit_event["eventId"])
    _METRICS["requests_total{outcome=passthrough}"] += 1
    return JSONResponse(upstream_response)


def _build_metrics_response() -> Response:
    """Build a simple Prometheus-formatted text response."""
    lines = [
        "# HELP tessera_requests_total Total requests processed",
        "# TYPE tessera_requests_total counter",
    ]
    for key, value in sorted(_METRICS.items()):
        if key.startswith("requests_total"):
            lines.append(f"tessera_{key} {value}")

    lines += [
        "# HELP tessera_decisions_total Total policy decisions",
        "# TYPE tessera_decisions_total counter",
    ]
    for key, value in sorted(_METRICS.items()):
        if key.startswith("decisions_total"):
            lines.append(f"tessera_{key} {value}")

    lines += [
        "# HELP tessera_audit_emit_failures_total Audit emit failures",
        "# TYPE tessera_audit_emit_failures_total counter",
        f"tessera_audit_emit_failures_total {_METRICS.get('audit_emit_failures_total', 0)}",
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
