# 04 — Azure Executor Audit (`azure/executor/`)

_1,578 LoC across [src/main.py](../../azure/executor/src/main.py) (469), [src/job_runner.py](../../azure/executor/src/job_runner.py) (927), [src/controlcenter_client.py](../../azure/executor/src/controlcenter_client.py) (173, byte-identical), [src/storage_pointers.py](../../azure/executor/src/storage_pointers.py) (9, byte-identical). Zero tests._

---

## 5.1 Current state

### main.py (469 LoC)

Same shape as AWS main.py with minor cloud-specific differences (auth env vars, Azure Blob upload instead of S3). Same lifecycle, same heartbeat thread, same JSON logging, same env-or-payload resolver pattern. **Confirms the BaseExecutor refactor opportunity.**

The Azure version differs in:
- Artifact upload uses `azure.storage.blob` instead of `boto3.client("s3")`.
- `STORAGE_PROVIDER` defaults to `"azure"` and the `_upload_artifacts` short-circuits if not `"azure"`.
- Auth env: `STORAGE_CONNECTION_STRING` or `AZURE_STORAGE_CONNECTION_STRING` or `STORAGE_SAS_TOKEN` or `AZURE_STORAGE_ACCOUNT_KEY`.

**Findings:**
- **P0 (cross-cutting):** Same as AWS — main.py belongs in `cloudmorph_common.BaseExecutor`. Each cloud's main.py becomes ~30 LoC.
- **P1:** `_upload_artifacts` for Azure should subclass `BlobArtifactWriter`. Implementation visible at `azure/executor/src/main.py:165-...` (not fully read this pass — confirm in Block C).

### job_runner.py (927 LoC, 17 dispatch branches)

Walking the partial read (`:1-150`):
- `:14-22` — declares 7 Azure REST API version constants: `CONTAINER_APPS_API_VERSION = "2023-05-01"`, `COMPUTE_API_VERSION = "2023-07-01"`, etc. **Hard-coded API versions** are stale within a year. P1 maintenance burden.
- `:25-32` — `_extract_action`, `_extract_payload` — same pattern.
- `:35-138` — Resolvers: `_resolve_account`, `_resolve_subscription`, `_resolve_resource_group`, `_resolve_container`, `_resolve_prefix`, `_resolve_max_results`, `_resolve_connection_string`, `_resolve_sas_token`, `_resolve_account_key`, `_resolve_access_token`. Each follows the same env-or-payload-with-aliases pattern. Should be one parameterized helper in common-py.
- `:140-143` — `_format_error` — handles `AzureError` then falls through. Clean.
- `:146-...` — `_request_json(url, token)` — direct urllib REST call to Azure Management API. **Note this is alongside the SDK-based `BlobServiceClient` import** — Azure executor has TWO different ways of calling Azure (urllib for management plane, SDK for blob). Should consolidate to SDK everywhere (better retry, telemetry, error types).

**Action surface (per docs/getting-started.md):**
- `azure.blob.list_containers`
- `azure.blob.list_blobs`
- `azure.compute.list_vms`
- `azure.containerapps.list_apps`
- `azure.containerapps.list_jobs`

5 documented, 17 dispatch branches → ~12 undocumented or partially-documented actions. Likely covers AKS, SQL DB, KeyVault (per the API version constants), App Services (Websites), Authorization (RBAC).

**Findings (job_runner.py):**
- **P0:** Same registry refactor needed. 17 branches → ~17 handlers. Plan:
  ```
  azure/executor/src/actions/
    __init__.py           # ACTIONS = {...}
    blob.py
    compute.py
    containerapps.py
    aks.py
    sql.py
    keyvault.py
    websites.py
    authorization.py
  ```
  Effort 10h (less than AWS — fewer branches).
- **P1:** Hard-coded API versions (`CONTAINER_APPS_API_VERSION = "2023-05-01"`, etc.). Move to `azure/executor/src/api_versions.py` so updates are one-file. Long-term: stop using urllib + raw REST and use `azure-mgmt-*` SDKs which manage API versions internally.
- **P1:** Mixed urllib + SDK pattern. Consolidate to SDK.
- **P1:** No retry logic on management-plane REST calls. `_request_json` does a single shot.
- **P2:** Multiple credential sources (`connection_string` OR `sas_token` OR `account_key` OR `access_token`) — confusing precedence; document.

### controlcenter_client.py / storage_pointers.py / Dockerfile

Same as AWS:
- controlcenter_client.py — byte-identical, extract to common-py.
- storage_pointers.py — byte-identical, extract.
- Dockerfile — also installs `boto3` AND `google-cloud-storage` AND `azure-storage-blob` despite only Azure libs being needed. Same bloat issue.

---

## 5.2 Action handler inventory (partial)

| Verb category | Risk | Likely handlers |
|---|---|---|
| `read.list` | low | blob.list_containers/blobs, compute.list_vms, containerapps.list_apps/jobs, aks.list_clusters, sql.list_servers/databases, keyvault.list_vaults/secrets-metadata, websites.list_sites, authorization.list_role_assignments |
| `read.describe` | low | (paired with list typically) |
| `read.get` | low | keyvault.get_secret_metadata (NOT value), sql.get_database_metrics |
| `write.*` | — | None today |
| `execute.*` | — | None today |

Confirm in Block G when refactoring.

---

## 5.3 Governance hooks

### 5.3.1 Activity Log correlation

Every Azure SDK call accepts a `x-ms-correlation-request-id` header. Inject Control Centre's `requestId`:

```python
from azure.core.pipeline.policies import HeadersPolicy
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient

credential = DefaultAzureCredential()
client = ComputeManagementClient(
    credential, subscription_id,
    headers_policy=HeadersPolicy({
        "x-ms-correlation-request-id": request_id,
        "x-ms-cloudmorph-intent": intent_id,
        "x-ms-cloudmorph-policy-bundle": policy_bundle_id,
    })
)
```

Activity Log rows then carry the correlation id and Activity Log queries can join back to Control Centre decisions:

```kusto
AzureActivity
| where CorrelationId == "{request_id}"
| project EventTimestamp, OperationName, Caller, ResultType
```

**Effort:** 6h. Block G.

### 5.3.2 Event Grid emitter

Mirror every decision to a customer-owned Event Grid topic:

```python
from azure.eventgrid import EventGridPublisherClient, EventGridEvent

client = EventGridPublisherClient(endpoint=customer_topic_endpoint, credential=cred)
client.send([EventGridEvent(
    subject="cloudmorph/decision",
    event_type="Cloudmorph.Decision",
    data=audit_event_dict,
    data_version="0.1",
)])
```

**Effort:** 4h. Block G.

### 5.3.3 Compile-to-Azure-Policy story

Generate Azure Policy assignments from the active bundle:

```python
def compile_bundle_to_azure_policy(bundle: PolicyBundle) -> List[Dict]:
    """Translate Rego rules into Azure Policy definitions."""
    definitions = []
    for rule in bundle.rules:
        if rule.outcome == "deny" and rule.action.startswith("azure."):
            definitions.append({
                "properties": {
                    "displayName": f"CloudMorph deny: {rule.id}",
                    "policyType": "Custom",
                    "mode": "Indexed",
                    "policyRule": {
                        "if": {...rule conditions translated...},
                        "then": {"effect": "Deny"},
                    }
                }
            })
    return definitions
```

Defense in depth: even if Control Centre is bypassed, Azure Policy enforces.

**Effort:** 16h. Post-MVP.

### 5.3.4 Defender for Cloud finding emission

When `cloudmorph_declare_intent` triggers and the agent then violates intent (`intent_mismatch` decision), emit a Microsoft Defender for Cloud Security Alert via the Defender API:

```python
defender.alerts.create(
    severity="Medium",
    alert_type="cloudmorph_intent_mismatch",
    extended_properties={
        "intent_id": intent_id,
        "declared_verbs": intent.structured_verbs,
        "attempted_action": tool_call.action,
    }
)
```

Customers see CloudMorph signals in their security dashboard.

**Effort:** 12h. Post-MVP.

---

## 5.4 Tests

Today: zero. Plan:

| Test file | Coverage | Effort |
|---|---|---:|
| `tests/test_azure_main.py` | Lifecycle | 4h |
| `tests/test_azure_action_registry.py` | Registry sanity | 1h |
| `tests/test_azure_handlers_blob.py` | Mocked `BlobServiceClient` | 3h |
| `tests/test_azure_handlers_compute.py` | Mocked `ComputeManagementClient` | 3h |
| `tests/test_azure_handlers_containerapps.py` | Mocked REST | 3h |
| `tests/test_azure_handlers_aks.py` | Mocked REST | 2h |
| `tests/test_azure_handlers_sql.py` | Mocked REST | 2h |
| `tests/test_azure_handlers_keyvault.py` | Mocked SDK | 3h |
| `tests/test_azure_governance_correlation.py` | Verify header injection | 2h |
| `tests/test_azure_governance_eventgrid.py` | Verify event emission | 2h |
| `tests/test_azure_artifact_upload.py` | Mocked Blob | 3h |
| Nightly integration vs Azurite | LocalStack-equivalent for Azure storage | 6h |

**Total: ~34h.** Same coverage target (60%) as AWS.

---

## 5.5 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Extract controlcenter_client.py | P0 | (cross/07) | C |
| Extract main.py lifecycle | P0 | (cross/07) | C |
| Registry refactor of job_runner.py | P0 | 10h | G |
| Tests (~34h) | P0 | 34h | G+H |
| Activity Log correlation header | P0 | 6h | G |
| Event Grid emitter | P1 | 4h | G |
| Mixed urllib+SDK consolidation | P1 | 12h | post-MVP |
| Hard-coded API versions to one file | P1 | 1h | G |
| Dockerfile: drop AWS/GCS deps, harden | P1 | 4h | H |
| Compile-to-Azure-Policy | P2 | 16h | post-MVP |
| Defender for Cloud finding emission | P2 | 12h | post-MVP |

---

## 5.6 Source links

- [azure/executor/src/main.py](../../azure/executor/src/main.py)
- [azure/executor/src/job_runner.py](../../azure/executor/src/job_runner.py)
- [azure/executor/src/controlcenter_client.py](../../azure/executor/src/controlcenter_client.py)
- [azure/executor/src/storage_pointers.py](../../azure/executor/src/storage_pointers.py)
- [azure/executor/Dockerfile](../../azure/executor/Dockerfile)

Implementation: Block C → G → H.
