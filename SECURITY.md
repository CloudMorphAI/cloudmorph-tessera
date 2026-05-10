# Security Policy

Tessera follows a **90-day coordinated disclosure** policy derived from the
[OSSF vulnerability disclosure guide](https://github.com/ossf/oss-vulnerability-guide/blob/main/templates/).

## Reporting a vulnerability

If you discover a security vulnerability in Tessera, **please do not file a public GitHub issue.**

Instead, email **security@cloudmorph.ai** with:

- A description of the issue
- Steps to reproduce (or a proof-of-concept)
- The Tessera version or commit SHA where you observed it
- Your name and a contact address (optional — for credit and follow-up)

We acknowledge all reports within **72 hours** of receipt.

## Contact and PGP

```
Email:  security@cloudmorph.ai
PGP:    (key block placeholder — full key at https://cloudmorph.ai/.well-known/pgp)

-----BEGIN PGP PUBLIC KEY BLOCK-----
[PGP key will be published at launch]
-----END PGP PUBLIC KEY BLOCK-----
```

If you cannot use PGP, encrypted email via S/MIME or age is available on request.

## Disclosure timeline

| Day | Event |
|---|---|
| 0 | Report received |
| ≤ 3 | Initial acknowledgement within 72 hours |
| ≤ 7 | Triage complete; severity assigned; reporter notified |
| ≤ 90 | Fix released and coordinated public disclosure |

We will keep you informed throughout the investigation. If a fix requires longer than the
90-day window, we will communicate the revised timeline before day 90 with your agreement.
For active exploitation in the wild we may compress the timeline with your consent.

We will credit you in the release notes and CHANGELOG unless you prefer to remain anonymous.

## Bug bounty

There is **no public bug-bounty programme** at this stage of the project. We rely on the
goodwill of the security community and commit to full public credit for all accepted reports.
A paid programme may follow in a later release (v0.4+).

## Safe harbor

We consider security research conducted under this policy to be authorised. We will not pursue
legal action against researchers who:

- Report findings through the private channel above rather than publicly.
- Avoid accessing, modifying, or deleting data belonging to others.
- Do not degrade the availability of Tessera or any system that depends on it.
- Limit testing to their own Tessera deployments or a dedicated test environment.

## Scope

### In scope (Tessera is responsible)

| Surface | Examples |
|---|---|
| The proxy (`tessera/proxy.py`) | Auth bypass; injection via malformed JSON-RPC; forwarding to the wrong upstream |
| The policy engine (`tessera/policy/`) | Logic flaws that produce incorrect allow or block decisions regardless of policy content |
| The audit chain (`tessera/audit/`) | Chain bypass; cross-scope event leakage; hash collision acceptance |
| The OSS Docker image | Container breakout; privilege escalation via uid 10001; sensitive data baked into the image |

### Out of scope (not Tessera's responsibility)

| Surface | Reason |
|---|---|
| User-authored policies | Their logic is the operator's responsibility; Tessera executes what is written |
| Upstream MCP servers | They sit behind Tessera and are not operated by us |
| Customer-supplied bearer tokens | Token strength, rotation schedule, and secret storage are the operator's responsibility |
| Dependencies with their own CVE programmes | Report to the dependency maintainer directly; we patch as fixes land |
| Social engineering of CloudMorph staff | Out of scope for this programme |

## Especially welcome reports

We are particularly interested in:

- **Cross-scope audit log leakage** — one token reading or polluting another token scope's audit events via `iter_events`, `audit verify`, or the hash chain.
- **Audit chain bypass** — writing events to the SQLite sink without correctly updating `prev_event_hash`, or accepting a chain event that fails hash verification.
- **Auth bypass** — reaching a `/mcp/*` endpoint without a valid bearer token, or bypassing `secrets.compare_digest` timing protections.
- **Regex DoS bypassing the load-time corpus test** — a pattern accepted by `regex_safety.py` at startup that still causes the 100 ms runtime timeout to fire reliably in production traffic.
- **Policy logic flaws** — conditions in `tessera/policy/conditions.py` that always return `true` or always return `false` regardless of input, or that evaluate against the wrong field.

## Hall of fame

Will be populated once we receive first reports. We intend to credit researchers prominently
here and in release notes.
