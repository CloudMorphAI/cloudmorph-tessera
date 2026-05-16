# Next steps — cloudmorph-tessera

Open items only. Closed work is in git history (`git log --oneline`) and per-release CHANGELOG entries. Releases 0.2.1, 0.3.0, 0.4.0, and 0.5.0 all shipped; the items that drove them are removed from this file.

## Founder-only — blocked on private signing key

The Ed25519 private signing key never leaves the founder's machine (per `tessera-intelligence/arch/status/signing-and-trust.md`). A coding sub-agent has no path to sign on the founder's behalf. These items sit until the founder runs them.

### P0-8 — sign the 4 unsigned packs

Four paid packs in `tessera-intelligence/packs/` lack detached `.sig` files. Customers who `tessera intelligence pull <pack>` get the tarball but cannot verify it because the signature is missing. The `manifest.content_hash` check still fires (catalog-level integrity holds), but the per-pack signature path is incomplete.

Action: run `scripts/sign_pack.py` against each pack on the founder's machine. Commit + re-publish to S3 + invalidate CloudFront. No code change required in this repo.

Affected packs (per `plan/details/tessera-content.md` § P0-8):
- `vendor-mcp-protection/v1.0.0/`
- `hipaa-guardrails/v1.0.0/`
- `fintech-pack/v1.0.0/`
- (fourth pack — see `plan/details/tessera-content.md`)

### P0-9 — sign the mapping bundles

Mapping bundles under `tessera-intelligence/mappings/{aws,azure,gcp}/v*/` are not yet signed. Catalog-level signature gate (P0-17 — closed) ensures the catalog announcing them is verified, but the bundle tarballs themselves are not.

Action: (1) extend `tessera-intelligence/scripts/sign_pack.py` to accept `--kind mapping` (walks the bundle directory, computes body-bytes SHA-256, signs, emits `.sig`); (2) run the extended script against existing mapping bundles; (3) re-publish + CloudFront-invalidate. The script extension is sub-agent-writable; the signing run is founder-only.

### Real-JWT 8-scenario CDN smoke test

`tests/integration_cdn_smoke.py` is env-gated on three test-tenant JWTs (developer / scale / enterprise) minted at `admin.cloudmorph.io`. The 8-scenario tier-gate matrix needs those JWTs before it can execute. Round-trip smoke against prod has already passed; this is the missing per-tier matrix.

## Other open work

Nothing else is currently blocking the next release. See `tessera-intelligence/arch/nextsteps.md` for content-producer-side open work and `cloudmorph-mono-repo/arch/tessera/improvements/2026-Q2-wrapped-image-ci.md` for the wrapped-image CI EventBridge half (still pending).

## Cross-references

- Trust-chain architecture: `arch/status/intelligence-and-licensing.md`.
- The async hot-path design: `arch/status/proxy-enforcement-and-audit.md`, `arch/status/integrations-and-cost.md`.
- The bundled-vs-paid bundling policy: `arch/status/policy-engine.md` § "Bundling policy (P0-19 resolution)".
- Signing chain: `tessera-intelligence/arch/status/signing-and-trust.md`.
