# 05 — Tier downgrade — cached packs persist

**Requires live CDN + the ability to mint a license JWT at two different tiers (developer → free) for the same tenant ID.** Cannot run offline.

## Starting state
- Fresh install per scenario 01
- A `developer`-tier license JWT exported as `TESSERA_LICENSE_JWT_DEV`
- Ability to mint a `free`-tier JWT for the **same tenant ID** (admin action at `admin.cloudmorph.io`) as `TESSERA_LICENSE_JWT_FREE`
- Local cache directory empty: `rm -rf ~/.cache/tessera/intelligence/`

## User actions (step-by-step)
1. Configure Tessera with the developer JWT: `export TESSERA_LICENSE_JWT="$TESSERA_LICENSE_JWT_DEV"`
2. Run scenario 02's sync flow to fetch + cache developer-tier-eligible packs
3. Verify cache contents: `tessera intelligence list-packs` shows `aws-cost-aware-defaults` + `vendor-mcp-protection` (the developer-tier-eligible set)
4. Restart Tessera with the free JWT: kill the process, `export TESSERA_LICENSE_JWT="$TESSERA_LICENSE_JWT_FREE"`, start again
5. Confirm cached packs still load: `tessera policy list | grep -E "(aws-cost-aware|vendor-mcp-protection)"` returns rows
6. Attempt a fresh sync: `tessera intelligence sync`
7. Inspect what new CDN fetches were attempted vs blocked

## Expected observable result
- After step 3, cache holds dev-tier packs and Tessera loads them at startup
- After step 5, **cached packs still enforce** even though the new JWT is free-tier — the firewall does not retroactively unload packs the customer once had access to
- After step 6, the sync emits `event=intelligence_tier_downgrade_detected` (or similar) and any CDN fetches for `team`/`enterprise`-gated content return HTTP 403 from the CloudFront Function
- The free-tier paths (`/v1.0.0/catalogs/`, `/v1.0.0/mappings/`) still return 200 — those are free-tier accessible
- No silent crash; the firewall continues to serve MCP traffic uninterrupted across the tier transition

## Failure modes to watch for
- Cached dev-tier packs DROPPED after tier change → owner: cache loader; the consumer should not gate by current tier on load, only on fresh fetch
- 200 on a team/enterprise pack with a free JWT → owner: CloudFront Function `intelligence-auth.js` tier table; check `shared/status/cloudfront-functions.md` for the matrix
- 403 on a free-tier path (`/v1.0.0/catalogs/`) → owner: CloudFront Function; should not require auth at all for catalogs
- Tessera crashes on the restart → owner: tier transition handling; check `tessera/intelligence/_tier.py`
- The `tier_downgrade_detected` event missing → owner: telemetry/observability layer

## How to verify manually

```bash
# Phase 1: dev tier
export TESSERA_LICENSE_JWT="$TESSERA_LICENSE_JWT_DEV"
tessera intelligence sync
tessera intelligence list-packs

# Phase 2: downgrade
pkill -f "tessera serve"
export TESSERA_LICENSE_JWT="$TESSERA_LICENSE_JWT_FREE"
tessera serve --config benchmarks/bench-tessera.yaml &
sleep 5
tessera policy list

# Phase 3: try to refetch
tessera intelligence sync
# Watch for 403s on dev-only paths in the sync output
```

## Owner on failure
Tier-transition primitive — `tessera/intelligence/_tier.py`, `tessera/intelligence/client.py`, plus producer-side CloudFront Function `cloudmorph-mono-repo/amplify/cdk/cloudfront-functions/intelligence-auth.js`

## Related code
- `tessera/intelligence/_tier.py`
- `tessera/intelligence/client.py` `IntelligenceClient.refresh()` and `.load_cached_packs()`
- `cloudmorph-mono-repo/amplify/cdk/cloudfront-functions/intelligence-auth.js` (CDN edge tier gate)
- `tessera-intelligence/arch/status/distribution-cdn.md`
