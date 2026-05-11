# Contributing to Tessera

Thank you for taking the time to contribute. This document covers everything you need to go from a fresh clone to an accepted pull request.

---

## Table of contents

1. [Dev setup](#dev-setup)
2. [Running tests and linters](#running-tests-and-linters)
3. [PR and commit conventions](#pr-and-commit-conventions)
4. [Adding a new condition](#adding-a-new-condition)
5. [Adding a new audit sink](#adding-a-new-audit-sink)
6. [Adding a reference policy](#adding-a-reference-policy)
7. [Opening issues](#opening-issues)

---

## Dev setup

**Prerequisites:** Python 3.12+, Git.

```bash
# Clone the repo
git clone https://github.com/cloudmorphai/cloudmorph-tessera.git
cd cloudmorph-tessera

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install the package in editable mode with all dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

The `[dev]` extra installs pytest, ruff, mypy, hypothesis, pre-commit, and the `regex` library used by the policy engine.

---

## Running tests and linters

All of the following must pass before opening a PR.

### Tests

```bash
# Run the full test suite
pytest

# Run a specific file or directory
pytest tests/unit/policy/

# Run with coverage report
pytest --cov=tessera --cov-report=term-missing
```

The project targets 80% overall coverage. The audit chain modules (`chain.py`, `canonical_json.py`) target 100%.

### Linting and formatting

```bash
# Check for lint errors
ruff check .

# Auto-fix safe issues
ruff check --fix .

# Format code
ruff format .
```

### Type checking

```bash
mypy --strict tessera
```

All public functions and methods must have type annotations. `mypy --strict` is enforced in CI.

### Pre-commit (runs all of the above on staged files)

```bash
pre-commit run --all-files
```

The hooks run automatically on `git commit`. If a hook rewrites a file, stage the change and commit again.

---

## PR and commit conventions

### Commit message format

```
<type>(<scope>): <short imperative description>
```

**Types:**

| Type | Use for |
|---|---|
| `feat` | New feature or behaviour |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or updating tests |
| `chore` | Build, CI, dependency, or tooling changes |
| `perf` | Performance improvement |

**Scopes:** `auth`, `policy`, `audit`, `proxy`, `cli`, `config`, `docker`, `deps`, `docs`.

**Examples:**

```
feat(policy): add time_of_day_outside condition
fix(audit): restore chain head after sqlite reconnect
docs(contributing): add sink walkthrough
chore(deps): bump regex to 2024.5.0
```

- Use the imperative mood ("add", not "adds" or "added").
- Keep the subject line under 72 characters.
- Reference issues in the body: `Closes #42`.

### Branch naming

```
<type>/<short-slug>
```

Examples: `feat/regex-timeout`, `fix/chain-head-restore`, `docs/audit-walkthrough`, `chore/pre-commit-update`.

### Pull request checklist

Before marking a PR ready for review:

- [ ] All tests pass (`pytest`).
- [ ] Linter and formatter pass (`ruff check .`, `ruff format .`).
- [ ] Type checker passes (`mypy --strict tessera`).
- [ ] New behaviour is covered by tests.
- [ ] Public API changes are reflected in the relevant `docs/` file.
- [ ] If a new env var was added, `docs/CONFIGURATION.md` is updated.
- [ ] CHANGELOG.md has an entry under `## [Unreleased]`.

---

## Adding a new condition

Conditions are the leaf predicates in a policy's `when` clause. To add one:

### 1. Add the Pydantic model to `tessera/policy/schema.py`

Each condition is a tagged union member. Add a new class that inherits from `BaseCondition` (or an appropriate mixin) and set `condition` as a `Literal` discriminator:

```python
class ArgStartsWithCondition(BaseModel):
    condition: Literal["arg_starts_with"]
    arg: str
    value: str
```

Add the new class to the `Condition` union type at the bottom of the file.

### 2. Implement the evaluator in `tessera/policy/conditions.py`

Add a branch to `evaluate_condition()`. The function signature is:

```python
def evaluate_condition(condition: Condition, ctx: EvaluationContext) -> bool:
```

Follow the existing pattern: missing argument returns `False` (fail-closed). If the condition uses a regex pattern, call `regex_safe_match()` from `tessera/policy/regex_safety.py` — do not call `re` or `regex` directly.

### 3. Write tests

Add a test file or extend the existing one at `tests/unit/policy/test_conditions.py`. Cover:
- Normal true case.
- Normal false case.
- Missing argument (must return `False`).
- Edge cases (empty string, None value, type mismatch).
- If the condition uses a regex: timeout case (patch `regex_safety.regex_safe_match` to raise `TimeoutError`) and verify `decision_error: regex_timeout` is recorded.

### 4. Update `docs/POLICIES.md`

Add a row to the condition catalog table. Include the condition name, required fields, and a one-line description of the truth condition. Add a minimal worked example below the table.

---

## Adding a new audit sink

An audit sink persists the hash-chained event stream. The default is SQLite; operators can swap in any backend that implements the `AuditSink` Protocol.

### 1. Implement the Protocol

Create a new file, e.g. `tessera/audit/sinks/postgres.py`. Implement every method of `AuditSink` from `tessera/audit/sinks/base.py`:

```python
class AuditSink(Protocol):
    name: str
    def emit(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...
    def head_hash(self, scope: str) -> str: ...
    def iter_events(self, scope: str | None = None) -> Iterator[dict]: ...
```

Key invariants:
- `emit` must be atomic per event; partial writes must not corrupt the chain.
- `head_hash(scope)` must return the `event_hash` of the last emitted event for that scope, or an empty string if no events exist yet. The emitter calls this at startup to restore the chain head.
- `iter_events(scope)` must yield events in ascending `seq` order for the given scope. If `scope` is `None`, yield all events across all scopes in deterministic order.

### 2. Write tests

Add `tests/unit/audit/sinks/test_<backend>.py`. Cover:
- `emit` persists the event.
- `head_hash` returns correct value after one and many emits.
- `iter_events` yields events in seq order.
- `close` is idempotent.
- Concurrent `emit` calls do not corrupt the chain (use `threading.Thread` or `asyncio.gather`).

### 3. Update `docs/AUDIT.md`

Add a section describing the new sink: how to enable it (`TESSERA_AUDIT_SINK=module:Class`), any required dependencies, connection configuration, and migration notes.

---

## Adding a reference policy

Reference policies live in `policies/` and ship with the Tessera image. They are mode-agnostic: the same YAML works in `enforcement`, `log_only`, and `observation`.

### 1. Write the YAML file

Create `policies/<id>.yaml`. Follow the existing policies as templates. Required fields: `id`, `name`, `match`, `action`. The `id` must be unique and kebab-case.

Run `tessera policy lint --policy-dir policies/` to validate the new file before committing.

### 2. Add paired test fixtures

Create at least one pass fixture and one fail fixture under `tests/fixtures/policies/<id>/`:

```
tests/fixtures/policies/<id>/
  pass/
    01_<description>.json    # A tool call that should NOT be blocked by this policy
  fail/
    01_<description>.json    # A tool call that SHOULD be blocked by this policy
```

Each fixture is a JSON object with `tool_call`, `intent` (optional), and `runtime` fields matching the evaluation context shape. See `tests/fixtures/policies/cost-cap/` for examples.

The integration test at `tests/integration/test_reference_policies.py` loads all fixture directories automatically — no code change required to exercise the new fixtures.

### 3. Update `docs/POLICIES.md`

Add a section for the new policy. Include: what it does, when to use it, the full YAML body, and a worked example showing a blocked call and a passing call.

---

## Opening issues

### Bug reports

Include:
- Tessera version (`tessera version --json`).
- Python version and OS.
- Minimal reproduction: `tessera.yaml`, policy YAML, and the `curl` or Python snippet that triggers the bug.
- Actual behaviour (include log output at `DEBUG` level if relevant).
- Expected behaviour.

### Feature requests

Include:
- The use case you are trying to solve (not just the feature name).
- What you have tried with the current version and why it falls short.
- Any prior art — similar features in comparable tools.

If the feature involves a new condition or a new Protocol method, a short sketch of the proposed API is helpful.

### Security vulnerabilities

**Do not file a public issue for security vulnerabilities.** See [SECURITY.md](SECURITY.md) for the responsible disclosure process.
