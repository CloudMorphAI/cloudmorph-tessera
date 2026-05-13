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
8. [Pluggable backends](#8-pluggable-backends)

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
| `policies.reload` | string | `watch` | Reload trigger: `watch` (filesystem watcher) or `none`. (`sighup` was declared in v0.1.x but unimplemented; removed from supported values in v0.2.0.) |
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

---

## 8. Pluggable backends

Tessera's authenticator, audit sink, and policy loader are all pluggable via
environment variables. Each accepts a `module.path:ClassName` string resolved
at startup by `tessera/pluggable.py`.

| Env var | Default | Protocol |
|---|---|---|
| `TESSERA_AUTHENTICATOR` | `tessera.auth.bearer:BearerTokenAuthenticator` | `Authenticator` |
| `TESSERA_AUDIT_SINK` | `tessera.audit.sinks.sqlite:SqliteSink` | `AuditSink` |
| `TESSERA_POLICY_LOADER` | `tessera.policy.loader:FilesystemPolicyLoader` | `PolicyLoader` |

The class is imported with `importlib.import_module` and instantiated with the
same keyword arguments as the default class. A `ConfigError` (exit 2) is raised
if the module cannot be imported or the attribute is missing.

### Example: custom audit sink

```bash
export TESSERA_AUDIT_SINK=mypackage.sinks:PostgresSink
tessera serve --config tessera.yaml
```

The `PostgresSink` class must implement the `AuditSink` protocol
(`emit`, `close`, `head_hash`, `iter_events`, `iter_scopes`).

### Example: test stub loader

```bash
export TESSERA_POLICY_LOADER=tests.fakes:FakePolicyLoader
pytest tests/integration/
```

This is useful in CI to start the proxy without a real policies directory.

*For policy authoring, see `docs/POLICIES.md`. For audit chain details, see `docs/AUDIT.md`.*

---

## 9. Management-plane SSO

Management-plane SSO uses OIDC/JWKS to authenticate requests to `/app/*` routes
(license check, org management — reserved for v0.2.x; the authenticator is
instantiated at startup but no routes consume it yet). MCP traffic at
`/mcp/{upstream}` continues to use `bearer` or `jwt` mode (see §10).

Configure in `tessera.yaml` under `auth.management_plane`:

```yaml
auth:
  type: bearer          # MCP traffic still uses bearer or jwt
  management_plane:
    provider: clerk
    jwks_url: https://clerk.your-domain.com/.well-known/jwks.json
    issuer: https://clerk.your-domain.com
    audience: tessera-management
    clock_skew_seconds: 60
    scope_claim: email  # claim used to derive the audit scope slug
```

### Clerk (default)

```yaml
auth:
  management_plane:
    provider: clerk
    jwks_url: https://<your-clerk-frontend-api>/.well-known/jwks.json
    issuer: https://<your-clerk-frontend-api>
    audience: tessera-management
```

The `email` claim from the Clerk session token is normalized to a SCOPE_RE-compliant
slug: `alice@example.com` → `alice_at_example_com`.

### Auth0

```yaml
auth:
  management_plane:
    provider: auth0
    jwks_url: https://<your-tenant>.auth0.com/.well-known/jwks.json
    issuer: https://<your-tenant>.auth0.com/
    audience: https://api.tessera.yourcompany.com
    scope_claim: email
```

### Cognito

```yaml
auth:
  management_plane:
    provider: cognito
    jwks_url: https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/jwks.json
    issuer: https://cognito-idp.<region>.amazonaws.com/<user-pool-id>
    audience: <app-client-id>
    scope_claim: email
```

### Bearer-vs-OIDC matrix

| Traffic type | Auth mechanism | Config field |
|---|---|---|
| MCP tool calls (`/mcp/{upstream}`) | Static bearer tokens | `TESSERA_BEARER_TOKENS` / `TESSERA_BEARER_TOKEN` |
| MCP tool calls (JWT-authenticated agents) | OIDC JWT (`auth.type: jwt`) | `auth.jwt.*` (see §10) |
| Management-plane routes (`/app/*`) | OIDC JWT (SSO) | `auth.management_plane.*` |

---

## 10. MCP traffic JWT mode

Set `auth.type: jwt` to authenticate MCP client requests with signed JWTs instead
of static bearer tokens. Useful for CI pipelines, multi-tenant SaaS, or deployments
where the agent runtime already has an OIDC token from Entra, Okta, or Cognito.

```yaml
auth:
  type: jwt
  jwt:
    jwks_url: https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
    issuer: https://login.microsoftonline.com/<tenant-id>/v2.0
    audience: api://tessera-mcp
    clock_skew_seconds: 60
    principal_claim: sub     # claim used as principal_id in audit events
    scope_claim: scope       # claim used for audit chain scope slug
```

The `JWTAuthenticator` validates the Bearer token in the `Authorization` header
against the JWKS endpoint, then extracts `principal_claim` (default `sub`) and
`scope_claim` (default `scope`). The scope is taken as the first space-delimited
token and must match `[a-z0-9_-]{1,64}`; non-compliant values fall back to
`deployment_id`.

### Microsoft Entra (Azure AD)

```yaml
auth:
  type: jwt
  jwt:
    jwks_url: https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
    issuer: https://login.microsoftonline.com/<tenant-id>/v2.0
    audience: api://tessera-mcp
    principal_claim: sub
    scope_claim: scope
```

### Okta

```yaml
auth:
  type: jwt
  jwt:
    jwks_url: https://<your-org>.okta.com/oauth2/default/v1/keys
    issuer: https://<your-org>.okta.com/oauth2/default
    audience: api://tessera
    principal_claim: sub
    scope_claim: scp
```

### Cognito (machine-to-machine)

```yaml
auth:
  type: jwt
  jwt:
    jwks_url: https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/jwks.json
    issuer: https://cognito-idp.<region>.amazonaws.com/<pool-id>
    audience: <app-client-id>
    principal_claim: client_id
    scope_claim: scope
```

`auth.type: jwt` requires the `oidc` extra: `pip install "cloudmorph-tessera[oidc]"`.
