# Intelligence and Licensing

The consumer side of the signed-content trust chain, plus the authentication surfaces that consume license JWTs. This document covers `tessera/intelligence/`, `tessera/auth/`, and the cross-repo coupling with `tessera-intelligence` (producer) and the `cloudmorph-mono-repo` license server.

## Trust chain in one diagram

```
[founder's machine]                    [tessera-intelligence repo]
Ed25519 private key  ─signs──>        packs/ + mappings/ +
                                       blast-radius/ + catalogs
                                              │
                                              ▼ scripts/publish.sh
                              ┌─────────────────────────────────┐
                              │ s3://tessera-intelligence-prod/ │
                              │   <version>/{packs,mappings,…}  │
                              └────────────┬────────────────────┘
                                           │
                                           ▼
                            ┌──────────────────────────────────┐
                            │  intelligence.tessera.cloudmorph │
                            │  .ai (CloudFront + license gate) │
                            └──────────────┬───────────────────┘
                                           │  X-Tessera-License: <jwt>
                                           ▼
                       ┌──────────────────────────────────────────┐
                       │  cloudmorph-tessera (this repo)          │
                       │  ─────────────────────────────────       │
                       │  tessera/intelligence/public_key.pem     │
                       │    (byte-coupled to producer copy)       │
                       │  tessera.intelligence.IntelligenceClient │
                       │    fetcher + Ed25519 verifier            │
                       │  tessera.intelligence.LicenseValidator   │
                       │    license-server JWT check + cache      │
                       └──────────────────────────────────────────┘
```

The customer trusts content because every signed artifact verifies against the bundled `public_key.pem`; the CloudFront license gate is opportunistic tier enforcement (described in `tessera-intelligence/arch/status/distribution-cdn.md`). The load-bearing security boundary lives inside this repo's `IntelligenceClient`, not at the edge.

## Bundled public key: the byte-for-byte invariant

`tessera/intelligence/public_key.pem` is the Ed25519 public key the package ships with. It must be byte-for-byte identical to `tessera-intelligence/_metadata/public-key.pem` — the producer-side source-of-truth copy. Divergence between the two breaks every signature verification at every customer install, end of story.

The file is shipped via `pyproject.toml`'s `[tool.setuptools.package-data]` block:

```toml
[tool.setuptools.package-data]
tessera = [
    "intelligence/*.pem",
    "intelligence/*.json",
    "policies_default/*.yaml",
]
```

So the wheel carries the PEM, the runtime can read it via `importlib.resources.files("tessera.intelligence") / "public_key.pem"`, and customers in air-gapped or restricted-egress environments never reach out to verify anything.

There is **no automated cross-repo check** that the two PEM copies agree. The integration surface is `tessera-intelligence/tests/round_trip_smoke.py`, which loads the cloudmorph-tessera copy and verifies live S3 signatures against it; divergence surfaces immediately as a round-trip failure but only at the founder's manual run cadence. Tightening this into automated CI is a tidiness improvement; today the round-trip script is the load-bearing gate. See `tessera-intelligence/arch/status/signing-and-trust.md` for the producer-side rationale.

## Intelligence client: fetch → verify → cache → load

`tessera/intelligence/client.py:IntelligenceClient` is the runtime fetcher. Lifecycle is `__init__` → `refresh()` → `start_refresh_task()` (background loop, default every 24 hours). On each refresh:

1. **Fetch catalogs**. GET `catalog_url` (default `https://intelligence.tessera.cloudmorph.ai/catalogs/pack-index.json`) and `mapping_url`, with the license JWT under `X-Tessera-License`. The CloudFront license gate enforces tier visibility — `401` for missing license, `403` for above-tier paths.
2. **Verify catalog signatures**. When the catalog body includes `signature` + `body_bytes_hex`, the client Ed25519-verifies before parsing. Today catalogs ship unsigned (per the producer-side design in `tessera-intelligence/arch/status/policy-packs.md`); when the v0.3.0 catalog-signing improvement lands, this code path activates without API change.
3. **Resolve current tier**. If a `LicenseValidator` is wired, call `license.check()` to determine the customer's tier (`free` / `developer` / `team` / `enterprise`). On license-check failure, `fail_closed_on_license_check: true` raises; otherwise the client downgrades to `free` and continues.
4. **Tier-filter manifests**. For each manifest in the catalog, drop entries where `_tier_allowed(manifest.min_tier, current_tier)` returns false. The tier rank ordering is `free=0 < developer=1 < team=2 < enterprise=3`.
5. **Download + verify + extract**. For each surviving manifest, GET the `pack_url`, recompute `SHA-256(content) == manifest.content_hash`, then untar into `cache_dir/packs/<name>/<version>/`. Hash mismatches log + skip the pack. The tar extraction uses `filter="data"` (Python 3.12 secure mode) to reject symlinks and absolute paths.
6. **Persist last_known_good**. Write `cache_dir/last_known_good.json` with the timestamp + tier. This is the input the offline-fallback path consults.

The default cache directory is `~/.tessera/intelligence/` (expanduser-resolved). Cache is persistent across restarts; a customer can run with `enabled: false` after first refresh and still serve from cache.

Refresh cadence is set by `intelligence.refresh_interval_hours` (default 24). The background task simply sleeps and re-runs `refresh(force=True)` in a loop; failures log but don't stop the loop.

## License validator: JWT verify + 7-day fallback

`tessera/intelligence/license.py:LicenseValidator` is consulted at refresh time to determine the customer's tier. It reads `TESSERA_LICENSE_KEY` from the environment (env var name configurable via `intelligence.license_key_env`), posts it to the license server at `intelligence.license_check_url` (default `https://license.tessera.cloudmorph.ai/v1/check`), and gets back a JSON response with a `token` field carrying a signed JWT.

The JWT is Ed25519-verified using the bundled public key (the same key that signs intelligence content — one keypair covers both). The claims extracted: `tier`, `exp`, `seats`, `customer_id`. Tier must be in the known set or it falls back to `free`. Expiry is enforced against `time.time()`.

The fallback path is the durability story for spotty connectivity:

- **In-memory cache** — a successful `check()` is cached for 24 hours; subsequent calls return the cached `LicenseStatus` without hitting the license server.
- **On-disk cache** — every successful check is persisted to `cache_dir/license.json`. On license-server unreachability, the disk cache is consulted; if its age is less than `intelligence.license_cache_fallback_days` (default 7), the cached tier is returned.
- **Final fallback** — when no cache is available or the cache is stale, the validator returns `_free_status(from_cache=True)`. The customer continues operating at the free tier rather than crashing.

A `fail_closed_on_license_check: true` flag inverts this. When set, license-server failures raise rather than degrading. Reserved for deployments where running at a lower tier than entitled is worse than refusing to start.

## Auth subsystem: bearer / JWT / OIDC

The OSS package today has three authenticator implementations under `tessera/auth/`, all implementing the `Authenticator` Protocol (`authenticate(request) → AuthContext`):

### BearerTokenAuthenticator (`bearer.py`) — default

The production-default path. Reads tokens from one of three sources, in precedence order:

1. `TESSERA_BEARER_TOKENS` — inline `name1:tk_xxx,name2:tk_yyy` (parsed at startup).
2. `TESSERA_BEARER_TOKENS_FILE` — YAML file with `tokens: [{name, token, scope?}]`.
3. `TESSERA_BEARER_TOKEN` — single legacy token (scope defaults to `default`).

When none are set, the proxy starts in "dev mode" — every request authenticates as `principal_id=anonymous, scope=<deployment_id>`, and a `WARNING` is emitted at startup and every 60 seconds thereafter. This is deliberately noisy to keep dev mode out of production by accident.

Validation rules: name must match `[a-z0-9_-]{1,64}` (`NAME_RE` in `base.py`), scope must match the same regex (`SCOPE_RE`), token must be ≥ 16 chars. Constant-time match via `secrets.compare_digest` to prevent timing attacks.

The per-token `scope` field is what keys the audit hash chain. `alice:tk_x:scope_a` and `bob:tk_y:scope_b` get independent audit streams.

### JWTAuthenticator (`jwt_mcp.py`) — MCP-traffic JWT mode

Validates Bearer JWTs against a configured JWKS endpoint. Used when MCP clients themselves carry JWTs from an external IdP (Entra, Okta, Cognito). Configured under `auth.jwt`:

```yaml
auth:
  type: jwt
  jwt:
    jwks_url: https://example.com/.well-known/jwks.json
    issuer: https://example.com
    audience: tessera-mcp
    clock_skew_seconds: 60
    principal_claim: sub
    scope_claim: scope
```

The JWT's `principal_claim` becomes `AuthContext.principal_id`; the `scope_claim` (first token of OAuth-style space-separated string) becomes `AuthContext.scope` after SCOPE_RE normalization (with fallback to `deployment_id` on regex mismatch).

### OIDCAuthenticator (`oidc.py`) — management-plane SSO

Reserved for `/app/*` routes (not yet wired in v0.2.x but present at `app.state.management_plane_authenticator`). Validates JWTs against a JWKS endpoint with the same `_jwks.py` shared cache. Designed for Clerk (default), Auth0, Cognito, or any custom OIDC provider. The `scope_claim` defaults to `email` and is normalized for SCOPE_RE compliance (`@` → `_at_`, `.` → `_`).

### Shared JWKS cache (`_jwks.py`)

Both JWT-validating authenticators share a single `JWKSCache` implementation. Cache TTL defaults to 3600s; on unknown `kid` (key rotation) the cache is refreshed eagerly. The validator uses `python-jose` (`pip install cloudmorph-tessera[oidc]`) and enforces `verify_aud`, `verify_iss`, `verify_exp` with a configurable leeway (default 60s).

## Resource Server surface — what's implemented

### Shipped endpoints

`tessera/auth/oauth_rs.py` registers two endpoints via `make_metadata_route(app)` in `proxy.create_app()`:

- `GET /.well-known/oauth-protected-resource` — returns an RFC 9728 metadata document. Fields: `resource` (read from `TESSERA_OAUTH_RESOURCE_URL`), `authorization_servers` (read from `cfg.auth.jwt.issuer` or `cfg.auth.management_plane.issuer`; falls back to `TESSERA_OAUTH_AUTHORIZATION_SERVER`), `scopes_supported: ["tessera:proxy", "tessera:admin", "tessera:audit:read"]`, `bearer_methods_supported: ["header"]`, `resource_documentation`. This satisfies the discoverability requirement for OAuth 2.1 protected resources per RFC 9728.
- `GET /.well-known/jwks.json` — stub that returns `{"keys": []}`. Tessera does not currently issue tokens and has no signing keys to publish; the endpoint exists for forward-compatibility with OAuth 2.1 validators that require the JWKS discovery surface. It will be populated if/when Tessera gains a token-issuance path (e.g., signed audit receipts).

### Remaining work

Two endpoints are deferred: `POST /oauth/introspect` (RFC 7662 token introspection) and `POST /oauth/register` (RFC 7591 Dynamic Client Registration proxy). Introspection requires deciding whether to return claims from the cached JWT validation or to call the upstream IdP for revocation status. DCR proxy requires a configured `auth.dcr.upstream_url`; without one, the correct behavior is a 501 with a message pointing to the IdP's native DCR path. Neither is a blocker for the current agent-already-has-a-JWT traffic pattern, but both are required for full spec compliance and enterprise procurement conversations about DCR-capable agents.

## License-JWT shape and consumption

The license JWT issued by the license server (in `cloudmorph-mono-repo`) carries:

- `tier` — string, one of `developer` / `team` / `enterprise` (lowercase).
- `exp` — Unix epoch seconds.
- `seats` — integer count.
- `customer_id` — opaque string, used for logging only.

The OSS package consumes the `tier` claim as authoritative. It does **not** verify against Stripe directly, does **not** carry Stripe credentials, and does **not** make outbound calls to anything other than `intelligence.license_check_url` and `intelligence.catalog_url` / `mapping_url`. The license server is the single point at which a Stripe subscription becomes a tier; the OSS package trusts the JWT signature and reads the claim.

How a tier change propagates:

1. Customer updates their Stripe subscription (downgrade from `team` to `developer`).
2. License server's webhook handler updates the customer's tier in its tenant DB and re-issues a JWT on next `/v1/check`.
3. This repo's `LicenseValidator.check()` returns `tier: developer` at the next refresh (in-memory cache TTL is 24 hours, so the worst-case lag is one cache cycle).
4. `IntelligenceClient.refresh()` re-filters the catalog at `tier: developer`, drops above-tier packs.
5. Above-tier packs that were already in `cache_dir/packs/` continue to exist on disk. Whether they're served from cache or invalidated immediately is the open design question captured in `improvements/v0.3.0-stripe-integration.md`.
6. Subsequent fetches of above-tier packs return 403 from the CloudFront tier gate.

## Verification flow (read alongside the producer-side doc)

The producer-side verification flow is detailed in `tessera-intelligence/arch/status/signing-and-trust.md`. The consumer-side mirror, in this repo, is:

1. Load `tessera/intelligence/public_key.pem` from the installed wheel (cached on the `IntelligenceClient` instance).
2. Fetch the catalog (`pack-index.json`).
3. For each tier-eligible manifest, fetch the pack tarball.
4. Compute `SHA-256(tarball_bytes)` and compare to `manifest.content_hash`.
5. (Today) Trust the manifest content directly. (Planned v0.3.0) Ed25519-verify the manifest's `signature` field against the public key — closing the per-tarball binding gap described in `tessera-intelligence/arch/improvements/v0.3.0-pack-content-hash-recompute.md`.
6. Extract into `cache_dir/packs/<name>/<version>/`.

The current verification depth is content-hash check only; signature verification on the manifest is implemented but the v0.2.0 manifests ship without a real `signature` field on the catalog inline. The full signed-manifest path activates when the v0.3.0 producer-side improvement lands; the consumer side is wired and waiting.

## Cache layout

`cache_dir` (default `~/.tessera/intelligence/`) holds:

```
~/.tessera/intelligence/
├── license.json                 # Persisted LicenseStatus + cached_at
├── last_known_good.json         # Last successful refresh timestamp + tier
├── packs/
│   └── <pack-name>/
│       └── <version>/
│           ├── policies/        # YAMLs that policy/loader.py can load
│           └── manifest.json    # Original manifest for audit
└── mappings/
    └── <bundle-name>/
        └── <version>/
            └── *.yaml           # Extended cost mappings (load_extended_mappings)
```

Pack policies are loaded by `tessera/policy/loader.py` exactly as if they were authored locally — there is no special-case code path for pack-sourced vs author-sourced policies. The integration is simply "point `policies.dir` at a directory that includes the cached pack policies." Customers typically add a glob like `policies.dir: /etc/tessera/policies` and symlink `~/.tessera/intelligence/packs/vendor-mcp-protection/v1.0.0/policies/` into it.

## What this repo does not do (license-side)

- **It does not issue or revoke JWTs.** That's the license server's job in `cloudmorph-mono-repo`.
- **It does not carry Stripe credentials.** Stripe interaction is server-side only.
- **It does not perform OAuth flow handshakes as a client.** The agent supplies the JWT; Tessera validates.
- **It does not enforce seat counts beyond the tier check.** Seat enforcement is the admin console's responsibility.

Closing the boundary cleanly: the OSS package's view of the world is "I have a public key, I have a (cached) tier, I have signed content I can verify; everything beyond that is the upstream provider's concern."

## Cross-references

- For the producer-side signing chain: `tessera-intelligence/arch/status/signing-and-trust.md`.
- For the CDN's edge license gating: `tessera-intelligence/arch/status/distribution-cdn.md`.
- For where intelligence-fetched policies plug into the policy loader: `policy-engine.md`.
- For pack and mapping schemas: `tessera-intelligence/arch/status/policy-packs.md` and `aws-mappings.md`.
