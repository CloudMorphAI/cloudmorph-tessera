# The 90-Day Roadmap

This is what we plan to build over the first 90 days after the v0.1 public launch on
2026-05-10. It is directional, not a contract. We will update this page as we learn.

## Days 0-30: Foundation

The immediate priority is making Tessera genuinely useful as a self-hosted tool — not just
a proof of concept, but something an engineer can drop into a real MCP deployment and trust.

**Cursor Hooks integration.** Tessera already supports stdio and HTTP transports for
intercepting MCP tool calls. We plan to ship first-class integration with Cursor's hooks
mechanism so that developers using Cursor get policy enforcement without any extra
infrastructure.

**Policy library expansion.** v0.1 ships with 7 built-in policies covering the most
common security concerns (path traversal, secret patterns, shell injection, and others). We
plan to expand this to 14 policies by end of Day 30. The additions will focus on
LLM-specific attack patterns that are not yet covered: prompt injection in tool outputs,
exfiltration via benign-looking tool parameters, and token budget abuse.

**Security baseline.** We plan to publish results from a first-pass static analysis and
manual review of the v0.1 codebase. Any findings above informational severity will be
resolved before the Day 30 milestone. This is about building the habit of shipping a clean
baseline, not about claiming the software is complete.

**Three integration recipes.** Tessera's `recipes/` directory will include end-to-end
configuration examples for three common deployment patterns: Cursor + local MCP server,
Claude Desktop + remote MCP server, and a containerized deployment behind a reverse proxy.

**Public handbook.** This handbook is part of the Day 0-30 work. Transparency about how we
build and what we believe is a feature, not a side project.

## Days 30-60: Tessera Cloud

Once the OSS foundation is stable, we plan to move into the hosted product phase.

**What Tessera Cloud is.** Tessera Cloud is a managed version of the firewall — same
engine, same policy library, but without the need to run infrastructure yourself. The target
customer is a small team or individual developer who wants policy enforcement without
managing a server.

**Design partner demos.** Before building the full cloud product, we plan to demo the
concept with a small number of teams and collect structured feedback. We do not know yet
exactly what managed Tessera needs to look like from an operations standpoint. The design
partner process is how we find out.

**First cloud-account integration.** The Day 30-60 milestone includes completing at least
one end-to-end cloud account integration for Tessera Cloud — meaning a user can sign up,
connect their MCP deployment, and have policies enforced without touching the server
themselves. We will be honest about what we do not have ready at that point: multi-tenant
isolation, audit log export, and compliance-oriented features are not in scope for this
milestone.

**What we do not know yet.** We do not know what the right infrastructure architecture for
Tessera Cloud is at scale. We have a working design for small-scale deployment, but we have
not validated it under realistic multi-tenant load. We will learn that during Days 30-60.

## Days 60-90: Paid Readiness

By Day 90 we plan to be ready to take a first paid contract. That means:

**Audit log export.** Policy enforcement without audit logs is not useful for any
compliance-sensitive organization. We plan to ship structured audit log export in a format
that can feed into existing SIEM tooling. The exact format is TBD — we want design partner
input before we commit to a schema.

**Stripe integration.** Tessera Cloud will charge for usage above the free tier. We plan to
have Stripe billing integrated and tested by Day 90. Self-hosted users are not affected —
the OSS version has no billing component.

**Operational stability.** Before we take money from customers, we need to be confident in
the uptime and incident response posture of Tessera Cloud. We plan to run a structured
pre-launch review against our own security commitment (see
[the-security-commitment.md](the-security-commitment.md)) and resolve any gaps.

## What this roadmap is not

This is not a product specification. Individual features may change, be deferred, or be
replaced with something better. The milestone structure reflects our current best
understanding of the right order of operations — OSS credibility first, then hosted product,
then paid contracts.

We will update this page when the plan changes materially. We do not expect it to stay
static.
