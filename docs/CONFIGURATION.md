# Tessera v0.1 — Configuration Reference

Authoritative reference for all Tessera configuration: environment variables, `tessera.yaml` fields,
enforcement modes, token setup, lockdown, and metrics. Start from `tessera.example.yaml`; full
schema at `schemas/config.schema.json`.

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

All variables are prefixed `TESSERA_*`. Invalid coercions exit with code 2.

| Variable | Type | Default | Required | Purpose |
|---|---|---|---|---|
| `TESSERA_CONFIG_PATH` | path | `/etc/tessera/tessera.yaml` | no | Runtime config file path. |
| `TESSERA_POLICY_DIR` | path | (from config) | no | Overrides `policies.dir`. |
| `TESSERA_AUDIT_PATH` | path | (from config) | no | Overrides `audit.path` (SQLite sink). |
| `TESSERA_BEARER_TOKENS` | string | — | no* | Inline token list: `name1:tk_xxx,name2:tk_yyy`. Scope defaults to name. |
| `TESSERA_BEARER_TOKENS_FILE` | path | — | no* | Path to a YAML token file. See [Multi-token setup](#4-multi-token-setup). |
| `TESSERA_BEARER_TOKEN` | string | — | no* | Legacy single-token shim. Translated to `{name: "default", token: <v>, scope: "default"}`. |
| `TESSERA_METRICS_TOKEN` | string | — | no | Dedicated read-only token for `/metrics`. Unset → any main-list token grants access. No effect when `metrics.enabled: false`. |
| `TESSERA_LOG_LEVEL` | string | `INFO` | no | Verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Case-insensitive. |
| `TESSERA_DEPLOYMENT_ID` | string | `default` | no | Audit scope in dev mode (when no token is configured). Overrides `deployment_id`. |
| `TESSERA_BIND_HOST` | string | `0.0.0.0` | no | Bind interface. Overrides `listen.host`. |
| `TESSERA_BIND_PORT` | int | `8080` | no | TCP port. Overrides `listen.port`. |
| `TESSERA_POLICY_LOADER` | string | `tessera.policy.loader:FilesystemPolicyLoader` | no | Pluggable loader (`module.path:ClassName`). Must implement `PolicyLoader`. |
| `TESSERA_AUDIT_SINK` | string | `tessera.audit.sinks.sqlite:SqliteSink` | no | Pluggable audit sink (`module.path:ClassName`). Must implement `AuditSink`. |
| `TESSERA_AUTHENTICATOR` | string | `tessera.auth.bearer:BearerTokenAuthenticator` | no | Pluggable authenticator (`module.path:ClassName`). Must implement `Authenticator`. |

**\* Token source.** Omitting all three enables dev mode: auth disabled, `principal_id = "anonymous"`,
`scope = TESSERA_DEPLOYMENT_ID`. Tessera logs `WARNING auth_disabled` at startup and every 60 s.
Dev mode is for local evaluation only.

---

## 2. tessera.yaml field reference

### `listen`

| Field | Type | Default | Description |
|---|---|---|---|
| `listen.host` | string | `0.0.0.0` | Bind interface. Overridden by `TESSERA_BIND_HOST`. |
| `listen.port` | int | `8080` | TCP port. Overridden by `TESSERA_BIND_PORT`. |

### `auth`

| Field | Type | Default | Description |
|---|---|---|---|
| `auth.type` | string | `bearer` | Auth strategy. Only `bearer` is built in; custom via `TESSERA_AUTHENTICATOR`. |

### `audit`

| Field | Type | Default | Description |
|---|---|---|---|
| `audit.sink` | string | `sqlite` | Primary sink: `sqlite`, `stdout`, or custom via `TESSERA_AUDIT_SINK`. |
| `audit.path` | path | `/var/lib/tessera/audit.db` | SQLite file path. Directory must exist and be writable. Overridden by `TESSERA_AUDIT_PATH`. |
| `audit.also_stdout` | bool | `false` | Mirror all events to stdout in addition to the primary sink. |

### `policies`

| Field | Type | Default | Description |
|---|---|---|---|
| `policies.dir` | path | `/etc/tessera/policies` | Policy YAML directory. `_`-prefixed files are config overlays, not policies. Overridden by `TESSERA_POLICY_DIR`. |
| `policies.reload` | string | `watch` | Reload trigger: `watch` (fs watcher), `sighup`, or `none`. |
| `policies.mode` | string | `log_only` | Enforcement mode. See [Three enforcement modes](#3-three-enforcement-modes). |
| `policies.default_action` | string | `block` | Action when no policy matches in `enforcement` mode. No effect in `log_only` or `observation`. |

### `intent`

| Field | Type | Default | Description |
|---|---|---|---|
| `intent.meta_key` | string | `tessera_intent` | Key inside MCP `_meta` that carries the intent block (`{verbs: [...], purpose: "..."}`). |
| `intent.required` | bool | `false` | When `true`, calls without a valid intent block are blocked before policy evaluation (`intent_required`). Leave `false` for off-the-shelf clients (Cursor, Claude Desktop). |

### `metrics`

| Field | Type | Default | Description |
|---|---|---|---|
| `metrics.enabled` | bool | `false` | Mount `/metrics`. When `false`, endpoint returns 404. |
| `metrics.bearer_token_env` | string | `TESSERA_METRICS_TOKEN` | Env var name holding the dedicated metrics token. If unset, any main-list token grants access. |

### `deployment_id`

| Field | Type | Default | Description |
|---|---|---|---|
| `deployment_id` | string | `default` | Audit chain scope in dev mode. In production, scope comes from per-token `scope` field. Overridden by `TESSERA_DEPLOYMENT_ID`. |

### `upstreams`

Required. Each upstream gets a route at `POST /mcp/{name}`.

```yaml
upstreams:
  - name: aws                             # alphanumeric + hyphens; must be unique
    url: https://mcp.aws.example.com      # base URL; no trailing slash
    timeout_seconds: 30                   # default 30; returns -32000 on timeout
    credentials:
      header: Authorization               # HTTP header name to inject
      value: "Bearer ${AWS_MCP_TOKEN}"    # ${VAR} resolved from env at startup; unset → exit 2
```

| Field | Type | Required | Description |
|---|---|---|---|
| `upstreams[].name` | string | yes | Route key for `/mcp/{name}`. |
| `upstreams[].url` | string | yes | Base URL of the upstream MCP server. |
| `upstreams[].timeout_seconds` | int | no | Per-request timeout (s). Default `30`. |
| `upstreams[].credentials.header` | string | no | Header name to inject on every forwarded request. |
| `upstreams[].credentials.value` | string | no | Header value. Supports `${VAR}` env interpolation. |

`upstreams` is config-file only — no env var equivalent.

### `runtime`

| Field | Type | Default | Description |
|---|---|---|---|
| `runtime.lockdown` | bool | `false` | When `true`, all `tools/call` requests return `-32603 lockdown_active` before policy evaluation. Pass-through methods also blocked. Re-read on SIGHUP — the only field that is. |

---

## 3. Three enforcement modes

`policies.mode` governs how the policy engine's decision is acted upon. Individual policies are
mode-agnostic.

| Mode | Engine runs? | Upstream called? | Response to client | Audit field |
|---|---|---|---|---|
| `enforcement` | yes | only on `allow` / `log_only` / `require_approval` | block → `-32603`; allow → upstream response | `decision: <action>` |
| `log_only` | yes | always | upstream response + `X-Tessera-*` shadow headers | `would_decision: <action>`, `mode: log_only` |
| `observation` | no | always | upstream response, unmodified | `mode: observation` (no decision field) |

`log_only` injects these headers on every response:

| Header | Present when |
|---|---|
| `X-Tessera-Mode: log_only` | always in log_only |
| `X-Tessera-Decision: would_block\|would_allow\|no_match` | always in log_only |
| `X-Tessera-Policy-Id: <id>` | `would_block` only |
| `X-Tessera-Reason: <reason>` | `would_block` only |

**`runtime.lockdown: true`** is checked before the mode branch and blocks all traffic regardless of
mode. When ready to switch from `log_only` to `enforcement`, update `policies.mode` in
`tessera.yaml` and restart (mode is not SIGHUP-reloadable).

---

## 4. Multi-token setup

Each token carries a `name` and an optional `scope`. Scope keys the audit chain stream — different
scopes produce isolated, independently-verifiable audit chains.

### Inline (`TESSERA_BEARER_TOKENS`)

```bash
TESSERA_BEARER_TOKENS="alice:tk_abc123,bob:tk_def456,ci:tk_ghi789"
```

### File-based (`TESSERA_BEARER_TOKENS_FILE`)

```yaml
# /etc/tessera/tokens.yaml
tokens:
  - name: alice
    token: tk_abc123
    scope: alice           # optional; defaults to name
  - name: ci
    token: tk_ghi789
    scope: ci-shared       # multiple tokens can share a scope
```

Validation: `name` and `scope` match `[a-z0-9_-]{1,64}`; `token` ≥ 16 chars, no whitespace.
Validation failure → exit 2.

### Legacy single token (`TESSERA_BEARER_TOKEN`)

Translated internally to `{name: "default", token: <value>, scope: "default"}`. All requests in
the `"default"` audit stream. Backward compatible; prefer `TESSERA_BEARER_TOKENS` for new work.

---

## 5. Lockdown kill switch

`runtime.lockdown: true` blocks all proxied traffic before any policy evaluation. Audit events are
still emitted. `/healthz`, `/readyz`, and `/metrics` remain accessible.

```bash
# Activate: edit tessera.yaml → runtime.lockdown: true, then:
kill -HUP $(pgrep -f "tessera serve")   # bare process
docker kill --signal=SIGHUP tessera     # Docker
systemctl kill --signal=SIGHUP tessera  # systemd

# Deactivate: set runtime.lockdown: false, send SIGHUP again.
```

Tessera logs `event=lockdown_activated` / `event=lockdown_deactivated`.

### File-watch and reload behavior

| Event | Effect |
|---|---|
| File change in `policies.dir` (`policies.reload: watch`) | Per-file policy reload; failed files keep previous version. |
| SIGHUP | Reload all policies + re-read `runtime.lockdown` only. |
| SIGTERM / SIGINT | Graceful shutdown: drain in-flight, flush sinks, exit 0. |
| Any other `tessera.yaml` field edit | Restart required. |

---

## 6. Metrics endpoint

Disabled by default. Enable in `tessera.yaml`:

```yaml
metrics:
  enabled: true
```

Restart required (not SIGHUP-reloadable). When disabled, `/metrics` returns 404.

**Auth:** bearer token required. If `TESSERA_METRICS_TOKEN` is set, only that token is accepted at
`/metrics` (main-list tokens are rejected there). If unset, any main-list token is accepted.
To use a custom env var name: set `metrics.bearer_token_env: MY_METRICS_SECRET` and export
`MY_METRICS_SECRET=tk_readonly_xyz`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `requests_total` | counter | `outcome` | Total requests by outcome. |
| `decisions_total` | counter | `action`, `policy_id`, `mode` | Policy decisions by action, policy, and mode. |
| `audit_emit_failures_total` | counter | — | Audit events that failed to persist. Non-zero warrants investigation. |
| `upstream_request_duration_seconds` | histogram | — | Upstream call latency. |
| `regex_timeout_total` | counter | `policy_id` | Regex timeouts per policy (sustained non-zero → ReDoS risk). |

Prometheus scrape config:

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

### Environment variables vs config file

Env vars always win over the config file:

| Env var | Overrides |
|---|---|
| `TESSERA_BIND_HOST` | `listen.host` |
| `TESSERA_BIND_PORT` | `listen.port` |
| `TESSERA_POLICY_DIR` | `policies.dir` |
| `TESSERA_AUDIT_PATH` | `audit.path` |
| `TESSERA_DEPLOYMENT_ID` | `deployment_id` |
| `TESSERA_LOG_LEVEL` | (no config-file equivalent) |
| `TESSERA_POLICY_LOADER` | (plugin class; no config-file equivalent) |
| `TESSERA_AUDIT_SINK` | (plugin class; no config-file equivalent) |
| `TESSERA_AUTHENTICATOR` | (plugin class; no config-file equivalent) |

Fields with no env-var override (`upstreams`, `auth.type`, `intent.*`, `policies.reload`,
`policies.mode`, `policies.default_action`, `audit.sink`, `audit.also_stdout`, `metrics.*`,
`runtime.lockdown`) require a restart when changed — except `runtime.lockdown`, which is
re-read on SIGHUP.

### Token source precedence

1. `TESSERA_BEARER_TOKENS` (inline)
2. `TESSERA_BEARER_TOKENS_FILE` (YAML file)
3. `TESSERA_BEARER_TOKEN` (legacy)
4. None — dev mode

First non-empty source wins. If `TESSERA_BEARER_TOKENS` is set, the other sources are ignored.

### Metrics token precedence

1. Env var named by `metrics.bearer_token_env` set → only that value accepted at `/metrics`.
2. That env var unset → any main-list token accepted.
3. No match → HTTP 401.

The metrics token does not grant access to `/mcp/{upstream}`.

### Policy priority within a directory

Policies are evaluated in descending `priority` order (highest first). Ties broken alphabetically
by `id`. First match wins; no-match falls through to `policies.default_action` in enforcement mode.

---

*For policy authoring, see `docs/POLICIES.md`. For audit chain details, see `docs/AUDIT.md`.*
