# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in CloudMorph Control Centre, **please do not file a public GitHub issue.**

Instead, email **security@cloudmorph.io** with:
- A description of the issue
- Steps to reproduce (or proof-of-concept)
- The version / commit SHA where you observed it
- Your name (for credit, optional)

## Our commitment

- We will acknowledge receipt within **72 hours** (per GDPR breach-notification SLA where applicable).
- We will keep you updated on our investigation.
- We will publicly disclose the issue **only after a fix is available**, with credit to you (unless you prefer to remain anonymous).
- We support coordinated disclosure with industry partners.

## Scope

In scope:
- The MCP server (`cloudmorph-mcp/`)
- The Python SDK (`sdk-python/`)
- The five executors (`aws/`, `azure/`, `gcp/`, `databricks/`, `snowflake/`)
- The hosted SaaS at `mcp.cloudmorph.io` (when live)
- The bundle distribution at `bundles.cloudmorph.io` (when live)

Out of scope:
- Vulnerabilities in upstream dependencies (report to those projects); we'll patch as fixes land.
- Compromise of customer-supplied cloud credentials (an IAM problem, not ours).
- Issues in the customer's policy bundle (they author it; we sandbox via OPA WASM).
- Social-engineering attacks on CloudMorph staff.

## What's especially welcome

- Cross-tenant leakage (decision cache, audit log, session store).
- Audit hash chain bypass.
- Policy bundle signature forgery.
- Intent spoofing techniques our matcher misses.
- Authn/authz bypass.
- TOCTOU in approval flows.
- Container escape from the executor sandbox (expect the bar here is low; report regardless).

We track an [adversarial test fixture suite](status/cross/08_tests_audit.md) — we'd love to learn from anything that's not yet captured.

## Hall of fame

Will be added once we have first reports. We intend to credit researchers prominently.

## PGP

```
Email:    security@cloudmorph.io
Key:      (publish PGP key on first stable release; until then, encrypted email via age or s/mime on request)
```

## Coordinated disclosure timeline

- Day 0: Report received → acknowledged within 72h.
- Day 0–14: Investigation + fix development.
- Day 14–30: Fix released; CVE assigned if applicable.
- Day 30+: Public disclosure with credit (or sooner if mutually agreed).

For active exploitation, we may compress the timeline aggressively.
