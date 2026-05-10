# Tessera v0.1 — Configuration Reference

This document is the authoritative reference for all Tessera configuration knobs: environment
variables, `tessera.yaml` fields, enforcement modes, token setup, the lockdown kill switch, and
the metrics endpoint. Read this alongside `tessera.example.yaml`, which shows every field with
inline annotations.

---

## Contents

1. [Environment variables](#1-environment-variables)
2. [tessera.yaml field reference](#2-tesserayaml-field-reference)
3. [Three enforcement modes](#3-three-enforcement-modes)
4. [Multi-token setup](#4-multi-token-setup)
5. [Lockdown kill switch](#5-lockdown-kill-switch)
6. [Metrics endpoint](#6-metrics-endpoint)
7. [Precedence rules](#7-precedence-rules)

---

## 1. Environment variables

All variables are prefixed `TESSERA_*`. Values are coerced from strings at startup; an invalid
coercion terminates the process with exit code 2 and logs the offending variable.

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `TESSERA_CONFIG_PATH` | path | `/etc/tessera/tessera.yaml` | no | Path to the runtime config file. Overrides the built-in default location. |
| `TESSERA_POLICY_DIR` | path | (from config file) | no | Overrides `policies.dir` in `tessera.yaml`. Useful when the config file is read-only and you want to point at a different policy directory without editing it. |
| `TESSERA_AUDIT_PATH` | path | (from config file) | no | Overrides `audit.path` for the SQLite sink. Useful for injecting a volume-mounted path in Docker without touching the config file. |
| `TESSERA_BEARER_TOKENS` | string | — | no* | Inline multi-token list. Format: `name1:tk_xxx,name2:tk_yyy`. Each entry is a `name:token` pair; scope defaults to the name. |
| `TESSERA_BEARER_TOKENS_FILE` | path | — | no* | Path to a YAML token file. See [Multi-token setup](#4-multi-token-setup) for the file format. |
| `TESSERA_BEARER_TOKEN` | string | — | no* | Legacy single-token compatibility shim. Internally translated to a token list containing one entry: `[{name: "default", token: <value>, scope: "default"}]`. Prefer `TESSERA_BEARER_TOKENS` or `TESSERA_BEARER_TOKENS_FILE` for new deployments. |
| `TESSERA_METRICS_TOKEN` | string | — | no | Dedicated read-only token for the `/metrics` endpoint. When set, only this token grants metrics access. When unset and metrics are enabled, any token from the main token list grants access. Has no effect when `metrics.enabled: false`. |
| `TESSERA_LOG_LEVEL` | string | `INFO` | no | Logging verbosity. Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Case-insensitive. |
| `TESSERA_DEPLOYMENT_ID` | string | `default` | no | Default audit-event scope used in dev mode (when no token authentication is configured). Audit chain streams are keyed by this value when `AuthContext.scope` is not set from a token. |
| `TESSERA_BIND_HOST` | string | `0.0.0.0` | no | Network interface to bind. Set to `127.0.0.1` to restrict to localhost. Overrides `listen.host`. |
| `TESSERA_BIND_PORT` | int | `8080` | no | TCP port to listen on. Overrides `listen.port`. |
| `TESSERA_POLICY_LOADER` | string | `tessera.policy.loader:FilesystemPolicyLoader` | no | Pluggable policy loader. Accepts `module.path:ClassName`. The class must implement the `PolicyLoader` protocol. |
| `TESSERA_AUDIT_SINK` | string | `tessera.audit.sinks.sqlite:SqliteSink` | no | Pluggable audit sink. Accepts `module.path:ClassName`. The class must implement the `AuditSink` protocol. |
| `TESSERA_AUTHENTICATOR` | string | `tessera.auth.bearer:BearerTokenAuthenticator` | no | Pluggable authenticator. Accepts `module.path:ClassName`. The class must implement the `Authenticator` protocol. |

**\* Token source.** None of the three token variables is strictly required, but omitting all of
them enables dev mode: requests pass without authentication, `principal_id` becomes `"anonymous"`,
and `scope` becomes the value of `TESSERA_DEPLOYMENT_ID`. Tessera logs a `WARNING` at level
`auth_disabled` once at startup and once every 60 seconds while running in this state. Dev mode is
appropriate for local evaluation only — never expose Tessera to untrusted traffic without
configuring tokens.

---

## 2. tessera.yaml field reference

Copy `tessera.example.yaml` to `tessera.yaml` as your starting point. The full schema is at
`schemas/config.schema.json`.

### `listen`

Controls the network binding for the proxy HTTP server.

```yaml
listen:
  host: 0.0.0.0   # string — bind interface; override with TESSERA_BIND_HOST
  port: 8080       # int    — bind port;      override with TESSERA_BIND_PORT
```

| Field | Type | Default | Description |
|---|---|---|---|
| `listen.host` | string | `0.0.0.0` | Network interface. Set `127.0.0.1` to restrict to loopback. Overridden by `TESSERA_BIND_HOST`. |
| `listen.port` | int | `8080` | TCP port. Overridden by `TESSERA_BIND_PORT`. |

### `auth`

Selects the authentication strategy. In v0.1 only `bearer` is built in; custom authenticators are
registered via `TESSERA_AUTHENTICATOR`.

```yaml
auth:
  type: bearer   # string — only value supported in v0.1
```

| Field | Type | Default | Description |
|---|---|---|---|
| `auth.type` | string | `bearer` | Authentication type. `bearer` activates `BearerTokenAuthenticator`. |

### `audit`

Configures where audit events are persisted and whether they are also mirrored to stdout.

```yaml
audit:
  sink: sqlite                        # string — sqlite (default) | stdout
  path: /var/lib/tessera/audit.db     # path   — SQLite database file; override with TESSERA_AUDIT_PATH
  also_stdout: false                  # bool   — mirror events to stdout in addition to primary sink
```

| Field | Type | Default | Description |
|---|---|---|---|
| `audit.sink` | string | `sqlite` | Primary sink. Built-in values: `sqlite`, `stdout`. Custom sinks via `TESSERA_AUDIT_SINK`. |
| `audit.path` | path | `/var/lib/tessera/audit.db` | Path for the SQLite database file. Created on first write. Directory must exist and be writable. Overridden by `TESSERA_AUDIT_PATH`. |
| `audit.also_stdout` | bool | `false` | When `true`, all events are mirrored to stdout in addition to the primary sink. Useful in Docker environments where stdout is collected by a log aggregator. |

### `policies`

Controls where policies are loaded from, how often they are reloaded, and which enforcement mode
governs their effect on traffic.

```yaml
policies:
  dir: /etc/tessera/policies   # path   — policy YAML directory; override with TESSERA_POLICY_DIR
  reload: watch                # string — watch | sighup | none
  mode: log_only               # string — enforcement | log_only | observation
  default_action: block        # string — action when no policy matches (enforcement only)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `policies.dir` | path | `/etc/tessera/policies` | Directory containing policy YAML files. Non-`_`-prefixed `.yaml` files are treated as policies. `_action_verbs.yaml` and other `_`-prefixed files are config overlays. Overridden by `TESSERA_POLICY_DIR`. |
| `policies.reload` | string | `watch` | Reload trigger. `watch` — filesystem watcher fires per-file reload on change; `sighup` — reload only on SIGHUP signal; `none` — no reload; restart required. |
| `policies.mode` | string | `log_only` | Enforcement mode. See [Three enforcement modes](#3-three-enforcement-modes) for full semantics. `tessera init` scaffolds with `log_only`. |
| `policies.default_action` | string | `block` | Action applied when no policy matches a `tools/call` in `enforcement` mode. Has no effect in `log_only` or `observation` mode. |

### `intent`

Controls whether and how agent intent declarations (passed via MCP `_meta`) are extracted and
enforced.

```yaml
intent:
  meta_key: tessera_intent   # string — key inside _meta to read intent from
  required: false            # bool   — true = block all calls without intent
```

| Field | Type | Default | Description |
|---|---|---|---|
| `intent.meta_key` | string | `tessera_intent` | The key within the MCP `_meta` object that carries the intent block. Agents set `params._meta.tessera_intent = {verbs: [...], purpose: "..."}`. |
| `intent.required` | bool | `false` | When `true`, any `tools/call` that does not include a valid intent block is blocked unconditionally, before policy evaluation, with reason `intent_required`. This is strict mode for organizations standardizing on intent-aware agents. Off-the-shelf clients (Cursor, Claude Desktop, Windsurf) do not populate intent; leave this `false` when supporting them. |

### `metrics`

Controls the optional Prometheus metrics endpoint. The endpoint is not mounted at all when
`enabled: false`.

```yaml
metrics:
  enabled: false                         # bool   — mount /metrics; default OFF
  bearer_token_env: TESSERA_METRICS_TOKEN  # string — env var name holding the dedicated metrics token
```

| Field | Type | Default | Description |
|---|---|---|---|
| `metrics.enabled` | bool | `false` | When `false` (default), `/metrics` is not mounted and returns 404. When `true`, the endpoint is mounted and requires bearer authentication. |
| `metrics.bearer_token_env` | string | `TESSERA_METRICS_TOKEN` | Name of the environment variable that holds the dedicated read-only metrics token. If that variable is unset, any token from the main token list grants metrics access. See [Metrics endpoint](#6-metrics-endpoint). |

### `deployment_id`

```yaml
deployment_id: default   # string — audit scope used in dev mode
```

| Field | Type | Default | Description |
|---|---|---|---|
| `deployment_id` | string | `default` | The audit chain scope (stream identifier) used when no token is configured and `AuthContext.scope` cannot be derived from a token. In production with tokens configured, scope comes from the per-token `scope` field instead. Overridden by `TESSERA_DEPLOYMENT_ID`. |

### `upstreams`

Required. One or more upstream MCP servers that Tessera proxies to. Each upstream gets its own
route at `POST /mcp/{name}`.

```yaml
upstreams:
  - name: aws                             # string — route key; alphanumeric + hyphens
    url: https://mcp.aws.example.com      # string — base URL of the upstream MCP server
    timeout_seconds: 30                   # int    — per-request timeout in seconds
    credentials:                          # optional — inject a header on every upstream request
      header: Authorization               # string — HTTP header name
      value: "Bearer ${AWS_MCP_TOKEN}"    # string — header value; ${VAR} resolved from environment
```

| Field | Type | Required | Description |
|---|---|---|---|
| `upstreams[].name` | string | yes | Route identifier. Used in the URL path `/mcp/{name}`. Alphanumeric and hyphens. Must be unique. |
| `upstreams[].url` | string | yes | Base URL of the upstream MCP server. No trailing slash. |
| `upstreams[].timeout_seconds` | int | no (default `30`) | Per-request upstream timeout in seconds. Tessera returns JSON-RPC code `-32000` on timeout. |
| `upstreams[].credentials.header` | string | no | HTTP header name to inject on every request forwarded to this upstream (e.g., `Authorization`, `X-Api-Key`). |
| `upstreams[].credentials.value` | string | no | Header value. Supports `${VAR}` interpolation — the placeholder is replaced with the value of the named environment variable at startup. If the variable is unset, startup fails with exit 2. |

`upstreams` comes only from the config file; it cannot be set via environment variables.

### `runtime`

Kill-switch controls evaluated before policy logic on every request.

```yaml
runtime:
  lockdown: false   # bool — block ALL traffic immediately when true
```

| Field | Type | Default | Description |
|---|---|---|---|
| `runtime.lockdown` | bool | `false` | When `true`, every `tools/call` request is rejected with JSON-RPC error `-32603` and reason `lockdown_active` before any policy evaluation. Pass-through methods (`tools/list`, `initialize`, etc.) are also blocked. See [Lockdown kill switch](#5-lockdown-kill-switch). |

---

## 3. Three enforcement modes

`policies.mode` governs how the policy engine's decision is acted upon. Set it deployment-wide in
`tessera.yaml`. Individual policies are mode-agnostic and work unchanged in all three modes.

### Mode summary

| Mode | Engine runs? | Upstream called? | Response to client | Audit fields |
|---|---|---|---|---|
| `enforcement` | yes | only if decision is `allow`, `log_only`, or `require_approval`; not on `block` | block → JSON-RPC error `-32603`; allow → upstream response | `decision: <action>` |
| `log_only` | yes | always (regardless of decision) | upstream response plus `X-Tessera-*` headers indicating what would have happened | `would_decision: <action>`, `mode: log_only` |
| `observation` | no | always | upstream response, unmodified | `mode: observation` (no decision field) |

### Mode semantics

**`enforcement`** — The engine's decision is honored. A `block` decision returns a JSON-RPC error
to the calling agent and the upstream is never contacted. An `allow` or `log_only` policy action
forwards the call. `require_approval` returns JSON-RPC code `-32604` with reason
`approval_required: <reason>`. When no policy matches, `policies.default_action` applies.

**`log_only`** — The engine runs and produces a decision, but that decision never stops traffic.
The upstream is always called and its response is always returned. Tessera injects extra HTTP
response headers so you can observe what would have happened:

- `X-Tessera-Mode: log_only` — present on every response in this mode
- `X-Tessera-Decision: would_block | would_allow | no_match` — the shadow decision
- `X-Tessera-Policy-Id: <id>` — only present when `would_block`
- `X-Tessera-Reason: <reason>` — only present when `would_block`

Audit events record `would_decision` rather than `decision`.

**`observation`** — The engine is skipped entirely. No policy evaluation takes place. Every request
is forwarded to the upstream unconditionally. Audit events record only `mode: observation` with no
decision field. Use this for pure traffic recording when you are not yet ready to author policies.

### Lockdown interaction

`runtime.lockdown: true` is checked before the mode branch. Lockdown blocks all traffic regardless
of which mode is active. This is intentional — lockdown is an emergency brake, not a policy.

### Worked transition: log\_only to enforcement

This is the recommended deployment pattern. `tessera init` scaffolds new deployments in `log_only`
by default.

**Step 1 — Deploy in `log_only`**

Start Tessera with `policies.mode: log_only`. All traffic flows through to upstreams. Audit events
accumulate, recording `would_decision` for every `tools/call`. Response headers tell you in
real-time which calls would have been blocked.

```yaml
# tessera.yaml
policies:
  dir: /etc/tessera/policies
  mode: log_only
  default_action: block
```

**Step 2 — Review the audit trail**

Run `tessera audit verify` to confirm the hash chain is intact, then review the audit database
for `would_decision: would_block` events. Look for unexpected blocks (policies too broad) or
expected blocks that are not firing (policies missing).

```bash
tessera audit verify --audit-path /var/lib/tessera/audit.db --json
```

Query the SQLite database directly to count shadow decisions by policy:

```sql
SELECT
  json_extract(payload_json, '$.policy_id') AS policy_id,
  json_extract(payload_json, '$.would_decision') AS would_decision,
  count(*) AS n
FROM audit_events
WHERE json_extract(payload_json, '$.mode') = 'log_only'
GROUP BY 1, 2
ORDER BY n DESC;
```

**Step 3 — Tune policies**

Edit policy YAML files in `policies.dir`. If `policies.reload: watch` is set (the default),
changes take effect within seconds without a restart. Re-examine the audit trail after each
adjustment until `would_block` matches your intent.

Lint your policies before flipping to enforcement:

```bash
tessera policy lint --policy-dir /etc/tessera/policies
```

**Step 4 — Flip to `enforcement`**

Edit `tessera.yaml`:

```yaml
policies:
  mode: enforcement
```

`policies.mode` is not a SIGHUP-reloadable field (see [File-watch and reload behavior](#file-watch-and-reload-behavior)). Restart Tessera to apply the change:

```bash
# Docker
docker restart tessera

# systemd
systemctl restart tessera
```

From this point forward, `block` decisions stop the upstream call and return a JSON-RPC error to
the agent. Monitor `X-Tessera-Decision` headers and audit events for the first few minutes after
flipping to confirm behavior matches expectations from the `log_only` run.

---

## 4. Multi-token setup

Tessera supports multiple simultaneous bearer tokens. Each token carries a name and an optional
scope. The scope becomes the `AuthContext.scope` that keys the audit chain stream — different
scopes produce isolated, independently-verifiable audit streams.

### Inline tokens (`TESSERA_BEARER_TOKENS`)

Provide a comma-separated list of `name:token` pairs as a single environment variable. Scope
defaults to the token name.

```bash
TESSERA_BEARER_TOKENS="alice:tk_abc123,bob:tk_def456,ci:tk_ghi789"
```

This is convenient for Docker deployments where secrets are injected as environment variables.

### File-based tokens (`TESSERA_BEARER_TOKENS_FILE`)

Point Tessera at a YAML file containing the token list. This is the recommended approach for
production because the file can be mounted as a read-only secret volume and updated without
changing environment variables.

```yaml
# /etc/tessera/tokens.yaml
tokens:
  - name: alice
    token: tk_abc123
    scope: alice           # optional — defaults to name
  - name: bob
    token: tk_def456
    # scope omitted — defaults to "bob"
  - name: ci
    token: tk_ghi789
    scope: ci-shared       # multiple tokens can share a scope; their audit events go in one stream
```

Token file validation rules:

- `name` — pattern `[a-z0-9_-]{1,64}`, must be unique within the list.
- `token` — opaque string, minimum 16 characters, no whitespace.
- `scope` — pattern `[a-z0-9_-]{1,64}`, defaults to the token `name`.

Tessera validates the file at startup. Any validation failure exits with code 2.

### Legacy single token (`TESSERA_BEARER_TOKEN`)

For backward compatibility with configurations that predate multi-token support. Tessera translates
this internally to a single-entry token list:

```
{name: "default", token: <value>, scope: "default"}
```

This means all requests authenticated with the legacy token appear in the `"default"` audit stream.
Existing scripts and configurations that set `TESSERA_BEARER_TOKEN` continue to work without
modification.

### Token source precedence

When multiple token sources are configured, the first match in the following order wins:

1. `TESSERA_BEARER_TOKENS` (inline env var)
2. `TESSERA_BEARER_TOKENS_FILE` (YAML file)
3. `TESSERA_BEARER_TOKEN` (legacy single token)
4. None — dev mode (auth disabled, warning logged)

Sources are mutually exclusive at the list-building level. If `TESSERA_BEARER_TOKENS` is set, the
other two sources are ignored. Set only one source per deployment to avoid confusion.

### Per-token scope and audit isolation

Every token's `scope` value is used to key the audit hash chain. Two tokens with different scopes
produce two separate, independently-verifiable chains in the SQLite database. This means:

- You can run `tessera audit verify --scope alice` to verify only Alice's audit stream.
- A chain tampering event in one scope does not affect verification of other scopes.
- Multiple tokens sharing a scope (e.g., `ci-shared`) contribute to a single audit stream.

### Authentication behavior

`BearerTokenAuthenticator` validates requests using constant-time comparison
(`secrets.compare_digest`) to prevent timing-based token enumeration. Every token in the list is
checked against the incoming `Authorization: Bearer <token>` header. The first match populates
`AuthContext`. No match returns HTTP 401.

---

## 5. Lockdown kill switch

`runtime.lockdown: true` is an emergency brake that blocks all proxied traffic immediately. It
applies before any policy evaluation or mode logic.

### What lockdown does

When `runtime.lockdown: true`:

- Every `tools/call` request returns JSON-RPC error `-32603` with reason `lockdown_active`.
- Pass-through methods (`tools/list`, `initialize`, `ping`, etc.) are also blocked.
- Audit events are still emitted (so the lockdown period is recorded in the chain).
- Policy reload continues to work during lockdown. When lockdown is lifted, the latest policies
  immediately become active.
- The `/healthz`, `/readyz`, and `/metrics` endpoints remain accessible.

### Activating lockdown

Edit `tessera.yaml`:

```yaml
runtime:
  lockdown: true
```

Then send SIGHUP to the Tessera process. `runtime.lockdown` is the only `tessera.yaml` field that
is re-read on SIGHUP — all other fields require a restart.

```bash
# Find the PID
kill -HUP $(pgrep -f "tessera serve")

# Docker
docker kill --signal=SIGHUP tessera

# systemd
systemctl kill --signal=SIGHUP tessera
```

Tessera logs: `level=INFO event=lockdown_activated`.

### Lifting lockdown

Edit `tessera.yaml` back to `runtime.lockdown: false` and send SIGHUP again:

```bash
kill -HUP $(pgrep -f "tessera serve")
```

Tessera logs: `level=INFO event=lockdown_deactivated`. Normal request flow resumes immediately.

### File-watch and reload behavior

The following table summarizes which signals and file events reload which parts of configuration:

| Event | Effect |
|---|---|
| File change in `policies.dir` (when `policies.reload: watch`) | Per-file policy reload with error isolation. Failed files keep their previous version; other files are updated. |
| SIGHUP | Reload all policies (per-file isolation) AND re-read `runtime.lockdown` from `tessera.yaml`. No other config fields are re-read. |
| SIGTERM / SIGINT | Graceful shutdown: drain in-flight requests, flush and close audit sinks, exit 0. |
| Edit to any other `tessera.yaml` field | Not auto-reloaded. Restart required. |

---

## 6. Metrics endpoint

Tessera exposes a Prometheus-compatible metrics endpoint at `/metrics`. It is disabled by default
and must be explicitly enabled.

### Enabling metrics

```yaml
metrics:
  enabled: true
```

When `enabled: false` (the default), `/metrics` is not mounted. Requests to `/metrics` return 404.

After enabling metrics, restart Tessera (metrics enablement is not SIGHUP-reloadable).

### Metrics authentication

The `/metrics` endpoint requires bearer authentication regardless of how it is enabled. Auth rules:

1. If `TESSERA_METRICS_TOKEN` is set — only that token grants access to `/metrics`. Main-list
   tokens are rejected at the metrics endpoint (they still work for `/mcp/{upstream}`).
2. If `TESSERA_METRICS_TOKEN` is not set — any token from the main token list (from whichever
   source was active) grants access to `/metrics`.

The `metrics.bearer_token_env` config field names the environment variable that Tessera reads for
the dedicated token. It defaults to `TESSERA_METRICS_TOKEN`. You can point it at a different env
var name if your secret management system requires a specific naming convention:

```yaml
metrics:
  enabled: true
  bearer_token_env: MY_CUSTOM_METRICS_SECRET
```

Then set `MY_CUSTOM_METRICS_SECRET=tk_readonly_xyz` in your environment.

### Available metrics

When enabled, Tessera exposes the following Prometheus metrics:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `requests_total` | counter | `outcome` | Total requests processed, labeled by outcome. |
| `decisions_total` | counter | `action`, `policy_id`, `mode` | Policy decisions emitted, labeled by action, matched policy, and mode. |
| `audit_emit_failures_total` | counter | — | Number of audit events that failed to persist. Non-zero values warrant investigation. |
| `upstream_request_duration_seconds` | histogram | — | Latency of upstream MCP server calls. |
| `regex_timeout_total` | counter | `policy_id` | Number of regex condition timeouts per policy. Sustained non-zero values indicate a ReDoS-prone pattern that passed the load-time corpus check. |

### Example: scrape config for Prometheus

```yaml
scrape_configs:
  - job_name: tessera
    static_configs:
      - targets: ["tessera:8080"]
    authorization:
      credentials: <value of TESSERA_METRICS_TOKEN>
```

---

## 7. Precedence rules

When the same configuration knob can be set in multiple places, the following rules apply.

### Environment variables vs config file

Environment variables always override the corresponding config file field. The mapping is:

| Env var | Overrides config field |
|---|---|
| `TESSERA_BIND_HOST` | `listen.host` |
| `TESSERA_BIND_PORT` | `listen.port` |
| `TESSERA_POLICY_DIR` | `policies.dir` |
| `TESSERA_AUDIT_PATH` | `audit.path` |
| `TESSERA_DEPLOYMENT_ID` | `deployment_id` |
| `TESSERA_LOG_LEVEL` | (no config-file equivalent) |
| `TESSERA_POLICY_LOADER` | (no config-file equivalent; selects plugin class) |
| `TESSERA_AUDIT_SINK` | (no config-file equivalent; selects plugin class) |
| `TESSERA_AUTHENTICATOR` | (no config-file equivalent; selects plugin class) |

Fields with no env-var override — `upstreams`, `auth.type`, `intent.*`, `policies.reload`,
`policies.mode`, `policies.default_action`, `audit.sink`, `audit.also_stdout`, `metrics.*`,
`runtime.lockdown` — are read only from `tessera.yaml` and require a restart when changed (with
the sole exception of `runtime.lockdown`, which is re-read on SIGHUP).

### Token source precedence

Among the three token-loading mechanisms, the first non-empty source wins:

1. `TESSERA_BEARER_TOKENS` (inline, comma-separated)
2. `TESSERA_BEARER_TOKENS_FILE` (YAML file)
3. `TESSERA_BEARER_TOKEN` (legacy single token)
4. No source configured — dev mode (auth disabled)

Only one source is active at a time. If `TESSERA_BEARER_TOKENS` is set (even to a value that
produces an empty list after parsing), the file and legacy sources are not consulted.

### Metrics token precedence

When `metrics.enabled: true`:

1. If the env var named by `metrics.bearer_token_env` (default: `TESSERA_METRICS_TOKEN`) is set
   and non-empty — only that value is accepted as a valid metrics token.
2. If that env var is unset or empty — any token from the main token list is accepted.
3. If no token matches — HTTP 401.

The metrics token is checked only at the `/metrics` endpoint. It does not grant access to
`/mcp/{upstream}`.

### Upstream credentials

Upstream `credentials.value` supports `${VAR}` placeholders. These are resolved from the process
environment at startup. If a referenced variable is unset, Tessera exits with code 2. There is no
fallback and no runtime re-resolution.

### Policy priority within a directory

When multiple policies could match a single `tools/call`, Tessera evaluates them in descending
`priority` order (highest number first). Ties are broken alphabetically by `id`. The first policy
whose `match` and `when` clauses both pass produces the decision; evaluation stops there
(first-match-wins). No-match falls through to `policies.default_action` in enforcement mode.

---

*For policy authoring, see `docs/POLICIES.md`. For audit chain details, see `docs/AUDIT.md`.*
