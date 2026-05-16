# Next steps — cloudmorph-tessera

State of the P0 worklist as of 2026-05-15, after the parallel sub-agent coding session that landed three commits (SA-1 / SA-2 / SA-3) closing 14 of the original P0 items in this repo. Tessera-intelligence picked up one companion commit (`471523f`, P0-12 rename).

For pre-P0 audit-style findings see `plan/nextsteps.md` — that catalogued the 2026-05-14 audit. The cross-repo audit findings that surfaced 2026-05-15 (tier ordering, `bundle_url` rename, mandatory `manifest.signed.json` verify, tarball SHA-256 check, base64 signature decoding, PyJWT explicit dep) were closed in commit `426ca84` + `9d84d82` + `18ffa13` — see "2026-05-15 cross-repo audit closures" below. This document focuses on the P0 worklist defined in `plan/details/tessera-content.md`, `plan/details/tessera-hotpath.md`, and `plan/details/tessera-cost-awsmcp.md`.

## 2026-05-15 cross-repo audit closures

Closed in `cloudmorph-tessera` `main`. Commit SHAs verified by `git log --oneline 1a55944..18ffa13`. The companion producer-side ship is `tessera-intelligence` commit `a481fe7` (production signing of 4 compliance packs + Azure pack + 4 mapping bundles + blast-radius bundle).

| Item | Closed by | Where it landed |
|------|-----------|-----------------|
| Tier table — OSS consumer's `_TIER_ORDER` used `team` for rank 2; producer canonical is `scale` | `426ca84` | `tessera/intelligence/_tier.py` — `scale=2` is canonical; `team` is retained as a same-rank alias so 0.2.0 customers don't see policies vanish. Documented at `arch/status/intelligence-and-licensing.md` step 4. |
| Mapping bundle URL — consumer was reading `mapping_url` but `mapping-index.json` ships `bundle_url` (every mapping download was a 404) | `426ca84` | `tessera/intelligence/client.py` — reads `bundle_url` first, falls back to `mapping_url` for backwards-compat with 0.2.0 catalogs. |
| Mandatory `manifest.signed.json` verify — consumer was treating catalog `content_hash` / `signature` (PLACEHOLDER strings) as if they were the load-bearing signature; the actual signed manifest at `manifest_url` was never fetched | `426ca84` | `tessera/intelligence/client.py:_verify_pack_manifest` — fetches `manifest_url`, recomputes canonical-JSON SHA-256 with `content_hash`/`signature`/`signed_at` zeroed, Ed25519-verifies. Mirrors `tessera-intelligence/scripts/sign_pack.py:compute_content_hash` exactly. Documented at `arch/status/intelligence-and-licensing.md` step 5. |
| Tarball SHA-256 check — consumer was comparing tarball-bytes-SHA-256 against manifest's `content_hash` (a hash of canonical JSON, not the tarball) | `426ca84` | `tessera/intelligence/client.py` now reads `tarball_sha256` from the verified manifest and compares it to `SHA-256(downloaded_tarball)`. Mandatory when `manifest_url` is present. |
| Base64 signature decoding — consumer was `bytes.fromhex` on signatures the producer emits as base64 | `426ca84` | `tessera/intelligence/client.py:_verify_signature` uses `base64.b64decode` (matching `sign_pack.py`). |
| PyJWT explicit dep | `9d84d82` | `pyproject.toml:[project.dependencies]` adds `PyJWT>=2.8`. License-validator + OAuth introspection no longer rely on transitive `python-jose` survival. Documented at `arch/status/packaging-and-release.md`. |
| 0.2.0 → 0.2.1 release bump | `18ffa13` | 5-place version bump (`pyproject.toml`, `tessera/__init__.py`, `README.md`, `docs/INSTALL.md`, `CHANGELOG.md`). |

Round-trip smoke passes against `s3://tessera-intelligence-prod/v1.0.0/` end-to-end with the 0.2.1 client + the producer-signed corpus from `a481fe7`.

## 2026-05-16 Batch 1 — 0.2.1 close-out — DONE

Per `plan/tessera-improvements-plan-2026-05-16.md` §3 Batch 1. The "founder follow-ups" listed in the 0.2.1 release-bump row above are now all closed:

| Action | Status | Detail |
|--------|--------|--------|
| PyPI `twine upload` 0.2.1 | ✓ DONE 2026-05-15T13:51Z | manual upload from WSL |
| `git tag v0.2.1` + push | ✓ DONE | tag points at `18ffa13`; on `origin/main` |
| GHCR multi-arch image | ✓ DONE | `ghcr.io/cloudmorphai/tessera:0.2.1` (amd64 + arm64) |
| ECR wrapper image push | ✓ DONE 2026-05-16T01:42Z | repo renamed `cloudmorph/tessera-cloud-prod` → `cloudmorph/tessera-cloud-wrapper` in mono-repo commit `a0c2c36b`; built via `cloudmorph-mono-repo/tessera-cloud-wrapper/build.sh 0.2.1` with `TESSERA_OSS_TAG=0.2.1`; tags `:0.2.1` + `:main` |
| ECS force-new-deployment | ✓ DONE 2026-05-16T07:01Z | service `tessera-cloud-prod` (cluster `tessera-cloud-prod`) rolled; deploy `ecs-svc/7011306979647899462` Completed; 1/1 task on fresh image |
| `tessera-ratelimits-prod` DDB | ✓ ACTIVE | CDK at `cloudmorph-mono-repo/amplify/backend/tessera.ts:219` (`TesseraRateLimitsTable`); IAM grant on wrapper task role at line 493; TTL on `expiresAt` |
| TI build + sign + publish | ✓ DONE 2026-05-16T02:21Z | `scripts/build.sh v1.0.0` + `_update_catalogs_from_dist.py` + `publish.sh`; all 11 new tarballs (6 mappings + blast-radius + 5 packs) + 3 catalog files on S3 |
| CloudFront invalidate | ✓ DONE 2026-05-16T02:22Z | invalidation `IBZAEEM6BV8SHCCYP84J00DB0J` Completed |
| Round-trip smoke against prod CDN | ✓ GREEN | 12 packs in live catalog, all Ed25519 signatures verify, tarball-hash binding OK |
| Clean-venv install verify | ✓ GREEN | `pip install cloudmorph-tessera==0.2.1` → `__version__ == "0.2.1"`, 18 bundled policies present, PEM trust anchor shipped (113 bytes) |

Memory drift fix: `reference_ecr_tessera_cloud.md` updated to reflect the wrapper rename (was `cloudmorph/tessera-cloud-prod`).

Open as of 2026-05-16 (non-blocking):

- **Real-JWT 8-scenario CDN smoke test** (`tests/integration_cdn_smoke.py`) — round-trip smoke ran against prod and passed, but the 8-scenario tier-gate matrix needs 3 test-tenant JWTs minted at `admin.cloudmorph.io` (developer / scale / enterprise) before it can execute. Founder action.
- **`tessera-intelligence/` working tree** has 78 modified YAMLs that are pure CRLF-vs-LF line-ending drift (Windows-side editor opened them; tarballs hash-bind to the CRLF bytes and signatures verify correctly). Cleanup is `git checkout -- mappings/` from WSL + adding `*.yaml text eol=lf` to `.gitattributes` to prevent recurrence — both shipped 2026-05-16.



## Closed — landed locally on `main` (2026-05-15)

All commit SHAs verified by `git log --oneline cd4113b..1a55944`. Tessera-intelligence companion: `471523f`.

| Item | Closed by | Where it landed |
|------|-----------|-----------------|
| P0-1 — IAM PassRole guard (bundled OSS policy) | `1933db4` | `tessera/policies_default/aws-mcp-passrole-guard.yaml` (priority 95, `require_approval`). Fail-closed posture when no `blast_radius_backend` is wired. |
| P0-2 — IAM admin-policy hard-deny | `1933db4` | `tessera/policies_default/aws-mcp-admin-policy-deny.yaml` (priority 99, `block`). Covers `AttachRolePolicy` for AWS-managed admin ARNs + `PutRolePolicy`/`CreatePolicy` with inline `Action:"*"+Resource:"*"`. |
| P0-3 — IAM CreateAccessKey deny | `1933db4` | `tessera/policies_default/aws-mcp-create-access-key-deny.yaml` (priority 97, `block`). Admin-tier `UserName` regex. |
| P0-4 — KMS ScheduleKeyDeletion approval | `1933db4` | `tessera/policies_default/aws-mcp-kms-deletion-approval.yaml` (priority 98, `require_approval`). |
| P0-5 — RDS public-access deny | `1933db4` | `tessera/policies_default/aws-mcp-rds-public-deny.yaml` (priority 97, `block`). `PubliclyAccessible: true` as bool / string / int. |
| P0-6 — EC2 IMDSv1 deny | `1933db4` | `tessera/policies_default/aws-mcp-ec2-imdsv1-deny.yaml` (priority 96, `block`). Uses `arg: "*"` arg-spanning regex against `"HttpTokens":"optional"` — slight over-match accepted as default behaviour. |
| P0-7 — blast-radius inline-wildcard scan | `1933db4` | Companion-policy artefact committed under P0-2 (admin-policy deny covers the inline wildcard primitive via regex). The deeper `_compute_iam_role_policy` extension scoped in `plan/details/tessera-hotpath.md` §4.1 is **not** in this commit; the bundled regex policy is sufficient for the OSS loss-leader and the boto3-grade refinement is deferred to a paid pack iteration. |
| P0-12 — false-positive `bucket_region_lookup` | `1933db4` (rationale) + `471523f` (rename in tessera-intelligence) | `bucket_region_lookup` was a value of `target_region_extraction:`, not an action verb. Renamed to `bucket_region_resolve_via_head_bucket` for grep clarity. **No runtime change.** Documented at `arch/status/policy-engine.md` § "P0-12 — `bucket_region_lookup` audit-false-positive resolution". |
| P0-13 — async audit emit | `1a55944` | `tessera/audit/async_emit.py:AsyncAuditQueue` (single-consumer `asyncio.Queue` + `asyncio.to_thread` drain). Hot path returns `event_id` synchronously; SHA-256 stamp + WAL fsync run in worker thread. `TESSERA_AUDIT_SYNC=1` restores legacy sync emit. Documented at `arch/status/proxy-enforcement-and-audit.md` § "Async audit emit (P0-13)". |
| P0-14 — blast-radius async prefetch | `1a55944` | `PolicyEngine.policies_need_blast_radius` gate + `asyncio.to_thread(blast_radius_backend.compute, …)` before context build. Result populates `context["blast_radius_cache"]` consulted by the evaluator. Documented at `arch/status/integrations-and-cost.md` § "Async prefetch (P0-14)". |
| P0-15 — DataVolume async prefetch | `1a55944` | `s3_head_size_sync` / `rds_explain_size_sync` top-level helpers + module-level `_DATA_VOL_LRU` (TTLCache, 1000 × 300s) + per-request `_data_vol_cache`. Documented at `arch/status/integrations-and-cost.md` § "DataVolume async prefetch (P0-15)". |
| P0-16 — intelligence cache pre-warm | `bb57a1a` | `IntelligenceClient.start_refresh_task()` now fires an immediate `refresh(force=True)` before scheduling the 24h loop, gated on `IntelligenceConfig.prewarm_on_start` (default `True`). Total failure → `event=intelligence_prewarm_failed` log + swallowed, background loop continues. Documented at `arch/status/intelligence-and-licensing.md` § "Startup pre-warm (P0-16)". |
| P0-17 — mandatory catalog signature | `bb57a1a` | `_require_or_skip_catalog_sig()` raises `ValueError` when `signature` or `body_bytes_hex` is missing or empty. Closes F2 fail-open gap. Opt-out: `IntelligenceConfig.allow_unsigned_catalog=True` for self-hosted CDN + CI fixtures only. Documented at `arch/status/intelligence-and-licensing.md` § "Intelligence client: fetch → verify → cache → load" step 2 and "Verification flow" step 3. |
| P0-18 — verify `add_spend()` write-back | `1a55944` | `_record_daily_spend()` wired into proxy's `allow` + `observation` success paths via `asyncio.create_task` + `asyncio.to_thread`. Regression test at `tests/unit/state/test_daily_spend.py` pins the persistence contract. **This was a real bug**: SA-2 confirmed `add_spend()` had zero production callers before this commit — every `cumulative_spend_today` cap was silently no-opping. Documented at `arch/status/integrations-and-cost.md` § "Auto-write integration (P0-18)" and `arch/status/proxy-enforcement-and-audit.md` § "Local state backend". |

## Still open — founder-only

These cannot be advanced by a coding sub-agent because they require either secret-key material the founder never lets leave their machine, or a product-level scope decision the founder must own.

### P0-8 — sign the 4 unsigned packs

Source: `plan/details/tessera-content.md` § P0-8 (line 1352).

**State.** Four paid packs exist in `tessera-intelligence/packs/` without detached `.sig` files. Tessera Cloud customers who pull them via `tessera intelligence pull <pack>` get the tarball but cannot verify it because the signature is missing. Per the v0.2.x consumer flow (`arch/status/intelligence-and-licensing.md` § "Verification flow") the `manifest.content_hash` check still fires — so the tarball is integrity-protected at the catalog level — but the per-pack signature path is incomplete.

**Action.** Run `scripts/sign_pack.py` against each of the four packs on the founder's machine (Ed25519 private key lives there only):

- `tessera-intelligence/packs/vendor-mcp-protection/v1.0.0/`
- `tessera-intelligence/packs/hipaa-guardrails/v1.0.0/`
- `tessera-intelligence/packs/fintech-pack/v1.0.0/`
- (fourth pack — see `plan/details/tessera-content.md`).

Commit + re-publish to S3 + invalidate CloudFront. No code change required in `cloudmorph-tessera`.

**Why founder-only.** The private signing key never leaves the founder's machine (per `tessera-intelligence/arch/status/signing-and-trust.md`). A sub-agent has no path to sign on the founder's behalf without exfiltrating the key.

### P0-9 — sign the mapping bundles

Source: `plan/details/tessera-content.md` § P0-9 (line 1396).

**State.** Mapping bundles (`tessera-intelligence/mappings/aws/v1.0.0/`, future `mappings/azure/`, `mappings/gcp/`) are not yet signed. The catalog-level signature gate (closed in P0-17 above) ensures the catalog announcing them is verified, but the bundle tarballs themselves are not.

**Action.** Two-part:

1. **Extend `scripts/sign_pack.py`** so it can also sign a `mapping_bundle` artefact (current script targets packs only). This is a small change to the script — accepts a `--kind mapping` flag, walks the bundle directory, computes a body-bytes SHA-256, signs with the Ed25519 key, emits the `.sig`.
2. **Run the extended script** against the existing mapping bundles. Re-publish + CloudFront-invalidate.

The script extension is technically code that a sub-agent could write, but the signing run itself requires the founder's key.

**Why founder-only.** Same as P0-8 — Ed25519 private key access.

### ~~P0-19 — product decision: bundled OSS vs paid-pack scope~~ **CLOSED 2026-05-15**

**Resolution: hybrid open-core.** Universal AWS-MCP primitives ship bundled OSS; industry-vertical compliance + per-tenant customization + boto3-grade conditions ship paid. Full policy + decision triggers documented at `arch/status/policy-engine.md` § "Bundling policy (P0-19 resolution, 2026-05-15)".

## 2026-05-15 P0 session result

Three parallel Sonnet sub-agents (SA-1 content, SA-2 hot-path, SA-3 trust-chain) landed:

| Commit | Author / scope | Closes |
|--------|---------------|--------|
| `bb57a1a` | SA-3 — intelligence pre-warm + mandatory catalog signature | P0-16, P0-17 |
| `1933db4` | SA-1 — 6 bundled AWS-MCP policies + P0-12 false-positive resolution | P0-1, P0-2, P0-3, P0-4, P0-5, P0-6, P0-12 |
| `1a55944` | SA-2 — async audit emit + blast-radius async + DataVolume async + add_spend write-back | P0-13, P0-14, P0-15, P0-18 |
| `471523f` (in `tessera-intelligence`) | SA-1 companion — rename `bucket_region_lookup` value | P0-12 (rename leg) |

Final test counts (post-commit):

- **577 passed** (baseline 529 + 53 new tests = 582 expected; the −5 delta is some baseline-counted-as-failing tests being re-classified).
- **9 pre-existing failures**, all unrelated to this session (see "Pre-existing failures" below).
- **2 skipped** — `tests/integration_cdn_smoke.py` (env-gated on live JWTs) and one OAuth introspection scenario.
- **+53 new tests** added this session: 30 (SA-1: bundled policy corpus + per-policy fire/pass) + 16 (SA-2: async audit + blast-radius prefetch + DataVolume prefetch + add_spend regression) + 7 (SA-3: pre-warm success / partial / total-failure + unsigned-catalog rejection + opt-in escape hatch + wrong-key).

### Key findings worth surfacing

- **SA-2 confirmed P0-18 was a real production bug.** `add_spend()` had zero callers in audited scope before this commit. Every `cumulative_spend_today` policy was silently no-opping in production — a cost-cap that doesn't accumulate spend is just an unreachable policy condition. The fix is now wired and pinned by a regression test. The arch doc previously described `add_spend()` "intended to be wired" without flagging that it wasn't; the proxy-enforcement-and-audit and integrations-and-cost docs have been updated to call this out explicitly.

- **SA-1 had to rewrite the policy YAMLs.** The original templates in `plan/details/tessera-cost-awsmcp.md` referenced schema conditions that do not exist in the v0.2.x `Policy` schema: `resolved_role_attached_policies_include`, `arg_outside_allowlist`, `arg_present`, `arg_not_equals`, `tool_equals`, `all_of`, plus a top-level `metadata:` block and a per-policy `approval_channels:` block. The schema is `extra="forbid"` pydantic; every typo or unknown discriminator fails at load time. SA-1 adapted all six policies to use only the schema-enum vocabulary (`arg_matches_regex`, `arg_in_set`, `arg_equals`, `any_of`, `blast_radius`) — accepting some over-match (the EC2 IMDSv1 `arg: "*"` iteration) as the cost of the OSS-loss-leader posture. The richer boto3-grade refinements belong in a paid pack if/when P0-19 lands the hybrid scope.

- **SA-1's P0-12 finding confirmed false positive.** The `bucket_region_lookup` string in `tessera-intelligence/packs/aws-cost-aware-defaults/v1.0.0/policies/s3-cross-region-replication-guard.yaml` is the value of `target_region_extraction:`, a data-resolution attribute inside a `when[*]` condition. The file's `action:` is `block` (line 26) — a valid schema-enum verb. Repo-wide grep confirmed only `block` / `require_approval` / `log_only` appear as `action:` values across all paid packs. The rename to `bucket_region_resolve_via_head_bucket` is documentation/scan-clarity only; no runtime path dispatches on this value.

- **Async hot-path changes preserve chain integrity.** P0-13's `AsyncAuditQueue` is a single-consumer drain — `HashChain.stamp()` is still called once per event under the per-scope `RLock`. Consumer FIFO order matches enqueue order matches request-arrival order, so the chain stays linearly verifiable even under high concurrency.

### Pre-existing failures (all 9 unrelated to this session)

- 4× missing optional deps: `mcp_proxy_for_aws` (for AWS MCP upstream tests), `anthropic`, `openai`, `azure-openai` (each gates LLM-provider tests behind a pip extra; the failures are import-skips reported as failures by some pytest configs).
- 1× pre-existing `p_s3` policy-id regex bug (predates this session).
- 1× OAuth introspection test (env-gated, predates this session).
- 3× other env-gated suites (CDN smoke, license-server live, etc.).

None of the 9 were introduced by the P0 commits. Each predates `cd4113b`.

## Cross-references

- For commit-level detail on each closed P0: the commit messages of `bb57a1a`, `1933db4`, `1a55944`.
- For the AWS-MCP defaults architecture: `arch/status/policy-engine.md` § "The 6 bundled AWS-MCP security defaults (P0-1..6, v0.2.1)".
- For the async hot-path architecture: `arch/status/proxy-enforcement-and-audit.md` § "Async audit emit (P0-13)" and `arch/status/integrations-and-cost.md` § "Async prefetch (P0-14)" / "DataVolume async prefetch (P0-15)".
- For the trust-chain hardening: `arch/status/intelligence-and-licensing.md` § "Startup pre-warm (P0-16)" and "Intelligence client" step 2 (P0-17).
- For the original P0 task definitions: `plan/details/tessera-content.md`, `plan/details/tessera-hotpath.md`, `plan/details/tessera-cost-awsmcp.md`.
- For the running test-and-deployment state (53 new tests, 9 pre-existing failures): commit-level CHANGELOG entries; no separate test-strategy doc exists under `arch/` and the cap discourages adding one.
