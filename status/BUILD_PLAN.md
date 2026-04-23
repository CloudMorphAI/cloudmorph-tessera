# BUILD_PLAN — 14-Day MVP

_Reading order: §0 conventions, §1 nine-block summary, §2 day-by-day execution. Every file path is real, every commit message is a `git commit -m` template. Bias: ship a working firewall + one design partner integrated end-to-end by day 14._

---

## §0 Conventions

- **Calendar:** Day 0 = today (2026-04-23). Day 14 = 2026-05-07. Working ~6h/day = 84 productive hours total. (Realistic with one engineer; double-engineer cuts the calendar in half.)
- **Branching:** direct commits to `main`. No feature branches. Per-file or small-cohesive-change commits, frequent.
- **Commit prefix:** `area(scope): subject`. Examples: `feat(mcp): add cloudmorph_declare_intent tool`, `chore(common): extract ControlCenterClient`, `test(policy): fixture suite v1`.
- **MVP marker:** every Block item is tagged `[MVP]` (must ship in 14 days), `[STUB]` (lands as scaffolding only), or `[POST-MVP]` (slip).
- **Severity carryover:** P0/P1/P2 from the audits; P0 only on critical path.
- **Founder calls:** must be locked Day 0 (see [cross/12](cross/12_strategic_open_questions.md)). Don't proceed past Block A without them.

---

## §1 Nine-block summary

| Block | Days | Theme | Hours | Critical? |
|---|---|---|---:|---|
| **A** | 0-1 | Truth & foundation: commit drift, root scaffolding, founder locks, hygiene | 12 | ✓ |
| **B** | 1-2 | Contracts: 5 new + 3 tightened, codegen pipeline, CI gate | 24 | ✓ |
| **C** | 2-3 | Common layer: extract `cloudmorph-common-py` + `-ts`; migrate executors | 30 | ✓ |
| **D** | 3-6 | MCP server core: SDK migrate, stdio, tool registry, declare_intent, explain_decision, audit chain, proxy tool | 80 | ✓ |
| **E** | 6-8 | Policy engine + intent matcher: OPA WASM, bundle hot-reload, lexical match, decision cache, fixture suite | 48 | ✓ |
| **F** | 8-10 | SDK firewall: wrap, govern, AsyncCloudMorph, Anthropic + OpenAI adapters | 40 | ✓ |
| **G** | 10-12 | Executor governance: AWS session-tagging, EventBridge, Snowflake QUERY_TAG, Databricks SQL execute_query | 32 | ✓ |
| **H** | 12-13 | Adversarial + hardening: 30 fixtures, distroless containers, Redis rate-limit, graceful shutdown, metrics, OTel | 32 | ✓ |
| **I** | 13-14 | Ship: hosted SaaS deploy, design partner integration, docs, tag v0.1.0 | 18 | ✓ |
| | | **Total** | **316** | |

316 hours total, ~38 person-days at 8h. **One engineer at 6h/day = ~52 days. Two engineers at 6h/day = ~26 days.** Three engineers (one MCP, one Python, one platform) at 6h/day each = ~18 days. **MVP requires at least 2 engineers; 3 is comfortable.**

If only one engineer: cut Block G to "AWS session tagging + Snowflake QUERY_TAG only" (~6h instead of 32h), defer Block F adapters (LangChain + LlamaIndex) to post-MVP, ship a stripped MVP at day 14 + one-week buffer to bring B/C-level executors up to par.

---

## §2 Day-by-Day execution

### Day 0 — 2026-04-23 (today)

#### Morning (3h) — Truth + Locks

**T-00 (15 min) — Founder calls locked.** Per [cross/12](cross/12_strategic_open_questions.md):
- Open-source MCP server: YES, Apache 2.0
- Pricing: per-decision with volume tiers (Free 100/d, Pro 1k/d $50, Team 10k/d $500, Ent 100k/d $2k+)
- First design partners: 1 scrappy AI agent startup + 1 compliance-anchor (mid-market FinSvc/Healthcare)
- Console relationship: bundled tier
- MCP SDK migration: YES
- OPA WASM: locked
- Hybrid intent (verbs + free-form goal): locked
- Hosted SaaS first: locked

**T-01 (1h) — Commit drift cleanup.** The worktree has 23 modified tracked files + 9 untracked, dating to 2026-04-16. Decision: commit them as `chore(worktree): commit 2026-04-16 drift` to record reality, then proceed. Alternative is `git reset --hard def8f9a` and reapply — riskier.

```bash
cd cloudmorph-control-center
git status --short  # confirm
git add -A          # everything in worktree
git commit -m "chore(worktree): commit 2026-04-16 drift before MVP rescan

Per status/00_inventory.md, the worktree was 2 months ahead of any
commit. This records reality before the 14-day MVP push begins."
```

**T-02 (30 min) — Status pages already committed.** From this rescan: 19 status files, ~4500 LoC of analysis. Commits 94b3bf5 → ef47fb0 (this commit).

**T-03 (1h) — Root scaffolding files.** New files at repo root:

```
README.md                         # 200 LoC: what is this, dev setup, links
CONTRIBUTING.md                   # 100 LoC: how to contribute, where to get an issue, signoff
SECURITY.md                       # 50 LoC: disclosure email + 72h SLA
LICENSE                           # Apache 2.0 (already in cloudmorph-mcp/LICENSE — copy here)
Makefile                          # contracts, lint, test, build, etc.
pyproject.toml                    # workspace-level pyproject (dev deps: ruff, mypy, pytest, pre-commit)
.pre-commit-config.yaml           # ruff, mypy, eslint, gitleaks, schema validation
.github/workflows/ci.yml          # root CI replacing cloudmorph-mcp-only
.github/workflows/docker.yml      # multi-arch image builds
.github/workflows/release.yml     # PyPI release on tag
```

```bash
git add README.md CONTRIBUTING.md SECURITY.md LICENSE Makefile pyproject.toml .pre-commit-config.yaml .github/
git commit -m "chore(repo): root scaffolding (Makefile, pre-commit, root CI, SECURITY.md)"
```

#### Afternoon (3h) — CI + hygiene

**T-04 (1h) — CI rewrite.** Root `.github/workflows/ci.yml` replaces `cloudmorph-mcp/.github/workflows/ci.yml`. Jobs (per [cross/08 §1.5](cross/08_tests_audit.md)):
- `contracts-verify` — generate + diff
- `lint` — ruff, mypy --strict, eslint, tsc --noEmit
- `test-mcp` — Vitest (will fail until day 4; expected)
- `test-py` — pytest matrix py3.9-3.13
- `test-policy-rego` — opa test (will fail until day 7)
- `bench` — autocannon (post-Block H)
- `adversarial` — pytest tests/adversarial/ (post-Block H)

**T-05 (30 min) — Delete misleading test_ratelimit.py.** Per [cross/08 §1.1](cross/08_tests_audit.md): the file re-implements the TS rate limiter in Python. Misleading. Real Vitest version lands day 4.

```bash
git rm tests/test_ratelimit.py
git commit -m "chore(tests): remove misleading test_ratelimit.py — re-impl in Python; real Vitest tests land in Block D"
```

**T-06 (1h) — gitleaks pre-commit + CI gate.** Per [cross/10 §1.1](cross/10_security_and_tenancy_audit.md). Add to `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/gitleaks/gitleaks
  rev: v8.21.2
  hooks:
    - id: gitleaks
```

Add `gitleaks-config.toml` (allowlist `cm_test_*` patterns).

```bash
pre-commit install
pre-commit run --all-files   # baseline pass
git add .pre-commit-config.yaml gitleaks-config.toml
git commit -m "chore(security): add gitleaks pre-commit and CI gate"
```

**T-07 (30 min) — Stale verb mapping CI gate.** Add `tests/test_action_verbs_complete.py` (placeholder; lights up after Block C). Add CI step.

**T-08 (close out day 0) — Plan reconciliation.** Update `status/BUILD_PLAN.md` with day 0 done items. Push everything.

---

### Day 1 — 2026-04-24 — Block B (Contracts) start

#### Morning (3h)

**T-09 (1h) — IntentDeclaration schema.** Author `contracts/intent_declaration.schema.json` per [contracts/02 §2.3](contracts/02_contracts_audit.md).

```bash
git add contracts/intent_declaration.schema.json
git commit -m "feat(contracts): add IntentDeclaration v0.1 schema

22-verb structured taxonomy + free-form statedGoal. ttlSeconds 5min default.
See status/contracts/02_contracts_audit.md and status/intent/06."
```

**T-10 (1h) — PolicyDecision schema.** Author `contracts/policy_decision.schema.json` per same source.

```bash
git commit -m "feat(contracts): add PolicyDecision v0.1 schema with 7 outcomes"
```

**T-11 (1h) — AuditEvent schema.** With hash-chain fields.

```bash
git commit -m "feat(contracts): add AuditEvent v0.1 schema with hash chain"
```

#### Afternoon (3h)

**T-12 (1h) — RuntimeContext + ToolCallRequest schemas.**

```bash
git commit -m "feat(contracts): add RuntimeContext and ToolCallRequest v0.1 schemas"
```

**T-13 (30 min) — Session + PolicyBundle schemas.**

```bash
git commit -m "feat(contracts): add Session and PolicyBundle v0.1 schemas"
```

**T-14 (30 min) — RedactionRule schema.**

```bash
git commit -m "feat(contracts): add RedactionRule v0.1 schema (post-MVP usage; schema lands now)"
```

**T-15 (1h) — Tighten existing 3 schemas.** Add `schemaVersion`, flip `additionalProperties: false`, add `x_meta`. Untangle `status` vs `decision` in Request.

```bash
git commit -m "feat(contracts): bump request/job/approval to v0.2 — schemaVersion + additionalProperties:false + status/decision split"
```

---

### Day 2 — 2026-04-25 — Block B finish + Block C start

#### Morning (3h)

**T-16 (1h) — Codegen pipeline scripts.** Add `scripts/generate-contracts.sh`, wire into Makefile:

```bash
make contracts          # generates Pydantic + TS interfaces
make contracts-verify   # CI gate
```

Tools needed:
- Python: `pipx install datamodel-code-generator[http]`
- TS: `npm install -g json-schema-to-typescript`

**T-17 (1h) — Codegen invocation.** First generation. Pydantic models go to `cloudmorph-common-py/cloudmorph_common/contracts/` (creating the package skeleton); TS interfaces go to `cloudmorph-common-ts/src/contracts/` (also creating).

```bash
mkdir -p cloudmorph-common-py/cloudmorph_common/contracts cloudmorph-common-ts/src/contracts
make contracts
git add cloudmorph-common-py/ cloudmorph-common-ts/ scripts/ Makefile
git commit -m "feat(contracts): generated Pydantic + TS types from schemas

cloudmorph-common-py/ and cloudmorph-common-ts/ skeletons created.
Codegen via 'make contracts'; CI gate via 'make contracts-verify'."
```

**T-18 (1h) — CI gate for `schemaVersion` bumps.** `scripts/contracts-version-check.sh` — diffs `schemaVersion` field per file against `main`, fails CI if changes are not consistent with category. Wired into root `ci.yml`.

```bash
git commit -m "ci(contracts): gate schemaVersion bumps against base branch"
```

#### Afternoon (3h) — Block C: Common layer

**T-19 (30 min) — `cloudmorph-common-py` package skeleton.** `pyproject.toml` with deps (`pydantic>=2.0`, `boto3` optional, etc.). `setup.py` for legacy compat.

**T-20 (1h) — Extract `ControlCenterClient`.** Move `aws/executor/src/controlcenter_client.py` → `cloudmorph-common-py/cloudmorph_common/client.py`. Keep `ControlCenterError`. Add docstring.

```bash
git mv aws/executor/src/controlcenter_client.py cloudmorph-common-py/cloudmorph_common/client.py
git rm azure/executor/src/controlcenter_client.py gcp/executor/src/controlcenter_client.py databricks/executor/src/controlcenter_client.py snowflake/executor/src/controlcenter_client.py
# update imports in 5 main.py files
git add -A
git commit -m "refactor(common): extract ControlCenterClient to cloudmorph-common-py

Removes 865 LoC of byte-identical duplication across 5 executors.
Updates imports in aws/azure/gcp/databricks/snowflake main.py."
```

**T-21 (30 min) — Extract `storage_pointers.build_pointer`.** Same pattern. GCP keeps its `build_gcs_pointer` extension in-place.

```bash
git rm aws/executor/src/storage_pointers.py azure/executor/src/storage_pointers.py databricks/executor/src/storage_pointers.py snowflake/executor/src/storage_pointers.py
# gcp keeps its file with build_gcs_pointer; update its imports to pull build_pointer from common
git commit -m "refactor(common): extract storage_pointers.build_pointer to cloudmorph-common-py"
```

**T-22 (1h) — `Settings` (Pydantic) per executor.** `cloudmorph_common.settings.ExecutorSettings` per [cross/07 §1.5](cross/07_common_layer_audit.md). Each cloud's main.py constructs from env.

```bash
git commit -m "feat(common): Pydantic Settings for executor env config"
```

---

### Day 3 — 2026-04-26 — Block C finish + Block D start

#### Morning (3h)

**T-23 (2h) — `BaseExecutor` + lifecycle.** Author `cloudmorph_common.base_executor.BaseExecutor` + `cloudmorph_common.lifecycle.{claim,heartbeat,shutdown}`. Each main.py shrinks to ~30 LoC.

```bash
git commit -m "refactor(common): BaseExecutor with claim/heartbeat/complete lifecycle

aws/main.py: 456 → 32 LoC
azure/main.py: 469 → 33 LoC
gcp/main.py: 470 → 32 LoC
databricks/main.py: 403 → 30 LoC
snowflake/main.py: 403 → 30 LoC
~1,500 LoC of structural near-duplication eliminated."
```

**T-24 (1h) — `AuditEmitter` + chain + sinks.** Author `cloudmorph_common.audit.{emitter,chain,canonical_json}`. Sinks: stdout (MVP), s3 (MVP), buffered (MVP). Hash chain RFC 8785 canonical JSON.

```bash
git commit -m "feat(common): AuditEmitter with hash chain (RFC 8785 JCS) + stdout/s3/buffered sinks"
```

#### Afternoon (3h)

**T-25 (1h) — `ArtifactWriter` interface + S3/GCS/Blob impls.**

```bash
git commit -m "refactor(common): ArtifactWriter interface; S3/GCS/Blob impls move out of per-executor main.py"
```

**T-26 (30 min) — Common-py tests baseline.** `cloudmorph-common-py/tests/test_client.py`, `test_base_executor.py`, `test_audit_emitter.py`, `test_audit_chain.py`. Target 80% coverage by EOD Block C.

```bash
git commit -m "test(common): baseline coverage for ControlCenterClient, BaseExecutor, AuditEmitter, AuditChain"
```

**T-27 (30 min) — Common-ts skeleton.** Mirror `cloudmorph-common-ts/src/{audit,action-verbs,canonical-json}.ts`. The MCP server will import from here in Block D.

```bash
git commit -m "feat(common-ts): mirror Audit + canonical-json + action-verbs"
```

**T-28 (1h) — Verify dedup.** Run `find` to confirm no stray copies; run `wc -l` to confirm executor main.py shrinkage. Update `status/00_inventory.md` LoC counts.

```bash
find . -name 'controlcenter_client.py' -not -path './cloudmorph-common-py/*'   # should be empty
wc -l aws/executor/src/main.py azure/executor/src/main.py gcp/executor/src/main.py databricks/executor/src/main.py snowflake/executor/src/main.py
git commit -m "docs(status): refresh inventory after Block C dedup (901 LoC removed)"
```

**T-29 (close out day 3) — Block D start: MCP SDK migration plan.** Read `@modelcontextprotocol/sdk` docs; sketch the swap. Add to package.json:

```bash
cd cloudmorph-mcp
npm install @modelcontextprotocol/sdk @open-policy-agent/opa-wasm pino prom-client @opentelemetry/sdk-node @opentelemetry/api lru-cache vitest @vitest/coverage-v8 undici
git commit -m "chore(mcp): add core deps — MCP SDK, OPA WASM, pino, OTel, prom-client, vitest"
```

---

### Day 4 — 2026-04-27 — Block D: MCP server core

#### Morning (3h)

**T-30 (2h) — Refactor `routes.ts` 583 LoC → tool registry.** Split into:
- `cloudmorph-mcp/src/tools/registry.ts`
- `cloudmorph-mcp/src/tools/cloudmorph_request.ts` (existing)
- `cloudmorph-mcp/src/tools/cloudmorph_request_status.ts` (existing)
- `cloudmorph-mcp/src/tools/cloudmorph_job_status.ts` (existing)
- `cloudmorph-mcp/src/router.ts` (express mount, ~80 LoC)

Each tool exports `{ name, description, inputSchema, handler }`. Router constructs from registry.

```bash
git commit -m "refactor(mcp): split routes.ts (583 LoC) into tool registry + per-tool files

No functional change. routes.ts → router.ts (80 LoC) + tools/ directory (3 files × ~60 LoC).
Sets up Block D additions to be 1-file each."
```

**T-31 (1h) — Migrate to `@modelcontextprotocol/sdk` for stdio.** Add `cloudmorph-mcp/src/transports/stdio.ts`:

```typescript
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "../server";

const server = createServer();   // shared with HTTP variant
const transport = new StdioServerTransport();
await server.connect(transport);
```

`cloudmorph-mcp/src/index-stdio.ts` is the entrypoint. Add `bin` script.

```bash
git commit -m "feat(mcp): add stdio transport via @modelcontextprotocol/sdk

bin/cloudmorph-mcp-stdio launches stdio mode for local dev / Cursor / Claude Desktop."
```

#### Afternoon (3h)

**T-32 (2h) — `TokenResolver`.** `cloudmorph-mcp/src/auth/resolver.ts`:

```typescript
export class TokenResolver {
  private cache = new LRUCache<string, ResolvedToken>({ max: 10_000, ttl: 30_000 });
  
  constructor(private upstreamUrl: string) {}
  
  async resolve(token: string): Promise<ResolvedToken> {
    const cached = this.cache.get(token);
    if (cached) return cached;
    
    const resp = await fetch(`${this.upstreamUrl}/v1/auth/verify`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new TokenInvalidError(resp.status);
    
    const resolved: ResolvedToken = await resp.json();
    this.cache.set(token, resolved);
    return resolved;
  }
}
```

Tests + 90% cache-hit measurement.

```bash
git commit -m "feat(mcp): TokenResolver with 30s LRU cache

Eliminates 1-3 upstream round trips per request once cache warms.
Targets >90% cache-hit ratio in steady state."
```

**T-33 (1h) — Real Vitest suite replacing `npm test`.** `cloudmorph-mcp/vitest.config.ts`. First tests: `tests/unit/auth.test.ts`, `tests/unit/ratelimit.test.ts` (the actual TS module, replacing the deleted Python re-impl).

```bash
sed -i 's|"echo \\"No tests yet\\" && exit 0"|"vitest run --coverage"|' package.json
git commit -m "test(mcp): real Vitest suite — auth + ratelimit unit tests; npm test no longer no-op"
```

---

### Day 5 — 2026-04-28 — Block D continues

#### Morning (3h)

**T-34 (3h) — Audit emitter + chain in MCP server.** `cloudmorph-mcp/src/audit/{emitter,chain,canonical-json}.ts` + `sinks/{stdout,s3,buffered}.ts`. Mirror common-py impl. Wire to `routes.ts` so every decision path emits.

```bash
git commit -m "feat(mcp): audit emitter with hash chain + stdout/s3/buffered sinks

Matches common-py impl; chain verifiable via npx @cloudmorph/audit-verify."
```

#### Afternoon (3h)

**T-35 (2h) — `cloudmorph_declare_intent` tool.** `cloudmorph-mcp/src/tools/cloudmorph_declare_intent.ts`. Plus `cloudmorph-mcp/src/intent/session_store.ts` (in-memory MVP). Plus `cloudmorph_revoke_intent` (1h adder).

```bash
git commit -m "feat(mcp): add cloudmorph_declare_intent + cloudmorph_revoke_intent tools

Session store: in-memory Map with TTL. Validates IntentDeclaration schema.
Emits intent.declared / intent.revoked audit events."
```

**T-36 (1h) — `cloudmorph_explain_decision` tool.** Returns `PolicyDecision` for a `decisionId`. Reads from in-memory decision log (ring buffer, last 1000 per tenant). Post-MVP: query audit log for older decisions.

```bash
git commit -m "feat(mcp): add cloudmorph_explain_decision tool

Returns matched rules + evaluationTrace for the decisionId.
MVP: ring buffer of last 1000 decisions per tenant. Post-MVP: query audit log."
```

---

### Day 6 — 2026-04-29 — Block D finish + Block E start

#### Morning (3h)

**T-37 (3h) — `cloudmorph_proxy` tool, the killer feature.** `cloudmorph-mcp/src/tools/cloudmorph_proxy.ts`. Schema per [mcp/01 §1.2a](mcp/01_server_audit.md). Server-side flow:
1. Resolve `(tenantId, sessionId, intentId)`
2. Synthesize `ToolCallRequest` for the wrapped action
3. Evaluate via policy engine
4. Branch on outcome (allow/deny/mutate/redact/approve/throttle/audit_only)
5. Forward `tools/call` to downstream MCP via HTTP (MVP)
6. Filter response per redactionFields
7. Emit AuditEvent

Smoke test against an example downstream MCP server (use `npx @modelcontextprotocol/server-everything` as the dummy).

```bash
git commit -m "feat(mcp): add cloudmorph_proxy tool — the killer feature

Wraps any downstream MCP server with policy enforcement.
HTTP transport in MVP; stdio + SSE post-MVP.
Tested against @modelcontextprotocol/server-everything as downstream."
```

#### Afternoon (3h) — Block E: Policy engine

**T-38 (3h) — OPA WASM engine v1.** `cloudmorph-mcp/src/policy/engine.ts` wrapping `@open-policy-agent/opa-wasm`. Loads pre-compiled `policy.wasm` from bundle. Exposes `evaluate(input) → PolicyDecision`.

Pre-compile a sample bundle:

```bash
cd cloudmorph-mcp/test-fixtures/bundles/readonly
cat > rules/main.rego <<EOF
package cm.decision
default outcome := "deny"
default reason := "no_matching_rule"
outcome := "allow" if input.toolCall.action in {"aws.s3.list_buckets", "aws.s3.list_objects"}
EOF
opa build -t wasm -e cm/decision -o opa/policy.wasm rules/
tar czf bundle.tar.gz manifest.json rules/ opa/policy.wasm
echo "$(sha256sum bundle.tar.gz | awk '{print $1}')" | hmac-sha256 -k testkey > bundle.sig
```

Engine integration test: load bundle, call evaluate, verify allow/deny outcomes.

```bash
git commit -m "feat(policy): OPA WASM engine v1 with sample readonly bundle"
```

---

### Day 7 — 2026-04-30 — Block E continues

#### Morning (3h)

**T-39 (2h) — Bundle loader: signature verify + hot reload.** `cloudmorph-mcp/src/policy/bundle.ts`. HMAC-SHA256 verification. `fs.watch` for path mode; `setInterval` poll for URL mode. Blue-green swap:

```typescript
class PolicyEngineHost {
  private active: PolicyEngine | null;
  private staged: PolicyEngine | null;
  private inflightCount = 0;
  
  async stageReload(bundle: Buffer) { /* ... */ }
  async evaluate(input: PolicyInput): Promise<PolicyDecision> { /* ... */ }
}
```

```bash
git commit -m "feat(policy): bundle loader with HMAC verify + blue-green hot reload"
```

**T-40 (1h) — Decision cache LRU 10s TTL.** `cloudmorph-mcp/src/policy/cache.ts`. Key per [policy/05 §1.10](policy/05_policy_engine_design.md). Tests for cache eviction on bundle reload.

```bash
git commit -m "feat(policy): decision cache LRU 10s TTL with bundle-reload invalidation"
```

#### Afternoon (3h)

**T-41 (2h) — Intent matcher: lexical match.** `cloudmorph-mcp/src/intent/matcher.ts`:

```typescript
export class IntentMatcher {
  constructor(private actionVerbs: Map<string, Set<string>>) {}
  
  match(intent: IntentDeclaration | undefined, action: string, args: any): MatchResult {
    if (!intent) return { verdict: "match", stage: "no_intent_declared" };
    const declared = new Set(intent.structuredVerbs);
    const required = this.actionVerbs.get(action) ?? new Set();
    if (required.size === 0) return { verdict: "ambiguous", reason: "unknown_action_verb_mapping" };
    if (isSubset(required, declared)) return { verdict: "match", stage: "lexical" };
    if (intersects(required, declared)) return { verdict: "ambiguous", stage: "lexical" };
    return { verdict: "mismatch", stage: "lexical", reason: ... };
  }
}
```

`actionVerbs` from `cloudmorph-common-ts/src/action-verbs.ts`. Stage 2 (semantic) and Stage 3 (LLM judge) stubbed for MVP.

```bash
git commit -m "feat(intent): IntentMatcher lexical stage; semantic and llm_judge stubbed"
```

**T-42 (1h) — Wire TokenResolver + IntentMatcher + PolicyEngine + DecisionCache + AuditEmitter into `cloudmorph_request`.** End-to-end: token → resolver → intent match → cache lookup → engine evaluate → forward → emit audit. **MVP firewall is now functional.**

```bash
git commit -m "feat(mcp): wire end-to-end firewall flow in cloudmorph_request

Token → resolver → intent match → cache → engine → forward → audit.
MVP firewall functional; first decision emits AuditEvent with hash chain."
```

---

### Day 8 — 2026-05-01 — Block E finish + Block F start

#### Morning (3h)

**T-43 (2h) — Rule taxonomy v1: 8 categories with example bundles.** Author 5 reference bundles (`test-fixtures/bundles/{readonly,intent-required,deny-destructive,mutate-row-cap,approve-high-blast}.tar.gz`). Each demonstrates 1-2 rule categories.

```bash
git commit -m "feat(policy): 5 reference bundles demonstrating allowlist/denylist/intent-conditional/mutate/approve rules"
```

**T-44 (1h) — Fixture suite: 30 (request, expected_decision) pairs.** `cloudmorph-mcp/tests/fixtures/decisions/*.json`. Vitest test that loads each, evaluates, asserts.

```bash
git commit -m "test(policy): 30-pair decision fixture suite — CI gate on regression"
```

#### Afternoon (3h) — Block F: SDK firewall

**T-45 (2h) — `firewall.start_proxy()` MCP-proxy primary.** `sdk-python/cloudmorph/firewall.py`. Spawns local proxy server pointed at Control Centre + downstream URL. Uses subprocess + Node `cloudmorph-mcp` in stdio mode.

```bash
git commit -m "feat(sdk): firewall.start_proxy() MCP-proxy primary integration

Spawns local stdio MCP server pointed at Control Centre + downstream URL.
Zero changes in agent code — point its MCP config at the local socket."
```

**T-46 (1h) — `@firewall.govern` decorator.** Wraps a tool-dispatch function: pre-call evaluates, post-call emits audit.

```bash
git commit -m "feat(sdk): @firewall.govern decorator pattern for raw tool-call loops"
```

---

### Day 9 — 2026-05-02 — Block F continues

#### Morning (3h)

**T-47 (2h) — Anthropic adapter.** `sdk-python/cloudmorph/adapters/anthropic.py`. `GovernedAnthropic` class wraps `Anthropic.messages.create`. Heuristic intent extractor (system prompt → verb set).

```bash
git commit -m "feat(sdk): cloudmorph.adapters.anthropic GovernedAnthropic

Drop-in replacement for anthropic.Anthropic. Extracts intent via
heuristic from system prompt; policy-evaluates every ToolUseBlock."
```

**T-48 (1h) — OpenAI adapter.** `sdk-python/cloudmorph/adapters/openai.py`. Same pattern.

```bash
git commit -m "feat(sdk): cloudmorph.adapters.openai GovernedOpenAI"
```

#### Afternoon (3h)

**T-49 (2h) — `AsyncCloudMorph` via httpx.** `sdk-python/cloudmorph/async_client.py`. Mirror sync API. Httpx with HTTP/2.

```bash
git commit -m "feat(sdk): AsyncCloudMorph via httpx — async parity with sync client"
```

**T-50 (30 min) — `extras_require` + `py.typed`.** Update `sdk-python/pyproject.toml`. Drop `Development Status :: 5` to `4 - Beta`.

```bash
git commit -m "chore(sdk): add extras_require[anthropic,openai,langchain,...]; py.typed marker; downgrade to 4-Beta"
```

**T-51 (30 min) — Fix `CloudMorphError.code` mapping bug.** Per [sdk/03 §3.1](sdk/03_python_sdk_audit.md). Test pinning the fix.

```bash
git commit -m "fix(sdk): CloudMorphError.code now maps from JSON-RPC numeric code, not message text"
```

---

### Day 10 — 2026-05-03 — Block F finish + Block G start

#### Morning (3h)

**T-52 (2h) — LangChain adapter (stretch P1).** `sdk-python/cloudmorph/adapters/langchain.py`. `CloudMorphCallback(BaseCallbackHandler)`. If time-tight, defer.

```bash
git commit -m "feat(sdk): cloudmorph.adapters.langchain CloudMorphCallback"
```

**T-53 (1h) — SDK test suite extension to 90% coverage.** Tests for: 429 path, polling loop, async client, decorator, anthropic adapter happy + error paths.

```bash
git commit -m "test(sdk): coverage to 90% — async, polling, adapters, decorator"
```

#### Afternoon (3h) — Block G: Executor governance hooks

**T-54 (2h) — Snowflake QUERY_TAG injection.** Per [snowflake/04 §8.3.1](snowflake/04_executor_audit.md). 2h of work for the highest-leverage hook.

```bash
git commit -m "feat(snowflake): inject QUERY_TAG with cloudmorph metadata on every query

Customers can audit via SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY:
SELECT query_text, query_tag FROM ... WHERE query_tag LIKE 'cloudmorph:%'"
```

**T-55 (1h) — AWS IAM session tagging.** `aws/executor/src/auth.py` wraps boto3 `Session` with `STS.AssumeRole + Tags`. Per [aws/04 §4.3.1](aws/04_executor_audit.md).

```bash
git commit -m "feat(aws): IAM session tagging on every API call

Every CloudTrail row from this executor carries cloudmorph:request_id,
cloudmorph:intent_id, cloudmorph:policy_bundle, cloudmorph:tenant.
Customer joins CloudTrail to Control Centre decisions."
```

---

### Day 11 — 2026-05-04 — Block G continues

#### Morning (3h)

**T-56 (2h) — Databricks SQL execute_query handler.** Per [databricks/04 §7.3.1](databricks/04_executor_audit.md). Path B (REST interceptor). Sqlglot for `mutate` (auto-add `LIMIT 10000` if absent).

```bash
git commit -m "feat(databricks): databricks.sql.execute_query action with sqlglot mutate rules

Agent submits SQL through us; we evaluate (allow/deny/mutate),
optionally rewrite with LIMIT, forward to Databricks SQL Warehouse,
emit audit. Unlocks the data-platform value prop."
```

**T-57 (1h) — AWS EventBridge emitter.** `cloudmorph_common.audit.sinks.eventbridge` for AWS executor. `PutEvents` to customer-owned bus.

```bash
git commit -m "feat(aws): EventBridge audit sink for customer-owned bus"
```

#### Afternoon (3h)

**T-58 (1h) — Azure activity log correlation.** `x-ms-correlation-request-id` header injection. Per [azure/04 §5.3.1](azure/04_executor_audit.md).

```bash
git commit -m "feat(azure): inject x-ms-correlation-request-id on every Azure API call"
```

**T-59 (1h) — GCP cloud audit logs trace correlation.** OTel span context propagation through google.cloud.* SDKs.

```bash
git commit -m "feat(gcp): OTel trace propagation for Cloud Audit Logs correlation"
```

**T-60 (1h) — Replace destructive-substring blocker with policy-engine call.** Databricks + Snowflake job_runner.py: instead of `if "delete" in normalized: ...`, call MCP `cloudmorph_request` with the action name; respect the decision.

```bash
git commit -m "refactor(executors): destructive-action gating via policy engine, not substring matching

databricks/snowflake: lexical 'delete'/'remove'/'drop' substring blocker
replaced with explicit cloudmorph_request decision call. Policy bundle
authors decide; reduces false positives (e.g., read_dropped_table_history)
and false negatives."
```

---

### Day 12 — 2026-05-05 — Block H: Hardening

#### Morning (3h)

**T-61 (3h) — Adversarial fixtures: 30 to start.** `tests/adversarial/test_*.py`. Categories per [cross/08 §1.2 Layer 4](cross/08_tests_audit.md): prompt injection (8), intent spoofing (5), intent mismatch (4), TOCTOU approvals (4), policy bundle tampering (4), audit chain forgery (3), session hijack (3), cache poisoning (2), redaction bypass (4), replay (3), token leak (3) = 43. Aim for 30 minimum + names for the rest.

```bash
git commit -m "test(adversarial): 30 fixtures across 11 attack categories

Includes: prompt injection via tool args, intent spoofing,
TOCTOU in approval flows, audit chain forgery, redaction bypass.
Target ≥50 by post-MVP. CI fail on any regression."
```

#### Afternoon (3h)

**T-62 (2h) — Distributed rate limiter (Redis token-bucket).** `cloudmorph-mcp/src/ratelimit-redis.ts`. Replace in-memory backend; keep API. Per [mcp/01 §1.9](mcp/01_server_audit.md).

```bash
git commit -m "feat(mcp): Redis token-bucket rate limiter; in-memory remains as dev fallback"
```

**T-63 (1h) — Graceful shutdown.** SIGTERM handler drains WS hub + in-flight HTTP + flushes audit emitter.

```bash
git commit -m "feat(mcp): graceful shutdown — drain WS, in-flight HTTP, flush audit"
```

---

### Day 13 — 2026-05-06 — Block H finish + Block I

#### Morning (3h)

**T-64 (2h) — `/metrics` Prometheus + OTel tracing.** `cloudmorph-mcp/src/metrics.ts` + `tracing.ts`. ~30 metrics per [cross/11 §1.2](cross/11_observability_and_slo.md). One trace span per: `mcp.receive`, `token.resolve`, `intent.match`, `policy.evaluate`, `upstream.call`, `audit.emit`.

```bash
git commit -m "feat(mcp): Prometheus /metrics endpoint and OpenTelemetry tracing

30 metrics labeled by tenant_tier (no tenant_id — cardinality control).
OTel spans: mcp.receive → token.resolve → intent.match → policy.evaluate → audit.emit."
```

**T-65 (1h) — Container hardening.** Per [cross/09 §1.1](cross/09_packaging_and_docker_audit.md):
- MCP Dockerfile: `npm ci`, `USER node`, `HEALTHCHECK`
- Executor Dockerfiles: drop unused cloud SDK installs
- Multi-arch buildx + Cosign signing in `.github/workflows/docker.yml`

```bash
git commit -m "build(docker): harden 6 Dockerfiles — npm ci, non-root user, HEALTHCHECK, drop cross-cloud bloat"
git commit -m "ci(docker): multi-arch buildx + Trivy scan + Cosign signing"
```

#### Afternoon (3h) — Block I: Ship

**T-66 (2h) — Hosted SaaS deploy.** `terraform/cloudmorph-mcp-hosted/`:
- ECS Fargate service (3 tasks, autoscale 3-30)
- ALB with ACM cert
- ElastiCache Redis (cache.t4g.small)
- S3 buckets (audit-default, bundles)
- CloudFront CDN for bundles
- CloudWatch Logs group
- IAM roles (task execution, app)

`mcp.cloudmorph.io` → ALB. Apply via:
```bash
cd terraform/cloudmorph-mcp-hosted
terraform init && terraform plan && terraform apply
```

```bash
git commit -m "deploy(hosted): Terraform module for mcp.cloudmorph.io ECS Fargate stack"
git commit -m "deploy(hosted): mcp.cloudmorph.io live in production v0.1.0-mvp"
```

**T-67 (1h) — Design partner integration.** Pair with the chosen scrappy startup. They:
1. Sign up at console.cloudmorph.io (or accept test tenant manually)
2. Mint integration token
3. `pip install cloudmorph` (locally; we do test-PyPI publish in T-72)
4. Add `firewall.start_proxy(cm_token=..., upstream_mcp_url=...)` to their agent
5. Run their golden-path agent task — observe decisions in `cloudmorph_explain_decision`

Verify p99 < 50ms for cached decisions in the bench harness. Audit log shows hash chain end-to-end. Customer can run `npx @cloudmorph/audit-verify` to confirm.

```bash
git commit -m "feat(launch): design partner X integrated end-to-end

Their <agent name> declared intent, called <action>, received
allow decision in <eval_ms>ms. Audit chain verified from their
S3 bucket. p99 cached decision < 50ms confirmed."
```

---

### Day 14 — 2026-05-07 — Ship

#### Morning (3h)

**T-68 (1h) — Docs.** Author/update:
- `docs/deployment.md` — hosted SaaS + self-hosted (skeleton)
- `docs/policy-authoring.md` — Rego primer + bundle structure
- `docs/intent-guide.md` — verb taxonomy + how to declare
- `docs/sdk-reference.md` — pyhon SDK API
- `docs/getting-started.md` — update with corrected SDK references (drop the imaginary TS SDK reference until built)

```bash
git commit -m "docs(launch): deployment / policy-authoring / intent-guide / sdk-reference"
```

**T-69 (30 min) — README updates** at root and in `cloudmorph-mcp/`. Reference architecture, link to docs.

**T-70 (30 min) — CHANGELOG.md** for v0.1.0 with everything shipped.

**T-71 (1h) — Tag and push.**

```bash
git tag -a v0.1.0-mvp -m "MVP: runtime firewall with intent capture + cloudmorph_proxy"
git push origin main --tags
```

#### Afternoon (3h)

**T-72 (1h) — PyPI publish (SDK).** Via GitHub Actions trusted publishing on the `sdk-v0.1.0` tag.

```bash
git tag -a sdk-v0.1.0 -m "First public SDK release"
git push origin sdk-v0.1.0
```

PyPI publishes via GHA; verify at `pip install cloudmorph` from a fresh venv.

**T-73 (1h) — GHCR image push.** Via GHA on the `v0.1.0-mvp` tag — multi-arch builds + cosign signing.

Verify: `docker run --rm ghcr.io/cloudmorphai/cloudmorph-mcp:v0.1.0-mvp --help` works.

**T-74 (1h) — Launch announcement** + design-partner reference quote (if granted) on cloudmorph.io blog post or X.

```bash
git commit -m "docs(launch): MVP shipped 2026-05-07 — v0.1.0-mvp tagged

Design partner: <name>; one engineering team integrated; firewall live."
```

---

## §3 What's NOT in the 14-day MVP

Marked `[POST-MVP]` throughout. Concentrated list:

- **MCP server tools:** `cloudmorph_replay`, `cloudmorph_redact_preview`, `cloudmorph_session_*`, `cloudmorph_list_sessions`, `cloudmorph_list_policies`, `cloudmorph_approve` / `cloudmorph_deny`. Most ship as scaffolding (registry stubs).
- **MCP server transports:** SSE.
- **MCP server auth:** mTLS, OIDC for approvers.
- **Policy engine:** OCI bundle distribution; per-event Ed25519 audit signatures; semantic-stage intent matcher in production quality; LLM judge for real (MVP stub returns "match").
- **SDK adapters:** Bedrock, Pydantic AI, CrewAI, AutoGen, Cohere, LangChain (stretch — slip if tight), LlamaIndex (stretch).
- **Executors:** registry refactor of all 5 job_runner.py files (only AWS gets it MVP; others stay as `if/elif` chains). Compile-to-IAM / Compile-to-Azure-Policy / Compile-to-Org-Policy / Compile-to-Snowflake-row-access. Notebook governance for Databricks. Cortex Agents governance.
- **Hosting:** multi-region active-active, Helm chart, Terraform multi-cloud, customer-owned audit sink, OIDC providers beyond Google.
- **Observability:** managed customer dashboard (lives in Console), per-tenant SLA dashboards, APM profiling.
- **Tests:** AWS/Azure/GCP per-handler integration tests beyond smoke (~120h slipped).
- **Docs:** docs site (`docs.cloudmorph.io`), interactive playground.

---

## §4 Slip-and-survive plan

If any block runs over (it will):

| Block | If runs over | Drop / defer | Replacement |
|---|---|---|---|
| A | +1 day | None — must finish | Compress B by 1 day (skip 3 schemas → 2) |
| B | +1 day | Drop Session + PolicyBundle schemas | Inline as TS interfaces in MCP server |
| C | +1 day | Skip TS-side common; keep MCP server's existing types | Extract TS post-MVP |
| D | +2 days | Drop `cloudmorph_proxy` | Ship without — biggest hit, reconsider whole timeline |
| E | +1 day | Use a single hand-rolled rule engine instead of OPA | OPA migration post-MVP — major risk |
| F | +1 day | Drop OpenAI adapter | Ship Anthropic-only |
| G | +1 day | Drop Databricks SQL execute_query | Snowflake QUERY_TAG only — much smaller hook |
| H | +1 day | Drop adversarial fixtures (10 instead of 30) | Ship with weaker test gate |
| I | impossible | Slip ship date by minimum days | One day slip = loss-of-momentum risk |

**The most fragile piece** is Block D's `cloudmorph_proxy` (12h). Without it, the MVP doesn't have the killer feature; the design partner integration is "just" the intent + policy evaluation. Still useful, less remarkable.

**The least slippable** is Block A and Block I. Hygiene at the start and ship at the end.

---

## §5 Daily check-in template

Each day at end-of-day, the lead engineer commits a status update to `status/daily/YYYY-MM-DD.md`:

```markdown
# Day N — YYYY-MM-DD

## Done today
- T-XX: ...
- T-XX: ...

## In flight
- T-XX: 50% — what's left

## Blockers
- (none) | description + who's unblocking

## Tomorrow
- T-XX through T-XX

## Cumulative status
- Block A: ✓
- Block B: ✓
- Block C: 80%
- Block D: not started
- ...
```

Status updates live in this repo, committed as `docs(daily): YYYY-MM-DD status`.

---

## §6 What the day-14 demo looks like

**Setup (recorded loom or live):**
- Open Cursor / Claude Desktop
- Configure MCP server pointing at `mcp.cloudmorph.io` with `cm_<partner>_<token>`
- Open a project the partner cares about (e.g., their AWS account or their Snowflake)

**Demo:**
1. **Show the firewall in default-pass mode.** Run a benign action (`aws.s3.list_buckets`). Open `cloudmorph_explain_decision` for the requestId. Show: matched rule "allow_read_first", evaluation trace, eval_ms < 5ms.
2. **Declare an explicit intent.** `cloudmorph_declare_intent({statedGoal: "audit S3 public access for compliance", structuredVerbs: ["read.list", "read.describe"]})`. Confirm.
3. **Try a destructive action against the intent.** `cloudmorph_request({action: "aws.s3.delete_object", payload: {bucket: "test"}})`. Get back `decision: "deny", reason: "intent_mismatch: declared {read.list, read.describe}, attempted {write.delete}"`.
4. **Open the audit log.** Show the events in the customer-owned S3 bucket, hash-chained. Run `npx @cloudmorph/audit-verify --bundle s3://...` — confirms chain intact.
5. **Bonus: cloudmorph_proxy.** `cloudmorph_proxy({downstreamUrl: "https://github-mcp.example.com", downstreamAction: "issues/create", ...})` — shows the wedge: ANY MCP server can be governed.

**Time: 7 minutes. Result:** the partner says "yes, I'll deploy this in production".

---

## §7 Commit log expected at day 14

~80-120 commits over 14 days. Cadence: 5-10 per workday. Mostly small, focused, easy to revert. No squashed mega-commits.

Final tag: `v0.1.0-mvp` on `main`. PR cycle: optional — direct commits to main per founder preference. Reviewers at design partner say "OK".

---

## §8 Source-link references for the plan

- [00_inventory.md](00_inventory.md) — what we're starting from
- [mcp/01_server_audit.md](mcp/01_server_audit.md) — MCP server work source
- [contracts/02_contracts_audit.md](contracts/02_contracts_audit.md) — Block B detail
- [sdk/03_python_sdk_audit.md](sdk/03_python_sdk_audit.md) — Block F detail
- [aws/04_executor_audit.md](aws/04_executor_audit.md) etc. — Block G per-cloud
- [policy/05_policy_engine_design.md](policy/05_policy_engine_design.md) — Block E detail
- [intent/06_intent_system_design.md](intent/06_intent_system_design.md) — Block E intent
- [cross/07_common_layer_audit.md](cross/07_common_layer_audit.md) — Block C detail
- [cross/08_tests_audit.md](cross/08_tests_audit.md) — Block H test detail
- [cross/09_packaging_and_docker_audit.md](cross/09_packaging_and_docker_audit.md) — Block H + I packaging
- [cross/10_security_and_tenancy_audit.md](cross/10_security_and_tenancy_audit.md) — Block I security
- [cross/11_observability_and_slo.md](cross/11_observability_and_slo.md) — Block H observability
- [cross/12_strategic_open_questions.md](cross/12_strategic_open_questions.md) — Day 0 founder locks
- [ARCHITECTURE.md](ARCHITECTURE.md) — what we're building toward
