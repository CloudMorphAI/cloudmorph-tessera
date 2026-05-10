# The Pricing

## The core principle

Self-hosted Tessera will always be fully functional and free. No license key, no feature
flags, no expiry. If you want to run Tessera yourself, nothing is withheld.

This is not a freemium model where the useful features live behind a paywall. It is a
genuine OSS project where the self-hosted version is the real product.

## Tiers

### Free — Self-Hosted

- Full policy engine, all built-in policies, custom policy support
- No license required
- No usage limits
- Community support via GitHub Issues

This tier exists because we believe the MCP security tooling ecosystem needs good OSS
infrastructure, and that good OSS infrastructure does not get built if the maintainer is
forced to hobble it to protect revenue.

### Starter — Tessera Cloud

- Hosted version of the firewall, managed by CloudMorph
- Aimed at individual developers and small teams who do not want to run infrastructure
- Pricing TBD at cloud launch (expected Day 30-60 milestone)
- Includes basic audit log access and a dashboard for reviewing policy decisions

The pricing for Starter will be posted publicly before the cloud product launches. There
will be no "contact us for pricing" for this tier.

### Pro / Growth — Tessera Cloud

- Higher request limits, longer audit log retention, team management features
- Compliance-oriented features: structured log export, policy versioning, change history
- Pricing TBD at cloud launch

### Enterprise

- Custom contract, SLAs, dedicated support, private deployment options
- Pricing by negotiation — this is the one tier where "contact us" is appropriate, because
  the requirements genuinely vary

## Why OSS plus paid cloud, not a different model

The alternatives we considered and why we rejected them:

**Freemium with feature gates.** Withholding features from the OSS version to push users to
the cloud product. We rejected this because it poisons the OSS community relationship. If
the self-hosted version is not genuinely useful, there is no community to build on.

**API key wall.** Requiring a license or API key even for self-hosted use. We rejected this
because it adds friction for zero revenue benefit — a determined user will work around it,
and a good-faith user is just annoyed.

**Open core with proprietary extensions.** Common model, but it blurs the line between what
is OSS and what is not. We prefer a clean split: the firewall engine is fully OSS, the
managed service is the paid product.

The cloud product charges for three things: infrastructure, uptime, and the labor of
operating the service at scale. Those are legitimate things to charge for. The software
itself is not.

## What will not change

- The Apache 2.0 license on the core engine will not change.
- Self-hosted users will not be required to register, authenticate, or call home.
- Paid tiers will not receive early access to features that are relevant to self-hosted
  security. Security patches go to the OSS version first.
