# Control plane (v0.7.0)

The wedge added in v0.7.0: the local proxy talks to `tessera.cloudmorph.ai` for policy refresh and audit upload, while the per-call decision path stays 100% local. Two new OSS modules + one OSS class + two CLI commands + one validator. Enforcement latency is unchanged; the cache (DecisionCache) actually drops it further on repeat calls.

Mental model:

```
[agent/IDE] ──► [tessera serve (local)] ──► [upstream MCP]
                     │  ▲
                     │  │ memoize hot decisions (DecisionCache, 1024×60s)
                     ▼  │
       ┌─────────────┴──┴─────────────┐
       │  cloud_sync (every 5min)     │ ──pull──► tessera.cloudmorph.ai/api/cli/policies
       │  cloud_uploader (batched)    │ ──push──► tessera.cloudmorph.ai/api/tessera/audit/ingest
       │  oauth_rs (token verify)     │
       └──────────────────────────────┘
                     ▲
       tessera login (PKCE) ─► browser ─► Cognito Hosted UI
                     │              │
                     │              ▼
                     └─────► oauth.json (~/.tessera/, 0600)
```

## What lives where

| File | Class / command | Lines | Purpose |
|---|---|---|---|
| `tessera/auth/oauth_rs.py` | `OAuthResourceServer` (line 372) | 888 total | Ed25519 token verify against bundled `oauth_pubkey.pem`; JWKS fetch fallback; RFC 9728 metadata; RFC 7591 DCR proxy; RFC 7662 introspection; RFC 7009 revocation; `require_scope(scope)` decorator. |
| `tessera/auth/oauth_pubkey.pem` | trust anchor | placeholder until founder pastes real pubkey | Bundled inside the OSS wheel — separate from `tessera/intelligence/public_key.pem` (different trust domain, different rotation). |
| `tessera/cloud_sync.py` | `CloudPolicySync` (line 41) | 226 | Periodic pull from `/api/cli/policies` → local SQLite cache. Default 5-min interval. Calls `DecisionCache.clear()` on every policy change. Failure-isolated. |
| `tessera/audit/cloud_uploader.py` | `AuditCloudUploader` (line 42) | 166 | Batched POST to `/api/tessera/audit/ingest` with exponential-backoff retry. Sends `chain_head` per batch; server stores raw, chain-verifies on read. |
| `tessera/proxy.py` | `DecisionCache` (line 82) | (inside proxy.py) | LRU+TTL memo on the hot path. 1024 entries / 60 s TTL. Keys: `sha256(canonical_json({scope, tool, args}))`. Caches `allow`+`observed` only — `block` and `require_approval` always re-evaluate. Wired at `app.state.decision_cache` (line 374); consulted on every `/mcp/*` call (line 944); invalidated on policy reload (line 383). |
| `tessera/cli.py` | `login` (line 1189) | (inside cli.py) | Browser-based PKCE flow. One-shot localhost listener. Writes `~/.tessera/oauth.json` (mode 0600). Refresh-token rotation on next call. |
| `tessera/cli.py` | `config_sync` (line 1377) | (inside cli.py) | Force one-shot pull from cloud, bypassing the periodic timer. Useful after editing a policy in the web UI. |

## Trust anchors (two separate keypairs — do NOT conflate)

1. **Intelligence signing key** — `tessera/intelligence/public_key.pem`. Used to verify pack + mapping + blast-radius bundles fetched from `s3://tessera-intelligence-prod/`. Rotation: very rare (signs every public pack release).
2. **OAuth signing key** — `tessera/auth/oauth_pubkey.pem`. Used to verify access + refresh JWTs minted by the cloudmorph.ai authorization server. Rotation: founder-controlled, planned every 6-12 months.

Different keypairs, different files, different rotation cadences, different blast-radius if compromised. This is intentional — one trust anchor revocation must not cascade into the other.

## Failure isolation contract

- `cloud_sync` unreachable → local enforcement keeps running on the last-known SQLite snapshot. Logged as `cloud_sync_unreachable`. Does not block decisions.
- `cloud_uploader` unreachable → events accumulate locally (bounded by `audit.queue_max_size`, defaults to 10_000). When the queue is full the oldest events are dropped to a `dropped_audit_events` counter. Local SQLite audit log is unaffected.
- `oauth_rs` verify failure → request 401. No fallback to "assume valid". Block-by-default on any signature-verify error.
- `DecisionCache` cleared on every policy reload — there is no scenario where a previously-allowed call survives a policy tightening past the next refresh window (5 min default; force with `tessera config sync`).

## What's still ahead (v0.7.1 candidates)

- Hash-chain verify on cloud `/audit/ingest` (currently verify-on-read only).
- Frontend-managed KMS-encrypted cloud cred storage (AWS/Azure/GCP).
- CLI `install-{cursor,claude-code,claude-desktop}` `--use-oauth` flag (today the install helpers wire raw API keys; OAuth tokens require manual config swap).
- `tessera deeplink` subcommand for IDE-side registration.
- Optional `authlib` + `cachetools` dependencies — current implementation is stdlib + the existing `cryptography` dep.

## Founder pre-publish blockers

1. `tessera/auth/oauth_pubkey.pem` currently contains `PLACEHOLDER_REPLACE_POST_DEPLOY_WITH_REAL_ED25519_PUBKEY`. Must be replaced with the real pubkey from the Secrets Manager keypair before tagging v0.7.0 + uploading to PyPI. Without that swap, `tessera login` succeeds but every subsequent token verify in the OSS package rejects.
2. The cloud-side OAuth Lambda + `/api/cli/*` route surface must be `cdk deploy`d in `cloudmorph-mono-repo` before `tessera login` can complete a handshake.
3. Cognito Hosted UI domain + AllowedCallbackURLs setup is manual (Cognito console step — not in CDK).
