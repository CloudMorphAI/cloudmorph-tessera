# 04 — AWS Executor Audit (`aws/executor/`)

_The most mature executor: 1,704 LoC across [src/main.py](../../aws/executor/src/main.py) (456), [src/job_runner.py](../../aws/executor/src/job_runner.py) (**1,066** — the worst single source file), [src/controlcenter_client.py](../../aws/executor/src/controlcenter_client.py) (173, byte-identical with 4 other clouds), [src/storage_pointers.py](../../aws/executor/src/storage_pointers.py) (9). Zero tests._

---

## 4.1 Current state

### main.py (456 LoC)

Lifecycle. Walking the file:

- `:1-37` — imports, including a `try: import boto3` guard that emits a structured JSON log on failure and re-raises. Good fail-fast pattern.
- `:39-72` — JSON-line logger keyed on level; severity map for Cloud Logging compatibility (`DEBUG/INFO/WARNING/ERROR/DEFAULT`); `_redact()` masks tokens to `xxxx...yyyy`; `_SAFE_PAYLOAD_KEYS` allowlist for log-emission of payload subsets. Sensible.
- `:75-100` — `_summarize_job(job)` extracts logged fields from a job — bounds payload key count to 20, samples payload values from the safe list. Good for not blowing up logs with raw user input.
- `:103-117` — `_require_env`, `_float_env` helpers.
- `:120-143` — Loads `contracts/job.schema.json` from a path computed by walking up 3 dirs (or env override). Validates job against schema if present and required fields are populated. **Uses an ImportError-shielded jsonschema import (`:32-36`)** — falls back to no-op if jsonschema isn't installed. So validation is best-effort, not guaranteed.
- `:146-158` — `_heartbeat_loop` — threading-based heartbeat with `Event.wait()`. Bounded; logs error but continues.
- `:160-161` — `_sleep_with_backoff(current, jitter)` — `time.sleep(current + uniform(0, jitter))`.
- `:164-203` — `_upload_artifacts(job_id, result, logs)` — provider-aware (AWS = S3); writes summary.txt, result.json, logs.jsonl to `s3://${STORAGE_BUCKET}/${ARTIFACT_BASE_PREFIX or "controlcentre/jobs/<jobId>"}`. Aggregates errors and re-raises if any partial failure. **AWS-specific**, won't be reused by other clouds — should move into a per-cloud `ArtifactWriter` interface in `cloudmorph-common-py`.
- `:206-234` — `_output_meta` — builds redacted log payload describing the result. Useful for observability; should be in common.
- `:237-456` — `main()`:
  - Logs startup with hostname, pid, python version.
  - Reads required env: `CONTROL_CENTER_API_URL`, `CONTROL_CENTER_EXECUTOR_TOKEN`, `CONTROL_CENTER_TENANT_ID`, `CONTROL_CENTER_ACCOUNT_ID`. Logs missing if any.
  - Reads `CONTROL_CENTER_CAPABILITIES` (default `agent.run`), `EXECUTOR_ID` (default hostname).
  - Reads heartbeat / poll backoff config.
  - Validates against the loaded schema.
  - **Two execution modes:**
    1. **One-shot** — if `JOB_ID` and `JOB_TOKEN` env are set, fetches the job, runs once, posts complete, exits. Used by ECS-task-per-job deployments.
    2. **Long-running** — claim/run/heartbeat/complete loop with exponential backoff. Default for daemon deployments.
  - Graceful shutdown via SIGTERM/SIGINT handlers setting `shutdown_event`.

**Findings (main.py):**
- **P1:** ~70% of `main.py` is structurally identical to the other 4 executors' `main.py` files. The lifecycle (claim/heartbeat/complete + signal handling + JSON logging + redaction) belongs in `cloudmorph_common.BaseExecutor`. Each cloud's `main.py` should be ~30 LoC: env validation + executor instantiation + `executor.run()`.
- **P1:** `_upload_artifacts` is AWS S3-specific; lives next to AWS. The cross-cloud abstraction is `ArtifactWriter`; each cloud subclasses (`S3ArtifactWriter`, `GcsArtifactWriter`, `BlobArtifactWriter`, `LocalArtifactWriter`).
- **P1:** Heartbeat thread leaks if `_heartbeat_loop` raises an unexpected exception (no try/except around the body). Add structured handling.
- **P2:** Schema validation is silently no-op when jsonschema isn't installed. Make it required.
- **P2:** Backoff is fixed exponential with no max retry count — so a long upstream outage means the executor backs off to `POLL_MAX_SECONDS=15` and polls forever. Acceptable but undocumented.

### job_runner.py (1,066 LoC) — the giant

A flat function with 36 `if/elif` branches dispatching by `normalized` action name. From the partial read of `:1-220`, the pattern is consistent: per-action helper functions (`_list_instances`, `_list_ecs_clusters`, `_list_ecs_services`, `_clusters_for_regions`, ...) that resolve region(s) from payload-or-env, paginate the AWS API, return `{count, items, ...}` shape.

**Action surface (estimated from grep + the visible code):**

Documented in `docs/getting-started.md`:
- `aws.s3.list_buckets`
- `aws.s3.list_objects`
- `aws.ec2.list_instances`
- `aws.ecs.list_clusters`
- `aws.ecs.list_services`
- `aws.ecs.list_tasks`

The 1,066 LoC and 36 dispatch branches imply substantially more than 6 actions are wired — likely IAM (list_users, list_roles, list_groups), Lambda (list_functions), CloudFormation (list_stacks), CloudWatch (list_alarms), VPC (list_vpcs, list_subnets, list_security_groups), RDS (list_db_instances), Secrets Manager (list_secrets), ELB (list_load_balancers), and more. Confirm by re-reading the file when you're in the registry refactor — the dispatch chain is the source of truth.

**Findings (job_runner.py):**
- **P0:** 1,066 LoC, single file, 36 branches, single function — the "registry refactor" target. Plan:
  ```
  aws/executor/src/actions/
    __init__.py                 # registry: ACTIONS = {"aws.s3.list_buckets": list_buckets_handler, ...}
    s3.py                       # list_buckets, list_objects
    ec2.py                      # list_instances, ...
    ecs.py                      # list_clusters, list_services, list_tasks
    iam.py
    lambda_.py                  # list_functions, ...
    cloudformation.py
    cloudwatch.py
    vpc.py
    rds.py
    secretsmanager.py
    elb.py
    ...
  aws/executor/src/job_runner.py   # 30 LoC: dispatch via ACTIONS dict
  ```
  Each handler takes `(payload, region_resolver, region_list_resolver) → result_dict` and is independently unit-testable. **Effort: 16h** (8h to extract, 4h to write the test harness with mocked boto3, 4h to fix things that break).
- **P0:** Zero tests today. Block H must add at least:
  - A handler-registry sanity test (every documented action resolves)
  - One mocked-boto3 test per handler family (s3, ec2, ecs at minimum) using `moto` or `botocore.stub.Stubber`. **Target: 60% coverage.** Effort 16h.
- **P1:** No retry/backoff on AWS API calls. A throttled `DescribeInstances` will surface as a `ClientError` and fail the job. Wrap calls with `tenacity` or boto3's built-in `retry: { mode: adaptive, max_attempts: 5 }` config.
- **P1:** No pagination cap by default for some helpers. `_list_instances` does honor `max_results` (`:127-128`) but the loop uses three nested `if len(...) >= max_results: break` — fragile. Refactor to use `itertools.islice` over the paginator's pages.
- **P2:** No support for assume-role per-tenant. The executor uses default boto3 credentials chain — fine for single-tenant BYOC; for multi-tenant SaaS executor (post-MVP), need `STS.AssumeRole` per-tenant.

### controlcenter_client.py (173 LoC)

**Byte-identical to azure/gcp/databricks/snowflake.** Detail in [../cross/07_common_layer_audit.md](../cross/07_common_layer_audit.md). Move to `cloudmorph_common.ControlCenterClient`.

### storage_pointers.py (9 LoC)

**Byte-identical to azure/databricks/snowflake.** Move to `cloudmorph_common.storage_pointers`.

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema boto3 google-cloud-storage azure-storage-blob
COPY contracts ./contracts
COPY aws/executor ./aws/executor
WORKDIR /app/aws/executor
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/main.py"]
```

**Findings:**
- **P1:** Installs `boto3` AND `google-cloud-storage` AND `azure-storage-blob` — only `boto3` is needed for AWS. **Bloats the image by ~80 MB.** Likely a copy-paste from a single shared "all clouds" base. Should be `boto3` only.
- **P1:** Runs as root.
- **P1:** No HEALTHCHECK.
- **P1:** No multi-stage build (drops build deps from runtime layer).
- **P1:** No SBOM, no Trivy scan.
- **P2:** No `LABEL`s.

---

## 4.2 Action handler inventory (need to enumerate, this pass partial)

For the registry refactor, every handler needs to be classified:

| Verb category | Risk class | Handlers (partial — finish in Block C) |
|---|---|---|
| `read.list` | low | s3.list_buckets, s3.list_objects, ec2.list_instances, ecs.list_*, iam.list_*, lambda_.list_*, vpc.list_*, rds.list_*, ... |
| `read.describe` | low | (typically paired with list — `describe_instances`, `describe_volumes`, ...) |
| `read.get` | low | iam.get_user, secrets.get_secret_metadata (NOT value), ... |
| `read.aggregate` | low | (none visible yet — could add `cost.get_summary`) |
| `write.create` | medium | (none today; future scope) |
| `write.update` | high | (none today) |
| `write.delete` | **destructive** | (none today; the AWS executor explicitly does not support delete) |
| `execute.run` | medium | ecs.run_task (?), lambda_.invoke (?) |
| `execute.deploy` | high | (none today) |

**Rule of thumb in the policy bundle:** all `read.*` allow by default for `Read-First` permission pack. All `write.*` and `execute.*` require explicit allow rule + intent that includes the matching verb. All `delete.*` require approval.

---

## 4.3 Governance hooks (the new value-add)

The executor today RUNS actions. Target: also EMIT cloud-native enforcement signals so even direct cloud calls (bypassing MCP) leave a record — and ideally are blockable.

### 4.3.1 IAM session tagging

Wrap every boto3 `Session` with `aws_session_token` derived from `STS.AssumeRole` with session tags:

```python
sts = boto3.client("sts")
resp = sts.assume_role(
    RoleArn=os.environ["CONTROL_CENTER_EXECUTOR_ROLE_ARN"],
    RoleSessionName=f"cm-{job_id[:32]}",
    Tags=[
        {"Key": "cloudmorph:request_id", "Value": request_id},
        {"Key": "cloudmorph:intent_id", "Value": intent_id or "none"},
        {"Key": "cloudmorph:policy_bundle", "Value": policy_bundle_id},
        {"Key": "cloudmorph:tenant", "Value": tenant_id},
    ],
)
session = boto3.Session(
    aws_access_key_id=resp["Credentials"]["AccessKeyId"],
    aws_secret_access_key=resp["Credentials"]["SecretAccessKey"],
    aws_session_token=resp["Credentials"]["SessionToken"],
)
```

Every API call from `session` — and CloudTrail row it produces — has these as principal session tags. Customers can write CloudTrail/EventBridge filters on `userIdentity.sessionContext.sessionIssuer.principalId` joined with tag values.

**Effort:** 6h. Block G. Requires customer to deploy a role with `sts:AssumeRole` from the executor's principal — provide a CloudFormation template.

### 4.3.2 EventBridge emitter

Mirror every decision (and every executor lifecycle event) to a customer-owned EventBridge bus:

```python
events = boto3.client("events")
events.put_events(Entries=[{
    "Source": "cloudmorph",
    "DetailType": "decision",
    "Detail": json.dumps(audit_event),
    "EventBusName": os.environ["CUSTOMER_EVENT_BUS_NAME"],
}])
```

Customer can build Lambda/Step Functions/Security Hub workflows downstream.

**Effort:** 4h. Block G.

### 4.3.3 CloudTrail correlation

Done by §4.3.1 if session-tagged. Verify by spot-checking that `eventSource` rows show the session tags in `userIdentity.sessionContext`.

**Effort:** 2h verification. Block G.

### 4.3.4 Permission-boundary compilation (compile-to-IAM)

Generate an IAM permission boundary from the active policy bundle:

```python
def compile_bundle_to_permission_boundary(bundle: PolicyBundle) -> Dict:
    """Translate Rego rules into IAM Allow/Deny statements."""
    statements = []
    for rule in bundle.rules:
        if rule.outcome == "allow" and rule.action.startswith("aws."):
            iam_action = rule.action.replace("aws.", "").replace(".", ":").replace("_", "")
            statements.append({"Effect": "Allow", "Action": iam_action, "Resource": "*"})
        elif rule.outcome == "deny" and rule.action.startswith("aws."):
            ...
    return {"Version": "2012-10-17", "Statement": statements}
```

Apply the boundary to the executor's IAM role. The boundary is the *worst-case* effective permissions — if an action is `Allow`'d by both the role and the boundary, it's allowed; if either denies, it's denied.

This is the "defense in depth" pitch: **even if Control Centre is bypassed, AWS itself enforces the policy.**

**Effort:** 16h. Post-MVP (Block H+).

### 4.3.5 AWS Config rule emission for declared intents

When `cloudmorph_declare_intent` fires, emit a Config custom rule that audits whether subsequent CloudTrail events match the intent's verb scope. Customer's Config dashboard then shows compliance.

**Effort:** 12h. Post-MVP.

---

## 4.4 Tests

Today: zero. Plan:

| Test file | What | Effort |
|---|---|---:|
| `tests/test_aws_main.py` | Lifecycle: claim/heartbeat/complete with mocked ControlCenterClient | 4h |
| `tests/test_aws_action_registry.py` | Every documented action resolves to a handler, registry has no duplicates | 1h |
| `tests/test_aws_handlers_s3.py` | `moto`-based `list_buckets`, `list_objects` happy path + error cases | 3h |
| `tests/test_aws_handlers_ec2.py` | Same for ec2.list_instances + multi-region | 4h |
| `tests/test_aws_handlers_ecs.py` | Same for ecs.* | 4h |
| `tests/test_aws_handlers_iam.py` | Same for iam.* | 3h |
| `tests/test_aws_handlers_lambda.py` | Same for lambda.* | 2h |
| `tests/test_aws_handlers_vpc.py` | Same for vpc.* | 3h |
| `tests/test_aws_handlers_rds.py` | Same for rds.* | 2h |
| `tests/test_aws_governance_session_tagging.py` | Mocked STS, verify session tags propagate | 3h |
| `tests/test_aws_governance_eventbridge.py` | Mocked events, verify decision payloads | 2h |
| `tests/test_aws_artifact_upload.py` | Mocked S3, verify summary/result/logs uploaded | 3h |

**Total: ~34h.** Coverage target 60% (cloud SDK mocking is intrinsically painful — integration tests bridge the gap, run nightly against LocalStack).

---

## 4.5 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Extract controlcenter_client.py to common-py | P0 | (in cross/07) | C |
| Extract main.py lifecycle to BaseExecutor | P0 | (in cross/07) | C |
| Extract storage_pointers.py to common-py | P1 | (in cross/07) | C |
| Registry refactor of job_runner.py (36 branches) | P0 | 16h | G |
| Add tests (~34h spread across handler families) | P0 | 34h | G+H |
| IAM session tagging | P0 | 6h | G |
| EventBridge emitter | P1 | 4h | G |
| CloudTrail correlation verification | P1 | 2h | G |
| Dockerfile: drop GCS/Blob deps, run as non-root, add HEALTHCHECK, multi-stage | P1 | 4h | H |
| Retry/backoff on AWS API calls | P1 | 3h | G |
| jsonschema required (not optional import) | P2 | 1h | C |
| Permission-boundary compilation (compile-to-IAM) | P2 | 16h | post-MVP |
| AWS Config rule emission for intents | P2 | 12h | post-MVP |
| Multi-tenant assume-role per-tenant | P2 | 8h | post-MVP |

**MVP work in this audit: ~70h.** The registry refactor + tests take half. Could be deferred to Block G/H if 14-day MVP is too tight — minimum viable AWS executor for design-partner demo is "just don't break what's there + add session tagging" which is ~10h.

---

## 4.6 Source links

- [aws/executor/src/main.py](../../aws/executor/src/main.py)
- [aws/executor/src/job_runner.py](../../aws/executor/src/job_runner.py)
- [aws/executor/src/controlcenter_client.py](../../aws/executor/src/controlcenter_client.py)
- [aws/executor/src/storage_pointers.py](../../aws/executor/src/storage_pointers.py)
- [aws/executor/Dockerfile](../../aws/executor/Dockerfile)

Implementation: Block C (extract) → G (governance hooks + registry refactor) → H (tests + Docker hardening).
