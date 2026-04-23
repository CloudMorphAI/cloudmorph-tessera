# 12 — Strategic Open Questions

_Founder calls needed BEFORE the corresponding workstream proceeds. Each has a recommended call and the architectural cost of getting it wrong._

---

## Q1. Open-source the MCP server?

**Recommended call: YES, Apache 2.0.** Server OSS, hosted tier proprietary.

**Why:**
- The wedge is *distribution*. Every other MCP firewall going forward will compare itself to ours; being the canonical OSS one wins the comparison.
- Open-sourcing the server doesn't open-source the value — managed bundles, hosted SaaS, audit retention, SSO, compliance reports, replay testing, customer-owned sinks all sit in the proprietary tier.
- Standard playbook (Vercel/Next.js, Grafana/Cloud, Supabase/Cloud, HashiCorp/Cloud).
- Apache 2.0 over MIT — explicit patent grant matters for security buyers.

**Cost of getting it wrong:**
- *No OSS:* every customer build-vs-buy decision compares "proprietary thing we have to evaluate from scratch" vs OPA-native alternatives. We lose the close.
- *Wrong license (GPL/AGPL):* enterprise legal teams reject; we lose the close.

**Block A decision (day 0):** lock OSS. Add LICENSE = Apache-2.0 (already in the worktree, just commit). Public repo announcement on day 14 ship.

---

## Q2. Pricing model

**Recommended call: per-decision with volume tiers.**

Tier sketch (revisit with first 3 partners):

| Tier | Price | Decisions/mo | Audit retention | SSO | Self-host | SLA |
|---|---:|---:|---|:-:|:-:|---|
| Free | $0 | 3,000 (~100/day) | 7 days | no | no | community |
| Pro | $50/mo | 30,000 (~1k/day) | 30 days | no | no | 99% |
| Team | $500/mo | 300,000 (~10k/day) | 90 days | yes | no | 99.9% |
| Enterprise | $2k+/mo | 3M+ (~100k/day) | 1y+ | yes | yes | 99.9%+ |

**Why per-decision:**
- Meters most directly to value (each decision is "one moment we kept your agent in line").
- Aligns with cost-of-goods (every decision is one OPA eval + one audit-write).
- Caps free-tier abuse cost at ~$5/mo.
- Familiar pricing model from Auth0 / LaunchDarkly / Snyk.

**Alternatives rejected:**
- *Per-agent:* unclear definition (is one Anthropic API call one agent? one assistant? one process?); customer disputes inevitable.
- *Per-seat:* doesn't scale for an API product; misaligns with usage.
- *Flat tiers only:* leaves free tier abuse exposed; doesn't give expansion revenue path.

**Cost of getting it wrong:**
- *Per-agent:* customer disputes; high-volume customers pay too little, low-volume too much.
- *Per-seat:* leaves money on the table from heavy automated workloads.

**Block A decision:** lock per-decision; revise tiers after design partners.

---

## Q3. First design partner profile

**Recommended call: scrappy agent startup (with one anchor "compliance enthusiast" from the founder's network).**

**Why startup-first:**
- Faster integration (1-2 weeks not 1-2 quarters).
- Tighter feedback loop (founder DM, not procurement form).
- Forgiving of MVP rough edges in exchange for partnership.
- Tests product-market-fit on the differentiator (intent capture, MCP-proxy) without procurement gatekeeping.

**Why one compliance anchor:**
- Ensures we don't over-rotate to startup ergonomics and lose the enterprise story.
- Forces us to keep audit hash chain, customer-owned sinks, SOC 2 path on the roadmap.
- Provides a sales-enabling reference once SOC 2 is in flight.

**Profile of ideal startup partner:**
- Building a code/devops/data agent (high-stakes tool calls, will appreciate firewall).
- Series A/B (has paid usage of LLM APIs, can spare engineering time).
- Founder/CTO active in MCP community (Discord/X, willing to publicly ref-sell).

**Profile of ideal compliance anchor:**
- Mid-market financial services or healthcare with active AI initiative.
- VP Eng / CTO with security mandate; budget for "AI governance" line item.
- Already deployed Datadog or Snyk; pattern-matches to our story.

**Cost of getting it wrong:**
- *Enterprise-only:* 6-month sales cycles; MVP feedback loop dies; we run out of runway.
- *Startup-only:* product narrows to startup ergonomics; we never close the SOC 2 anchor; market feels like a tools company not a security company.

**Block A decision:** identify both partners by day 7. Day-13 demo is for the startup partner. Compliance-anchor demo by day 30.

---

## Q4. Relationship to CloudMorph Console

**Recommended call: bundled-tier model.** Console is the management UI / billing surface; Control Centre is the runtime data plane. One tenant, one bill, two products.

**Why:**
- Console already has billing, accounts, integration tokens, permission packs ([docs/getting-started.md](../../docs/getting-started.md) references all of these).
- Building a separate billing/auth stack for Control Centre duplicates work and confuses customers ("which login is which?").
- Bundled tier ("Console + Firewall") is a clean cross-sell story: existing Console customers can flip on Control Centre with a setting.

**Architecture implications:**
- MCP server token resolver calls Console's `/v1/auth/verify` to map `cm_<token>` → `(tenantId, scopes, planLimits, policyBundleId)`.
- Console UI hosts the bundle authoring + decision dashboard.
- Console DB is the source of truth for tenants; MCP server has no tenant table.
- Audit events flow to customer-owned sinks (S3) AND to Console for in-app dashboard view.

**Standalone-firewall option (rejected for MVP):**
- Self-hosted Control Centre without Console: requires us to build a slimmed token-resolver / static config flow. Add post-MVP.
- "MCP Firewall" as a separate brand without Console: dilutes go-to-market; brand confusion.

**Cost of getting it wrong:**
- *Separate products:* duplicate billing, duplicate auth, customer confusion.
- *Console-feature-only:* loses the standalone-firewall demo for MCP-savvy buyers who don't want a Console.

**Block A decision:** bundled-tier model. MCP server's `TokenResolver` calls Console's auth endpoint (already exists per the existing executor envvar `CONTROL_CENTER_API_URL`). Block I deploys both linked.

---

## Q5. Rego vs Cedar vs hand-rolled DSL

**Locked at OPA WASM (Rego)** — see [policy/05_policy_engine_design.md §1.1](../policy/05_policy_engine_design.md).

**Revisit conditions:**
- After 3 real bundles authored by customers, evaluate Rego ergonomics in 1:1s.
- If two of three say "Rego is hard to author / debug" — pilot Cedar in a `CedarEngine` implementation behind the same `Engine` interface.
- If we ever need static analysis of bundles (e.g., "this rule will never fire") — Cedar's typed nature wins.

**Cost of getting it wrong:**
- *Picking a proprietary DSL:* every security review questions the language; we lose deals.
- *Picking too cute (Cedar early):* less ecosystem, no off-the-shelf editors, customer security teams haven't seen it.

**No Block A decision needed** — Block E proceeds with OPA WASM.

---

## Q6. Intent definition — free-form, structured, hybrid

**Locked at hybrid** — see [intent/06_intent_system_design.md §1.2](../intent/06_intent_system_design.md).

**Revisit conditions:**
- After first 3 design partners, measure: do agents declare meaningful structured verbs? Or do they all default to `["read.list"]` to game the matcher?
- If agents game the matcher — tighten verb mapping enforcement; add LLM judge to verify structured verbs match the goal.

---

## Q7. Move executors to `cloudmorph-console-containers`?

**Recommended call: YES, after `cloudmorph-common-py` extraction lands.**

**Why:**
- Executors are runtime data plane; this repo is the firewall (MCP + SDK + contracts + policy + intent).
- Splitting reduces this repo's surface from 9k LoC to ~3k LoC (MCP + contracts + SDK + policy + audit). Easier to comprehend, easier to OSS.
- The Console product already owns ECS deployment of executors per the existing token model.

**Sequencing:**
1. Block C: extract `cloudmorph-common-py`. **Don't** move executors yet.
2. Post-MVP day 30: copy executors to `cloudmorph-console-containers` repo with full git history (`git filter-repo`).
3. Post-MVP day 35: delete from this repo; this repo becomes the OSS firewall.

**Cost of getting it wrong:**
- *Move too early (before common-py):* executors-in-other-repo can't depend on common; duplication multiplies.
- *Never move:* this repo stays muddled; OSS pitch ("CloudMorph is the MCP firewall") is undermined by the executor sprawl.

**Block A decision:** plan the move; don't execute yet. Block J (post-MVP) executes.

---

## Q8. Self-hosted vs hosted-first

**Recommended call: hosted-first; self-hosted artifacts (Helm/Terraform) when the first compliance-heavy customer demands it.**

**Why:**
- Hosted shortens the time-to-first-decision from "hours" (self-hosted setup) to "minutes" (cm.cloudmorph.io signup).
- Hosted means we control the deploy; bug fixes ship same-day; no customer "we're stuck on v0.3.1".
- Compliance-heavy customers will ask for self-hosted; we have until then to build it.

**MVP target:** `mcp.cloudmorph.io` running production by day 13. Self-hosted artifacts by day 60.

**Cost of getting it wrong:**
- *Self-hosted-only:* no integration in 14 days; no design-partner demo possible.
- *Hosted-only forever:* loses the compliance-heavy market; eventually has to pivot.

**Block A decision:** hosted-first locked. Helm/Terraform deferred.

---

## Q9. MCP spec adherence — migrate to `@modelcontextprotocol/sdk` or keep hand-rolled?

**Recommended call: migrate.**

**Why:**
- MCP spec evolves; spec drift on a hand-rolled JSON-RPC implementation is a ticking time bomb.
- Stdio transport requires the SDK anyway (or a re-implementation of stdio framing — wasteful).
- Customer client teams using `@modelcontextprotocol/sdk` get cleaner integration if our server speaks the same dialect.
- Future MCP features (resources, prompts, sampling) are free with the SDK.

**Migration cost:**
- ~8h to swap the transport layer.
- Tool registry migrates to SDK's `Server.setRequestHandler(ListToolsRequestSchema, ...)` pattern.
- Net LoC after migration: ~30% smaller (`routes.ts` shrinks substantially).

**Cost of getting it wrong:**
- *Don't migrate:* spec drift kills us in a year.
- *Migrate later:* every additional tool added in `routes.ts` is rework when we eventually do.

**Block A decision:** lock SDK migration as Block D first task.

---

## Q10. AI-native or AI-agnostic?

(New question, not in the scoping prompt — flag.)

**Recommended call: AI-native primary, AI-agnostic possible.**

**Why:**
- The product's pitch ("intent layer for agents") is AI-native by definition.
- LLM judge in the matcher is AI-native by design.
- BUT — the policy engine, audit chain, executors are AI-agnostic. They could govern any tool-calling system (RPA bots, batch jobs, custom workflow engines).

**Marketing implication:** lead with "AI agent governance"; mention "any tool-calling system" only when challenged. Don't muddy the wedge.

**Cost of getting it wrong:**
- *AI-agnostic-first:* dilutes positioning; no clear category leader.
- *AI-only-forever:* misses post-MVP expansion to RPA / workflow markets.

**No Block A decision** — naming and positioning are post-MVP marketing.

---

## Q11. Telemetry data sharing back to CloudMorph?

**Recommended call: opt-in; default OFF for hosted; never for self-hosted.**

**Why:**
- Anonymized decision metrics could fuel a benchmarks dashboard ("90th percentile tenant denies 12% of agent calls"), valuable for marketing.
- BUT: zero-trust is the brand; opt-in keeps it.
- Aligns with HashiCorp / Grafana playbooks.

**Opt-in mechanism:** explicit setting in tenant config, prominent in onboarding.

**No Block A decision** — implement opt-out post-MVP.

---

## Summary of locks (Block A day-0 decisions)

| # | Question | Lock | Block trigger |
|---|---|---|---|
| 1 | Open-source MCP server | YES, Apache 2.0 | A |
| 2 | Pricing model | per-decision with volume tiers | A |
| 3 | First design partner profile | scrappy startup + 1 compliance anchor | A |
| 4 | Console relationship | bundled-tier | A (auth integration) |
| 5 | Rego vs Cedar vs DSL | OPA WASM (Rego) | E (locked) |
| 6 | Intent definition | hybrid (verbs + free-form) | E (locked) |
| 7 | Move executors | yes, post-MVP | A planning, J execution |
| 8 | Self-hosted vs hosted | hosted-first | I |
| 9 | MCP spec — SDK migrate | yes | D first task |
| 10 | AI-native or agnostic | AI-native primary | post-MVP marketing |
| 11 | Telemetry sharing | opt-in, default OFF | post-MVP |

Decisions 1, 5, 6, 9 are unblocking work that happens THIS WEEK. Decisions 2, 3, 4, 8 unblock work in week 2. Decisions 7, 10, 11 are post-MVP.

---

## Source-link references

- [policy/05_policy_engine_design.md](../policy/05_policy_engine_design.md)
- [intent/06_intent_system_design.md](../intent/06_intent_system_design.md)
- [docs/getting-started.md](../../docs/getting-started.md) (Console references)
- [BUILD_PLAN.md Block A](../BUILD_PLAN.md)
