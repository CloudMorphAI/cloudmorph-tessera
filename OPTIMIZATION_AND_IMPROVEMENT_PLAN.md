# Control Centre — Optimization & Improvement Plan

_Last updated: 2026-04-23. Based on actual repo state at commit `def8f9a`._

---

## 1. Current State Audit

Ground truth, per directory. LoC counts are source lines only. Last meaningful commit dates come from `git log`.

| Area | LoC | Last commit | Maturity | One-line reality |
|---|---:|---|---|---|
| [`cloudmorph-mcp/`](cloudmorph-mcp/) | 1,241 TS | 2026-01-19 (`6cf5a8f`) | **Partial** | Stateless HTTP+WS proxy to upstream Control Center API — no local policy, no intent capture |
| [`sdk-python/`](sdk-python/) | 254 Py | 2026-01-19 | **Partial** | Minimal stdlib-only sync client; no async, no framework adapters |
| [`contracts/`](contracts/) | 3 files | 2026-01-15 (`a10f5ed`) | **Skeleton** | JSON Schema draft-07, `additionalProperties: true` everywhere, advisory only |
| [`aws/executor/`](aws/executor/) | 1,704 Py | 2026-01-19 (`2391a2e`) | **Working** | 50+ action handlers, BYOC job runner, heartbeat loop, S3 artifacts |
| [`gcp/executor/`](gcp/executor/) | 1,412 Py | 2026-01-15 | **Partial** | ~30 actions, GCS/Compute/BQ; less tested than AWS |
| [`azure/executor/`](azure/executor/) | 1,578 Py | 2026-01-15 | **Partial** | ~25 actions, Blob/VM/SQL/KeyVault |
| [`databricks/executor/`](databricks/executor/) | 901 Py | 2026-02-10 (`def8f9a`) | **Partial** | Workspace/SQL/Unity Catalog; mostly read |
| [`snowflake/executor/`](snowflake/executor/) | 911 Py | 2026-02-10 | **Partial** | SHOW/DESCRIBE only; no DML/DDL |
| [`tests/`](tests/) | 5 files | 2026-02-10 | **Thin** | SDK + ratelimit + dbx/sf job runners; AWS/GCP/Azure executors untested; MCP untested (`"echo 'No tests yet'"`) |
| [`docs/`](docs/) | 1 file | 2026-02-10 | **Skeleton** | Single [`getting-started.md`](docs/getting-started.md); references a TypeScript SDK that does not exist in this repo |

**Bluntest gaps:**
- The word "intent" appears **zero** times in the codebase. The "intent layer" pillar of the product is 100% aspirational.
- The word "policy" appears **once** (in a tool description string). There is no local policy engine — [`routes.ts`](cloudmorph-mcp/src/routes.ts) forwards every call to `CONTROL_CENTER_API_URL` via bearer token.
- Rate limiter is in-memory ([`ratelimit.ts`](cloudmorph-mcp/src/ratelimit.ts)) — cannot scale past a single instance.
- No root-level `README.md`, `Makefile`, `Dockerfile`, `pyproject.toml`, or CI config. Each sub-project is its own island.
- No shared base class across the 5 executors — `ControlCenterClient` is **copy-pasted** (173 LoC × 5 = 865 duplicate LoC).
- Contracts schemas are not enforced at the MCP boundary; `additionalProperties: true` means they're documentation, not validation.
- Repo hasn't had a meaningful commit since 2026-02-10. Two months of drift.

---

## 2. Strategic Framing — What Control Centre Is

Lock this down. Every section below flows from it.

> **Control Centre is a runtime firewall for agentic AI tool calls.** It sits in the hot path between an agent's planner and the tools the agent invokes. It captures the agent's stated **intent** (what it says it's doing and why) before execution, evaluates intent + tool call + runtime context against declarative **policies**, and returns allow/deny/approve/mutate decisions in a p99 latency budget tight enough for agent loops. It ships as an **MCP server** that sits between the host agent and downstream MCP servers, plus thin **SDKs** for agents that don't speak MCP natively. The cloud directories (`aws/`, `azure/`, `gcp/`) and data platform directories (`databricks/`, `snowflake/`) are **integration targets** for runtime governance — not the product itself.

The current repo implements the **job execution plumbing** (executors) and a **gateway MCP server** — but not the firewall, intent, or policy engine. The wedge is still to be built.

---

## 3. Priority Order

1. **MCP server ([`cloudmorph-mcp/`](cloudmorph-mcp/)) — this is the wedge.** An agentic runtime firewall that cannot be trivially installed as an MCP server has no distribution. Every other component is downstream of this.
2. **Contracts ([`contracts/`](contracts/)) — second.** Without `ToolCallRequest`, `IntentDeclaration`, `PolicyDecision`, `AuditEvent`, `RuntimeContext` locked down, the MCP server, SDK, and executors will drift apart. Contracts must land before SDK expansion.
3. **Python SDK ([`sdk-python/`](sdk-python/)) — third.** Once an agent can hit the MCP server *natively*, the SDK is for agents that can't (raw Anthropic/OpenAI SDK loops) and for wrapping frameworks (LangChain, LlamaIndex, Pydantic AI).
4. **Cloud + data platform integrations — last.** The executors already work. Evolving them into governance targets (runtime enforcement hooks) waits until the core firewall is credible. Premature investment here locks us to a shape we don't yet understand.

**Justification:** The product's value is the firewall. Everything else is either supply (integrations) or demand plumbing (SDK). A working MCP server + one real design partner agent beats a broader-but-shallower investment across five clouds.

---

## 4. MCP Server Improvements ([`cloudmorph-mcp/`](cloudmorph-mcp/))

The heart. Today it's 1,241 LoC of TypeScript that proxies JSON-RPC to an upstream API. It needs to become the firewall.

### 4.1 Tool surface

Current tools ([`routes.ts:44-92`](cloudmorph-mcp/src/routes.ts)):
- `cloudmorph_request` — submit an action, wait for decision
- `cloudmorph_request_status` — poll a request
- `cloudmorph_job_status` — poll a job

Missing runtime-governance tools:
- [ ] `cloudmorph_declare_intent` — agent declares what it's about to do and why, **before** invoking any tool. Returns an `intentId` scoped to the session.
- [ ] `cloudmorph_list_policies` — introspection for agent and operator tooling
- [ ] `cloudmorph_approve` / `cloudmorph_deny` — human-in-the-loop from an approver agent or UI
- [ ] `cloudmorph_replay` — replay a past `requestId` with an updated policy set (dry run)
- [ ] `cloudmorph_proxy` — transparent proxy to a downstream MCP server URL, wrapping every `tools/call` with policy evaluation. **This is the killer feature** — turns Control Centre into an MCP that policy-enforces other MCP servers.

### 4.2 Transport

Today: HTTP POST `/mcp` + WebSocket `/mcp/ws` ([`index.ts`](cloudmorph-mcp/src/index.ts), [`ws.ts`](cloudmorph-mcp/src/ws.ts)). **No stdio.** Cursor/Codex users configure via URL, which is fine for hosted, but local development and self-hosted deployments need stdio.

- [ ] Add stdio transport. Spec: [modelcontextprotocol.io](https://modelcontextprotocol.io/). Use `@modelcontextprotocol/sdk` for correctness instead of the hand-rolled JSON-RPC in [`routes.ts`](cloudmorph-mcp/src/routes.ts).
- [ ] Add SSE transport (HTTP streaming) — some clients prefer it over WS.
- [ ] Keep the hand-rolled HTTP JSON-RPC for REST-bridge users but have it share one policy kernel with the stdio/SSE paths.

### 4.3 Auth & multi-tenancy

Today: bearer token in `Authorization` header, forwarded upstream ([`auth.ts`](cloudmorph-mcp/src/auth.ts) — 13 LoC, extraction only). The MCP server does not know which tenant a token belongs to; it relies on upstream to tell it.

- [ ] Local token → tenant resolution with short-lived cache (30s TTL). Eliminates a round trip on the hot path.
- [ ] mTLS option for enterprise self-hosted.
- [ ] OIDC integration for human approvers (Google, GitHub, Okta).
- [ ] Per-tenant policy namespace, so `tenant-a`'s rules don't affect `tenant-b`.

### 4.4 Policy engine

**This is the biggest gap.** Currently zero local evaluation. Options:

- **Option A — embed OPA (Rego)**: mature, declarative, hot-reloadable, used by Kubernetes/Envoy. Foreign syntax for JS/Python developers.
- **Option B — embed Cedar (AWS)**: newer, strong types, readable. Less ecosystem.
- **Option C — hand-rolled policy DSL in TypeScript**: fastest to ship, total control, becomes a liability.

**Opinion:** Ship **Option A (OPA/Rego)**. Use the OPA WASM bundle to evaluate in-process (sub-millisecond). Rego is ugly but it's the industry standard — any customer security team will accept it; no one will accept a homegrown DSL on a compliance review.

- [ ] Add [`cloudmorph-mcp/src/policy/`](cloudmorph-mcp/src/policy/) directory.
- [ ] Embed OPA WASM runtime (`@open-policy-agent/opa-wasm`).
- [ ] Hot-reload from filesystem path (`POLICY_BUNDLE_PATH`) or bundle URL (`POLICY_BUNDLE_URL`) with HMAC verification.
- [ ] Deny-by-default. Every request must match an explicit `allow` rule.
- [ ] Evaluation order: (1) tenant-level allowlist/denylist, (2) intent-matching rules, (3) approval rules, (4) default deny.
- [ ] Decision cache keyed on `hash(policy_bundle_id, tenant_id, action, intent_id, payload_digest)` with 10s TTL.

### 4.5 Intent capture

**Zero coverage today.** This is the differentiator from generic MCP gateways.

- [ ] New contract: `IntentDeclaration { intentId, sessionId, agentName, agentVersion, statedGoal, statedSteps, constraints, createdAt }` (see §5).
- [ ] New MCP tool: `cloudmorph_declare_intent` (see §4.1).
- [ ] Intent is bound to a session token; subsequent tool calls in that session reference `intentId`.
- [ ] Mismatch detection: if an agent declared intent "list S3 buckets to audit public access" and then calls `aws.s3.delete_bucket`, the policy engine can block on intent-vs-action divergence. This check needs to run at policy time and produce a structured `intent_mismatch` decision.
- [ ] Intent telemetry: emit every declaration to the audit log whether or not a tool call follows, so we can spot dropped or suspicious intents.

### 4.6 Audit log

Today: `console.log`-style JSON lines to stdout (`logEvent` in [`routes.ts`](cloudmorph-mcp/src/routes.ts:197-201)). Suitable for containerized deployments with log shipping, but no durability or tamper-evidence.

- [ ] Define `AuditEvent` contract (§5). Every decision writes one.
- [ ] Pluggable sinks: stdout (default), S3 (append-only, object-lock for WORM), Kafka, ClickHouse.
- [ ] Hash chain: each event includes `prevEventHash` so tampering is detectable.
- [ ] Retention: 1 year default, configurable. Per-tenant.
- [ ] Add [`cloudmorph-mcp/src/audit/`](cloudmorph-mcp/src/audit/) directory.

### 4.7 Latency budget

A runtime firewall that adds 500ms to every agent tool call is a non-starter. Target: **p99 < 10ms** for cached policy decisions, **p99 < 50ms** for cold decisions (excluding network to downstream MCP).

Blockers in current code:
- Every request does a remote `fetch` to upstream Control Center ([`routes.ts:110-155`](cloudmorph-mcp/src/routes.ts)). That alone is typically 20–100ms.
- No decision cache.
- WebSocket hub uses in-memory map — fine for single instance.

- [ ] Local policy evaluation (§4.4) removes the round trip for the common case.
- [ ] Decision cache (§4.4).
- [ ] Connection pooling for upstream forwards that *do* happen (keep-alive, HTTP/2).
- [ ] Benchmark harness in [`cloudmorph-mcp/bench/`](cloudmorph-mcp/bench/) — k6 or autocannon, measure p50/p95/p99 at 100/1k/10k RPS.

### 4.8 Operational

- [ ] Distributed rate limiter (Redis token-bucket) replacing [`ratelimit.ts`](cloudmorph-mcp/src/ratelimit.ts).
- [ ] Graceful shutdown (drain in-flight requests, close WS cleanly).
- [ ] `/metrics` Prometheus endpoint: request count, decision count by outcome, policy eval time, cache hit rate.
- [ ] OpenTelemetry tracing (span per decision).
- [ ] Actually run `npm test` against something real — today it's `echo "No tests yet"` ([`package.json:22`](cloudmorph-mcp/package.json)).

---

## 5. Contracts ([`contracts/`](contracts/))

Today: 3 JSON Schema draft-07 files, all `additionalProperties: true`, not validated server-side. Effectively advisory.

### 5.1 Existing contracts — what's there

- [`request.schema.json`](contracts/request.schema.json) — `Request` (requestId, tenantId, integrationId, action, targets, payload, status, decision, reason, jobId, timestamps)
- [`job.schema.json`](contracts/job.schema.json) — `Job` (jobId, requestId, status, executorTarget, payload, artifacts, leaseUntil, claimedBy, attempts, jobToken)
- [`approval.schema.json`](contracts/approval.schema.json) — `Approval` (approvalId, requestId, status, requestedBy, approvedBy, decisionAt, notes)

### 5.2 Missing contracts

- [ ] `IntentDeclaration` — see §4.5
- [ ] `PolicyDecision` — `{ decisionId, requestId, intentId, outcome (allow|deny|approve|mutate), reason, matchedRules[], evalTimeMs, policyBundleId }`
- [ ] `AuditEvent` — `{ eventId, prevEventHash, eventHash, tenantId, sessionId, eventType, payload, occurredAt }`
- [ ] `RuntimeContext` — `{ sessionId, agentIdentity, agentVersion, hostEnv, networkContext, callerChain }` — passed with every tool call so policies can reason about *who* is calling
- [ ] `ToolCallRequest` — normalizing MCP's `tools/call` into our domain model

### 5.3 Tightening

- [ ] Add `schemaVersion` field to every contract. Today they're unversioned.
- [ ] Flip `additionalProperties: false` everywhere. Add explicit extension field `x_meta: object` for forward-compat.
- [ ] Enforce validation at MCP boundary — reject unknown fields with a clear error.
- [ ] Ship generated types: Python (`datamodel-code-generator`), TypeScript (`json-schema-to-typescript`). Auto-generate on contract change.
- [ ] Versioning policy: additive fields bump minor, removed/renamed fields bump major. CI gate that compares `schemaVersion` against base branch.

### 5.4 Cross-language readiness

Today the Python SDK has no generated types — it hand-rolls dicts. TypeScript MCP server hand-rolls its own interfaces. This will bite.

- [ ] Make [`contracts/`](contracts/) the single source of truth for types in both languages.
- [ ] When (not if) we add Go or TypeScript SDK, contracts should already be ready.

---

## 6. Python SDK ([`sdk-python/`](sdk-python/))

Today: 254 LoC, stdlib-only, synchronous. Good bones, narrow surface.

### 6.1 Public API audit

What a consumer imports today ([`__init__.py`](sdk-python/cloudmorph/__init__.py)):

```python
from cloudmorph import CloudMorphClient  # or CloudMorph
client = CloudMorphClient(token="cm_...")
result = client.request_and_wait("aws.s3.list_buckets")
```

The 3-line goal for a firewall SDK should be:

```python
from cloudmorph import firewall
firewall.wrap(my_agent)  # auto-intercepts tool calls
```

Gap: there is nothing to wrap. The SDK is a raw client, not a firewall integration point.

### 6.2 Patterns to add

- [ ] **MCP-proxy pattern (primary)** — if the agent already speaks MCP, the SDK spawns a local proxy MCP server pointed at Control Centre, no code changes in the agent.
- [ ] **Decorator pattern** — `@firewall.govern` for raw `Anthropic().messages.create(..., tools=[...])` loops.
- [ ] **Middleware pattern** — for frameworks with a middleware concept (LangChain callbacks, LlamaIndex hooks, Pydantic AI).

### 6.3 Framework adapters

- [ ] `cloudmorph.adapters.anthropic` — wrap `client.messages.create` with intent declaration + tool-call interception
- [ ] `cloudmorph.adapters.openai` — same for OpenAI SDK `tools=`
- [ ] `cloudmorph.adapters.langchain` — callback handler
- [ ] `cloudmorph.adapters.llamaindex` — query engine wrapper
- [ ] `cloudmorph.adapters.pydantic_ai` — tool wrapper

### 6.4 Async & streaming

Today: sync-only, urllib-based ([`client.py`](sdk-python/cloudmorph/client.py)).

- [ ] `AsyncCloudMorphClient` — async variant using `httpx` or `aiohttp`. Many modern agent runtimes are async.
- [ ] Streaming decision support — when `wait=true` and decision involves approval, stream status updates instead of long-poll.

### 6.5 Packaging & distribution

- [ ] Add `extras_require`: `[anthropic]`, `[openai]`, `[langchain]`, `[llamaindex]`, `[all]`.
- [ ] PyPI release flow via GitHub Actions on version tag.
- [ ] Support py3.9 through py3.13 in CI matrix.
- [ ] Add `py.typed` marker and ship type stubs.

### 6.6 Known bug to fix

- [ ] [`client.py`](sdk-python/cloudmorph/client.py) — `RateLimitError` parses retry-after from error message text; should parse from `error.code` / structured `error.data`. Rework once `PolicyDecision` contract (§5) is enforced.

---

## 7. Cloud Integration Adapters ([`aws/`](aws/), [`azure/`](azure/), [`gcp/`](gcp/))

These are **BYOC executors** today — job runners that pop jobs from upstream and call cloud SDKs. Not governance integrations. Evolving them takes a clear redefinition:

**The executor stays (already works — 1,704 + 1,578 + 1,412 = 4,694 LoC of functional code).** What we add is **runtime enforcement hooks**: the executor doesn't just run the action, it emits cloud-native enforcement signals so that even if an agent bypassed the MCP and called the cloud directly, there'd be a record and ideally a block.

### 7.1 Cross-cutting (all three clouds)

- [ ] Extract [`aws/executor/src/controlcenter_client.py`](aws/executor/src/controlcenter_client.py), [`azure/executor/src/controlcenter_client.py`](azure/executor/src/controlcenter_client.py), [`gcp/executor/src/controlcenter_client.py`](gcp/executor/src/controlcenter_client.py) — identical 173-LoC files — into a shared `cloudmorph_executor_common` package. Same for [`storage_pointers.py`](aws/executor/src/storage_pointers.py) (9-line stubs).
- [ ] Define a `BaseExecutor` interface with `claim()`, `run()`, `heartbeat()`, `complete()`, `redact()`. Each cloud subclasses.
- [ ] Every executor emits `AuditEvent`s matching the MCP server's format — one control plane log.

### 7.2 AWS ([`aws/executor/`](aws/executor/))

- [ ] **IAM session tagging**: every AWS API call from the executor should pass a session tag `cloudmorph:request_id=<id>`, so CloudTrail rows can be joined back to Control Centre decisions.
- [ ] **EventBridge emitter**: mirror each decision to a customer-owned EventBridge bus, so customers can build their own workflows (Security Hub finding, Lambda remediation).
- [ ] **SCPs / permission boundary integration**: optional mode where we *generate* a permission boundary from the customer's active policy bundle — compile-to-IAM.
- [ ] Fix [`aws/executor/src/job_runner.py`](aws/executor/src/job_runner.py) — 1,066 lines of flat `if action == "..."` dispatch. Pull into handler registry for maintainability.

### 7.3 Azure ([`azure/executor/`](azure/executor/))

- [ ] **Activity Log correlation**: include `x-ms-correlation-request-id` with Control Centre `requestId`.
- [ ] **Event Grid emitter**: mirror decisions.
- [ ] **Azure Policy integration**: compile-to-Azure-Policy for the same policy-bundle-to-cloud-enforcement story.
- [ ] Same registry refactor for [`azure/executor/src/job_runner.py`](azure/executor/src/job_runner.py).

### 7.4 GCP ([`gcp/executor/`](gcp/executor/))

- [ ] **Cloud Audit Logs correlation**: include `trace_id` tied to `requestId`.
- [ ] **Eventarc emitter**: mirror decisions.
- [ ] **Org Policy / IAM Conditions integration**.
- [ ] Same refactor for [`gcp/executor/src/job_runner.py`](gcp/executor/src/job_runner.py).

---

## 8. Data Platform Adapters ([`databricks/`](databricks/), [`snowflake/`](snowflake/))

Why they exist: **governing agentic access to data platforms is a distinct value prop.** An agent doing analysis in Databricks or a support agent running queries against Snowflake is a very high-ROI firewall target.

### 8.1 Databricks ([`databricks/executor/`](databricks/executor/))

Today: 901 LoC, REST API client, mostly workspace/SQL/Unity Catalog list & describe.

- [ ] **Query interception at SQL Warehouse layer**: hook into the SQL Warehouse's query submission path (JDBC proxy or REST interceptor) so every SQL from an agent-scoped credential flows through Control Centre.
- [ ] **Unity Catalog row/column policy**: when intent is "generate a quarterly sales summary", automatically narrow result sets to aggregate views, deny row-level access.
- [ ] **Cost policy**: estimate query cost (`EXPLAIN COST` where possible); block queries above per-intent budget.
- [ ] **Notebook policy**: intercept `dbutils.*` calls from notebooks invoked by agents (non-trivial; start with runtime env var injection).

### 8.2 Snowflake ([`snowflake/executor/`](snowflake/executor/))

Today: 911 LoC, read-only SHOW/DESCRIBE.

- [ ] **Query Tag injection**: every agent-originated query gets `QUERY_TAG = 'cloudmorph:request_id=<id>'`.
- [ ] **Row-access policy codegen**: compile Control Centre policies into Snowflake native row-access policies where possible.
- [ ] **Warehouse cost gating**: Snowflake credit estimate as a policy input.
- [ ] Write DML/DDL only unlocks behind explicit policy allow.

---

## 9. Cross-Cutting Architecture

### 9.1 Shared interfaces

- [ ] `cloudmorph-common-py` package (new) — the `BaseExecutor`, `ControlCenterClient`, `AuditEmitter`, `RedactionFilter`. Eliminates the 865 LoC of copy-paste across executors.
- [ ] `cloudmorph-common-ts` — same shape for the MCP server and future TS SDK.
- [ ] Keep the five `executor/` directories thin — cloud-specific handlers only.

### 9.2 Configuration model

Today: each executor reads its own env vars ad hoc.

- [ ] Pydantic `Settings` classes per executor, loaded from env + optional TOML file.
- [ ] Per-tenant override mechanism (header, JWT claim).
- [ ] Secrets via SDK (AWS Secrets Manager, Key Vault, GCP Secret Manager) — stop requiring raw tokens in env.

### 9.3 Observability

- [ ] Structured logging via `structlog` (Python) and `pino` (Node) — everywhere.
- [ ] OpenTelemetry: single trace spans `MCP -> policy eval -> upstream API -> executor -> cloud`. Today nothing is traced.
- [ ] Metrics emitted: decision count by outcome, eval time, cache hit, executor job duration, heartbeat gap.
- [ ] SLO doc: MCP p99 < 50ms; decision correctness vs known fixtures = 100%.

### 9.4 Failure modes

**Fail-open or fail-closed when the policy engine is down?** This is a founder call.

- **Opinion:** fail-closed for `deny-first` tenants, fail-open-with-flag for `audit-only` tenants. Default to fail-closed; log loudly when fail-open is selected.
- [ ] Document this choice in [`docs/deployment.md`](docs/deployment.md) (new).
- [ ] What if audit log sink is unreachable? Buffer to local disk (bounded queue), drop oldest on overflow, alert. **Never** silently drop.
- [ ] Executor disconnected from Control Center: continue executing claimed job, fail closed on next claim.

---

## 10. Testing & Quality

Current state: 5 test files, ~200 LoC. MCP server has zero tests. AWS/GCP/Azure executors have zero tests.

- [ ] **MCP server test harness**: Vitest or Node's built-in runner. Fixtures for JSON-RPC methods (`initialize`, `tools/list`, `tools/call`). WebSocket integration tests. Rate limiter already has [`tests/test_ratelimit.py`](tests/test_ratelimit.py) but that's Python testing a TS module? — verify what's actually being tested there.
- [ ] **Policy engine test suite**: fixture-based. For each policy bundle, a set of `(request, expected_decision)` pairs.
- [ ] **Integration tests** per executor against LocalStack (AWS), Azurite (Azure), GCP emulators. Nightly.
- [ ] **Replay testing**: capture real decisions from staging, replay with candidate policy bundles to detect regressions before rollout.
- [ ] **Adversarial suite**: prompt-injection via tool args, intent-mismatch attacks, time-of-check-to-time-of-use (TOCTOU) in approval flows, replay attacks. Grow this suite aggressively — it's the product's moat.
- [ ] **Pre-commit hooks** at repo root: `ruff`, `mypy --strict` on Python; `eslint`, `tsc --noEmit` on TS; schema validation on [`contracts/`](contracts/).
- [ ] Coverage target: 80% on MCP server, 80% on SDK, 60% on executors (cloud SDK mocking is painful — integration tests matter more).

---

## 11. Packaging & Distribution

### 11.1 MCP server

- [ ] **Container image** (primary, first): `ghcr.io/cloudmorphai/cloudmorph-mcp:1.0.0`. Multi-arch (amd64 + arm64). [`Dockerfile`](cloudmorph-mcp/Dockerfile) already exists — audit for security hardening (non-root user, distroless base).
- [ ] **Single binary** (secondary): bundle with `pkg` or migrate to a Go rewrite later if latency demands.
- [ ] **Hosted SaaS** (`mcp.cloudmorph.io`): the default for design partners — zero-install onboarding. Already referenced in [`docs/getting-started.md`](docs/getting-started.md:69).

Ship container + hosted first. Binary later if demand.

### 11.2 SDK

- [ ] PyPI: monthly cadence or on feature additions.
- [ ] Semver: 0.x until contracts are v1.
- [ ] Pin the contract schema version the SDK targets; reject mismatched servers with a clear error.

### 11.3 Self-hosted vs hosted

- [ ] **Hosted first.** Compliance-heavy customers will want self-hosted later; by then the image is proven.
- [ ] When self-hosted ships: Helm chart, Terraform module, one-command installer.

---

## 12. 30 / 60 / 90 Day Roadmap

Bias: one working MCP firewall + one real design partner wiring it into a real agent. Everything else is a supporting cast.

### Days 0–30: "Firewall that actually firewalls"

- [ ] Contract v0.1 drafted: `IntentDeclaration`, `PolicyDecision`, `AuditEvent`, `RuntimeContext`, `ToolCallRequest` (§5)
- [ ] MCP server embeds OPA WASM; local policy eval working with a bundled example bundle (§4.4)
- [ ] `cloudmorph_declare_intent` tool shipped (§4.1)
- [ ] Intent-vs-action mismatch detection implemented (§4.5)
- [ ] Audit log pluggable sinks (stdout + S3) with hash chain (§4.6)
- [ ] MCP server has a real test suite — not `echo "No tests yet"` (§10)
- [ ] **Done = A design partner's agent can declare intent, call a tool, and get a policy decision from a locally-evaluated OPA bundle in under 50ms p99.**

### Days 31–60: "Distribution"

- [ ] `cloudmorph_proxy` tool shipped — Control Centre as firewall in front of other MCP servers (§4.1)
- [ ] Python SDK: Anthropic + OpenAI adapters, async client (§6)
- [ ] Decision cache + distributed rate limiter (§4.7, §4.8)
- [ ] Prometheus metrics + OpenTelemetry tracing (§9.3)
- [ ] Container hardening, hosted SaaS on `mcp.cloudmorph.io` running v1 in production (§11)
- [ ] Shared `cloudmorph-common-py` package, executors deduped (§9.1)
- [ ] **Done = Three design partners using the hosted MCP in production; SDK on PyPI.**

### Days 61–90: "Depth"

- [ ] Adversarial test suite landed — ≥50 distinct attack fixtures (§10)
- [ ] Replay testing (§10)
- [ ] AWS/GCP/Azure executor governance hooks: session tagging + event bus emission (§7)
- [ ] Databricks query interception POC (§8.1)
- [ ] Snowflake query tag injection (§8.2)
- [ ] LangChain + LlamaIndex adapters (§6.3)
- [ ] Compliance docs: SOC2-ready audit log, data handling docs (§9.3)
- [ ] **Done = Clear demo for any security or compliance buyer. At least one design partner willing to reference-sell.**

---

## 13. Open Questions

Founder calls needed **before** implementation on the corresponding workstream.

- [ ] **Open-source the MCP server?** The wedge is distribution. Open-sourcing the MCP server (Apache 2) with a proprietary hosted tier (policy bundles, audit retention, SSO, compliance reports) is the standard playbook (Vercel, Grafana, Supabase). Call needed before §4 work ossifies the architecture. **My lean: yes, open-source the server.**
- [ ] **Pricing model.** Per-decision (like Auth0), per-agent (like Datadog hosts), per-seat (like most SaaS), flat tier? Affects what we meter in §4.8 and audit (§4.6). **My lean: per-decision with volume tiers.**
- [ ] **First design partner profile.** Enterprise with compliance pain → different priorities (SOC2, self-hosted, SSO) vs a scrappy agent startup → SDK polish, framework adapters, hosted. Locking this picks which 30-day scope wins. **My lean: scrappy agent startup — fewer gates, tighter feedback loop.**
- [ ] **Relationship to CloudMorph Console.** Separate product, bundled tier, or feature of Console? The `/sdk-python/` and the Console's billing share naming conventions — clarify whether a Console tenant auto-provisions a Control Centre tenant. Affects auth (§4.3) and contracts (§5).
- [ ] **Rego vs Cedar vs hand-rolled DSL.** Locked by §4.4 but worth a second look once we have three real policy bundles — if Rego's ergonomics become a conversion blocker, Cedar is a drop-in swap thanks to the WASM abstraction.
- [ ] **What counts as "intent"?** Free-form text declaration is easy but hard to enforce. Structured intent (a typed schema of goals) is enforceable but burdensome. Hybrid with LLM extraction? Decision shapes §4.5 and every adversarial test (§10).
- [ ] **Keep executors in this repo, or move to `cloudmorph-console-containers`?** The executors are runtime, not governance; they arguably belong to the Console product. Moving them simplifies this repo to the firewall (MCP + SDK + contracts + policy) which is cleaner. **My lean: move them, but only after §9.1 extracts the shared interfaces.**

---

## Appendix: What's intentionally *not* here

- No rewrite of [`aws/executor/`](aws/executor/) action handlers. They work. The 1,066-line `if/elif` chain is ugly but functional — registry refactor only if it blocks a feature.
- No premature OpenAPI/protobuf. JSON Schema is enough until we have a second language SDK in flight.
- No K8s operator, no Terraform modules, no Helm charts — until self-hosted demand is real.
- No agent-framework-specific support beyond the top four (Anthropic SDK, OpenAI SDK, LangChain, LlamaIndex). Everything else via MCP.
- No UI work in this repo — that lives in the Console.
