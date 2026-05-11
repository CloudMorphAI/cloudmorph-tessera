# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-05-11

### Fixed

- **Docker image — pip CVE remediation.** Upgraded pip to `>=26.1.1` in both Docker
  build stages. Closes CVE-2026-6357 (pip 26.0.1 was the default in `python:3.12-slim`).
  The CVE was dormant in v0.1.0 because Tessera never invokes `pip install` at
  runtime, but upgrading removes the static-scan finding for any image scanner
  pointed at the Tessera image.
- **CI security workflow.** `pip-audit` job now upgrades pip to `>=26.1.1` before
  scanning, so the CI runner's own toolchain pip doesn't trigger the same CVE
  finding on every push to main.
- **Dockerfile.** Moved `ENV SOURCE_DATE_EPOCH` inside each `FROM` stage with a
  re-declared `ARG`. Previous layout (`ENV` before any `FROM`) was syntactically
  invalid and broke the v0.1.0 release Docker build on first attempt.
- **Branding.** Aligned every `cloudmorph-ai` reference to the canonical org slug
  `cloudmorphai` (no hyphen) — the previous slug was a different GitHub identity
  and would have 404'd at `docker pull` time. Affected: README, INSTALL.md,
  Dockerfile LABEL, release.yml, CHANGELOG.md, CONTRIBUTING.md.
- **Action verbs registry.** Renamed all 50 entries from dotted (`aws.s3.list_buckets`)
  to underscored (`aws_s3_list_buckets`), matching the actual MCP server naming
  convention. Three shipped policies (`read-only-mode`, `write-action-approval`,
  `data-residency-eu`) that use `action_class_in` would otherwise silently never
  match real MCP tool names.
- **Cursor demo (`examples/cursor_hooks_demo/test.sh`).** Rewrote with auto-start
  for the mock upstream and Tessera proxy, with cleanup on exit. Old version
  required two terminals running first.
- **`tessera/proxy.py` lifespan migration.** Replaced deprecated `@app.on_event`
  decorators with the FastAPI `lifespan` async context manager. Eliminates 19
  `DeprecationWarning`s and the `unclosed database` `ResourceWarning` previously
  surfaced by pytest.

### Changed

- **Docs structure (cleanup).** Removed `handbook/` (7 files) and `docs/ARCHITECTURE.md`;
  folded `docs/REPRODUCIBLE_BUILDS.md` into `docs/INSTALL.md`. Slimmed
  `docs/INSTALL.md`, `docs/POLICIES.md`, `docs/CONFIGURATION.md`, `docs/AUDIT.md`,
  `docs/INTEGRATIONS.md`, and `docs/TROUBLESHOOTING.md` by an aggregate ~63%.
- **`gitleaks-config.toml`.** Removed two stale allowlist paths referencing
  pre-rename folder names (`cloudmorph-mcp/`, `cloudmorph-common-py/`); updated
  the header comment.
- **`.gitignore`.** Whitelisted `tests/fixtures/**` so the `*_secret*` rule doesn't
  block legitimate fixture content. Gated `docs/_internal/**` so internal
  operational drafts don't leak on push (with explicit allowlist for
  `v1.1-spec.md`).

## [0.1.0] - 2026-05-10

### Added

**Authentication**
- Multi-token bearer authentication with three source forms: inline (`TESSERA_BEARER_TOKENS=name:token,...`), YAML file (`TESSERA_BEARER_TOKENS_FILE`), and legacy single-token (`TESSERA_BEARER_TOKEN`) for backward compatibility.
- Per-token scope field; `AuthContext.scope` keys the audit hash chain so each token gets an isolated event stream.
- Constant-time token comparison via `secrets.compare_digest` to prevent timing attacks.
- Dev-mode bypass when no tokens are configured: requests pass as `anonymous`, a `WARNING` is logged at startup and every 60 seconds thereafter.
- Dedicated read-only metrics token via `TESSERA_METRICS_TOKEN` (falls back to main token list when unset).

**Policy engine**
- Pure-Python policy engine — no OPA, no Rego. Evaluates YAML policy files directly.
- 16 conditions: `arg_equals`, `arg_greater_than`, `arg_less_than`, `arg_matches_regex`, `arg_in_set`, `arg_contains_pattern`, `arg_size_greater_than`, `tool_name_in`, `action_class_in`, `intent_class_in`, `intent_purpose_matches`, `region_in`, `time_of_day_outside`, `meta_field_equals`, `any_of`, `none_of`.
- First-match-wins evaluation with `priority` ordering (higher = earlier); alphabetical `id` as tie-breaker.
- Missing-argument conditions fail-closed (return `false`). `arg: "*"` iterates every top-level argument.
- Lockdown short-circuit: `runtime.lockdown: true` blocks all traffic before policy evaluation.
- `default_action` config field controls behaviour when no policy matches.

**Enforcement modes**
- Three deployment-wide enforcement modes: `enforcement` (decisions are honoured — block means block), `log_only` (decisions are advisory — upstream always called; `X-Tessera-Mode`, `X-Tessera-Decision`, `X-Tessera-Policy-Id`, and `X-Tessera-Reason` headers injected), `observation` (engine not invoked — pure passthrough with audit).
- `tessera init` scaffolds new deployments with `mode: log_only` by default.
- Mode is not SIGHUP-reloadable; a restart is required to change it.

**Audit log**
- SHA-256 hash chain with per-scope isolation; each token scope maintains its own chain head.
- SQLite persistence (default sink) with WAL journal mode. Schema: `audit_events` table with `scope`, `seq`, `event_hash`, `prev_event_hash`.
- `AuditSink` Protocol for pluggable backends (Postgres sink is a v0.2 deliverable).
- `StdoutSink` for Docker log collection (`audit.also_stdout: true`).
- `BufferedSink` wrapper for operators adding a remote sink.
- Canonical JSON (RFC 8785 JCS) used for deterministic hash input.
- `tessera audit verify` command walks the chain and reports the first broken link (exit codes 0 / 2 / 3).

**Reference policy library** — 7 mode-agnostic policies in `policies/`, each with paired pass/fail test fixtures:
- `cost-cap.yaml` — blocks tool calls that request spend above a configured threshold.
- `prod-protection.yaml` — blocks write/delete actions targeting resources matching a production name pattern.
- `data-residency-eu.yaml` — blocks calls that would write data to regions outside EU boundaries.
- `pii-block.yaml` — blocks calls where arguments match known PII patterns (email, SSN, card numbers).
- `write-action-approval.yaml` — escalates write-class actions to `require_approval` in enforcement mode.
- `read-only-mode.yaml` — blocks any non-read action across all upstreams.
- `secret-leak-block.yaml` — blocks calls where arguments contain credential-shaped strings.

**Reload error isolation**
- Per-file reload isolation: a file that fails validation on reload keeps its previous version in memory; other files reload normally.
- Startup still requires every policy to be valid (exit 2 on any failure).
- `/healthz` exposes `policy_state: {loaded: <int>, errored: [{path, error}]}` for operator visibility without log access.

**Regex safety**
- Policy patterns (`arg_matches_regex`, `arg_contains_pattern`, `tool_pattern`, `intent_purpose_matches`) use the `regex` library (not stdlib `re`) for per-match timeouts.
- 100 ms per-match runtime timeout; on timeout the condition returns `false` and the audit event records `decision_error: regex_timeout`.
- Load-time corpus test in `tessera/policy/regex_safety.py`: each pattern is run against 5 synthetic strings (10 / 100 / 1 000 / 10 000 / 100 000 chars). Patterns that exceed 50 ms are rejected. At startup this causes exit 2; at reload the file is skipped.

**Intent extraction**
- Intent extracted from MCP `_meta.<configured_key>` (default key `tessera_intent`). Fields: `verbs` (required when present), `purpose` (optional, ≤ 1 024 chars).
- Intent-blind agent support: off-the-shelf MCP clients (Cursor, Claude Desktop, Windsurf) work without `_meta.tessera_intent`. Policies with `match.require_intent: true` are skipped for intent-blind calls.
- `intent.required: true` global strict mode blocks all calls that lack an intent declaration.

**Metrics endpoint**
- Prometheus metrics endpoint at `/metrics`. Disabled by default (`metrics.enabled: false`).
- When enabled, bearer authentication is required (dedicated `TESSERA_METRICS_TOKEN` or any main-list token).
- Labels: `requests_total{outcome}`, `decisions_total{action,policy_id,mode}`, `audit_emit_failures_total`, `upstream_request_duration_seconds`, `regex_timeout_total{policy_id}`.

**CLI** (Typer-based, entry point `tessera`):
- `tessera serve` — start the proxy; `--config`, `--policy-dir`, `--bind`, `--log-level`.
- `tessera audit verify` — walk the hash chain; `--audit-path`, `--scope`, `--all`, `--json`.
- `tessera policy test` — run fixture decisions against loaded policies; `--policy-dir`, `--fixture`, `--fixture-dir`, `--json`.
- `tessera policy lint` — validate all YAML policies and run the ReDoS corpus test; `--policy-dir`, `--json`.
- `tessera version` — print version, git SHA, and Python version; `--json`.
- `tessera init` — scaffold `tessera.yaml` (with `mode: log_only`), `policies/`, and `tokens.example.yaml` into a directory; `--dir`, `--force`.

**Docker image**
- Multi-stage Dockerfile: `python:3.12-slim` builder and runtime stages.
- Non-root `tessera` user (uid/gid 10001).
- HEALTHCHECK polls `/healthz` every 30 s.
- Target image size ~150 MB (no OPA runtime).
- Published to `ghcr.io/cloudmorphai/tessera:0.1.0`.

**Pluggable extension points** — three `Protocol` interfaces for Tessera Cloud and custom deployments:
- `PolicyLoader` — `load_all(scope)` + `watch(scope, callback)`.
- `AuditSink` — `emit(event)`, `close()`, `head_hash(scope)`, `iter_events(scope)`.
- `Authenticator` — `authenticate(request) -> AuthContext`.
- Selected via `TESSERA_POLICY_LOADER`, `TESSERA_AUDIT_SINK`, `TESSERA_AUTHENTICATOR` env vars (`module:Class` format, resolved by `tessera/pluggable.py` at startup).

**Configuration**
- `tessera.yaml` runtime config with env-var overrides (`TESSERA_*` prefix) and `${VAR}` interpolation in upstream credential values.
- SIGHUP reloads policies (per-file) and re-reads `runtime.lockdown`; all other fields require a restart.
- JSON Schemas for policy (`schemas/policy.schema.json`), audit event (`schemas/audit_event.schema.json`), and config (`schemas/config.schema.json`).

**Documentation**
- `README.md` with 5-minute quickstart (`log_only` → `enforcement` walkthrough).
- `docs/INSTALL.md`, `docs/POLICIES.md`, `docs/CONFIGURATION.md`, `docs/INTEGRATIONS.md`.
- `docs/AUDIT.md` — audit event schema, `verify` usage, hash chain guarantees, SQLite → Postgres migration path.
- `docs/TROUBLESHOOTING.md` — common issues and remediation steps.
- `docs/ROADMAP.md` — features deferred to v0.2 with rationale.

[0.1.1]: https://github.com/CloudMorphAI/cloudmorph-tessera/releases/tag/v0.1.1
[0.1.0]: https://github.com/CloudMorphAI/cloudmorph-tessera/releases/tag/v0.1.0
