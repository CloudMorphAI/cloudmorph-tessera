# Contributing to CloudMorph Control Centre

Thanks for your interest. This is an OSS-first project (Apache 2.0) under active MVP development — see [`status/BUILD_PLAN.md`](status/BUILD_PLAN.md) for the 14-day plan.

## Before you start

- For non-trivial changes, **open an issue first** describing the problem and your proposed approach. We may already be working on it.
- For tiny fixes (typos, obvious bugs), a PR with a clear commit message is fine.
- All commits must be signed off (DCO) or PRs include CLA acceptance.

## Development setup

```bash
git clone https://github.com/CloudMorphAI/cloudmorph-control-center
cd cloudmorph-control-center
pip install pre-commit && pre-commit install
make contracts        # generate Pydantic + TS types from contracts/*.schema.json
make lint             # ruff + mypy + eslint + tsc --noEmit
make test             # python + node test suites
```

## Commit conventions

```
area(scope): subject (under 70 chars)

Optional body explaining the why, references to issues / status docs.
```

`area` is one of: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `build`, `ci`, `perf`, `style`. `scope` is one of: `mcp`, `sdk`, `contracts`, `common`, `aws`, `azure`, `gcp`, `databricks`, `snowflake`, `policy`, `intent`, `audit`, `repo`, `docker`, `release`.

Examples:
- `feat(mcp): add cloudmorph_declare_intent tool`
- `fix(sdk): map JSON-RPC error.code instead of error.message string`
- `refactor(common): extract ControlCenterClient to cloudmorph-common-py`

Direct commits to `main` are the norm per founder preference. Reviewers may request changes after the fact.

## Code style

- **Python:** ruff format + ruff check + mypy --strict. Pydantic v2 for data models. No type: ignore unless absolutely required.
- **TypeScript:** prettier defaults + eslint + tsc --noEmit (no `any` unless absolutely required).
- **Rego:** opa fmt. One rule per concept.
- **JSON Schema:** 2-space indent, `additionalProperties: false`, `schemaVersion` field required.

## Tests

- Every new feature must add tests. Coverage gates per [`status/cross/08_tests_audit.md`](status/cross/08_tests_audit.md):
  - MCP server: 80%
  - Common-py: 80%
  - SDK: 90%
  - Policy engine (Rego): 90%
  - Intent system: 85%

- Adversarial fixtures live in `tests/adversarial/`. Adding new attack categories is encouraged.

## Schema changes

Any change to `contracts/*.schema.json` must:
1. Bump `schemaVersion` per the matrix in [`status/contracts/02_contracts_audit.md §2.6`](status/contracts/02_contracts_audit.md):
   - Add optional field → minor (v0.1 → v0.2)
   - Add required field, remove field, rename, tighten enum/pattern → major
   - Bug fix (no semantic change) → patch
2. Run `make contracts` to regenerate Pydantic + TS types.
3. CI gate verifies clean diff and bump category.

## Reporting bugs

GitHub Issues at [github.com/CloudMorphAI/cloudmorph-control-center/issues](https://github.com/CloudMorphAI/cloudmorph-control-center/issues).

For **security issues** see [SECURITY.md](SECURITY.md) — do not file public issues.
