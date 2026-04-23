# 05 — Policy Engine Design (`cloudmorph-mcp/src/policy/`)

_Green-field. The single largest piece of MVP work. ~36h to a credible v1._

---

## 1.1 The choice — OPA WASM, locked

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **OPA (Rego) via WASM** | Industry standard. Sub-ms eval. Security teams accept it. Battle-tested by Kubernetes/Envoy/Conftest. Bundles + signing already exist. | Rego syntax is foreign; learning curve. WASM blob is ~5 MB. | **Lock here.** |
| Cedar (AWS) | Strong types. Readable. Designed for authz. AWS adoption story. | Newer; smaller ecosystem. Less Rego-style explainability tooling. | Cedar v2 swap is a future-option once we have 3 real bundles and can compare. The WASM abstraction (§1.4) makes this tractable. |
| Hand-rolled DSL in TypeScript | Total control. Zero foreign syntax. | Becomes a liability. Every customer security team will want to compare it to a standard. | Reject. Avoid building a policy language. |
| Cel (Google) | Used by IAM Conditions, Kubernetes admission. Compact. | Lacks set-comprehension features Rego has. Less of an industry default for *policy*. | Reject for now. |

**Decision:** OPA WASM. Justification: every enterprise security team has either deployed Rego (Kubernetes admission, Envoy authz, GKE policies) or knows it from a compliance review. No surprise factor at sale time.

**Migration path** (if Rego ergonomics become a sales blocker): the engine integration is behind an `Engine` interface (§1.4). Replacing OPA with Cedar means writing a `CedarEngine` that satisfies the same interface and shipping new bundles.

---

## 1.2 Bundle architecture

A bundle is a signed tarball containing Rego sources, OPA-compiled WASM, optional data documents, and metadata.

```
bundle.tar.gz
├── manifest.json              # PolicyBundle envelope (see contracts/02)
├── rules/
│   ├── allow_read_first.rego      # tenant's allowlist of read.* actions
│   ├── deny_destructive.rego      # tenant's denylist of write.delete actions
│   ├── intent_match.rego          # intent-vs-action mismatch rules
│   ├── time_of_day.rego           # business-hours constraints
│   ├── approval_required.rego     # high-blast-radius actions need approval
│   ├── mutate_limits.rego         # auto-rewrite to enforce row caps
│   ├── throttle_costly.rego       # rate-limit per-action
│   └── redact_pii.rego            # post-execution response filtering
├── data/
│   ├── tenant_settings.json       # per-tenant constants (max cost, regions)
│   └── verb_taxonomy.json         # cross-cuts with intent system
├── opa/
│   └── policy.wasm               # pre-compiled by `opa build` — what we actually load
└── signature                       # HMAC-SHA256 over manifest + sha256(every file)
```

Build:
```
opa build -t wasm -e cm/decision -o policy.wasm rules/
tar czf bundle.tar.gz manifest.json rules/ data/ opa/policy.wasm
echo -n "$(sha256sum bundle.tar.gz | cut -d' ' -f1)" | hmac-sha256 -k $BUNDLE_HMAC_KEY > bundle.sig
```

**Why pre-compile to WASM:** runtime evaluation is sub-millisecond. Compiling at runtime takes 50-200ms per bundle (cold path); pre-compiling moves that to bundle authorship time.

---

## 1.3 Bundle distribution

Three modes, configurable per-tenant:

### Mode A — Filesystem mount

```bash
POLICY_BUNDLE_PATH=/etc/cloudmorph/bundles/active.tar.gz
```

The MCP server watches the file via `fs.watch`; on change, validates the signature, swaps in. Used by:
- Self-hosted deployments where the customer mounts a bundle volume
- Local dev (point at a directory, no signing needed if `POLICY_BUNDLE_INSECURE=true`)

### Mode B — Signed URL pull

```bash
POLICY_BUNDLE_URL=https://bundles.cloudmorph.io/tnt_abc/active.tar.gz
POLICY_BUNDLE_HMAC_KEY=<base64-secret>
POLICY_BUNDLE_POLL_SECONDS=60
```

Periodic GET; `If-None-Match` ETag negotiation; signature verify on bundle bytes. Used by hosted SaaS — bundle authoring lives in the Console UI; CDN serves bundles to MCP instances.

### Mode C — OCI registry artifact

```bash
POLICY_BUNDLE_OCI=ghcr.io/cloudmorphai/bundles:tnt_abc-v0.42.0
```

OCI artifact pull (cosign-signed). Used by customers running their own OCI registry as part of supply-chain hardening.

**MVP supports A and B. C is post-MVP.**

---

## 1.4 Engine interface

```typescript
// cloudmorph-mcp/src/policy/engine.ts
import { PolicyDecision, ToolCallRequest } from "../contracts";

export interface PolicyEngine {
  evaluate(input: PolicyInput): Promise<PolicyDecision>;
  reload(bundle: Buffer): Promise<void>;
  close(): Promise<void>;
  bundleId: string;
  bundleVersion: string;
}

export interface PolicyInput {
  toolCall: ToolCallRequest;
  intent?: IntentDeclaration;
  intentMatchScore?: { lexical: number; semantic?: number; verdict: "match" | "ambiguous" | "mismatch" };
  runtimeContext: RuntimeContext;
  tenantSettings: Record<string, unknown>;   // from bundle data/
}
```

Implementations:
- `OpaWasmEngine` — uses `@open-policy-agent/opa-wasm`
- `MockEngine` (test fixture) — returns scripted outcomes
- `CedarEngine` (future) — same interface

---

## 1.5 Hot-reload semantics

Blue-green swap with version vector:

```typescript
class PolicyEngineHost {
  private active: Engine | null;
  private staged: Engine | null;
  private inflightCount = 0;
  
  async evaluate(input: PolicyInput): Promise<PolicyDecision> {
    this.inflightCount++;
    try {
      return await this.active!.evaluate(input);
    } finally {
      this.inflightCount--;
      if (this.staged && this.inflightCount === 0) {
        const old = this.active;
        this.active = this.staged;
        this.staged = null;
        await old?.close();
        this.emit("bundle.swapped", { newId: this.active.bundleId });
      }
    }
  }
  
  async stageReload(bundle: Buffer): Promise<void> {
    const verified = await verifyBundle(bundle);
    const next = await OpaWasmEngine.fromBundle(verified);
    this.staged = next;
    if (this.inflightCount === 0) {
      // Immediate swap if quiescent
      const old = this.active;
      this.active = this.staged;
      this.staged = null;
      await old?.close();
    }
  }
}
```

In-flight evaluations finish on the old engine; new evaluations route to the new engine. Zero downtime, zero observable transition.

**Atomicity:** if the new bundle fails signature verification or fails OPA compile, swap is aborted; current bundle continues. Audit emits `policy.bundle.reload.failed` with reason.

---

## 1.6 Evaluation order

```
1. Tenant emergency lockdown (data flag in bundle: tenant.locked = true) → deny all, log "tenant_locked"
2. Tenant-level allowlist/denylist (rules in /allow_read_first.rego, /deny_destructive.rego) — first match wins
3. Intent-conditional rules — if intent is declared, evaluate intent-aware rules
4. Approval-required rules — return "approve" with approval shape
5. Mutate / redact rules — apply transformation
6. Throttle rules — return "throttle" with delayMs
7. Default deny (or allow, per tenant default)
```

Rego encoding:

```rego
package cm.decision

import future.keywords

default outcome := "deny"
default reason := "no_matching_rule"

# 1. Tenant lockdown
outcome := "deny" if input.tenantSettings.locked == true
reason := "tenant_locked" if input.tenantSettings.locked == true

# 2. Tenant denylist (first match wins)
outcome := "deny" if denylist_match
reason := matched_denylist_reason if denylist_match

# 3. Tenant allowlist
outcome := "allow" if {
    allowlist_match
    not denylist_match
}

# 4. Intent-conditional (if intent provided)
outcome := "deny" if {
    input.intent
    input.intentMatchScore.verdict == "mismatch"
}
reason := "intent_mismatch" if {
    input.intent
    input.intentMatchScore.verdict == "mismatch"
}

# 5. Approval-required
outcome := "approve" if {
    high_blast_radius_action
}

# ... etc
```

The MCP server passes `outcome` and `reason` into a `PolicyDecision` object plus matched-rule trace.

---

## 1.7 Rule taxonomy (with examples)

### Allowlist / denylist by action

```rego
allowlist_match if {
    input.toolCall.action in {
        "aws.s3.list_buckets",
        "aws.s3.list_objects",
        "aws.ec2.list_instances",
        "databricks.workspace.list_clusters",
        "snowflake.account.list_databases",
    }
}

denylist_match if {
    input.toolCall.action in {
        "aws.s3.delete_bucket",
        "aws.iam.delete_user",
    }
}
```

### Intent-conditional

Allow `aws.s3.delete_object` ONLY when intent's `statedGoal` matches "test fixture cleanup" AND `structuredVerbs` includes `write.delete`:

```rego
outcome := "allow" if {
    input.toolCall.action == "aws.s3.delete_object"
    input.intent.structuredVerbs[_] == "write.delete"
    contains(lower(input.intent.statedGoal), "test fixture cleanup")
}
```

### Intent-vs-action divergence

The MCP server pre-computes `intentMatchScore` (lexical + semantic + LLM-judge cascade — see [intent/06_intent_system_design.md](../intent/06_intent_system_design.md)). Rego uses the verdict:

```rego
outcome := "deny" if {
    input.intent
    input.intentMatchScore.verdict == "mismatch"
}
reason := sprintf("intent_mismatch: declared %v, attempted %v", [
    input.intent.structuredVerbs,
    input.toolCall.action,
]) if {
    input.intent
    input.intentMatchScore.verdict == "mismatch"
}
```

### Time-of-day rules

Production destructive actions denied outside maintenance windows:

```rego
outcome := "deny" if {
    is_destructive(input.toolCall.action)
    input.runtimeContext.sessionTags.environment == "production"
    not in_maintenance_window
}

in_maintenance_window if {
    hour := time.clock(time.now_ns())[0]
    hour >= 22  # 22:00 UTC
}
in_maintenance_window if {
    hour := time.clock(time.now_ns())[0]
    hour < 4    # before 04:00 UTC
}
```

### Approval-required

```rego
outcome := "approve" if {
    input.toolCall.action in {"aws.iam.create_user", "aws.s3.put_bucket_policy"}
}

approvalRequest := {
    "approvers": ["alice@acme.com", "bob@acme.com"],
    "minApprovals": 1,
    "ttlSeconds": 3600,
} if outcome == "approve"
```

### Mutate

Auto-narrow query result sets:

```rego
outcome := "mutate" if {
    input.toolCall.action == "databricks.sql.execute_query"
    not has_limit(input.toolCall.arguments.sql)
}

mutatedArguments := {
    "sql": sprintf("SELECT * FROM (%s) LIMIT 10000", [input.toolCall.arguments.sql]),
} if outcome == "mutate"
```

### Throttle

```rego
outcome := "throttle" if {
    input.toolCall.action == "snowflake.sql.execute_query"
    cost_estimate := estimate_credits(input.toolCall.arguments.sql)
    cost_estimate > 10  # > 10 credits
}
throttleDelayMs := 5000 if outcome == "throttle"
```

### Redact

Strip PII from response (post-execution; the MCP server applies after the executor returns):

```rego
outcome := "redact" if {
    input.toolCall.action == "snowflake.sql.execute_query"
    contains(input.toolCall.arguments.sql, "users")
}
redactionFields := ["/rows/*/email", "/rows/*/ssn"] if outcome == "redact"
```

### Cost rule

```rego
outcome := "deny" if {
    cost_estimate := estimate_cost(input.toolCall)
    cost_estimate > input.tenantSettings.maxCostUsd
}
reason := sprintf("cost_exceeds_budget: estimated $%.2f > limit $%.2f", [
    cost_estimate, input.tenantSettings.maxCostUsd,
]) if outcome == "deny"
```

`estimate_cost` is implemented in Rego with simple heuristics (action-name × payload-size) for MVP; richer models post-MVP.

### Composite

```rego
outcome := "approve" if {
    is_destructive(input.toolCall.action)
    input.runtimeContext.sessionTags.environment == "production"
    not human_present
}
```

---

## 1.8 Decision outcomes — locked taxonomy

7 outcomes, frozen for v1.

| Outcome | Semantics | Server behavior |
|---|---|---|
| `allow` | Proceed with original args. | Forward to executor / downstream MCP. |
| `deny` | Block. | Return `tools/call` result with `isError: true`, `reason`. |
| `approve` | Pause for human approval. | Create `Approval`, return `pending_approval` to caller. On approve → re-evaluate, then forward; on deny → return rejected. |
| `mutate` | Proceed with modified args. | Server replaces `arguments` with `decision.mutatedArguments` before forwarding. |
| `redact` | Proceed; filter response. | After executor returns, apply `decision.redactionFields` (JSON Pointer paths) before returning to caller. |
| `throttle` | Proceed after delay. | Server `setTimeout(decision.throttleDelayMs)` then forwards. Counts against rate-limit. |
| `audit_only` | Proceed; log loudly. | Forward as if `allow`, but emit prominent audit event with `outcome: "audit_only"`. Used for safe rule-rollout (test new rules in shadow before flipping to deny). |

**Why these 7:** every customer compliance use case maps cleanly. Avoids common antipatterns: no `allow_with_warning` (use `audit_only`), no `proxy_through_admin` (use `approve`), no silent transforms (every `mutate` is logged).

---

## 1.9 Decision evidence

Every `PolicyDecision` includes:

- `matchedRules: [{ruleId, outcome, weight}]` — every rule that fired
- `evaluationTrace: [...]` — step-by-step trace; available via `cloudmorph_explain_decision`
- `evidence: {intentMatchScore, costEstimate, ...}` — auxiliary signals

The trace is deterministic and reproducible: feeding the same `PolicyInput` to the same `bundleId@version` always produces the same trace. This is **the explainability moat** — security buyers will demand "show me why this decision".

---

## 1.10 Decision cache

Key: `sha256(bundleId + bundleVersion + tenantId + canonicalJson({action, intentId, payload, runtimeContext.sessionTags}))`.

TTL: **10s** default. Aggressive enough to avoid recomputing the same decision twice in a hot loop; short enough that a bundle reload hits within 10s. Configurable per-tenant.

Implementation: in-process LRU (`lru-cache` npm). Capacity 10,000 entries (~10 MB for typical decision objects). Evict on:
- LRU
- Bundle reload (entire cache invalidated)
- Manual flush (`POST /admin/cache/flush` — admin-only)

Cache hit ratio target: **70%+** in steady state. Track via `cm_decision_cache_hits_total` / `cm_decision_cache_misses_total` Prometheus counters.

**Why not Redis cache:** in-process eliminates a network hop. For a horizontally scaled fleet, each instance has its own cache, which is fine — at most you compute the same decision once per instance per 10s window. Redis adds latency for marginal benefit.

---

## 1.11 Test strategy

Three layers:

### Layer 1 — Rego unit tests

OPA has built-in `opa test`. Each rule file gets a `_test.rego`:

```rego
package cm.decision_test

import data.cm.decision

test_allow_read_first if {
    decision.outcome == "allow" with input as {
        "toolCall": {"action": "aws.s3.list_buckets"},
        "tenantSettings": {"locked": false},
    }
}

test_deny_destructive_outside_window if {
    decision.outcome == "deny" with input as {
        "toolCall": {"action": "aws.s3.delete_bucket"},
        "runtimeContext": {"sessionTags": {"environment": "production"}},
    }
}
```

Run via `opa test rules/`. CI gate. **Coverage target 90% on Rego.**

### Layer 2 — Engine integration tests

In TypeScript via Vitest, test the `OpaWasmEngine` against real compiled bundles:

```typescript
describe("OpaWasmEngine", () => {
  let engine: OpaWasmEngine;
  beforeAll(async () => {
    const bundle = await fs.readFile("./test-fixtures/bundle-readonly.tar.gz");
    engine = await OpaWasmEngine.fromBundle(bundle);
  });

  it("allows aws.s3.list_buckets", async () => {
    const decision = await engine.evaluate({
      toolCall: { action: "aws.s3.list_buckets", arguments: {} },
      tenantSettings: { locked: false },
    });
    expect(decision.outcome).toBe("allow");
  });
  
  it("denies on intent mismatch", async () => {
    const decision = await engine.evaluate({
      toolCall: { action: "aws.s3.delete_object", arguments: {} },
      intent: { structuredVerbs: ["read.list"], statedGoal: "audit s3 access" },
      intentMatchScore: { lexical: 0.0, verdict: "mismatch" },
      tenantSettings: {},
    });
    expect(decision.outcome).toBe("deny");
    expect(decision.reason).toContain("intent_mismatch");
  });
  
  // ... ~30 fixtures for MVP, target ≥50 by post-MVP
});
```

### Layer 3 — Replay testing

Capture real decisions from staging into `replay/<date>/decisions.jsonl`. Periodically replay against a candidate bundle:

```bash
cm-policy-replay \
  --bundle ./candidates/v0.43.0.tar.gz \
  --decisions ./replay/2026-04-23/decisions.jsonl \
  --baseline ./baselines/v0.42.0-decisions.jsonl

# Output:
# 12,847 decisions replayed
# 12,820 unchanged (allow→allow, deny→deny)
#     12 changed (allow→deny) — were allowed, now denied
#      8 changed (deny→allow) — were denied, now allowed
#      7 errors (rule eval failed)
# Approve before promoting bundle.
```

Critical for safe rollout — before flipping a new bundle to production, prove what changes vs current.

---

## 1.12 Performance targets

| Metric | Target | Measurement |
|---|---|---|
| OPA WASM eval (single rule) | < 200μs p50, < 1ms p99 | bench/`opa-eval-bench.ts` |
| Engine evaluate() (full Input → Decision) | < 500μs p50, < 2ms p99 cold, < 50μs cached | bench/`engine-bench.ts` |
| Bundle load (verify + compile) | < 500ms | bench/`bundle-load-bench.ts` |
| Bundle hot-reload (blue-green swap, in-flight finishes) | < 5s end-to-end | integration test |
| Cache hit ratio | > 70% steady state | Prometheus |

If OPA WASM doesn't hit < 1ms p99 single-rule on the deployment hardware, alternative is `opa eval -t exec` (subprocess) — slower but more diagnostic. Keep both interfaces compatible.

---

## 1.13 Security considerations

- **Bundle signing is mandatory in production.** `POLICY_BUNDLE_INSECURE=true` only allowed when `NODE_ENV !== "production"`.
- **HMAC key stored in cloud secret manager**, not env. AWS Secrets Manager / GCP Secret Manager / Azure Key Vault.
- **Rego sandbox:** OPA WASM is sandboxed by WASM itself — cannot do file IO, network, or access process state. Even a malicious bundle cannot exfil data.
- **Bundle injection:** the bundle distribution path must be authenticated. Filesystem path is trusted (root permissions); URL pull validates HMAC; OCI uses cosign.
- **Decision tampering:** the engine returns decisions in-process; no tamper opportunity until they hit the audit log (which has its own hash chain — see [01_server_audit.md §1.7](../mcp/01_server_audit.md)).

---

## 1.14 Failure modes

| Failure | Behavior | Severity |
|---|---|---|
| Bundle signature invalid | Reject load, current bundle continues, alert | P0 |
| Bundle compile error | Reject load, current bundle continues, alert | P0 |
| OPA WASM crash mid-eval | Per-tenant fail-closed (default) or fail-open (opt-in) | P0 |
| Bundle URL unreachable | Continue with cached bundle, retry with backoff, alert after 3 failures | P1 |
| Decision cache OOM | Evict aggressively (already LRU); alert; never block evaluation | P1 |
| Rego rule infinite loop | OPA has built-in iteration limits — eval errors out; treated as fail-closed | P1 |
| Tenant settings missing | Use empty defaults (which are restrictive); alert | P2 |

**Fail-open vs fail-closed when engine is unhealthy:**
- Default: **fail-closed** for safety.
- Per-tenant config: `POLICY_FAIL_MODE=open` enables fail-open (every request returns `audit_only` decision).
- When fail-open is active, **emit a loud audit event every evaluation** so the customer cannot silently rely on it.

---

## 1.15 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| OPA WASM engine integration | P0 | 12h | E |
| Bundle loader (filesystem + URL modes) | P0 | 8h | E |
| Bundle signature verification | P0 | 4h | E |
| Hot-reload blue-green swap | P0 | 4h | E |
| Rule taxonomy (8 categories with example bundles) | P0 | 12h | E |
| 7 decision outcomes wired end-to-end | P0 | 8h | D+E |
| Decision cache (LRU 10s TTL) | P0 | 4h | E |
| Bundle versioning + version-vector logging | P1 | 3h | E |
| Decision evidence + evaluationTrace | P0 | 6h | E |
| Test fixture suite (≥30 decisions) | P0 | 8h | E |
| Replay test harness | P1 | 8h | H |
| OCI bundle distribution | P2 | 8h | post-MVP |
| `cm-policy-replay` CLI | P2 | 6h | post-MVP |
| Per-tenant fail-mode config | P1 | 3h | H |
| Bench harness for OPA eval | P1 | 4h | H |

**MVP critical-path total: ~78h.** Block E. The largest single chunk of MVP work.

---

## 1.16 Source-link references for the MCP server integration

- [../mcp/01_server_audit.md §1.5](../mcp/01_server_audit.md) for MCP integration points
- [../intent/06_intent_system_design.md](../intent/06_intent_system_design.md) for intentMatchScore producer
- [../contracts/02_contracts_audit.md §2.3](../contracts/02_contracts_audit.md) for the `PolicyDecision` and `PolicyBundle` contracts
- [../ARCHITECTURE.md §6](../ARCHITECTURE.md) for system-level diagram
