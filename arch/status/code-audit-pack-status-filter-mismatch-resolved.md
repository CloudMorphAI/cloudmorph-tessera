> STATUS: Resolved. client.py filter accepts "production" and "active" as active statuses.
> Confirmed at session start 2026-06-11.

# code-audit: pack status filter "active" vs producer "production" — zero packs downloaded (CRITICAL)

**Discovered**: 2026-05-22 code-audit overnight pass
**Severity**: CRITICAL

## Problem
`IntelligenceClient.refresh()` filters `if manifest.status != "active": continue` at both line 468 (packs) and line 484 (mappings). The production catalog (`tessera-intelligence/catalogs/pack-index.json` + `mapping-index.json`) uses `"production"` or `"pre-signed"` for `status` — never `"active"`. So every pack and every mapping bundle is silently skipped on every refresh. `IntelligenceClient.refresh()` returns `{"packs_downloaded": 0, "mappings_downloaded": 0, "errors": []}` — false all-clear.

The `PackManifest.status` docstring at line 52 documents `"active" | "deprecated" | etc.` — out of sync with the catalog's actual values.

The intelligence feature is functionally broken in production today. No policies from the CDN are ever applied.

## Where
- File: `tessera/intelligence/client.py:468` (packs filter)
- File: `tessera/intelligence/client.py:484` (mappings filter)
- File: `tessera/intelligence/client.py:52` (PackManifest.status docstring)
- File: `tessera/intelligence/client.py:276` (default value `"active"` when missing — also drifted)

## Suggested fix
Consumer-side, cheapest: change the two filters to `if manifest.status not in ("active", "production"): continue`. Update the docstring at line 52 to reflect both values. Update default at line 276 if changing.

Long-term cleaner: standardize the catalog producer on `"active"` everywhere (regen `_update_catalogs_from_dist.py` + manifest.schema.json enum). Then revert consumer to single-value check.

Either fix needs cross-coordination with `tessera-intelligence` (the producer side).

## Effort
small (consumer-side, one-line tweak in two places + docstring)

## Acceptance criteria
- A `refresh()` against the production catalog downloads >0 packs and >0 mappings
- `manifest.status == "production"` is not treated as inactive
- `manifest.status == "pre-signed"` is still skipped (don't accidentally activate pre-signed packs)
- A regression test fixture uses `"status": "production"` (not `"active"`) to lock in the fix

## On merge
Folds into the OSS intelligence client status doc. This file deleted after merge.
