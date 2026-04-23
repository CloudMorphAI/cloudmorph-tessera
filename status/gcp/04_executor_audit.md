# 04 — GCP Executor Audit (`gcp/executor/`)

_1,412 LoC across [src/main.py](../../gcp/executor/src/main.py) (470), [src/job_runner.py](../../gcp/executor/src/job_runner.py) (750), [src/controlcenter_client.py](../../gcp/executor/src/controlcenter_client.py) (173, byte-identical), [src/storage_pointers.py](../../gcp/executor/src/storage_pointers.py) (**19** — only file in the executors with extra content beyond the 9-line baseline). Zero tests._

---

## 6.1 Current state

### main.py (470 LoC)

Same shape as AWS/Azure main.py. Lifecycle, heartbeat, signal handling. Cloud-specific differences:
- Artifact upload uses `google.cloud.storage.Client`.
- Auth via Application Default Credentials (`google.auth.default()`) — clean, no env-var soup.

**Same finding:** belongs in `BaseExecutor`. Block C.

### job_runner.py (750 LoC, 20 dispatch branches)

From the partial read (`:1-100`):
- Imports: `google.auth`, `google.auth.transport.requests`, `google.cloud.storage`, `requests` (NOT `urllib` — finally uses a real HTTP client).
- `_resolve_project`, `_resolve_bucket`, `_resolve_prefix`, `_resolve_location`, `_resolve_zones`, `_resolve_max_results` — same env-or-payload pattern.
- `_resolve_location_target` — handles `"all"|"*"|"-"` as wildcard for global resources (clever).

**Action surface (per docs/getting-started.md):**
- `gcp.storage.list_buckets`
- `gcp.storage.list_objects`
- `gcp.compute.list_instances`
- `gcp.run.list_jobs`
- `gcp.run.list_services`
- `gcp.container.list_clusters`

6 documented, 20 dispatch branches → ~14 undocumented. Likely IAM (list_service_accounts, list_roles), BigQuery (list_datasets, list_tables), Pub/Sub (list_topics, list_subscriptions), Logging (list_sinks), Functions (list_functions), Eventarc (list_triggers).

**Findings:**
- **P0:** Same registry refactor. 20 branches → ~20 handlers in `gcp/executor/src/actions/`. Effort 10h.
- **P1:** Uses `requests` library — actually a P0 *good* finding compared to urllib (better retry, sessions, error types). Other clouds should follow.
- **P1:** Application Default Credentials pattern is clean — easier multi-tenant story than AWS/Azure (just inject SA key per-tenant via env or workload identity federation).

### storage_pointers.py (19 LoC) — the outlier

```python
def build_pointer(kind, uri, metadata=None):  # 9 LoC, identical to other clouds
    ...

def build_gcs_pointer(bucket, object_path, metadata=None):  # +10 LoC, GCP-specific
    object_path = object_path.lstrip("/")
    uri = f"gs://{bucket}/{object_path}"
    return build_pointer("gcs", uri, metadata)
```

The `build_gcs_pointer` helper is GCP-specific and stays in the GCP executor (or moves to `cloudmorph_common.gcp.storage_pointers`). The base `build_pointer` extracts to common.

**Findings:**
- **P1:** Add `build_s3_pointer` and `build_blob_pointer` helpers in their respective clouds for consistency. Tiny lift.

### Dockerfile

Same `python:3.11-slim` + `boto3 google-cloud-storage azure-storage-blob` install pattern → same bloat finding.

---

## 6.2 Governance hooks

### 6.2.1 Cloud Audit Logs trace correlation

Inject `goog-trace-id` header into every API call:

```python
from google.cloud import compute_v1
from google.api_core.client_options import ClientOptions
from google.api_core.gapic_v1.client_info import ClientInfo

client = compute_v1.InstancesClient(
    client_info=ClientInfo(user_agent=f"cloudmorph/0.1 request_id={request_id}"),
)
```

Or use OpenTelemetry tracing — the GCP SDKs auto-propagate trace context if you wrap calls in `with tracer.start_as_current_span("cm.action.aws.s3.list_buckets") as span: span.set_attribute("cloudmorph.request_id", request_id) ...`. Cloud Trace then ties spans to `requestId`.

**Effort:** 6h. Block G.

### 6.2.2 Eventarc emitter

Mirror decisions to a customer-owned Eventarc trigger or Pub/Sub topic:

```python
from google.cloud import pubsub_v1
publisher = pubsub_v1.PublisherClient()
topic = f"projects/{customer_project}/topics/{customer_topic}"
publisher.publish(topic, json.dumps(audit_event).encode(), source="cloudmorph", type="decision")
```

**Effort:** 4h. Block G.

### 6.2.3 Compile-to-Org-Policy + IAM Conditions

Generate Org Policy constraints + IAM Conditions from the bundle:

```python
def compile_bundle_to_org_policy(bundle: PolicyBundle):
    """Translate to GCP Org Policy custom constraints."""
    constraints = []
    for rule in bundle.rules:
        if rule.outcome == "deny" and rule.action.startswith("gcp."):
            constraints.append({
                "name": f"customConstraints/cloudmorph-{rule.id}",
                "displayName": rule.description,
                "condition": rego_to_cel(rule),  # translation step
                "actionType": "DENY",
                "methodTypes": ["CREATE", "UPDATE"],
            })
    return constraints
```

GCP's CEL (Common Expression Language) is closer to Rego than IAM JSON, so the translation is more tractable.

**Effort:** 16h. Post-MVP.

### 6.2.4 Security Command Center finding emission

When `intent_mismatch` fires:

```python
from google.cloud import securitycenter_v1
client = securitycenter_v1.SecurityCenterClient()
client.create_finding(
    parent=f"organizations/{org_id}/sources/{source_id}",
    finding_id=f"cm-{decision_id}",
    finding={
        "category": "CLOUDMORPH_INTENT_MISMATCH",
        "severity": "MEDIUM",
        "source_properties": { "intentId": intent_id, "attemptedAction": ... },
    }
)
```

**Effort:** 12h. Post-MVP.

---

## 6.3 Tests

Same plan as AWS/Azure: ~34h to reach 60% coverage. Use `pytest-mock` + `google.cloud.*` mocks. Or `vcrpy` for replay testing against the real APIs (capture once, replay forever).

---

## 6.4 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Extract controlcenter_client.py + storage_pointers (base) | P0 | (cross/07) | C |
| Registry refactor of job_runner.py (20 branches) | P0 | 10h | G |
| Tests (~34h) | P0 | 34h | G+H |
| Cloud Audit Logs trace correlation | P0 | 6h | G |
| Eventarc / Pub/Sub emitter | P1 | 4h | G |
| Dockerfile harden | P1 | 4h | H |
| Compile-to-Org-Policy | P2 | 16h | post-MVP |
| Security Command Center finding emission | P2 | 12h | post-MVP |
| Workload Identity Federation for multi-tenant | P2 | 8h | post-MVP |

---

## 6.5 Source links

- [gcp/executor/src/main.py](../../gcp/executor/src/main.py)
- [gcp/executor/src/job_runner.py](../../gcp/executor/src/job_runner.py)
- [gcp/executor/src/controlcenter_client.py](../../gcp/executor/src/controlcenter_client.py)
- [gcp/executor/src/storage_pointers.py](../../gcp/executor/src/storage_pointers.py)
- [gcp/executor/Dockerfile](../../gcp/executor/Dockerfile)
