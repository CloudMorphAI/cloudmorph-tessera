# Pilot preparation

Status: preparation only. No partner identified, independent review completed,
grant awarded, or client compatibility established by these templates.

Start with synthetic workflows on routed HTTP MCP. Scope is the tool traffic
explicitly routed through Tessera; define the route before interpreting results.
Use fictional accounts, payments, files and identifiers. Obtain consent and
approve access, retention and acceptance criteria before any live partner use.

Preparation records:

- [Partner brief](PARTNER_PILOT_BRIEF.md): scope, consent, access and acceptance.
- [Compatibility](COMPATIBILITY_RECORD.md): client/version evidence and limits.
- [External review](EXTERNAL_REVIEW_SCOPE.md): independent scope and case target.
- [Procurement](PROCUREMENT_RECORD.md): supplier quotes and approval evidence.
- [Milestones](MILESTONE_RECORD.md): proposed six-month outputs and capped budget.

The local evidence command is:

```sh
python examples/synthetic_pilot/run.py --output-dir <new-directory>
```

The maintainer verified the command against this staged checkout on 13 October 2026;
15 synthetic acceptance checks passed. The runner uses no network or model and
produces loose, unsigned records, without a ZIP. Its results are local evidence, not a desktop
client transcript, independent assessment, signed attestation or live deployment.
Record the actual command, version, output paths and result after running it.

See [runner scope and checkpoint files](../../examples/synthetic_pilot/README.md)
and [Streamable HTTP support](../STREAMABLE_HTTP.md). Actual named-client
transcripts and external pilot acceptance remain pending.

Content selection is a gate. Current local reconciliation found 119 archived
policies rejected by the runtime schema and missing advertised price artifacts.
Select and explicitly approve packs/policies for the pilot, validate their
schema and required inputs, and record integrity evidence. A valid signature
does not establish runtime support, current prices, or universal readiness.

Preparation owner: [name]  Date: [date]  Runtime revision/version: [value]

- [ ] Synthetic scope and selected content approved.
- [ ] Trusted public key and expected fingerprint recorded.
- [ ] Raw argument handling, redaction, access and retention approved.
- [ ] Compatibility claims limited to collected evidence.
- [ ] Review, supplier and partner approvals linked; pending items explicit.

Remaining blockers and next owner/action: [fill in]
