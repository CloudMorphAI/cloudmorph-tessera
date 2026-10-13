# Compatibility evidence record

Status: pending actual named desktop/client/version transcripts.
Owner: [name]  Date: [date]

Evidence category: [offline synthetic protocol / actual client transcript]
Client/desktop name and exact version: [pending; synthetic harness if applicable]
OS/version: [value]  Tessera runtime revision/version: [value]
HTTP MCP transport/configuration and routed boundary: [value]
Server/tool versions and synthetic workflow: [values]
Selected approved policy/content versions: [values]
Trusted key source/fingerprint and validation evidence: [values]

Execution command and timestamp: [actual values]
Expected behavior / observed behavior / result: [fill in after execution]
Transcript or evidence paths: [pending]
Allow/block/error behavior and relevant tool arguments, redacted: [values]
Required context inputs and absent-input behavior observed: [values]
Known limitations / untested cases / reproduction steps: [values]

- [ ] Evidence category is explicit; synthetic checks are not client certification.
- [ ] Actual named client/version transcript captured before compatibility claim.
- [ ] Only routed HTTP MCP traffic included in conclusions.
- [ ] Synthetic data used first; consent/access gates met before live use.
- [ ] Raw argument privacy, redaction, retention and access approved.
- [ ] Selected content schema and integrity verified; known 119 schema rejects
  and absent price artifacts are handled as selected-content gates.
- [ ] Failures and pending checks retained alongside successes.
- [ ] Acceptance criteria, reviewer and decision recorded: [pending].

Planned local preparation command (maintainer verification pending):

```sh
python examples/synthetic_pilot/run.py --output-dir <new-directory>
```

Intended output is loose unsigned JSON/Markdown from local synthetic checks,
without network, model calls or ZIP packaging. Record its actual result after
verification. It cannot establish compatibility with a desktop/client that was
not exercised, independent review, signed attestation or live cloud readiness.

Permitted claim based on this evidence: [narrow statement / none yet]
Next required transcript/check and owner: [fill in]
