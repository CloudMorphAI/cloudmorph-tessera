# Tessera compliance mappings

This directory maps specific obligations from external compliance frameworks
to the Tessera controls that provide evidence toward satisfying them. It is
decision-support data, not legal advice, and not a substitute for a
customer's own legal or compliance counsel.

## What Tessera actually does

Tessera enforces policy deterministically at the MCP tool-call boundary
(`allow` / `block` / `require_approval` / `log_only`), records a
per-decision entry (tool, `policy_id`, reason, timestamp) into a SHA-256
hash-chained, tamper-evident audit log, and can produce an Ed25519-signed,
offline-verifiable export of that log as evidence. There is no ML/AI model
in the enforcement path — every decision is deterministic and reproducible
from the policy file that produced it.

Tessera does not see traffic outside the MCP boundary, does not verify a
caller's intent, and does not provide identity or access management. Every
mapping in this directory is scoped to what the hash-chained log and signed
export can actually demonstrate — nothing more.

## Files

- `mappings/eu-ai-act.yaml` — EU AI Act (Regulation (EU) 2024/1689), obligations applicable 2026-08-02
- `mappings/india-dpdp.yaml` — Digital Personal Data Protection Act, 2023 (India)
- `mappings/soc2.yaml` — SOC 2, AICPA Trust Services Criteria (Common Criteria series)

## Schema

Each mapping file has this shape:

```yaml
framework: "<framework display name>"
version: "<which version/date/applicability window this mapping targets>"

obligations:
  - id: "kebab-case-id"                # stable identifier, used by exports/tooling
    obligation: "<plain-language description of what the obligation requires>"
    source: "<citation, or a plain-language restatement if the exact
              article/section is uncertain>"
    review: FOUNDER                    # OPTIONAL — present only when the
                                        # citation or its applicability needs
                                        # founder/counsel confirmation
    tessera_controls:                  # which Tessera capabilities are being
      - "..."                          # cited as evidence — must be drawn from
      - "..."                          # Tessera's actual capabilities, never
                                        # aspirational ones
    evidenced_by: >
      <which evidence-bundle artifact or policy behavior demonstrates this,
      and — just as important — what it does NOT cover>
```

Field notes:

- **`source`** — every obligation entry carries one. If the exact
  article/section number is known with confidence, it's cited directly. If
  not, the field holds a plain-language restatement of the obligation instead
  of a guessed citation, and the entry is flagged `review: FOUNDER`. There are
  no invented article or section numbers anywhere in this directory.
- **`review: FOUNDER`** — appears only on entries where the citation itself
  (not just the strength of the mapping) needs confirmation before the entry
  is used in front of a customer or auditor. An entry without this flag still
  is not a legal opinion — see "How to use this data" below — but its
  citation has been checked against the framework's primary or official-source
  text.
- **`evidenced_by`** — deliberately states both what Tessera's controls
  demonstrate and what they don't. A mapping that quietly overclaims is worse
  than no mapping at all; several entries in this directory describe partial
  coverage of a broader criterion on purpose.

## How to use this data

- This is a starting point for filling out a compliance questionnaire or
  building an evidence package — not a compliance certification, and not
  legal advice.
- Every `review: FOUNDER` entry must be confirmed by the founder or outside
  counsel before it is shown to a customer, auditor, or regulator.
- Entries without `review: FOUNDER` still describe what Tessera's logs and
  exports can demonstrate, not whether a given customer's overall program
  satisfies the obligation — that depends on everything else the customer
  does outside Tessera's boundary.
- Framework applicability, commencement dates, and interpretive guidance
  change. Re-verify citations periodically rather than treating this
  directory as permanently current — see the `version` field in each file for
  known timing caveats (the India DPDP file in particular notes an unresolved
  commencement-date question).

## Adding a new framework or obligation

Follow the schema above. Do not add a `source` citation you are not certain
of — restate the obligation in plain language and add `review: FOUNDER`
instead. Only map to Tessera capabilities that are actually shipped and true
today; do not describe Tessera as "intelligent" or "AI-powered" anywhere in
this directory — its enforcement path is deterministic policy evaluation, and
that distinction is itself part of what makes the audit log a credible
evidence source.
