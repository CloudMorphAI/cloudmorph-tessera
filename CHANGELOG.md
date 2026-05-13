# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - UNRELEASED

This entry tracks the in-progress v0.2.0 release.

### Breaking changes

- **Default bind address flipped from `0.0.0.0:8080` to `127.0.0.1:8080`**
  (`tessera/config.py:ListenConfig.host`). Existing deployments needing
  non-loopback exposure must explicitly set `listen.host: 0.0.0.0` in
  `tessera.yaml`, pass `--bind 0.0.0.0:8080` to `tessera serve`, or set the
  `TESSERA_BIND_HOST=0.0.0.0` environment variable. (A-4-1.)
- **`install-claude-code` now refuses to overwrite** an existing
  `mcpServers[upstream_name]` entry in `~/.claude.json` without `--upgrade`.
  (A-4-8.)
- **`BufferedSink` removed from public exports** (`tessera/audit/__init__.py`).
  Import directly from `tessera.audit.sinks._buffered` if needed. The source
  file has been renamed `tessera/audit/sinks/_buffered.py` (underscore-prefix
  marks it as internal). (A-4-9.)

### Features

- **Management-plane SSO via Clerk (OIDC)** — per OQ-2. `OIDCAuthenticator`
  (`tessera/auth/oidc.py`) validates JWT bearer tokens against a JWKS endpoint
  with configurable TTL-based key caching and auto-re-fetch on unknown `kid`.
  Configured under `auth.management_plane` in `tessera.yaml`. Supports Clerk,
  Auth0, Cognito, and any custom OIDC provider. Exposed as
  `app.state.management_plane_authenticator` at startup; reserved for
  `/app/*` routes in v0.2.x. (A-2-1, A-2-2.)
- **JWT validator mode for MCP traffic** (`tessera/auth/jwt_mcp.py`). Set
  `auth.type: jwt` to authenticate MCP client requests with signed JWTs
  (Entra, Okta, Cognito). Shared JWKS validation logic extracted to
  `tessera/auth/_jwks.py`. `principal_claim` (default `sub`) and `scope_claim`
  (default `scope`) are configurable. Requires `pip install
  "cloudmorph-tessera[oidc]"`. (A-3-1.)
- **Reference policy split (OQ-3)**: 7 vendor-specific policies migrated to
  `tessera-intelligence/packs/vendor-mcp-protection/`. OSS repo now retains 7
  generic policies + 5 new AWS-illustrative examples = 12 total. (A-9-1.)
- **5 AWS-illustrative reference policies** (`policies/aws-*-EXAMPLE.yaml`):
  `aws-ec2-cost-cap-EXAMPLE`, `aws-iam-blast-radius-EXAMPLE`,
  `aws-region-allowlist-EXAMPLE`, `aws-cost-runaway-stop-EXAMPLE`,
  `aws-bedrock-cost-ceiling-EXAMPLE`. Illustrate `predicted_cost`,
  `blast_radius`, `cumulative_spend_today` semantic conditions (full
  implementation in Tessera Cloud `aws-cost-aware-defaults` pack). (A-9-2.)
- **`kind: aws_mcp` upstream** (`tessera/integrations/aws/upstream.py`). AWS
  IAM-signed MCP server routing via `mcp-proxy-for-aws`. Configure with
  `kind: aws_mcp`, `aws_region`, and optionally `aws_service` /
  `aws_endpoint_override` in `tessera.yaml`. Credentials resolved via boto3
  chain — no Tessera config for credentials. Install with
  `pip install "cloudmorph-tessera[aws]"`. (A-1-1, A-1-2, A-1-4.)
- **`--default-action` flag for `tessera policy test`** (`tessera/cli.py`).
  Accepts `allow|block|log_only|require_approval`. Without the flag, a WARN
  is printed to stderr noting that the production default is `block`. (A-4-3.)
- **Multi-token Cursor hook propagation** (`tessera/integrations/cursor_hooks.py`).
  `_resolve_bearer_token()` walks the same 3-source precedence as
  `build_token_list()`. `TESSERA_CURSOR_TOKEN_NAME` env var selects a named
  token. `tessera install-cursor-hooks --token-name <name>` injects it. (A-4-4.)
- **`fail_closed` Cursor hook config** (`tessera/integrations/cursor_hooks.py`).
  When `TESSERA_CURSOR_FAIL_CLOSED=true`, an unreachable Tessera proxy causes
  `handle_before` to return `deny` instead of failing open. Wire via
  `tessera install-cursor-hooks --fail-closed`. (A-4-5.)
- **Pluggable backends wired into runtime** (`tessera/proxy.py:_lifespan`).
  `TESSERA_AUTHENTICATOR`, `TESSERA_AUDIT_SINK`, `TESSERA_POLICY_LOADER` env
  vars are now consulted at startup via `pluggable.resolve()` before
  instantiating the default classes. Documented in `docs/CONFIGURATION.md`
  §8. (A-4-10.)
- **`passthrough_data_leak_candidate` audit events** (`tessera/proxy.py`).
  The 5 data-exfil-risk pass-through methods (`prompts/get`, `resources/read`,
  `resources/subscribe`, `completion/complete`, `sampling/createMessage`) now
  emit an additional audit event with method, truncated params, principal_id,
  and scope. Controlled by `audit.flag_data_leak_passthrough: bool` (default
  `True`). (A-PRE-4, OQ-1.)
- **Optional dependency groups** (`pyproject.toml`). Added `aws`, `gemini`,
  `anthropic`, `openai`, `bedrock`, `azure-openai`, `oidc`, `all-llm`,
  `intelligence`, `infracost` groups. (A-1-1, A-10-1.)
- **`CursorHooksConfig` and `IntegrationsConfig`** nested under `TesseraConfig`
  for future cursor_hooks YAML config (`tessera/config.py`). (A-4-5.)
- **`audit.flag_data_leak_passthrough`** field on `AuditConfig` (default
  `True`). Allows operators to suppress the extra audit event if noisy. (A-PRE-4.)

### Fixes

- **`HashChain.restore_head` auto-called on lifespan startup** (`tessera/proxy.py`).
  On startup, `SqliteSink.iter_scopes()` is used to enumerate persisted scopes;
  `head_hash()` is called per scope and fed into `chain.restore_head()`. This
  ensures the hash chain is continuous across process restarts. (A-4-6.)
- **`_action_verbs.yaml` user mappings wired into engine** (`tessera/proxy.py`).
  If `<policies_dir>/_action_verbs.yaml` exists, `load_user_mappings()` is
  called at startup and the results are merged into the module-level
  `_user_mappings` dict before policies are loaded. `verbs_for()` now consults
  user mappings first. (A-4-2.)
- **`SqliteSink.iter_scopes()`** added to return all distinct scope values
  from the audit database. Used by the chain-restore code path. (A-4-6.)

### Documentation

- **`docs/CONFIGURATION.md`** — new "## 9. Management-plane SSO" section with
  Clerk, Auth0, and Cognito config examples; Bearer-vs-OIDC decision matrix.
  New "## 10. MCP traffic JWT mode" section with Entra, Okta, and Cognito
  config snippets. (A-2-4, A-3-3.)
- **`policies/README.md`** — rewritten for v0.2.0 catalog (12 policies: 7
  generic + 5 AWS-illustrative). Mentions vendor-7 migration to premium pack.
  (A-9-3.)
- **`README.md`** — rewritten with deterministic-positioning hero paragraph
  ("the deterministic cost and blast-radius firewall for AI agents on AWS"),
  "What's New in v0.2.0" section, and AWS Quickstart with `tessera.yaml` sample.
  Policy catalog updated from 14 to 12 (vendor-7 → premium pack). (A-10-5.)
- **`docs/INTEGRATIONS.md`** — new "## AWS MCP Server" section with `kind:
  aws_mcp` config block, boto3 chain explanation, and AWS Activate link.
  (A-1-5.)
- **`docs/CONFIGURATION.md`** — new "## 8. Pluggable backends" section
  documenting `TESSERA_AUTHENTICATOR`, `TESSERA_AUDIT_SINK`,
  `TESSERA_POLICY_LOADER` with example usage. (A-4-10.)
- **`docs/TROUBLESHOOTING.md`** issue 8 (bearer-token rejection) rewritten to
  reference the actually-supported env vars. (A-4-7.)
- **`docs/TROUBLESHOOTING.md`** issue 9 (upstream timeout) references the
  correct config field `upstreams[].timeout_seconds`. (A-4-7.)
- **`docs/INSTALL.md`** bind-mount cheatsheet now references non-root UID
  `10001`. (A-4-7.)
- **`docs/CONFIGURATION.md`** `policies.reload` field documentation removes
  the unimplemented `sighup` option. (A-4-7.)

### Version

- `tessera/__init__.py:__version__` bumped to `"0.2.0"`. (A-10-6.)

### CI / Build

- **Dockerfile** base image pinned to SHA-256 digest for reproducible builds.
  `pip install` now installs `[aws,gemini,oidc,intelligence,infracost]` extras
  by default. `TODO(FOUNDER)` block removed. (A-10-2.)
- **`release.yml`** multi-arch `linux/amd64,linux/arm64` buildx added to the
  `sign` job. `docker/setup-qemu-action@v3` and `docker/setup-buildx-action@v3`
  were already present; `platforms` added to `build-push-action`. SBOM job uses
  `cyclonedx-bom==7.3.0`. Attest job uses `cosign attest`. (A-10-3, A-10-7.)

### Not yet landed (deferred to follow-up sessions)

- **A-5 series** — seven new semantic condition types (`predicted_cost`,
  `blast_radius`, `affected_resource_count`, `data_volume`,
  `cumulative_spend_today`, plus `time_of_day_outside` and `region_in`
  documentation).
- **A-6 series** — Infracost GraphQL client, AWS mapping shim, license-gated
  extended mappings, `tessera pricing serve` CLI wrapper.
- **A-7 series** — Gemini policy-authoring CLI (`tessera policy author`,
  `tessera analyze`), stub providers for Anthropic / OpenAI / Bedrock /
  Azure OpenAI.
- **A-8 series** — `tessera/intelligence/` client subsystem (catalog fetch,
  Ed25519 signature verification, pack download, cache management, license
  tier gating).
- **A-9 series** — 5 new AWS-illustrative reference policies; migration of
  7 vendor-specific policies to `tessera-intelligence/packs/vendor-mcp-protection/`
  per OQ-3.
- **A-10 series** (partial) — Dockerfile base-image SHA pinning +
  `[aws,gemini]` extras, multi-arch image verification, README update,
  `release.yml` end-to-end run, PyPI + GHCR publish.

## [0.1.1] - 2026-05-11

### Fixed

- **JSON-RPC error response shape.** When Tessera blocked a tool call, the
  `tessera_audit_event_id` was being injected at the top level of the response
  next to `error`. That's not JSON-RPC 2.0 spec-compliant. Strict MCP clients
  (Claude Code's Zod validator, the official MCP SDK) rejected the whole
  response and reported a transport-layer failure instead of surfacing the
  block reason. The fix nests the audit id under `error.data._meta` instead.
  Discovered during Claude Code integration testing.
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
- Labels: `requests_total{outcome}`, `decisions_total{action,mode}`, `audit_emit_failures_total`.

**CLI** (Typer-based, entry point `tessera`):
- `tessera serve` — start the proxy; `--config`, `--policy-dir`, `--bind`, `--log-level`.
- `tessera audit verify` — walk the hash chain; `--audit-path`, `--scope`, `--all`, `--json`.
- `tessera policy test` — run fixture decisions against loaded policies; `--policy-dir`, `--fixture`, `--fixture-dir`, `--json`.
- `tessera policy lint` — validate all YAML policies and run the ReDoS corpus test; `--policy-dir`, `--json`.
- `tessera version` — print version, git SHA, and Python version; `--json`.
- `tessera init` — scaffold `tessera.yaml` (with `mode: log_only`), `policies/`, and `.env.example` into a directory; `--dir`, `--force`.

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
