# cloudmorph

Official Python SDK for CloudMorph — govern AI agent cloud actions across AWS, GCP, Azure, Databricks, and Snowflake.

## Installation

```bash
pip install cloudmorph
```

Requires Python 3.9+. Zero external dependencies (stdlib only: `urllib`, `json`).

## Quick Start

```python
from cloudmorph import CloudMorphClient

cm = CloudMorphClient(token="cm_your_integration_token")

# Submit a request and wait for completion
result = cm.request_and_wait("aws.s3.list_buckets", account_id="acc_123")
print(result["status"])   # "completed"
print(result["output"])   # {"buckets": [...], "count": 5}
```

## Authentication

Create a `CloudMorphClient` with your integration token from the CloudMorph Control Centre dashboard.
Optionally, point it at a self-hosted MCP server with `base_url`.

```python
from cloudmorph import CloudMorphClient

# Managed cloud (default)
cm = CloudMorphClient(token="cm_your_integration_token")

# Self-hosted MCP server
cm = CloudMorphClient(
    token="cm_your_integration_token",
    base_url="https://mcp.your-org.example.com",
    timeout=30,
)
```

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `token` | `str` | required | Integration token from Control Centre |
| `base_url` | `str` | `"https://mcp.cloudmorph.io"` | MCP server URL |
| `timeout` | `int` | `60` | Request timeout in seconds |

## API Reference

### `request(action, *, targets=None, payload=None, account_id=None, wait=False, wait_seconds=None)`

Submit a policy request. Returns as soon as the server responds (immediately if `wait=False`, or after
the server-side wait completes when `wait=True`).

```python
# Fire-and-forget — returns immediately with decision
result = cm.request("aws.s3.list_buckets")

# Server-side wait — returns when the action completes
result = cm.request(
    "aws.ec2.list_instances",
    payload={"region": "us-east-1"},
    account_id="acc_123",
    wait=True,
)

# Custom server-side wait timeout (seconds)
result = cm.request("aws.rds.list_instances", wait_seconds=30)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `action` | `str` | Action name (e.g., `"aws.s3.list_buckets"`) |
| `targets` | `list[str]` | Target CloudMorph account IDs |
| `payload` | `dict` | Action-specific parameters |
| `account_id` | `str` | Shorthand for `targets=[account_id]` |
| `wait` | `bool` | Ask server to wait for result |
| `wait_seconds` | `int` | Ask server to wait up to N seconds |

**Returns:** `dict` with keys `requestId`, `decision`, `status`, `output`, etc.

---

### `request_and_wait(action, *, targets=None, payload=None, account_id=None, poll_interval=2.0, max_wait=120.0)`

Submit a request and poll the server until it reaches a terminal state (`completed`, `failed`, `blocked`, `cancelled`).

```python
result = cm.request_and_wait(
    "aws.iam.list_roles",
    account_id="acc_123",
    poll_interval=2.0,   # poll every 2 seconds
    max_wait=60.0,       # give up after 60 seconds
)
print(result["status"])   # "completed"
print(result["output"])   # {...}
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | `str` | required | Action name |
| `targets` | `list[str]` | `None` | Target account IDs |
| `payload` | `dict` | `None` | Action-specific parameters |
| `account_id` | `str` | `None` | Shorthand for `targets=[account_id]` |
| `poll_interval` | `float` | `2.0` | Seconds between status polls |
| `max_wait` | `float` | `120.0` | Maximum total wait time in seconds |

**Returns:** Final result `dict`.

---

### `get_request_status(request_id)`

Fetch the current status of a request by its ID.

```python
result = cm.request("aws.s3.list_buckets")
request_id = result["requestId"]

status = cm.get_request_status(request_id)
print(status["status"])   # "pending" | "running" | "completed" | "failed" | "blocked"
```

**Returns:** `dict` with `requestId`, `status`, `decision`, `output`, etc.

---

### `get_job_status(job_id)`

Fetch the status of a job by its job ID (returned in some action outputs).

```python
job = cm.get_job_status("job_abc123")
print(job["status"])
print(job["logs"])
```

**Returns:** `dict` with job details.

---

## Error Handling

```python
from cloudmorph import CloudMorphClient, CloudMorphError, RateLimitError

cm = CloudMorphClient(token="cm_your_token")

try:
    result = cm.request_and_wait("aws.s3.list_buckets", account_id="acc_123")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after_seconds}s")
except CloudMorphError as e:
    print(f"Error [{e.code}] (HTTP {e.status}): {e}")
    print(e.data)   # raw server payload for debugging
```

**Exception hierarchy:**

```
CloudMorphError(Exception)
└── RateLimitError          # HTTP 429 — server-side rate limit exceeded
```

`RateLimitError` adds `.retry_after_seconds` (int) parsed from the `Retry-After` response header.

---

## Action Naming

Actions use dot-separated names matching your CloudMorph permission pack:

| Cloud | Examples |
|-------|---------|
| **AWS** | `aws.s3.list_buckets`, `aws.iam.list_roles`, `aws.ec2.list_instances`, `aws.rds.list_instances`, `aws.lambda.list_functions`, `aws.cloudwatch.list_alarms`, `aws.sts.get_caller_identity` |
| **GCP** | `gcp.storage.list_buckets`, `gcp.iam.list_service_accounts`, `gcp.bigquery.list_datasets`, `gcp.functions.list_functions`, `gcp.pubsub.list_topics`, `gcp.sql.list_instances` |
| **Azure** | `azure.blob.list_containers`, `azure.rbac.list_role_assignments`, `azure.aks.list_clusters`, `azure.sql.list_servers`, `azure.functions.list_apps`, `azure.keyvault.list_vaults` |

Pass the CloudMorph account ID (`cmacc_…`) in `account_id` or `targets` when targeting a specific account.

---

## License

Apache-2.0 — see [LICENSE](https://www.apache.org/licenses/LICENSE-2.0) for details.
