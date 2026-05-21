# File inventory — cloudmorph-tessera

**Generated**: 2026-05-21 by the arch-scan-2026-05-21 pass (Opus 1M parent + Sonnet sub-agents).
**Branch**: `feature/arch-scan-2026-05-21` (branched from `main` @ `e4f797a`).
**Source-of-truth scan output**: `/tmp/arch-scan-cloudmorph-tessera.md` (kept locally, not committed).

## How to use this file

Canonical per-file reference for the OSS Tessera Python package. For every meaningful file under the repo, the table below names its inputs, outputs, and process in one or two sentences. This is the file future Claude Code sessions should grep first when answering "what does X do" before falling back to `arch/status/*.md` (which is shorter and slice-focused).

When a file is renamed or removed, the entry here MUST be updated in the same change that touches the code. Drift between this file and the filesystem is a documentation bug.

The drift items the scan turned up (e.g., `cost/aws_mapping.py` ghost reference in status docs, undocumented v0.6 combinations engine, undocumented v0.5.1 streamable-HTTP upstream, `_TIER_ORDER` mismatch between `license.py` and `client.py`) are tracked in `arch/improvements/arch-scan-*.md` for triage.

## Section 1 — File tree (top 3 levels)

```
cloudmorph-tessera/                   # OSS MCP firewall package (PyPI: cloudmorph-tessera)
├── tessera/                          # Main Python package (import: tessera)
│   ├── __init__.py                   # Version re-export only
│   ├── _version.py                   # Importlib.metadata + literal fallback
│   ├── cli.py                        # Typer CLI: serve / audit / policy / pricing subcommands
│   ├── config.py                     # Pydantic TesseraConfig + YAML loader + env overrides
│   ├── errors.py                     # TesseraError hierarchy (6 types)
│   ├── intent.py                     # _meta.tessera_intent extraction + validation
│   ├── pluggable.py                  # importlib resolver for Protocol extension points
│   ├── proxy.py                      # FastAPI app — MCP interception hot path
│   ├── audit/                        # Hash-chained audit log subsystem
│   ├── auth/                         # Authenticator implementations (bearer, JWT, OIDC, OAuth RS)
│   ├── cost/                         # Cost evaluation (price tables, Infracost, combinations)
│   ├── integrations/                 # Adapters: cursor hooks, AWS MCP, streamable HTTP upstream
│   ├── intelligence/                 # CDN consumer + Ed25519 verify + license validator
│   ├── llm/                          # Off-hot-path LLM policy authoring (7 providers)
│   ├── observability/                # Prometheus + OpenTelemetry (optional deps)
│   ├── policies_default/             # 24 bundled YAML policies
│   ├── policy/                       # Pure-Python policy engine
│   └── state/                        # Local stateful backends (DailySpendState)
├── tests/                            # Pytest: unit + integration + property + scenarios
├── arch/                             # Living documentation (NOT shipped to customers)
├── benchmarks/                       # Microbenchmarks (decision latency, RPS)
├── docs/                             # User-facing documentation
├── examples/                         # Integration examples
├── policies/                         # Canonical reference policies
├── recipes/                          # Integration recipe guides
├── schemas/                          # JSON Schema contracts (audit_event, config, policy)
├── scripts/                          # Developer tooling (bump_version.py)
├── Dockerfile                        # Two-stage build, python:3.12-slim@sha256 pinned
├── Makefile                          # sbom + docker-build-repro targets
├── CHANGELOG.md                      # Keep-a-Changelog format, v0.6.0 current
├── pyproject.toml                    # Build config, deps, version 0.6.0
├── tessera.example.yaml              # Example config file
└── tokens.example.yaml               # Example bearer-tokens file
```

## Section 2 — File inventory

### Core package — `tessera/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/__init__.py` | source | — | `__version__` re-export | Single-line re-export from `_version.py`. Public package surface. |
| `tessera/_version.py` | source | `importlib.metadata` (package name `cloudmorph-tessera`) | `__version__` string | Reads installed package metadata; falls back to literal `"0.6.0"` in dev mode. Must stay in sync with `pyproject.toml`. |
| `tessera/cli.py` | source | CLI args (typer), `tessera.yaml` path, `--bind`, `--log-level`, policy dir | stdout, uvicorn server, exit codes | Typer CLI defining `serve`, `audit` (tail/verify-chain/export/inspect), `policy` (author/analyze/test), `pricing`, `init`, `install-cursor-hooks`, `install-claude-code` subcommands. |
| `tessera/config.py` | source | `tessera.yaml`, env vars (`TESSERA_*`), `${VAR}` interpolation | `TesseraConfig` pydantic model | 15+ Pydantic sub-models covering listen, auth, audit, policies, intent, metrics, credentials, upstreams, cursor_hooks, intelligence, infracost, state, combinations. Performs YAML load + env substitution. |
| `tessera/errors.py` | source | — | Exception classes | Six types: `TesseraError`, `ConfigError`, `PolicyError`, `AuditSinkError`, `UpstreamError`, `UnauthorizedError`, `TamperDetected`. |
| `tessera/intent.py` | source | `params._meta` dict, `meta_key` string, `known_verbs` frozenset | intent dict or `None`; raises `PolicyError` on malformed | Extracts and validates `tessera_intent` from MCP `_meta`. Validates `verbs` list and optional `purpose` field (≤1024 chars). |
| `tessera/pluggable.py` | source | `"module.path:ClassName"` env var string | Python class | Resolves extension-point classes via `importlib`. Used for `TESSERA_AUTHENTICATOR`, `TESSERA_AUDIT_SINK`, `TESSERA_POLICY_LOADER`. |
| `tessera/proxy.py` | source | FastAPI `Request`, `TesseraConfig`, upstream MCP servers | FastAPI `Response`, audit events, metrics | Core hot path. Routes `POST /mcp/{upstream}`, `POST /intent`, `GET /healthz`, `GET /readyz`, `GET /metrics`. Orchestrates auth → parse → branch → intent → cost-prefetch → engine → upstream → audit flow. |

### Audit subsystem — `tessera/audit/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/audit/async_emit.py` | source | `AuditEmitter`, event payloads (via `enqueue`) | Flushed audit events | `asyncio.Queue`-backed single-consumer drain. Overflow drops oldest; drain has 10s timeout. |
| `tessera/audit/canonical_json.py` | source | Python dict | Canonical JSON bytes | RFC 8785 JCS: lexicographic key sort, no whitespace, `ensure_ascii=False`. Used for event hashing. |
| `tessera/audit/chain.py` | source | event dict, scope string | Stamped event dict with `prevEventHash` + `eventHash` | `HashChain` — in-memory per-scope rolling SHA-256. Thread-safe via `RLock`. |
| `tessera/audit/emitter.py` | source | tenant_id, sink list, event type + payload | Stamped audit event dict; calls `sink.emit()` for each | `AuditEmitter` — fans out to ≥1 sink, isolates per-sink failures, builds event dict, calls `HashChain.stamp()`. Schema version `v0.1`. |
| `tessera/audit/inspect.py` | source | `SqliteSink`, scope, limit, format | Iterator[dict], CSV/JSONL file | Helper functions (`tail_events`, `export_jsonl`, `export_csv`, `fetch_event_by_id`) used by CLI audit subcommands. |
| `tessera/audit/verifier.py` | source | `SqliteSink`, scope | Verification results | Walks hash chain oldest→newest, re-computes canonical_json hash, reports first broken link. |
| `tessera/audit/sinks/base.py` | source | — | Protocol definition | `AuditSink` Protocol: `emit`, `close`, `head_hash`, `iter_events`. Runtime-checkable. |
| `tessera/audit/sinks/sqlite.py` | source | SQLite DB path, event dicts | SQLite rows; head_hash | WAL-mode SQLite sink with `synchronous=NORMAL`. Includes seq, event_id, scope, event_type, occurred_at, prev_hash, event_hash, payload. |
| `tessera/audit/sinks/stdout.py` | source | audit event dicts | stdout JSON lines | StdoutSink — one JSON line per event. For Docker log aggregation. |
| `tessera/audit/sinks/_buffered.py` | source | wrapped sink, batch size, flush interval | Forwarded batch calls | Internal buffered wrapper; not exported. Batches `emit()` calls to reduce SQLite write amplification. |

### Auth subsystem — `tessera/auth/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/auth/base.py` | source | — | `AuthContext`, `Authenticator` Protocol | Defines `AuthContext(principal_id, scope, metadata)` dataclass and `Authenticator` Protocol. |
| `tessera/auth/bearer.py` | source | `Authorization: Bearer <token>` header, env vars, YAML token file | `AuthContext` | Multi-token bearer auth with constant-time compare. Inline list / YAML file / legacy single-token env. Dev-mode no-tokens path returns `anonymous` with recurring warning. |
| `tessera/auth/_jwks.py` | source | JWKS URL (httpx), JWT string | `JWKSCache`, decoded claims | Shared JWKS fetch (sync) and JWT decode via `python-jose`. 1h TTL, `kid`-keyed, re-fetch on unknown `kid`. |
| `tessera/auth/jwt_mcp.py` | source | `JWTAuthConfig`, JWT `Authorization` header | `AuthContext` | JWT-mode authenticator for MCP-traffic auth. Extracts `principal_claim` (default `sub`) and `scope_claim` (default `scope`). |
| `tessera/auth/oauth_rs.py` | source | `TESSERA_OAUTH_*` env vars, HTTP requests (POST /register, /introspect, /revoke) | JSON HTTP responses (RFC 9728/7591/7662/7009) | OAuth 2.1 Resource Server endpoints. Registers 4 routes on the FastAPI app. `InMemoryRevocationStore` singleton; replaceable via `set_revocation_store()`. Per-IP rate limiting on DCR. |
| `tessera/auth/oidc.py` | source | `ManagementPlaneConfig`, JWKS URL, JWT header | `AuthContext` | OIDC/management-plane authenticator. Used when `auth.type: jwt` + management plane is Clerk/Auth0/Cognito/custom. |

### Cost subsystem — `tessera/cost/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/cost/_aws_canonical_ops.py` | source | — | `BUILTIN_AWS_OPS` list (10 op names) | Data-only registry of the 10 builtin AWS operation names. Migrated from the removed `aws_mapping.py` in v0.4.0. |
| `tessera/cost/combinations.py` | source | combination definitions (from tessera-intelligence), `(tenant, scope)` context, op names + costs | `CombinationTracker` instance, `CombinationChain` state | **v0.6.0 NEW** multi-op call-chain tracker. Memory-bounded (1000 chains/tenant, LRU evict). Exposes `aggregate_cost_usd`, `ops_count`, `window_seconds`, `principals_count`. Feeds 4 new policy conditions. Thread-safe, no I/O on hot path. |
| `tessera/cost/infracost.py` | source | Infracost GraphQL endpoint URL, SKU query params | `SkuResult` (USD/unit, confidence_band) | GQL client with 200ms timeout + 300s in-process cache. Falls back to `ceiling` confidence on timeout. Requires `[infracost]` extra. |
| `tessera/cost/price_table.py` | source | Signed price-table JSON artifact (from intelligence cache) | `PriceTable` instance with `cost_for_call()` method | Loads price-table artifact into a `(operation, realm, frozenset(params))` → price_usd dict. Sub-ms lookups. Supports `aws_*`, `azure_*`, `gcp_*` prefixed ops. |
| `tessera/cost/types.py` | source | — | `CostResult`, `InfracostQuery`, `CostSource` dataclasses | Data models. `CostSource` is `Literal["price_table", "infracost_live", "miss"]`. |

### Integrations — `tessera/integrations/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/integrations/cursor_hooks.py` | source | JSON from Cursor hook stdin, `TESSERA_URL`, `TESSERA_BEARER_TOKEN*` env | JSON to stdout (`action: allow\|deny`), Tessera `/intent` call | Cursor v1.7-beta hook script. Resolves multi-token bearer, calls `/intent`, returns allow/deny. Fail-closed mode via `TESSERA_CURSOR_FAIL_CLOSED=true`. |
| `tessera/integrations/aws/blast_radius.py` | source | boto3 session, IAM/S3/KMS policy document | `int` principal count | `BlastRadiusBackend` — counts IAM users/roles/accounts affected by a policy change. Thread-safe 5-min TTL cache. Lazy boto3 init. |
| `tessera/integrations/aws/cli_translator.py` | source | canonical op name, tool args dict | `{"tool": "call_aws", "command": "..."}` dict; reverse name from `call_aws` args | Bridges Tessera canonical tool names to `awslabs/mcp/aws-api-mcp-server`'s `call_aws` interface. Per-op handler registry + reverse-lookup map. Kebab-to-camel overrides for RDS, EKS, etc. |
| `tessera/integrations/aws/upstream.py` | source | `UpstreamConfig` (aws_mcp kind), JSON-RPC body, boto3 creds chain | `JSONResponse` (forwarded or error) | `AWSMcpUpstream` — wraps `mcp_proxy_for_aws.client.aws_iam_streamablehttp_client`. Routes `tools/call` via SigV4. Supports `aws_mcp_routing: specific-first\|call-aws-only`. |
| `tessera/integrations/streamable_http/upstream.py` | source | `UpstreamConfig` (mcp_streamable_http kind), JSON-RPC body | `JSONResponse` (forwarded or error) | **v0.5.1 NEW** `StreamableHttpUpstream` — implements MCP 2025-06-18 streamable-HTTP transport. Session-id handshake, SSE response parsing, session-expire retry. Process-local session cache. |

### Intelligence subsystem — `tessera/intelligence/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/intelligence/client.py` | source | `IntelligenceConfig`, CDN URLs, `LicenseValidator`, `public_key.pem` | Pack tarballs (extracted to cache_dir), `PriceTable` instances, policy YAML files | `IntelligenceClient` — fetches catalog from CDN, verifies Ed25519 signature, downloads packs, verifies SHA-256 content hashes, extracts to local cache. Background 24h refresh task. `_TIER_ORDER` has 5 keys (free/developer/team/scale/enterprise). |
| `tessera/intelligence/license.py` | source | `TESSERA_LICENSE_KEY` env, license server URL, `public_key.pem`, disk cache | `LicenseStatus(tier, expires_at, seats, jwt)` | `LicenseValidator` — validates against license server, caches 24h, falls back to cached value for up to `license_cache_fallback_days`. **MISMATCH**: `_TIER_ORDER` has 4 keys (free/developer/team/enterprise — no `scale`). See `arch/improvements/arch-scan-tier-order-mismatch.md`. |
| `tessera/intelligence/public_key.pem` | other | — | Ed25519 public key bytes (via `importlib.resources`) | Trust anchor. **Must be byte-for-byte identical to `tessera-intelligence/_metadata/public-key.pem`.** Shipped inside the wheel. |

### LLM policy authoring — `tessera/llm/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/llm/base.py` | source | — | `PolicyAuthor`, `ToolCatalogAnalyzer` Protocols; `PolicyRecommendation` dataclass | Defines the two provider Protocols and output type. |
| `tessera/llm/_shared.py` | source | `Policy.model_json_schema()` (introspected), intent string | System prompt string | Schema-driven prompt builder. Introspects live schema at runtime — auto-picks up new conditions. |
| `tessera/llm/anthropic.py` | source | Anthropic SDK (optional), intent / tool catalog, system prompt | `list[PolicyRecommendation]` | Anthropic provider. Validates YAML against pydantic; retries up to `max_retries` on invalid YAML. |
| `tessera/llm/azure_openai.py` | source | Azure OpenAI + azure-identity (optional), intent / tool catalog | `list[PolicyRecommendation]` | Azure OpenAI provider. Same validation + retry pattern. |
| `tessera/llm/bedrock.py` | source | boto3 (optional), intent / tool catalog | `list[PolicyRecommendation]` | AWS Bedrock provider. |
| `tessera/llm/cohere.py` | source | Cohere SDK (optional), intent / tool catalog | `list[PolicyRecommendation]` | Cohere provider. Not yet listed in `llm-policy-authoring.md` status doc. |
| `tessera/llm/gemini.py` | source | google-genai SDK (optional), intent / tool catalog | `list[PolicyRecommendation]` | Google Gemini provider. Default model for CLI `--model gemini`. |
| `tessera/llm/mistral.py` | source | Mistral SDK (optional), intent / tool catalog | `list[PolicyRecommendation]` | Mistral provider. Not yet listed in `llm-policy-authoring.md` status doc. |
| `tessera/llm/openai.py` | source | OpenAI SDK (optional), intent / tool catalog | `list[PolicyRecommendation]` | OpenAI provider. |

### Observability — `tessera/observability/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/observability/events.py` | source | hook functions (via `register_*`), decision/audit event dicts | async hook invocations (fire-and-forget) | Protocol-based hook registry. `OnDecision` and `OnAuditEmit`. Failures logged and swallowed so hooks never block hot path. |
| `tessera/observability/metrics.py` | source | prometheus_client (optional), decision labels, latency floats | Prometheus Counter / Histogram values | Prometheus counters with no-op stubs when `prometheus_client` is missing. |
| `tessera/observability/tracing.py` | source | `TESSERA_OTEL_ENABLED` env, opentelemetry SDK (optional) | OTel spans | OTel tracing. All no-op when `TESSERA_OTEL_ENABLED` is unset. |

### Policy engine — `tessera/policy/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/policy/action_verbs.py` | source | tool name string, optional user YAML mappings | `frozenset[str]` of verb names | Registry mapping MCP tool names → intent-verb sets. Covers 50+ tools across AWS/GCP/Azure/Databricks/Snowflake. |
| `tessera/policy/conditions.py` | source | condition model instance, evaluation context dict | bool (True = matched) | Per-condition evaluator functions + `_DISPATCH` dict keyed on type. Unknown types fail-closed. Handles all **25 condition types** including v0.6.0 combination conditions. |
| `tessera/policy/engine.py` | source | `list[Policy]`, evaluation context dict | `Decision(action, reason, policy_id)` | First-match-wins evaluator. Lockdown short-circuit. Resolves effective tool name once per request. |
| `tessera/policy/loader.py` | source | policy directory path, YAML files | `list[Policy]` (sorted by priority/cost-tier), loader state | `FilesystemPolicyLoader` — loads/validates YAML files, isolates per-file errors, hot-reloads on file change via `watchdog`. |
| `tessera/policy/matchers.py` | source | policy match spec, request upstream/tool name | bool | Upstream wildcard match, tool glob match (`fnmatch`), tool_pattern regex match, canonical tool name resolution. |
| `tessera/policy/regex_safety.py` | source | regex pattern string | compiled regex or `RegexSafetyError` | ReDoS corpus validator. Tests candidate against known-adversarial strings with 100ms timeout. |
| `tessera/policy/schema.py` | source | YAML dict (via pydantic) | `Policy` pydantic models; **25 `ConditionType` union members** | All policy YAML shapes validated here. `extra="forbid"` on all models. Discriminated union on `condition` literal. **Includes 4 v0.6.0 combination conditions**: `combination_aggregate_cost_usd_gt`, `combination_ops_count_gt`, `combination_window_seconds_lt`, `combination_id_matches`. |

### State — `tessera/state/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `tessera/state/daily_spend.py` | source | `TESSERA_STATE_DIR` env (or `~/.tessera/state/`), `(scope, day)` keys, USD amounts | SQLite DB (`daily_spend.db`), float cumulative USD | `DailySpendState` — thread-safe SQLite per-scope daily USD accumulator. `INSERT ... ON CONFLICT DO UPDATE`. Used by `cumulative_spend_today` condition. |

### Policies (bundled defaults) — `tessera/policies_default/`

**24 YAML files** (status docs say 18 — stale) covering: MCP admin denial, create-access-key denial, EC2 IMDSv1 denial, KMS deletion approval, PassRole guard, RDS public denial, cost ceiling/cap/runaway examples, IAM blast-radius example, region allowlist, business-hours, data-residency-eu, non-prod-only, oversized-payload, PII block, prod-protection, prompt-injection-heuristic, read-only-mode, require-intent, secret-leak-block, tool-allowlist, write-action-approval.

### Schemas — `schemas/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `schemas/audit_event.schema.json` | schema | — | JSON Schema Draft-07 for audit events | Required: `schemaVersion`, `eventId`, `tenantId`, `eventType`, `occurredAt`, `prevEventHash`, `eventHash`, `payload`. |
| `schemas/config.schema.json` | schema | — | JSON Schema for tessera.yaml | External publishable contract for config file shape. |
| `schemas/policy.schema.json` | schema | — | JSON Schema Draft-07 for policy YAML | Pydantic models are authoritative at runtime; this is the external publishable form. |

### Scripts — `scripts/`

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `scripts/bump_version.py` | script | `sys.argv` (target version string), 5 source files | Updated `pyproject.toml`, `tessera/_version.py`, `README.md`, `docs/INSTALL.md`, `CHANGELOG.md` | Bumps version across all 5 canonical sites. Validates semver, guards against downgrade. `--dry-run` and `--validate` modes. |

### Root config

| Path | Type | Inputs | Outputs | Process |
|------|------|--------|---------|---------|
| `pyproject.toml` | config | — | Build metadata, deps, ruff/mypy/pytest/coverage config | Version **`0.6.0`**. 9 optional-dep groups. Entry point: `tessera = "tessera.cli:app"`. |
| `Dockerfile` | config | `pyproject.toml`, `tessera/` source, pinned base image | OCI image | Two-stage build pinned to `python:3.12-slim@sha256:401f6e1a...`. Runtime: UID 10001, non-root. Installs `[aws,gemini,oidc,intelligence,infracost]` extras. |
| `Makefile` | config | docker CLI, cyclonedx-py | sbom.json, Docker image | Two targets: `sbom` and `docker-build-repro`. |
| `bandit.yaml` | config | — | Bandit security scan config | Customizes which Bandit rules apply per path. |
| `gitleaks-config.toml` | config | — | Gitleaks secret-scan config | Custom rules for CloudMorph-specific secret patterns. |
| `semgrep.yaml` | config | — | Semgrep SAST rule config | Custom security patterns for this codebase. |
| `tessera.example.yaml` | config | — | Example tessera.yaml | Annotated example config covering all major settings blocks. |
| `tokens.example.yaml` | config | — | Example bearer tokens YAML | Multi-token format example. |
| `docker-compose.example.yaml` | config | — | Example docker-compose setup | Customer quickstart for Docker-mode deployments. |
| `MANIFEST.in` | config | — | Source distribution manifest | Ensures non-Python files (PEM, YAML, JSON) are included in sdist. |

### Top-level docs

| Path | Type | Process |
|------|------|---------|
| `README.md` | doc | Customer-facing project overview, install/quickstart, version-stamped Docker image reference. |
| `CHANGELOG.md` | doc | Keep-a-Changelog format. Current: **v0.6.0** (2026-05-18). |
| `SECURITY.md` | doc | Security policy + CVE reporting. |
| `CONTRIBUTING.md` | doc | Dev setup, testing conventions, PR process. |
| `WORKFLOW_REQUIRES_AWS_OIDC_SETUP.md` | doc | GitHub Actions OIDC setup guide. |

### Tests — `tests/`

`tests/unit/` mirrors the `tessera/` package structure (test_engine.py, test_conditions.py, test_chain.py, sinks/test_sqlite.py, auth/test_bearer.py, cost/test_cost_for_call.py, state/test_daily_spend.py, integrations/aws/test_cli_translator.py, policy/test_regex_safety.py, policy/test_blast_radius.py, policy/test_cumulative_spend.py + ~20 more files).

Top-level: `test_combinations.py` (CombinationTracker chain tracking, LRU eviction), `test_oauth_resource_server.py` (RS endpoints), `test_audit_cli.py`, `test_llm_cohere.py`, `test_llm_mistral.py`, `test_policies_default.py`, `test_v0_6_packs.py`.

`tests/integration/`: `test_proxy_round_trip.py`, `test_policy_decisions.py`, `test_aws_api_mcp_translation.py` (contains 1 `TODO(SA-3D)`), `test_intelligence_client.py`, `test_jwks_prewarm.py`, + 11 more integration files.

`tests/integration_cdn_smoke.py`: 8-scenario tier-gate matrix. Requires real tier JWTs (still pending per `nextsteps.md` P0-8).

### Benchmarks — `benchmarks/`

| Path | Process |
|------|---------|
| `benchmarks/decision_latency.py` | Microbenchmark for `PolicyEngine.evaluate()`. Baseline: ~60 µs per eval. |
| `benchmarks/rps_sustained.py` | Sustained throughput benchmark via asyncio + concurrent httpx. |
| `benchmarks/blast_radius_latency.py` | Blast-radius condition overhead measurement. |
| `benchmarks/mock_upstream.py` | Local echo MCP server (benchmark upstream stub). |

## See also

- `arch/status/overview.md` — high-level system overview (note: `cost/aws_mapping.py` ghost reference flagged for cleanup)
- `arch/status/proxy-enforcement-and-audit.md` — hot-path detail (note: step 5/6 still reference removed `aws_mapping`)
- `arch/status/policy-engine.md` — policy schema + 25 conditions (was "21" — stale)
- `arch/status/intelligence-and-licensing.md` — CDN + license validator
- `arch/status/integrations-and-cost.md` — AWS upstream + cost backends (note: missing streamable-HTTP coverage)
- `arch/status/llm-policy-authoring.md` — LLM provider system (note: missing cohere + mistral)
- `arch/status/packaging-and-release.md` — PyPI + Docker (note: stated PyPI version `0.2.1` is stale; actual `0.6.0`)
- `arch/nextsteps.md` — open work items (P0-8 unsigned packs, P0-9 unsigned mappings, real-JWT CDN smoke)
- `arch/improvements/arch-scan-*.md` — issues surfaced by the 2026-05-21 arch-scan pass
