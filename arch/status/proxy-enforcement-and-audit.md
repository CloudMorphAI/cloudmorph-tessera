# Proxy, Enforcement, and Audit

The hot path: MCP call interception, policy evaluation, hash-chained audit emission, upstream forwarding. This is the load-bearing surface of the OSS firewall — every other subsystem (policy engine, intelligence client, cost backend, AWS upstream) feeds this path. It is also the architectural wedge that distinguishes Tessera from probabilistic / ML-driven competitors: the decision for a given input is byte-identical across runs, with no LLM in the loop and no remote authorization call.

## The FastAPI proxy

`tessera/proxy.py` exports `create_app(config)` which returns a configured FastAPI application. The app exposes a small surface:

| Route | Purpose |
|-------|---------|
| `POST /mcp/{upstream_name}` | The MCP interception endpoint. Every `tools/call` flows through here. |
| `POST /intent` | Derive intent verbs deterministically from a tool name (consumed by `cursor_hooks.py`). |
| `GET  /healthz` | Liveness probe. Returns loader state (loaded count + errored files). |
| `GET  /readyz` | Readiness probe. Confirms config loaded, policies loaded, ≥1 upstream reachable. |
| `GET  /metrics` | Prometheus-format counters. Disabled by default; bearer auth required when enabled. |

A single FastAPI `lifespan` async context manager handles startup and shutdown. Startup resolves the three pluggable Protocol implementations (see below), opens the audit sink, restores hash-chain heads from persisted state, loads policies, instantiates upstream clients (httpx for `bearer`, AWS-IAM-signed for `aws_mcp`), and optionally initializes the Infracost / blast-radius / state / intelligence backends. Shutdown closes everything in reverse order.

## Pluggable Protocols

Three runtime extension points let Tessera Cloud and custom deployments swap implementations without modifying core code:

| Protocol | Default impl | Env-var override | Purpose |
|----------|--------------|------------------|---------|
| `Authenticator` | `BearerTokenAuthenticator` (or `JWTAuthenticator` when `auth.type: jwt`) | `TESSERA_AUTHENTICATOR` | Validate incoming MCP requests, return `AuthContext(principal_id, scope, metadata)` |
| `AuditSink` | `SqliteSink` | `TESSERA_AUDIT_SINK` | Persist + iterate audit events; report `head_hash(scope)` |
| `PolicyLoader` | `FilesystemPolicyLoader` | `TESSERA_POLICY_LOADER` | Load policies from a backing store; provide `watch()` for hot reload |

Resolution is `module.path:ClassName` via `tessera/pluggable.py:resolve()`. Pure importlib; no plugin registry. Tessera Cloud's deployment overrides all three (e.g., `DynamoDBPolicyLoader`, `PostgresAuditSink`, OIDC-based authenticator) without forking the engine.

## The interception flow

`POST /mcp/{upstream_name}` is the single hot-path entry. Steps, in order:

1. **Authenticate.** `app.state.authenticator.authenticate(request)` returns `AuthContext`. Bearer mode uses `secrets.compare_digest` for constant-time match across the configured token list; JWT mode validates against a JWKS endpoint with TTL-cached keys and re-fetches on unknown `kid`. Dev mode (no tokens configured) returns `principal_id="anonymous"`, scope=`deployment_id`, and prints a recurring `auth_disabled` warning every 60 seconds.
2. **Parse JSON-RPC body.** Malformed JSON returns `-32700`. The `method`, `id`, and `params` fields are extracted.
3. **Branch on method.** `notifications/*` and an 11-method pass-through set (`initialize`, `ping`, the four `*/list`, `logging/setLevel`, `resources/unsubscribe`, and the five data-exfil-risk methods) are forwarded without policy evaluation. The five data-leak methods (`prompts/get`, `resources/read`, `resources/subscribe`, `completion/complete`, `sampling/createMessage`) additionally emit a `passthrough_data_leak_candidate` audit event when `audit.flag_data_leak_passthrough: true` (the default). Method ≠ `tools/call` and not in the pass-through set returns `-32601`.
4. **Extract intent.** `tessera/intent.py:extract_intent()` reads `params._meta[<intent.meta_key>]` (default `tessera_intent`). When `intent.required: true` and intent is absent, the call is blocked with reason `intent_required`. Off-the-shelf agents (Cursor, Claude Desktop, Windsurf) typically don't supply intent; policies that need it are skipped via `match.require_intent: true`.
5. **Pre-fetch cost.** If both an Infracost backend and an `aws_mapping` module are configured, the proxy calls `aws_mapping.map_request(tool_name, args)` to build a query and then `cost_backend.query_sku(...)` to fetch a price. Result is stored in `context["cost_cache"][tool_name]`. This is the async→sync bridge that lets the synchronous `predicted_cost` condition evaluator consult cost data without spinning an event loop inside the engine.
6. **Build evaluation context.** A dict with `tool_call`, `intent`, `upstream`, `runtime`, `mode`, `policy_id`, `scope`, `cost_backend`, `cost_cache`, `aws_mapping`, `blast_radius_backend`, `state_backend`. The engine reads everything it needs from this context.
7. **Lockdown short-circuit.** `runtime.lockdown: true` blocks every call before the engine runs. The reason field on the audit event is `lockdown_active`. This is a kill-switch for incident response.
8. **Decision cache lookup (v0.7.0).** Before invoking the engine, `app.state.decision_cache.get(scope, tool_name, tool_call)` is consulted. On hit, the cached `allow`/`observed` decision is reused — sub-microsecond. On miss, evaluate then `put(...)`. `block` and `require_approval` decisions are NEVER cached (they always re-evaluate against current policy so a fix-up rule takes effect immediately). Cache cleared on every `CloudPolicySync` reload — see `arch/status/control-plane-v0.7.0.md`.
9. **Mode branch.**
   - `observation` — engine skipped; upstream always called; audit event records `decision: null`.
   - `log_only` — engine evaluates; decision is recorded as `would_decision`; upstream is **always** forwarded regardless of decision. `X-Tessera-Mode`, `X-Tessera-Decision` (`would_block` / `would_allow` / `no_match`), and on `would_block` also `X-Tessera-Policy-Id` and `X-Tessera-Reason` headers are added to the response.
   - `enforcement` — engine result is honored.
9. **Honor decision.**
   - `allow` / `log_only` action — forward to upstream, audit success, return upstream response with `tessera_audit_event_id` injected at `result._meta` or `error.data._meta`.
   - `block` — emit audit event, return JSON-RPC error `-32603` ("Internal error") with the policy's `reason` field at `error.data.reason`.
   - `require_approval` — emit audit event with `decision: require_approval`, return JSON-RPC error `-32604` ("Approval required") with `reason = approval_required: <policy reason>`. There is no approval-fulfillment surface today; the JSON-RPC error returns to the agent unchanged and the human-approval workflow is the operator's responsibility.

Steps 1–9 are sequential. The only concurrency is the optional pre-fetch (step 5) which `await`s the cost backend before the engine runs synchronously.

## Determinism as the architectural wedge

The decision returned at step 9 is a pure function of the policy YAML files on disk, the request body, the optional pre-fetched cost number, and the optional state-backend reads (cumulative-spend, blast-radius-cache, S3 head). For a fixed input set, the same decision is produced byte-for-byte across runs. No LLM. No probabilistic classifier. No round-trip to an authorization service that could change its mind between calls.

This is the wedge against Runlayer/MintMCP/Aurascape competitors that build firewalls on ML classification of tool-call risk. Those systems produce a different decision on the same input depending on training-data drift, model version, or temperature. Tessera's customer can run `tessera policy test --fixture-dir tests/fixtures/` in CI and prove that policy version `X` produces decision `Y` on input `Z` — without ever calling out to a cloud service or sampling a model.

The cost paid for determinism is that nuanced behavior (LLM-judged "this looks malicious," intent-based classification) is out of scope for the engine. Those classifications happen pre-engine: `cursor_hooks.py` calls `/intent` to derive verbs, the agent supplies `tessera_intent` in `_meta`, the LLM policy-author subsystem helps authoring time. Once a request enters the engine, the decision is mechanical.

## Audit log: hash-chained per-scope event stream

Every decision (and most pass-throughs) is written to an audit event. The audit subsystem is a three-layer split:

- **`HashChain` (`tessera/audit/chain.py`)** — in-memory per-scope rolling SHA-256 bookkeeping. `head(scope)` returns the most-recent event hash; `stamp(event)` sets `prevEventHash`, computes a new `eventHash` over the canonical JSON of the stamped event, advances the head, and returns the result. Thread-safe via `RLock`. Persistence is the sink's concern.
- **`AuditEmitter` (`tessera/audit/emitter.py`)** — fan-out to ≥1 sink with per-sink failure isolation. Builds the event dict (`schemaVersion`, `eventId`, `tenantId`, `eventType`, `payload`, `occurredAt`), routes it through `HashChain.stamp()`, then calls `sink.emit(stamped)` on every sink. A sink that raises `AuditSinkError` is logged; other sinks continue. `TESSERA_DEBUG=1` runs each event through `schemas/audit_event.schema.json` for development-time schema validation.
- **`AuditSink` Protocol (`tessera/audit/sinks/base.py`)** — `emit(event)`, `close()`, `head_hash(scope)`, `iter_events(scope)`. Default impl `SqliteSink` uses WAL journal mode and `PRAGMA synchronous=NORMAL`; schema in `tessera/audit/sinks/sqlite.py:_CREATE_TABLE`. `StdoutSink` (for Docker log collection) and a `_buffered` internal wrapper round out the bundled set. `BufferedSink` is internal and not exported in the public `tessera.audit` namespace.

The event-schema contract is `schemas/audit_event.schema.json`. Required fields: `schemaVersion` (literal `v0.1`), `eventId` (`evt_<urlsafe-base64>`), `tenantId`, `eventType`, `occurredAt` (RFC 3339), `prevEventHash` (64-char hex or empty), `eventHash` (64-char hex), `payload`. Optional: `sessionId`, `actorId`, `pricingSnapshotId`.

**v0.3.0 cost fields.** When `cost_for_call()` resolves a price for the tool call being evaluated, the proxy additionally records `cost_source` (one of `"price_table"` / `"infracost_live"` / `"miss"`) and `cost_band` (`"high"` / `"medium"` / `"ceiling"`) in the audit event payload. These fields are present only when a price was prefetched (i.e., `cost_source != "miss"`); absent on calls where no cost mapping is registered. Operators can filter the audit log on `cost_source` to distinguish price-table-resolved calls from live Infracost fallbacks.

**`canonical_tool_name` + `effective_tool_name` (v0.3.0)**: every audit event for a `tools/call` carries both fields. For native `aws_*_*` flows they're identical. For `call_aws` invocations (i.e., traffic from `awslabs/mcp/aws-api-mcp-server`), `canonical_tool_name == "call_aws"` and `effective_tool_name == "<resolved canonical>"` (e.g., `"aws_iam_PassRole"`). Operators searching the audit log by either name find the event. Both fields are declared as optional strings in `schemas/audit_event.schema.json`.

## Hash-chain canonicalization rule

`tessera/audit/canonical_json.py` implements RFC 8785 JCS. Object keys are sorted lexicographically, no whitespace, `ensure_ascii=False`, integers serialize as integers (even if originally `float`), NaN/Infinity rejected with `ValueError`. The event hash is computed over `canonical_json({...event, eventHash: "", signature: ""})` — the empty `eventHash` and `signature` fields are zeroed before hashing so the hash includes itself by construction.

This rule must match exactly across any reimplementation of the verifier. The Python and (planned) TypeScript verifiers must produce identical 64-char digests for identical event content. `tessera audit verify` uses the same code path as the emitter, so an event always re-hashes to its stored value when verified locally — divergence indicates tamper.

## Per-scope chain isolation

The hash chain is keyed by `tenantId`, which the proxy populates from `AuthContext.scope` (per-token scope from `TESSERA_BEARER_TOKENS`, JWT scope claim, or `deployment_id` in dev mode). Each scope maintains its own independent chain stream. A `scope=alice` audit chain and a `scope=bob` chain share zero events; they can be verified independently and exported independently.

This is the multi-tenant primitive: a Tessera Cloud deployment can serve dozens of customers from one process, each with their own audit chain, each verifiable end-to-end without cross-customer leakage.

## Chain restore across restarts

On process restart, every scope's head needs to be restored before new events are appended — otherwise a fresh process would start the chain over and break verification. The lifespan startup calls `SqliteSink.iter_scopes()` to enumerate persisted scopes, then `sink.head_hash(scope)` for each, then `chain.restore_head(scope, head)` to seed the in-memory bookkeeping. `restore_head` validates the input is a 64-char lowercase hex digest; an invalid value is logged and skipped (the next `emit()` will start a fresh chain — bug-on-tamper rather than silent drift). The same path is invoked lazily by `_get_or_create_emitter` when an event is emitted for a scope not seen at startup.

## Local state backend

`tessera/state/daily_spend.py:DailySpendState` is the sole stateful backend in the OSS package today. It's a thread-safe SQLite store keyed `(scope, day)` → `cumulative_usd`, used by the `cumulative_spend_today` condition (described in `policy-engine.md`). The DB lives at `~/.tessera/state/daily_spend.db` by default, overridable via `TESSERA_STATE_DIR`. Day boundaries are UTC; `add_spend()` uses `INSERT ... ON CONFLICT DO UPDATE` to accumulate.

Cost write-back is wired into the proxy's success path (P0-18, shipped 2026-05). After an `allow` or `observation`-mode forward, the proxy calls `_record_daily_spend(state, scope, tool_name, cost_cache)` which reads the prefetched cost estimate (from `cost_cache[tool_name]`, populated by the price-table or Infracost prefetch in step 5 of the interception flow) and schedules `state_backend.add_spend(scope, usd)` via `asyncio.create_task` + `asyncio.to_thread`. The write is fire-and-forget so the SQLite WAL fsync never blocks the customer's response; failures bump `daily_spend_write_failures_total` and are logged at WARN. The write uses the pre-call estimate (not the actual post-call cost) — for usage-priced ops (Bedrock token-spend, S3 GET egress) the estimate is a ceiling. Post-call usage-based reconciliation is a future enhancement.

State survival across restarts is by virtue of SQLite persistence; the connection is opened lazily on first lookup, schema is created if absent. Regression coverage lives in `tests/unit/state/test_daily_spend.py` — it pins the contract that `add_spend()` actually persists to disk and survives a re-open.

## Async audit emit (P0-13)

The audit emit path used to be synchronous: the request handler called `_emit(...)`, which acquired an `RLock`, computed a SHA-256 over canonical JSON, then called `SqliteSink.emit()` (a WAL-mode INSERT with `synchronous=NORMAL`). Total cost: 0.5–5 ms per call inside `async def proxy(...)`, with a worst-case 10 ms when the WAL fsync hit a slow disk. At 50 rps × 3 emits per request that's 15–75% of an event-loop worker spent in audit emit.

The current shape moves the stamp + sink work off the hot path:

- **`AuditEmitter.emit_with_id(...)`** — variant that uses a caller-supplied `event_id`. The hot path allocates the ID synchronously (cheap — `secrets.token_urlsafe` only), injects it into the response immediately, and defers stamp + persist.
- **`AsyncAuditQueue` (`tessera/audit/async_emit.py`)** — single-consumer `asyncio.Queue` with a background task that drains via `asyncio.to_thread(emitter.emit_with_id, ...)`. Soft-bounded at 10k in-flight jobs; on overflow, drops the oldest event and bumps `audit_emit_dropped_total`. A single consumer is intentional: the chain still goes through `HashChain.stamp()`'s `RLock`, but consumer FIFO order is deterministic and matches enqueue order.
- **`_emit(...)` in `proxy.py`** — allocates the event_id, calls `audit_queue.enqueue(...)` (cheap `put_nowait`), and returns `{"eventId": event_id}` immediately. Two override paths:
  - `TESSERA_AUDIT_SYNC=1` — fully synchronous emit (legacy behaviour, used by tests that need deterministic flush before assertions).
  - `audit_queue is None` — pre-lifespan emits (startup audit event) fall back to a sync emit so the chain still starts cleanly.
- **Lifespan integration** — `audit_queue.start()` fires after the sink + emitter map are constructed; `await audit_queue.drain()` runs in lifespan shutdown before `sink.close()` so any in-flight events flush against a live SQLite handle. Drain has a 10s timeout; on overshoot the consumer is cancelled and the warning is logged.

Failure modes:
- Sink raises mid-stamp → consumer logs `audit_emit_failed_async`, bumps `audit_emit_failures_total`, continues draining the queue.
- Queue overflow → event dropped, `audit_emit_dropped_total` bumped.
- Drain timeout → cancelled, `audit_drain_timeout` logged with the residual queue depth.

Chain integrity is preserved: `HashChain.stamp()` is still called once per event under the per-scope `RLock`, so even concurrent enqueues from multiple `asyncio.to_thread` workers (none in the single-consumer design, but defensive) cannot interleave stamping. The chain order matches consumer-drain order, which matches enqueue order, which matches request-arrival order.

Regression coverage: `tests/unit/audit/test_async_emit.py` covers `emit_with_id`, drain semantics, sync-fallback, and consumer-survives-sink-failure.

## Pass-through audit emissions

Pass-through methods (the 11-method set) emit a `passthrough` audit event for visibility without going through the engine. The five data-exfil-risk methods additionally emit a `passthrough_data_leak_candidate` event with the method name, truncated params (string values > 1 KB are replaced with `<truncated N chars>`), principal_id, scope, upstream, and request_id. This audit-only handling is a deliberate compromise (per OQ-1): real-world traffic patterns for these methods aren't yet understood, so policy evaluation is deferred, but operators get visibility without an explicit policy opt-in.

The truncation marker bounds audit-row size at ~1 KB per param value, which keeps the SQLite WAL from ballooning on long completion arguments.

## Cursor Hooks as one integration surface among several

`tessera/integrations/cursor_hooks.py` is a hook script Cursor v1.7-beta fires on `beforeMCPExecution` and `afterMCPExecution`. The script reads the hook payload from stdin, calls Tessera's `POST /intent` endpoint to derive deterministic verb metadata, and returns a JSON `action: allow` (with the intent envelope attached) or `action: deny` (when `TESSERA_CURSOR_FAIL_CLOSED=true` and Tessera is unreachable). Cursor v1.7-beta has a known bug where the `allow`/`ask` paths are unreliable; only `deny` is enforced — so this hook is currently used for telemetry and intent enrichment, not enforcement. Final enforcement always happens at the proxy when the MCP request itself transits.

The hook is one of three documented integration surfaces. The others (`recipes/claude-code.md`, `recipes/cursor-mcp-json.md`) point clients at Tessera as an HTTP MCP server via plain config. From the proxy's perspective all three look identical — an inbound `POST /mcp/<upstream>` with a JSON-RPC body. The Cursor Hooks path is more capable because it can inject intent metadata into the audit envelope; the plain-MCP-config paths are simpler and work for clients without hook support.

For the demo walkthrough see `examples/cursor_hooks_demo/`; do not duplicate that content here.

## CLI entry points relevant to the proxy

`tessera/cli.py` is the Typer-based entry point. The serve subcommand is the production path:

- `tessera serve --config tessera.yaml --policy-dir policies/ --bind 0.0.0.0:8080` starts uvicorn against `create_app(config)`. CLI flags override config-file values for host, port, policy directory, log level.
- `tessera init [--dir <path>] [--force]` scaffolds a `tessera.yaml`, a `policies/` directory, and `.env.example`. Default mode in the scaffolded YAML is `log_only` — safe to try, nothing is blocked yet.
- `tessera install-cursor-hooks` and `tessera install-claude-code` write the integration glue into Cursor's hooks config and `~/.claude.json` respectively. The latter refuses to overwrite an existing entry without `--upgrade` (A-4-8).

The `tessera audit` subcommand group exposes four inspection operations against the SQLite audit database:

- `tessera audit tail [--scope S] [--limit N] [--follow] [--json]` — prints the most recent N events in human-readable form (timestamp, event_id, event_type, key payload fields). `--follow` polls every second for new events. Implementation delegates to `tessera/audit/inspect.py:tail_events` which calls `SqliteSink.iter_recent`.
- `tessera audit verify-chain [--scope S] [--all] [--json]` — walks the hash chain and prints the first broken link with structured output (seq, event_id, kind, expected/computed hashes). Exit code 0 on success, 3 on any failure — matching the convention established by the older `tessera audit verify` subcommand. Delegates to `tessera/audit/verifier.py:verify_chain`.
- `tessera audit export [--scope S] [--format jsonl|csv] [--output PATH]` — bulk export. `jsonl` emits one full-JSON event per line, suitable for `jq`, Splunk, Vector, Elasticsearch. `csv` emits flat columns (`event_id, scope, event_type, occurred_at, prev_event_hash, event_hash, payload`); the payload column is JSON-stringified with large cells truncated at 4 KB. Both formats preserve the `prev_event_hash` and `event_hash` columns so downstream tools can re-verify the chain offline.
- `tessera audit inspect <eventId>` — fetch one event by ID and print its full JSON. Delegates to `tessera/audit/inspect.py:fetch_event_by_id` which calls `SqliteSink.fetch_by_id`.

Helper functions (`tail_events`, `export_jsonl`, `export_csv`, `fetch_event_by_id`) live in `tessera/audit/inspect.py`; the CLI subcommands are thin wrappers that handle argument parsing, file I/O, and exit codes. Hash-chain repair (replacing a corrupt event and re-stamping forward) is deliberat