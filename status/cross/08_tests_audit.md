# 08 — Tests Audit

_Current: 5 test files (784 LoC). MCP server: 0 tests (`echo "No tests yet"`). AWS/Azure/GCP executors: 0 tests. Test-ratelimit-py is a misleading re-implementation. Adversarial suite: doesn't exist._

---

## 1.1 What's there today

| File | LoC | Honest assessment |
|---|---:|---|
| [tests/test_python_sdk.py](../../tests/test_python_sdk.py) | 122 | Decent — covers SDK init + happy path + RPC error. Missing: 429 path, polling loop, type checks. ~60% coverage of `client.py`. |
| [tests/test_databricks_job_runner.py](../../tests/test_databricks_job_runner.py) | 174 | Best per-executor coverage. ~80% of `job_runner.py`. Missing: pagination, error paths. |
| [tests/test_snowflake_job_runner.py](../../tests/test_snowflake_job_runner.py) | 154 | ~75% coverage. Missing: key-pair auth path. |
| [tests/test_credentials.py](../../tests/test_credentials.py) | 246 | Tests credential encrypt/mask/inject. **Imports from `cloudmorph-mono-repo/amplify/functions/shared/accounts/`** — reaches into a sibling repo. Brittle but real coverage. |
| [tests/test_ratelimit.py](../../tests/test_ratelimit.py) | 88 | **Misleading.** Re-implements the TS rate limiter in Python and tests the re-implementation. Header comment admits: "For the actual TS rate limiter, tests would run via node --test". Effective coverage of [ratelimit.ts](../../cloudmorph-mcp/src/ratelimit.ts): **0%.** |

**Aggregate truth:**
- TS: 0% coverage (no Vitest, no Node test runner wiring).
- Python — Databricks runner: ~80%, Snowflake runner: ~75%, SDK: ~60%, AWS/Azure/GCP runners: 0%, common (doesn't exist yet): N/A.
- The product's two pillars (policy engine, intent system) **don't exist yet** so no tests possible until they ship.

---

## 1.2 The 14-day MVP test plan

### Layer 1 — Unit tests (per-component)

#### MCP server (Vitest, `cloudmorph-mcp/tests/`)

```
cloudmorph-mcp/tests/
├── unit/
│   ├── auth.test.ts                   # bearer extraction, edge cases
│   ├── ratelimit.test.ts              # actual TS rate limiter (replaces test_ratelimit.py)
│   ├── routes-jsonrpc.test.ts         # initialize, tools/list, tools/call dispatch
│   ├── routes-tools-cloudmorph_request.test.ts
│   ├── routes-tools-declare_intent.test.ts        # NEW
│   ├── routes-tools-explain_decision.test.ts      # NEW
│   ├── routes-tools-proxy.test.ts                 # NEW
│   ├── ws.test.ts                     # subscribe/unsubscribe/wait
│   ├── policy/engine.test.ts          # OPA WASM eval
│   ├── policy/bundle-loader.test.ts   # signature, hot-reload
│   ├── policy/cache.test.ts           # LRU + TTL
│   ├── intent/lexical-matcher.test.ts # verb intersection
│   ├── intent/session-store.test.ts   # in-memory + TTL
│   ├── audit/emitter.test.ts          # chain + sinks
│   ├── audit/chain-verify.test.ts     # tamper detection
│   ├── audit/sinks-stdout.test.ts
│   ├── audit/sinks-s3.test.ts         # mocked aws-sdk
│   ├── audit/sinks-buffered.test.ts   # disk overflow + drop-oldest
│   └── transports/stdio.test.ts       # MCP SDK stdio adapter
├── fixtures/
│   ├── bundles/
│   │   ├── readonly.tar.gz             # ~10 rules
│   │   ├── intent-required.tar.gz      # require intent for everything
│   │   ├── deny-destructive.tar.gz     # deny write.delete*
│   │   ├── mutate-row-cap.tar.gz       # add LIMIT 10000 to SQL
│   │   └── ...
│   └── decisions/
│       └── (request, expected_decision) pairs — 30+ for MVP, 50+ post-MVP
└── integration/
    ├── mcp-end-to-end.test.ts          # spawn server, full tools/call against stub upstream
    ├── policy-replay.test.ts           # capture-replay against new bundle
    └── benches/                         # autocannon harnesses
        ├── tools-call-allow.js
        ├── tools-call-deny.js
        └── tools-call-cache-hit.js
```

**Effort:** ~40h to wire (Vitest setup + first 20 tests). Block D ongoing.

#### Common-py (`cloudmorph-common-py/tests/`)

```
tests/
├── test_client.py                # ControlCenterClient (the one extracted from 5 dups)
├── test_base_executor.py         # lifecycle: claim/heartbeat/complete; signal handling
├── test_audit_emitter.py         # chain + sinks
├── test_audit_chain.py           # tamper detection
├── test_artifact_writers.py      # S3/GCS/Blob (mocked SDKs)
├── test_settings.py              # Pydantic env loading
├── test_secret_resolver.py
├── test_action_verbs_complete.py # every action has a verb mapping
└── test_contracts_generated.py   # generated Pydantic models match schemas
```

Coverage target: 80%. Effort: 14h. Block C.

#### Per-executor

(Detail in each per-cloud audit. Summary: ~30h per cloud × 5 = ~150h. **Most slips to post-MVP.** MVP coverage target: AWS @ 40%, others stay at current.)

#### SDK (`tests/test_python_sdk*.py` and adapters)

```
tests/
├── test_python_sdk.py              # extend current
├── test_python_sdk_async.py        # AsyncCloudMorph (when added)
├── test_firewall_decorator.py
├── test_firewall_proxy.py
├── test_adapter_anthropic.py
└── test_adapter_openai.py
```

Coverage target: 90% on `client.py` + `firewall.py`, 70% on adapters. Effort: ~24h. Block F.

### Layer 2 — Integration tests

| Test target | Approach | Effort |
|---|---|---:|
| MCP server end-to-end | Spawn server in subprocess, talk to stub upstream API | 8h |
| Policy bundle hot-reload | Author 2 bundles, swap, verify in-flight requests behave correctly | 4h |
| Audit chain integrity | Generate 1000 events, verify chain end-to-end with verifier CLI | 4h |
| Cross-MCP-proxy round trip | Stand up dummy downstream MCP server, verify policy enforcement | 6h |
| AWS executor against LocalStack | Real S3, EC2 (LocalStack pro for some services); nightly | 8h |
| GCP executor against fake-gcs-server / gcloud emulators | Same | 8h |
| Azure executor against Azurite | Real Blob; nightly | 4h |
| Databricks executor against vcrpy recordings | Replay; CI per-PR | 4h |
| Snowflake executor against vcrpy recordings | Same | 4h |

**MVP:** MCP end-to-end (8h) + policy hot-reload (4h) + audit chain (4h) = 16h. Block H.

### Layer 3 — Replay tests

Capture real decisions from staging into JSONL files. Replay against candidate bundles to detect regressions before promotion.

```bash
# Capture (continuous in staging)
cm-staging-export-decisions --since 2026-04-23 --to ./replay/2026-04-23.jsonl

# Replay (in CI per-PR that touches bundles or policy code)
cm-policy-replay \
  --bundle ./bundles/candidate.tar.gz \
  --decisions ./replay/2026-04-23.jsonl \
  --baseline ./baselines/v0.42.0-decisions.jsonl

# CI fails if > 1% of replayed decisions changed without explicit approval
```

**Effort:** 8h to write the capture + replay tooling. Block H.

### Layer 4 — Adversarial suite — the moat

This is where the product earns the security buyer's trust. Target: ≥ 30 distinct attack fixtures by MVP, ≥ 50 by 60-day, ≥ 200 by 90-day.

| Category | Example fixtures | Count target (MVP) |
|---|---|---:|
| **Prompt injection via tool args** | `{"sql": "; DROP TABLE users; --"}`, `{"name": "../../etc/passwd"}`, Unicode normalization tricks | 8 |
| **Intent spoofing** | Declare benign intent then attempt destructive; partial-match games; stale-intent-replay | 5 |
| **Intent mismatch** | Declared `read.list`, attempted `write.delete`; verb downcasing tricks | 4 |
| **TOCTOU in approval flows** | Approve, then race to change args before forward; cancel-and-resubmit | 4 |
| **Policy bundle tampering** | Swapped bundle bytes; valid signature on stale bundle; signature key rotation race | 4 |
| **Audit chain forgery** | Insert event with future timestamp; modify and re-sign; gap in chain | 3 |
| **Session hijack** | Reuse expired session; cross-tenant session-id collision | 3 |
| **Decision cache poisoning** | Cache a permissive decision then attempt to ride it after bundle changes | 2 |
| **Redaction bypass via re-encoding** | Base64 the field before MCP eval; nested JSON; gzip | 4 |
| **Replay attacks** | Replay a 1h-old `cloudmorph_request` from a captured token | 3 |
| **Token leak** | Tokens in URL, logs, error messages | 3 |

**Authoring effort:** 12h for 30 fixtures (Block H). **Each fixture should:**
1. Have a name like `adv_intent_spoof_partial_match`
2. Set up the world (tenant, session, intent, bundle, what-if state)
3. Execute the attack
4. Assert the firewall blocks AND the audit log captures the attempt with a clear reason
5. Be idempotent (no leaked state)

**Run schedule:** every PR. Failing one = security regression = blocker.

---

## 1.3 Coverage targets

| Component | MVP target | Post-MVP target | Current |
|---|---:|---:|---:|
| MCP server | 80% line | 90% | 0% |
| Common-py | 80% line | 90% | N/A (new) |
| SDK (`client.py`) | 90% line | 95% | ~60% |
| SDK (`firewall.py`) | 80% line | 90% | N/A (new) |
| SDK adapters (anthropic, openai) | 70% line | 85% | 0% |
| AWS executor | 40% line | 70% | 0% |
| Azure executor | 40% line | 70% | 0% |
| GCP executor | 40% line | 70% | 0% |
| Databricks executor | 80% line | 90% | ~80% |
| Snowflake executor | 75% line | 90% | ~75% |
| Policy engine (Rego) | 90% line | 95% | N/A (new) |
| Intent system | 85% line | 95% | N/A (new) |
| Audit emitter | 90% line | 95% | N/A (new) |
| Adversarial fixtures | 30 fixtures | 50+ | 0 |

**Tooling:**
- TypeScript: `vitest --coverage` (c8). Codecov upload for trend.
- Python: `coverage` + `pytest-cov`. Codecov upload.
- CI fails on coverage drop > 2% from baseline (per-component).

---

## 1.4 Test data hygiene

- **No real customer data in fixtures.** Synthetic accounts, generated tokens.
- **Token format `cm_test_<32-char-random>`** to make it grep-able and rotate-able.
- **No live API calls in unit tests.** All cloud SDKs mocked via `moto`/`mock-aws-sdk-v3`/equivalent.
- **Integration tests against emulators** (LocalStack, Azurite, fake-gcs) on nightly schedule, not per-PR.
- **vcrpy recordings checked into repo** under `tests/cassettes/` — auditable, reviewable.

---

## 1.5 CI structure

```yaml
# .github/workflows/ci.yml (root, replaces cloudmorph-mcp/.github/workflows/ci.yml)

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  contracts-verify:
    name: contracts · validate · regenerate · diff
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make contracts-verify

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make lint   # ruff, mypy --strict, eslint, tsc --noEmit

  test-mcp:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 18 }
      - run: cd cloudmorph-mcp && npm ci && npm test -- --coverage
      - uses: codecov/codecov-action@v5
        with: { files: cloudmorph-mcp/coverage/coverage-final.json }

  test-py:
    strategy:
      matrix:
        python: ["3.9", "3.10", "3.11", "3.12", "3.13"]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: ${{ matrix.python }} }
      - run: pip install -e cloudmorph-common-py -e sdk-python
      - run: pytest tests/ cloudmorph-common-py/tests/ sdk-python/tests/ --cov

  test-policy-rego:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          curl -L https://github.com/open-policy-agent/opa/releases/latest/download/opa_linux_amd64 -o opa
          chmod +x opa
          ./opa test cloudmorph-mcp/src/policy/rules/

  bench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cd cloudmorph-mcp && npm ci && npm run bench
      - uses: ./.github/actions/bench-compare    # fail on > 20% regression

  adversarial:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest tests/adversarial/ -v --tb=short

  docker-build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target: [cloudmorph-mcp, aws-executor, azure-executor, gcp-executor, databricks-executor, snowflake-executor]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - run: docker buildx build --platform linux/amd64,linux/arm64 -t ${{ matrix.target }}:ci .
      - run: trivy image --severity HIGH,CRITICAL --exit-code 1 ${{ matrix.target }}:ci

  nightly-integration:
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'
    services:
      localstack: { image: localstack/localstack:latest, ports: [4566:4566] }
      azurite: { image: mcr.microsoft.com/azure-storage/azurite, ports: [10000:10000] }
    steps:
      - uses: actions/checkout@v4
      - run: pytest tests/integration/ -v
```

Effort: 8h to wire root CI replacing the mcp-only one. Block A.

---

## 1.6 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Replace `npm test` no-op with Vitest | **P0** | 4h | A |
| MCP server unit test suite (~20 tests) | P0 | 24h | D |
| Policy engine Rego unit tests | P0 | 8h | E |
| Intent matcher unit tests | P0 | 6h | E |
| Common-py test suite (80%) | P0 | 14h | C |
| SDK test suite extension to 90% | P0 | 8h | F |
| Adversarial fixtures (30 in MVP) | P0 | 12h | H |
| MCP integration tests | P0 | 16h | H |
| Replay test harness | P1 | 8h | H |
| Nightly LocalStack/Azurite integration | P1 | 16h | post-MVP |
| Coverage tooling + CI gates | P0 | 4h | A |
| Root CI replacing mcp-only | P0 | 8h | A |
| Trivy + SBOM scans in CI | P1 | 4h | H |
| Bench CI gate (regression > 20%) | P1 | 4h | H |
| Per-executor handler tests (AWS/Azure/GCP) | P1 | ~90h spread | post-MVP |
| Delete misleading `test_ratelimit.py` | P0 | 1h | A |

**MVP critical-path total: ~120h.** A LOT. Realistically: ~60h achievable for "tests that catch regressions in the wedge" (MCP + policy + intent + audit + SDK + common). The per-executor handler tests slip to post-MVP unless a design partner needs them earlier.

---

## 1.7 Out of scope

- Mutation testing (`mutmut`, `stryker`). Post-MVP if coverage targets aren't catching enough bugs.
- Property-based testing (`hypothesis`, `fast-check`). Post-MVP — add for the adversarial suite first.
- Snapshot testing of decision payloads. Useful but adds maintenance overhead; defer.
- E2E browser tests (we don't have a browser product in this repo).

---

## 1.8 Source links

- [tests/test_python_sdk.py](../../tests/test_python_sdk.py)
- [tests/test_databricks_job_runner.py](../../tests/test_databricks_job_runner.py)
- [tests/test_snowflake_job_runner.py](../../tests/test_snowflake_job_runner.py)
- [tests/test_credentials.py](../../tests/test_credentials.py)
- [tests/test_ratelimit.py](../../tests/test_ratelimit.py) (delete in Block A)
- [cloudmorph-mcp/.github/workflows/ci.yml](../../cloudmorph-mcp/.github/workflows/ci.yml) (replace in Block A)
