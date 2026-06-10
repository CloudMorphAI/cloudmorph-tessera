# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).



## [0.8.0] - 2026-06-10

### Added — unified MCP entry point (`POST /mcp`)

- **Unified MCP route** (`tessera/proxy.py` — `POST /mcp`). Single-entry-point proxy that handles all configured upstreams. `tools/list` fans out concurrently to every upstream and returns a merged catalog with tool names namespaced as `<upstream>__<tool>` (e.g. `aws__s3_PutObject`, `gcp__storage_buckets_insert`). `tools/call` parses the namespace, rewrites the tool name to canonical form, and dispatches through the identical policy+audit+forward pipeline as the per-upstream route. Other methods (`initialize`, `ping`, `notifications/*`, etc.) are forwarded to the first configured upstream.

- **Tool namespacing helpers** (`tessera/proxy.py`). `TOOL_NAMESPACE_SEPARATOR = "__"`, `namespace_tool(upstream, tool)`, `parse_namespaced_tool(namespaced)`. Double underscore chosen because single underscore collides with AWS MCP's existing convention (`s3_PutObject`, `ec2_DescribeInstances`).

- **`_run_proxy_pipeline`** (`tessera/proxy.py`). Module-level async helper that encapsulates the full policy+audit+forward pipeline (Steps 3-end from `proxy()`). Used by `proxy_unified` for tools/call dispatch; both routes share identical policy evaluation, decision caching, audit emission, and upstream forwarding.

- **D4 startup validation** (lazy). On first `tools/list` aggregation, validates that no upstream tool name contains `__`. If a collision is detected: logs a warning with the offending tool names and sets `state.unified_mode_disabled = True`. Subsequent `tools/list` calls return a JSON-RPC `-32603` error directing users to the per-upstream `POST /mcp/<upstream_name>` routes. Implemented lazily (not at startup) because the existing code does not fetch tool inventories at startup — adding blocking network calls at startup would be out of pattern.

- **D4 choice**: log warning + DISABLE unified mode (fall back to per-upstream routing) rather than refusing to start. Rationale: refusing to start would break existing v0.7.x deployments that happen to proxy an upstream with `__` in tool names. Disabling unified mode is non-destructive — the per-upstream routes continue working.

- **`install-claude-desktop` unified mode** (`tessera/cli.py`). Matches `install-claude-code` and `install-cursor`: default URL is now `/mcp` (unified), default upstream_name is `tessera`, `--legacy-per-upstream` flag added, `--upgrade` migration removes legacy per-upstream entries and writes the unified entry.

- **Policies use canonical (un-namespaced) tool names** (D2). The namespace is a transport detail; `match.tool_pattern` in `tessera.yaml` continues to reference `s3_PutObject` not `aws__s3_PutObject`. No customer-facing policy change required.

### Changed

- `install-claude-code` and `install-cursor` now default to unified mode (URL = `http://localhost:8080/mcp`, entry key = `tessera`). Previous behavior is available via `--legacy-per-upstream`.
- `install-claude-desktop` updated to match: unified default + `--legacy-per-upstream` + `--upgrade` migration support.
- `docs/INTEGRATIONS.md` updated with v0.8 unified snippets (CLI one-liners + JSON examples). Legacy per-upstream snippets retained.
- `docs/INSTALL.md` version references updated to 0.8.0.

### Compatibility

- `POST /mcp/{upstream_name}` routes are **unchanged** (D3). All v0.7.x IDE configs and direct curl calls to per-upstream routes continue to work without modification.
- Existing policy YAML files require no changes — canonical tool names in `match.tool_pattern` are unaffected by namespacing.

## [0.7.2] - 2026-05-24

### Changed
- **Default OAuth + cloud endpoints switched to `https://auth.tessera.cloudmorph.ai`.**
  Up to v0.7.1 the defaults pointed at `https://tessera.cloudmorph.ai` which
  routes to the ECS ALB (Tessera Cloud SaaS — wrapped-image MCP firewall),
  a separate service from the OAuth Lambda. The server-side fix is a new
  ApiMapping on the `tessera-api-prod` HttpApi (lands in cloudmorph-mono-repo
  commit `338ae27a`); this OSS release flips the client defaults to match.
  - `tessera.cli._OAUTH_DEFAULT_ISSUER`
  - `tessera.cloud_sync._DEFAULT_ENDPOINT`
  - `tessera.audit.cloud_uploader._DEFAULT_ENDPOINT`
  - `tessera.auth.oauth_rs._DEFAULT_TESSERA_JWKS_URL`

  No `--issuer` / `--endpoint` flag is required on `tessera login` /
  `tessera config sync` / `tessera audit upload --once` anymore.

  The `iss` claim of issued tokens stays `tessera.cloudmorph.ai`
  (unchanged — RFC 7519 issuer is an identifier string, not a URL).

### Note
- Pre-0.7.2 installs that already set `tessera login --issuer https://hiya106w1a...`
  will keep working — the raw API Gateway URL is still in the Cognito
  callback allowlist for transition.

## [0.7.1] - 2026-05-24 (hotfix)

### Fixed
- **Bundled OAuth trust anchor missing from wheel.** v0.7.0 shipped without
  `tessera/auth/oauth_pubkey.pem` in the wheel — the `[tool.setuptools.
  package-data]` declaration in `pyproject.toml` only included
  `intelligence/*.pem`, and `MANIFEST.in` only included
  `tessera/intelligence/public_key.pem`. Result on v0.7.0: every call to
  `OAuthResourceServer.validate_bearer_token()` silently fell through to
  the JWKS-fetch path, and the default JWKS URL points at
  `tessera.cloudmorph.ai` which doesn't currently route to the OAuth
  Lambda (v0.7.1 server-side fix is the ApiMapping work, see v0.7.0
  release notes below). 0.7.1 adds `auth/*.pem` to package-data + adds
  `tessera/auth/oauth_pubkey.pem` to MANIFEST.in so the trust anchor is
  bundled.
- **Affected versions**: 0.7.0 only. Workaround for users stuck on 0.7.0:
  `pip install --upgrade cloudmorph-tessera` (this pulls 0.7.1). No
  schema or behavioural change otherwise — 0.7.1 is a packaging-only fix.

## [0.7.0] - 2026-05-24

### Added — control-plane wiring (Item D of v0.7.0 plan)
- **OAuth 2.1 resource server validator** (`tessera/auth/oauth_rs.py:372` — `OAuthResourceServer`). Verifies Ed25519 access tokens against the bundled `tessera/auth/oauth_pubkey.pem` trust anchor with JWKS fetch fallback at `https://tessera.cloudmorph.ai/oauth/.well-known/jwks.json`. Surfaces RFC 9728 protected-resource metadata, RFC 7591 DCR proxy, RFC 7662 introspection, RFC 7009 revocation. `require_scope(scope)` decorator helper for route protection. Gap analysis vs the §7.1 spec at `plan/v0.7.0-item-D-oauth_rs-gaps.md`.
- **Cloud policy sync** (`tessera/cloud_sync.py` — `CloudPolicySync`). Periodic background pull from `/api/cli/policies` into the local SQLite policy cache. Configurable refresh interval (default 5 min). Invalidates the proxy `DecisionCache` on every policy change so a tightened rule takes effect on the very next call. Failure-isolated: cloud unreachable does not break local enforcement.
- **Audit cloud uploader** (`tessera/audit/cloud_uploader.py` — `AuditCloudUploader`). Background batched POST to `/api/tessera/audit/ingest` with exponential-backoff retry. Carries the local hash-chain head per batch; server stores raw and chain-verifies on read.
- **DecisionCache** (`tessera/proxy.py:82` — `DecisionCache`). LRU + 60s TTL memoization on the per-call hot path. Bounded at 1024 entries. Caches `allow` and `observed` decisions only — `block` and `require_approval` are always re-evaluated against current policy. Cache key is `sha256(canonical_json({scope, tool, args}))` so arg-ordering doesn't fragment the cache. Cleared on every `CloudPolicySync` reload.
- **`tessera login` CLI command** (`tessera/cli.py:1189`). Browser-based PKCE flow against the new cloudmorph.ai OAuth server. Spawns a one-shot localhost listener, exchanges the auth code for an access + refresh JWT pair, and persists them at `~/.tessera/oauth.json`.
- **`tessera config sync` CLI command** (`tessera/cli.py:1377`). Force a one-shot pull of policies from cloud, bypassing the periodic timer.

### Fixed (CRITICAL — from code-audit-2026-05-22)
- **Pack status filter** (`tessera/intelligence/client.py:468,484`) now accepts both `"active"` and `"production"`. Before this fix, every pack and every mapping bundle was silently skipped on every `refresh()` call because the production catalog uses `"production"` while the client checked `!= "active"`. Zero packs downloaded in prod for the full v0.6 lifecycle.
- **Scale-tier customers silently downgraded to free** (`tessera/intelligence/license.py:_TIER_ORDER`) — the license validator's tier dict was missing `"scale"`. Customers on Quaestor's Scale plan got `tier="scale"` from the license server, which the validator coerced to `"free"`. Paying customers couldn't access premium packs.

### Changed
- `PackManifest.status` enum extended in code+docstring: `"active" | "production" | "deprecated" | "pre-signed"`.
- `LicenseStatus.tier` Literal type extended to include `"scale"`.
- Package version bump 0.6.0 → 0.7.0 (`pyproject.toml`, `tessera/_version.py`, `docs/INSTALL.md` x5 occurrences).

### Infrastructure (cloud-side, lands in cloudmorph-mono-repo v0.7.0)
- OAuth 2.1 authorization server (Python Lambda at `amplify/backend/tessera/oauth/`) — `/oauth/authorize`, `/oauth/cb`, `/oauth/token`, `/oauth/jwks.json`, `/.well-known/oauth-authorization-server`. Ed25519 (EdDSA) token signing.
- HTTP API Lambda authorizer protecting a new `/api/cli/*` route surface (mirror of `/api/tessera/*` but Tessera-Bearer-protected instead of Cognito).
- `POST /api/tessera/audit/ingest` — Tessera-Bearer-only ingest endpoint, scope `tessera:audit:write`.
- Currently reachable at the raw API Gateway URL `https://hiya106w1a.execute-api.us-east-1.amazonaws.com/...`. v0.7.1 will add an `auth.tessera.cloudmorph.ai` CustomDomain ApiMapping; until then, pass `--issuer https://hiya106w1a.execute-api.us-east-1.amazonaws.com` to `tessera login` (or set `TESSERA_OAUTH_ISSUER`).

### Note
- The `tessera/auth/oauth_pubkey.pem` bundled with this release is the LIVE production Ed25519 public key (`x=uXOv_3nDLTkwOw1bPbSaVw1xWZM7NNREIwXXbAnou_0`). The matching private key is held only in AWS Secrets Manager `tessera/oauth/jwt-signing-key-prod`. Both keys were generated 2026-05-24 in WSL via `cryptography.hazmat.primitives.asymmetric.ed25519`.

### Deferred to v0.7.1 (intentional, not regressions)
- CLI `install-{cursor,claude-code,claude-desktop}` `--use-oauth` flag (current install helpers still wire raw API keys).
- CLI `tessera deeplink` subcommand for IDE-side deeplink registration.
- `authlib` + `cachetools` dependencies — current implementation uses stdlib + the existing `cryptography` dep, so no new wheel additions for v0.7.0.
- ApiMapping for `auth.tessera.cloudmorph.ai` on the `tessera-api-prod` HttpApi so the OAuth URLs are at a stable hostname instead of the raw API Gateway ID.

## [0.6.0] - 2026-05-18

### Added — tri-cloud parity at AWS depth
- Tri-cloud cost-mapping ops expanded to 236 total (78 AWS / 80 Azure / 78 GCP) — Azure +23 in mappings/azure/v1.3.0, GCP +53 in mappings/gcp/v1.1.0.
- Tri-cloud blast-radius rules expanded to 189 total (70 AWS / 59 Azure / 60 GCP). Azure 6→59 (mappings/azure/v1.1.0), GCP 7→60 (gcp/v1.1.0 extended in place), AWS 27→70 (new blast-radius/aws/v1.2.0 with 43 new rules covering identity escalation, cryptographic, data exposure, network expansion, compute escalation, cost runaway). Symmetry ratio max/min = 1.19× across the three clouds.
- Cost-combinations engine — new `CombinationTracker` class in `tessera/cost/combinations.py`. Tracks multi-op call chains within a tenant scope, computes aggregate cost in real-time, exposes policy primitives (`combination.aggregate_cost_usd`, `combination.ops_count`, `combination.principals_count`, `combination.window_seconds`). Async-safe, memory-bounded (1000 chains per tenant, LRU evict beyond).
- 4 new policy condition types: `combination_aggregate_cost_usd_gt`, `combination_ops_count_gt`, `combination_window_seconds_lt`, `combination_id_matches`.
- 45 new combination definitions in `tessera-intelligence/combinations/{aws,azure,gcp}/v1.0.0/` (15 per cloud) with 45 oracle test fixtures.
- 3 new tri-cloud policy packs:
  - `tri-cloud-cost-explosion-defense` v1.0.0 — 10 policies (3 AWS / 3 Azure / 3 GCP / 1 cross-cloud aggregate cap)
  - `tri-cloud-blast-radius-defense` v1.0.0 — 10 policies covering identity / data / crypto across all clouds
  - `multi-cloud-data-exfiltration-defense` v1.0.0 — 8 policies (6 pairwise mirror blocks + 2 cross-cutting)

### Added — new bundle type
- `kind: combination` artifact bundles in `tessera-intelligence/dist/v1.0.0/combinations/` with their own catalog at `catalogs/combination-index.json`. New `--kind combination` mode in `scripts/sign_pack.py`.

### Tested against
- Real AWS account 237509402889 — STS, EC2, Lambda, Amplify forwarding all green
- 5556 tests passing in tessera-intelligence (up from 4862 pre-v0.6.0 — +694 new from tri-cloud expansion + symmetry pass)
- Audit chain integrity preserved across all v0.6.0 test runs
- Benchmarks v0.5.1 numbers still valid for v0.6.0: p50 0.496 ms block-path, 60.1 µs engine eval, ~30 ms Tessera overhead vs direct MCP (within noise)

### Internal
- v0.5.1 streamable-HTTP MCP upstream (originally tagged for separate release) is folded into v0.6.0 — single release covers the transport gap close + the tri-cloud parity work.
- 11 new signed artifacts under production Ed25519 key (signed-manifest count 27→38).
- 4 catalogs updated with new signatures; combination-index.json is a new catalog file live for the first time.
- No changes to public `tessera` API surface.

## [0.5.1] - 2026-05-17

### Added
- `kind: mcp_streamable_http` upstream. Supports FastMCP streamable-HTTP transport
  with session-id handshake and SSE response parsing. Required for proxying in front
  of awslabs.aws-api-mcp-server, Anthropic example MCP servers, and any FastMCP-based
  community MCP server. The existing `kind: bearer` upstream continues to work for
  plain HTTP JSON-RPC servers; no behavior change for existing configs.

### Tested against
- awslabs.aws-api-mcp-server v0.1.2 local in streamable-http mode
- Real AWS account 237509402889, multiple services: S3 (block path), EC2 / STS / IAM /
  Amplify (allow + forward path)
- Audit chain integrity preserved across all 12 test scenarios

### Internal
- New `tessera/integrations/streamable_http/upstream.py` — `StreamableHttpUpstream` class
  registered in `proxy._forward_upstream` match block alongside `aws_mcp` and `bearer`.
- `UpstreamConfig` gains three new optional fields: `auth_header`, `session_timeout_s`,
  `request_timeout_s`. No changes to existing config parsing.
- No changes to public `tessera` API surface (`IntelligenceClient`, `LicenseValidator`,
  `PolicyEngine`, `AuditEmitter` untouched).

## [0.5.0] — 2026-05-16

### Added — 6 new bundled OSS policies (Batch 8)

Out-of-the-box defensive policy bench expanded from 18 to **24 bundled policies**.
New entries in `tessera/policies_default/`:

- **`require-intent.yaml`** (priority 85, `log_only`) — records when
  `_meta.tessera_intent` is explicitly null. (Block-on-missing-intent requires
  a future schema enhancement; the YAML comments the limitation.)
- **`business-hours-only.yaml`** (priority 75, `block`) — blocks `write.create` /
  `write.update` / `write.delete` / `execute.deploy` outside 9am–5pm US Pacific.
- **`oversized-payload.yaml`** (priority 70, `block`) — blocks any args whose
  JSON serializes > 64 KB via `arg_size_greater_than`.
- **`tool-allowlist.yaml`** (priority 65, `block`) — blocks any tool not in the
  shipped allowlist (read-only-by-default starter set; operators override).
- **`prompt-injection-heuristic.yaml`** (priority 80, `block`) — regex bench
  for common prompt-injection signatures (`(?i)(ignore previous|disregard above|system: you are now|jailbreak)`).
- **`non-prod-only.yaml`** (priority 60, `block`) — counterpoint to
  `prod-protection`: blocks writes on resources tagged `environment != prod`.

### Added — 2 new conditions

- **`arg_path_matches_regex`** — dot-path arg access for nested structures
  (e.g., `MetadataOptions.HttpTokens`). Reuses the regex pre-compile pipeline
  from v0.4.0.
- **`sts_chain_depth_greater_than`** — counts AWS assume-role chain depth from
  `_meta.aws_session_chain` (list). Fail-closed don't-block when meta absent.

### Changed — proxy method evaluation

- `resources/read` and `sampling/createMessage` are now **engine-evaluable**
  (gated by new config flag `policies.engine_eval_data_methods: bool = false`).
  When opted in, those methods flow through `engine.evaluate()` with a
  synthesized tool_call context. Default `false` for backward compat with
  v0.4.x.

## [0.4.0] — 2026-05-16

### Added — Observability subsystem (Batch 4)

- New `tessera.observability` subpackage:
  - `metrics.py`: Prometheus histograms with labels (upstream, mode, cost_source, action):
    `tessera_decision_latency_seconds`, `tessera_audit_emit_latency_seconds`,
    `tessera_blast_radius_prefetch_latency_seconds`, `tessera_cost_prefetch_latency_seconds`
    + counters `tessera_decisions_total`, `tessera_audit_emit_failures_total`.
    Stub fallback when `prometheus_client` missing (zero-cost no-op).
  - `tracing.py`: OpenTelemetry integration. **OFF by default per Q3 lock** —
    `TESSERA_OTEL_ENABLED=1` to enable. `@trace()` decorator is zero-cost when
    disabled (sync + async variants). `init_tracer()` from lifespan startup;
    OTLP exporter via `TESSERA_OTEL_ENDPOINT`.
  - `events.py`: Protocol-based `OnDecision` / `OnAuditEmit` hook registry.
    Hooks fire async fire-and-forget; failures logged + swallowed.
- `proxy.py` instrumented: latency histograms wrap policy.evaluate, cost prefetch,
  blast-radius prefetch, audit emit. `fire_on_decision` runs registered hooks
  after every decision.
- Audit event payload now carries `conversation_id` (read from
  `_meta.tessera_intent.conversation_id` or `_meta.conversation_id`); declared
  in `schemas/audit_event.schema.json`.
- New `[observability]` extra: `prometheus_client>=0.20.0`,
  `opentelemetry-sdk>=1.20.0`, `opentelemetry-exporter-otlp-proto-http>=1.20.0`.

### Performance — hot-path correctness (Batch 4)

- **JWKS pre-warm**: lifespan startup calls `prewarm_jwks_cache()` so the first
  request after cache rotation no longer blocks the event loop on a sync
  `httpx.get`. Failures swallowed + WARNING logged; on-demand fetch fallback.
- **Regex pre-compile at load**: `validate_pattern()` returns the compiled
  `regex.Pattern`; the loader stores it on `Policy` / `MatchSpec`. Per-request
  evaluators reuse the compiled object instead of re-compiling on every match.
- **Condition cost-tier ordering**: `loader._sort_conditions` sorts each policy's
  `when` list cheap-first (arg_equals / tool_name_in / arg_in_set before regex
  before semantic conditions before blast_radius / predicted_cost). Composite
  `any_of` / `none_of` sorts recursively. Cuts short-circuit time when the
  policy load includes mixed-cost conditions.

### Added — OAuth surface (Batch 9)

- `POST /revoke` endpoint (RFC 7009): validates HTTP Basic auth, marks token
  revoked in `InMemoryRevocationStore`, returns 200 regardless per spec §2.2.
  `RevocationStore` Protocol for production Redis/DDB swap.
- `POST /introspect` now Ed25519/RSA signature-verifies the inbound JWT against
  the configured JWKS cache. Verification failure returns `{"active": false}` +
  emits `event=oauth_introspect_sigverify_failed`. Revoked tokens via the new
  store also return `{"active": false}`.
- `POST /register` per-IP token-bucket rate limiter (default 10/min,
  configurable via `TESSERA_DCR_RATE_LIMIT`). Returns 429 + `Retry-After`
  header on exceed. `RateLimiter` Protocol for production Redis swap.

### Added — Tooling (Batch 9)

- `scripts/bump_version.py` — single-command version bump across the 5 canonical
  sites (pyproject + `__init__.py` + README + INSTALL + CHANGELOG) with semver
  validation, downgrade refusal, `--dry-run`, `--validate` modes.
- `tessera/_version.py` — reads version from installed package metadata via
  `importlib.metadata.version`; literal fallback for dev environments.
  `tessera/__init__.py` re-exports from `_version.py` (the literal in
  `_version.py` is kept in sync by `scripts/bump_version.py`).

### Added — Benchmarks (Batch 6)

- New `benchmarks/` directory:
  - `decision_latency.py` (pytest-benchmark): 7 microbenches against the 18-policy
    default set. Measured: **p50 15–72µs** (well under the 500µs target).
  - `blast_radius_latency.py` (standalone): prefetch ON vs OFF, 100ms simulated
    IAM RTT. Measured: **~797× speedup** (0.1ms cache-hit vs 100.6ms inline).
  - `rps_sustained.py` (locust): 5-step ramp (10/50/100/200/500 concurrent
    users), 60s windows. Founder-run pre-tag for publishable RPS numbers.
  - `README.md` + `results/v0.4.0.md`.
- New `.github/workflows/bench.yml`: runs decision_latency + blast_radius on
  every tagged release, commits results to `benchmarks/results/<tag>.md`.

### Fixed — Code quality (Batch 9)

- **mypy clean**: 49 errors → **0**. Fixes include explicit `FastAPI` typing
  for `oauth_rs.py` decorators (closes 4 untyped-decorator), `isinstance` loop
  for `anthropic` SDK content union (closes 9 union-attr), `dict[str, Any]`
  generic args throughout `tessera/llm/*.py`, narrowed `errors_raw` for
  `len(errors)` in `intelligence/client.py`, and `[[tool.mypy.overrides]]`
  blocks for optional-extra SDK modules. Added `types-PyYAML` +
  `types-python-jose` to `[dev]` extra.
- **ruff clean**: 11 deferred errors → **0**. F841 unused-locals renamed `_`,
  N806/N818 renames, ASYNC240 `# noqa` on test-only sync Path calls, SIM117
  merged nested `with` statements, RET504 collapsed returns, C408 literal.

### Removed (BREAKING)

- **`tessera.cost.aws_mapping`** is **removed** (per Q1 locked decision: deprecated
  in v0.3.0 with `DeprecationWarning`, removed in v0.4.0). Customers must migrate
  to `tessera.cost.cost_for_call()` (introduced in v0.3.0). The `InfracostQuery`
  dataclass moved to `tessera.cost.types` (re-exported via `tessera.cost`).
  `_BUILTIN_MAPPING` canonical-name list moved to data-only
  `tessera.cost._aws_canonical_ops.BUILTIN_AWS_OPS`.

### Architecture docs

- `arch/status/proxy-enforcement-and-audit.md`: observability section
  documenting metrics + tracing + events + the OTel-off-by-default contract.
- `arch/status/policy-engine.md`: regex pre-compile + condition cost-tier
  ordering notes; documents the call_aws reverse-resolution dispatch.

## [0.3.0] - UNRELEASED

### Added — Unified cost API (Batch 2)

- New `tessera.cost.cost_for_call(operation, args, region) -> CostResult` is
  the canonical entry point for cost-aware policies. Routes by operation
  prefix (`aws_*` / `azure_*` / `gcp_*`) to a registered `PriceTable`.
- New `tessera.cost.types.CostResult` dataclass — `price_usd`, `unit`,
  `confidence_band`, `source` (`"price_table"` / `"infracost_live"` / `"miss"`),
  `operation`. Adapts both `PriceTable.cost_for_call()` and
  `InfracostClient.query_sku()` into one consumer shape.
- `IntelligenceClient._load_price_tables_from_cache` now scans for and loads
  AWS + **Azure + GCP** price-table artifacts (previously AWS-only). Each
  artifact's Ed25519 signature is verified against the bundled
  `public_key.pem` before registration.
- Audit-event payload carries `cost_source` and `cost_band` whenever a cost
  was prefetched. Updates `schemas/audit_event.schema.json`.

### Added — AWS MCP translation layer (Batch 3)

- New `tessera.integrations.aws.cli_translator` bridges canonical Tessera
  names (`aws_iam_PassRole`) to the official `awslabs/mcp/aws-api-mcp-server`'s
  single `call_aws` tool. 25 explicit per-op handlers cover the priv-esc-
  and cost-sensitive surface; a generic fallback derives `aws <service>
  <kebab-verb> --kebab-key value` for the long-tail.
- `AWSMcpUpstream` gains `_translate_call_aws_op`. New config fields on the
  upstream: `aws_mcp_server` (set to `"aws-api-mcp-server"` to opt-in) and
  `aws_mcp_routing` ∈ `{specific-first, call-aws-only}` (default
  `specific-first`).
- Reverse-resolver in `tessera/policy/matchers.py`: when an inbound
  `tool_call.name == "call_aws"`, the matcher reverse-resolves
  `arguments.command` to canonical (e.g., `aws_iam_PassRole`) so policies
  authored against canonical names fire correctly. Cached on
  `context["_effective_tool_name"]`.
- Audit-event payload carries both `canonical_tool_name` (inbound raw) and
  `effective_tool_name` (resolved canonical or unchanged).
- `tessera.cost.aws_mapping.InfracostQuery` learns `official_mcp_tool_name`
  + `official_mcp_server` optional fields (parsed from the 2026-05-16
  reconciliation work in `tessera-intelligence`).

### Added — Adoption examples (Batch 5)

- `examples/wrap_anthropic_sdk/` — Anthropic Claude tool-use → Tessera → MCP
  upstream. Uses the `mcp_servers` kwarg from anthropic-python ≥ 0.40.
- `examples/wrap_openai_sdk/` — OpenAI tools API with explicit
  manual dispatch through Tessera. README explains the difference vs MCP-
  native SDKs.
- `examples/wrap_langchain/` — LangChain `create_tool_calling_agent` with a
  custom `MCPToolNode` (`tessera_tool_wrapper.py`) that forwards through
  Tessera. Tested with `ChatAnthropic`; swap-in for OpenAI documented.
- `examples/wrap_claude_code/` — `~/.claude.json` MCP-server entry pointing
  at Tessera. Auto-installable via `tessera install-claude-code`.
- `examples/wrap_vscode_copilot/` — `.vscode/settings.json` for VS Code 1.99+
  Copilot Chat / Continue / Cline (and any MCP-aware extension).
- `recipes/generic-shell-hook.md` — 20-line bash wrapper around
  `/mcp/<upstream>` for tools/CLIs without bespoke MCP support.
- New `.github/workflows/examples-smoke.yml` matrix validates each example's
  policy YAML + tessera config + AST-parses every `.py` file in CI.
- `README.md` gains a "How customers use Tessera" section with an ASCII flow
  diagram + links to all 5 wrap-examples.

### Deprecated

- `tessera.cost.aws_mapping` raises `DeprecationWarning` at import. The
  legacy `aws_mapping._BUILTIN_MAPPING` + `map_request()` direct-call path
  remains callable through v0.3.x as a fallback and is **scheduled for
  removal in v0.4.0**. Migrate to `tessera.cost.cost_for_call()`.
- `InfracostClient` itself stays supported as a fallback indefinitely. Only
  the direct-per-call pattern is deprecated; `InfracostClient` underlies the
  `source="infracost_live"` path of `cost_for_call`.

### Changed

- `context["cost_cache"]` now stores `CostResult` objects (was: raw floats).
  Test fixtures that populate the cache get a one-line migration helper
  (`tests/integration/test_reference_policies.py:_wrap_cost_cache` +
  `test_engine_v020.py:_ctx`) that wraps legacy floats into `CostResult`.
- `_evaluate_predicted_cost` now asserts `"cost_cache" in context`
  to surface missing-context calls during refactors.

## [0.2.1] - UNRELEASED

### Fixed (cross-repo audit 2026-05-14 — closed 2026-05-15)

- **Tier ordering**: `IntelligenceClient._TIER_ORDER` now carries `"scale": 2`
  (the canonical name on pricing.cloudmorph.ai and the license server). `"team"`
  is preserved at the same rank as a backward-compat alias for customers
  upgrading from 0.2.0.
- **Mapping bundle URL**: `_parse_catalog` no longer falls back to the
  non-existent `mapping_url` field. The producer-side
  `catalogs/mapping-index.json` uses `bundle_url`; the consumer now reads it.
- **Manifest signature verification**: `_download_and_extract` now fetches the
  per-pack signed `manifest.json` via the catalog's `manifest_url`, recomputes
  the canonical-JSON content_hash with signed fields zeroed, asserts it matches
  the stored hash (tamper detection), and Ed25519-verifies the base64 signature
  against the content_hash bytes — mirroring `tessera-intelligence/scripts/sign_pack.py`.
  The earlier flow trusted the catalog-declared `content_hash` as if it were the
  tarball hash, which it never was.
- **Tarball hash check**: `tarball_sha256` is now read from the **verified**
  manifest (not the catalog) and is the authoritative integrity check for the
  downloaded tarball. The pre-existing `_verify_tarball_hash` is now invoked
  with that authoritative value.
- **Signature decoding**: `_verify_signature` now uses `base64.b64decode` (was
  `bytes.fromhex`). The producer-side `sign_pack.py` emits base64; the consumer's
  hex-decoding silently failed against real producer output. The bug was masked
  because catalog signature verification was opt-in until P0-17 landed.

### Added

- **`tests/integration_cdn_smoke.py`** — end-to-end CDN license-gating matrix (8
  scenarios: anonymous, developer, scale, and enterprise JWT tokens against
  free/scale/enterprise-tier packs). Gated behind `TESSERA_INTEGRATION_TESTS=1`
  env var; excluded from the normal CI matrix.  Also includes an `xfail`
  placeholder for a typed-error test pending a `fetch_pack()` method on
  `IntelligenceClient`.
- **`PyJWT>=2.8.0`** as an explicit dependency. `/oauth/introspect` imports
  `jwt` (PyJWT); previously it was a transitive of `mcp` / `msal` and could
  silently disappear on a minor version bump of those packages.

### Changed

- **`PackManifest` dataclass** gained two fields: `manifest_url: str = ""` (URL
  of the per-pack signed `manifest.json`) and `tarball_sha256: str = ""`
  (populated after fetching + verifying the signed manifest, used by
  `_download_and_extract` to verify the tarball it just downloaded).
- **`_parse_catalog`** now reads from the producer-correct catalog keys:
  `packs` for packs (unchanged) and `mapping_bundles` for mappings (was
  `mappings` — a typo that worked accidentally because the catalog had a
  legacy key that has since been deprecated). Both old and new keys are
  accepted for backward compat.

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
