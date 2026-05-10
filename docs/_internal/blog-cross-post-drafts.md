# Blog Cross-Post Drafts

Target publish: 2026-05-12 (Tuesday, Tessera launch day)

---

## Post 1: Why Tessera is bootstrapped (and why that matters for an OSS security tool)

**Target:** cloudmorph.ai/blog, Hashnode, Substack
**Word count:** ~720

**By CloudMorph AI**
**Published: 2026-05-12**

---

Tessera launched today as a fully open-source MCP firewall. One of the first questions we expect from developers is: who funded this, and what strings are attached?

The answer is straightforward: no one funded it. Tessera is bootstrapped. There is no venture capital, no angels, no accelerator backing the work. It is self-funded by the founder, whose full-time employment at a separate company covers living costs. That arrangement gives CloudMorph AI the ability to operate without external reporting obligations, and means that any revenue Tessera generates goes back into the product rather than into investor return calculations.

We want to explain why this is a deliberate choice, not a gap in the fundraising story.

### The VC timeline does not fit OSS

The standard venture capital model assumes a company can demonstrate meaningful traction within 12-18 months, and reach a liquidity event within 5-7 years. That timeline works reasonably well for pure SaaS products where the revenue mechanism is clear from day one. It works poorly for open-source businesses.

Open-source projects build trust slowly. Trust is the actual product — the thing that makes developers adopt the tool, contribute to it, and eventually pay for hosted versions of it. Compressing that trust-building phase to match a VC's reporting cadence tends to produce the same set of bad outcomes: feature flags inserted to push users toward paid plans, OSS versions that are deliberately crippled, or licenses that are quietly changed after the community is large enough to feel locked in.

These are not hypothetical risks. They are well-documented patterns across the OSS industry, and they consistently damage the developer communities that made those projects worth funding in the first place.

### What staying bootstrapped costs

Being honest requires acknowledging the real cost: slower progress on everything that requires sustained engineering time.

A well-funded team could ship Tessera Cloud in two months. A bootstrapped effort will take longer. The first 90 days of Tessera's roadmap are public in the handbook — OAuth 2.1, rate limiting, a Postgres audit sink, multi-tenant isolation — and that timeline is realistic for a small team, not an ambitious one. Anyone expecting the pace of a Series A company should adjust their expectations.

The counterargument is that slower, steadier progress is exactly what an OSS security tool needs. Security tools that ship too fast and patch too slowly create the very risks they claim to address. We are comfortable with the tradeoff.

### What this means for the OSS version

Bootstrapped funding means there is no investor pressure to limit the OSS version. There is no board asking why we are giving away features that could be monetized. The Apache 2.0 license on the core engine will not change. The self-hosted version will not acquire a license key requirement, a call-home mechanism, or feature flags tied to a paid tier.

We are writing this down because public commitments are harder to walk back than private intentions. If the funding picture changes — if we take on external investment at some point — this page will be updated and the reasons will be stated plainly. We will not pretend continuity when there has been a change.

### The revenue plan

The plan is simple: Tessera Cloud, the hosted version, charges for infrastructure and operations. The OSS version is free. If the hosted product generates enough revenue to cover development costs, the model is sustainable. If it does not, we will say so rather than quietly pivoting.

This is not an unusual model — it is the same one used by many successful OSS companies. What is unusual is stating the constraints this plainly at launch rather than after the first funding announcement. We think developers deserve to know the business model of the security tools they depend on before they are already deeply integrated.

Tessera is available now at [github.com/cloudmorph-ai/tessera](https://github.com/cloudmorph-ai/tessera). The hosted product is on the 30-60 day roadmap. If you want to be notified when Tessera Cloud launches, there is a waitlist at cloudmorph.ai.

---

## Post 2: How we're pricing Tessera Cloud — and why self-hosted will always be free

**Target:** cloudmorph.ai/blog, Hashnode, Substack
**Word count:** ~680

**By CloudMorph AI**
**Published: 2026-05-12**

---

Tessera is an open-source MCP firewall that ships today. The self-hosted version is fully functional, requires no license, and has no usage limits. The hosted cloud version — Tessera Cloud — is coming in 30-60 days and will have a paid tier above the free level.

This post explains exactly how the pricing works, and more importantly, explains the reasoning behind each decision.

### What "self-hosted is free" actually means

A lot of products use "free" in ways that turn out to have asterisks. Free for personal use only. Free up to X requests per month. Free during the beta. Free with limited features.

Tessera's self-hosted tier is free in the plain sense: you run it, you get the full policy engine, all seven reference policies, custom policy support, the hash-chain audit log, and the CLI. There are no rate limits. There is no license key. There is no expiry. There is nothing locked behind a flag that gets unlocked when you pay.

This is not a generosity decision. It is a strategic one. A security tool that developers do not trust completely is a security tool that does not get adopted. If the OSS version were crippled, the community would not build on it, and there would be no community to convert to cloud customers. The self-hosted version has to be genuinely useful for the business model to work at all.

### The cloud tiers

Tessera Cloud will launch with four tiers, though the exact pricing for each is not yet final. We will publish the numbers publicly before the product launches — there will be no "contact us for pricing" wall except for the Enterprise tier.

**Starter** targets individual developers and small teams who do not want to run infrastructure. The priority is making it easy to get started without an ops burden. Basic audit log access, a dashboard for reviewing policy decisions, and low request limits.

**Pro/Growth** adds longer audit log retention, higher request limits, team management, and compliance-oriented features: structured log export, policy versioning, and change history. These features are relevant to teams that need to demonstrate their AI agent governance posture to auditors or stakeholders.

**Enterprise** is a custom contract. This is the one tier where "contact us" is appropriate, because the requirements genuinely vary — private deployment, SLAs, dedicated support, custom audit log integrations. We are not going to publish a number here and pretend it applies to every enterprise situation.

### What we rejected and why

Three alternative models got serious consideration before we settled on the current structure.

**Freemium with feature gates.** This is the most common model in the OSS-to-SaaS space: give away a version with limited features, and put the useful features behind a paywall. We rejected it because it makes the OSS version into a marketing artifact rather than a real product. A security tool that is only partially functional is not a security tool.

**API key wall.** Requiring a license key even for self-hosted use. This adds friction without adding value — a determined user works around it, and a good-faith user is just annoyed. There is no revenue benefit because the self-hosted user was not going to pay anyway.

**Open core with proprietary extensions.** Common in database tooling. The problem is that the line between OSS and proprietary gets blurry quickly, and users lose track of what they can rely on. We prefer a clean separation: the firewall engine is fully OSS under Apache 2.0, and the managed service is the commercial product.

### What will not change

Three specific commitments that are not conditional on business outcomes:

The Apache 2.0 license on the core engine will not change. Self-hosted users will not be required to register, authenticate, or call home. Security patches will go to the OSS version first — paid tiers do not get early access to fixes for vulnerabilities.

The pricing page in the Tessera handbook will be updated as numbers are finalized. Tessera is available now at [github.com/cloudmorph-ai/tessera](https://github.com/cloudmorph-ai/tessera).

---

## Post 3: Our security disclosure commitment: 72 hours, 90 days, and nothing hidden

**Target:** cloudmorph.ai/blog, Hashnode, Substack
**Word count:** ~750

**By CloudMorph AI**
**Published: 2026-05-12**

---

Tessera is a security tool. It sits between AI agents and the MCP servers they call, evaluating every tool invocation against a policy set and writing the results to a tamper-detectable audit log. If Tessera's own security is weak, it cannot be trusted to enforce anything.

This post explains exactly how we handle security reports — the timeline, the process, and why we made the specific commitments we did.

### The disclosure timeline

We follow coordinated disclosure with the following concrete schedule from the moment a valid report arrives:

**72 hours** — acknowledgement of receipt and initial severity assessment. Not an automated reply. A human will read the report and respond with an initial assessment of severity.

**7 days** — full triage. The vulnerability is either confirmed or rejected. If confirmed, severity is assigned, a fix owner is identified, and we tell the reporter what the expected resolution path looks like.

**90 days** — patch target for confirmed vulnerabilities. This is consistent with the timeline established by Project Zero and widely accepted across the security community.

If we cannot hit 90 days — and occasionally that happens when a fix depends on an upstream library, or the issue is unusually complex — we communicate that to the reporter before the deadline and propose an extension. We do not go silent and we do not make the reporter chase us.

### Why these specific numbers

72 hours for initial acknowledgement is achievable without being trivially easy. It is enough time to read and understand a report, consult internally if needed, and form an honest first impression of severity. A faster commitment risks producing a response that is not actually useful. A slower commitment leaves reporters in the dark too long.

90 days for a patch is the de facto industry standard, and for good reason. It gives the maintainer enough time to understand the issue, design a fix that does not introduce new problems, and test it. It gives the reporter a firm deadline so they know when they can publish their research if no fix is forthcoming. Both sides benefit from the structure.

### How to report

Send reports to security@cloudmorph.ai. Include a description of the vulnerability, steps to reproduce, your severity assessment, and whether you have a proposed fix. Plain-text email is fine. We do not require a specific format.

Tessera's full source is on GitHub under the Apache 2.0 license. There is no private source tree. Researchers can audit the policy engine, the audit chain, the transport layer, and the test suite without requesting access. We consider this a feature: the security of a firewall should be verifiable by anyone, not just asserted by the vendor.

### The vulnerability classes we care most about

Based on Tessera's design, three categories of vulnerabilities get highest-priority treatment:

**Audit chain bypass.** Tessera's hash-chain audit log is a core trust guarantee — every tool call produces an event that is chained to the previous one via SHA-256, making tampering detectable. Any technique that allows a call to execute without producing an audit entry, or that allows entries to be fabricated or modified, directly undermines the product's value. We treat these as critical.

**Authentication bypass.** Any technique that allows a caller to execute tool calls that should be blocked by policy, or to assume a scope they have not been granted. The bearer token model is simple by design — simplicity reduces attack surface — but simple does not mean trivially breakable, and we take authentication integrity seriously.

**Regex denial of service.** The policy engine uses regular expressions for pattern matching. Malicious input that triggers catastrophic backtracking could block the firewall's event loop, effectively disabling enforcement for legitimate traffic. We already mitigate this with a 100ms timeout on all regex evaluation via the `regex` library, but we recognize it as an ongoing area of concern.

### On bug bounties

There is no bug bounty program at v0.1. We cannot make financial commitments we cannot honor, and honesty about that is more useful than a symbolic bounty that implies resources we do not have.

We plan to introduce a structured bug bounty program at v0.4, or when Tessera Cloud reaches a stable paid user base — whichever comes first. The scope and reward structure will be published at that point.

### Disclosure policy

When a confirmed vulnerability is patched: a CVE will be requested for anything scoring CVSS 4.0 or above. The fix will be documented in CHANGELOG.md with a CVE reference where applicable. The reporter will be credited by name unless they request anonymity.

We do not do quiet patches. If something was broken, the changelog will say so and the severity will be stated accurately. A security tool that hides its own vulnerability history is asking you to trust it on faith. We are not interested in that arrangement.

Tessera is available now at [github.com/cloudmorph-ai/tessera](https://github.com/cloudmorph-ai/tessera). The full security commitment is documented in [SECURITY.md](https://github.com/cloudmorph-ai/tessera/blob/main/SECURITY.md) and the [handbook](https://github.com/cloudmorph-ai/tessera/blob/main/handbook/the-security-commitment.md).
