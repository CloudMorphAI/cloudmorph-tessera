# 00 — Inventory

_Generated: 2026-04-23. Working tree at HEAD = `def8f9a` (Datbricks and snowflake, 2026-02-10) plus the uncommitted post-Feb drift on `main`._

This file is the ground-truth root all subsequent audits cite. Every LoC count is from `wc -l`, every mtime is from `stat`. No speculation; if a claim is in here it has a path you can `Read`.

---

## 0.1 Working-tree truth

```
git rev-parse HEAD       → def8f9a (Datbricks and snowflake, 2026-02-10)
git status (modified)    → 23 tracked files modified, ~9 untracked
git log --oneline -10    → 6 commits total (Jan 15 → Feb 10)
```

Recent commits (most recent first):

| Commit | Date | Subject |
|---|---|---|
| `def8f9a` | 2026-02-10 | Datbricks and snowflake |
| `78091e7` | 2026-01-22 | Intial fix |
| `2391a2e` | 2026-01-19 | Implement AWS S3 read actions in executor |
| `6cf5a8f` | 2026-01-19 | Add MCP tools/list and tools/call endpoint |
| `2964300` | 2026-01-15 | Intial fix |
| `a10f5ed` | 2026-01-15 | Initial commit |

**Drift:** every source file under `cloudmorph-mcp/src/`, every `controlcenter_client.py`, every `job_runner.py`, the SDK, and `OPTIMIZATION_AND_IMPROVEMENT_PLAN.md` (29.7 KB) was modified on **2026-04-16** but never committed. The working tree on disk is two months ahead of any commit. Until the drift is committed (or reset), every audit below describes the *worktree*, not the published `def8f9a` snapshot. Block A in [BUILD_PLAN.md](BUILD_PLAN.md) closes this.

`OPTIMIZATION_AND_IMPROVEMENT_PLAN.md` is the prior pass at this same exercise (last edited 2026-04-23) — its strategic conclusions stand and inform every audit that follows. This rescan extends the prior plan with file-level severity, hour-level execution detail, and a 14-day MVP deadline rather than the prior 30/60/90 horizon.

---

## 0.2 Top-level directory map

```
cloudmorph-control-center/
├── .gitignore                         # 16 lines, decent (covers .env, *.pem, service-account.json)
├── OPTIMIZATION_AND_IMPROVEMENT_PLAN.md  # 29,657 B, untracked, 2026-04-23 — prior plan
├── aws/executor/                      # 1,704 LoC Py + 1 Dockerfile + 1 README — most mature executor
├── azure/executor/                    # 1,578 LoC Py + 1 Dockerfile + 1 README
├── gcp/executor/                      # 1,412 LoC Py + 1 Dockerfile + 1 README
├── databricks/executor/               #   901 LoC Py + 1 Dockerfile + 1 README
├── snowflake/executor/                #   911 LoC Py + 1 Dockerfile + 1 README
├── cloudmorph-mcp/                    # 1,241 LoC TS — MCP gateway (not firewall yet)
├── sdk-python/                        #   254 LoC Py — stdlib-only sync client
├── contracts/                         #     3 JSON schemas (200 LoC total) — advisory only
├── tests/                             #     5 files,   784 LoC — uneven coverage
└── docs/                              #     1 file (getting-started.md, 159 LoC)
```

There is **no root `README.md`, `Makefile`, `pyproject.toml`, root `Dockerfile`, root `tsconfig.json`, root `eslintrc`, root `pre-commit` config, or root CI** at this repo level. Each sub-project is its own island. CI exists *only* for the MCP server ([cloudmorph-mcp/.github/workflows/ci.yml](cloudmorph-mcp/.github/workflows/ci.yml)) and runs `npm test` which is `echo "No tests yet" && exit 0` ([package.json:22](cloudmorph-mcp/package.json)). No Python is linted or tested in CI. P0 hygiene gap.

---

## 0.3 File-by-file table — source of truth

LoC = `wc -l`. Last modified = `stat`. Tracked = present in `git ls-files` of `def8f9a`. "Worktree status" = current modification status in the un-committed worktree.

### `cloudmorph-mcp/` — 1,241 LoC TS, 6 source files

| File | LoC | mtime | Tracked | Worktree | Purpose (one line) |
|---|---:|---|:-:|:-:|---|
| [src/index.ts](cloudmorph-mcp/src/index.ts) | 111 | 2026-04-16 | ✓ | M | Express bootstrap: CORS, JSON request log, health, rate-limiter, WS hub mount, router mount |
| [src/routes.ts](cloudmorph-mcp/src/routes.ts) | 583 | 2026-04-16 | ✓ | M | Hand-rolled JSON-RPC 2.0; declares 3 tools; every `tools/call` PROXIES to upstream `/controlcenter/mcp/...` |
| [src/ws.ts](cloudmorph-mcp/src/ws.ts) | 302 | 2026-01-22 | ✓ |   | WebSocket hub: subscribe/unsubscribe by `requestId`/`jobId`, waiter pattern, heartbeat, query-string token fallback |
| [src/ratelimit.ts](cloudmorph-mcp/src/ratelimit.ts) | 223 | 2026-04-16 | ✓ | M | In-memory token-bucket rate limiter (daily/burst/concurrent), djb2-style `hashToken` |
| [src/auth.ts](cloudmorph-mcp/src/auth.ts) | 13 | 2026-01-15 | ✓ |   | `getBearerToken(req)` parser only — no validation, no tenant lookup |
| [src/health.ts](cloudmorph-mcp/src/health.ts) | 9 | 2026-01-15 | ✓ |   | `GET /health → {status:"ok"}` — no upstream check, no version, no tenant |
| [package.json](cloudmorph-mcp/package.json) | 36 | — | ✓ | M | `npm test` = `echo "No tests yet" && exit 0`; deps = `express@4.19.2`, `ws@8.17.0`; no `@modelcontextprotocol/sdk` |
| [tsconfig.json](cloudmorph-mcp/tsconfig.json) | — | — | ✓ |   | TS config (not read this pass) |
| [Dockerfile](cloudmorph-mcp/Dockerfile) | 22 | — | ✓ |   | 3-stage `node:18-alpine`; runs as root; no healthcheck; `EXPOSE 8080`; no labels |
| [README.md](cloudmorph-mcp/README.md) | — | — | ✗ | ?? | Untracked (added in worktree drift) |
| [LICENSE](cloudmorph-mcp/LICENSE) | — | — | ✗ | ?? | Untracked — appears to be MIT per package.json |
| [CONTRIBUTING.md](cloudmorph-mcp/CONTRIBUTING.md) | — | — | ✗ | ?? | Untracked |
| [.env.example](cloudmorph-mcp/.env.example) | 88 | — | ✗ | ?? | Untracked, well-documented env vars (CONTROL_CENTER_API_URL, MCP_RATE_LIMIT_*, MCP_EVENT_SECRET) |
| [.github/workflows/ci.yml](cloudmorph-mcp/.github/workflows/ci.yml) | 34 | — | ✗ | ?? | Untracked. node-18 lint+build+test, but test is the no-op echo |
| [.github/ISSUE_TEMPLATE/bug_report.md](cloudmorph-mcp/.github/ISSUE_TEMPLATE/bug_report.md) | — | — | ✗ | ?? | Untracked |
| [.github/ISSUE_TEMPLATE/feature_request.md](cloudmorph-mcp/.github/ISSUE_TEMPLATE/feature_request.md) | — | — | ✗ | ?? | Untracked |
| [package-lock.json](cloudmorph-mcp/package-lock.json) | — | — | ✗ | ?? | Untracked. Should be tracked. |

**Severity callouts (preview — fully scored in [mcp/01_server_audit.md](mcp/01_server_audit.md)):**
- **P0:** `npm test` is a no-op. CI green means nothing.
- **P0:** Zero local evaluation. Every `tools/call` round-trips to upstream API → latency budget will fail.
- **P0:** Word "intent" appears 0 times in source. Word "policy" appears 1 time (in a tool description string at [routes.ts:51](cloudmorph-mcp/src/routes.ts)).
- **P1:** In-memory rate-limiter is single-instance only. Cannot horizontally scale.
- **P1:** WS token fallback to `?token=` query string ([ws.ts:55-62](cloudmorph-mcp/src/ws.ts)) leaks tokens to access logs.
- **P1:** Hand-rolled JSON-RPC instead of `@modelcontextprotocol/sdk`. Spec drift risk.
- **P2:** Untracked package-lock.json, README, LICENSE, CI, ISSUE_TEMPLATE — should be committed before any open-source push.

### `sdk-python/` — 254 LoC Py, 2 source files

| File | LoC | mtime | Tracked | Worktree | Purpose |
|---|---:|---|:-:|:-:|---|
| [cloudmorph/__init__.py](sdk-python/cloudmorph/__init__.py) | 18 | 2026-04-16 | ✓ | M | Re-exports `CloudMorph`, `CloudMorphClient` (alias), `CloudMorphError`, `RateLimitError`. `__version__ = "0.1.0"`. |
| [cloudmorph/client.py](sdk-python/cloudmorph/client.py) | 236 | 2026-04-16 | ✓ | M | Sync stdlib-only client. JSON-RPC 2.0 over urllib. Methods: `request`, `request_and_wait`, `get_request_status`, `get_job_status`. Defaults to `https://mcp.cloudmorph.io`. |
| [pyproject.toml](sdk-python/pyproject.toml) | 32 | — | ✓ | M | `name="cloudmorph"`, `version="0.1.0"`, py3.9-3.12, `Development Status :: 5 - Production/Stable` (overstated), Apache-2.0, no deps, no `extras_require`. |
| [README.md](sdk-python/README.md) | — | — | ✓ | M | (not read this pass — modified) |
| [cloudmorph.egg-info/](sdk-python/cloudmorph.egg-info/) | — | — | ✗ |   | Build artifact — should be gitignored under `*.egg-info/` (already covered in root .gitignore at line 6 — but the *folder* is committed, suggesting historical leak). Worth a sweep. |

**Severity callouts:**
- **P1:** Real bug: `CloudMorphError.code` is set from the JSON-RPC error message string, not the actual numeric code ([client.py:165-167](sdk-python/cloudmorph/client.py)). `code=err.get("message", ...)` should be `code=err.get("code", ...)` or a structured field on `error.data`. *Note:* the `RateLimitError` claim in the prior plan that retry-after is parsed from message text is **wrong** — it's actually parsed from the `Retry-After` HTTP header ([client.py:207](sdk-python/cloudmorph/client.py)) which is correct. The real bug is the `code` field on the *base* `CloudMorphError`.
- **P1:** No async, no httpx, no streaming — kills compatibility with async agent runtimes.
- **P1:** `Development Status :: 5 - Production/Stable` is wildly overstated for `0.1.0` with no firewall integration. Should be `4 - Beta` until v1.0.
- **P2:** No `extras_require` for framework adapters.
- **P2:** No `py.typed` marker.

### `contracts/` — 200 LoC across 3 schemas

| File | LoC | Tracked | Required fields | `additionalProperties` |
|---|---:|:-:|---|---|
| [request.schema.json](contracts/request.schema.json) | 73 | ✓ | requestId, tenantId, integrationId, action, targets, payload, status, createdAt | **true** (advisory) |
| [job.schema.json](contracts/job.schema.json) | 76 | ✓ | jobId, requestId, status, executorTarget, payload, createdAt, updatedAt | **true** (advisory) |
| [approval.schema.json](contracts/approval.schema.json) | 51 | ✓ | approvalId, requestId, status, requestedBy, approvedBy, decisionAt | **true** (advisory) |

All draft-07. None versioned. None enforced server-side. Detail in [contracts/02_contracts_audit.md](contracts/02_contracts_audit.md).

**Severity callouts:**
- **P0:** Missing five contracts the firewall needs: `IntentDeclaration`, `PolicyDecision`, `AuditEvent`, `RuntimeContext`, `ToolCallRequest`. (Plus `Session`, `PolicyBundle`, `RedactionRule` for completeness.)
- **P1:** `additionalProperties: true` everywhere — extension creep is invisible. Flip to `false` + explicit `x_meta`.
- **P1:** No `schemaVersion` field. Versioning required before SDK matures.

### Executors — 5,506 LoC Py across 5 clouds

`controlcenter_client.py` and `main.py` are duplicated in pattern (intentionally — same control-plane protocol, same lifecycle), but `controlcenter_client.py` is **byte-identical** in 4 of 5. `storage_pointers.py` is byte-identical in 4 of 5 (gcp adds a `build_gcs_pointer` helper).

| Executor | controlcenter_client.py | job_runner.py | main.py | storage_pointers.py | dispatch branches | Tests |
|---|---:|---:|---:|---:|---:|---|
| [aws/executor/](aws/executor/) | 173 | **1,066** | 456 | 9 | 36 | None |
| [azure/executor/](azure/executor/) | 173 | 927 | 469 | 9 | 17 | None |
| [gcp/executor/](gcp/executor/) | 173 | 750 | 470 | **19** | 20 | None |
| [databricks/executor/](databricks/executor/) | 173 | 316 | 403 | 9 | 6 | [test_databricks_job_runner.py](tests/test_databricks_job_runner.py) (174 LoC) |
| [snowflake/executor/](snowflake/executor/) | 173 | 326 | 403 | 9 | 5 | [test_snowflake_job_runner.py](tests/test_snowflake_job_runner.py) (154 LoC) |
| **Totals** | **865** | **3,385** | **2,201** | **55** | **84** | 2 of 5 covered |

**Duplication confirmed (verified by `diff`):**
- `aws/executor/src/controlcenter_client.py` ≡ `azure/executor/src/controlcenter_client.py` ≡ `gcp/executor/src/controlcenter_client.py` ≡ `databricks/executor/src/controlcenter_client.py` ≡ `snowflake/executor/src/controlcenter_client.py`. Five byte-identical copies. **865 LoC of duplication, no diff.**
- `aws/executor/src/storage_pointers.py` ≡ `azure/executor/src/storage_pointers.py` ≡ `databricks/executor/src/storage_pointers.py` ≡ `snowflake/executor/src/storage_pointers.py`. Four byte-identical copies (9 LoC each = **36 LoC of duplication**). The gcp file at 19 LoC adds one extra helper (`build_gcs_pointer`) but otherwise duplicates.

**Effective duplication:** 865 + 36 = **901 LoC of pure copy-paste**. Plus partial overlap in `main.py` (claim/heartbeat/complete loop, JSON logging, env loading, redaction, S3-vs-blob-vs-GCS artifact upload) which is structurally similar but not byte-identical — additional ~600 LoC of "near-duplicate" that should also be lifted into `BaseExecutor` once the pure dup is gone.

**Action handler dispatch shape:** every job_runner is a flat `if normalized == "x.y.z" / elif normalized == "x.y.z2"` chain. Counts: AWS 36, Azure 17, GCP 20, Databricks 6, Snowflake 5 = **84 dispatch branches** to refactor into a registry. The AWS file is the worst — 1,066 lines, 36 branches, single function. Detail and per-handler inventory in the per-cloud audits.

### `tests/` — 5 files, 784 LoC

| File | LoC | What it tests |
|---|---:|---|
| [test_credentials.py](tests/test_credentials.py) | 246 | Credential encryption (b64 fallback), masking, metadata building, injection mapping. **Imports from `cloudmorph-mono-repo/amplify/functions/shared/accounts/`** — this test reaches into a sibling repo (P1 coupling). |
| [test_databricks_job_runner.py](tests/test_databricks_job_runner.py) | 174 | Resolvers + 6 dispatch cases for the Databricks runner. |
| [test_snowflake_job_runner.py](tests/test_snowflake_job_runner.py) | 154 | Resolvers + 5 dispatch cases for the Snowflake runner. |
| [test_python_sdk.py](tests/test_python_sdk.py) | 122 | SDK init, `_is_terminal`, mocked `urlopen` for `request` happy-path, RPC error → `CloudMorphError`. |
| [test_ratelimit.py](tests/test_ratelimit.py) | 88 | **Re-implements the TS rate limiter in Python and tests the re-implementation.** Does not actually test [ratelimit.ts](cloudmorph-mcp/src/ratelimit.ts). Header comment admits it: _"For the actual TS rate limiter, tests would run via node --test"_. **Effective coverage of the real rate limiter: 0%.** |

**Severity callouts:**
- **P0:** Three of five executors (AWS, Azure, GCP) have **zero tests**. The 1,066-line AWS runner is completely uncovered.
- **P0:** MCP server has zero TS tests.
- **P0:** [test_ratelimit.py](tests/test_ratelimit.py) is misleading — it asserts a Python re-implementation, not the production code. Should be deleted or replaced by Vitest tests of the actual TS module.
- **P1:** [test_credentials.py](tests/test_credentials.py) imports from `../../cloudmorph-mono-repo/amplify/functions/shared/accounts/` — that directory is in a sibling repo. Brittle. Either move the shared code to a published package or move the test next to the code under test.
- **P2:** No coverage tooling (`coverage.py`, `c8`, `nyc`) wired anywhere.

### `docs/` — 1 file

| File | LoC | Purpose |
|---|---:|---|
| [docs/getting-started.md](docs/getting-started.md) | 159 | Marketing-ish quickstart. **References [`@cloudmorph/sdk`](docs/getting-started.md:34) (TypeScript SDK) which does not exist in this repo** — points at `npm install @cloudmorph/sdk` but no `packages/sdk/` directory. Lists 6 AWS / 6 GCP / 5 Azure / 6 Databricks / 5 Snowflake actions, all read-only. Lists `mcp.cloudmorph.io` as the hosted endpoint. |

**Severity callouts:**
- **P0:** Doc lies about a TypeScript SDK that doesn't exist. Either build it or remove the references.
- **P0:** No `deployment.md`, `policy-authoring.md`, `intent-guide.md`, `sdk-reference.md`, `architecture.md`. Hosted SaaS at `mcp.cloudmorph.io` is named in code and docs but never documented as a deployment target.
- **P1:** Action catalog is hand-maintained — will drift the moment a runner gets a new branch. Need to generate it from the runner registries (Block G).

### Root files

| File | Tracked | Purpose |
|---|:-:|---|
| [.gitignore](.gitignore) | ✓ | 16-line gitignore: covers `.env`, `*.pem`, `*.key`, `credentials.json`, `service-account.json`, `dist/`, `build/`, `__pycache__/`, `*.egg-info/`. Also has `*_secret*` and `*_credential*` glob — good. |
| `OPTIMIZATION_AND_IMPROVEMENT_PLAN.md` | ✗ | Untracked. 29.7 KB. Prior strategic plan, last edited 2026-04-23 — input for this rescan. |

---

## 0.4 LoC totals

| Area | LoC | % |
|---|---:|---:|
| Executors (5 clouds) | 6,506 (incl. duplication) | 65% |
| MCP server | 1,241 | 12% |
| Tests | 784 | 8% |
| Main runners (excl. dup) | 5,605 | — |
| SDK | 254 | 3% |
| Contracts | 200 | 2% |
| Docs | 159 | 2% |
| **Total source LoC** | **~9,144** | 100% |

Of that ~9,144, **~901 LoC is pure copy-paste duplication** (10% of the codebase). Net unique source after the common-layer extraction in Block C: ~8,243 LoC.

---

## 0.5 The honest summary

| Pillar | Status | Reality |
|---|---|---|
| Job execution plumbing | **Working** | Five executors run real cloud calls, claim/heartbeat/complete cleanly, write S3/GCS/Blob artifacts. Functional. |
| MCP server | **Gateway, not firewall** | 1,241 LoC TS that adds CORS, rate-limit, WS event hub, JSON-RPC framing on top of a plain HTTP proxy to upstream. |
| Runtime firewall | **Does not exist** | Zero local policy evaluation. Every decision is a remote `fetch`. Latency budget unachievable in this shape. |
| Intent layer | **Does not exist** | Word "intent" appears in zero source files. The differentiating pillar is fully aspirational. |
| Cross-MCP-server proxy (`cloudmorph_proxy`) | **Does not exist** | The killer tool that policy-enforces *other* MCP servers — unwritten. |
| Audit log with hash chain | **Does not exist** | `console.log` JSON lines to stdout. Tamper-evident chain unwritten. |
| Tests for MCP / AWS / Azure / GCP | **Zero** | Two of five executors covered (Databricks, Snowflake). MCP has `echo "No tests yet"`. |
| Open-source readiness | **80% staged, not committed** | LICENSE, README, CONTRIBUTING, CI exist but untracked in `cloudmorph-mcp/`. Block A commits them. |
| Hosted SaaS at `mcp.cloudmorph.io` | **Referenced, not deployed** | Named in `getting-started.md:69` and SDK default — no deployment manifest, no Terraform, no record of a running instance. |

The repo is **executor plumbing + a polite gateway**. The wedge — the runtime firewall — is unbuilt. The 14-day MVP in [BUILD_PLAN.md](BUILD_PLAN.md) ships the wedge.

---

## 0.6 What's NOT in this inventory and why

- **Generated files** (`*.egg-info`, `node_modules`, `dist`, `__pycache__`) — excluded from the `find` that produced this. They are build artifacts, not source.
- **Schema field-by-field walks** — moved to [contracts/02_contracts_audit.md](contracts/02_contracts_audit.md).
- **Per-handler action catalogs** — moved to per-cloud audits ([aws/04_executor_audit.md](aws/04_executor_audit.md) et al.).
- **OPA / intent / audit design** — green-field, lives in [policy/05_policy_engine_design.md](policy/05_policy_engine_design.md), [intent/06_intent_system_design.md](intent/06_intent_system_design.md), and [ARCHITECTURE.md §8](ARCHITECTURE.md).
- **Cross-cutting refactors** — [cross/07_common_layer_audit.md](cross/07_common_layer_audit.md) onward.
- **The 14-day plan** — [BUILD_PLAN.md](BUILD_PLAN.md).

---

## 0.7 Index of audit files

| # | File | Subject |
|---|---|---|
| 00 | [00_inventory.md](00_inventory.md) | This file. |
| 01 | [mcp/01_server_audit.md](mcp/01_server_audit.md) | MCP server line-by-line + firewall gaps. |
| 02 | [contracts/02_contracts_audit.md](contracts/02_contracts_audit.md) | Schemas: existing, missing, tightening, codegen. |
| 03 | [sdk/03_python_sdk_audit.md](sdk/03_python_sdk_audit.md) | SDK: bug, async, framework adapters, packaging. |
| 04 | [aws/04_executor_audit.md](aws/04_executor_audit.md) | AWS executor + governance hooks. |
| 04 | [azure/04_executor_audit.md](azure/04_executor_audit.md) | Azure executor + governance hooks. |
| 04 | [gcp/04_executor_audit.md](gcp/04_executor_audit.md) | GCP executor + governance hooks. |
| 04 | [databricks/04_executor_audit.md](databricks/04_executor_audit.md) | Databricks: SQL Warehouse interception + UC policy compile. |
| 04 | [snowflake/04_executor_audit.md](snowflake/04_executor_audit.md) | Snowflake: Query Tag + row-access policy compile. |
| 05 | [policy/05_policy_engine_design.md](policy/05_policy_engine_design.md) | OPA WASM, bundle format, eval order, decision cache. |
| 06 | [intent/06_intent_system_design.md](intent/06_intent_system_design.md) | Hybrid free-form + structured verbs; mismatch detection. |
| 07 | [cross/07_common_layer_audit.md](cross/07_common_layer_audit.md) | `cloudmorph-common-py`: extract & dedup. |
| 08 | [cross/08_tests_audit.md](cross/08_tests_audit.md) | Test plan, coverage targets, adversarial suite. |
| 09 | [cross/09_packaging_and_docker_audit.md](cross/09_packaging_and_docker_audit.md) | Dockerfiles, base image, multi-arch, SBOM. |
| 10 | [cross/10_security_and_tenancy_audit.md](cross/10_security_and_tenancy_audit.md) | Tenant isolation, mTLS, OIDC, audit hash chain. |
| 11 | [cross/11_observability_and_slo.md](cross/11_observability_and_slo.md) | SLOs, metrics, traces, customer-facing decision API. |
| 12 | [cross/12_strategic_open_questions.md](cross/12_strategic_open_questions.md) | Founder calls (open-source, pricing, partner profile). |
| — | [ARCHITECTURE.md](ARCHITECTURE.md) | Master architecture with mermaid diagrams. |
| — | [BUILD_PLAN.md](BUILD_PLAN.md) | 14-day MVP plan, hour-by-hour. |
