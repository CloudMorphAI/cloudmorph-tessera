# cloudmorph

Official Python SDK for the CloudMorph Control Centre.

Govern AI agents across AWS, GCP, Azure, Databricks, and Snowflake.

## Installation

```bash
pip install cloudmorph
```

## Quick Start

```python
from cloudmorph import CloudMorph

cm = CloudMorph(token="cm_your_integration_token")

# Submit a request and wait for completion
result = cm.request_and_wait("aws.s3.list_buckets", account_id="acc_123")
print(result["status"])   # "completed"
print(result["output"])   # {"buckets": [...], "count": 5}
```

## API Reference

### `CloudMorph(token, base_url=None, timeout=60)`

Create a client instance.

### `cm.request(action, *, targets=None, payload=None, account_id=None, wait=False)`

Submit a policy request. Returns immediately with the decision.

```python
result = cm.request("aws.ec2.list_instances", payload={"region": "us-east-1"}, wait=True)
```

### `cm.request_and_wait(action, *, targets=None, payload=None, poll_interval=2, max_wait=120)`

Submit and poll until completion.

### `cm.get_request_status(request_id)`

Check status of a request.

### `cm.get_job_status(job_id)`

Get job details by job ID.

## Error Handling

```python
from cloudmorph import CloudMorph, CloudMorphError, RateLimitError

try:
    result = cm.request("aws.s3.list_buckets")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after_seconds}s")
except CloudMorphError as e:
    print(f"Error: {e.code} — {e}")
```

## Zero Dependencies

This SDK uses only Python standard library (`urllib`, `json`). No external
packages required.

## License

Apache-2.0
