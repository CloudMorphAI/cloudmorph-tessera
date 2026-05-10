# The Security Commitment

Tessera is a security tool. If its own security posture is weak, it cannot be trusted to
enforce policies on anything else. This page describes what we commit to and what researchers
can expect when they engage with us.

## Disclosure timeline

We follow a 90-day coordinated disclosure policy, consistent with industry norms established
by Project Zero and others.

The timeline from a valid report:

- **72 hours** — acknowledgement of receipt and initial severity assessment
- **7 days** — full triage: confirmed or rejected, severity assigned, fix ownership assigned
- **90 days** — patch target for confirmed vulnerabilities

If we cannot hit the 90-day patch target for a legitimate reason (unusual complexity,
dependency on an upstream fix), we will communicate that to the reporter before the deadline
and propose an extension. We will not go silent.

## How to report

Send findings to **security@cloudmorph.ai**.

Include:
- A description of the vulnerability
- Steps to reproduce
- Your assessment of severity and impact
- Whether you have a proposed fix

We do not require a specific format. A clear plain-text email is fine.

## What OSS means for security research

Tessera's full source is on GitHub under the Apache 2.0 license. There is nothing hidden.
Researchers can audit the policy engine, the audit chain implementation, the transport
layer, and the test suite without asking for access.

This is a feature. We think the security of a tool like Tessera should be verifiable by
anyone, not claimed by the vendor.

## Areas we care most about

Based on Tessera's threat model, the vulnerability classes we treat as highest priority:

**Audit chain bypass.** Any technique that allows a tool call to be executed without
producing an audit log entry, or that allows an audit log entry to be fabricated or
tampered with. The audit chain is a core trust guarantee.

**Authentication bypass.** Any technique that allows a caller to execute tool calls that
should be blocked by policy, or to assume a different tenant identity.

**Regex denial of service.** Tessera's policy engine uses regular expressions for pattern
matching. Malicious input that causes catastrophic backtracking and blocks the firewall
could be used to disable enforcement entirely. We take ReDoS seriously.

## Bug bounty

There is no bug bounty program at v0.1. We cannot make financial commitments we cannot
honor.

We plan to introduce a bug bounty program at v0.4 or when Tessera Cloud reaches a stable
paid user base, whichever comes first. When we do, the scope and reward structure will be
published here.

## Disclosure policy

When a valid vulnerability is confirmed and patched:

- A CVE will be requested if the severity warrants it (CVSS 4.0 or above).
- The fix will be documented in CHANGELOG.md with a CVE reference where applicable.
- The reporter will be credited by name unless they request anonymity.

We do not do quiet patches. If something was broken, the changelog will say so, and the
severity will be stated accurately.
