# SONNET_REWRITE.md — Autonomous Tessera v0.1 Rewrite + Documentation

You are a Sonnet 4.6 session running inside the cloudmorph-tessera workspace. Your single autonomous run produces:

- A working v0.1 OSS MCP firewall (Python package + Docker image)
- Complete documentation
- All tests passing
- A clean repo ready for tagging

There is **no second Sonnet session** for this work. Phase 1 (rewrite) and Phase 2 (docs + cleanup) execute sequentially in one run.

---

## 1 — Mission

Build Tessera v0.1 to the spec locked in `SPEC.md` (v1.1). Success criteria, all required:

1. Every Phase-1 task verifies (its `pytest` / `docker build` / smoke test passes).
2. Coverage ≥ 80% (matches existing `pyproject.toml: [tool.coverage.report] fail_under = 80`); hash chain modules at 100%.
3. `mypy --strict`, `ruff check`, `ruff format --check`, and `pre-commit run --all-files` all pass.
4. `docker build -t tessera-test:dev .` succeeds; image < 200 MB.
5. Manual end-to-end smoke: `docker run` with mounted config, curl through proxy, see expected allow/block.
6. Phase 2 produces every doc file listed below; README quickstart paste-able and works.
7. `_keep/` is removed in the final cleanup task; `SPEC.md` is preserved at `docs/_internal/v1.1-spec.md`.
8. Final report enumerates: every file created/modified, test counts, image size, LoC by module, any open questions for the founder.

You operate on git branch `rewrite/v0.1` with atomic commits per task (one commit per task; never amend prior commits). **Do not push.**

---

## 2 — Repository state at start

- Branch: `main` is clean. Create and check out `rewrite/v0.1` as your first action.
- Workspace: `C:\Users\found\Desktop\CloudMorph\cloudmorph-tessera`.
- Existing files (do not delete except where noted):
  - `LICENSE` (Apache-2.0; preserve)
  - `.gitignore` (preserve)
  - `.pre-commit-config.yaml` (minor updates allowed: ensure mypy hook covers `tessera/`)
  - `pyproject.toml` (must add `[project]`, `[build-system]`, deps; SPEC §12 Task 1)
  - `gitleaks-config.toml` (drop CloudMorph Control Centre allowlist paths; keep token regexes)
  - `SECURITY.md` (rewrite for Tessera scope per SPEC §11)
  - `SPEC.md` (read-only reference; moved at Task 16)
  - `_keep/` (read-only source for ported files; deleted at Task 16)
- `_keep/` second-pass cleanup (SPEC Part A) was applied by the founder before this session. Files surviving:
  ```
  _keep/action_verbs/action_verbs.py
  _keep/audit/canonical_json.py
  _keep/audit/chain.py
  _keep/audit/emitter.py
  _keep/audit/sinks/buffered.py
  _keep/audit/sinks/stdout.py
  _keep/fixtures/decisions/{01..06}_*.json + README.md
  ```
  These are your inputs for ports. Verify the file list at session start; if it differs (founder may not have applied yet), STOP and ask.

---

## 3 — Sub-agent dispatch plan

Phase 1 has 14 tasks. The dependency DAG yields parallel batches. Use **sub-agents with `subagent_type=sonnet-coder`** for execution; the parent session orchestrates and verifies.

### Wave 0 — sequential (1 task)

- **Task 1** — Repo skeleton. Must complete before any other task.

### Wave 1 — 5-way parallel after Task 1 lands

Dispatch 5 sub-agents simultaneously:

| Sub-agent | Task | Why parallel-safe |
| --- | --- | --- |
| A | Task 2 — Audit chain + canonical JSON | Self-contained port + tests |
| B | Task 3 — Sink Protocol + Stdout + Buffered | Independent of Task 2 |
| C | Task 6 — Action verbs registry | Independent module |
| D | Task 9 — Config loader + env interpolation | Independent module |
| E | Task 10 — Authenticator (multi-token) + intent | Independent module |

### Wave 2 — 2-way parallel after Wave 1

| Sub-agent | Task | Depends on |
| --- | --- | --- |
| F | Task 4 — SqliteSink | Task 3 (Sink Protocol) |
| G | Task 7 — Policy schema + loader + regex safety + reload error handling | Tasks 1, 6 |

### Wave 3 — 2-way parallel after Wave 2

| Sub-agent | Task | Depends on |
| --- | --- | --- |
| H | Task 5 — AuditEmitter + verifier | Task 4 |
| I | Task 8 — Policy engine (matchers + conditions) | Task 7 |

### Wave 4 — 1 task

- **Task 11** — Reference policies + paired fixtures. Depends on Tasks 7, 8.

### Wave 5 — 1 task

- **Task 12** — Pluggable resolver + MCP proxy + three-mode + metrics auth. Depends on Tasks 5, 8, 9, 10, 11.

### Wave 6 — 1 task

- **Task 13** — CLI. Depends on Tasks 5, 7, 8, 9, 12.

### Wave 7 — 1 task

- **Task 14** — Dockerfile + image build. Depends on Tasks 12, 13.

### Phase 2 — Documentation parallelism

After Phase-1 checkpoint passes (§5), dispatch **8 sub-agents in parallel** for these doc files:

| Sub-agent | File |
| --- | --- |
| D1 | `docs/INSTALL.md` |
| D2 | `docs/POLICIES.md` |
| D3 | `docs/CONFIGURATION.md` |
| D4 | `docs/ARCHITECTURE.md` |
| D5 | `docs/INTEGRATIONS.md` |
| D6 | `docs/AUDIT.md` |
| D7 | `docs/TROUBLESHOOTING.md` |
| D8 | `docs/ROADMAP.md` |

Then sequentially: `CHANGELOG.md`, `CONTRIBUTING.md`, rewritten `SECURITY.md`. **README.md last** so it can link confidently to all subdocs.

### Parallelism budget summary

State this in your first message: **"Phase 1: max 5-way parallel (Wave 1). Phase 2: max 8-way parallel (docs)."**

When you dispatch parallel sub-agents, send them all in a single message with multiple Agent tool blocks. Wait for all to complete before moving to the next wave.

---

## 4 — Phase 1: Rewrite tasks

Each task below has: title, depends-on, key inputs, key outputs (selected), and verification. Full detail in `SPEC.md` §12. **Read the SPEC §12 entry for each task before dispatching the sub-agent.**

### Task 1 — Repo skeleton & build setup

**Depends on:** none.
**Inputs:** existing `pyproject.toml`; SPEC §1, §2.
**Pre-flight:** Run `pip index versions tessera`. If a release exists with a different maintainer/description than CloudMorph, the name is taken — uncomment `name = "cloudmorph-tessera"` in `pyproject.toml`. Else uncomment `name = "tessera"`. Either way, import name remains `tessera`.
**Outputs:**
- `tessera/__init__.py` — `__version__ = "0.1.0"`, public re-exports.
- `tessera/errors.py` — `ConfigError`, `PolicyError`, `AuditSinkError`, `UpstreamError`, `UnauthorizedError`.
- `pyproject.toml` updates: `[project]` with name (chosen above), `version = "0.1.0"`, deps; `[project.optional-dependencies]` `dev` (pytest, hypothesis, ruff, mypy, pre-commit) and `runtime` (fastapi, uvicorn, httpx, pydantic, typer, regex, watchdog, pyyaml); `[project.scripts] tessera = "tessera.cli:app"`; `[build-system] requires = ["setuptools>=68", "wheel"]`.
- `.env.example` — annotated.
- `tessera.example.yaml` — annotated, **with `policies.mode: log_only` as the default**.
- `tokens.example.yaml` — multi-token YAML format example.
**Verification:**
- `pip install -e ".[dev]"` succeeds.
- `python -c "import tessera; print(tessera.__version__)"` prints `0.1.0`.
- `tessera --help` prints (even with empty subcommands at this stage).
- `tessera.example.yaml` contains `mode: log_only` literal.

### Task 2 — Audit chain + canonical JSON

**Depends on:** Task 1.
**Inputs:** `_keep/audit/canonical_json.py`, `_keep/audit/chain.py`.
**Outputs:**
- `tessera/audit/canonical_json.py` — copy verbatim from `_keep`. **Do not reinvent.**
- `tessera/audit/chain.py` — copy verbatim from `_keep`; only edit the docstring to clarify that `tenant_id` is the scope/stream identifier (in OSS this comes from `AuthContext.scope`).
- `tests/unit/audit/test_canonical_json.py` — RFC 8785 conformance, NaN/Inf rejection, sorted keys, non-string-key rejection.
- `tests/unit/audit/test_chain.py` — stamping, per-scope head isolation, restore_head, verify_pair, verify_event_hash, thread safety.
- `tests/property/test_hash_chain_property.py` — Hypothesis: 4 properties from SPEC §10.
**Verification:** `pytest tests/unit/audit/test_canonical_json.py tests/unit/audit/test_chain.py tests/property/` passes. Coverage 100% on chain.py and canonical_json.py.

### Task 3 — Sink Protocol + Stdout + Buffered

**Depends on:** Task 1 (parallel-safe with Task 2).
**Inputs:** `_keep/audit/sinks/stdout.py`, `_keep/audit/sinks/buffered.py`.
**Outputs:**
- `tessera/audit/sinks/base.py` — `AuditSink` Protocol per SPEC §5.
- `tessera/audit/sinks/stdout.py` — copy from `_keep`; `head_hash` returns empty string, `iter_events` raises `NotImplementedError("stdout is write-only")`.
- `tessera/audit/sinks/buffered.py` — copy from `_keep`, no changes.
- Tests for both.
**Verification:** `pytest tests/unit/audit/sinks/test_stdout.py tests/unit/audit/sinks/test_buffered.py` passes.

### Task 4 — SqliteSink

**Depends on:** Task 3.
**Inputs:** SPEC §5 schema + PRAGMAs.
**Outputs:**
- `tessera/audit/sinks/sqlite.py` — connection-per-instance; CREATE-IF-NOT-EXISTS; WAL pragmas; `emit` uses transaction with `seq = MAX(seq WHERE scope=?)+1`; `head_hash` queries by `(scope, seq DESC LIMIT 1)`; `iter_events` orders by `(scope, seq)`.
- `tests/unit/audit/sinks/test_sqlite.py` — CRUD round-trip, concurrent emits don't collide, head_hash correctness, iter_events ordering, idempotent CREATE.
**Verification:** pytest passes; `sqlite3 audit.db ".schema"` matches SPEC §5 exactly.

### Task 5 — AuditEmitter + verifier

**Depends on:** Task 4.
**Inputs:** `_keep/audit/emitter.py`.
**Outputs:**
- `tessera/audit/emitter.py` — copy from `_keep`; replace `from cloudmorph_common.errors import AuditSinkError` with `from tessera.errors import AuditSinkError`.
- `tessera/audit/verifier.py` — chain-walk verifier; takes `AuditSink` + scope; returns dict per SPEC §5 verify output shape.
- `tessera/audit/__init__.py` — re-exports `HashChain`, `AuditEmitter`, `AuditSink`, `SqliteSink`, `StdoutSink`, `BufferedSink`, `canonical_json`.
- Tests for emitter (sink fan-out, failure isolation, on_sink_failure callback) and verifier (clean ok; tampered hash detected; chain break detected; empty scope ok).
**Verification:** `pytest tests/unit/audit/` passes (whole tree).

### Task 6 — Action verbs registry

**Depends on:** Task 1 (parallel-safe).
**Inputs:** `_keep/action_verbs/action_verbs.py`.
**Outputs:**
- `tessera/policy/action_verbs.py` — copy from `_keep`; **remove the `mcp.proxy` entry** in the dict and **remove the `mcp.proxy.*` prefix special-case** in `verbs_for`. Add `load_user_mappings(path: Path) -> dict[str, frozenset[str]]` that reads a YAML file with shape `{mappings: {tool_name: [verb, ...]}}`, validates each verb against `KNOWN_VERBS`, returns the merged dict. Add `merge_mappings(builtin, user) -> dict` for the loader to call.
- `tests/unit/policy/test_action_verbs.py` — `KNOWN_VERBS` frozen; `verbs_for` known/unknown; `mcp.proxy.*` no longer special-cased; user-mapping merge with override; rejects unknown verbs.
**Verification:** pytest passes.

### Task 7 — Policy schema + loader + regex safety + reload error handling

**Depends on:** Tasks 1, 6.
**Inputs:** SPEC §3 (schema, condition catalog, mode, lifecycle, regex safety).
**v1.1-specific implementation notes:**
- `policies.mode` lives at deployment-config level (SPEC §2 `tessera.yaml`), not per-policy. The Policy Pydantic model does NOT have a `mode` field. The engine returns a `Decision`; the proxy honors `mode`.
- Reload error handling: per-file isolation. The loader maintains a dict `{path: Policy}`. On reload, for each changed file: try parse + validate + regex-safety-check; on failure log structured error and **keep prior dict entry**; on success replace entry. Removed files: drop entry. Atomic swap of the sorted policy list at the end of each reload pass.
- `policy_state` exposed via a `loader.state()` method returning `{loaded: int, errored: list[{path: str, error: str}]}`. Proxy's `/healthz` reads this.
- Regex safety: `tessera/policy/regex_safety.py` exposes `validate_pattern(pattern: str) -> None` raising `PolicyError(reason="regex_potential_redos")` on corpus-test failure. The loader calls this for every regex string in `arg_matches_regex`, `arg_contains_pattern`, `tool_pattern`, `intent_purpose_matches` BEFORE constructing the `Policy`.
- Library: `regex` (PyPI). Compile once, reuse. Set `regex.TIMEOUT` flag with timeout=0.1 (100ms) on every match call; the corpus test asserts `<0.05` per string.
**Outputs:**
- `schemas/policy.schema.json` (JSON Schema draft-07).
- `schemas/config.schema.json` (incl. `policies.mode`, `default_action`, `metrics.enabled`, `metrics.bearer_token_env`, `intent.required`, `intent.meta_key`, multi-token references).
- `tessera/policy/schema.py` (Pydantic models).
- `tessera/policy/loader.py` (FilesystemPolicyLoader, PolicyLoader Protocol, watchdog integration, `state()` method).
- `tessera/policy/regex_safety.py`.
- Tests covering schema round-trip, loader sort order, duplicate ID rejection, `_action_verbs.yaml` recognition, watch callback firing, **per-file reload error isolation** (a malformed reload does NOT replace the prior valid version), regex safety (corpus rejects `(a+)+$`, accepts benign).
**Verification:**
- `pytest tests/unit/policy/test_schema.py test_loader.py test_regex_safety.py` passes.
- All 7 reference policies (Task 11) validate against `schemas/policy.schema.json`.
- Reload-error test: change a file to malformed → `loader.state()` shows it errored, but `loader.load_all(scope)` still returns the prior valid version.

### Task 8 — Policy engine (matchers + conditions)

**Depends on:** Task 7.
**Outputs:**
- `tessera/policy/matchers.py` — upstream/tool match: glob, regex (via `regex` lib + 100ms timeout), `*`.
- `tessera/policy/conditions.py` — every condition from SPEC §3 catalog. `arg: "*"` iterates all top-level args. Missing-arg fail-closed. **Regex matching uses `regex` library with 100ms timeout; on timeout return `False` and tag `decision_error: regex_timeout` via a side-channel (the engine reads it from a thread-local `decision_context.errors`).** Reason interpolation helper supports `${arg.X}` and `${audit.event_id}` (audit id injected by the proxy after emit).
- `tessera/policy/engine.py` — `PolicyEngine.evaluate(context: dict) -> Decision`. **Mode-AGNOSTIC**: returns a Decision regardless of mode; the proxy honors mode. First-match-wins by sorted policy order. Lockdown short-circuit BEFORE the policy loop. If no match, return `Decision(action=default_action, reason="default", policy_id=None)`.
- Tests for matchers, conditions (every variant, true/false cases, regex timeout case), engine (priority order, lockdown, default action).
**Verification:** `pytest tests/unit/policy/` passes (whole tree).

### Task 9 — Config loader + env interpolation

**Depends on:** Task 1 (parallel-safe).
**Outputs:**
- `tessera/config.py` — Pydantic `TesseraConfig` matching `tessera.yaml`; env-var loader with precedence per SPEC §2; `${VAR}` interpolation in `upstreams[].credentials.value` (resolve at load, never log resolved values).
- `tests/unit/test_config.py` — round-trip; env override; `${VAR}` substitution failure modes (var unset → `ConfigError`); type coercion; `policies.mode` enum validation; `metrics.enabled: false` defaults.
**Verification:** pytest passes; `tessera.example.yaml` validates against `schemas/config.schema.json`.

### Task 10 — Authenticator (multi-token) + intent extractor

**Depends on:** Task 1 (parallel-safe).
**v1.1 implementation notes:**
- `build_token_list()` precedence (return `list[Token]` where `Token = NamedTuple(name, token, scope)`):
  1. `TESSERA_BEARER_TOKENS` set → parse `name1:tk_xxx,name2:tk_yyy` (split on `,`, then on first `:`). Error on malformed entry (raise `ConfigError`).
  2. Else `TESSERA_BEARER_TOKENS_FILE` set → load YAML at path, parse `tokens: [{name, token, scope?}]`. Error on duplicates or missing fields.
  3. Else `TESSERA_BEARER_TOKEN` set → return `[Token("default", value, "default")]`.
  4. Else → return `[]` (dev mode).
- `BearerTokenAuthenticator.authenticate(request)`:
  - Extract `Authorization: Bearer <token>`.
  - If list empty (dev mode): return `AuthContext("anonymous", DEPLOYMENT_ID, {"warning": "auth_disabled"})`.
  - Iterate list with `secrets.compare_digest(candidate, incoming)`. Match → `AuthContext(name, scope, {})`. No match → raise `UnauthorizedError`.
- Dev-mode warning: a background `asyncio.Task` started at app startup logs `WARNING level=startup event=auth_disabled` once at boot and every 60s thereafter.
- Validation: name `[a-z0-9_-]{1,64}`, token ≥ 16 chars no whitespace, scope `[a-z0-9_-]{1,64}` (default = name).
**Outputs:**
- `tessera/auth/base.py` — `Authenticator` Protocol, `AuthContext` dataclass.
- `tessera/auth/bearer.py` — `build_token_list()` + `BearerTokenAuthenticator`.
- `tessera/intent.py` — extract `_meta.<configured_key>`; validate `verbs` against `KNOWN_VERBS`; enforce `intent.required`; reject malformed (verbs not list, purpose > 1024 chars).
- Tests covering every loader path, constant-time compare property, dev-mode bypass + warning loop, per-token scope reaches `AuthContext.scope`, intent extraction edge cases.
**Verification:** pytest passes.

### Task 11 — Reference policies + paired fixtures

**Depends on:** Tasks 7, 8.
**Outputs:**
- 7 YAML policies in `policies/` per SPEC §9.
- `policies/README.md` — brief intro (mode-agnostic, load order, kill-switch pairing).
- `tests/fixtures/policies/<id>/{pass,fail}/*.json` — at least 1 pass + 1 fail per policy.
- `tests/integration/test_reference_policies.py` — loads each policy, runs paired fixtures, asserts engine output.
**Verification:**
- `pytest tests/integration/test_reference_policies.py` passes.
- `tessera policy lint --policy-dir policies/` exits 0 (CLI exists at this point as a stub or full impl from Task 13 — note ordering caveat below).

> **Ordering note:** Task 11 runs before Task 13 (CLI). For the verification step, use a temporary inline call to `tessera.policy.loader.FilesystemPolicyLoader().load_all("default")` instead of the CLI. Re-run `tessera policy lint` verification after Task 13 lands.

### Task 12 — Pluggable resolver + MCP proxy + three-mode + metrics auth

**Depends on:** Tasks 5, 8, 9, 10, 11.
**v1.1 implementation notes:**
- **Mode handling** in proxy request flow (SPEC §4 step 7):
  - Read `config.policies.mode`. Branch on the three values.
  - `enforcement` → engine returns `Decision`. If `Decision.action` is `block`/`require_approval`, return JSON-RPC error and DO NOT forward; else forward to upstream.
  - `log_only` → engine returns `Decision`. **Always** forward upstream and return upstream response. Inject response headers: `X-Tessera-Mode: log_only`, `X-Tessera-Decision: would_block | would_allow | no_match` (mapping: `block` → `would_block`, `allow`/`log_only` → `would_allow`, default-no-match → `no_match`). On `would_block`, also `X-Tessera-Policy-Id: <id>` and `X-Tessera-Reason: <interpolated reason>`.
  - `observation` → skip engine entirely. Forward.
- **Audit event payload** records `mode` and either `decision` (enforcement) or `would_decision` (log_only) per SPEC §5.
- **Lockdown** check happens before the mode branch. Lockdown short-circuit returns block in all three modes.
- **Metrics gating** (SPEC §4): mount `/metrics` only when `config.metrics.enabled: true`. Add a FastAPI dependency that, when metrics is mounted, validates `Authorization: Bearer <token>` against either:
  1. `TESSERA_METRICS_TOKEN` if set (only that token accepted), or
  2. The main token list (any token grants metrics access).
  - Return 401 on no/bad token.
  - When metrics disabled, the route is not mounted; FastAPI returns 404.
- **`/healthz` policy_state**: proxy holds a reference to the `FilesystemPolicyLoader` and reads `loader.state()` per request. Returns `{"status": "ok", "policy_state": {"loaded": N, "errored": [...]}}`.
- **Audit event id injection**: after emit, the proxy adds `_meta.tessera_audit_event_id` to every response body (allow, would_*, observation, passthrough, block-with-body).
**Outputs:**
- `tessera/pluggable.py` — `resolve(env_var, default) -> class` via `importlib.import_module` + `getattr`.
- `tessera/proxy.py` — FastAPI app, request lifecycle, mode handling, metrics gating.
- `tests/fixtures/decisions/` — port from `_keep/fixtures/decisions/`; rename `06_deny_tenant_locked.json` → `06_deny_lockdown_active.json`; adapt input shapes (`toolCall.action` → `tool_call.name`, `tenantSettings` → `runtime.lockdown`); strip `intentMatchScore`.
- `tests/fixtures/upstream/mock_mcp_server.py` — FastAPI mock per SPEC §10.
- `tests/fixtures/tokens.example.yaml` — used by tests.
- `tests/conftest.py` — fixtures per SPEC §10.
- `tests/integration/test_proxy_round_trip.py` — allow + block + lockdown + intent missing + upstream timeout. **Three-mode tests:** enforcement honors decision; log_only forwards always with headers; observation skips engine. **Multi-token test:** per-token scope reaches audit chain.
- `tests/integration/test_policy_decisions.py` — runs ported decision fixtures.
- `tests/integration/test_audit_persistence.py` — restart restores chain head.
- `tests/integration/test_metrics_endpoint.py` — disabled = 404; enabled-no-token = 401; enabled with main token = 200; enabled with dedicated metrics token = 200.
**Verification:** `pytest tests/integration/` passes; all 6 ported fixtures pass; log_only headers present; healthz JSON has `policy_state`.

### Task 13 — CLI

**Depends on:** Tasks 5, 7, 8, 9, 12.
**Outputs:**
- `tessera/cli.py` — Typer app: `serve`, `audit verify`, `policy test`, `policy lint`, `version`, `init`. Exit codes per SPEC §6. `init` defaults `policies.mode: log_only` in scaffolded YAML.
- `tests/unit/test_cli.py` — every command's exit codes and `--json` shape (use `typer.testing.CliRunner`).
**Verification:**
- `pytest tests/unit/test_cli.py` passes.
- All 6 manual commands behave per SPEC §6 (lint exits 0 against `policies/`; test exits 0 against ported fixtures; verify exits 0 against an empty DB; init scaffolds with log_only mode).

### Task 14 — Dockerfile + image build

**Depends on:** Tasks 12, 13.
**Outputs:**
- `Dockerfile` per SPEC §7 (multi-stage, slim, non-root, healthcheck on `/healthz`).
- `docker-compose.example.yaml` — Tessera + mock upstream for local eval.
- `.dockerignore` — exclude `_keep/`, `tests/`, `.git/`, `__pycache__/`, etc.
**Verification:**
- `docker build -t tessera-test:dev .` succeeds.
- `docker image ls tessera-test:dev` shows < 200 MB.
- `docker run --rm tessera-test:dev tessera version` exits 0.
- **Manual smoke** (record commands run + outputs in your final report):
  ```bash
  docker run -d --name tessera-smoke -p 8080:8080 \
    -v $PWD/tessera.example.yaml:/etc/tessera/tessera.yaml:ro \
    -v $PWD/policies:/etc/tessera/policies:ro \
    -e TESSERA_BEARER_TOKEN=tk_smoke_$(openssl rand -hex 16) \
    tessera-test:dev
  curl -s http://localhost:8080/healthz | jq
  curl -s -X POST http://localhost:8080/mcp/<some-upstream> \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"...","arguments":{...}},"id":1}'
  docker stop tessera-smoke && docker rm tessera-smoke
  ```
  (Use a stub upstream from `docker-compose.example.yaml` for the curl test; the upstream URL in `tessera.example.yaml` should point to `http://mock-upstream:8081` in the compose network.)

---

## 5 — Phase 1 checkpoint

After Task 14, run a comprehensive checkpoint **before** Phase 2:

```bash
# Test suite
pytest --cov=tessera --cov-report=term-missing
# Coverage must show ≥ 80% (and 100% on chain.py + canonical_json.py).

# Static analysis
mypy --strict tessera
ruff check tessera tests
ruff format --check tessera tests

# Pre-commit
pre-commit run --all-files

# Docker
docker build -t tessera-test:dev .
docker image ls tessera-test:dev   # confirm < 200 MB
docker run --rm tessera-test:dev tessera version
```

If any step fails: try the fix in-place once. If still failing, **STOP** and report what's blocking. **Do not proceed to Phase 2 with a red checkpoint.**

Commit the checkpoint pass with message `chore(rewrite): phase 1 complete — all tasks 1-14 verified`.

---

## 6 — Phase 2: Documentation

Phase 2 begins only after a green Phase-1 checkpoint.

**Strict rule:** **Do not modify code during Phase 2.** If documentation reveals a code bug, append it to `TODOS.md` at repo root (one bullet per finding: file path, what's wrong, how docs say it should be). Do NOT fix code in this phase. The founder triages `TODOS.md` after the run.

### Wave 8 — 8-way parallel docs

Dispatch 8 sub-agents simultaneously. Each writes one file. Sub-agents use `subagent_type=general-purpose` (not coder; they're not editing code).

| Agent | File | Required content |
| --- | --- | --- |
| D1 | `docs/INSTALL.md` | Docker primary install (full `docker run`); pip secondary install; docker-compose example walkthrough; volume mount cheatsheet; `tessera init` 60-second walkthrough; production hardening checklist (rotate tokens, mount audit DB on a backed-up volume, run behind a reverse proxy with rate limit). |
| D2 | `docs/POLICIES.md` | Full YAML schema reference; condition catalog table with one example per row; the 7 reference policies explained line-by-line; how to author a custom policy from scratch; intent declarations (intent-aware vs intent-blind paragraph); composition limitations (no Rego in v0.1, no namespacing, no chaining — link to ROADMAP). |
| D3 | `docs/CONFIGURATION.md` | Every env var (full table); every `tessera.yaml` field; the **three modes** with worked transition (deploy log_only → review audit → flip to enforcement); multi-token setup (inline / file / legacy); lockdown kill switch; metrics endpoint with auth; precedence rules. |
| D4 | `docs/ARCHITECTURE.md` | For contributors. Component overview (proxy, engine, audit, sinks, auth) with ASCII diagram (agent → Tessera → upstream MCP). Request lifecycle. Audit subsystem. The three pluggable Protocols (`PolicyLoader`, `AuditSink`, `Authenticator`). Why no OPA in v0.1. FROM-base relationship for Tessera Cloud (high-level only — say "a private commercial wrapper extends these Protocols," do NOT reference internal cloudmorph-mono-repo paths). Local dev setup. |
| D5 | `docs/INTEGRATIONS.md` | Wiring Tessera into Cursor (worked `~/.cursor/mcp.json`), Claude Code (`~/.claude.json` snippet), Claude Desktop (`claude_desktop_config.json`), Windsurf. Each shows the bearer header and the local `http://localhost:8080/mcp/<upstream>` URL pointing through Tessera. Intent-aware vs intent-blind explanation in user-friendly terms. Per-known-MCP-server config templates (AWS, GitHub, Slack, Linear). |
| D6 | `docs/AUDIT.md` | Audit event schema (full JSON Schema reference). `tessera audit verify` usage with all flags. Hash chain integrity guarantees (SHA-256, per-scope chains, restart-survives via head_hash restore). SQLite-to-Postgres migration path (manual export then import; full migration story is v0.2). Retention guidance (keep forever default; user runs cron rotation; chain stays intact provided head row survives). |
| D7 | `docs/TROUBLESHOOTING.md` | Common issues with symptoms + remediation. Coverage: policy not applying (check log_only mode + healthz policy_state + intent-blind skip); intent missing (check meta_key + intent.required); audit verify failures (kind=hash_mismatch vs chain_break); Docker volume permissions (`/var/lib/tessera` ownership); regex timeout warnings (which policy + how to simplify pattern); `/metrics` returns 401 (token mismatch); healthz shows errored policies (read errored[].error string). |
| D8 | `docs/ROADMAP.md` | Per-feature deferral with rationale. Cover: OAuth 2.1 PKCE (v0.2 — needed for SaaS, not OSS), Rego escape hatch (v0.2 — adds OPA dep, gated on customer ask), multi-tenant in OSS (not planned — Cloud feature), ML intent inference (not planned — out of scope for deterministic firewall), native rate limiting (v0.2 — workaround via reverse proxy), shadow MCP discovery via MDM (v0.2+), Postgres sink (v0.2), stdio transport (v0.2). Open with one sentence per item, then a paragraph on rationale. End with: "Want a feature here moved up? Open an issue with the use case." |

### Wave 9 — sequential docs

After Wave 8 completes:

- `CHANGELOG.md` — start at v0.1.0 with the dated entry summarizing what shipped.
- `CONTRIBUTING.md` — dev setup (`pip install -e ".[dev]"`, `pre-commit install`); running tests (`pytest`, `ruff`, `mypy`); PR conventions (commit message style: `<type>(<scope>): <message>`; branch naming `<type>/<short>`); adding a new condition (one-stop on schema.py + conditions.py + tests + docs); adding a new sink (Protocol contract, tests, docs); adding a reference policy (YAML + paired fixtures + docs entry).
- `SECURITY.md` rewrite — drop CloudMorph Control Centre references. New scope:
  - In: proxy, policy engine, audit chain, OSS Docker image.
  - Out: user-authored policies (their problem), upstream MCP servers, customer-supplied bearer tokens.
  - Especially welcome: cross-scope audit log leakage, audit chain bypass, auth bypass, regex DoS bypassing the load-time corpus test, policy logic flaws.
  - Keep PGP and disclosure timeline sections; update email if needed (default `security@cloudmorph.io`).

### Wave 10 — README.md (last)

After all `docs/*.md` and the sequential trio land, write `README.md`. **Required structure** (max 400 lines, quickstart above the fold = within first 50 lines from top):

1. **Header** — `# Tessera`, tagline `The open-source MCP firewall for AI agents`, badges (license, Python ≥ 3.12, Docker pulls placeholder).
2. **Why Tessera** (3-5 sentences) — agents calling tools your way exceeds your safety envelope (problem); deterministic policies + hash-chain audit + no ML (solution); open source, no cloud creds, ships the policy library others charge for (wedge).
3. **5-minute quickstart** — paste-able commands a fresh user runs:
   - `docker pull ghcr.io/cloudmorph-ai/tessera:0.1.0`
   - `docker run --rm -v $PWD:/out ghcr.io/cloudmorph-ai/tessera:0.1.0 tessera init --dir /out` (creates `tessera.yaml` + `policies/` + `.env.example`)
   - Open `tessera.yaml`; edit `upstreams[].url` to point to your real MCP server.
   - `docker run -d --name tessera ...` (the full command from SPEC §7, with mounts and `TESSERA_BEARER_TOKEN`).
   - Configure Cursor/Claude Code with `mcp.json` snippet pointing at `http://localhost:8080/mcp/<upstream>` plus the bearer header (link to `docs/INTEGRATIONS.md` for full snippet).
   - Send a tool call. Watch it logged in audit (`tessera audit verify`).
   - Edit `tessera.yaml`: change `mode: log_only` → `mode: enforcement`. Restart container. Now blocks fire.
   - **If exposing Tessera beyond localhost, put it behind nginx/Caddy with a rate-limit rule. Native rate limiting is on the v0.2 roadmap.**
4. **What ships** — bulleted feature list; link each to its subdoc.
5. **How it works** — single ASCII diagram (`Agent → [Tessera: auth → engine → audit] → Upstream MCP`); link to `docs/ARCHITECTURE.md`.
6. **Configuration at a glance** — minimal 10-line `tessera.yaml`; link to `docs/CONFIGURATION.md`.
7. **Authoring policies** — minimal 10-line policy; link to `docs/POLICIES.md`.
8. **Tessera Cloud** — short paragraph: "Want hosted? Multi-tenant? SSO? Compliance evidence export? Tessera Cloud is the same engine with hosted orchestration. Same Protocols, swapped implementations. <https://cloudmorph.ai>."
9. **Roadmap** — 4-6 bullets of deferred features; link to `docs/ROADMAP.md`.
10. **Contributing / License / Security** — three-line footer with relative links.

**README constraints** (enforce in your write):
- Max 400 lines (under 20 KB).
- Every claim verifiable against running code (no aspirational language).
- Every internal link uses relative paths (`docs/POLICIES.md`, NOT `https://github.com/...`).
- Every quickstart command actually works — copy-paste-execute.
- No marketing language.
- Quickstart visible above the fold.

### Phase 2 verification

Run before commit:

```bash
# Link check (every relative link in *.md resolves)
python -c "import re,os,glob; [print(f) for f in glob.glob('**/*.md', recursive=True) for m in re.finditer(r'\\]\\(([^)]+\\.md(?:#.+)?)\\)', open(f, encoding='utf-8').read()) if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(f), m.group(1).split('#')[0]))) and not m.group(1).startswith('http')]"
# Should print nothing.

# Markdown lint (if `markdownlint-cli` is available; else skip and note in final report)
markdownlint README.md docs/*.md CHANGELOG.md CONTRIBUTING.md SECURITY.md  || true

# File size sanity
wc -c README.md             # < 20480 bytes
wc -c docs/*.md             # each < 30720 bytes

# Quickstart smoke (manual)
# Run the README quickstart end-to-end on a clean machine. Confirm:
# - log_only deploy returns the upstream response with X-Tessera-Mode header.
# - audit verify shows the call logged.
# - After mode flip to enforcement, the same call now blocks (or allows per its policy).
```

If link check or quickstart fails, add the issue to `TODOS.md` (do NOT modify docs to paper over a code bug, and do NOT modify code per the strict rule). Then commit Phase 2 with whatever passes.

Commit Phase 2 with message `docs(rewrite): phase 2 complete — readme + docs/*`.

---

## 7 — Final task: Cleanup (Task 16 from SPEC)

After Phase 2 verifies:

```bash
rm -rf _keep/
mkdir -p docs/_internal
mv SPEC.md docs/_internal/v1.1-spec.md
```

Commit: `chore(cleanup): remove _keep/, archive spec to docs/_internal`.

Final verification:

```bash
git status                 # clean tree
pytest                     # everything still passes
ruff check tessera tests
mypy --strict tessera
pre-commit run --all-files
```

---

## 8 — Final report

Print at the end of the run:

```
=== Tessera v0.1 rewrite — final report ===
Branch:               rewrite/v0.1
Commits:              <N atomic commits, one per task + checkpoint + docs + cleanup>
PyPI distribution:    tessera | cloudmorph-tessera (with one-line rationale from pre-flight check)

Files created:
  tessera/         <list every .py>
  tests/           <list every .py + .json + .yaml>
  policies/        <list every .yaml>
  schemas/         <list every .json>
  docs/            <list every .md>
  Dockerfile, docker-compose.example.yaml, .dockerignore, .env.example,
  tessera.example.yaml, tokens.example.yaml,
  README.md, CHANGELOG.md, CONTRIBUTING.md
  docs/_internal/v1.1-spec.md (archived spec)

Files modified:
  pyproject.toml, .pre-commit-config.yaml, gitleaks-config.toml, SECURITY.md

Files deleted:
  _keep/  (entire tree)
  SPEC.md (moved, not deleted)

Test counts:
  unit:        <N> passed
  integration: <N> passed
  property:    <N> passed
  Total:       <N> passed, 0 failed

Coverage:    <pct>% overall; chain.py 100%, canonical_json.py 100%

LoC by module (`tessera/`):
  audit/             <N>
  policy/            <N>
  auth/              <N>
  proxy.py           <N>
  config.py, intent.py, errors.py, pluggable.py, cli.py: <N each>

Docker image:
  tessera-test:dev   <size MB>  (target < 200 MB)

Open questions for the founder:
  <list any TODOS.md entries; e.g., "TODOS.md item 1: docs/POLICIES.md describes
  arg_in_set with case-insensitive comparison but conditions.py uses ==. Confirm intended.">
  <or "None — clean run.">

Manual smoke results:
  /healthz returns 200 with policy_state.<...>
  POST /mcp/<upstream> in log_only mode returns upstream response + X-Tessera-Mode: log_only.
  After flipping to enforcement, the same call blocks with the expected reason.

Status: READY FOR FOUNDER REVIEW.
```

---

## 9 — Constraints (durable for the whole run)

- Branch: `rewrite/v0.1`. Atomic commits per task. **Do not push.**
- Maximum parallel sub-agents at every safe parallel batch — state explicit count per batch in your first message ("Phase 1: max 5-way parallel; Phase 2: max 8-way parallel").
- Coverage target: 80% (matches existing pyproject.toml fail_under).
- Type hints throughout; `mypy --strict` passes.
- `ruff` + `ruff format` pass; `pre-commit` hooks pass.
- Every task's verification must pass before moving to its dependent task.
- If a task fails verification after **one** retry, **STOP** and report.
- Build Dockerfile but do NOT publish to GHCR. Verify `docker build` succeeds locally.
- Do NOT write GitHub Actions workflows.
- Do NOT delete `_keep/` until Task 16; rewrite consumes it then removes it.
- Do NOT modify code during Phase 2. If docs reveal a code bug, write it to `TODOS.md`; do not fix.
- After everything: print the final report.

---

## 10 — Failure modes — call these out, do not commit them

- **Don't reinvent the audit chain.** Port `_keep/audit/{chain.py, canonical_json.py, emitter.py}` substantially as-is. Only the `cloudmorph_common.errors` import path is fixed; everything else is verbatim.
- **Don't add OPA dependency.** v0.1 is pure-Python evaluation. No `opa-python`, no wasm-opa, no subprocess to `opa`.
- **Don't add executor scaffolding.** No jobs, approvals, async dispatch, lease tables. The firewall inspects, never acts. `require_approval` is a tagged audit + JSON-RPC error, nothing more.
- **Don't add multi-tenant logic.** Single-deployment-single-policy-set in OSS. Multi-token gives per-token scope, but there is no tenant resolution from URL or claim parsing in the OSS code.
- **Don't add OAuth 2.1 / SSO / SAML.** Bearer-only in v0.1. No JWT validation in the OSS Authenticator (Cloud's CognitoJWTAuthenticator is in the mono-repo).
- **Don't write GitHub Actions.** No `.github/workflows/*.yml`. The founder publishes manually.
- **Don't reference "Control Centre" anywhere except git history.** All product copy says "Tessera." Search-and-confirm before each commit.
- **Don't silently work around regex timeouts.** When the corpus test rejects a pattern at startup, FAIL hard with exit 2 and a clear log line. Do not auto-rewrite the user's pattern.
- **Don't redact arguments in audit events.** v0.1 stores them verbatim. DLP is the user's job via policies like `pii-block.yaml`. Adding a redaction pipeline silently is scope creep.
- **Don't change the Protocol surface.** `PolicyLoader`, `AuditSink`, `Authenticator` shapes are fixed by SPEC §8. Tessera Cloud's wrapper Dockerfile assumes these signatures.

---

## Begin

When you start:
1. Confirm `_keep/` matches the expected file list (SPEC §2). If it doesn't, STOP and ask.
2. Create branch `rewrite/v0.1` and check out.
3. Print: `Phase 1 starts. Wave 0: Task 1 (sequential). Wave 1: Tasks 2/3/6/9/10 (5-way parallel). [...]`
4. Begin Task 1.
