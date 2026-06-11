# Combinations Engine

Multi-op attack-chain detection, introduced in v0.6.0 (2026-05-18). Tracks sequences of MCP tool calls across a sliding time window and feeds four new condition types in the policy engine.

## What is a combination

A **combination** is a policy primitive that spans multiple consecutive MCP tool calls. Single-call policies (`arg_equals`, `predicted_cost`, etc.) fire on one request in isolation. A combination fires when an ordered sequence of tool calls, each a step in a recognised attack chain, is observed within a configurable time window.

Three threat models drive the combination catalog:

- **Slow-burn cost overrun** — no single call exceeds the threshold, but the aggregate cost of a chain (e.g. `RunInstances` → `CreateStack` → repeated Bedrock inference) accumulates to a budget-busting total.
- **Multi-step privilege escalation** — a series of individually-allowed IAM operations (`CreateRole` → `PutRolePolicy` → `AssumeRole`) that together grant principal access unobtainable by any single call.
- **Fan-out data exfiltration** — an op that creates a large output surface (`S3:GetObject` across multiple buckets, Redshift full-scan query, DynamoDB table scan) chained with a transmission op.

The `combination-index.json` catalog in `tessera-intelligence` lists the three signed bundles (one per cloud) that define the 45 chains shipped at v1.0.0. See `tessera-intelligence/arch/status/combinations.md` for the content side.

## Chain tracking lifecycle

**Class:** `tessera/cost/combinations.py:CombinationTracker`

The tracker is the in-process, in-memory backend for all combination conditions. It is a write-through cache of active chains keyed by `(tenant_id, scope_id, combination_id)`.

### Data structures

```
CombinationTracker
  _defs_by_id:   dict[combination_id → CombinationDef]
  _defs_by_op:   dict[tool_name → list[CombinationDef]]   # lookup index
  _active:       OrderedDict[(tenant, scope, combo_id) → ActiveChain]  # LRU order

ActiveChain
  combination_id: str
  started_at:     float   (unix epoch; set when first op observed)
  last_updated:   float
  observed_ops:   list[(tool_name, timestamp, args_dict)]
  aggregate_cost_usd: float
  principals:     set[str]
```

### Hot path: `record_op()`

Called **after every successful tool-call forward** (allow / log\_only / observation modes) in the proxy. Steps:

1. Look up `_defs_by_op[tool_name]` — O(1). If the tool is not a trigger op in any loaded combination, returns immediately (no lock acquired).
2. For each matching `CombinationDef`:
   - Retrieve or create an `ActiveChain` for `(tenant, scope, combination_id)`.
   - If the chain exists but is expired (`now - started_at > window_seconds`), replace it with a fresh chain (the window has closed; a new attack window starts now).
   - Append `(tool_name, now, args)` to `chain.observed_ops`.
   - Add `per_op_cost_usd` to `chain.aggregate_cost_usd`.
   - Move the chain to the end of the LRU `OrderedDict` (marks it as recently used).
3. Evict oldest chains if the per-tenant cap is exceeded (see Memory bound below).
4. Return the list of `ActiveChain` objects updated by this op (zero or more).

### Memory bound

Default cap: **1000 active chains per tenant**. After a `record_op()` call, if the number of chains for the calling tenant exceeds the cap, the oldest chains (front of the `OrderedDict`) are removed until the count is at the cap. This is LRU eviction: the most recently updated chains survive.

The cap is set at construction time (`max_chains_per_tenant` arg, default `CombinationTracker.DEFAULT_MAX_CHAINS_PER_TENANT = 1000`). Tessera Cloud deployments may override this via constructor injection.

### Expiry

A chain **expires** when `now - started_at > defn.window_seconds`. The window is defined per combination in the YAML (`window_seconds` field; typically 600 s for short-burst chains, 3600 s for slow-burn cost chains). Expiry is checked lazily: on `record_op()` (expired chain is replaced) and on `get_active_chain()` (expired chain is popped and `None` returned). `cleanup_expired()` is a maintenance method that sweeps all chains — not called from the hot path.

### Chain field semantics

| Field | What it means |
|---|---|
| `aggregate_cost_usd` | Sum of `per_op_cost_usd` across all ops recorded into this chain. `per_op_cost_usd` comes from the proxy's pre-fetch cost estimate (`cost_cache[tool_name]`) — the same value recorded in the audit event. |
| `ops_count` (method) | `len(observed_ops)` — raw count of calls recorded into the chain, including repeated calls to the same tool. |
| `window_seconds` (method) | `now - started_at` — elapsed time since the first op was observed. |
| `principals` (set) | Distinct `principal_id` values that contributed ops to this chain; populated when `principal` arg is passed to `record_op()`. |

## The four combination ConditionType members

Defined in `tessera/policy/schema.py`, dispatched in `tessera/policy/conditions.py:_DISPATCH`. Format matches the condition table in `policy-engine.md`.

| Condition | Discriminator literal | Parameters | Fail direction |
|---|---|---|---|
| `CombinationAggregateCostUsdGt` | `combination_aggregate_cost_usd_gt` | `threshold: float`, `combination_id: str \| None` | `False` (don't block) on missing tracker |
| `CombinationOpsCountGt` | `combination_ops_count_gt` | `threshold: int`, `combination_id: str \| None` | `False` on missing tracker |
| `CombinationWindowSecondsLt` | `combination_window_seconds_lt` | `threshold: float`, `combination_id: str` | `False` on missing tracker or no active chain |
| `CombinationIdMatches` | `combination_id_matches` | `combination_id: str` | `False` on missing tracker |

**Behaviour when `combination_id` is `None`** (applies to the first two): the evaluator iterates `tracker.all_active_chains(tenant, scope)` and returns `True` if **any** active chain in the scope satisfies the threshold. This lets a policy fire on "any chain is accumulating too much cost" without naming a specific combination.

**Fail direction**: all four conditions return `False` (don't block) when:
- The tracker is absent from context (tracker not loaded, combinations disabled).
- No active chain exists for the given `combination_id` in the current scope.

This is the same fail-open direction as `predicted_cost` and `cumulative_spend_today` — cost-gate uncertainty does not justify blocking a call.

## Context key: `combination_tracker`

The conditions locate the tracker via `tessera/policy/conditions.py:_get_combination_tracker(context)`. Resolution order:

1. `context["combination_tracker"]` — explicit injection; Tessera Cloud's `DynamoDB`-backed deployment or tests can supply a custom tracker here.
2. `tessera.cost.combinations.get_global_tracker()` — module-level process singleton set via `set_global_tracker(tracker)`. Used by the OSS proxy startup path.

If neither yields a tracker, all four conditions return `False`.

Note: the proxy's `proxy.py` build-evaluation-context step does not yet inject `combination_tracker` directly into the context dict (as of v1.0.0 — proxy integration is a v1.1.0 backlog item). The conditions reach the tracker via the module-level singleton path. The `proxy-enforcement-and-audit.md` context-dict table has been updated to document this.

## `intelligence.combinations_url` in tessera.yaml

The `IntelligenceConfig` model (`tessera/config.py`) holds the CDN URL configuration for all bundle types. There is no separate `combinations:` sub-model; the combination bundles are fetched and verified by `IntelligenceClient` using the same pipeline as mapping bundles and blast-radius bundles (bundle URL from `combination-index.json` → download → Ed25519 verify → unpack YAML → `load_from_yaml_docs()`). The `combination_bundle` kind is handled in `IntelligenceClient._fetch_bundle()` at the `kind: "combination_bundle"` branch.

The `CombinationTracker` is **not** automatically constructed from `TesseraConfig`; the proxy startup code that calls `intelligence_client.load_combinations()` → `set_global_tracker(tracker)` is the activation path.

## Tests

| File | What it covers |
|---|---|
| `tests/test_combinations.py` | Unit tests for `CombinationTracker`: chain creation, op recording, LRU eviction at cap, aggregate cost accumulation, window expiry, `cleanup_expired()`, `all_active_chains()` query. |
| `tests/test_v0_6_packs.py` | Integration test loading the combination-aware packs (`tri-cloud-cost-explosion-defense`, `tri-cloud-blast-radius-defense`) against a fixture tracker; confirms the four combination conditions evaluate correctly end-to-end. |

## Cross-references

- `arch/status/policy-engine.md` — full 25-condition catalog table; the four combination entries at rows 22–25 reference this document.
- `arch/status/integrations-and-cost.md` — `cost/` subpackage context; `CombinationTracker` lives alongside `CostEstimator` in `tessera/cost/`.
- `arch/status/proxy-enforcement-and-audit.md` — the interception flow and evaluation context; `combination_tracker` row in the context-dict table.
- `tessera-intelligence/arch/status/combinations.md` — the content side: bundle inventory, YAML schema, catalog contract, build/sign pipeline.
