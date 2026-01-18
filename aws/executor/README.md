# Control Center AWS Executor

Minimal poller that claims jobs from Control Center, runs them, and reports status/heartbeat/completion.

## Required environment variables
- CONTROL_CENTER_API_URL
- CONTROL_CENTER_EXECUTOR_TOKEN
- CONTROL_CENTER_TENANT_ID
- CONTROL_CENTER_ACCOUNT_ID

## Optional environment variables
- CONTROL_CENTER_CAPABILITIES (comma-separated, default: agent.run)
- EXECUTOR_ID (defaults to hostname)
- HEARTBEAT_SECONDS (default: 20)
- POLL_BASE_SECONDS (default: 2)
- POLL_MAX_SECONDS (default: 15)
- CONTROL_CENTER_JOB_SCHEMA_PATH (overrides default job schema path)

## Local run
```bash
export CONTROL_CENTER_API_URL=https://controlcenter.example
export CONTROL_CENTER_EXECUTOR_TOKEN=cm_exec_...
export CONTROL_CENTER_TENANT_ID=acme
export CONTROL_CENTER_ACCOUNT_ID=123456789012
python src/main.py
```

## Docker build
Build from the repo root so `contracts/` is available:
```bash
docker build -f aws/executor/Dockerfile -t cloudmorph-executor .
```
