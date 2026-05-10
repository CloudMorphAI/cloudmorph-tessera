# Tessera v0.1 — Architecture

This document is for contributors and anyone who wants to understand how Tessera
works internally. For operator documentation see `CONFIGURATION.md`, `POLICIES.md`,
and `AUDIT.md`.

---

## Table of contents

1. [Component overview](#1-component-overview)
2. [Request lifecycle](#2-request-lifecycle)
3. [Audit subsystem](#3-audit-subsystem)
4. [Pluggable Protocols](#4-pluggable-protocols)
5. [Why no OPA in v0.1](#5-why-no-opa-in-v01)
6. [Tessera Cloud — FROM-base relationship](#6-tessera-cloud--from-base-relationship)
7. [Local dev setup](#7-local-dev-setup)

---

## 1. Component overview

Tessera is a thin HTTP proxy that sits between an AI agent and one or more
upstream MCP servers. Every `tools/call` request passes through three stages
before reaching the upstream: authentication, policy evaluation, and audit
emission.

```
Agent
  |
  | POST /mcp/{upstream}
  |   Authorization: Bearer <token>
  v
+------------------------------------------------------------------+
|  Tessera                                                         |
|                                                                  |
|  +------------+    +----------------+    +--------------------+  |
|  | auth       | -> | engine         | -> | audit              |  |
|  | (bearer)   |    | (policy eval)  |    | (emitter + chain)  |  |
|  +------------+    +----------------+    +--------------------+  |
|        |                  |                        |             |
|        |                  |                   +---------+        |
|        |                  |                   | sinks   |        |
|        |                  |                   | sqlite  |        |
|        |                  |                   | stdout  |        |
|        |                  |                   +---------+        |
+--------|------------------|-------------------------|------------+
         |            allow/log_only                  |
         |                  |                         |
         v                  v                         v
     401 / block      Upstream MCP             audit.db / stdout
```

**Components:**

| Component | Module | Responsibility |
|---|---|---|
| Proxy | `tessera/proxy.py` | FastAPI application. Routes `/mcp/{upstream}`, `/healthz`, `/readyz`, `/metrics`. Orchestrates the auth → engine → audit pipeline. |
| Config | `tessera/config.py` | Pydantic models for `tessera.yaml`. Env-var overrides. `${VAR}` interpolation for upstream credentials. |
| Auth | `tessera/auth/` | `Authenticator` Protocol + default `BearerTokenAuthenticator`. Multi-token bearer auth with per-token scope. |
| Intent | `tessera/intent.py` | Extracts and validates `_meta.<meta_key>` intent blocks from MCP `tools/call` params. |
| Policy loader | `tessera/policy/loader.py` | `FilesystemPolicyLoader`. Reads YAML policies from disk, validates each against the Pydantic schema, watches for changes. Per-file reload error isolation. |
| Policy engine | `tessera/policy/engine.py` | `PolicyEngine.evaluate(context)`. Pure-Python evaluation. First-match-wins. Lockdown short-circuit. Mode-agnostic. |
| Matchers | `tessera/policy/matchers.py` | Upstream and tool name matching (glob, regex, wildcard). |
| Conditions | `tessera/policy/conditions.py` | Evaluates the `when:` clause of a policy. All 16 condition types. Regex via the `regex` library with a 100 ms per-match timeout. |
| Regex safety | `tessera/policy/regex_safety.py` | Load-time ReDoS corpus test. Rejects patterns that take more than 50 ms on synthetic strings at startup (exits 2); skips on reload. |
| Action verbs | `tessera/policy/action_verbs.py` | Built-in verb taxonomy. User extensions via `policies/_action_verbs.yaml`. |
| Audit chain | `tessera/audit/chain.py` | SHA-256 per-scope rolling hash. Thread-safe. In-process only — does not persist. |
| Canonical JSON | `tessera/audit/canonical_json.py` | RFC 8785 JCS implementation used to produce deterministic hash inputs. |
| Audit emitter | `tessera/audit/emitter.py` | Builds the event dict, stamps hash chain, fans out to all sinks. Per-sink failure isolation. |
| Audit verifier | `tessera/audit/verifier.py` | `verify_chain()` — chain-walk integrity check for `tessera audit verify`. |
| Sinks | `tessera/audit/sinks/` | `AuditSink` Protocol + `SqliteSink` (default), `StdoutSink`, `BufferedSink`. |
| Pluggable resolver | `tessera/pluggable.py` | `resolve("module:Class")` — `importlib`-based loader for the three Protocols. |
| CLI | `tessera/cli.py` | Typer CLI: `serve`, `audit verify`, `policy test`, `policy lint`, `version`, `init`. |

---

## 2. Request lifecycle

This section traces a single `tools/call` request end-to-end. Other methods
(`tools/list`, `initialize`, `ping`, `notifications/*`) follow the
**pass-through** path: authenticated, forwarded directly, audited as
`event_type: passthrough`, no policy evaluation.

### Step-by-step for `POST /mcp/{upstream}` with `method: tools/call`

**Step 1 — Authenticate.**
`BearerTokenAuthenticator.authenticate(request)` extracts the
`Authorization: Bearer <token>` header. It iterates the configured token list
using `secrets.compare_digest` (constant-time) for each candidate. A match
produces an `AuthContext(principal_id, scope, metadata)`. The `scope` field
drives which audit hash chain stream the event lands in.

- No match → `UnauthorizedError` → HTTP 401; request ends.
- Empty token list (dev mode) → `AuthContext("anonymous", deployment_id, {"warning": "auth_disabled"})` with a warning logged.

**Step 2 — Parse JSON-RPC.**
The body is parsed as JSON. Malformed JSON → HTTP 400, JSON-RPC code `-32700`.
The `method` field is read. Unrecognized methods → HTTP 400, code `-32601`.

**Step 3 — Extract intent (optional).**
`extract_intent()` reads `params._meta.<meta_key>` (default: `tessera_intent`).
If `intent.required: true` is set globally and no intent block is present, the
request is blocked immediately with reason `intent_required` and audited.
If `intent.required: false` (default), a missing intent block is allowed; only
policies with `match.require_intent: true` will be skipped.

**Step 4 — Build evaluation context.**

```python
context = {
    "tool_call": {"name": tool_name, "arguments": arguments, "_meta": meta},
    "intent": intent,            # None if absent and not required
    "upstream": upstream_name,
    "runtime": {"lockdown": cfg.runtime.lockdown},
    "mode": cfg.policies.mode.value,
    "policy_id": None,
}
```

**Step 5 — Lockdown check.**
If `runtime.lockdown: true`, the request is blocked before the mode branch.
JSON-RPC code `-32603`, reason `lockdown_active`. Audited. Applies in all
three modes.

**Step 6 — Mode branch.**

The `policies.mode` setting (set once at deployment level) controls how the
engine's `Decision` is acted on:

| Mode | Engine called? | Upstream called? | Audit payload key |
|---|---|---|---|
| `enforcement` | yes | only on `allow` / `log_only` action | `decision` |
| `log_only` | yes | always | `would_decision` |
| `observation` | no | always | (no decision field) |

**`enforcement`** — The engine evaluates the sorted policy list. First policy
whose `match` block and `when` conditions all pass wins. The proxy honors the
`Decision.action`:

- `allow` or `log_only` (per-policy action): forward to upstream, return
  upstream response, audit `decision: allow/log_only`.
- `block`: return JSON-RPC error `-32603` with interpolated reason, audit
  `decision: block`. Upstream is NOT called.
- `require_approval`: return JSON-RPC error `-32604` with reason
  `approval_required: <reason>`, audit `decision: require_approval`. Upstream
  is NOT called.
- No match: use `default_action` from config (default: `block`).

**`log_only`** — The engine evaluates, but the upstream is always called
regardless of the decision. The response carries extra headers:

```
X-Tessera-Mode: log_only
X-Tessera-Decision: would_block | would_allow | no_match
X-Tessera-Policy-Id: <id>          (only on would_block)
X-Tessera-Reason: <reason>         (only on would_block)
```

Audit payload uses `would_decision` instead of `decision`.

**`observation`** — The engine is not called at all. The request passes
through unconditionally. Audit records `mode: observation` with no decision
field.

**Step 7 — Audit emit.**
`AuditEmitter.emit()` is called with the event payload regardless of outcome.
The emitter stamps `prevEventHash` and `eventHash` onto the event via
`HashChain.stamp()` (see Section 3), then fans out to all configured sinks.
Sink failures are isolated: one failing sink does not prevent delivery to
others, and does not affect the HTTP response to the agent.

**Step 8 — Inject audit event ID.**
After emit, `_meta.tessera_audit_event_id` is injected into every response
body (including block responses) so the agent can correlate the call to its
audit record.

### Error table

| Scenario | HTTP status | JSON-RPC code | Audit `outcome` |
|---|---|---|---|
| Missing or invalid bearer | 401 | n/a | `unauthorized` |
| Malformed JSON-RPC | 400 | `-32700` | `parse_error` |
| Unknown method | 400 | `-32601` | `unknown_method` |
| Intent required but missing | 200 | `-32603` | `block` (reason `intent_required`) |
| Lockdown active | 200 | `-32603` | `block` (reason `lockdown_active`) |
| Policy block (enforcement) | 200 | `-32603` | `block` |
| Require approval (enforcement) | 200 | `-32604` | `require_approval` |
| Upstream timeout | 200 | `-32000` | `upstream_timeout` |
| Upstream 5xx | 200 | `-32001` | `upstream_error` |
| Audit sink failure | 200 | n/a | `audit_emit_failures_total` incremented |

---

## 3. Audit subsystem

### Overview

Every request — allowed, blocked, or passed through — produces an audit event.
Events are chained: each event carries the SHA-256 hash of the previous event
in its scope. This makes silent deletion or modification of an event
detectable.

### HashChain (`tessera/audit/chain.py`)

`HashChain` is an in-process rolling-head bookkeeper. It does not write to
disk; that is the sink's job.

Key operations:

- `stamp(event)` — reads the current head hash for `event["tenantId"]`,
  sets `prevEventHash`, computes `eventHash` over the canonical-JSON
  representation of the event (with `eventHash` blanked), advances the head,
  and returns the stamped event.
- `restore_head(tenant_id, head_hash)` — called at startup to re-anchor the
  chain from the persisted `head_hash` returned by the sink. Without this,
  a restart would produce a chain that appears broken at the restart boundary.
- `verify_pair(prev, next)` — static check: `next.prevEventHash == prev.eventHash`.
- `verify_event_hash(event)` — recomputes the hash and compares to the stored
  `eventHash`. Used by the verifier.

The chain is **per-scope** (the `tenantId` field in the event, populated from
`AuthContext.scope`). A deployment with multiple tokens using distinct scopes
produces independent chain streams that do not interfere with each other.

### Canonical JSON (`tessera/audit/canonical_json.py`)

`canonical_json(obj)` produces a deterministic UTF-8 byte string: keys sorted,
no extra whitespace, non-string keys rejected, NaN/Infinity rejected. This is
the RFC 8785 JCS subset required to make `eventHash` reproducible regardless of
Python dict insertion order.

### AuditEmitter (`tessera/audit/emitter.py`)

`AuditEmitter` is the public interface for recording events. It:

1. Constructs the full event dict (`eventId`, `tenantId`, `schemaVersion`,
   `eventType`, `occurredAt`, `payload`).
2. Calls `HashChain.stamp()` under a lock to set `prevEventHash` and
   `eventHash`.
3. Iterates `self.sinks`, calling `sink.emit(stamped)` for each. Exceptions
   from individual sinks are caught and reported via the optional
   `on_sink_failure` callback; they do not propagate to the proxy.
4. Returns the stamped event so the proxy can inject `tessera_audit_event_id`.

### Sinks

The `AuditSink` Protocol (defined in `tessera/audit/sinks/base.py`) is the
contract every sink must satisfy:

```python
class AuditSink(Protocol):
    name: str
    def emit(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...
    def head_hash(self, scope: str) -> str: ...
    def iter_events(self, scope: str | None = None) -> Iterator[dict]: ...
```

`head_hash(scope)` enables restart-survivable chains: the proxy calls it at
startup to retrieve the last known hash for each scope, then passes it to
`HashChain.restore_head()`.

`iter_events(scope)` must yield events in ascending `(scope, seq)` order so
the verifier can walk the chain in sequence.

**Bundled sinks:**

| Sink | Module | Notes |
|---|---|---|
| `SqliteSink` | `tessera/audit/sinks/sqlite.py` | Default. WAL mode, `synchronous=NORMAL`. Per-scope sequential numbering. |
| `StdoutSink` | `tessera/audit/sinks/stdout.py` | Dev and Docker stdout collection. `head_hash` returns empty; `iter_events` raises `NotImplementedError` (write-only). |
| `BufferedSink` | `tessera/audit/sinks/buffered.py` | Optional wrapper; useful when adding a remote sink. |

### SQLite schema

```sql
CREATE TABLE IF NOT EXISTS audit_events (
  event_id        TEXT    PRIMARY KEY,
  scope           TEXT    NOT NULL,
  seq             INTEGER NOT NULL,
  event_type      TEXT    NOT NULL,
  occurred_at     TEXT    NOT NULL,
  payload_json    TEXT    NOT NULL,
  prev_event_hash TEXT    NOT NULL,
  event_hash      TEXT    NOT NULL UNIQUE,
  schema_version  TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_scope_seq
    ON audit_events (scope, seq);
CREATE INDEX IF NOT EXISTS idx_audit_scope_occurred_at
    ON audit_events (scope, occurred_at);
```

PRAGMAs on connection open: `journal_mode=WAL`, `synchronous=NORMAL`,
`foreign_keys=ON`. No schema migrations in v0.1.

### Audit verifier (`tessera/audit/verifier.py`)

`verify_chain(sink, scope)` walks `sink.iter_events(scope)` in order. For each
event it:

1. Recomputes the hash with `HashChain.verify_event_hash(event)`. Failure →
   `kind: hash_mismatch`.
2. Checks `HashChain.verify_pair(prev, event)`. Failure → `kind: chain_break`.

Returns a JSON-serializable dict:

```json
{
  "scope": "default",
  "events_checked": 12345,
  "first_event_at": "2026-05-01T00:00:00.000000Z",
  "last_event_at":  "2026-05-10T18:00:00.000000Z",
  "ok": true,
  "first_failure": null
}
```

On failure, `first_failure` is populated with `seq`, `event_id`, `kind`,
`expected_event_hash`, and `computed_event_hash`. The CLI exits 0 on success,
2 on config error, 3 on integrity failure.

### Audit event schema

Every emitted event has this top-level structure:

```jsonc
{
  "schemaVersion": "v0.1",
  "eventId":       "evt_<url-safe chars>",
  "tenantId":      "<AuthContext.scope>",
  "eventType":     "decision | passthrough | startup | reload | regex_timeout | audit_self_check",
  "occurredAt":    "<ISO 8601 UTC microsecond>",
  "prevEventHash": "<sha256 hex | empty for first event in scope>",
  "eventHash":     "<sha256 hex>",
  "payload": {
    "mode":           "enforcement | log_only | observation",
    "decision":       "allow | block | log_only | require_approval",  // enforcement only
    "would_decision": "allow | block | log_only | require_approval",  // log_only mode only
    "policy_id":      "<id | null>",
    "reason":         "<interpolated reason string | null>",
    "upstream":       "<name>",
    "tool_call": {
      "name":      "<tool name>",
      "arguments": { ... },           // stored verbatim; no redaction in v0.1
      "_meta":     { ... }
    },
    "principal_id":   "<from AuthContext>",
    "request_id":     "<uuid>",
    "decision_error": "regex_timeout | policy_error | null"
  }
}
```

`decision` and `would_decision` are mutually exclusive. `observation` events
carry neither. Full JSON Schema at `schemas/audit_event.schema.json`.

---

## 4. Pluggable Protocols

Tessera ships three extension points as Python `Protocol` classes. A commercial
or on-premise deployment replaces any of these by pointing an environment
variable at a `module:Class` string (see Section 6 for the pattern).

### PolicyLoader

```python
class PolicyLoader(Protocol):
    def load_all(self, scope: str) -> list[Policy]: ...
    def watch(self, scope: str, callback: Callable[[list[Policy]], None]) -> None: ...
```

**Default implementation:** `tessera.policy.loader:FilesystemPolicyLoader`.

`load_all(scope)` reads all non-`_*` `*.yaml` files from the configured
`policies.dir`. Each file is validated with the Pydantic `Policy` schema and
the ReDoS corpus check. At startup, any validation failure causes Tessera to
exit 2. At reload (file-watch or SIGHUP), a failed file is skipped: the
previously-loaded version is retained and `loader.state()["errored"]` records
the failure for `/healthz` to expose.

`watch(scope, callback)` registers a callback invoked whenever the policy set
changes (via `watchdog` or SIGHUP). The proxy uses this to swap in a new
`PolicyEngine` atomically.

### AuditSink

```python
class AuditSink(Protocol):
    name: str
    def emit(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...
    def head_hash(self, scope: str) -> str: ...
    def iter_events(self, scope: str | None = None) -> Iterator[dict]: ...
```

**Default implementation:** `tessera.audit.sinks.sqlite:SqliteSink`.

An alternative sink (Postgres, S3, a managed logging service) implements these
five members and is selected via `TESSERA_AUDIT_SINK`. The `head_hash` and
`iter_events` contracts are essential: `head_hash` enables restart-survivable
chains; `iter_events` feeds the verifier.

### Authenticator

```python
class Authenticator(Protocol):
    def authenticate(self, request: Request) -> AuthContext: ...
```

`AuthContext` is a plain dataclass:

```python
@dataclass
class AuthContext:
    principal_id: str   # token name (or "anonymous" in dev mode)
    scope: str          # audit chain stream
    metadata: dict[str, Any]
```

**Default implementation:** `tessera.auth.bearer:BearerTokenAuthenticator`.

`authenticate` must either return an `AuthContext` or raise
`tessera.errors.UnauthorizedError`. The proxy converts `UnauthorizedError` to
HTTP 401 and does not call the engine.

The `scope` value returned in `AuthContext` is what `AuditEmitter` uses as the
`tenantId` for hash chain keying. Per-token scopes therefore produce
independent, isolated audit streams.

### Selection mechanism

`tessera/pluggable.py` resolves a `"module.path:ClassName"` string at startup:

```python
from tessera.pluggable import resolve

PolicyLoaderClass = resolve(
    os.environ.get("TESSERA_POLICY_LOADER", ""),
    default="tessera.policy.loader:FilesystemPolicyLoader",
)
```

`resolve()` calls `importlib.import_module(module_path)` then `getattr(module, class_name)`.
It raises `ConfigError` on any import or attribute failure, which causes the
proxy to exit 2 before accepting traffic.

---

## 5. Why no OPA in v0.1

Policy evaluation in v0.1 is pure Python. There is no OPA dependency, no Rego
files, no subprocess calls, and no WASM runtime.

Reasons:

**Image size.** The `python:3.12-slim`-based image targets approximately 150 MB.
Adding the OPA binary or a Python OPA binding raises the image to approximately
250 MB or more. For a sidecar or edge-deployed firewall, image size affects
cold-start latency and pull time.

**Cold-start latency.** OPA initialization (bundle load, Rego compilation)
adds measurable overhead before the first request can be handled. Pure-Python
evaluation with pre-compiled Pydantic models and sorted policy lists starts
immediately.

**Single-language stack.** Keeping everything in Python means contributors
need one language to work on the entire codebase. Rego is a specialized query
language with its own learning curve.

**Sufficiency.** The v0.1 condition catalog (16 condition types including regex
matching, intent checks, time windows, and argument comparisons) covers the
wedge of real-world MCP firewall use cases. OPA adds expressive power that
v0.1 policies do not need.

**Non-breaking path to Rego.** The `PolicyLoader` Protocol means a Rego-backed
implementation can be added in v0.2 without changing the proxy, engine, or
audit subsystem. No existing policy file format is invalidated. The Rego escape
hatch is deferred, not foreclosed.

---

## 6. Tessera Cloud — FROM-base relationship

The OSS image (`ghcr.io/cloudmorph-ai/tessera:<version>`) is the base for
Tessera Cloud, a private commercial wrapper. The Cloud distribution does not
modify the proxy, engine, or audit chain. It replaces the three Protocols with
implementations suited to a managed multi-tenant environment:

```dockerfile
FROM ghcr.io/cloudmorph-ai/tessera:0.1.0
COPY tessera_cloud /opt/tessera_cloud
RUN pip install --no-cache-dir /opt/tessera_cloud

ENV TESSERA_POLICY_LOADER=tessera_cloud.policies:DynamoDBPolicyLoader
ENV TESSERA_AUDIT_SINK=tessera_cloud.audit:DynamoDBAuditSink
ENV TESSERA_AUTHENTICATOR=tessera_cloud.auth:CognitoJWTAuthenticator

CMD ["tessera-cloud", "serve"]
```

The Cloud layer follows the same `module:Class` selection mechanism as any
user-supplied implementation. The `PolicyLoader`, `AuditSink`, and
`Authenticator` Protocol signatures are frozen in the OSS codebase; the Cloud
wrapper depends on these signatures directly.

This means:

- Any bug fix or feature added to the OSS proxy, engine, or audit chain is
  automatically available to Cloud by bumping the `FROM` tag.
- The Protocol surface cannot be changed in the OSS repo without a coordinated
  update to the Cloud implementations. Changes to Protocol signatures are
  therefore breaking changes and require a minor version bump.
- The `AuthContext` dataclass shape (`principal_id`, `scope`, `metadata`) is
  also part of this frozen surface. Cloud's `CognitoJWTAuthenticator` returns
  the same `AuthContext` type; it does not extend it.

---

## 7. Local dev setup

### Prerequisites

- Python 3.12+
- Git

### Install

```bash
git clone https://github.com/cloudmorph-ai/cloudmorph-tessera.git
cd cloudmorph-tessera
pip install -e ".[dev]"
```

The `dev` extras include `pytest`, `hypothesis`, `ruff`, `mypy`, and
`pre-commit`. The `runtime` extras (`fastapi`, `uvicorn`, `httpx`, `pydantic`,
`typer`, `regex`, `watchdog`, `pyyaml`) are installed as regular dependencies.

### Pre-commit hooks

```bash
pre-commit install
```

This installs ruff, mypy, and other hooks that run on every commit. To run
them manually against the entire tree:

```bash
pre-commit run --all-files
```

### Tests

```bash
pytest
```

The test suite is split across three directories:

```
tests/unit/        Per-module unit tests.
tests/integration/ Multi-module flows; proxy round-trips; audit persistence.
tests/property/    Hypothesis property tests for the hash chain.
```

Coverage target is 80% overall; `tessera/audit/chain.py` and
`tessera/audit/canonical_json.py` target 100%.

To run with coverage:

```bash
pytest --cov=tessera --cov-report=term-missing
```

### Linting and type-checking

```bash
ruff check tessera tests
ruff format --check tessera tests
mypy --strict tessera
```

### Policy development

To validate policies without running the full server:

```bash
tessera policy lint --policy-dir policies/
tessera policy test --policy-dir policies/ --fixture-dir tests/fixtures/decisions/
```

### Running locally

Copy the example config, edit it to point at a real or mock upstream, then:

```bash
cp tessera.example.yaml tessera.yaml
cp tokens.example.yaml tokens.yaml
TESSERA_BEARER_TOKENS_FILE=tokens.yaml tessera serve --config tessera.yaml
```

The server starts on `http://0.0.0.0:8080` by default. The `/healthz` endpoint
is unauthenticated and shows the current policy state:

```bash
curl http://localhost:8080/healthz
```

For a containerized local run, see `docker-compose.example.yaml`.

### Scaffolding a new deployment

```bash
tessera init --dir /path/to/new-deployment
```

This creates `tessera.yaml` (with `policies.mode: log_only`), an empty
`policies/` directory, and `.env.example`. The `log_only` default means
policies are evaluated but all traffic passes through — safe for initial
deployment while you tune your policy set.
