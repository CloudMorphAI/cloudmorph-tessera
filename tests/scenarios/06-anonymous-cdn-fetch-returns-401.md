# 06 — Anonymous CDN fetch returns 401

**Requires live CDN.** Cannot run offline.

## Starting state
- A workstation with `curl` installed
- No license JWT configured
- Internet access to `https://intelligence.tessera.cloudmorph.ai/`

## User actions (step-by-step)
1. `curl -isS https://intelligence.tessera.cloudmorph.ai/v1.0.0/catalogs/pack-index.json` — no `X-Tessera-License` header
2. `curl -isS -H "X-Tessera-License: deliberately-invalid-jwt" https://intelligence.tessera.cloudmorph.ai/v1.0.0/packs/hipaa-guardrails/v1.0.0/manifest.signed.json`
3. `curl -isS https://intelligence.tessera.cloudmorph.ai/v1.0.0/packs/hipaa-guardrails/v1.0.0/manifest.signed.json` — no header at all on a paid-tier path
4. `curl -isS https://intelligence.tessera.cloudmorph.ai/v1.0.0/catalogs/pack-index.json` — no header on a free-tier path

## Expected observable result
- Step 1: catalogs ARE free-tier accessible — returns **HTTP 200** with the JSON body. Catalogs are the discovery layer; they list what packs exist but contain no protected content (per `tessera-intelligence/arch/status/signing-and-trust.md`)
- Step 2: invalid JWT on a paid-tier path returns **HTTP 401** with body `{"error": "invalid license"}` or equivalent. The CloudFront Function `intelligence-auth.js` rejects the request at the edge before it reaches S3
- Step 3: no header on a paid-tier path returns **HTTP 401**
- Step 4: same as step 1 — catalogs are free-tier, **HTTP 200**
- No 500-class errors anywhere
- `x-amz-cf-id` headers present (confirms CloudFront actually served the response)
- CloudFront response cached normally per its TTL (200s cache; 401s short-cache or no-cache per the function config)

## Failure modes to watch for
- Step 2 returns 200 → owner: CloudFront Function `intelligence-auth.js` JWT validation is broken; the function is not actually invoked OR the verification short-circuits to allow
- Step 1 returns 401 → owner: CloudFront Function over-blocks; catalogs are supposed to be free-tier
- Step 2 returns 403 instead of 401 → owner: function returns wrong status code; 401 ("unauthenticated") and 403 ("forbidden by tier") have different semantics and the consumer-side handler distinguishes them
- Step 2 returns 500 → owner: function crashed; check CloudWatch logs for `intelligence-auth-prod`
- No `x-amz-cf-id` header → owner: domain routing; the request didn't actually hit CloudFront

## How to verify manually

```bash
# 1. Catalog with no auth (expect 200)
curl -isS https://intelligence.tessera.cloudmorph.ai/v1.0.0/catalogs/pack-index.json | head -10

# 2. Paid pack with invalid JWT (expect 401)
curl -isS -H "X-Tessera-License: deliberately-invalid" https://intelligence.tessera.cloudmorph.ai/v1.0.0/packs/hipaa-guardrails/v1.0.0/manifest.signed.json | head -10

# 3. Paid pack with no auth at all (expect 401)
curl -isS https://intelligence.tessera.cloudmorph.ai/v1.0.0/packs/hipaa-guardrails/v1.0.0/manifest.signed.json | head -10
```

## Owner on failure
CDN edge tier gate — `cloudmorph-mono-repo/amplify/cdk/cloudfront-functions/intelligence-auth.js`

## Related code
- `cloudmorph-mono-repo/amplify/cdk/cloudfront-functions/intelligence-auth.js`
- `cloudmorph-mono-repo/arch/shared/status/cloudfront-functions.md`
- `tessera-intelligence/arch/status/distribution-cdn.md`
- `tessera-intelligence/arch/status/signing-and-trust.md`
