# CloudMorph Control Centre

**Runtime firewall for agentic AI tool calls.** Captures intent, evaluates against declarative policies, returns allow/deny/approve/mutate/redact/throttle/audit_only decisions in a sub-50ms latency budget. Ships as an MCP server, plus thin SDKs and per-cloud governance executors.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![Status: pre-MVP](https://img.shields.io/badge/Status-pre--MVP-orange.svg)](status/BUILD_PLAN.md)

> **Status:** under active 14-day MVP development. See [`status/`](status/) for the architectural rescan and [`status/BUILD_PLAN.md`](status/BUILD_PLAN.md) for the day-by-day plan. Target ship: 2026-05-07.

---

## What's in this repo

| Path | Purpose |
|---|---|
| [`cloudmorph-mcp/`](cloudmorph-mcp/) | MCP server (TypeScript) — the firewall data plane. Apache 2.0. |
| [`sdk-python/`](sdk-python/) | Python SDK (`pip install cloudmorph`) — for agents that don't speak MCP natively. |
| [`contracts/`](contracts/) | JSON Schema contracts — single source of truth for types across languages. |
| [`cloudmorph-common-py/`](cloudmorph-common-py/) | Shared Python lib for executors and SDK (`BaseExecutor`, `ControlCenterClient`, `AuditEmitter`). |
| [`cloudmorph-common-ts/`](cloudmorph-common-ts/) | Mirror for TypeScript: shared types, audit, canonical JSON. |
| [`aws/executor/`](aws/executor/) | BYOC executor for AWS — runs jobs in customer's AWS account. |
| [`azure/executor/`](azure/executor/) | Same for Azure. |
| [`gcp/executor/`](gcp/executor/) | Same for GCP. |
| [`databricks/executor/`](databricks/executor/) | Data platform executor — Databricks SQL, Unity Catalog, Notebooks. |
| [`snowflake/executor/`](snowflake/executor/) | Snowflake executor — Query Tag, row-access policies, Cortex. |
| [`tests/`](tests/) | Cross-component tests (per-component tests live next to their code). |
| [`docs/`](docs/) | User-facing documentation. |
| [`status/`](status/) | Architectural rescan + 14-day MVP plan. |

## Quick start (developer)

```bash
git clone https://github.com/CloudMorphAI/cloudmorph-control-center
cd cloudmorph-control-center

# Install pre-commit hooks
pip install pre-commit && pre-commit install

# Generate contracts → typed Pydantic + TS
make contracts

# Lint everything
make lint

# Run tests
make test

# Build MCP server container
make docker-mcp
```

## Quick start (agent integration)

```python
# pip install cloudmorph
from cloudmorph import firewall

firewall.start_proxy(
    cm_token="cm_...",
    upstream_mcp_url="http://localhost:3001",   # any downstream MCP server
)
# point your agent at the local stdio socket — every tool call is now policy-evaluated.
```

Or for raw `Anthropic().messages.create(..., tools=[...])` loops:

```python
from cloudmorph.adapters.anthropic import GovernedAnthropic

client = GovernedAnthropic(api_key="sk-ant-...", cm_token="cm_...")
response = client.messages.create(
    model="claude-opus-4-7",
    messages=[{"role": "user", "content": "list our public S3 buckets"}],
    tools=[...],
)
# Every tool_use block in the response was policy-evaluated; intent inferred from system prompt.
```

## Design

- **System architecture:** [`status/ARCHITECTURE.md`](status/ARCHITECTURE.md) — 18 sections with diagrams.
- **Policy engine:** [`status/policy/05_policy_engine_design.md`](status/policy/05_policy_engine_design.md) — OPA WASM, bundle hot-reload, 7 outcomes.
- **Intent system:** [`status/intent/06_intent_system_design.md`](status/intent/06_intent_system_design.md) — hybrid verb taxonomy, 3-stage matcher.
- **Build plan:** [`status/BUILD_PLAN.md`](status/BUILD_PLAN.md) — 14-day day-by-day execution.

## License

Apache 2.0 — see [LICENSE](LICENSE). Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Security disclosures: [SECURITY.md](SECURITY.md).
