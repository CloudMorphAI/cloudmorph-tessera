# 04 — Snowflake Executor Audit (`snowflake/executor/`)

_911 LoC across [src/main.py](../../snowflake/executor/src/main.py) (403), [src/job_runner.py](../../snowflake/executor/src/job_runner.py) (326), [src/controlcenter_client.py](../../snowflake/executor/src/controlcenter_client.py) (173, byte-identical), [src/storage_pointers.py](../../snowflake/executor/src/storage_pointers.py) (9, byte-identical). Has tests: [tests/test_snowflake_job_runner.py](../../tests/test_snowflake_job_runner.py) (154 LoC, 9 cases)._

---

## 8.1 Why Snowflake matters strategically

Same logic as Databricks. Snowflake's agentic surface is **Cortex AI / Cortex Agents** — first-party LLM-powered analysis sitting on top of the customer's data. Governing what those agents can read is the same wedge: prevent PII exfiltration, enforce row-level access, cap warehouse credits.

Bonus: Snowflake's `QUERY_TAG` mechanism is the simplest cross-cloud governance hook in the entire product. Almost free to add.

---

## 8.2 Current state

### main.py (403 LoC)

Same lifecycle pattern. Same BaseExecutor extraction opportunity (cross/07).

### job_runner.py (326 LoC, 5 dispatch branches)

Read in full this pass. Structure:

- `:1-77` — imports (`snowflake-connector-python`), action/payload extract, resolvers for account/user/password/warehouse/role/database/schema. Same env-or-payload pattern.
- `:79-119` — `_get_connection(payload)` — supports password auth OR key-pair auth (private key from path on disk). **Sensible auth model.** Uses `cryptography` library for PEM parsing.
- `:122-131` — `_query` — DictCursor, lowercases keys.
- `:134-235` — five list helpers: `_list_databases`, `_list_warehouses`, `_list_schemas`, `_list_tables`, `_list_roles`. Each runs a `SHOW <thing>` and projects.
- `:238-262` — `_format_error`, `_build_result`. Standard.
- `:264-326` — `run(job)` dispatch.

**Same destructive-substring blocker** at `:272-277`:
```python
if "delete" in normalized or "remove" in normalized or "drop" in normalized:
    return _build_result(...)
```

Same brittleness as Databricks. Replace with policy engine.

**Action surface (per docs/getting-started.md):**
- `snowflake.account.list_databases`
- `snowflake.account.list_warehouses`
- `snowflake.database.list_schemas`
- `snowflake.schema.list_tables`
- `snowflake.account.list_roles`

5 documented, 5 dispatch branches, 100% coverage.

**Findings:**
- **P0:** Read-only `SHOW` actions only. Same product gap — agentic data access requires query execution interception.
- **P1:** Substring destructive blocker — replace with policy.
- **P1:** No retry on Snowflake errors. `snowflake.connector` exposes `RetryRequest` exception type — wrap.
- **P2:** Resolver pattern duplicated.
- **P1:** `_get_connection` opens a fresh connection per `run(job)` call. For one-shot mode that's fine; for long-running daemon mode that's wasteful (Snowflake connection setup is ~500ms-2s). Add a connection pool keyed on `(account, user, warehouse)`.

### controlcenter_client.py / storage_pointers.py

Same as others (byte-identical, extract).

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema snowflake-connector-python
...
```

Lean, only installs what it needs. **Better than the AWS/Azure/GCP Dockerfiles.** P2 minor: add `cryptography` explicitly (currently pulled in transitively by `snowflake-connector-python`; safer to declare).

### tests/test_snowflake_job_runner.py (154 LoC, 9 cases)

Covers:
- Resolvers (account/database/schema)
- Dispatch sanity (missing action, destructive at multiple verbs, unsupported)
- Five handler happy paths with mocked `_get_connection`
- Two required-arg paths (schemas requires database, tables requires schema)

**Coverage:** roughly 75% on `job_runner.py`. Good.

**Gaps:**
- No tests for key-pair auth path
- No tests for connection-error fallback
- No tests for `_format_error` Snowflake-specific branch
- No integration test against `snowflake-connector-python` mock or LocalSnow (which doesn't really exist)

---

## 8.3 Governance hooks

### 8.3.1 Query Tag injection — the cheap win

Snowflake's `QUERY_TAG` is a session parameter you can set per session or per query. Every query then carries the tag, queryable via `QUERY_HISTORY`:

```python
def inject_query_tag(conn, request_id, intent_id, policy_bundle_id):
    cursor = conn.cursor()
    tag = f"cloudmorph:request_id={request_id};intent={intent_id};bundle={policy_bundle_id}"
    cursor.execute(f"ALTER SESSION SET QUERY_TAG = '{tag}'")
    cursor.close()
```

Customer can then audit via:
```sql
SELECT query_text, query_tag, total_elapsed_time, credits_used_cloud_services
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE query_tag LIKE 'cloudmorph:%'
ORDER BY start_time DESC LIMIT 100;
```

**Effort:** 2h. **The single highest-leverage governance hook in the entire repo.** Block G — must ship.

### 8.3.2 `snowflake.sql.execute_query` action with policy gating

Same as Databricks §7.3.1 — agent sends raw SQL, we evaluate, mutate (with sqlglot), forward, redact response.

```python
def execute_query(payload):
    sql = payload["sql"]
    warehouse = payload.get("warehouse")
    
    # 1. Build PolicyInput, evaluate.
    decision = policy_engine.evaluate(...)
    
    if decision.outcome == "deny":
        return _build_result("failed", reason=decision.reason)
    
    if decision.outcome == "mutate":
        sql = decision.mutated_arguments["sql"]
    
    # 2. Inject Query Tag.
    inject_query_tag(conn, request_id, intent_id, bundle_id)
    
    # 3. Execute.
    rows = _query(conn, sql)
    
    # 4. Redact response.
    if decision.outcome == "redact":
        rows = redact_fields(rows, decision.redaction_fields)
    
    return _build_result("completed", "Query executed.", {"rows": rows})
```

**Effort:** 12h. Block G. (Less than Databricks because Snowflake's connector handles more lifecycle for us.)

### 8.3.3 Compile-to-Snowflake row-access policies

Compile bundle rules to Snowflake's native row-access policies:

```sql
CREATE ROW ACCESS POLICY cm_pii_policy AS (region VARCHAR) RETURNS BOOLEAN ->
  CASE
    WHEN current_role() IN ('CM_AUDIT_ROLE') THEN region IS NOT NULL  -- allow all rows
    WHEN current_role() IN ('CM_AGENT_READONLY') THEN region = 'US'   -- restricted
    ELSE FALSE
  END;

ALTER TABLE sales ADD ROW ACCESS POLICY cm_pii_policy ON (region);
```

The compile is rule-by-rule; each `read.list` rule with `scopeRegions: ["us"]` becomes a row-access policy. Defense in depth: even if Control Centre is bypassed, Snowflake itself enforces.

**Effort:** 16h. Post-MVP.

### 8.3.4 Warehouse credit estimation

Snowflake exposes `QUERY_HISTORY` patterns showing typical credit usage per query shape. Build a model: estimate credits based on join cardinality and warehouse size. Block queries above per-intent budget.

**Effort:** 8h. Post-MVP.

### 8.3.5 Cortex AI / Cortex Agents governance

Cortex Agents call user-defined functions and SQL. Same playbook as §8.3.2 — intercept the calls, evaluate, allow/mutate/deny. Effort 24h, post-MVP.

### 8.3.6 DML/DDL gating

Today: substring blocker says no. Better: explicit allow rules in the bundle. `INSERT INTO foo` allowed if intent `write.create` AND target `foo` in scope. `DROP TABLE foo` allowed if intent `write.delete` AND human approval granted.

This needs the policy engine to be live (Block E) — until then, the substring blocker stays as a fail-safe.

**Effort:** 4h once policy engine lands. Block G.

---

## 8.4 Tests (additional plan)

| Test | Coverage gap | Effort |
|---|---|---:|
| Key-pair auth path | _get_connection branch | 2h |
| Connection error fallback | _format_error branch | 1h |
| Query tag injection | (new in §8.3.1) | 2h |
| execute_query handler | (new in §8.3.2) | 6h |
| sqlglot mutate tests | (new in §8.3.2) | 4h |
| Connection pool reuse | (new optimization) | 2h |
| vcrpy or snowflake-mock recordings | full handler pass | 4h |

**Total: ~21h.**

---

## 8.5 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Extract controlcenter_client + storage_pointers | P0 | (cross/07) | C |
| BaseExecutor lifecycle | P0 | (cross/07) | C |
| **Query Tag injection (cheapest highest-impact hook)** | **P0** | 2h | G |
| `snowflake.sql.execute_query` with policy gating | P0 | 12h | G |
| Replace destructive-substring with policy engine | P1 | 4h | G |
| Connection pooling | P1 | 4h | G |
| Tests (~21h additional) | P1 | 21h | G+H |
| Compile-to-row-access-policy | P2 | 16h | post-MVP |
| Warehouse credit estimation | P2 | 8h | post-MVP |
| Cortex Agents governance | P2 | 24h | post-MVP |
| DML/DDL via policy engine (replaces substring fail-safe) | P1 | 4h | G (after E) |

**MVP critical-path: ~22h.** Query Tag injection alone is a 2h add that gives every query an audit trail. **Do it day 1.**

---

## 8.6 Source links

- [snowflake/executor/src/main.py](../../snowflake/executor/src/main.py)
- [snowflake/executor/src/job_runner.py](../../snowflake/executor/src/job_runner.py)
- [snowflake/executor/src/controlcenter_client.py](../../snowflake/executor/src/controlcenter_client.py)
- [snowflake/executor/src/storage_pointers.py](../../snowflake/executor/src/storage_pointers.py)
- [snowflake/executor/Dockerfile](../../snowflake/executor/Dockerfile)
- [tests/test_snowflake_job_runner.py](../../tests/test_snowflake_job_runner.py)
