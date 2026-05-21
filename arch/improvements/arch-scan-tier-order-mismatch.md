# arch-scan — `_TIER_ORDER` mismatch between `license.py` and `client.py` (latent bug)

**Severity**: MEDIUM — latent bug; could KeyError or silently misclassify `scale`-tier customers.
**Discovered**: 2026-05-21 arch-scan pass.
**Status**: Open.

## Problem

Two adjacent modules in `tessera/intelligence/` define a `_TIER_ORDER` dict, with different key sets:

- `tessera/intelligence/license.py:_TIER_ORDER` — 4 keys: `free`, `developer`, `team`, `enterprise`.
- `tessera/intelligence/client.py:_TIER_ORDER` — 5 keys: `free`, `developer`, `team`, `scale`, `enterprise` (with `scale` at rank 2, `team` retained as a legacy alias).

The CDN-side intelligence client (`client.py`) was extended during the 2026-05 cross-repo audit (commit `9d84d82`) to recognize the `scale` tier introduced into the license server's JWT issuance. The license-validator (`license.py`), which is consulted at JWT validation time, was NOT updated in lockstep — it still only knows the legacy 4 tiers.

The failure mode is:

1. License server issues a JWT with `tier: "scale"` (which `client.py` accepts as rank 2).
2. The licence-validator path in `license.py` consults `_TIER_ORDER[claim["tier"]]` to compare against `required_tier`.
3. If the lookup is via `[]` indexing → `KeyError("scale")` → validation crashes, customer locked out.
4. If the lookup is via `.get(claim["tier"], 0)` → silent rank-0 (treated as `free`) → customer's premium content access silently denied.

The status doc `arch/status/intelligence-and-licensing.md` references `client.py`'s `_TIER_ORDER` and asserts the tier ordering, but does not flag that `license.py` has a separate, narrower copy.

## Investigation needed

1. **Has the license server ever issued a `tier: "scale"` JWT to a real customer?**
   - Check `cloudmorph-mono-repo/amplify/functions/tessera/license/` for the tier-set the server can mint.
   - If "scale" is unreachable from the license server, this is latent (no customer impact yet).
2. **Which lookup pattern does `license.py:_TIER_ORDER` use?**
   - If `[claim["tier"]]` → KeyError-on-scale.
   - If `.get(..., default)` → silent misclassification.
3. **What does the corresponding test cover?**
   - Grep for `_TIER_ORDER` in `tests/`; specifically check whether `scale` is exercised in any license-validator path.

## Approach

Option A (small): unify both modules around the 5-tier order. Move `_TIER_ORDER` into a shared module (e.g., `tessera/intelligence/_tier.py`) and import from both. Best if the duplication is unintentional.

Option B (larger): if `license.py` deliberately uses a narrower ordering (e.g., because the license server isn't yet authoritative for `scale`), document the asymmetry explicitly in code comments + arch docs. Add a test that asserts the divergence is intentional.

Recommended: **Option A** unless investigation reveals a load-bearing reason for the split. Centralizing a single tier-order constant eliminates a class of "I added a tier in one file" bugs.

## Acceptance criteria

- A single source of truth for `_TIER_ORDER` exists (one file, both modules import).
- A test asserts the dict has the 5 expected keys with the documented ranks.
- `arch/status/intelligence-and-licensing.md` reflects the unified definition.
- A `scale`-tier license JWT exercises both the `client.py` content fetch path AND the `license.py` validation path in `tests/`.

## Effort

1-2 hours: unify + add tests + verify integration tests still green.

## On-merge

Fold into `arch/status/intelligence-and-licensing.md`. Note this as a regression-resistance improvement (the next tier added — `team`-rename, `pro`, future SKUs — won't be able to introduce the same drift).

## References

- `tessera/intelligence/client.py` — current authoritative `_TIER_ORDER` (5 keys).
- `tessera/intelligence/license.py` — narrower `_TIER_ORDER` (4 keys).
- `arch/status/intelligence-and-licensing.md` — current doc that doesn't flag the split.
- arch-scan-2026-05-21 sub-agent output `/tmp/arch-scan-cloudmorph-tessera.md` § "Architectural risks #5".
