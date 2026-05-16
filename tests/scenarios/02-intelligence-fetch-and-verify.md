# 02 — Intelligence fetch + verify

**Requires live CDN + valid license JWT.** Cannot run offline.

## Starting state
- Fresh install per scenario 01 OR existing dev environment
- A valid `developer`-tier or higher license JWT exported as `TESSERA_LICENSE_JWT`
- Internet access to `https://intelligence.tessera.cloudmorph.ai/`
- Empty or absent local cache at `~/.cache/tessera/intelligence/`

## User actions (step-by-step)
1. `tessera config init` (creates `~/.config/tessera/tessera.yaml` with defaults)
2. Export the license: `export TESSERA_LICENSE_JWT="<jwt>"`
3. `tessera intelligence sync` (or invoke `IntelligenceClient.refresh()` from a Python REPL)
4. `tessera intelligence list-packs` (lists what was fetched)
5. Inspect local cache: `ls ~/.cache/tessera/intelligence/v1.0.0/{catalogs,packs,mappings,blast-radius}/`

## Expected observable result
- Sync emits `event=intelligence_sync_started` then `event=catalog_signature_verified` then per-pack `event=pack_manifest_verified` log lines
- No `ValueError: catalog signature missing` or `InvalidSignature` errors
- `list-packs` shows at least: `vendor-mcp-protection`, `aws-cost-aware-defaults`, plus tier-eligible packs (developer: those two; team: + `hipaa-guardrails`, `pci-dss-controls`; enterprise: all 12)
- Cache directory contains a tarball + `*.signed.json` manifest for each pack listed
- Every `*.signed.json` manifest carries `signature` (base64 Ed25519) and `tarball_sha256` fields; `tarball_sha256` matches `sha256sum` of the sibling `.tar.gz`

## Failure modes to watch for
- `ValueError: catalog signature missing` → owner: tessera-intelligence side; catalog regen ran without `_update_catalogs_from_dist.py` signature backfill
- `InvalidSignature: ed25519 verification failed` → owner: signing-key drift; `tessera/intelligence/public_key.pem` does not match the producer's signing key
- HTTP 403 from CDN on tier-eligible pack → owner: CloudFront Function `intelligence-auth.js` tier table mismatch
- `tarball_sha256 mismatch` → owner: producer-side build pipeline; tarball was re-packed without re-signing
- Empty `list-packs` output but no error → owner: client cache-loader; check `IntelligenceClient.load_cached_packs()`

## How to verify manually

```bash
export TESSERA_LICENSE_JWT="eyJhbGciOi..."  # from admin.cloudmorph.io
tessera config init
tessera intelligence sync
tessera intelligence list-packs
ls ~/.cache/tessera/intelligence/v1.0.0/packs/
```

## Owner on failure
Intelligence client + producer side — `tessera/intelligence/client.py` (consumer) or `tessera-intelligence/scripts/{build,sign_pack,publish}.sh` (producer)

## Related code
- `tessera/intelligence/client.py`
- `tessera/intelligence/_tier.py`
- `tessera/intelligence/public_key.pem`
- `tessera-intelligence/scripts/sign_pack.py` (producer)
