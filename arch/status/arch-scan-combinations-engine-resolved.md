# arch-scan — v0.6.0 combinations engine is undocumented in arch/status

**Severity**: MEDIUM-HIGH — entire subsystem missing from arch docs.
**Discovered**: 2026-05-21 arch-scan pass.
**Status**: Open.

## Problem

The v0.6.0 release (2026-05-18, current source-tree version) shipped a complete multi-op attack-chain detection engine that has zero coverage in `arch/status/`:

| Component | Where | What it does |
|---|---|---|
| `tessera/cost/combinations.py` | New module | `CombinationTracker` — memory-bounded (1000 chains/tenant, LRU) tracker that aggregates op chains within a sliding window. Exposes `aggregate_cost_usd`, `ops_count`, `window_seconds`, `principals_count`. |
| `tessera/policy/schema.py` | Modified | 4 new `ConditionType` union members: `combination_aggregate_cost_usd_gt`, `combination_ops_count_gt`, `combination_window_seconds_lt`, `combination_id_matches`. |
| `tessera/policy/conditions.py` | Modified | `_DISPATCH` entries for the 4 new conditions. |
| `tessera/config.py` | Modified | `CombinationsConfig` sub-model under `combinations:` in `tessera.yaml`. |
| `tests/test_combinations.py` | New | Unit tests for chain tracking, LRU eviction, aggregate cost. |
| `tests/test_v0_6_packs.py` | New | Integration test for combination-aware packs. |
| `combination-index.json` artifact family | tessera-intelligence | New catalog + 45 attack-chain YAMLs (15 per cloud × 3) signed in dist/. |

The v0.6.0 release shipped end-to-end (signed content in `tessera-intelligence/dist/`, customer-fetchable, exercised by paid `tri-cloud-cost-explosion-defense` and `tri-cloud-blast-radius-defense` packs).

None of the 7 `arch/status/*.md` docs in this repo mention combinations. `policy-engine.md` previously said "21-condition catalog" (was fixed in-place during the 2026-05-21 arch-scan to "25-condition catalog" + a brief table extension).

## Approach

Three options, ordered by completeness:

### Option A — extend existing status docs (minimal)

- `policy-engine.md` — the 4 new conditions are now in the table (in-place fix done). Add a "Combination conditions and chain context" subsection explaining how the conditions read from `combinations_backend` in eval context.
- `proxy-enforcement-and-audit.md` — step 6 "Build evaluation context" gains a `combinations_backend` key. Document the chain-tracker side-effect: every successful (allow / log_only / observation) call appends to the chain.
- `integrations-and-cost.md` — `cost/combinations.py` is technically in `cost/`; add a "Combinations" subsection mirroring the "Price table" + "Infracost" subsections.

### Option B — new dedicated status doc `combinations.md` (recommended)

Create `arch/status/combinations.md`. Sections:

1. **What is a combination** — multi-op chain; alternative to single-call policies; threat model (slow-burn cost overrun, multi-step privesc, fan-out exfil).
2. **Chain tracking lifecycle** — when chains are created, how they grow, when they're evicted, memory bound.
3. **Catalog contract** — `combination-index.json` shape; `combination_id_matches` lookup; pre-signed status.
4. **4 conditions catalog** — table similar to policy-engine.md's condition table.
5. **Backend dependency** — `combinations_backend` injection point; `pluggable.py` env override; fail-direction policy on chain-tracker miss.
6. **Tests + verification** — pointers to `test_combinations.py`, `test_v0_6_packs.py`, integration smoke.
7. **Cross-references** — to policy-engine.md, integrations-and-cost.md, tessera-intelligence catalog docs.

### Option C — defer to v0.7 documentation pass

Bundle this work with the v0.7 OAuth / SaaS foundation arch refresh planned post-accelerator. Risk: combinations stays undocumented for another sprint; new contributors / future Claude sessions discover it by reading source.

Recommended: **Option B**, kept tight (~3-4 pages). The subsystem is non-trivial; the four conditions are different in shape from prior conditions (they consult a stateful chain-tracker, not just args); the per-tenant memory cap is a new operational constraint worth documenting.

## Acceptance criteria

- `arch/status/combinations.md` exists and covers the 7 sections above (or equivalent).
- `arch/status/overview.md` package tree includes `cost/combinations.py`.
- `arch/status/integrations-and-cost.md` no longer claims "The `aws/` subpackage is the only multi-file integration today" (it's been false since v0.5.1; will be doubly false once streamable-HTTP is also documented).
- Cross-reference from `policy-engine.md`'s combination-condition rows to the new doc.

## Effort

3-4 hours: read code → draft → review for accuracy → cross-link.

## On-merge

This improvement file becomes obsolete once `arch/status/combinations.md` lands. Delete or move to `archive/`.

## References

- `tessera/cost/combinations.py` — `CombinationTracker`, `CombinationChain`.
- `tessera/policy/schema.py` — 4 new `ConditionType` literals.
- `tessera-intelligence/catalogs/combination-index.json` — catalog.
- `tessera-intelligence/combinations/{aws,azure,gcp}/v1.0.0/` — 45 attack-chain YAMLs.
- arch-scan-2026-05-21 sub-agent output `/tmp/arch-scan-cloudmorph-tessera.md` § Finding 1.
