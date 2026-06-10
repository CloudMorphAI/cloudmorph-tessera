"""Tessera MCP proxy — FastAPI application."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from tessera.cost.types import CostResult
    from tessera.integrations.aws.upstream import AWSMcpUpstream
    from tessera.integrations.streamable_http.upstream import StreamableHttpUpstream

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tessera import pluggable
from tessera.audit.async_emit import AsyncAuditQueue, sync_mode_enabled
from tessera.audit.chain import HashChain
from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.base import AuditSink
from tessera.audit.sinks.sqlite import SqliteSink
from tessera.auth.bearer import BearerTokenAuthenticator
from tessera.config import PoliciesMode, TesseraConfig, load_config
from tessera.errors import PolicyError, UnauthorizedError
from tessera.intent import extract_intent
from tessera.observability import events as _obs_events
from tessera.observability import metrics as _obs_metrics
from tessera.observability import tracing as _obs_tracing
from tessera.policy import action_verbs as _action_verbs_module
from tessera.policy.action_verbs import load_user_mappings, verbs_for
from tessera.policy.engine import PolicyEngine
from tessera.policy.schema import Action

logger = logging.getLogger(__name__)

# Pricing snapshot version — refreshed once per minute by a background task.
# None when no Infracost backend is configured.
_pricing_snapshot_id: str | None = None


# ── Intent endpoint models ────────────────────────────────────────────────────


class IntentRequest(BaseModel):
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    command: str = ""
    conversation_id: str = ""
    generation_id: str = ""
    workspace_roots: list[str] = Field(default_factory=list)


class IntentMeta(BaseModel):
    verbs: list[str]
    purpose: str


class IntentResponseMeta(BaseModel):
    tessera_intent: IntentMeta


# ── Metrics counters ─────────────────────────────────────────────────────────
# Simple in-memory counters (no prometheus_client dependency).

_METRICS: dict[str, int] = defaultdict(int)


# ── v0.7.0 Item D §7.4 — DecisionCache (LRU + TTL memoization) ──────────────


class DecisionCache:
    """Memoize policy decisions for identical recent calls.

    Caches ``allow`` and ``observed`` (i.e. non-block) decisions only — block
    decisions are always re-evaluated so a policy change that newly blocks a
    previously-allowed call surfaces on the very next request. Cache key is
    ``sha256(canonical_json({scope, tool, args}))`` so two calls that differ
    only in arg ordering still hit the same entry.

    Bounded: max 1024 entries, 60s TTL each. LRU eviction on insert when full.
    Cleared on every policy reload via :func:`create_app` reload-watcher.
    """

    MAX_SIZE = 1024
    TTL_SECONDS = 60
    _NON_CACHEABLE_ACTIONS = {"block", "require_approval"}

    def __init__(self) -> None:
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(scope: str, tool_name: str, tool_call: Any) -> str:
        try:
            payload = json.dumps(
                {"scope": scope, "tool": tool_name, "args": tool_call},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            # Defensive: if tool_call has unserializable fragments, salt with
            # repr() so we don't poison the cache or crash the hot path.
            payload = repr(("salt", scope, tool_name, str(tool_call)))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, scope: str, tool_name: str, tool_call: Any) -> Any | None:
        key = self._key(scope, tool_name, tool_call)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            decision, expires_at = entry
            if time.monotonic() >= expires_at:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return decision

    def put(self, scope: str, tool_name: str, tool_call: Any, decision: Any) -> None:
        # Skip blocks + require_approval; only memoize allow + observed.
        action_value = getattr(getattr(decision, "action", None), "value", "")
        if action_value in self._NON_CACHEABLE_ACTIONS:
            return
        key = self._key(scope, tool_name, tool_call)
        with self._lock:
            self._cache[key] = (decision, time.monotonic() + self.TTL_SECONDS)
            self._cache.move_to_end(key)
            while len(self._cache) > self.MAX_SIZE:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

# MCP-AUDIT-2026-05-11: The following methods are passed-through without policy
# evaluation, even though they are MCP "action" category and *could* leak data:
#   - prompts/get          (prompt arguments may contain PII/secrets)
#   - resources/read       (could exfiltrate via resource URI)
#   - resources/subscribe  (subscription is a write-ish action)
#   - completion/complete  (arguments may contain PII/secrets)
#   - sampling/createMessage (arguments may contain PII/secrets)
# Audit-only handling (v0.2.0, OQ-1): these 5 methods emit a separate
# passthrough_data_leak_candidate audit event in addition to the normal
# passthrough event. The audit flag is configurable via
# audit.flag_data_leak_passthrough (default True). Policy evaluation for these
# methods is deferred to a future release once real-world traffic patterns are
# understood.
_PASS_THROUGH_METHODS = {
    # Lifecycle
    "initialize",
    "ping",
    # Discovery — metadata only, no policy risk
    "tools/list",
    "prompts/list",
    "resources/list",
    "roots/list",
    # Config / admin — no data exfil risk
    "logging/setLevel",
    # Resource actions — pass-through for v0.1.1 (see MCP-AUDIT above)
    "resources/unsubscribe",
    # Action-category methods passed through pending founder decision (see MCP-AUDIT above)
    "prompts/get",
    "resources/read",
    "resources/subscribe",
    "completion/complete",
    "sampling/createMessage",
}


# ── v0.8.0 — Unified MCP routing helpers ────────────────────────────────────

TOOL_NAMESPACE_SEPARATOR = "__"


def namespace_tool(upstream_name: str, tool_name: str) -> str:
    """Return the namespaced tool name: '<upstream>__<tool>'."""
    return f"{upstream_name}{TOOL_NAMESPACE_SEPARATOR}{tool_name}"


def parse_namespaced_tool(namespaced: str) -> tuple[str, str]:
    """Return (upstream_name, canonical_tool_name). Raises ValueError if no separator found."""
    parts = namespaced.split(TOOL_NAMESPACE_SEPARATOR, 1)
    if len(parts) != 2:
        raise ValueError(f"tool name {namespaced!r} missing upstream namespace (expected '<upstream>__<tool>')")
    return parts[0], parts[1]


async def aggregate_tools_list(state: Any) -> dict[str, Any]:
    """Fan out tools/list to every configured upstream and return a merged catalog.

    Tool names are namespaced as '<upstream>__<tool>' so the agent sees a single
    flat catalog with no collisions between upstreams.

    D4 validation: if unified mode has been disabled at startup (state.unified_mode_disabled
    is True), returns a JSON-RPC error directing callers to use per-upstream routes.

    Per-upstream errors are logged and skipped — a partial catalog is returned rather
    than failing the entire aggregation.
    """
    if getattr(state, "unified_mode_disabled", False):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32603,
                "message": "Unified mode disabled: one or more upstream tools contain '__' in their name. "
                           "Use per-upstream routes POST /mcp/<upstream_name> instead.",
            },
        }

    cfg: TesseraConfig = state.config
    all_tools: list[dict[str, Any]] = []

    async def _fetch_one(upstream_name: str) -> list[dict[str, Any]]:
        req_body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }
        response = await _forward_upstream(state, upstream_name, req_body, None)
        if isinstance(response, JSONResponse):
            logger.warning("event=aggregate_tools_list_upstream_error upstream=%s", upstream_name)
            return []
        tools: list[dict[str, Any]] = response.get("result", {}).get("tools", [])
        namespaced: list[dict[str, Any]] = []
        for tool in tools:
            tool_name: str = tool.get("name", "")
            renamed = {**tool, "name": namespace_tool(upstream_name, tool_name)}
            namespaced.append(renamed)
        return namespaced

    results = await asyncio.gather(
        *[_fetch_one(u.name) for u in cfg.upstreams],
        return_exceptions=True,
    )
    for res in results:
        if isinstance(res, list):
            all_tools.extend(res)
        else:
            logger.warning("event=aggregate_tools_list_gather_error err=%s", res)

    return {"jsonrpc": "2.0", "id": None, "result": {"tools": all_tools}}


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
    """Inject tessera_audit_event_id into JSON-RPC response body.

    Placement is JSON-RPC 2.0 + MCP spec compliant:
    - On `result` responses: nest under `result._meta`.
    - On `error` responses: nest under `error.data._meta`.
    Top-level `_meta` next to `error` is NOT valid JSON-RPC and is rejected
    by strict MCP clients (Claude Code's Zod validator, MCP SDK).
    """
    result = body.get("result")
    error = body.get("error")
    if isinstance(result, dict):
        meta = result.setdefault("_meta", {})
        meta["tessera_audit_event_id"] = event_id
    elif isinstance(error, dict):
        data = error.setdefault("data", {})
        if isinstance(data, dict):
            meta = data.setdefault("_meta", {})
            meta["tessera_audit_event_id"] = event_id
        # If error.data is set to a non-dict, skip rather than overwrite it.
    # If body has neither result nor error (shouldn't happen for valid JSON-RPC),
    # we drop the audit_id rather than break the response shape.
    return body


# ── App factory ──────────────────────────────────────────────────────────────


def create_app(config: TesseraConfig | None = None) -> FastAPI:
    """Create and configure the Tessera FastAPI app."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        # ── Startup ──────────────────────────────────────────────────────────
        cfg = config if config is not None else load_config()
        app.state.config = cfg

        # ── Pluggable backend resolution ─────────────────────────────────────
        # Env vars override the default classes.  Format: "module.path:ClassName".
        auth_spec = os.environ.get("TESSERA_AUTHENTICATOR")
        sink_spec = os.environ.get("TESSERA_AUDIT_SINK")
        loader_spec = os.environ.get("TESSERA_POLICY_LOADER")

        sink_cls = pluggable.resolve(
            sink_spec or "", "tessera.audit.sinks.sqlite:SqliteSink"
        )
        loader_cls = pluggable.resolve(
            loader_spec or "", "tessera.policy.loader:FilesystemPolicyLoader"
        )

        # Auth — dispatch on cfg.auth.type when no TESSERA_AUTHENTICATOR override is set
        if auth_spec:
            authenticator_cls = pluggable.resolve(auth_spec, "tessera.auth.bearer:BearerTokenAuthenticator")
            app.state.authenticator = authenticator_cls(
                deployment_id=cfg.deployment_id,
            )
        elif cfg.auth.type == "jwt":
            if not cfg.auth.jwt:
                from tessera.errors import ConfigError
                raise ConfigError("auth.type=jwt requires auth.jwt sub-block")
            from tessera.auth.jwt_mcp import JWTAuthenticator
            _jwt_auth = JWTAuthenticator(
                jwks_url=cfg.auth.jwt.jwks_url,
                issuer=cfg.auth.jwt.issuer,
                audience=cfg.auth.jwt.audience,
                clock_skew_seconds=cfg.auth.jwt.clock_skew_seconds,
                principal_claim=cfg.auth.jwt.principal_claim,
                scope_claim=cfg.auth.jwt.scope_claim,
                deployment_id=cfg.deployment_id,
            )
            await _jwt_auth.prewarm()
            app.state.authenticator = _jwt_auth
        else:
            authenticator_cls = pluggable.resolve(
                "", "tessera.auth.bearer:BearerTokenAuthenticator"
            )
            app.state.authenticator = authenticator_cls(
                deployment_id=cfg.deployment_id,
            )

        # Management-plane authenticator (reserved for future /app/* routes)
        if cfg.auth.management_plane:
            from tessera.auth.oidc import OIDCAuthenticator
            mp = cfg.auth.management_plane
            app.state.management_plane_authenticator = OIDCAuthenticator(
                jwks_url=mp.jwks_url,
                issuer=mp.issuer,
                audience=mp.audience,
                clock_skew_seconds=mp.clock_skew_seconds,
                scope_claim=mp.scope_claim,
                provider=mp.provider,
                deployment_id=cfg.deployment_id,
            )
        else:
            app.state.management_plane_authenticator = None

        # Audit sink
        audit_path = cfg.audit.path
        sink = sink_cls(path=audit_path)
        app.state.sink = sink

        # Hash chain (shared across scopes)
        chain = HashChain()
        app.state.hash_chain = chain

        # Restore hash chain heads from persisted sink for all existing scopes.
        # This ensures the chain is continuous across process restarts.
        try:
            for scope in sink.iter_scopes():
                head = sink.head_hash(scope)
                if head:
                    try:
                        chain.restore_head(scope, head)
                    except ValueError:
                        logger.warning("event=bad_chain_head scope=%s head=%r", scope, head)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=chain_restore_failed error=%s", exc)

        # Emitter map: scope → AuditEmitter (created on demand)
        app.state.emitter_map = {}

        # Async audit emit queue (P0-13): drains stamp+sink off the hot path.
        # Started here so the request handlers can enqueue() safely.
        def _on_audit_dropped(_job: Any) -> None:
            _METRICS["audit_emit_dropped_total"] += 1

        def _on_audit_failure(_exc: BaseException) -> None:
            _METRICS["audit_emit_failures_total"] += 1

        audit_queue = AsyncAuditQueue(
            on_dropped=_on_audit_dropped,
            on_failure=_on_audit_failure,
        )
        await audit_queue.start()
        app.state.audit_queue = audit_queue

        # Policy loader
        loader = loader_cls(
            cfg.policies.dir,
            reload_mode=cfg.policies.reload,
        )

        # Load user-defined action verb mappings before policies are evaluated so
        # verbs_for() sees the merged table during evaluation (option b: update
        # module-level _user_mappings dict, leaving ACTION_VERBS builtins intact).
        _action_verbs_yaml = Path(cfg.policies.dir) / "_action_verbs.yaml"
        if _action_verbs_yaml.exists():
            try:
                user_mappings = load_user_mappings(_action_verbs_yaml)
                _action_verbs_module._user_mappings.update(user_mappings)
                logger.info(
                    "event=action_verbs_merged user_entries=%d", len(user_mappings)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=action_verbs_merge_failed path=%s error=%s", _action_verbs_yaml, exc)

        try:
            policies = loader.load_all("default")
        except Exception as exc:
            logger.error("event=startup_policy_load_failed error=%s", exc)
            # Best-effort cleanup of the sink we just opened, then re-raise.
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                pass
            raise

        default_action = Action(cfg.policies.default_action)
        engine = PolicyEngine(policies, default_action=default_action)
        app.state.loader = loader
        app.state.engine = engine
        # v0.7.0 Item D §7.4 — decision memoization. Empty on cold start; warms
        # as identical calls land. Cleared on every policy reload so a fresh
        # policy version doesn't continue serving stale allow decisions.
        app.state.decision_cache = DecisionCache()

        # Watch for policy changes if configured
        if cfg.policies.reload == "watch":

            def _on_reload(updated_policies: list[Any]) -> None:
                new_engine = PolicyEngine(updated_policies, default_action=default_action)
                app.state.engine = new_engine
                # Invalidate memoized decisions — the new engine may differ.
                cache: DecisionCache | None = getattr(app.state, "decision_cache", None)
                if cache is not None:
                    cache.clear()
                logger.info("event=policy_reloaded count=%d", len(updated_policies))

            loader.watch("default", _on_reload)

        # HTTP clients, AWS clients, and streamable-HTTP clients per upstream
        app.state.http_clients = {}
        app.state.aws_clients = {}
        app.state.streamable_http_clients = {}

        for upstream in cfg.upstreams:
            if upstream.kind == "aws_mcp":
                from tessera.integrations.aws.upstream import AWSMcpUpstream

                aws_client = AWSMcpUpstream(
                    name=upstream.name,
                    endpoint=upstream.url,
                    aws_region=upstream.aws_region or "us-east-1",
                    aws_service=upstream.aws_service,
                    aws_endpoint_override=upstream.aws_endpoint_override,
                    timeout_seconds=upstream.timeout_seconds,
                    aws_mcp_routing=upstream.aws_mcp_routing,
                    aws_mcp_server=upstream.aws_mcp_server,
                )
                await aws_client.__aenter__()
                app.state.aws_clients[upstream.name] = aws_client
            elif upstream.kind == "mcp_streamable_http":
                from tessera.integrations.streamable_http.upstream import (
                    StreamableHttpUpstream,  # noqa: PLC0415
                )

                sh_client = StreamableHttpUpstream(
                    name=upstream.name,
                    url=upstream.url,
                    auth_header=upstream.auth_header,
                    session_timeout_s=upstream.session_timeout_s,
                    request_timeout_s=upstream.request_timeout_s,
                )
                await sh_client.__aenter__()
                app.state.streamable_http_clients[upstream.name] = sh_client
            else:
                headers: dict[str, str] = {}
                if upstream.credentials:
                    headers[upstream.credentials.header] = upstream.credentials.value
                app.state.http_clients[upstream.name] = httpx.AsyncClient(
                    base_url=upstream.url,
                    headers=headers,
                    timeout=upstream.timeout_seconds,
                )

        # ── Optional: Infracost cost backend ─────────────────────────────────
        infracost_url = os.environ.get("TESSERA_INFRACOST_URL")
        if infracost_url:
            try:
                from tessera.cost import aws_mapping as _aws_mapping_module
                from tessera.cost.infracost import InfracostClient
                cost_client = InfracostClient(
                    backend_url=infracost_url,
                    api_key=os.environ.get("INFRACOST_API_KEY"),
                )
                app.state.cost_backend = cost_client
                app.state.aws_mapping = _aws_mapping_module
                logger.info("event=infracost_backend_initialized url=%s", infracost_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=infracost_backend_init_failed error=%s", exc)
                app.state.cost_backend = None
                app.state.aws_mapping = None
        else:
            app.state.cost_backend = None
            app.state.aws_mapping = None

        # ── Optional: blast-radius backend ───────────────────────────────────
        if os.environ.get("TESSERA_BLAST_RADIUS_ENABLED", "").lower() in ("1", "true", "yes"):
            try:
                from tessera.integrations.aws.blast_radius import BlastRadiusBackend
                app.state.blast_radius_backend = BlastRadiusBackend()
                logger.info("event=blast_radius_backend_initialized")
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=blast_radius_backend_init_failed error=%s", exc)
                app.state.blast_radius_backend = None
        else:
            app.state.blast_radius_backend = None

        # ── State backend (cumulative spend) ──────────────────────────────────
        try:
            from tessera.state.daily_spend import DailySpendState
            state_dir_env = os.environ.get("TESSERA_STATE_DIR")
            state_dir = Path(state_dir_env) if state_dir_env else None
            app.state.state_backend = DailySpendState(state_dir=state_dir)
            logger.info("event=state_backend_initialized")
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=state_backend_init_failed error=%s", exc)
            app.state.state_backend = None

        # ── Background task: refresh pricing snapshot id once per minute ─────
        _pricing_refresh_task: asyncio.Task[None] | None = None
        if app.state.cost_backend is not None:
            async def _refresh_pricing_snapshot() -> None:
                global _pricing_snapshot_id
                while True:
                    try:
                        ver = await app.state.cost_backend.data_version()
                        _pricing_snapshot_id = ver
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("event=pricing_snapshot_refresh_failed error=%s", exc)
                    await asyncio.sleep(60)

            _pricing_refresh_task = asyncio.create_task(_refresh_pricing_snapshot())

        # ── Optional: Intelligence client (pack downloads + license check) ────
        app.state.intelligence_client = None
        app.state.price_table = None
        if cfg.intelligence.enabled:
            try:
                # Load bundled public key for license verification
                import importlib.resources as _ilr

                from tessera.intelligence.client import IntelligenceClient
                from tessera.intelligence.license import LicenseValidator
                _pub_key_pem = (_ilr.files("tessera.intelligence") / "public_key.pem").read_bytes()

                _license_validator = LicenseValidator(
                    config=cfg.intelligence,
                    public_key_pem=_pub_key_pem,
                )
                intel_client = IntelligenceClient(
                    config=cfg.intelligence,
                    license_validator=_license_validator,
                )
                await intel_client.refresh()
                await intel_client.start_refresh_task()
                app.state.intelligence_client = intel_client
                app.state.price_table = intel_client.get_price_table("aws")
                if app.state.price_table is not None:
                    logger.info(
                        "event=price_table_loaded_from_intelligence ops=%d",
                        app.state.price_table.operation_count,
                    )
                logger.info("event=intelligence_client_initialized")
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=intelligence_client_init_failed error=%s", exc)

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

        # Initialise OTel tracer (no-op when TESSERA_OTEL_ENABLED is unset)
        _obs_tracing.init_tracer()

        try:
            yield
        finally:
            # ── Shutdown ─────────────────────────────────────────────────────
            # Cancel pricing refresh task
            if _pricing_refresh_task is not None:
                _pricing_refresh_task.cancel()
                try:
                    await _pricing_refresh_task
                except asyncio.CancelledError:
                    pass
            # Close HTTP clients
            for client in getattr(app.state, "http_clients", {}).values():
                await client.aclose()
            # Close AWS clients
            for aws_client in getattr(app.state, "aws_clients", {}).values():
                await aws_client.__aexit__(None, None, None)
            # Close streamable-HTTP clients
            for sh_client in getattr(app.state, "streamable_http_clients", {}).values():
                await sh_client.__aexit__(None, None, None)
            # Close Infracost client
            cost_backend = getattr(app.state, "cost_backend", None)
            if cost_backend is not None:
                await cost_backend.aclose()
            # Close state backend
            state_bk = getattr(app.state, "state_backend", None)
            if state_bk is not None:
                state_bk.close()
            # Drain the async audit queue (P0-13) before closing the sink so any
            # pending stamp+persist completes against a live SQLite handle.
            audit_q = getattr(app.state, "audit_queue", None)
            if audit_q is not None:
                try:
                    await audit_q.drain()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("event=audit_queue_drain_failed error=%s", exc)
            # Close audit sink
            sink = getattr(app.state, "sink", None)
            if sink is not None:
                sink.close()
            # Stop policy watcher
            loader = getattr(app.state, "loader", None)
            if loader is not None:
                loader.stop()

    from tessera import __version__ as _tessera_version

    app = FastAPI(title="Tessera MCP Proxy", version=_tessera_version, lifespan=_lifespan)

    # ── OAuth 2.1 Resource Server routes ─────────────────────────────────────
    from tessera.auth.oauth_rs import make_metadata_route
    make_metadata_route(app)

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

    @app.post("/intent")
    async def intent(request: Request) -> JSONResponse:
        # Step 1 — Authenticate (same pattern as /mcp)
        try:
            auth_ctx = app.state.authenticator.authenticate(request)
        except UnauthorizedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)

        # Step 2 — Parse body
        try:
            body = await request.json()
            req = IntentRequest.model_validate(body)
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=422)

        # Step 3 — Derive verbs deterministically from action_verbs registry
        verbs = sorted(verbs_for(req.tool_name))  # sorted for determinism

        # Step 4 — Derive purpose deterministically (no LLM)
        purpose_parts = []
        if req.command:
            purpose_parts.append(req.command)
        if req.tool_input:
            for k, v in sorted(req.tool_input.items()):
                if isinstance(v, str) and v:
                    purpose_parts.append(f"{k}={v}")
        purpose = "; ".join(purpose_parts) if purpose_parts else f"tool:{req.tool_name}"

        # Step 5 — Audit event
        _emit(
            app.state,
            auth_ctx.scope,
            "intent_derivation",
            {
                "tool_name": req.tool_name,
                "verbs": verbs,
                "purpose": purpose,
                "principal_id": auth_ctx.principal_id,
            },
        )

        # Step 6 — Return response
        response_body: dict[str, Any] = {
            "_meta": {
                "tessera_intent": {
                    "verbs": verbs,
                    "purpose": purpose,
                }
            }
        }
        return JSONResponse(response_body)

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
        # v0.5.0: resources/read and sampling/createMessage can be promoted to
        # engine-evaluated when policies.engine_eval_data_methods is True.
        _engine_eval_data = cfg.policies.engine_eval_data_methods
        _data_eval_methods = {"resources/read", "sampling/createMessage"}
        if method.startswith("notifications/") or (
            method in _PASS_THROUGH_METHODS
            and not (_engine_eval_data and method in _data_eval_methods)
        ):
            return await _handle_pass_through(
                app.state, cfg, auth_ctx, upstream_name, body, jsonrpc_id, request_id
            )

        if method not in ("tools/call", "resources/read", "sampling/createMessage"):
            _METRICS["requests_total{outcome=unknown_method}"] += 1
            return _jsonrpc_error(jsonrpc_id, -32601, "Method not found")

        # Step 4 — Extract params
        params = body.get("params", {})
        if method == "tools/call":
            tool_name: str = params.get("name", "")
            arguments: dict[str, Any] = params.get("arguments", {}) or {}
            meta: dict[str, Any] | None = params.get("_meta")
        else:
            # resources/read and sampling/createMessage promoted to engine-eval.
            # Synthesize a tool_call shape so conditions can inspect params uniformly.
            tool_name = method
            arguments = dict(params) if isinstance(params, dict) else {}
            meta = arguments.pop("_meta", None) if isinstance(arguments, dict) else None

        # Step 4b — Extract conversation_id from _meta for audit threading
        # Checks _meta.tessera_intent.conversation_id first, falls back to _meta.conversation_id.
        _conversation_id: str | None = None
        if isinstance(meta, dict):
            _ti = meta.get("tessera_intent")
            if isinstance(_ti, dict):
                _conversation_id = _ti.get("conversation_id") or None
            if _conversation_id is None:
                _conversation_id = meta.get("conversation_id") or None

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
                    "canonical_tool_name": tool_name,
                    "effective_tool_name": tool_name,
                    "conversation_id": _conversation_id,
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
        # Pre-fetch cost estimate for predicted_cost condition (async → sync bridge)
        cost_backend = getattr(app.state, "cost_backend", None)
        aws_mapping_mod = getattr(app.state, "aws_mapping", None)
        _t0_cost = perf_counter()
        cost_cache: dict[str, CostResult] = await _prefetch_cost(tool_name, arguments, cost_backend, aws_mapping_mod)
        _cost_result_for_latency = cost_cache.get(tool_name)
        _obs_metrics.tessera_cost_prefetch_latency_seconds.labels(
            upstream=upstream_name,
            cost_source=_cost_result_for_latency.source if _cost_result_for_latency is not None else "miss",
        ).observe(perf_counter() - _t0_cost)

        # Step 6b — Pre-fetch blast-radius when any matching policy needs it (P0-14).
        # Wraps the synchronous boto3 IAM/S3/KMS call in asyncio.to_thread to avoid
        # blocking the event loop during the round-trip. Result populates the
        # per-request cache that the condition evaluator consults first.
        blast_radius_cache: dict[str, int] = {}
        engine_for_prefetch: PolicyEngine | None = getattr(app.state, "engine", None)
        blast_radius_backend = getattr(app.state, "blast_radius_backend", None)
        if (
            blast_radius_backend is not None
            and engine_for_prefetch is not None
            and engine_for_prefetch.policies_need_blast_radius(tool_name, upstream_name)
        ):
            _t0_br = perf_counter()
            try:
                count = await asyncio.to_thread(
                    blast_radius_backend.compute, tool_name, arguments
                )
                blast_radius_cache[tool_name] = int(count)
            except Exception:  # noqa: BLE001
                # Leave cache empty so the evaluator takes its fail-closed branch.
                _METRICS["blast_radius_prefetch_failures_total"] += 1
            finally:
                _obs_metrics.tessera_blast_radius_prefetch_latency_seconds.labels(
                    upstream=upstream_name
                ).observe(perf_counter() - _t0_br)

        # Step 6c — Pre-fetch DataVolume sizes for matching policies (P0-15).
        data_vol_cache: dict[str, int] = {}
        needed_estimators: set[str] = (
            engine_for_prefetch.policies_need_data_volume(tool_name, upstream_name)
            if engine_for_prefetch is not None
            else set()
        )
        if "s3_get_byte_estimate" in needed_estimators:
            try:
                from tessera.policy.conditions import s3_head_size_sync as _s3_sync
                cache_key, size = await asyncio.to_thread(_s3_sync, arguments)
                if size is not None and cache_key:
                    data_vol_cache[cache_key] = size
            except Exception:  # noqa: BLE001
                _METRICS["data_volume_prefetch_failures_total"] += 1
        if "rds_query_result_estimate" in needed_estimators:
            try:
                from tessera.policy.conditions import rds_explain_size_sync as _rds_sync
                cache_key, size = await asyncio.to_thread(_rds_sync, arguments)
                if size is not None and cache_key:
                    data_vol_cache[cache_key] = size
            except Exception:  # noqa: BLE001
                _METRICS["data_volume_prefetch_failures_total"] += 1

        context: dict[str, Any] = {
            "tool_call": {"name": tool_name, "arguments": arguments, "_meta": meta},
            "intent": intent,
            "upstream": upstream_name,
            "runtime": {"lockdown": cfg.runtime.lockdown},
            "mode": cfg.policies.mode.value,
            "policy_id": None,
            "scope": auth_ctx.scope,
            "cost_backend": cost_backend,
            "cost_cache": cost_cache,
            "aws_mapping": aws_mapping_mod,
            "blast_radius_backend": blast_radius_backend,
            "blast_radius_cache": blast_radius_cache,
            "state_backend": getattr(app.state, "state_backend", None),
            "_data_vol_cache": data_vol_cache,
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
                    "canonical_tool_name": tool_name,
                    "effective_tool_name": context.get("_effective_tool_name", tool_name),
                    "conversation_id": _conversation_id,
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

            # P0-18 — write back daily spend in observation mode too, so caps
            # that operators configure later have meaningful history.
            _record_daily_spend(
                app.state,
                scope=auth_ctx.scope,
                tool_name=tool_name,
                cost_cache=cost_cache,
            )

            _obs_cost = cost_cache.get(tool_name)
            _obs_payload: dict[str, Any] = {
                "mode": "observation",
                "policy_id": None,
                "reason": None,
                "upstream": upstream_name,
                "tool_call": context["tool_call"],
                "principal_id": auth_ctx.principal_id,
                "request_id": request_id,
                "decision_error": None,
                "canonical_tool_name": tool_name,
                "effective_tool_name": context.get("_effective_tool_name", tool_name),
                "conversation_id": _conversation_id,
            }
            if _obs_cost is not None:
                _obs_payload["cost_source"] = _obs_cost.source
                _obs_payload["cost_band"] = _obs_cost.confidence_band
            audit_event = _emit(app.state, auth_ctx.scope, "decision", _obs_payload)
            _inject_audit_id(upstream_response, audit_event["eventId"])
            _METRICS["requests_total{outcome=observation}"] += 1
            return JSONResponse(upstream_response)

        # Evaluate engine (enforcement + log_only) — with v0.7.0 §7.4 memo cache
        _t0_eval = perf_counter()
        _decision_cache: DecisionCache | None = getattr(app.state, "decision_cache", None)
        _cache_key_args = context.get("tool_call") or {}
        decision = None
        if _decision_cache is not None:
            decision = _decision_cache.get(auth_ctx.scope, tool_name, _cache_key_args)
        if decision is None:
            decision = engine.evaluate(context)
            if _decision_cache is not None:
                _decision_cache.put(auth_ctx.scope, tool_name, _cache_key_args, decision)
            _METRICS["decision_cache_misses_total"] += 1
        else:
            _METRICS["decision_cache_hits_total"] += 1
        _obs_metrics.tessera_decision_latency_seconds.labels(
            upstream=upstream_name, mode=mode.value
        ).observe(perf_counter() - _t0_eval)
        _obs_metrics.tessera_decisions_total.labels(
            upstream=upstream_name, mode=mode.value, action=decision.action.value
        ).inc()
        _METRICS[f"decisions_total{{action={decision.action.value},mode={mode.value}}}"] += 1

        # Fire decision hooks fire-and-forget (never blocks hot path)
        asyncio.create_task(_obs_events.fire_on_decision(decision, context))

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

            _lo_cost = cost_cache.get(tool_name)
            _lo_payload: dict[str, Any] = {
                "mode": "log_only",
                "would_decision": decision.action.value,
                "policy_id": decision.policy_id,
                "reason": decision.reason,
                "upstream": upstream_name,
                "tool_call": context["tool_call"],
                "principal_id": auth_ctx.principal_id,
                "request_id": request_id,
                "decision_error": decision.decision_error,
                "canonical_tool_name": tool_name,
                "effective_tool_name": context.get("_effective_tool_name", tool_name),
                "conversation_id": _conversation_id,
            }
            if _lo_cost is not None:
                _lo_payload["cost_source"] = _lo_cost.source
                _lo_payload["cost_band"] = _lo_cost.confidence_band
            audit_event = _emit(app.state, auth_ctx.scope, "decision", _lo_payload)
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

            # P0-18 — daily-spend write-back so cumulative_spend_today caps enforce.
            # Uses the pre-fetched cost estimate (cost_cache) since we don't have
            # an actual post-call cost from most upstreams. Offloaded to a thread
            # so the SQLite WAL fsync never blocks the event loop. Failures bump
            # a counter but never affect the customer's response.
            _record_daily_spend(
                app.state,
                scope=auth_ctx.scope,
                tool_name=tool_name,
                cost_cache=cost_cache,
            )

            _enf_allow_cost = cost_cache.get(tool_name)
            _enf_allow_payload: dict[str, Any] = {
                "mode": "enforcement",
                "decision": decision.action.value,
                "policy_id": decision.policy_id,
                "reason": decision.reason,
                "upstream": upstream_name,
                "tool_call": context["tool_call"],
                "principal_id": auth_ctx.principal_id,
                "request_id": request_id,
                "decision_error": decision.decision_error,
                "canonical_tool_name": tool_name,
                "effective_tool_name": context.get("_effective_tool_name", tool_name),
                "conversation_id": _conversation_id,
            }
            if _enf_allow_cost is not None:
                _enf_allow_payload["cost_source"] = _enf_allow_cost.source
                _enf_allow_payload["cost_band"] = _enf_allow_cost.confidence_band
            audit_event = _emit(app.state, auth_ctx.scope, "decision", _enf_allow_payload)
            _inject_audit_id(upstream_response, audit_event["eventId"])
            _METRICS["requests_total{outcome=allow}"] += 1
            return JSONResponse(upstream_response)

        if decision.action == Action.block:
            _enf_block_cost = cost_cache.get(tool_name)
            _enf_block_payload: dict[str, Any] = {
                "mode": "enforcement",
                "decision": "block",
                "policy_id": decision.policy_id,
                "reason": decision.reason,
                "upstream": upstream_name,
                "tool_call": context["tool_call"],
                "principal_id": auth_ctx.principal_id,
                "request_id": request_id,
                "decision_error": decision.decision_error,
                "canonical_tool_name": tool_name,
                "effective_tool_name": context.get("_effective_tool_name", tool_name),
                "conversation_id": _conversation_id,
            }
            if _enf_block_cost is not None:
                _enf_block_payload["cost_source"] = _enf_block_cost.source
                _enf_block_payload["cost_band"] = _enf_block_cost.confidence_band
            audit_event = _emit(app.state, auth_ctx.scope, "decision", _enf_block_payload)
            _METRICS["requests_total{outcome=block}"] += 1
            # Surface block as MCP tool-error (result.isError=true) instead
            # of a JSON-RPC -32603. Reason: -32603 ("Internal error") is the
            # JSON-RPC signal for transport/system failure, which causes
            # well-trained agents (Claude, GPT-4) to retry with adjusted
            # parameters — looping uselessly against a deterministic policy.
            # A result.isError response is the MCP-spec way to say "the tool
            # ran and returned a structured failure" — agents read it as
            # final, surface to the user, and don't retry.
            block_text = (
                "POLICY_BLOCK\n"
                f"policy_id: {decision.policy_id or 'unknown'}\n"
                f"reason: {decision.reason or 'blocked by policy'}\n\n"
                "This is a final decision from the Tessera policy gate. "
                "Do NOT retry with different parameters — the policy will "
                "reject equivalent calls. Surface this block to the user."
            )
            resp_body = {
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "result": {
                    "content": [{"type": "text", "text": block_text}],
                    "isError": True,
                },
            }
            _inject_audit_id(resp_body, audit_event["eventId"])
            return JSONResponse(resp_body)

        if decision.action == Action.require_approval:
            reason_str = f"approval_required: {decision.reason or ''}"
            _enf_appr_cost = cost_cache.get(tool_name)
            _enf_appr_payload: dict[str, Any] = {
                "mode": "enforcement",
                "decision": "require_approval",
                "policy_id": decision.policy_id,
                "reason": reason_str,
                "upstream": upstream_name,
                "tool_call": context["tool_call"],
                "principal_id": auth_ctx.principal_id,
                "request_id": request_id,
                "decision_error": decision.decision_error,
                "canonical_tool_name": tool_name,
                "effective_tool_name": context.get("_effective_tool_name", tool_name),
                "conversation_id": _conversation_id,
            }
            if _enf_appr_cost is not None:
                _enf_appr_payload["cost_source"] = _enf_appr_cost.source
                _enf_appr_payload["cost_band"] = _enf_appr_cost.confidence_band
            audit_event = _emit(app.state, auth_ctx.scope, "decision", _enf_appr_payload)
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
    """Emit an audit event for the given scope. Swallows sink errors.

    Automatically attaches the current pricing_snapshot_id when available.

    P0-13: allocates the event_id synchronously so the response can inject it
    immediately, then offloads the SHA-256 stamp + sink writes to the async
    queue (`AsyncAuditQueue`). The hot path returns in ~µs — only the ID
    allocation + a queue put_nowait happens inline.

    TESSERA_AUDIT_SYNC=1 env var restores fully synchronous behaviour for
    tests that need deterministic ordering and immediate persistence.
    """
    emitter = _get_or_create_emitter(state, scope)
    event_id = AuditEmitter._new_event_id()

    audit_queue: AsyncAuditQueue | None = getattr(state, "audit_queue", None)

    if sync_mode_enabled() or audit_queue is None:
        # Synchronous fallback (tests, or startup-time emits before queue exists).
        try:
            return emitter.emit_with_id(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                pricing_snapshot_id=_pricing_snapshot_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("event=audit_emit_failed scope=%s error=%s", scope, exc)
            _METRICS["audit_emit_failures_total"] += 1
            return {"eventId": f"evt_failed_{uuid.uuid4().hex[:10]}"}

    audit_queue.enqueue(
        emitter=emitter,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        pricing_snapshot_id=_pricing_snapshot_id,
    )
    return {"eventId": event_id}


async def _prefetch_cost(
    tool_name: str,
    arguments: Any,
    cost_backend: Any | None,
    aws_mapping_mod: Any | None,
) -> dict[str, CostResult]:
    """Resolve a CostResult for tool_name and return it in a cache dict.

    Resolution order:
    1. `cost_for_call()` (module-level price-table registry) — sub-millisecond.
    2. If source == "miss" AND a live cost_backend (InfracostClient) is configured:
       `cost_backend.query_sku(...)`.  On hit, builds a CostResult(source="infracost_live").
       Logs WARNING so operators know the live path fired.
    3. Both miss → CostResult.miss(tool_name).

    Always returns a dict with exactly one entry keyed on tool_name.
    """
    from tessera.cost import CostResult, cost_for_call  # noqa: PLC0415

    region_arg: str | None = arguments.get("region") if isinstance(arguments, dict) else None
    result = cost_for_call(tool_name, arguments if isinstance(arguments, dict) else {}, region=region_arg)

    if result.source == "miss" and cost_backend is not None and aws_mapping_mod is not None:
        query = aws_mapping_mod.map_request(tool_name, arguments)
        if query is not None:
            try:
                sku_result = await cost_backend.query_sku(
                    query.service, query.region, query.attributes
                )
                if sku_result is not None:
                    logger.warning(
                        "event=cost_live_fallback tool=%s reason=price_table_miss",
                        tool_name,
                    )
                    result = CostResult(
                        price_usd=sku_result.usd_per_unit,
                        unit=sku_result.unit,
                        confidence_band=sku_result.confidence_band,
                        source="infracost_live",
                        operation=tool_name,
                    )
            except Exception:  # noqa: BLE001
                pass

    return {tool_name: result}


def _record_daily_spend(
    state: Any,
    *,
    scope: str,
    tool_name: str,
    cost_cache: dict[str, CostResult],
) -> None:
    """Write back per-call cost to the daily-spend state backend (P0-18).

    The proxy's `cumulative_spend_today` condition reads from this backend; if
    no caller writes to it, every cap silently no-ops. We use the pre-fetched
    cost estimate (`cost_cache[tool_name]`) — the actual post-call cost is
    usually not knowable until the upstream returns usage metadata, which is
    P2 work. For most AWS operations the estimate is fixed-per-call and a
    good approximation.

    Fires-and-forgets via `asyncio.create_task` + `asyncio.to_thread` so the
    SQLite WAL fsync never blocks the event loop. Failures bump a counter
    but never affect the customer's response.

    Skips the write when source == "miss" (no price data available).
    """
    state_backend = getattr(state, "state_backend", None)
    if state_backend is None:
        return
    cost_result = cost_cache.get(tool_name)
    if cost_result is None or cost_result.source == "miss" or cost_result.price_usd is None:
        return
    try:
        est_usd = float(cost_result.price_usd)
    except (TypeError, ValueError):
        return
    if est_usd <= 0:
        return

    async def _spend_async() -> None:
        try:
            await asyncio.to_thread(state_backend.add_spend, scope, est_usd)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=daily_spend_write_failed scope=%s tool=%s error=%s",
                scope,
                tool_name,
                exc,
            )
            _METRICS["daily_spend_write_failures_total"] += 1

    try:
        asyncio.create_task(_spend_async())
    except RuntimeError:
        # No running event loop (rare; sync test harness). Fall back to a sync
        # write so the spend still lands somewhere.
        try:
            state_backend.add_spend(scope, est_usd)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=daily_spend_write_failed_sync scope=%s tool=%s error=%s",
                scope,
                tool_name,
                exc,
            )
            _METRICS["daily_spend_write_failures_total"] += 1


async def _forward_upstream(
    state: Any,
    upstream_name: str,
    body: dict[str, Any],
    jsonrpc_id: Any,
) -> dict[str, Any] | JSONResponse:
    """Forward a request to the named upstream. Returns parsed response dict or JSONResponse on error.

    Dispatches on upstream kind:
      - "aws_mcp" → AWSMcpUpstream.forward() (IAM-signed streamable HTTP)
      - "bearer" / default → existing httpx.AsyncClient path
    """
    # Determine which upstream kind this is and dispatch accordingly.
    cfg: TesseraConfig = state.config
    upstream_cfg = next((u for u in cfg.upstreams if u.name == upstream_name), None)

    upstream_kind = upstream_cfg.kind if upstream_cfg is not None else "bearer"

    match upstream_kind:
        case "aws_mcp":
            aws_clients: dict[str, AWSMcpUpstream] = getattr(state, "aws_clients", {})
            aws_client = aws_clients.get(upstream_name)
            if aws_client is None:
                _METRICS["requests_total{outcome=unknown_upstream}"] += 1
                return _jsonrpc_error(
                    jsonrpc_id, -32001, "Upstream error", reason=f"unknown upstream: {upstream_name!r}"
                )
            return await aws_client.forward(body)

        case "mcp_streamable_http":
            sh_clients: dict[str, StreamableHttpUpstream] = getattr(state, "streamable_http_clients", {})
            sh_client = sh_clients.get(upstream_name)
            if sh_client is None:
                _METRICS["requests_total{outcome=unknown_upstream}"] += 1
                return _jsonrpc_error(
                    jsonrpc_id, -32001, "Upstream error", reason=f"unknown upstream: {upstream_name!r}"
                )
            return await sh_client.forward(body)

        case _:
            # Default bearer / httpx path
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


# Methods that pass through without policy evaluation but carry data-exfil risk.
# These emit an additional passthrough_data_leak_candidate audit event when
# audit.flag_data_leak_passthrough is True (the default).
_DATA_LEAK_PASSTHROUGH_METHODS = frozenset(
    {
        "prompts/get",
        "resources/read",
        "resources/subscribe",
        "completion/complete",
        "sampling/createMessage",
    }
)

_MAX_PARAM_VALUE_LEN = 1024  # bound audit-row size for data-leak events


def _truncate_params(params: Any) -> Any:
    """Return params with string values longer than 1 KB replaced by a truncation marker."""
    if not isinstance(params, dict):
        return params
    result: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > _MAX_PARAM_VALUE_LEN:
            result[k] = f"<truncated {len(v)} chars>"
        else:
            result[k] = v
    return result


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

    method: str = body.get("method", "")

    # For the 5 data-exfil-risk methods, emit an additional audit event so operators
    # have visibility without needing to enable full policy evaluation for them.
    if method in _DATA_LEAK_PASSTHROUGH_METHODS and cfg.audit.flag_data_leak_passthrough:
        _emit(
            state,
            auth_ctx.scope,
            "passthrough_data_leak_candidate",
            {
                "method": method,
                "params": _truncate_params(body.get("params")),
                "principal_id": auth_ctx.principal_id,
                "scope": auth_ctx.scope,
                "upstream": upstream_name,
                "request_id": request_id,
            },
        )

    audit_event = _emit(
        state,
        auth_ctx.scope,
        "passthrough",
        {
            "upstream": upstream_name,
            "method": method,
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
