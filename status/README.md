# `status/` — Architectural Rescan & 14-Day MVP Build Plan

_Generated 2026-04-23 by a deep, line-by-line rescan of `cloudmorph-control-center`. Single source of truth for the runtime-firewall MVP push._

## Read in this order

1. **[00_inventory.md](00_inventory.md)** — file-by-file LoC + duplication confirmation. Start here.
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** — what we're building toward (system diagram, multi-tenancy, all 18 sections).
3. **[BUILD_PLAN.md](BUILD_PLAN.md)** — 14-day day-by-day execution plan with hour-level tasks.

## Per-area audits

| File | Subject | LoC of audit |
|---|---|---:|
| [mcp/01_server_audit.md](mcp/01_server_audit.md) | MCP server line-by-line + firewall gaps | 441 |
| [contracts/02_contracts_audit.md](contracts/02_contracts_audit.md) | 3 existing + 5 new schemas; codegen pipeline | 542 |
| [sdk/03_python_sdk_audit.md](sdk/03_python_sdk_audit.md) | SDK firewall integration patterns | 389 |
| [aws/04_executor_audit.md](aws/04_executor_audit.md) | AWS executor + IAM session tagging | 274 |
| [azure/04_executor_audit.md](azure/04_executor_audit.md) | Azure executor + Activity Log correlation | 232 |
| [gcp/04_executor_audit.md](gcp/04_executor_audit.md) | GCP executor + Eventarc emitter | 169 |
| [databricks/04_executor_audit.md](databricks/04_executor_audit.md) | Databricks SQL execute_query + UC policy | 201 |
| [snowflake/04_executor_audit.md](snowflake/04_executor_audit.md) | Snowflake QUERY_TAG + row-access policy | 228 |
| [policy/05_policy_engine_design.md](policy/05_policy_engine_design.md) | OPA WASM, bundle hot-reload, 7 outcomes | 587 |
| [intent/06_intent_system_design.md](intent/06_intent_system_design.md) | Hybrid verb taxonomy + 3-stage matcher | 358 |
| [cross/07_common_layer_audit.md](cross/07_common_layer_audit.md) | `cloudmorph-common-py` extraction (901 LoC dedup) | 412 |
| [cross/08_tests_audit.md](cross/08_tests_audit.md) | Test plan + adversarial fixtures | 344 |
| [cross/09_packaging_and_docker_audit.md](cross/09_packaging_and_docker_audit.md) | 6 Dockerfiles + multi-arch + SBOM | 340 |
| [cross/10_security_and_tenancy_audit.md](cross/10_security_and_tenancy_audit.md) | Tenant isolation, audit chain, mTLS, OIDC | 289 |
| [cross/11_observability_and_slo.md](cross/11_observability_and_slo.md) | SLOs, metrics catalog, OTel, COG | 287 |
| [cross/12_strategic_open_questions.md](cross/12_strategic_open_questions.md) | Founder calls (locked Day 0) | 269 |

**Total plan output:** ~7,335 LoC across 19 files. Inventory: 67 source files, ~9,144 LoC of code (901 LoC of which is pure copy-paste duplication, eliminated in Block C).

## Bluntest one-liner findings

- The wedge (`cloudmorph_proxy` MCP tool + intent capture) is unbuilt.
- Word "intent" appears **0 times** in source. Word "policy" appears **1 time** (in a tool description string).
- `controlcenter_client.py` is byte-identical across 5 executors — **865 LoC of duplication.**
- `aws/executor/src/job_runner.py` is **1,066 lines of flat `if/elif` dispatch** (36 branches).
- MCP server `npm test` is `echo "No tests yet" && exit 0`.
- The repo is **executor plumbing + a polite gateway**. The firewall is unbuilt.

## What changes by Day 14

- Runtime firewall live: local OPA WASM eval, intent capture, intent-vs-action mismatch detection, audit log with hash chain, `cloudmorph_proxy` MCP tool.
- Hosted SaaS at `mcp.cloudmorph.io` running v0.1.0-mvp.
- One design partner integrated end-to-end.
- 901 LoC of dup eliminated; executors thin and registry-based (AWS at minimum).
- 30 adversarial test fixtures.
- Python SDK with firewall + Anthropic + OpenAI adapters on PyPI.
- Multi-arch container images on GHCR with Cosign signatures + SBOM.
