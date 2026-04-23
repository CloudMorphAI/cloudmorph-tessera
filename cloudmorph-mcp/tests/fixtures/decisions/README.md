# Decision fixtures

`(input, expected_decision)` pairs that lock the readonly bundle's behavior.

Block E lights up `cloudmorph-mcp/tests/policy/fixtures.test.ts` to load each
JSON in this directory and assert the OPA WASM engine returns `expected.outcome`
and `expected.reason` for `input`.

## Adding a fixture

1. Pick the next sequence number (`08_`, `09_`, …).
2. Create `<seq>_<name>.json` with the shape `{name, description, input, expected}`.
3. The test runner picks it up automatically.

## Coverage so far

- 01: allow read.list
- 02: deny unknown
- 03: deny destructive
- 04: deny intent mismatch
- 05: allow intent match
- 06: deny tenant locked

Target by Block E end: ≥ 30 fixtures across allowlist, denylist, intent-conditional, time-of-day, approve, mutate, redact, throttle, audit-only, composite categories.

Detail in `status/policy/05_policy_engine_design.md §1.11 Layer 2`.
