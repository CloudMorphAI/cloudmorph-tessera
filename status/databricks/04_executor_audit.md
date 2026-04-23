# 04 — Databricks Executor Audit (`databricks/executor/`)

_901 LoC across [src/main.py](../../databricks/executor/src/main.py) (403), [src/job_runner.py](../../databricks/executor/src/job_runner.py) (316), [src/controlcenter_client.py](../../databricks/executor/src/controlcenter_client.py) (173, byte-identical), [src/storage_pointers.py](../../databricks/executor/src/storage_pointers.py) (9, byte-identical). Has tests: [tests/test_databricks_job_runner.py](../../tests/test_databricks_job_runner.py) (174 LoC, 11 cases)._

---

## 7.1 Why Databricks matters strategically

The cloud executors (AWS/Azure/GCP) are governance for *infra* actions. Databricks is governance for **agentic data access** — much higher ROI per integration. An agent doing analysis in Databricks (Mosaic AI / Genie / SQL bot) is the highest-stakes firewall target in the product:
- Queries can pull millions of rows of PII
- Queries can spend tens of dollars per minute on warehouse credits
- Notebook execution can run arbitrary Python

A working Databricks integration means: "you can let your agent into your data warehouse; we'll narrow what it sees."

---

## 7.2 Current state

### main.py (403 LoC)

Same lifecycle pattern as the cloud executors. Differences:
- No `_upload_artifacts` — Databricks executor doesn't write S3 artifacts (probably writes back to Databricks workspace storage instead, but not visible in the lifecycle path read).

**Same finding:** lifecycle to BaseExecutor (cross/07).

### job_runner.py (316 LoC, 6 dispatch branches)

Read in full this pass. Walking it:

- `:1-89` — imports, action/payload extraction, host/token/max_results/page_token resolvers, `_databricks_get` HTTP helper. Uses `urllib` (not `requests`) — same finding as Azure (P1).
- `:91-200` — six list helpers: `_list_clusters`, `_list_jobs`, `_list_notebooks`, `_list_sql_warehouses`, `_list_catalogs`, `_list_schemas`. Each calls a specific Databricks REST API endpoint and projects the response to a normalized shape.
- `:227-251` — `_format_error`, `_build_result`. Standard.
- `:253-316` — `run(job)` dispatch.

**Crucial line:** `:261-266` —
```python
if "delete" in normalized or "remove" in normalized or "drop" in normalized:
    return _build_result(
        "failed",
        "Destructive actions are not supported by this executor.",
        reason="destructive_action_not_supported",
    )
```

This is **lexical** destructive-action detection. It blocks anything containing the substrings "delete", "remove", or "drop". This is a fail-safe — but a weak one: an action named `databricks.workspace.consume_credits` slips through; an action named `databricks.unity_catalog.read_dropped_table_history` (legitimate metadata read) is incorrectly blocked. Should be policy-engine-driven, not substring-matched. P1.

**Action surface (per docs/getting-started.md):**
- `databricks.workspace.list_clusters`
- `databricks.workspace.list_jobs`
- `databricks.workspace.list_notebooks`
- `databricks.sql.list_warehouses`
- `databricks.unity_catalog.list_catalogs`
- `databricks.unity_catalog.list_schemas`

6 documented, 6 dispatch branches, 100% coverage. This executor is the most honest about its scope.

**Findings:**
- **P0:** All actions today are read-only listing. The product value (governing agentic data access) requires query execution interception — not in this executor. See §7.3.
- **P1:** Substring destructive-action blocker is brittle. Replace with policy-engine.
- **P1:** `urllib` vs `requests` (same as Azure).
- **P1:** No retry on `databricks_api_error` — single-shot.
- **P2:** Resolvers (`_resolve_host`, `_resolve_token`, etc.) repeat for-loop pattern — could be a parameterized helper.

### controlcenter_client.py / storage_pointers.py / Dockerfile

Same as other executors. Dockerfile correctly only installs `jsonschema requests` — minimal. **Best Dockerfile of the five.**

### tests/test_databricks_job_runner.py (174 LoC, 11 cases)

Covers:
- Resolvers (host with/without https, host from env, token from payload, max_results)
- Dispatch sanity (missing action, destructive blocked at multiple substrings, unsupported action, missing host)
- Six handler happy paths (list_clusters, list_jobs, list_notebooks, list_sql_warehouses, list_catalogs, list_schemas) with mocked `_databricks_get`
- One required-arg path (list_schemas requires catalog)

**Coverage:** roughly 80% on `job_runner.py`. **Best-tested executor** in the repo.

**Gaps:**
- No tests for `_format_error` paths (URL error vs HTTP error)
- No tests for pagination (`page_token` flow)
- No tests for `_build_result` error variants
- No tests against an actual Databricks emulator (none exists; closest is `databricks-sql-cli` mock or `vcrpy` recordings)

---

## 7.3 Governance hooks — the data platform play

Listing endpoints are table stakes. The actual product is governance of **the Databricks surfaces agents touch**: SQL Warehouse, Notebooks, Unity Catalog row/column policies, Mosaic AI / Genie.

### 7.3.1 Query interception at SQL Warehouse layer

**The killer integration.** Two paths:

**Path A — JDBC proxy (deeper, harder).** Stand up a JDBC proxy that intercepts every SQL query an agent submits, evaluates against policy, optionally rewrites (mutate), forwards to the real warehouse, optionally redacts the response. Effort: 40-80h to v1. Post-MVP unless a specific design partner asks.

**Path B — REST interceptor (shallower, ships fast).** Use Databricks' SQL Warehouse REST API; agent sends queries through us instead of directly. Add `databricks.sql.execute_query` action that:
1. Receives raw SQL from agent.
2. Builds `ToolCallRequest{action: "databricks.sql.execute_query", arguments: {sql: ..., warehouse_id: ...}}`.
3. Policy engine evaluates — can `mutate` (rewrite SQL with row/column filters), `deny`, `approve`, `redact` (filter response).
4. On allow/mutate, forwards to `POST /api/2.0/sql/statements`.
5. Polls until done (or returns `statementId` for async).
6. Filters response per `redact` rules.

**Effort:** 16h. Block G — MVP fits this.

The hard problem inside: **SQL parsing for `mutate` rules.** Use `sqlglot` (Python) for parse/rewrite. Adding "WHERE region = 'us'" to "SELECT * FROM sales" is sqlglot-easy; rewriting subqueries to enforce row-level access is harder. Start with limit-only mutate (`SELECT * FROM x → SELECT * FROM x LIMIT 10000`).

### 7.3.2 Unity Catalog row/column policy compile-from-intent

When intent is `read.aggregate "summarize sales"`, automatically:
1. Detect tables in the SQL.
2. Check Unity Catalog metadata — does the table have row-level filters tied to roles?
3. If not, transparently rewrite the query to use an aggregate view (`SELECT SUM(amount) FROM sales` instead of `SELECT * FROM sales`).
4. Or: deny with a clear error "intent declares aggregation; query reads rows. Either re-declare intent or rewrite query."

**Effort:** 24h. Post-MVP — depends on §7.3.1.

### 7.3.3 Cost policy via `EXPLAIN COST`

Databricks exposes query cost estimates via `EXPLAIN COST <query>`. Policy can require cost < $X:

```python
def estimate_cost(sql: str, warehouse_id: str) -> float:
    explain_result = databricks_sql.execute(f"EXPLAIN COST {sql}", warehouse_id)
    # Parse the cost from the EXPLAIN output (warehouse-credits-based)
    return parse_cost(explain_result)
```

Block queries where `estimate > intent.constraints.maxCostUsd` or `estimate > tenant.policy.defaultMaxCostUsd`.

**Effort:** 12h. Post-MVP.

### 7.3.4 Notebook policy via dbutils interception

Hardest of all. Agent-launched notebooks call `dbutils.fs.ls(...)`, `dbutils.secrets.get(...)`, `dbutils.notebook.run(...)`. Intercepting these requires either:
- **Init-script-based wrapper:** ship a custom init script that monkey-patches `dbutils.*` with policy-aware wrappers. Brittle but works.
- **Runtime env-var injection:** set `CLOUDMORPH_REQUEST_ID`, `CLOUDMORPH_INTENT_ID` as env vars in the cluster, then provide a `cloudmorph-dbutils` Python package the agent imports for governed access. Less powerful but cleaner.

**Effort:** 40h. Post-MVP.

### 7.3.5 Workflow / Job run governance

Agent-launched Databricks Jobs (`POST /api/2.1/jobs/run-now`) need session tagging. Inject `tags: { "cloudmorph_request_id": ..., "cloudmorph_intent_id": ... }` into job runs. Then queryable via `GET /api/2.1/jobs/runs/list?tags=...`.

**Effort:** 4h. Block G.

### 7.3.6 Mosaic AI / Genie governance

Mosaic AI Agent and Genie are Databricks' first-party agentic surfaces. They issue tool calls to look up tables, write SQL, run notebooks. Each issues HTTP calls to a Databricks-internal endpoint. Same playbook as §7.3.1 — intercept, evaluate, allow/mutate/deny.

**Effort:** 32h. Post-MVP (depends on §7.3.1, §7.3.4).

---

## 7.4 Tests (additional plan)

| Test | Coverage gap | Effort |
|---|---|---:|
| Add error-path tests | _format_error, build_result error branches | 2h |
| Add pagination tests | page_token flow | 2h |
| Add SQL execute_query handler | (new in §7.3.1) | 6h |
| Add SQL parse-mutate tests using sqlglot | (new in §7.3.1) | 6h |
| Add cost-estimate tests | (new in §7.3.3) | 4h |
| Add session-tag injection tests | (new in §7.3.5) | 2h |
| Replace destructive-substring test with policy-engine test | (after §7.2 fix) | 2h |
| Integration test against Databricks via vcrpy | full handler pass | 6h |

**Total: ~30h.**

---

## 7.5 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Extract controlcenter_client + storage_pointers | P0 | (cross/07) | C |
| BaseExecutor lifecycle | P0 | (cross/07) | C |
| Replace destructive-substring blocker with policy engine | P1 | 2h | G |
| `databricks.sql.execute_query` action with SQL parse/mutate | **P0** (MVP wedge) | 16h | G |
| Workflow run session tagging | P1 | 4h | G |
| `urllib → requests` consolidation | P1 | 3h | G |
| Tests (~30h additional) | P1 | 30h | G+H |
| Dockerfile minor (already lean) | P2 | 1h | H |
| Unity Catalog row-policy compile | P2 | 24h | post-MVP |
| Cost policy via EXPLAIN COST | P2 | 12h | post-MVP |
| Notebook governance (dbutils interception) | P2 | 40h | post-MVP |
| Mosaic AI / Genie governance | P2 | 32h | post-MVP |

**MVP critical-path: ~25h.** The SQL execute_query handler is the unlock — once it exists, the data-platform value prop is demonstrable to design partners.

---

## 7.6 Source links

- [databricks/executor/src/main.py](../../databricks/executor/src/main.py)
- [databricks/executor/src/job_runner.py](../../databricks/executor/src/job_runner.py)
- [databricks/executor/src/controlcenter_client.py](../../databricks/executor/src/controlcenter_client.py)
- [databricks/executor/src/storage_pointers.py](../../databricks/executor/src/storage_pointers.py)
- [databricks/executor/Dockerfile](../../databricks/executor/Dockerfile)
- [tests/test_databricks_job_runner.py](../../tests/test_databricks_job_runner.py)
