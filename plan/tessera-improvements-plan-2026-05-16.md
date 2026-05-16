# Tessera improvements plan — 2026-05-16

**Author**: Opus 1M planning session
**Strategic context**: Tessera-first per the 2026-05-15 narrowing (IN: SaaS Tessera + Console intelligence; OUT: BYOC / billing-feature work / Portal polish / OCI inventory polish).
**Scope**: `cloudmorph-tessera` package (OSS firewall + image) + `cloudmorph-mono-repo` license server + Tessera SPA + `cloudmorph-console-containers/saas/intelligence/` runtime + the consumer-side coupling to `tessera-intelligence` content.
**Out of scope**: BYOC scheduler / executor parity, Stripe billing pipeline (server-side already lives in mono-repo), Portal app surface beyond what Tessera Cloud needs, OCI inventory completeness, marketing site replatform.
**Length target**: 5–6k words; comprehensive but ordered.

---

## TL;DR — one-paragraph posture and start-here recommendation

Tessera shipped a remarkably dense 14-day window: 0.2.1 OSS release (5-place version bump, PyJWT explicit, cross-repo audit fixes, mandatory catalog signature, per-pack `manifest.signed.json` verify, tarball-SHA-256 binding, base64 signature decode), 6 bundled AWS-MCP defaults, async hot path (audit / blast-radius / DataVolume), price-table consumer wired, `add_spend()` write-back closed (it was a real production bug — every `cumulative_spend_today` policy was silently no-opping), and the producer side now ships tri-cloud parity (75 AWS + 46 Azure + 25 GCP cost mappings, blast-radius bundles for all 3 clouds, 5 new packs production-signed). The trust chain end-to-end is correct. **The next 8–12 weeks are not about feature breadth — they are about closing the operational gap between "0.2.1 release-prepped locally" and "0.2.1 live on PyPI with the Cloud wrapper rolling under it" (Batch 1), then turning the firewall into a defensible product through a unified `cost_for_call()` API and the AWS-MCP translation layer (Batches 2–3) that lets us claim "works with the official `awslabs/mcp/aws-api-mcp-server` out of the box." Everything else compounds from that.** Start here: **Batch 1 (0.2.1 close-out, ~0.5 founder-day)** is the only thing you cannot defer; do it first, then Batch 2.

---

## 1. Where Tessera stands today (current-state snapshot, grounded in code)

### 1.1 Package surface (cloudmorph-tessera)

| Fact | Value | Source |
|------|-------|--------|
| `pyproject.toml` version | `0.2.1` | [pyproject.toml:15](../pyproject.toml#L15) |
| `tessera/__init__.py:__version__` | `0.2.1` | [tessera/__init__.py:13](../tessera/__init__.py#L13) |
| Git remote tag | `v0.2.1` exists locally; push status founder-controlled | `git tag -l` |
| PyPI published | `cloudmorph-tessera 0.2.0` live since 2026-05-14; **0.2.1 not yet uploaded** (WSL env lacked twine auth) | arch/status/packaging-and-release.md |
| Image (GHCR) | `ghcr.io/cloudmorphai/tessera:0.2.0` — 0.2.1 rebuild pending | arch/status/packaging-and-release.md |
| Image (ECR mirror) | `237509402889.dkr.ecr.us-east-1.amazonaws.com/cloudmorph/tessera-cloud-prod` — 0.2.1 push pending | memory + arch |
| Source modules in `tessera/` | 59 `.py` files (verified `find`) | inventory |
| Tests | 72 `.py` files: 43 unit, 16 integration, 2 property, 11 root-level. Last green run pinned 577 passing in arch/nextsteps.md | filesystem |
| `proxy.py` size | 1301 lines (the big load-bearing surface) | `wc -l` |
| `intelligence/client.py` size | 622 lines | `wc -l` |
| `policy/conditions.py` size | 697 lines | `wc -l` |
| `cost/price_table.py` | Present (consumer wired) | filesystem |
| `cost/aws_mapping.py` | Present (10 builtin + extended loader) | filesystem |
| `audit/inspect.py` | Present (CLI tail / verify / export / inspect) | filesystem |
| `auth/oauth_rs.py` | Present (RFC 9728 metadata + RFC 7591 DCR + RFC 7662 introspection + JWKS stub) | filesystem |
| `state/daily_spend.py` | Present; auto-write integration wired (P0-18) | filesystem |
| LLM providers | 7: `gemini`, `anthropic`, `openai`, `azure_openai`, `bedrock`, `mistral`, `cohere` | filesystem |
| Bundled policies (`policies_default/`) | 18: 7 generic + 5 AWS-`-EXAMPLE` + 6 AWS-MCP P0-1..6 | filesystem |
| Schemas | `policy.schema.json`, `config.schema.json`, `audit_event.schema.json` | `schemas/` |
| Docs surface | 7 user-facing: AUDIT / CONFIGURATION / INSTALL / INTEGRATIONS / POLICIES / ROADMAP / TROUBLESHOOTING | `docs/` |
| `benchmarks/` directory | **MISSING** — no latency / RPS publishable harness exists | filesystem |
| `scripts/` directory | **MISSING** — repo has no build/sign/release helpers (the producer scripts live in tessera-intelligence) | filesystem |
| `examples/` directory | Present — but only `cursor_hooks_demo/`. No `wrap-LangChain` / `wrap-Anthropic-SDK` / `wrap-OpenAI-SDK` examples | filesystem |
| arch/status doc count | 7 (capped at 16 total per arch README) | filesystem |
| arch/improvements doc count | 1 — `v0.3.0-stripe-integration.md` | filesystem |
| Dirty files | 0 (clean) | `git status` |
| Commits ahead of `origin/main` | 0 (synced) | `git rev-list` |

### 1.2 Intelligence content (tessera-intelligence)

| Surface | Coverage | Status |
|---------|----------|--------|
| AWS mapping ops | 78 (v1.0.0: 37 + v1.1.0: 21 + v1.2.0: 20) | All YAML present; v1.2.0 tarball missing from `dist/`; signing waits on tarball build |
| Azure mapping ops | 57 (v1.0.0: 15 + v1.1.0: 26 + v1.2.0: 16) | Same — v1.2.0 tarball missing |
| GCP mapping ops | 25 (v1.0.0 bootstrap) | Same — v1.0.0 tarball missing |
| AWS blast-radius rules | 15 (v1.0.0: 9 + v1.1.0: 6 new) | v1.1.0 tarball missing |
| Azure blast-radius rules | 6 (v1.0.0 bootstrap) | Tarball missing |
| GCP blast-radius rules | 7 (v1.0.0 bootstrap) | Tarball missing |
| Production packs (signed) | 12 total: 7 from before + 5 new — `aws-cost-aware-defaults v1.1.0`, `azure-cost-aware-defaults v1.1.0`, `gcp-cost-aware-defaults v1.0.0`, `cloud-mcp-protection v1.0.0`, `cross-cloud-defaults v1.0.0` — all locally signed | New 5 unsigned-on-CDN pending tarball + publish |
| Catalog signature on `pack-index.json` / `mapping-index.json` / `blast-radius-index.json` | Live on CDN for the older versions; new entries have empty `content_hash` / `signature` pending tarball build | TI nextsteps item 2/3 |
| `official_mcp_tool_name` field | Added to all 160 mapping YAMLs (AWS 78, Azure 57, GCP 25) on 2026-05-16 — `_schemas/mapping.schema.json` accepts the new optional field | Reconciliation commits `7a5c91e`, `424e800`, `3266a54` |
| `scripts/build.sh` | **Now exists** (TI commit `1e583fa5`); 6 new tarballs need building + signing + publishing | Founder-only operational |
| Git state | 30 commits ahead of `origin/main` as of state-snapshot; subsequent push pattern: founder-controlled | State-snapshot 2026-05-16 |

### 1.3 License server + Tessera SPA (cloudmorph-mono-repo)

| Surface | State |
|---------|-------|
| `amplify/functions/tessera/` Lambdas | 4 present: `api_keys/` (Tessera Cloud key minting), `contact/`, `license/` (JWT issuance), `waitlist/` |
| `arch/tessera/status/` docs | 6: `backend-infra`, `cloud-wrapper`, `frontend`, `intelligence-cdn-and-license-server`, `license-and-billing`, `overview` |
| `tessera-cloud-wrapper/` (Fargate image) | Renamed from `tessera-cloud/` 2026-05-16 (commit `a0c2c36b`); lifespan wiring for OSS 0.2.1 in commit `ebe28c59`; DDB Streams grant in `36ee7f99` |
| Tessera SPA (`apps/tessera/`) | Policies React page + AuditLog React page shipped (P0-30 / P0-31 commit `614c30ce`); rate-limit + status endpoint code shipped (`e4176085`) — **image rebuild deferred** |
| Tessera Cognito pool | Pool 3 `us-east-1_*` (tessera-vertical) added 2026-05-14 — separate from console/portal |
| Founder-only operational | `tessera-ratelimits-prod` DDB table needs CDK provisioning in `amplify/backend/tessera.ts`; until then the wrapper's fail-open path triggers and rate limits no-op |

### 1.4 Console-intelligence tier (cloudmorph-console-containers/saas/intelligence/)

Per the strategic narrowing this tier is IN scope. Brief state:

| Surface | State |
|---------|-------|
| Runtime | Fargate-deployed container (`IntelligenceTaskDef 512/1024` in `amplify/backend/fargate.ts`) |
| Modules | `rule_engine.py`, `providers/`, `rules/`, `evidence/`, `scoring/`, `digest/`, `writer/`, `retention/`, `bedrock/`, `modelweights/`, `_validators/` |
| v2 modelweight handler | Wired 2026-05-15 (P0-20 commit `9f2a8c2`): 510 v2 rules across 47 anchor providers |
| Evidence collectors | 29 live: original 10 + 8 cloud (P0-21) + 4 OCI + 7 AI provider (P0-22) |
| Anomaly notifications | Wired (P0-23 commit `5b05a1c`); CDK grants + SNS topic + DDB dedupe live (mono-repo `a6736bcc`) |
| Known gap | Scoring is single-axis (no recency decay / blast-radius / feedback loop) — workspace `_findings.md` flags it as WEAK |
| Cross-provider correlation | Missing (workspace verdict MISSING — P2-16) |

Console-intelligence consumes Tessera-intelligence-content indirectly via the Console's own rule packs (cost / security findings). It is a separate runtime from `tessera/intelligence/client.py` — the firewall's consumer side.

---

## 2. The arch/nextsteps.md backlog triage

Per the prompt's "121-item backlog" framing: the actual `cloudmorph-tessera/arch/nextsteps.md` is **130 lines and is exclusively a P0 closure tracker**, not a forward backlog. The 108-item workspace `plan/nextsteps.md` is the broader CloudMorph backlog. Triage below covers items relevant to Tessera scope only.

### 2.1 P0 items in `arch/nextsteps.md` — closure state

| Item | Category | Action |
|------|----------|--------|
| P0-1..6 (6 AWS-MCP bundled defaults) | **shipped** (commit `1933db4`) | Strike — done |
| P0-7 (inline-wildcard scan) | **shipped** (`1933db4` + `2362a5c` fixtures) | Strike — done |
| P0-8 (sign 4 compliance packs) | **shipped** (TI `a481fe7`) | Strike — done |
| P0-9 (sign mapping bundles via `--kind mapping`) | **shipped** (TI `a481fe7`) | Strike — done |
| P0-10 (Azure enforcement pack) | **shipped** (`2362a5c` content + `a481fe7` sign) | Strike — done |
| P0-11 (AWS priv-esc blast-radius 4→9) | **shipped** (`2362a5c`) | Strike — done |
| P0-12 (`bucket_region_lookup` rename) | **shipped** (`471523f` rename, false positive resolved) | Strike — done |
| P0-13 (async audit emit) | **shipped** (`1a55944`) | Strike — done |
| P0-14 (blast-radius async prefetch) | **shipped** (`1a55944`) | Strike — done |
| P0-15 (DataVolume async prefetch) | **shipped** (`1a55944`) | Strike — done |
| P0-16 (intelligence cache pre-warm) | **shipped** (`bb57a1a`) | Strike — done |
| P0-17 (mandatory catalog signature) | **shipped** (`bb57a1a`) | Strike — done |
| P0-18 (verify `add_spend` write-back) | **shipped** (`1a55944`) — was a real production bug | Strike — done |
| P0-19 (bundling policy decision) | **shipped** (doc-only `dc4bb9d`: hybrid open-core) | Strike — done |
| Cross-repo audit (tier ordering, `bundle_url`, manifest verify, `tarball_sha256`, base64 sig) | **shipped** (`426ca84`) | Strike — done |
| PyJWT explicit dep | **shipped** (`9d84d82`) | Strike — done |
| 0.2.0 → 0.2.1 bump | **shipped** (`18ffa13`) — PyPI upload founder-only | Carry into Batch 1 |

### 2.2 `plan/nextsteps.md` items (5 open items A-E)

| Letter | Item | Verdict |
|--------|------|---------|
| A | PyJWT undeclared dep | **CLOSED** by `9d84d82` |
| B | `jwt_mcp.py` blocks event loop on JWKS cache miss | **Still valid** — fold into Batch 4 (observability + correctness pass) |
| C | mypy `untyped-decorator` errors at `oauth_rs.py:194/200/211/297` | **Still valid** — fold into Batch 9 (Cleanup) |
| D | Anthropic SDK type drift (9 `union-attr`) | **Still valid** — fold into Batch 9 |
| E | `price_table.py` subset-match 2^N worst case | **Defer** — only worth doing if a customer reports performance issue; revisit in Batch 6 if benchmarks surface it |

### 2.3 Workspace `plan/nextsteps.md` Tessera-scoped items

| ID | Item | Verdict |
|----|------|---------|
| P0-28..31 | tessera-cloud rate limits / status / Policies / AuditLog | **Shipped (code-side)**; image rebuild + DDB CDK pending → Batch 1 |
| P1-1 | Tessera content: ECR + Bedrock Guardrails / Agents mappings | **Producer-side** — overnight 2026-05-15 added Bedrock Guardrails + Agents + ECR ops to AWS v1.2.0. Strike. |
| P1-2 | Tessera content: SQS/SNS/EventBridge mappings | **Shipped overnight** in AWS v1.2.0. Strike. |
| P1-3 | Tessera content: Azure blast-radius seed | **Shipped overnight** (6 rules). Strike. |
| P1-4 | Tessera content: IAM/KMS/Secrets-Mgr mappings | Already in AWS v1.0.0 — Strike. |
| P1-5 | 6 new bundled policies (require-intent, business-hours, oversized-payload, tool-allowlist, prompt-injection, non-prod-only) | **Still valid** — Batch 8 (defensive policy bench depth) |
| P1-6 | Vendor-MCP pack depth | **Producer-side**; Tessera consumes mechanically — defer to TI |
| P1-7 | Pre-compile regex at policy load | **Still valid** — Batch 4 (observability + correctness) |
| P1-8 | Condition ordering by cost tier | **Still valid** — Batch 4 |
| P1-9 | CostBackend + UpstreamForwarder Protocols | **Still valid + load-bearing** — Batch 7 (Protocol hardening) |
| P1-10 | Latency histograms | **Still valid + Batch 4** (observability core) |
| P1-11 | Thread `conversation_id` → audit | **Still valid + Batch 4** |
| P1-12 | Cross-request Infracost cache | **Partially shipped** via price_table; defer to Batch 6 benchmarks first |
| P1-13 | Intelligence regression replay | **Still valid** — defer Q3 (Batch 10) |
| P1-14 | LLM lint | **Still valid** — Batch 10 (LLM authoring depth) |
| P1-15 | OAuth `/revoke` + JWKS sig-verify in `/introspect` | **Still valid** — Batch 9 |
| P1-16 | DCR `/register` rate limit | **Still valid + small** — Batch 9 quick win |
| P1-17 | Generic shell hook (non-Cursor) | **Still valid** — Batch 5 (examples + adoption) |
| P1-18..19 | Snapshot share + Cognito MFA + Function App + SB queue mappings | **Producer-side** — defer to TI |
| P2-1 | CFN/CodeBuild/CodePipeline mappings | **Producer-side TI** — defer |
| P2-5 | OpenTelemetry tracing | **Still valid** — Batch 4 |
| P2-6 | Promote `resources/read` + `sampling/createMessage` to policy eval | **Still valid** — Batch 8 |
| P2-7 | STS chain depth condition | **Still valid + advanced** — Batch 8 |
| P2-8 | RI/Spot/SP modeling | **Defer — out of scope through Q3** |
| P2-9 | Per-tenant TZ + retention GC + forward projection | **Defer indefinitely** unless customer asks |
| P2-10 | LLM explain + suggest + multi-turn refine | **Still valid** — Batch 10 |
| P2-11 | Intelligence cache eviction + license JWT re-verify | **Partially in v0.3.0-stripe-integration improvement** — Batch 12 |
| P2-12 | Claude Code / VS Code IDE | **Still valid** — Batch 5 |

### 2.4 Items NOT in any backlog that SHOULD be batches (Opus surfacing)

These are items I found by walking the repo that aren't tracked in any nextsteps file today:

| New item | Why it belongs | Goes to |
|---|---|---|
| `cost_for_call(operation, args, region)` unified API on `tessera/cost/` | Arch docs say "v0.3.0 OSS deliverable"; `price_table.py` exists but no top-level API; legacy `aws_mapping.py` + `infracost.py` still co-exist | Batch 2 |
| AWS MCP translation layer — `aws_ec2_RunInstances` → `call_aws` + CLI command | Required to integrate with the official `awslabs/mcp/aws-api-mcp-server` (71 of 78 AWS ops route there); flagged in TI nextsteps #18 | Batch 3 |
| `official_mcp_tool_name` engine resolver — use as dispatch key | TI nextsteps #20; required to honour the reconciliation work | Batch 3 |
| Production blast-radius `BlastRadiusBackend` for Azure + GCP | OSS `tessera/integrations/aws/blast_radius.py` is AWS-only; producer rules now exist for Azure + GCP | Batch 7 |
| Benchmarks publishing harness | No `benchmarks/` folder exists; "sub-millisecond decision latency" is unmeasured | Batch 6 |
| `examples/` for wrap-LangChain / wrap-Anthropic-SDK / wrap-OpenAI-SDK / wrap-Claude-Code | Only `cursor_hooks_demo` exists today; competing firewalls (Runlayer, MintMCP) have examples | Batch 5 |
| Real-JWT 8-scenario CDN smoke test execution | Test harness shipped; founder needs to mint 3 JWTs and run | Batch 1 |
| `tessera-ratelimits-prod` DDB CDK provisioning | Code is live; table not in CDK | Batch 1 |
| Reproducibility — `bump-version.py` single-source-of-truth | Today 5-place manual sync per tag; fragile | Batch 9 |
| `vendor-mcp-protection` extension to Azure-MCP / AWS-MCP / Stripe-MCP / Linear-MCP | Per `policy-packs.md`: "pack scaffolds are precious; policy YAMLs are cheap" — producer-side work but Tessera consumes mechanically | Defer to TI |
| Customer-facing documentation site (Docusaurus or similar) | Today docs are 7 .md files in `docs/`; no hosted site; adoption fuel | Defer Q3 unless funding |
| Audit-log export to external SIEM (Splunk/Datadog/Snowflake/etc.) | Workspace verdict P1-44 — MISSING; tessera-cloud SIEM egress sinks | Batch 11 |
| Telemetry opt-out / consent flow | Today `tessera audit export` is local-only; no upstream telemetry exists yet — but if we add any, legal/trust matters | Defer Q3 |

### 2.5 Items deliberately deferred indefinitely

| Item | Reason | Revisit trigger |
|------|--------|-----------------|
| Stripe direct API calls from OSS | Architecturally OUT — Stripe stays server-side | Never |
| Seat-count enforcement in OSS | License-server's job | Never |
| BYOC scheduler polish | OUT per strategic narrowing | Strategic narrowing revisit |
| Portal API-token UI | OUT per strategic narrowing | Strategic narrowing revisit |
| OCI BYOC inventory completeness | OUT per strategic narrowing | Strategic narrowing revisit |
| Cross-cloud SaaS-seat waste detector | Console-analytics scope, OUT | Strategic narrowing revisit |
| Multi-region tessera-cloud routing | P2-25 — only after first big enterprise asks | Customer ask |
| RI / Spot / Savings-Plan modeling | P2-8 — requires cost-data depth we don't have yet | Customer ask |
| LLM cost-discipline `--model-name` CLI flag | Not customer-blocking | Customer ask |
| Per-tenant TZ + retention GC | P2-9 — not blocking | Customer ask |
| Hosted JSON-Schema URLs (`tessera-intelligence.cloudmorph.io/schemas/*.json`) | Today resolved via repo file; hosting is tidiness only | If a third-party tool asks |
| Catalog auto-regeneration from per-pack manifests | Tidiness; `tests/test_pack_manifests.py` polices drift | If drift surfaces twice |

**Triage outcome**: ~30 still-valid items mapped into 12 batches below. The aggressive cut from the workspace 108-item backlog is justified by the strategic narrowing and the fact that most P0/P1-1..4 items are already shipped.

---

## 3. Batches — ordered with dependencies, scope, acceptance, effort

### Batch 1 — 0.2.1 close-out + operational tail (**START HERE**)

- **Purpose**: Get 0.2.1 from "release-prepped locally" to "live on PyPI + GHCR + ECR with the Cloud wrapper rolling under it." Block on this — everything else in this plan compounds from a published 0.2.1 baseline.
- **Dependencies**: None.
- **Cross-repo dependencies**:
  - `cloudmorph-mono-repo`: `tessera-ratelimits-prod` DDB CDK provisioning needed; tessera-cloud-wrapper image rebuild for 0.2.1; ECS `force-new-deployment` to roll Fargate
  - `tessera-intelligence`: 6 missing tarballs need building + signing + publishing if Tessera 0.2.1's pre-warm round-trip is to land on the new content
- **Scope** (concrete):
  - [ ] Verify `pyproject.toml` + `__init__.py` + `README.md` + `docs/INSTALL.md` + `CHANGELOG.md` all read 0.2.1 (already true; confirm)
  - [ ] Founder runs `python -m build` + `twine upload dist/*` from WSL (after setting `TWINE_USERNAME=__token__` + token from PyPI Trusted Publisher)
  - [ ] Founder rebuilds the Cloud wrapper image at `cloudmorph-mono-repo/tessera-cloud-wrapper/`: `bash build.sh 0.2.1` → push to ECR `cloudmorph/tessera-cloud-prod:0.2.1`
  - [ ] `aws ecs update-service --cluster <prod> --service tessera-cloud --force-new-deployment`
  - [ ] Add `tessera-ratelimits-prod` DDB table to `amplify/backend/tessera.ts` (PK `tenant_id`, PAY_PER_REQUEST); `amplify pipeline-deploy`
  - [ ] Founder runs `tessera-intelligence/scripts/build.sh v1.0.0` for the 6 missing tarballs (aws-v1.2.0, azure-v1.2.0, gcp-v1.0.0, blast-radius/aws-v1.1.0, blast-radius/azure-v1.0.0, blast-radius/gcp-v1.0.0) + 5 new packs
  - [ ] Founder signs each tarball: `python scripts/sign_pack.py --kind mapping --tarball <path>` per bundle, `python scripts/sign_pack.py --tarball-hash <sha> <path>` per pack
  - [ ] Founder runs `_update_catalogs_from_dist.py` to backfill catalog `content_hash` + `signature` for the new entries
  - [ ] Founder runs `./scripts/publish.sh v1.0.0` (S3 sync)
  - [ ] Founder runs `aws cloudfront create-invalidation --distribution-id E2IT1ZRR36STAC --paths "/v1.0.0/*"`
  - [ ] Founder runs the 8-scenario real-JWT CDN smoke test from `tests/integration_cdn_smoke.py` after minting 3 test-tenant JWTs at `admin.cloudmorph.io`
  - [ ] Push `cloudmorph-tessera v0.2.1` tag if not pushed; push `tessera-intelligence main` (~30+ commits); push mono-repo 3–5 commits ahead
- **Acceptance criteria**:
  - [ ] `pip install cloudmorph-tessera==0.2.1` works from a clean venv
  - [ ] `ghcr.io/cloudmorphai/tessera:0.2.1` exists and `cosign verify` succeeds against the Sigstore identity
  - [ ] ECR digest in CDK matches the new 0.2.1 image; `aws ecs describe-services` reports `runningCount == desiredCount` on the new task definition
  - [ ] Production round-trip smoke against `s3://tessera-intelligence-prod/v1.0.0/` passes against the 0.2.1 client
  - [ ] CDN smoke test: 8/8 scenarios PASS (or skip with documented reason)
  - [ ] `tessera-ratelimits-prod` exists in DDB; the wrapper's rate-limit code is no longer in fail-open path
- **Effort**: ~0.5 founder-day (4 hours wall-clock if no surprises). Pure ops — no Sonnet sub-agent needed.
- **Risk**: LOW (everything is rehearsed and the changes are signed local artifacts moving to remote infra).
- **Sales / narrative value**: HIGH. Founder cannot honestly claim "0.2.1 shipped" until this is done. The Google × Antler / Cohort pitch on May 22 wants the live PyPI version to read 0.2.1.

### Batch 2 — v0.3.0 unified `cost_for_call()` API

- **Purpose**: Promote `tessera/cost/price_table.py` from "exists" to "is the canonical cost-resolution surface." Deprecate the legacy `aws_mapping.py` + `infracost.py` per-call paths into clearly-named fallbacks. The arch doc in `integrations-and-cost.md` says "the v0.3.0 OSS deliverable" — make it so.
- **Dependencies**: Batch 1 (0.2.1 live).
- **Cross-repo dependencies**:
  - `tessera-intelligence`: producer must publish `<cloud>-prices-<version>.json` artifacts alongside mapping bundles (today AWS-v1.0.0 + AWS-v1.1.0 covered; v1.2.0 + Azure + GCP price-tables need to be materialized by `scripts/materialize_prices.py` with a real `INFRACOST_API_KEY`)
  - The TI side currently has dry-run only — founder needs to run live materialization with the key
- **Scope** (concrete):
  - [ ] `tessera/cost/__init__.py`: export a top-level `cost_for_call(operation: str, args: dict, region: str | None = None) -> SkuResult | None` (today this lives in `price_table.py` only, behind an instance method)
  - [ ] `tessera/proxy.py:_prefetch_cost`: route via `cost_for_call` first (price-table); fall back to `InfracostClient.query_sku` on miss; record a `cost_source` field in `cost_cache[tool_name]` (one of `price_table` / `infracost_live` / `miss`)
  - [ ] Emit `cost_source` in audit event payload alongside `pricingSnapshotId`
  - [ ] `tessera/policy/conditions.py:_evaluate_predicted_cost`: confirm it reads `context["cost_cache"]` only (already true per arch); add an assertion so future drift fails loudly
  - [ ] Deprecation comment in `tessera/cost/aws_mapping.py:_BUILTIN_MAPPING` pointing at price-table as the primary path
  - [ ] Update `arch/status/integrations-and-cost.md` § "Cost: AWS mapping shim" to mark legacy paths as fallback-only
  - [ ] Add Azure + GCP price-table loader paths in `IntelligenceClient._load_price_tables_from_cache()` (today AWS-only)
  - [ ] Bench: median latency of `cost_for_call` < 1ms on a 200-op price table (record p50/p95/p99)
- **Acceptance criteria**:
  - [ ] `tests/unit/cost/test_cost_for_call.py` exercises hit / miss / fallback / ceiling-band paths; all GREEN
  - [ ] `tests/integration/test_proxy_cost_resolution.py` simulates one `predicted_cost`-gated `tools/call` and asserts `cost_source: "price_table"` in the audit event
  - [ ] Bench numbers in commit message: p50/p95/p99
  - [ ] Tag `v0.3.0` after merge; PyPI shows 0.3.0
- **Effort**: ~3 founder-days with Sonnet 4.6 sub-agent assist (1 Sonnet day for the API + tests, 0.5 day for proxy integration, 0.5 day for arch update, 1 day buffer for bench + release).
- **Risk**: MEDIUM. The semantics of "cost_source" need to make sense to operators; deprecation of legacy paths needs to be reversible (keep them callable for a release).
- **Sales / narrative value**: HIGH. "Cost-aware policies with sub-millisecond decision latency, no Infracost dependency at call time, works air-gapped" is a defensible competitive line. Today the arch doc claims this; v0.3.0 makes the code match the claim.

### Batch 3 — AWS MCP translation layer + `official_mcp_tool_name` engine resolver

- **Purpose**: Close the integration gap between Tessera's canonical tool naming (`aws_ec2_RunInstances`) and the official `awslabs/mcp/aws-api-mcp-server` surface (a single `call_aws` tool with CLI string parameters). Without this, customers can't enable Tessera in front of the official AWS MCP server. The reconciliation work in TI added `official_mcp_tool_name` to every YAML — this is the consumer-side mechanical work.
- **Dependencies**: Batch 1 (0.2.1 live). Can run in parallel with Batch 2.
- **Cross-repo dependencies**:
  - `tessera-intelligence`: the field is already in every mapping YAML (commits `7a5c91e`, `424e800`, `3266a54`)
  - `tessera-intelligence/_schemas/mapping.schema.json` updated to accept the optional field
- **Scope** (concrete):
  - [ ] `tessera/cost/aws_mapping.py:load_extended_mappings`: parse the optional `official_mcp_tool_name` + `official_mcp_server` fields into `InfracostQuery`
  - [ ] `tessera/integrations/aws/upstream.py:AWSMcpUpstream`: add a `_translate_call_aws_op(tool_name, args)` method that, when the upstream's effective tool surface is `awslabs/mcp/aws-api-mcp-server` (config flag `upstream.aws_mcp_server: aws-api-mcp-server`), wraps the call as `{"tool": "call_aws", "command": "<aws-cli-string built from tool_name + args>"}`
  - [ ] Builder of the CLI string lives at `tessera/integrations/aws/cli_translator.py`: for each canonical op known to `aws_mapping.py`, encode the `aws <service> <verb> --flag <value>` form. Use the existing `args_used` field on `InfracostQuery` to know which args to thread.
  - [ ] `tessera/proxy.py`: branch the upstream forward based on the new `aws_mcp_server` config field; route both shapes correctly
  - [ ] Audit-event payload: record both `canonical_tool_name` and `effective_tool_name` so operators can search either
  - [ ] Engine resolver — `tessera/policy/matchers.py`: when matching `tool_name_in` against an incoming `call_aws` invocation, reverse-resolve via `args.command` to the canonical name so policies authored against `aws_ec2_RunInstances` still fire
- **Acceptance criteria**:
  - [ ] `tests/integration/test_aws_api_mcp_translation.py`: routes through the translator end-to-end with a fixture upstream; policies fire on canonical name
  - [ ] Round-trip test: Tessera's bundled `aws-mcp-passrole-guard` correctly blocks an inbound `call_aws` invocation whose `command` resolves to `iam:PassRole`
  - [ ] `arch/status/integrations-and-cost.md` and `arch/status/policy-engine.md` updated to document the dispatch contract
- **Effort**: ~4 founder-days. Mostly mechanical Sonnet work — translation table for the ~78 AWS ops is the biggest item. Reverse-resolver is delicate (CLI string parsing); pin it with property tests.
- **Risk**: MEDIUM. CLI argument parsing has edge cases; favour explicit per-op handlers over a generic parser.
- **Sales / narrative value**: HIGH. "Drops in front of `awslabs/mcp/aws-api-mcp-server` with zero configuration" is the highest-leverage feature you can ship right now. The AWS MCP customers exist; we just need to be reachable from where they already are.

### Batch 4 — Observability + hot-path correctness pass

- **Purpose**: Move Tessera's observability from "counters in `/metrics` if you enable Prometheus" to "latency histograms + OTel traces + structured-event hooks an operator can plug into Splunk/Datadog/Snowflake." Close the residual hot-path correctness items (`jwt_mcp.py` blocking JWKS, regex pre-compilation at load).
- **Dependencies**: Batch 1.
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] `tessera/observability/` new subpackage. Exports `metrics.py` (Prometheus histograms — decision-latency, audit-emit-latency, blast-radius-prefetch-latency, cost-prefetch-latency, all keyed by upstream+mode), `tracing.py` (OpenTelemetry spans on the proxy hot path — `tessera.proxy.handle_tools_call`, `tessera.policy.evaluate`, `tessera.audit.emit`), `events.py` (structured-event hook protocol — `OnDecision`, `OnAuditEmit` callbacks an operator implements + registers)
  - [ ] `tessera/proxy.py`: instrument every span boundary with OTel spans (no-op when no exporter configured); wrap policy.evaluate + audit emit in histograms
  - [ ] `tessera/auth/jwt_mcp.py`: pre-warm JWKS cache in lifespan startup (Path A from `plan/nextsteps.md` item B); make `validate_jwt` truly non-blocking
  - [ ] `tessera/policy/regex_safety.py:validate_pattern`: also pre-compile the regex object and stash it on the loaded `Policy` instance so per-request evaluators reuse it (P1-7)
  - [ ] `tessera/policy/conditions.py`: cost-tier condition ordering (P1-8) — within a single policy's `when` list, evaluate `arg_equals` / `arg_in_set` / `tool_name_in` before regex / semantic conditions
  - [ ] `tessera/audit/`: thread `conversation_id` (read from `_meta.tessera_intent.conversation_id` or `_meta.conversation_id`) into the emitted event payload (P1-11)
  - [ ] `arch/status/proxy-enforcement-and-audit.md`: add an "Observability" section
- **Acceptance criteria**:
  - [ ] `/metrics` exposes `tessera_decision_latency_seconds_bucket{quantile=...}` histograms
  - [ ] Setting `TESSERA_OTEL_ENDPOINT=...` exports spans to an OTLP collector
  - [ ] `tests/unit/auth/test_jwks_prewarm.py` confirms first request after lifespan does not block
  - [ ] Bench: regex pre-compile reduces evaluation latency on a 18-policy default set by measurable amount
- **Effort**: ~3 founder-days. OTel integration is the longest piece.
- **Risk**: LOW. Additive instrumentation; no behaviour change to the decision path.
- **Sales / narrative value**: MEDIUM-HIGH. Operators ask "can I see what's happening?" in every demo. Histograms + OTel make the answer "yes, plug your existing stack in."

### Batch 5 — Examples + adoption surface

- **Purpose**: Lower the activation cost for new users. Today the only documented integration is Cursor hooks. Customers who want to wrap LangChain, the Anthropic SDK, the OpenAI SDK, Claude Code, or VS Code Copilot have nothing to copy-paste.
- **Dependencies**: Batch 1.
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] `examples/wrap_anthropic_sdk/` — 30-line example: anthropic-python client pointed at Tessera as an HTTP MCP proxy
  - [ ] `examples/wrap_openai_sdk/` — same shape for `openai.beta.tools`
  - [ ] `examples/wrap_langchain/` — `LangChainAgent` with a `MCPToolNode` routed through Tessera
  - [ ] `examples/wrap_claude_code/` — `~/.claude.json` config that points Claude Code at Tessera (build on the existing `tessera install-claude-code` CLI subcommand)
  - [ ] `examples/wrap_vscode_copilot/` — VS Code MCP server config
  - [ ] Generic shell-hook recipe in `recipes/generic-shell-hook.md` (P1-17): for IDEs without bespoke hook support, run a 20-line shell wrapper that POSTs to Tessera's `/intent` and surfaces the decision via stdout/exit-code
  - [ ] `README.md` adoption-section update: link the examples; add a "tessera serves the request → tessera audits it → tessera blocks if your policy says so" hero diagram
- **Acceptance criteria**:
  - [ ] Each example runs end-to-end against a local `tessera serve` with a sample policy
  - [ ] CI runs the example smoke tests via a minimal `pytest` mark
- **Effort**: ~1.5 founder-days. 30 LOC per example, plus a README pass.
- **Risk**: LOW.
- **Sales / narrative value**: HIGH for marketing / VC pitch surface. Concrete examples turn "AI firewall" into "drop this in." The Mistralship / Cohere / Antler narrative wants this.

### Batch 6 — Benchmarks publishing harness

- **Purpose**: Quantify "sub-millisecond decision latency" and "1000 RPS sustained." The claim is in the arch docs; today it's unmeasured.
- **Dependencies**: Batch 2 (price-table is on the hot path), Batch 4 (histograms).
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] `benchmarks/` new top-level directory
  - [ ] `benchmarks/decision_latency.py` — pytest-benchmark suite measuring `engine.evaluate()` p50/p95/p99 on a 18-policy default set across 10k synthetic requests
  - [ ] `benchmarks/rps_sustained.py` — `locust` or `wrk`-driven load test against `tessera serve` running on a single uvicorn worker; record sustained RPS at p99 < 10ms
  - [ ] `benchmarks/blast_radius_latency.py` — with prefetch on vs off, demonstrate the win
  - [ ] `benchmarks/README.md` — publishable results table + reproduction commands
  - [ ] CI job in `.github/workflows/bench.yml` running on tagged releases; commits result table to `benchmarks/results/<version>.md`
- **Acceptance criteria**:
  - [ ] Published numbers in `benchmarks/results/v0.3.0.md` (or whatever version): p50/p95/p99 + sustained RPS
  - [ ] Reproduction instructions readable by a non-CloudMorph engineer
- **Effort**: ~1.5 founder-days.
- **Risk**: LOW (read-only measurements).
- **Sales / narrative value**: HIGH. Numbers are the difference between "fast" and "fast enough to deploy in production." This batch produces the slide.

### Batch 7 — Production blast-radius for Azure + GCP + Protocol hardening

- **Purpose**: The producer side now ships Azure + GCP blast-radius rules (6 + 7) and stub evaluators. The consumer side OSS package today has only `tessera/integrations/aws/blast_radius.py`. Without production evaluators per cloud, the `blast_radius` condition for Azure/GCP fail-closes (over-blocks) — usable but not great. Promote the stubs into production. Simultaneously add the missing extension Protocols (`CostBackend`, `UpstreamForwarder`) so Tessera Cloud can swap them out cleanly (P1-9).
- **Dependencies**: Batch 1.
- **Cross-repo dependencies**:
  - `tessera-intelligence/tests/azure_blast_radius_stub.py` and `gcp_blast_radius_stub.py` already exist — port their algorithms
- **Scope**:
  - [ ] `tessera/integrations/azure/blast_radius.py` — Azure-RBAC live-API evaluator. Tool dispatch table parallel to AWS: `azure_authorization_RoleAssignments_Create` → live `az role assignment list` via `azure-mgmt-authorization`; `azure_keyvault_Vaults_CreateOrUpdate` → counts principals from the supplied access-policy doc
  - [ ] `tessera/integrations/gcp/blast_radius.py` — GCP IAM live-API evaluator. Tool dispatch table: `gcp_resourcemanager_Projects_SetIamPolicy` → counts bindings; `gcp_iam_ServiceAccountKeys_Create` → conservative 1
  - [ ] `tessera/integrations/azure/upstream.py` and `gcp/upstream.py` — extend the adapter pattern from `aws_mcp` to `azure_mcp` (when Microsoft ships official server stability) and `gcp_mcp`; for today, stubs that warn-and-fallback so config doesn't break
  - [ ] `tessera/pluggable.py`: define `CostBackend` and `UpstreamForwarder` Protocols (P1-9). Move `InfracostClient` to implement `CostBackend`; move every upstream class to implement `UpstreamForwarder`. Pluggable resolution via `TESSERA_COST_BACKEND` and `TESSERA_UPSTREAM_FORWARDER_<NAME>` env vars
  - [ ] `arch/status/integrations-and-cost.md`: add Azure + GCP sections parallel to AWS
- **Acceptance criteria**:
  - [ ] `tests/unit/integrations/test_azure_blast_radius.py` + `test_gcp_blast_radius.py` — same shape as `test_aws_blast_radius.py`
  - [ ] `tests/integration/test_pluggable_cost_backend.py` — swaps `CostBackend` impl via env var; engine still decides correctly
  - [ ] Extras groups in `pyproject.toml`: `[azure]`, `[gcp]` for `azure-mgmt-authorization` / `google-cloud-resource-manager`
- **Effort**: ~5 founder-days.
- **Risk**: MEDIUM. Live API call timing on Azure ARM and GCP IAM can be slow; reuse the AWS prefetch pattern (P0-14).
- **Sales / narrative value**: HIGH. "Tri-cloud parity at the firewall level" matches the producer-side tri-cloud parity. Lets the pitch claim it across the whole stack.

### Batch 8 — Defensive policy bench depth (OSS expansion)

- **Purpose**: Workspace P1-5 — ship 6 new bundled OSS policies (require-intent, business-hours, oversized-payload, tool-allowlist, prompt-injection, non-prod-only). Plus P2-6 (promote `resources/read` + `sampling/createMessage` to policy eval) and P2-7 (STS chain depth condition). This deepens the OSS-loss-leader without competing with paid packs.
- **Dependencies**: Batch 1. Independent of Batch 2 / 3 / 4 / 5 / 6 / 7.
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] 6 new bundled YAMLs at `tessera/policies_default/`:
    - `require-intent.yaml` — block tool calls whose `_meta.tessera_intent` is absent on a configured upstream
    - `business-hours-only.yaml` — block writes outside `9-17` in configured TZ (uses `time_of_day_outside`)
    - `oversized-payload.yaml` — block calls whose `args` JSON-serialize > 64KB (uses `arg_size_greater_than`)
    - `tool-allowlist.yaml` — block any tool NOT in an explicit allowlist (uses `none_of` + `tool_name_in`)
    - `prompt-injection-heuristic.yaml` — block calls where args contain `ignore previous instructions` / common-prompt-injection regex bench
    - `non-prod-only.yaml` — block any write on resources tagged `environment != prod` (counterpoint to `prod-protection`)
  - [ ] `tests/test_policies_default.py` — fire/pass tests for each
  - [ ] `tessera/policy/conditions.py`: new `arg_path_matches_regex` condition (closes the dot-path arg access gap; `arg_matches_regex` only works on top-level args today)
  - [ ] `tessera/policy/conditions.py`: new `sts_chain_depth_greater_than` condition (P2-7) — counts assume-role chain depth from `_meta.aws_session_chain` if present
  - [ ] `tessera/proxy.py`: promote `resources/read` + `sampling/createMessage` from pass-through-with-audit to engine-evaluated (P2-6); add a config flag to revert
- **Acceptance criteria**:
  - [ ] 24 bundled policies total (12 prior + 6 new + the 6 AWS-MCP defaults remain)
  - [ ] `policies_default/` directory enumerated in `arch/status/policy-engine.md` under "The 6 new ship-with-package generic policies"
  - [ ] `tests/test_policies_default.py::test_all_default_policy_actions_in_schema_enum` still GREEN
- **Effort**: ~2.5 founder-days.
- **Risk**: LOW.
- **Sales / narrative value**: MEDIUM. Each new policy is one more "out of the box" defense Tessera can claim; cumulatively this is the "loss-leader OSS depth" story.

### Batch 9 — Cleanup + correctness tail

- **Purpose**: Close the mypy/ruff cleanup items in `plan/nextsteps.md` C and D. Plus the workspace P1-15 (`/revoke` + JWKS sig-verify) and P1-16 (DCR `/register` rate limit). Plus an automation pass to remove the 5-place manual version sync.
- **Dependencies**: None (parallel with anything).
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] `tessera/auth/oauth_rs.py:194/200/211/297` — type `app_ref: FastAPI` (mypy untyped-decorator fix)
  - [ ] `tessera/llm/anthropic.py` — explicit isinstance loop with return-type annotation (mypy union-attr fix for 9 errors)
  - [ ] `tessera/auth/oauth_rs.py` — add `POST /revoke` (RFC 7009)
  - [ ] `tessera/auth/oauth_rs.py:_introspect` — Ed25519/RSA signature-verify the inbound JWT before claims access (P1-15)
  - [ ] `tessera/auth/oauth_rs.py` — per-IP token-bucket rate limiter on `POST /register` (P1-16)
  - [ ] New `scripts/bump_version.py` — single-source-of-truth helper (`python scripts/bump_version.py 0.3.0`) updates all 5 places + CHANGELOG section header
  - [ ] `tessera/_version.py` — alternate single-source via `importlib.metadata.version` so README references can be programmatic
- **Acceptance criteria**:
  - [ ] `mypy tessera/` is 0-errors-in-tessera (today: 9 known)
  - [ ] `pytest tests/test_oauth_resource_server.py` GREEN with new `/revoke` + sig-verify tests
  - [ ] `python scripts/bump_version.py 0.3.0` updates pyproject + `__init__.py` + README + INSTALL + CHANGELOG in one shot
- **Effort**: ~2 founder-days.
- **Risk**: LOW.
- **Sales / narrative value**: LOW directly; HIGH indirectly (clean lint/typecheck signals a serious codebase to enterprise security reviewers).

### Batch 10 — LLM policy authoring depth

- **Purpose**: Workspace P1-14 (LLM lint), P2-10 (explain / suggest / multi-turn refine). Current state: 7 providers, single-turn `propose_policies` + `analyze_tools`, retry-on-invalid-YAML. To make this product-grade we need: lint generated policies for ReDoS / unreachable conditions / regex bench overlap; explain generated YAMLs in plain English; suggest improvements; allow a multi-turn refinement loop where the operator narrows the intent.
- **Dependencies**: Batch 1.
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] `tessera/llm/lint.py` — new module. Lints a `PolicyRecommendation` for: ReDoS-prone regex (reuse `regex_safety.validate_pattern`), conflicting overlapping policies (same upstream + tool + lower-priority block under higher-priority allow), unreachable conditions (`tool: aws_iam_*` + `condition arg_equals UserName=$value` that can never match)
  - [ ] `tessera/cli.py:policy_author` — accept `--lint` flag; refuse to write a policy file that fails lint without `--force`
  - [ ] `tessera/llm/explain.py` — new module. Given a policy YAML, produce a plain-English summary ("blocks `iam:CreateUser` when target is the admin tier")
  - [ ] `tessera/llm/suggest.py` — given a customer's existing policy set, suggest policies for gaps detected (e.g., tool catalog has `s3:DeleteBucket` but no policy mentions it)
  - [ ] Multi-turn refinement in `tessera policy author`: `--refine` flag enters an interactive loop where the operator critiques the last draft; the next call carries the critique as an `Earlier draft was: ... Operator feedback: ...` prefix
- **Acceptance criteria**:
  - [ ] `tessera policy author --intent "block destructive S3" --lint` rejects a ReDoS-prone draft and asks for re-roll
  - [ ] `tessera policy explain --file policies/my-policy.yaml` outputs a paragraph
  - [ ] `tessera policy suggest --policy-dir policies/ --mcp <url>` outputs a gap report
- **Effort**: ~3 founder-days. Mostly Sonnet-coder work.
- **Risk**: LOW.
- **Sales / narrative value**: MEDIUM-HIGH. "I describe what I want and Tessera writes the YAML, then explains it back, then improves it" is a compelling demo line for the Antler/Cohere/Mistralship pitches.

### Batch 11 — Audit log SIEM egress sinks

- **Purpose**: Workspace P1-44. Today the audit log is SQLite + stdout only. Enterprise customers want to ship events to Splunk / Datadog / Snowflake / S3-as-Parquet / Vector / Kafka. Use the existing `AuditSink` Protocol surface — these are plug-in classes.
- **Dependencies**: Batch 1. Batch 4 helpful (events.py callback protocol).
- **Cross-repo dependencies**: None.
- **Scope**:
  - [ ] `tessera/audit/sinks/splunk.py` — HEC (HTTP Event Collector) sink; bearer-token auth, batched POST
  - [ ] `tessera/audit/sinks/datadog.py` — Datadog Logs API sink
  - [ ] `tessera/audit/sinks/snowflake.py` — JSONL upload to a Snowflake stage (uses `snowflake-connector-python`)
  - [ ] `tessera/audit/sinks/s3_parquet.py` — Parquet writer to S3, partitioned by `scope` and `event_date`
  - [ ] `tessera/audit/sinks/vector.py` — generic HTTP POST to a Vector aggregator
  - [ ] Optional extras groups: `[splunk]` / `[datadog]` / `[snowflake]` / `[s3-parquet]`
  - [ ] `arch/status/proxy-enforcement-and-audit.md`: new "SIEM egress" subsection
- **Acceptance criteria**:
  - [ ] Each sink is exercised by `tests/unit/audit/test_<sink>.py` against a local mock
  - [ ] One end-to-end integration test ships an event to an in-memory Splunk-like collector
- **Effort**: ~3 founder-days. Most Sonnet-coder work.
- **Risk**: LOW.
- **Sales / narrative value**: HIGH for enterprise prospects. Most security teams already pay for one of these systems; the integration is "yes" vs "build it yourself."

### Batch 12 — v0.3.0 Stripe-integration consumer-side

- **Purpose**: Land the existing `arch/improvements/v0.3.0-stripe-integration.md` work. Cache-eviction policy decision, three new log events, `licenseTier` in audit events. Closes the file → merges content into `intelligence-and-licensing.md`.
- **Dependencies**: Batch 1 (and the license server in mono-repo must wire Stripe subscription state into the JWT tier claim — which the May 2026 work has done).
- **Cross-repo dependencies**:
  - `cloudmorph-mono-repo`: license server already issues tier claims; no further change needed
- **Scope**: (per `arch/improvements/v0.3.0-stripe-integration.md`):
  - [ ] Test that mocks license server returning `tier: developer` then `tier: free` confirms the consumer accepts transition
  - [ ] Add `event=license_tier_change from=<old> to=<new>` log
  - [ ] Add `event=pack_above_tier_skipped` log
  - [ ] Add `event=license_subscription_expired customer_id=<id>` log
  - [ ] Extend `AuditEmitter` to carry `licenseTier`
  - [ ] Optional `intelligence.evict_above_tier_packs` flag (default `false`)
  - [ ] Merge improvement file content into `arch/status/intelligence-and-licensing.md`; delete the improvement file
- **Acceptance criteria**: as per the improvement file's "Acceptance criteria" section.
- **Effort**: ~1 founder-day (per the improvement file's estimate).
- **Risk**: LOW.
- **Sales / narrative value**: LOW direct; HIGH operational (closes a known improvements file, gets `arch/improvements/` to 0 active files).

---

## 4. Recommended 8–12 week sequence

Calendar-anchored. Founder's realistic capacity: 3–5 hours/day on weekdays, scattered, while at Telus. Sonnet 4.6 sub-agents assist throughout.

### Week 1 (2026-05-19 → 2026-05-23) — Operational close-out

- **Batch 1: 0.2.1 close-out** (0.5 founder-day)
- Push 30 unpushed `tessera-intelligence` commits (founder-only)
- Mono-repo 3–5 commits ahead push (founder; gated by Amplify deploy timing)
- Sanity: 8-scenario CDN smoke test
- Status: 0.2.1 live on PyPI; new content live on CDN; Cloud wrapper rolling

### Week 2–3 (2026-05-26 → 2026-06-06) — Cost API + adoption surface

- **Batch 2: `cost_for_call()` unified API** (3 founder-days)
- **Batch 5: examples + adoption** (1.5 founder-days)
- Tag v0.3.0 mid-Week 3
- Status: 0.3.0 live; price-table is the canonical path; 5 new examples in repo

### Week 4 (2026-06-09 → 2026-06-13) — Benchmarks publishing

- **Batch 6: benchmarks** (1.5 founder-days)
- Publish results table; tweet/medium the numbers
- Status: defensible latency claims with reproducible numbers

### Week 5–6 (2026-06-16 → 2026-06-27) — AWS-MCP translation

- **Batch 3: AWS MCP translation layer + `official_mcp_tool_name` resolver** (4 founder-days)
- Tag v0.3.1 with the translation layer
- Status: drops in front of `awslabs/mcp/aws-api-mcp-server` with zero config

### Week 7–8 (2026-06-30 → 2026-07-11) — Observability + correctness

- **Batch 4: observability + correctness pass** (3 founder-days)
- **Batch 9: cleanup tail** (2 founder-days, can run parallel with Batch 4)
- Tag v0.4.0
- Status: histograms + OTel live; mypy clean; bump_version.py automation

### Week 9–10 (2026-07-14 → 2026-07-25) — Azure + GCP blast-radius + Protocols

- **Batch 7: production blast-radius Azure + GCP + Protocol hardening** (5 founder-days)
- Tag v0.4.1
- Status: tri-cloud at the firewall layer

### Week 11 (2026-07-28 → 2026-08-01) — OSS depth expansion

- **Batch 8: defensive policy bench depth** (2.5 founder-days)
- Tag v0.5.0
- Status: 24-policy default set

### Week 12 (2026-08-04 → 2026-08-08) — Stripe consumer + LLM authoring start

- **Batch 12: Stripe consumer-side** (1 founder-day)
- **Batch 10: LLM authoring depth (lint + explain)** — start (1.5 days; spillover into Week 13)
- Tag v0.5.1

### Beyond Week 12 (if bandwidth allows)

- **Batch 10 finish: LLM suggest + multi-turn refine** (1.5 days)
- **Batch 11: SIEM egress sinks** (3 days)

---

## 5. Cross-repo dependencies map

| Cloudmorph-tessera batch | Requires from elsewhere | Produces for elsewhere |
|--------------------------|-------------------------|------------------------|
| Batch 1 (0.2.1 close-out) | TI tarballs built + signed + published; mono-repo CDK for `tessera-ratelimits-prod`; mono-repo image rebuild | The OSS 0.2.1 baseline every downstream depends on |
| Batch 2 (`cost_for_call`) | TI `<cloud>-prices-<version>.json` artifacts materialized with live `INFRACOST_API_KEY` (today dry-run only) | A unified consumption surface for the price table TI produces |
| Batch 3 (AWS MCP translation) | TI `official_mcp_tool_name` field on every mapping (shipped 2026-05-16) | Customer-facing integration with `awslabs/mcp/aws-api-mcp-server` |
| Batch 4 (observability) | None | OTel spans + histograms consumable by Console-intelligence's own dashboards |
| Batch 5 (examples) | None | Adoption funnel into the OSS package |
| Batch 6 (benchmarks) | Batch 2 (price table on hot path), Batch 4 (histograms) | Defensible latency numbers for sales |
| Batch 7 (Azure/GCP blast-radius + Protocols) | TI Azure/GCP blast-radius rules (shipped 2026-05-15) + stub algorithms | Production tri-cloud parity that matches producer-side parity |
| Batch 8 (OSS depth) | None | More-defensible loss-leader |
| Batch 9 (cleanup) | None | Clean lint/typecheck signals |
| Batch 10 (LLM authoring) | None | Demoable "describe → write → explain → refine" flow |
| Batch 11 (SIEM egress) | None | Enterprise-tier feature |
| Batch 12 (Stripe consumer) | Mono-repo license server with Stripe-driven tier claims (already wired) | Clean tier transitions audited |

**Cross-repo work that does NOT belong in this plan but is on the same critical path** (founder must track separately):

1. `tessera-intelligence` strict-mode `--tarball-hash` enforcement in `sign_pack.py` (TI nextsteps #2) — small, founder-only
2. `tessera-intelligence` catalog auto-regeneration from manifests (TI nextsteps #3) — small, founder
3. `tessera-intelligence` AWS-v1.1.0 + Azure per-op argument assertions (TI nextsteps #4) — Sonnet-side, no urgency
4. `tessera-intelligence` Bedrock cost-cap per-series price table (TI nextsteps #5) — feeds Batch 2 but not blocking
5. `cloudmorph-mono-repo` CloudFront Function `min_tier` table auto-sync (TI nextsteps #13) — small, mono-repo CDK
6. `cloudmorph-mono-repo` Tessera SPA polish (image rebuild + DDB CDK from Batch 1) — operational

---

## 6. Strategic narrowing application

For each candidate batch, explicitly check against IN / OUT scope:

| Batch | IN per narrowing? | Notes |
|-------|-------------------|-------|
| 1 — 0.2.1 close-out | **IN** (SaaS Tessera) | Cannot defer regardless |
| 2 — `cost_for_call` API | **IN** (SaaS Tessera) | Core differentiator |
| 3 — AWS MCP translation | **IN** (SaaS Tessera) | Highest-leverage adoption hook |
| 4 — Observability | **IN** (SaaS Tessera + Console intelligence) | Spans help Console-intelligence too |
| 5 — Examples / adoption | **IN** (SaaS Tessera) | OSS funnel |
| 6 — Benchmarks | **IN** (SaaS Tessera) | Sales / pitch |
| 7 — Azure/GCP blast-radius + Protocols | **IN** (SaaS Tessera) | Producer parity matches consumer parity |
| 8 — OSS policy depth | **IN** (SaaS Tessera) | Loss-leader strategy |
| 9 — Cleanup tail | **IN** (SaaS Tessera) | Enterprise hygiene |
| 10 — LLM authoring depth | **IN** (SaaS Tessera) | Demoable differentiator |
| 11 — SIEM egress sinks | **IN** (SaaS Tessera, enterprise tier) | Enterprise gating feature |
| 12 — Stripe consumer | **IN** (SaaS Tessera) | Closes existing improvement file |

**Deferred indefinitely (OUT)**:

| Item | Reason | Revisit trigger |
|------|--------|-----------------|
| BYOC scheduler polish | Per narrowing OUT | Strategic narrowing revisit |
| Portal API tokens / audit / compliance downloads | Per narrowing OUT | Narrowing revisit |
| OCI BYOC executor / collector breadth | Per narrowing OUT | Narrowing revisit |
| Console forge horizontals (cross-cloud collectors) | Per narrowing OUT | Narrowing revisit |
| Console analytics SaaS-seat detector | Per narrowing OUT | Narrowing revisit |
| Console analytics seasonal forecasting | Per narrowing OUT | Narrowing revisit |
| BYOC playbooks for non-AWS | Per narrowing OUT | Narrowing revisit |
| Multi-region tessera-cloud routing | P2-25; not blocking | First $100k+ enterprise ask |
| Marketing site replatform | Per narrowing OUT | If funding closes |

---

## 7. Quick wins (independent of any batch, < 2 hours each)

Standalone items the founder can knock out between batches:

| Quick win | Effort | Where |
|-----------|--------|-------|
| Update `cloudmorph-tessera/README.md` to reflect 0.2.1 + tri-cloud-content narrative | 1h | `README.md` |
| Add GitHub topic tags to `CloudMorphAI/cloudmorph-tessera` repo: `mcp`, `firewall`, `ai-security`, `cost-control`, `aws`, `azure`, `gcp` | 15min | GitHub UI |
| PyPI page metadata polish — verify keywords + classifiers render | 30min | `pyproject.toml` already has them; verify on pypi.org/project/cloudmorph-tessera |
| Pre-commit hook for `ruff format` + `ruff check` + `mypy tessera/` | 1h | `.pre-commit-config.yaml` |
| Tighten round-trip smoke into CI: TI's `round_trip_smoke.py` runs nightly against live S3 | 1h | `.github/workflows/round-trip-nightly.yml` |
| Cross-repo public-key parity CI check (TI `_metadata/public-key.pem` byte-equal to OSS `tessera/intelligence/public_key.pem`) | 30min | CI script under TI |
| `tessera --version` CLI subcommand verification + smoke | 15min | `tessera/cli.py` |
| Fix `.git/config` line 18 trailing whitespace gotcha in this repo (`sed -i '18s/^[[:space:]]*$//' .git/config`) | 2min | per CLAUDE.md |
| Open a GitHub Discussion for "What MCP servers would you like Tessera to support next?" | 15min | GitHub Discussions |
| Audit `arch/status/` for any v0.2.x release-note prose that escaped the cleanup (re-do D-3 sweep) | 1h | `arch/status/*.md` |
| `arch/improvements/` rotation cleanup — `v0.3.0-stripe-integration.md` should merge in Batch 12 and disappear | 5min after Batch 12 | `arch/improvements/` |

---

## 8. What this plan deliberately doesn't cover

- **BYOC scheduler polish** (per strategic narrowing) — tracked in `cloudmorph-console-containers/arch/nextsteps.md`
- **Portal side** — retired 2026-05-16; remaining settings work goes through `cloudmorph-mono-repo`'s console arch
- **OCI inventory completeness** — Console-forge concern, not Tessera
- **Multi-tenant Cognito Pool 3 admin tooling** — mono-repo concern
- **Console-analytics forecasting** — out of scope per narrowing
- **Console-forge horizontals** — `_shared/collectors/` is a Console-forge concern
- **Marketing site replatform** — defer until funding allows
- **Producer-side test depth** — TI nextsteps owns this
- **Mono-repo CDK improvements beyond `tessera-ratelimits-prod`** — see mono-repo arch
- **Stripe webhook ingestion** — server-side only; mono-repo's job

---

## 9. Reading order for execution

When a future Sonnet 4.6 execution session picks up Batch N, here's the exact reading order:

1. **This file** — `cloudmorph-tessera/plan/tessera-improvements-plan-2026-05-16.md`
2. **The batch's "Scope" subsection** above
3. **Relevant arch/status file**:
   - Batch 1 → `arch/status/packaging-and-release.md`
   - Batch 2 → `arch/status/integrations-and-cost.md`
   - Batch 3 → `arch/status/integrations-and-cost.md` + `arch/status/policy-engine.md`
   - Batch 4 → `arch/status/proxy-enforcement-and-audit.md` + `arch/status/policy-engine.md`
   - Batch 5 → `arch/status/proxy-enforcement-and-audit.md` (Cursor Hooks section)
   - Batch 6 → `arch/status/proxy-enforcement-and-audit.md` (determinism wedge)
   - Batch 7 → `arch/status/integrations-and-cost.md` (AWS blast-radius design) + `arch/status/policy-engine.md`
   - Batch 8 → `arch/status/policy-engine.md`
   - Batch 9 → `arch/status/packaging-and-release.md` (version-sync convention)
   - Batch 10 → `arch/status/llm-policy-authoring.md`
   - Batch 11 → `arch/status/proxy-enforcement-and-audit.md` (audit subsection)
   - Batch 12 → `arch/improvements/v0.3.0-stripe-integration.md` + `arch/status/intelligence-and-licensing.md`
4. **Source files listed in the batch scope**
5. **Cross-repo references**:
   - For `tessera-intelligence` content: `tessera-intelligence/arch/status/<relevant>.md`
   - For `cloudmorph-mono-repo` license + wrapper: `cloudmorph-mono-repo/arch/tessera/status/<relevant>.md`
6. **Then execute**: ruff + mypy clean per batch; one commit per concern (per memory `feedback_commit_size_no_autopush.md`); never `git push` without explicit founder word.

---

## 10. Decisions locked (2026-05-16)

All five sign-off questions answered. These are now part of the spec, not open.

| # | Decision | Implication |
|---|----------|-------------|
| Q1 | Cost API legacy = **fallback through v0.3.x, remove in v0.4.0 with `DeprecationWarning`** (no founder preference → using recommended default) | Tessera Cloud wrapper has a release cycle to migrate. Reversible if anyone complains. CI test for deprecation warning emission. |
| Q2 | AWS MCP routing = **service-specific when `official_mcp_tool_name` is set, else `call_aws`** — configurable per-upstream via `upstream.aws_mcp_routing: specific-first \| call-aws-only` | Honors the reconciliation work in tessera-intelligence. Best of both — clean per-tool when AWS provides it, fallback when they don't. |
| Q3 | OTel = **off by default, flip with `TESSERA_OTEL_ENABLED=1`** | Zero per-call cost for operators not using OTel. Single env var to enable. Matches Prometheus convention. |
| Q4 | Azure + GCP blast-radius = **live-API only** (parallel to AWS) | **Requires Microsoft Graph + Azure RM read perms for Azure; GCP IAM read perms for GCP.** Operators in restricted-IAM environments will fail-closed (over-block) — same shape as how AWS works today. Founder accepts this tradeoff. **Founder owns: documenting the IAM-read perm requirement clearly in `arch/status/integrations-and-cost.md` Azure + GCP sections during Batch 7.** |
| Q5 | `tessera policy explain` = **LLM-only** | Requires one of the 7 LLM provider extras installed. Output quality is meaningfully better than template-based. Operators without LLM extras get a clear error message pointing them at `pip install cloudmorph-tessera[anthropic]` or equivalent. |

### Implications for downstream batches

- **Batch 2**: `_BUILTIN_MAPPING` in `tessera/cost/aws_mapping.py` gets a `# DeprecationWarning: removed in v0.4.0 — use cost_for_call()` comment. Add `warnings.warn(...)` on legacy call. CI test asserts the warning fires.
- **Batch 3**: `tessera/integrations/aws/upstream.py` config schema gains `aws_mcp_routing` field with values `specific-first | call-aws-only`. Default: `specific-first`. Per-upstream translation logic conditionally uses service-specific routing.
- **Batch 4**: `tessera/observability/tracing.py` checks `TESSERA_OTEL_ENABLED` env var before initializing the OTel SDK. If unset/false, all `@trace` decorators no-op. `tracing.is_enabled()` helper for downstream code to skip work.
- **Batch 7**: `blast_radius.mode` config defaults to `live`. No `rules-only` or `hybrid` shipped initially — can add later if a customer asks. Document the IAM-read perm requirements:
  - Azure: `Microsoft.Authorization/*/read` + `Microsoft.Graph/Directory.Read.All`
  - GCP: `resourcemanager.projects.getIamPolicy` + `iam.serviceAccountKeys.list`
- **Batch 10**: `tessera policy explain` raises `MissingLLMExtra` if no LLM provider extras installed, with a `pip install cloudmorph-tessera[anthropic|openai|gemini|bedrock|azure-openai|mistral|cohere]` hint.

---

## 11. Founder summary — one-pager for tomorrow

If you read nothing else, read this:

- **Batch 1 (0.2.1 close-out, 0.5 day)** is the only thing you cannot defer. Do it this week.
- **Batch 2 (cost_for_call, 3 days)** and **Batch 3 (AWS MCP translation, 4 days)** are the next two and they unlock the May 22 Google × Antler pitch lines: "sub-millisecond, signed, tri-cloud cost firewall" and "drops in front of awslabs/mcp/aws-api-mcp-server with zero config."
- **Batches 4–6 (observability + examples + benchmarks)** in Week 4–8 turn Tessera from "feature complete" into "verifiable" — operators see histograms, prospects copy-paste examples, sales has bench numbers.
- **Batches 7–8 (Azure/GCP blast-radius + OSS depth)** in Week 9–11 close the producer-consumer parity gap and deepen the loss leader.
- **Batches 9–12 (cleanup + LLM authoring + SIEM egress + Stripe consumer)** in Week 11–14+ are polish + enterprise gating.

By end of Week 12 (mid-August 2026) you have shipped: **0.2.1, 0.3.0, 0.3.1, 0.4.0, 0.4.1, 0.5.0, 0.5.1** with documented benchmarks, OTel + histograms, tri-cloud blast-radius, AWS MCP integration, 5 wrap-examples, 24 bundled policies, mypy-clean, OAuth `/revoke` + signed `/introspect`, an automated version-bump script. The "Tessera-first" narrowing produces exactly that surface in 12 weeks — no BYOC distraction, no Portal polish, no OCI inventory cleanup, no marketing-site work.

The one thing that's NOT on this plan and might surprise you in 8 weeks: customer-facing documentation site. Today `docs/` is 7 markdown files in the repo. Most successful OSS firewalls (Snyk, HashiCorp Vault) hit a wall at "Markdown in repo" once adoption climbs into hundreds of users. If the Google × Antler funding closes and you can hire a part-time tech writer, a Docusaurus site at `docs.cloudmorph.io/tessera/` is the highest-ROI growth investment. I deliberately left it off the plan because it's a strategic spend, not a technical one — flag it for the Q3 budget conversation.

Good luck. Start Batch 1 Monday.
