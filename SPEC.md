# Tessera v0.1 — Architecture Specification

Version: **v1.1 (locked)**
Date: 2026-05-10
Author: Spec session (Opus)

Status: Locked. Founder-review decisions applied; the v1.0 Spec Review Checklist is resolved and removed. Mechanical execution by Sonnet follows; see `SONNET_REWRITE.md` in the repo root.

## Decision Log

v1.1 changes from v1.0 (founder-review applied):

- **Multi-token bearer auth.** Replaces the single-token model. Tokens carry per-token scope; `AuditEmitter` keys its hash chain by that scope so token-isolated streams stay distinct.
- **Three-mode policy enforcement** (`enforcement | log_only | observation`) at deployment level. `tessera init` scaffolds new deployments in `log_only`; README quickstart walks through the flip to `enforcement`.
- **Regex safety.** Policy loader uses `regex` library with a 100ms per-match timeout; load-time corpus test rejects ReDoS-prone patterns at startup (50ms cap).
- **Policy reload error handling.** Per-file isolation on reload. A failed reload keeps the previous version in memory; startup still requires every policy to validate. `/healthz` exposes `policy_state` for operator visibility.
- **Intent-blind agent support** documented as first-class. Off-the-shelf MCP clients (Cursor/Claude Desktop/Windsurf) work without `_meta.tessera_intent`. Strict mode via `intent.required: true` is the explicit override.
- **Metrics endpoint default-off + bearer-required when on.** Optional separate read-only token via `TESSERA_METRICS_TOKEN`.
- **Rate limiting explicitly out of scope for v0.1.** Documented mitigation: deploy behind nginx/Caddy/Cloudflare. Native rate limiting is a v0.2 deliverable.
- **Documentation surface expanded:** `docs/AUDIT.md`, `docs/TROUBLESHOOTING.md`, `docs/ROADMAP.md` added.
- **PyPI distribution name uncertainty documented.** Best knowledge: `tessera` is taken on PyPI by an older tessellation library. Rewrite session does a pre-flight `pip index versions tessera` check; if available → distribution name `tessera`, else → `cloudmorph-tessera`. Import name is `tessera` regardless. `pyproject.toml` comments both variants for the rewrite session to choose.
- **`tenant_id` field name in audit chain kept** (per founder confirmation #10), redocumented as scope/stream identifier; multi-token mode populates this from per-token scope.
- **Three Protocols only** (`PolicyLoader`, `AuditSink`, `Authenticator`). `IntentExtractor`, `RateLimiter`, `MetricsExporter` rejected for v0.1.

The 16 confirmed-as-written items from the v1.0 Spec Review Checklist are applied unchanged.

## Document layout

- **Part A** — `_keep/` second-pass cleanup verdict (unchanged from v1.0; founder applies manually).
- **Part B** — 12-section architecture spec (locked).

---

# Part A — `_keep/` second-pass cleanup verdict

The first cleanup pass was conservative. This pass is aggressive: anything that does not slot directly into the v0.1 firewall is cut. After applying the verdict, what remains in `_keep/` is exactly the asset list the rewrite consumes.

| File | Verdict | Reason / target location |
| --- | --- | --- |
| `_keep/README.md` | DELETE | Meta-doc describing `_keep/`; has stale references (mentions an S3 sink not in `_keep/`). After rewrite consumes other files, `_keep/` is removed entirely. |
| `_keep/action_verbs/action_verbs.py` | STAYS | Verb taxonomy is the firewall's classification primitive. Target: `tessera/policy/action_verbs.py`. Drop `mcp.proxy.*` synthetic-action special case (was an executor concept). Default mappings ship as the OOTB registry; users add custom mappings via `policies/_action_verbs.yaml` (see Section 3). |
| `_keep/audit/__init__.py` | DELETE | Imports `cloudmorph_common.audit.sinks.s3.S3Sink` which does not exist in `_keep/` or v0.1 scope. Replaced wholesale by `tessera/audit/__init__.py`. |
| `_keep/audit/canonical_json.py` | STAYS | RFC 8785 JCS implementation; pure, no business logic. Target: `tessera/audit/canonical_json.py`. No code changes. |
| `_keep/audit/chain.py` | STAYS | SHA-256 hash chain, thread-safe, verify helpers. Target: `tessera/audit/chain.py`. No structural changes. `tenant_id` field name kept; docstring re-clarified as "scope/stream identifier" — in OSS multi-token mode this is the per-token scope. |
| `_keep/audit/emitter.py` | STAYS | Fan-out to multiple sinks with hash-chain stamping and per-sink failure isolation. Target: `tessera/audit/emitter.py`. Drop `from cloudmorph_common.errors import AuditSinkError`; define `AuditSinkError` in `tessera/errors.py`. |
| `_keep/audit/sinks/__init__.py` | DELETE | Stale `s3` import. Replaced by fresh `tessera/audit/sinks/__init__.py`. |
| `_keep/audit/sinks/buffered.py` | STAYS | Optional wrapper for users who later add a remote sink. Target: `tessera/audit/sinks/buffered.py`. No code changes. Documented as opt-in. |
| `_keep/audit/sinks/stdout.py` | STAYS | Useful for dev mode and Docker stdout collection. Target: `tessera/audit/sinks/stdout.py`. No changes. |
| `_keep/fixtures/decisions/01_allow_aws_s3_list_buckets.json` | STAYS | Allow-on-read fixture. Target: `tests/fixtures/decisions/`. Input shape adapted to v0.1 evaluation context (`tool_call.name`, `tool_call.arguments`, `runtime.lockdown`). |
| `_keep/fixtures/decisions/02_deny_unknown_action.json` | STAYS | Default-deny fixture. Same target & adaptation. |
| `_keep/fixtures/decisions/03_deny_destructive.json` | STAYS | Destructive-denylist fixture. Same target & adaptation. |
| `_keep/fixtures/decisions/04_deny_intent_mismatch.json` | STAYS | Intent-vs-action divergence. Mismatch is computed by the engine; fixture's `intentMatchScore` is dropped — it carried executor-side stage metadata. |
| `_keep/fixtures/decisions/05_allow_intent_match.json` | STAYS | Intent-match fixture. Same target & adaptation. |
| `_keep/fixtures/decisions/06_deny_tenant_locked.json` | STAYS | Reframed as "lockdown kill switch." Rename to `06_deny_lockdown_active.json`; `tenantSettings.locked: true` becomes `runtime.lockdown: true`. |
| `_keep/fixtures/decisions/README.md` | STAYS | Useful index. Target: `tests/fixtures/decisions/README.md`. Content rewritten to drop "Block E" / `cloudmorph-mcp` / `status/policy/` references. |
| `_keep/rego/main.rego` | DELETE | v0.1 evaluates YAML directly in pure Python. No OPA. Existing rules tied to executor-style input shape; Rego escape hatch deferred to v0.2 (would be authored fresh). |
| `_keep/rego/main_test.rego` | DELETE | Only useful with `main.rego`. |
| `_keep/rego/manifest.json` | DELETE | OPA bundle metadata; v0.1 has no OPA. |
| `_keep/schemas/approval.schema.json` | DELETE | Approval is an executor concept. |
| `_keep/schemas/job.schema.json` | DELETE | Job is an executor concept. |
| `_keep/schemas/request.schema.json` | DELETE | Executor-style request shape; doesn't match MCP `tools/call`. Internal `ToolCallRequest` modeled as Pydantic class, not ported from this schema. |

**Net result:** 11 files survive in `_keep/` — exactly what the rewrite consumes, then removes in the final cleanup task.

---

# Part B — v0.1 architecture specification

## Section 1 — Final repo structure

Target tree after the rewrite. Inline commentary describes contents. The PyPI distribution name is decided at the start of Task 1 by a `pip index versions tessera` check (see Section 12).

```
cloudmorph-tessera/
├── tessera/
│   ├── __init__.py                # __version__ = "0.1.0", public re-exports.
│   ├── proxy.py                   # FastAPI app: POST /mcp/{upstream}, /healthz, /readyz, /metrics (auth-gated).
│   ├── config.py                  # Pydantic models for tessera.yaml + env-var loader + ${VAR} interpolation.
│   ├── errors.py                  # ConfigError, PolicyError, AuditSinkError, UpstreamError, UnauthorizedError.
│   ├── intent.py                  # Extract & validate intent from MCP _meta.<configured_key>.
│   ├── audit/
│   │   ├── __init__.py            # Public re-exports.
│   │   ├── chain.py               # FROM _keep — no changes.
│   │   ├── canonical_json.py      # FROM _keep — no changes.
│   │   ├── emitter.py             # FROM _keep — minus cloudmorph_common.errors import.
│   │   ├── verifier.py            # NEW — chain-walk integrity verification for `tessera audit verify`.
│   │   └── sinks/
│   │       ├── __init__.py        # NEW — exports SqliteSink, StdoutSink, BufferedSink, AuditSink Protocol.
│   │       ├── base.py            # NEW — AuditSink Protocol.
│   │       ├── sqlite.py          # NEW — default sink.
│   │       ├── stdout.py          # FROM _keep — no changes.
│   │       └── buffered.py        # FROM _keep — no changes.
│   ├── policy/
│   │   ├── __init__.py            # Public re-exports.
│   │   ├── action_verbs.py        # FROM _keep — minus mcp.proxy special case; adds load_user_mappings.
│   │   ├── schema.py              # NEW — Pydantic models matching schemas/policy.schema.json.
│   │   ├── loader.py              # NEW — FilesystemPolicyLoader: dir read, validate, watch, per-file reload error isolation.
│   │   ├── engine.py              # NEW — PolicyEngine.evaluate(context) → Decision; mode-agnostic.
│   │   ├── matchers.py            # NEW — upstream / tool name match logic.
│   │   ├── conditions.py          # NEW — condition evaluators; regex via `regex` lib + 100ms timeout.
│   │   └── regex_safety.py        # NEW — load-time corpus test for regex patterns; rejects ReDoS-prone ones.
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── base.py                # NEW — Authenticator Protocol, AuthContext dataclass.
│   │   └── bearer.py              # NEW — multi-token BearerTokenAuthenticator (default).
│   ├── pluggable.py               # NEW — module:Class env-var resolver for the three Protocols.
│   └── cli.py                     # NEW — Typer CLI: serve, audit verify, policy test, policy lint, version, init.
├── policies/                      # Reference policy library.
│   ├── README.md
│   ├── cost-cap.yaml
│   ├── prod-protection.yaml
│   ├── data-residency-eu.yaml
│   ├── pii-block.yaml
│   ├── write-action-approval.yaml
│   ├── read-only-mode.yaml
│   └── secret-leak-block.yaml
├── schemas/
│   ├── policy.schema.json
│   ├── audit_event.schema.json
│   └── config.schema.json
├── tests/
│   ├── conftest.py
│   ├── unit/                      # Per-module test files (per Section 10).
│   ├── integration/               # Multi-module flows + reference-policy fixtures + multi-token + three-mode tests.
│   ├── property/                  # Hypothesis tests for the hash chain.
│   └── fixtures/
│       ├── decisions/             # Migrated from _keep, adapted.
│       ├── policies/              # Test-only policies + per-reference-policy pass/fail fixtures.
│       ├── tokens.example.yaml    # Example multi-token file used by tests.
│       └── upstream/
│           └── mock_mcp_server.py # FastAPI mock for proxy round-trip.
├── docs/
│   ├── INSTALL.md
│   ├── POLICIES.md
│   ├── CONFIGURATION.md
│   ├── ARCHITECTURE.md
│   ├── INTEGRATIONS.md
│   ├── AUDIT.md                   # NEW — audit event schema, verify usage, hash chain guarantees, SQLite→Postgres migration path.
│   ├── TROUBLESHOOTING.md         # NEW — common issues + remediation.
│   └── ROADMAP.md                 # NEW — what's deferred to v0.2 with rationale.
├── Dockerfile
├── docker-compose.example.yaml
├── tessera.example.yaml
├── tokens.example.yaml            # Example tokens file (multi-token format).
├── .env.example
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE                        # Already exists.
├── SECURITY.md                    # Already exists; rewritten for Tessera scope.
├── pyproject.toml                 # Already exists; adds [project], [build-system], runtime deps.
│                                  # Comments both `name = "tessera"` and `name = "cloudmorph-tessera"`;
│                                  # rewrite session uncomments the available one after pre-flight check.
├── .gitignore                     # Already exists, unchanged.
├── .pre-commit-config.yaml        # Already exists; minor updates for type-check coverage.
└── gitleaks-config.toml           # Already exists; minor rewrite to drop CloudMorph-specific allowlist paths.
```

## Section 2 — Configuration model

### Environment variables

All env vars are prefixed `TESSERA_*`. Types are coerced from strings; invalid coercion → exit 2.

| Variable | Type | Default | Required | Purpose |
| --- | --- | --- | --- | --- |
| `TESSERA_CONFIG_PATH` | path | `/etc/tessera/tessera.yaml` | no | Runtime config file location. |
| `TESSERA_POLICY_DIR` | path | (config file) | no | Override `policies.dir`. |
| `TESSERA_AUDIT_PATH` | path | (config file) | no | Override `audit.path` for the SQLite sink. |
| `TESSERA_BEARER_TOKENS` | string | — | no* | Inline multi-token list. Format: `name1:tk_xxx,name2:tk_yyy`. |
| `TESSERA_BEARER_TOKENS_FILE` | path | — | no* | Path to a YAML token file. |
| `TESSERA_BEARER_TOKEN` | string | — | no* | **Legacy single-token compat.** Internally translated to `[{name: "default", token: <value>, scope: "default"}]`. |
| `TESSERA_METRICS_TOKEN` | string | — | no | Dedicated read-only token for `/metrics`. If unset and metrics enabled, any token from the main list grants metrics access. |
| `TESSERA_LOG_LEVEL` | string | `INFO` | no | DEBUG / INFO / WARNING / ERROR. |
| `TESSERA_DEPLOYMENT_ID` | string | `default` | no | Default audit-event scope when no token-derived scope applies (dev mode without auth). |
| `TESSERA_BIND_HOST` | string | `0.0.0.0` | no | Bind interface. |
| `TESSERA_BIND_PORT` | int | `8080` | no | Bind port. |
| `TESSERA_POLICY_LOADER` | string | `tessera.policy.loader:FilesystemPolicyLoader` | no | Pluggable loader (Section 8). |
| `TESSERA_AUDIT_SINK` | string | `tessera.audit.sinks.sqlite:SqliteSink` | no | Pluggable sink. |
| `TESSERA_AUTHENTICATOR` | string | `tessera.auth.bearer:BearerTokenAuthenticator` | no | Pluggable authenticator. |

*One of `TESSERA_BEARER_TOKENS`, `TESSERA_BEARER_TOKENS_FILE`, or `TESSERA_BEARER_TOKEN` is recommended. **All unset → no-auth dev mode**: requests pass without authentication, principal becomes `anonymous`, scope becomes `TESSERA_DEPLOYMENT_ID`. Tessera logs `WARNING level=startup event=auth_disabled` once at boot and every 60s thereafter.

### Multi-token format

**Inline (TESSERA_BEARER_TOKENS):** Comma-separated `name:token` pairs. Scope defaults to name.

```
TESSERA_BEARER_TOKENS="alice:tk_abc123,bob:tk_def456,ci:tk_ghi789"
```

**File (TESSERA_BEARER_TOKENS_FILE):** YAML at the referenced path.

```yaml
# /etc/tessera/tokens.yaml
tokens:
  - name: alice
    token: tk_abc123
    scope: alice          # optional, defaults to name; sets AuthContext.scope and audit chain stream
  - name: bob
    token: tk_def456
  - name: ci
    token: tk_ghi789
    scope: ci-shared      # multiple tokens can share a scope
```

Validation:
- `name` is `[a-z0-9_-]{1,64}`, unique within the list.
- `token` is opaque, ≥ 16 chars, no whitespace.
- `scope` is `[a-z0-9_-]{1,64}`; defaults to `name`.

Loader precedence (first wins):
1. `TESSERA_BEARER_TOKENS` (inline).
2. `TESSERA_BEARER_TOKENS_FILE` (YAML).
3. `TESSERA_BEARER_TOKEN` (legacy single).
4. None → dev mode (warned).

### Runtime config file (`tessera.yaml`)

```yaml
listen:
  host: 0.0.0.0
  port: 8080

auth:
  type: bearer

audit:
  sink: sqlite
  path: /var/lib/tessera/audit.db
  also_stdout: false

policies:
  dir: /etc/tessera/policies
  reload: watch                       # watch | sighup | none
  mode: log_only                      # enforcement | log_only | observation. SEE SECTION 3.
  default_action: block               # used in enforcement when no policy matches.

intent:
  meta_key: tessera_intent
  required: false                     # if true, calls without intent are blocked unconditionally.

metrics:
  enabled: false                      # default OFF.
  bearer_token_env: TESSERA_METRICS_TOKEN   # optional separate token; falls back to main TOKENS.

deployment_id: default                # used when no token scope applies (dev mode).

upstreams:
  - name: aws
    url: https://aws-mcp.internal.example.com
    timeout_seconds: 30
    credentials:
      header: Authorization
      value: "Bearer ${AWS_MCP_TOKEN}"
  - name: github
    url: https://github-mcp.internal.example.com
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${GITHUB_MCP_TOKEN}"

runtime:
  lockdown: false
```

### Precedence

1. Env vars override file values for the keys listed in the env table.
2. Token sources follow the precedence list above.
3. `upstreams[]` come only from the file.
4. `runtime.lockdown` may be toggled by editing the file + SIGHUP (no other field is re-read on SIGHUP).
5. Metrics access: if `TESSERA_METRICS_TOKEN` set → only that token; else any token from the main list; if metrics disabled → `/metrics` not mounted.

### File-watch and reload behavior

| Event | Effect |
| --- | --- |
| File change in `policies.dir` (when `reload: watch`) | Per-file reload (Section 3 "Policy lifecycle"). Failed validation → keep prior, log error, continue. |
| SIGHUP | Reload policies (per-file) AND re-read `runtime.lockdown`. Other config fields ignored. |
| SIGTERM / SIGINT | Graceful shutdown: drain, close sinks, exit 0. |
| Other `tessera.yaml` field changed | NOT auto-reloaded; restart required. |

## Section 3 — Policy YAML schema (the v0.1 wedge)

### Top-level schema

A YAML file under `policies.dir` is one policy. Filenames starting with `_` are config files (e.g., `_action_verbs.yaml`), not policies. Filename is informational; `id` is canonical and unique.

```yaml
id: <kebab-case unique id>           # required, [a-z0-9-]{1,64}
name: <short human-readable name>    # required, ≤ 100 chars
description: <one-paragraph>         # optional

match:                               # required.
  upstream: <name> | "*"             # default: "*".
  tool: <glob> | "*"                 # default: "*".
  tool_pattern: <regex>              # mutually exclusive with `tool`.
  require_intent: true | false       # if true, calls without intent skip this policy.

when: [ <condition>, ... ]           # AND'd. Default: [] (always-true).

action: allow | block | log_only | require_approval   # required.

reason: <string>                     # ${arg.X} and ${audit.event_id} interpolation supported.

priority: <int>                      # higher = earlier. Default 0. Tie-break: id alpha.
```

### Condition catalog (v0.1)

| `condition` | Fields | Truth |
| --- | --- | --- |
| `arg_equals` | `arg`, `value` | `arguments[arg] == value` |
| `arg_greater_than` | `arg`, `value` | numeric `>` |
| `arg_less_than` | `arg`, `value` | numeric `<` |
| `arg_matches_regex` | `arg`, `pattern` | `regex.search(pattern, str(arguments[arg]))` (see Regex safety) |
| `arg_in_set` | `arg`, `values` | membership |
| `arg_contains_pattern` | `arg`, `pattern` | alias of `arg_matches_regex` |
| `arg_size_greater_than` | `arg`, `bytes` | `len(json.dumps(arguments[arg])) > bytes` |
| `tool_name_in` | `values` | tool name in list |
| `action_class_in` | `values` | tool's verb-set intersects values |
| `intent_class_in` | `values` | declared intent verbs intersects values |
| `intent_purpose_matches` | `pattern` | regex search on `intent.purpose` |
| `region_in` | `arg`, `regions` | `arguments[arg]` startswith any prefix |
| `time_of_day_outside` | `start`, `end`, `tz` | timestamp outside daily window |
| `meta_field_equals` | `key`, `value` | `_meta` dot-path equality |
| `any_of` | `conditions` | OR |
| `none_of` | `conditions` | NOT (OR) |

Missing-arg fail-closed (return false). `arg: "*"` iterates every top-level argument value as a string.

### Three worked examples

(Three policies — `cost-cap`, `prod-protection`, `pii-block` — same as v1.0; included so this document stands alone. See Section 9 for the full library.)

### Policy enforcement modes

`policies.mode` selects how the engine's decision is acted upon. Set deployment-wide; reference policies are mode-agnostic.

| Mode | Engine evaluates? | Upstream called? | Response shape | Audit emitted? |
| --- | --- | --- | --- | --- |
| `enforcement` | yes | only if `allow`/`log_only`/no-match-with-allow-default | block → JSON-RPC error; allow → upstream response | yes (`decision: <action>`) |
| `log_only` | yes | **always** | upstream response **plus** headers `X-Tessera-Mode: log_only`, `X-Tessera-Decision: would_block | would_allow | no_match`, plus `X-Tessera-Policy-Id` and `X-Tessera-Reason` on `would_block` | yes (`would_decision: <action>`, `mode: log_only`) |
| `observation` | **no** | always | upstream response | yes (`mode: observation`, no decision) |

Operational pattern (also in README quickstart):
1. Deploy in `mode: log_only` (`tessera init` default).
2. Run live traffic; review `tessera audit verify` + per-decision audit rows.
3. Tune policies until `would_block` matches expectation.
4. Flip `policies.mode` to `enforcement`; restart Tessera (mode flip is not a SIGHUP-reloadable field; per Section 2 reload semantics).

Lockdown (`runtime.lockdown: true`) is enforced **before** the mode branch — blocks all traffic regardless.

### YAML → engine: evaluation strategy

**Decision: pure Python evaluation. No OPA. No Rego.**

Justification: smaller image (~150 MB vs ~250+ MB), faster cold start, single-language stack, sufficient for v0.1. Rego escape hatch deferable to v0.2 without breaking changes.

Pipeline:
1. Loader reads each non-`_*` `*.yaml`, validates against `policy.schema.json`, builds `Policy`.
2. Sort by descending `priority`, ascending `id`.
3. On each `tools/call`: walk sorted list. For each policy: run match (upstream, tool, optional `require_intent` filter); if matched, evaluate `when` conditions left-to-right with short-circuit. If all true → return `Decision`.
4. No match → `Decision(default_action, reason="default", policy_id=None)`.

Lockdown short-circuit happens BEFORE step 3.

### Policy lifecycle and error handling

**At startup:**
- Every non-`_*` `*.yaml` in `policies.dir` is loaded and validated.
- ANY policy fails validation → Tessera **refuses to start**. Exit 2.
- Per-failure log: `level=ERROR event=policy_validation_failed path=<file> line=<n if YAML> error=<msg>`.

**At reload (file-watch fires or SIGHUP):**
- Per-file isolation. Each new/changed file loaded and validated independently.
- Failure → log `level=ERROR event=policy_reload_skipped path=<file> policy_id=<id-or-null> error=<msg>`. Keep the previously-loaded version. Tessera continues serving.
- Reload is per-file, NOT all-or-nothing.

**Lockdown override:** `runtime.lockdown: true` does not stop reloads (so when lockdown lifts, latest policies are active). It only blocks request flow.

**Operator visibility:** `GET /healthz` includes `policy_state: {loaded: <int>, errored: [{path, error}]}`. Operators detect errored policies via the healthcheck without log access. (Healthz remains unauthenticated; policy paths/validation errors are not secrets.)

### Regex safety

ReDoS is the failure mode we explicitly defend against.

- **Library:** `regex` (PyPI), not stdlib `re`. Supports timeouts.
- **Per-match timeout:** 100ms on every compiled pattern (`arg_matches_regex`, `arg_contains_pattern`, `tool_pattern`, `intent_purpose_matches`).
- **Load-time corpus test:** `tessera/policy/regex_safety.py` runs each pattern against 5 synthetic strings (lengths 10/100/1000/10000/100000 chars; mixed alphanumeric). Each match must complete in <50ms.
  - At **startup**: failure → policy rejected with `event=policy_validation_failed reason=regex_potential_redos`. Tessera refuses to start (exit 2).
  - At **reload**: failure → policy skipped with `event=policy_reload_skipped reason=regex_potential_redos`.
- **Runtime timeout:** every match also bounded by 100ms. On timeout: condition returns false; audit event records `decision_error: regex_timeout` with `policy_id`. **Tessera does NOT fail-closed for the entire request on regex timeout** — only the timed-out condition is skipped.
- **Dependency:** `regex >= 2024.0.0` added to runtime deps.

### action_verbs taxonomy reference

Built-in registry in `tessera/policy/action_verbs.py` (ported from `_keep`). Verbs:

```
read.list, read.describe, read.get, read.search, read.aggregate
analyze, summarize, compare
write.create, write.update, write.delete
execute.run, execute.deploy
notify.send, notify.publish
escalate.approve, escalate.deny
audit.log, audit.export
simulate, dry_run
```

Customers extend via `policies/_action_verbs.yaml`:

```yaml
mappings:
  github_create_issue: [write.create]
  github_close_issue: [write.update]
  github_delete_repo: [write.delete]
```

Loader merges with built-ins (file overrides). Tools not in registry → empty verb-set; `action_class_in` against them returns false.

### Intent declarations from MCP `_meta`

Agents populate `_meta.<configured_key>` (default `tessera_intent`):

```json
{
  "params": {
    "name": "aws_s3_list_buckets",
    "arguments": {},
    "_meta": {
      "tessera_intent": {
        "verbs": ["read.list"],
        "purpose": "Inventory S3 buckets to count objects per region for the cost-attribution report."
      }
    }
  }
}
```

Contract: `verbs` required when intent block present; `purpose` optional, ≤ 1024 chars.

**Intent-blind agent support.** Off-the-shelf MCP clients (vanilla Cursor, Claude Desktop, Windsurf) do NOT populate intent. Tessera supports both modes simultaneously:

- **Intent-aware agents:** policies with `match.require_intent: true` evaluate against declared intent. Mismatches and `intent_class_in` checks fire.
- **Intent-blind agents:** policies with `match.require_intent: true` are SKIPPED. Policies without `require_intent` evaluate normally based on tool name + arguments alone.

`intent.required: true` (global config) is the strict-mode override: ALL calls must include intent or are blocked with reason `intent_required` regardless of policy match clauses. For organizations standardizing on intent-aware tooling.

### Deferred (NOT in v0.1)

| Feature | Status | Why deferred |
| --- | --- | --- |
| Inline Rego files alongside YAML | v0.2 | OPA dep; YAML covers the wedge. |
| Policy composition | v0.2 | Policy graph adds complexity. |
| Scopes / namespacing | v0.2 | Multi-scope is a Cloud concern. |
| LLM-judge intent matching | not planned | Out of scope for OSS firewall. |
| Per-policy version pinning / signed bundles | v0.2 | OSS users own the dir. |
| Native rate limiting | v0.2 | See Section 4 "Out of scope". |

## Section 4 — MCP proxy implementation plan

### HTTP framework

**FastAPI.** Async-native, Pydantic-friendly, OpenAPI-by-default.

### Endpoint structure

`POST /mcp/{upstream_name}` per upstream.

| Endpoint | Method | Purpose | Auth |
| --- | --- | --- | --- |
| `/mcp/{upstream}` | POST | Proxied JSON-RPC. | Bearer required (or dev-mode bypass). |
| `/healthz` | GET | Liveness; returns `{status: "ok", policy_state: {loaded, errored}}`. | None. |
| `/readyz` | GET | Readiness; 200 only if at least one upstream reachable AND policies loaded. | None. |
| `/metrics` | GET | Prometheus. **Mounted only when `metrics.enabled: true`.** | Bearer required (per Section 2 metrics rules). |

Metrics labels (when enabled): `requests_total{outcome}`, `decisions_total{action,policy_id,mode}`, `audit_emit_failures_total`, `upstream_request_duration_seconds`, `regex_timeout_total{policy_id}`.

### Upstream configuration

In `tessera.yaml: upstreams[]`. Read once at startup. Credentials from env via `${VAR}`.

### Bearer-token validation (Tessera-facing)

`BearerTokenAuthenticator` (default):

1. Iterate token list; `secrets.compare_digest` against incoming `Authorization: Bearer <token>`. **Constant-time** to prevent timing attacks.
2. Match → `AuthContext(principal_id=token name, scope=token scope, metadata={})`.
3. No match → raise `UnauthorizedError` → HTTP 401.
4. Empty list (dev mode) → bypass; `AuthContext(principal_id="anonymous", scope=TESSERA_DEPLOYMENT_ID, metadata={"warning": "auth_disabled"})`.

`AuthContext.scope` is what `AuditEmitter` keys its hash chain by. Multi-token deployments get isolated per-token streams.

### Request flow

1. Receive `POST /mcp/{upstream}`. Authenticator validates. 401 on failure.
2. Parse JSON-RPC. Malformed → HTTP 400, code `-32700`.
3. Branch on `method`:
   - `tools/call` → policy path (steps 4–9).
   - `tools/list`, `prompts/list`, `resources/list`, `initialize`, `notifications/*`, `ping` → **pass-through** (forward, return, audit `event_type: passthrough`). NOT policy-evaluated.
   - Else → HTTP 400, `outcome: rejected_unknown_method`.
4. Extract `params.name`, `params.arguments`, `params._meta`.
5. Build context: `{tool_call, intent, runtime, upstream, mode}`.
6. Lockdown short-circuit: if `runtime.lockdown` → JSON-RPC error `-32603` reason `lockdown_active`. Audit. Skip 7–9.
7. Mode branch:
   - **`enforcement`** → engine returns `Decision`. Honor: `allow`/`log_only` → forward; `block` → `-32603`; `require_approval` → `-32604` reason `approval_required: <reason>`. Audit `decision: <action>`.
   - **`log_only`** → engine returns `Decision`. **Always forward upstream and return upstream response.** Inject response headers per the table above. Audit `would_decision: <action>, mode: log_only`.
   - **`observation`** → skip engine. Forward. Audit `mode: observation`.
8. Pass-through and observation: no `Decision`; audit records `event_type` only.
9. Append `_meta.tessera_audit_event_id` to every response body.

### Error handling

| Scenario | HTTP | JSON-RPC code | Audit `outcome` |
| --- | --- | --- | --- |
| Missing/invalid bearer | 401 | n/a | `unauthorized` |
| Malformed JSON-RPC | 400 | `-32700` | `parse_error` |
| Unknown method | 400 | `-32601` | `unknown_method` |
| Policy block (enforcement) | 200 | `-32603` | `block` |
| Lockdown block | 200 | `-32603` | `block` (reason `lockdown_active`) |
| Require-approval (enforcement) | 200 | `-32604` | `require_approval` |
| Upstream timeout | 200 | `-32000` | `upstream_timeout` |
| Upstream 5xx | 200 | `-32001` | `upstream_error` |
| Policy evaluation error | 200 | `-32002` (enforcement only; log_only/observation forward upstream regardless) | `policy_error` |
| Audit sink failure | 200 (request unaffected) | n/a | `audit_emit_failures_total` incremented |

### Out of scope for v0.1

**Native rate limiting.** v0.1 does NOT include per-token, per-agent, or global rate limiting. A misbehaving or compromised agent can hammer the proxy. Operators should deploy Tessera behind nginx, Caddy, Cloudflare, or AWS API Gateway for rate limiting until v0.2 ships native support.

README quickstart includes: "If exposing Tessera beyond localhost, put it behind nginx/Caddy with a rate-limit rule. Native rate limiting is on the v0.2 roadmap."

### Transport

HTTP only. v0.2 may add stdio.

### Concurrency model

Async with FastAPI + `httpx.AsyncClient` (pooled). SQLite writes via `asyncio.to_thread`. Target ~200 rps.

## Section 5 — Audit storage interface

### `AuditSink` Protocol

```python
class AuditSink(Protocol):
    name: str
    def emit(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...
    def head_hash(self, scope: str) -> str: ...
    def iter_events(self, scope: str | None = None) -> Iterator[dict]: ...
```

### Audit event schema (v0.1)

Every emitted event has these top-level fields. Full JSON Schema at `schemas/audit_event.schema.json`.

```jsonc
{
  "schemaVersion": "v0.1",
  "eventId":       "evt_<26 url-safe chars>",
  "tenantId":      "<scope from AuthContext.scope; OSS = token scope or deployment_id>",
  "eventType":     "decision | passthrough | startup | reload | regex_timeout | audit_self_check",
  "occurredAt":    "<ISO 8601 UTC microsecond>",
  "prevEventHash": "<sha256 hex | empty for first event in scope>",
  "eventHash":     "<sha256 hex>",
  "payload": {
    "mode":           "enforcement | log_only | observation",      // present on decision events
    "decision":       "allow | block | log_only | require_approval", // present in enforcement
    "would_decision": "allow | block | log_only | require_approval", // present in log_only
    "policy_id":      "<id | null>",
    "reason":         "<interpolated reason string | null>",
    "upstream":       "<name>",
    "tool_call": {
      "name":      "<tool name>",
      "arguments": { "<sanitized>": "<...>" },
      "_meta":     { "tessera_intent": { ... } }
    },
    "principal_id":   "<from AuthContext>",
    "request_id":     "<uuid>",
    "decision_error": "regex_timeout | policy_error | null"
  }
}
```

`decision` and `would_decision` are mutually exclusive; populated by mode. `observation` events have neither. Pass-through events have no `mode`/`decision`. `arguments` are stored verbatim — v0.1 does NOT redact (DLP at policy time via `pii-block.yaml`).

### Default SQLite sink

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_scope_seq          ON audit_events (scope, seq);
CREATE INDEX        IF NOT EXISTS idx_audit_scope_occurred_at  ON audit_events (scope, occurred_at);
```

PRAGMAs: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`. Migrations: NONE in v0.1.

### Postgres sink (interface only)

Protocol designed to support without breaking changes. v0.1 ships no `postgres.py`. Selected via `TESSERA_AUDIT_SINK` env var when added.

### Hash chain integration

On `AuditEmitter.emit()`: stamp via `HashChain.stamp(event)`; sink persists. On startup: `sink.head_hash(scope)` → `HashChain.restore_head(scope, head_hash)`. Per-scope head.

### `tessera audit verify`

Walks `iter_events(scope)`. Recomputes hash; verifies adjacent pairs. JSON output:

```json
{
  "scope": "default",
  "events_checked": 12345,
  "first_event_at": "2026-05-01T00:00:00Z",
  "last_event_at": "2026-05-10T18:00:00Z",
  "ok": true,
  "first_failure": null
}
```

On failure: `first_failure: {seq, event_id, kind, expected_event_hash, computed_event_hash}`. Exit 0/2/3.

### Retention

v0.1 keeps everything forever. Customers run their own rotation. `tessera audit reset` is v0.2.

### Stdout sink

`audit.also_stdout: true` mirrors events to stdout in addition to primary sink.

## Section 6 — CLI surface

### Framework

**Typer.**

### Commands

| Command | Purpose | Notable |
| --- | --- | --- |
| `tessera serve` | Start the proxy. | `--config`, `--policy-dir`, `--bind`, `--log-level`. SIGTERM = drain; SIGHUP = reload policies + lockdown. |
| `tessera audit verify` | Walk hash chain. | `--audit-path`, `--scope`, `--all`, `--json`. Exit 0/2/3. |
| `tessera policy test` | Run fixture decisions against policies. | `--policy-dir`, `--fixture`/`--fixture-dir`, `--json`. |
| `tessera policy lint` | Validate YAML + ReDoS corpus check. | `--policy-dir`, `--json`. |
| `tessera version` | Version + git SHA + Python. | `--json`. |
| `tessera init` | Scaffold starter files. **Defaults `policies.mode: log_only`**. | `--dir`, `--force`. |

Exit codes: `0` success, `1` soft error, `2` config error, `3` integrity failure.

## Section 7 — Dockerfile design

`python:3.12-slim`, target ~150 MB. Multi-stage. Non-root `tessera` user (uid/gid 10001). HEALTHCHECK uses `/healthz`. No OPA. Mounts: `/etc/tessera/{tessera.yaml,policies/,tokens.yaml}` ro, `/var/lib/tessera/` rw.

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY tessera/ ./tessera/
RUN pip install --target=/install --no-cache-dir .

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/cloudmorph-ai/cloudmorph-tessera"
LABEL org.opencontainers.image.licenses="Apache-2.0"

RUN groupadd -g 10001 tessera && \
    useradd  -u 10001 -g 10001 -M -s /usr/sbin/nologin tessera && \
    mkdir -p /etc/tessera/policies /var/lib/tessera && \
    chown -R tessera:tessera /etc/tessera /var/lib/tessera

COPY --from=builder /install /usr/local/lib/python3.12/site-packages
COPY policies/             /etc/tessera/policies-default/
COPY tessera.example.yaml  /etc/tessera/tessera.example.yaml
COPY tokens.example.yaml   /etc/tessera/tokens.example.yaml

USER tessera
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=3).status==200 else 1)"

CMD ["tessera", "serve"]
```

### Worked `docker run`

```bash
docker run -d \
  --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v "$PWD/tokens.yaml:/etc/tessera/tokens.yaml:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKENS_FILE=/etc/tessera/tokens.yaml \
  -e AWS_MCP_TOKEN="$AWS_MCP_TOKEN" \
  ghcr.io/cloudmorph-ai/tessera:0.1.0
```

## Section 8 — Tessera Cloud FROM-base relationship

### Image distribution

`ghcr.io/cloudmorph-ai/tessera:<version>`. Tags `:0.1.0`, `:0.1`, `:latest`, `:main`. Manual publish at v0.1.0 (no GitHub Actions per founder).

### Cloud's wrapper Dockerfile (mono-repo, illustrative)

```dockerfile
FROM ghcr.io/cloudmorph-ai/tessera:0.1.0
COPY tessera_cloud /opt/tessera_cloud
RUN pip install --no-cache-dir /opt/tessera_cloud

ENV TESSERA_POLICY_LOADER=tessera_cloud.policies:DynamoDBPolicyLoader
ENV TESSERA_AUDIT_SINK=tessera_cloud.audit:DynamoDBAuditSink
ENV TESSERA_AUTHENTICATOR=tessera_cloud.auth:CognitoJWTAuthenticator

CMD ["tessera-cloud", "serve"]
```

### Pluggable extension points

Three Protocols shipped in OSS:

```python
class PolicyLoader(Protocol):
    def load_all(self, scope: str) -> list[Policy]: ...
    def watch(self, scope: str, callback: Callable[[list[Policy]], None]) -> None: ...

class AuditSink(Protocol):
    name: str
    def emit(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...
    def head_hash(self, scope: str) -> str: ...
    def iter_events(self, scope: str | None = None) -> Iterator[dict]: ...

class Authenticator(Protocol):
    def authenticate(self, request: "Request") -> "AuthContext": ...

@dataclass
class AuthContext:
    principal_id: str
    scope: str
    metadata: dict[str, Any]
```

### Default Authenticator implementation

`tessera.auth.bearer:BearerTokenAuthenticator` accepts the multi-token list (any source per Section 2). Per-token `scope` populates `AuthContext.scope`. Constant-time compare via `secrets.compare_digest`.

Cloud's `CognitoJWTAuthenticator` validates JWT signatures against Cognito JWKS, extracts `tenant_id` from claims. The OSS `AuthContext` shape needs no extension.

### Selection mechanism

`tessera/pluggable.py` resolves `module:Class` via `importlib.import_module` + `getattr`. Each implementation receives the relevant `tessera.yaml` config slice. Resolution at startup.

## Section 9 — Reference policy library

**The 7 reference policies are mode-agnostic.** They declare actions; the proxy honors mode. A policy authored for `enforcement` works unchanged in `log_only` (decision becomes `would_*`) and is skipped in `observation`.

(Policy bodies — `cost-cap.yaml`, `prod-protection.yaml`, `data-residency-eu.yaml`, `pii-block.yaml`, `write-action-approval.yaml`, `read-only-mode.yaml`, `secret-leak-block.yaml` — are identical to v1.0 Section 9. Each ships in `policies/` with paired pass/fail fixtures under `tests/fixtures/policies/<id>/{pass,fail}/`.)

For full bodies see `policies/` after the rewrite. The shapes were locked in v1.0 and need no v1.1 changes — they are mode-agnostic by construction.

## Section 10 — Testing strategy

Layout: `tests/{unit,integration,property,fixtures}/`.

### v1.1 additional test responsibilities

| Source area | Additional tests |
| --- | --- |
| `tessera/auth/bearer.py` | Inline format parsing; file format parsing; legacy single-token compat; precedence; dev-mode warn loop; constant-time; per-token scope reaches `AuthContext.scope`. |
| `tessera/policy/loader.py` | Per-file reload error isolation: malformed reload does NOT replace prior valid version; healthz reflects errored entries. |
| `tessera/policy/regex_safety.py` | Corpus test: known-bad pattern (`(a+)+$`) rejected; benign accepted; load-time and reload-time behavior diverge (startup exits, reload skips). |
| `tessera/policy/conditions.py` | 100ms runtime timeout: pattern that legitimately exceeds returns false; audit `decision_error: regex_timeout` recorded. |
| `tessera/policy/engine.py` + `tessera/proxy.py` | Three modes round-trip: in `log_only`, `would_block` decision still forwards upstream and emits headers; in `observation`, no engine call; in `enforcement`, decision honored. |
| `tessera/proxy.py` | Metrics gating: disabled = 404; enabled-no-token = 401; enabled-with-main-token = 200; enabled-with-dedicated-token = 200. Healthz exposes `policy_state`. |

### Coverage target

`fail_under = 80` (existing). Hash chain modules target 100%.

### Property tests (Hypothesis)

1. Verify always succeeds on a clean stamped chain.
2. Mutating any byte breaks `verify_event_hash`.
3. Adjacent swap breaks `verify_pair`.
4. `canonical_json` is deterministic across dict orderings.

### Mock upstream

`tests/fixtures/upstream/mock_mcp_server.py` — FastAPI app with configurable response per `params.name`; supports artificial delay/fail.

## Section 11 — Documentation skeleton

### `README.md` (≤ 400 lines, quickstart above the fold)

1. Header (name, tagline, badges).
2. Why Tessera (3-5 sentences).
3. **5-minute quickstart** (paste-able commands, log_only → enforcement walkthrough).
4. What ships (bulleted, links to subdocs).
5. How it works (one ASCII diagram).
6. Configuration at a glance (10-line yaml).
7. Authoring policies (10-line policy).
8. Tessera Cloud (short paragraph + link).
9. Roadmap (link).
10. Contributing / License / Security.

### `docs/INSTALL.md`

Docker primary, pip secondary, docker-compose example, production hardening, `tessera init` walkthrough.

### `docs/POLICIES.md`

Full YAML schema, condition catalog (table + per-row example), action_verbs taxonomy + extension, intent (aware vs blind), per-reference-policy walkthrough, composition limitations.

### `docs/CONFIGURATION.md`

Every env var, every `tessera.yaml` field, the three modes, multi-token setup (inline + file + legacy), lockdown, metrics endpoint with auth.

### `docs/ARCHITECTURE.md`

Component overview, request lifecycle, audit subsystem, three Protocols, why no OPA, FROM-base relationship for Tessera Cloud (high-level only — no internal mono-repo references).

### `docs/INTEGRATIONS.md`

Cursor / Claude Code / Claude Desktop / Windsurf with worked `mcp.json` examples. Intent-aware vs intent-blind explanation.

### `docs/AUDIT.md` (NEW)

Audit event schema, `tessera audit verify` usage, hash chain guarantees, SQLite→Postgres migration path, retention guidance.

### `docs/TROUBLESHOOTING.md` (NEW)

Common issues: policy not applying, intent missing, audit verify failures, Docker volume permissions, regex timeout warnings, `/metrics` 401, healthz showing errored policies.

### `docs/ROADMAP.md` (NEW)

Per-feature deferrals + rationale: OAuth 2.1 PKCE, Rego escape hatch, multi-tenant, ML intent inference, native rate limiting, shadow MCP discovery, Postgres sink, stdio transport.

### `CHANGELOG.md`

Starts at v0.1.0.

### `CONTRIBUTING.md`

Dev setup, tests, PR conventions, adding a new condition / sink / reference policy.

### `SECURITY.md` (rewrite)

Drop CloudMorph Control Centre references. Scope (in): proxy, engine, audit chain, OSS Docker image. Scope (out): user policies, upstream MCP servers, customer tokens. Especially welcome: cross-scope leakage, audit chain bypass, auth bypass, regex DoS, policy logic flaws.

## Section 12 — Task-ordered rewrite plan

Each task is verifiable. v1.1 LoC additions noted inline.

```
Task 1 — Repo skeleton & build setup
Depends on: none
v1.1: PyPI name pre-flight check; pyproject.toml comments both name variants.
Outputs:
  - tessera/__init__.py (__version__ = "0.1.0")
  - tessera/errors.py (ConfigError, PolicyError, AuditSinkError, UpstreamError, UnauthorizedError)
  - pyproject.toml updates: [project] (with both name variants commented), [project.optional-dependencies]
    dev/runtime, [project.scripts] tessera = "tessera.cli:app"
    Pre-flight: `pip index versions tessera` decides which name to uncomment.
  - .env.example, tessera.example.yaml (with mode: log_only), tokens.example.yaml
Verification:
  - pip install -e ".[dev]" succeeds
  - python -c "import tessera; print(tessera.__version__)" prints 0.1.0
  - tessera --help prints
  - tessera.example.yaml has mode: log_only

Task 2 — Audit chain + canonical JSON
Depends on: Task 1
Inputs: _keep/audit/canonical_json.py, _keep/audit/chain.py
Outputs:
  - tessera/audit/canonical_json.py (copy from _keep, no changes)
  - tessera/audit/chain.py (copy from _keep; docstring rephrased — tenant_id is scope/stream)
  - tests/unit/audit/test_canonical_json.py
  - tests/unit/audit/test_chain.py
  - tests/property/test_hash_chain_property.py (Hypothesis: 4 properties)
Verification:
  - pytest tests/unit/audit/test_canonical_json.py test_chain.py tests/property/ passes
  - 100% coverage on chain.py and canonical_json.py

Task 3 — AuditSink Protocol + StdoutSink + BufferedSink
Depends on: Task 1 (parallel-safe with Task 2)
Inputs: _keep/audit/sinks/{stdout.py,buffered.py}
Outputs:
  - tessera/audit/sinks/base.py (AuditSink Protocol)
  - tessera/audit/sinks/stdout.py (copy; head_hash/iter_events return empty)
  - tessera/audit/sinks/buffered.py (copy)
  - tests/unit/audit/sinks/{test_stdout.py,test_buffered.py}
Verification: pytest tests/unit/audit/sinks/test_stdout.py test_buffered.py passes

Task 4 — SqliteSink (default)
Depends on: Task 3
Outputs:
  - tessera/audit/sinks/sqlite.py (per Section 5: schema, WAL, emit/close/head_hash/iter_events)
  - tests/unit/audit/sinks/test_sqlite.py
Verification:
  - pytest passes; sqlite3 audit.db ".schema" matches spec

Task 5 — AuditEmitter + verifier
Depends on: Task 4
Inputs: _keep/audit/emitter.py
Outputs:
  - tessera/audit/emitter.py (drop cloudmorph_common.errors import)
  - tessera/audit/verifier.py (chain-walk verifier)
  - tessera/audit/__init__.py (public re-exports)
  - tests/unit/audit/test_emitter.py, test_verifier.py
Verification: pytest tests/unit/audit/ passes

Task 6 — Action verbs registry
Depends on: Task 1 (parallel-safe with Tasks 2-5)
Inputs: _keep/action_verbs/action_verbs.py
Outputs:
  - tessera/policy/action_verbs.py (drop mcp.proxy entry + prefix special-case; add load_user_mappings)
  - tests/unit/policy/test_action_verbs.py
Verification: pytest passes

Task 7 — Policy schema + loader (+ regex safety + reload error handling)
Depends on: Tasks 1, 6
v1.1 additions:
  - Three-mode + default_action in config schema (mode at deployment level): ~10 LoC
  - Per-file reload error isolation in loader.py: ~30 LoC
  - Regex safety subsystem (regex_safety.py + conditions.py integration via loader pre-validation): ~40 LoC
Outputs:
  - schemas/policy.schema.json
  - schemas/config.schema.json (incl. policies.mode + default_action + metrics.* fields)
  - tessera/policy/schema.py (Pydantic: Policy, MatchSpec, Condition union, Action enum)
  - tessera/policy/loader.py (FilesystemPolicyLoader: dir read; per-file isolation on reload;
    keeps prior on failure; structured logs; healthz state hook; PolicyLoader Protocol)
  - tessera/policy/regex_safety.py (corpus test; rejects ReDoS at startup, skips at reload)
  - tests/unit/policy/test_schema.py, test_loader.py, test_regex_safety.py
Verification:
  - pytest tests/unit/policy/test_schema.py test_loader.py test_regex_safety.py passes
  - All 7 reference policies (Task 11) validate against policy.schema.json
  - Reload-error test: malformed reload → engine still serves with prior version

Task 8 — Policy engine (matchers + conditions, regex-safe)
Depends on: Task 7
v1.1 additions: regex matching uses `regex` library with 100ms timeout in conditions.py — ~20 LoC
Outputs:
  - tessera/policy/matchers.py
  - tessera/policy/conditions.py (every condition; arg: "*" iteration; missing-arg fail-closed;
    regex via regex library with 100ms timeout; on timeout return false + tag decision_error)
  - tessera/policy/engine.py (PolicyEngine.evaluate(context) → Decision; first-match-wins;
    lockdown short-circuit; mode-AGNOSTIC)
  - tests/unit/policy/test_matchers.py, test_conditions.py (incl. timeout case), test_engine.py
Verification: pytest tests/unit/policy/ passes (whole tree)

Task 9 — Config loader + env interpolation
Depends on: Task 1 (parallel-safe)
v1.1 additions: policies.mode + default_action + metrics.* + tokens config — ~15 LoC
Outputs:
  - tessera/config.py (Pydantic models; env override; ${VAR} interpolation)
  - tests/unit/test_config.py
Verification: pytest passes; tessera.example.yaml validates

Task 10 — Authenticator (multi-token) + intent extractor
Depends on: Task 1 (parallel-safe)
v1.1 additions: full multi-token surface — ~30 LoC over v1.0 single-token
Outputs:
  - tessera/auth/base.py (Authenticator Protocol, AuthContext dataclass)
  - tessera/auth/bearer.py:
      * build_token_list() with precedence: TOKENS (inline) > TOKENS_FILE > TOKEN (legacy) > [] (dev)
      * BearerTokenAuthenticator iterates list with secrets.compare_digest
      * AuthContext.scope from per-token scope
      * Dev mode: AuthContext(principal_id="anonymous", scope=DEPLOYMENT_ID); 60s warn loop
  - tessera/intent.py (extract _meta.<key>; validate verbs; enforce intent.required)
  - tests/unit/auth/test_bearer.py (every loader path; constant-time; dev-mode bypass; per-token scope)
  - tests/unit/test_intent.py
Verification: pytest tests/unit/auth/ tests/unit/test_intent.py passes

Task 11 — Reference policies + paired fixtures
Depends on: Tasks 7, 8
Outputs:
  - policies/{cost-cap,prod-protection,data-residency-eu,pii-block,write-action-approval,
    read-only-mode,secret-leak-block}.yaml
  - policies/README.md (mode-agnostic note + load order)
  - tests/fixtures/policies/<id>/{pass,fail}/*.json (≥1 each per policy)
  - tests/integration/test_reference_policies.py
Verification:
  - pytest tests/integration/test_reference_policies.py passes
  - tessera policy lint --policy-dir policies/ exits 0

Task 12 — Pluggable resolver + MCP proxy (+ three-mode + metrics auth)
Depends on: Tasks 5, 8, 9, 10, 11
v1.1 additions:
  - Three-mode handling in proxy with header injection in log_only — ~30 LoC
  - Metrics endpoint mounted-only-when-enabled + bearer auth — ~10 LoC
  - /healthz returns policy_state — ~10 LoC (consumed from loader hook)
Outputs:
  - tessera/pluggable.py (importlib resolver)
  - tessera/proxy.py (FastAPI: POST /mcp/{upstream}, /healthz with policy_state, /readyz,
    /metrics gated; full request lifecycle; mode branch with header injection;
    httpx.AsyncClient pooled per upstream)
  - tests/fixtures/decisions/ (port from _keep, adapt; 06 renamed)
  - tests/fixtures/upstream/mock_mcp_server.py
  - tests/fixtures/tokens.example.yaml
  - tests/conftest.py (fixtures from Section 10)
  - tests/integration/test_proxy_round_trip.py (allow + block + lockdown + intent missing +
    upstream timeout; THREE-MODE: enforcement honors decision; log_only forwards always with
    headers; observation skips engine; multi-token: per-token scope reaches audit)
  - tests/integration/test_policy_decisions.py (loads ported _keep fixtures)
  - tests/integration/test_audit_persistence.py (restart restores chain head)
  - tests/integration/test_metrics_endpoint.py (disabled=404; no-token=401; main-token=200;
    metrics-token=200)
Verification:
  - pytest tests/integration/ passes
  - All 6 ported decision fixtures pass
  - log_only mode integration test: response includes X-Tessera-Mode + X-Tessera-Decision headers
  - /healthz JSON includes policy_state

Task 13 — CLI
Depends on: Tasks 5, 7, 8, 9, 12
Outputs:
  - tessera/cli.py (Typer: serve, audit verify, policy test, policy lint, version, init)
  - tests/unit/test_cli.py
Verification:
  - pytest passes
  - tessera --help shows all 6 commands
  - tessera version exits 0
  - tessera policy lint --policy-dir policies/ exits 0
  - tessera policy test --policy-dir policies/ --fixture-dir tests/fixtures/decisions/ exits 0
  - tessera audit verify --audit-path /tmp/empty.db exits 0
  - tessera init --dir /tmp/scaffold scaffolds with mode: log_only

Task 14 — Dockerfile + image build
Depends on: Tasks 12, 13
Outputs:
  - Dockerfile (multi-stage)
  - docker-compose.example.yaml (Tessera + mock upstream)
  - .dockerignore
Verification:
  - docker build -t tessera-test:dev . succeeds
  - docker image ls tessera-test:dev shows < 200 MB
  - docker run --rm tessera-test:dev tessera version exits 0
  - End-to-end smoke (manual): docker run with tessera.yaml + policies + tokens.yaml mounted;
    curl POST /mcp/<upstream> with bearer token returns expected response

Task 15 — Documentation (Phase 2)
Depends on: Tasks 1-14
Outputs:
  - README.md
  - docs/INSTALL.md, docs/POLICIES.md, docs/CONFIGURATION.md, docs/ARCHITECTURE.md,
    docs/INTEGRATIONS.md, docs/AUDIT.md (NEW), docs/TROUBLESHOOTING.md (NEW),
    docs/ROADMAP.md (NEW)
  - CHANGELOG.md, CONTRIBUTING.md
  - Updated SECURITY.md
Verification:
  - All internal links resolve
  - Markdown lint passes
  - 5-minute quickstart from README, executed by a fresh reader, produces a successful
    blocked + allowed call AFTER flipping mode from log_only to enforcement

Task 16 — Cleanup
Depends on: All
Outputs:
  - rm -rf _keep/
  - mv SPEC.md docs/_internal/v1.1-spec.md (preserves spec for archival)
Verification:
  - git status clean (besides committed branch changes)
  - pytest passes (no _keep/ references)
  - ruff + mypy + pre-commit run --all-files passes
```

The rewrite is complete after Task 16.
