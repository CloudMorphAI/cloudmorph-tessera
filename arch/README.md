# cloudmorph-tessera/arch/

Architecture docs for the OSS `tessera` package: deterministic MCP firewall, consumer side of the intelligence trust chain, packaging.

## Structure

- **`status/*.md`** — architecture-design docs. How each subsystem is designed and intended to work.
- **`improvements/*.md`** — version-scoped planned work. Named `<version>-<short-description>.md`.

## Combined cap: 16 files

Hard ceiling across `README.md` + `status/` + `improvements/`. New work fits by merging, not adding. Recommended cuts under pressure: fold `intelligence-and-licensing` into `proxy-enforcement-and-audit` once the OAuth surface stabilizes; fold `llm-policy-authoring` into `policy-engine` if the LLM authoring subsystem stops growing.

## Status docs are design, not snapshots

Status files describe architecture: invariants, contracts, rationale, trade-offs. They do **not** contain "verified as of" timestamps, test counters, version markers, or release-note prose. Snapshot content belongs in commit messages and `CHANGELOG.md`. A status file reads identically today and six months from now (assuming the design hasn't changed).

## Lifecycle is merge-not-move

When an improvement ships, its content **merges into** the relevant `status/` doc and the improvement file is **deleted**. Never moved. This keeps the file count flat: improvements close by absorbing into the design surface they extended.

Follow-on work for a later version is a **new** improvement file. The original is not extended past its committed version.

## Improvements are version-scoped

Each file describes work to ship one specific version (v0.2.1, v0.3.0, v0.4.0). When that version ships, content folds into one or more `status/` docs (see each file's "On merge" section) and the improvement file is deleted.

## Plan-M task files live outside arch/

`arch/` contains architecture docs only. Per-task implementation plans (the founder's plan-M workflow) live in a separate folder. Do not introduce task files inside `arch/`.

## Scope

This directory covers **cloudmorph-tessera content only** — the OSS firewall, the consumer side of the trust chain, packaging. Cross-repo concerns are noted at boundaries:

- **Producer side** of the trust chain lives in `tessera-intelligence/arch/`. The byte-for-byte coupling of `tessera/intelligence/public_key.pem` with `tessera-intelligence/_metadata/public-key.pem` is the load-bearing cross-repo invariant.
- **License server** (JWT issuance, Stripe-driven tier claims, revocation) lives in `cloudmorph-mono-repo`. Out of scope here.
- **Fargate deployment** of this image (CDK, IAM, secrets) lives in `cloudmorph-mono-repo`'s CDK. Out of scope here.

## Navigation

- Hot path: `status/proxy-enforcement-and-audit.md`.
- YAML → decisions: `status/policy-engine.md`.
- Signed content + license: `status/intelligence-and-licensing.md`.
- AWS + cost: `status/integrations-and-cost.md`.
- LLM authoring: `status/llm-policy-authoring.md`.
- Distribution + release: `status/packaging-and-release.md`.

Read `status/overview.md` first if this is a new visit.
