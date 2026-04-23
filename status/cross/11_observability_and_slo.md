# 11 — Observability & SLO

_Current state: stdout JSON lines on the MCP server, stdout JSON lines on each executor, zero metrics endpoints, zero traces, zero customer-facing visibility. SLOs unwritten._

---

## 1.1 SLO definitions (locked for MVP)

| SLO | Target | Measurement |
|---|---|---|
| **MCP `tools/call` latency (cached decision)** | p50 < 5ms, p95 < 10ms, p99 < 20ms | Prometheus histogram `cm_tools_call_seconds{outcome,cache_hit}` |
| **MCP `tools/call` latency (cold decision)** | p50 < 20ms, p95 < 35ms, p99 < 50ms | Same |
| **Decision correctness vs fixture suite** | 100% (zero regressions on locked fixtures) | CI gate against `cloudmorph-mcp/tests/fixtures/decisions/` |
| **Audit log durability** | 0 events lost (with bounded local buffer guarantee) | sink-success / sink-failure ratio + buffer-drop counter |
| **Policy bundle hot-reload** | < 5s end-to-end (signature verify → blue-green swap) | Integration test gates |
| **Availability (hosted SaaS)** | 99.9% monthly (43 min downtime budget) | StatusGator pingback + own ELB target health |
| **Token resolver cache hit ratio** | > 90% steady state | `cm_token_resolver_cache_hits_total / total` |
| **Decision cache hit ratio** | > 70% steady state | `cm_decision_cache_hits_total / total` |
| **Executor heartbeat gap** | < 60s p99 | `cm_executor_heartbeat_gap_seconds` |
| **End-to-end audit emission time** | < 5s p99 (decision → event in customer sink) | OTel trace |

**Error budgets:**
- 99.9% = 43 min/mo downtime → exhaust 5% per incident → halt non-critical changes when budget < 50%.
- Zero-tolerance on audit loss: a single dropped event halts deploys until root-caused.

---

## 1.2 Metrics catalog (Prometheus + OpenTelemetry)

Naming: `cm_<subsystem>_<metric>_<unit>`. Labels: low-cardinality (tenant tier, outcome, cloud — NOT raw tenantId or requestId).

### Decision metrics

```
cm_decisions_total{outcome="allow|deny|approve|mutate|redact|throttle|audit_only", tenant_tier="free|pro|enterprise", policy_bundle="<id>"}
cm_decision_eval_seconds{tenant_tier, cache_hit="true|false"}        # histogram
cm_decision_cache_hits_total
cm_decision_cache_misses_total
cm_decision_cache_size                                                # gauge
cm_decision_cache_evictions_total
```

### Policy bundle metrics

```
cm_policy_bundle_loaded_at{bundle_id, version}                       # gauge (epoch seconds)
cm_policy_bundle_reloads_total{outcome="success|signature_invalid|compile_failed"}
cm_policy_bundle_size_bytes{bundle_id}                               # gauge
```

### Intent metrics

```
cm_intent_declarations_total{tenant_tier, agent_vendor, primary_verb}
cm_intent_calls_total{tenant_tier}
cm_intent_expired_unreferenced_total{tenant_tier}
cm_intent_revoked_total{tenant_tier}
cm_intent_mismatch_total{tenant_tier, severity="lexical|semantic|llm_judge"}
cm_intent_match_seconds{stage="lexical|semantic|llm_judge"}          # histogram
cm_intent_llm_judge_calls_total
cm_intent_llm_judge_cache_hits_total
```

### Audit metrics

```
cm_audit_events_total{sink="stdout|s3|s3-customer|kafka", event_type}
cm_audit_sink_failures_total{sink, reason}
cm_audit_buffer_size_bytes                                            # gauge
cm_audit_buffer_dropped_total
cm_audit_emit_seconds{sink}                                           # histogram
```

### MCP server metrics

```
cm_tools_call_seconds{tool, outcome}                                  # histogram
cm_jsonrpc_methods_total{method, outcome="success|error"}
cm_ws_connections                                                     # gauge
cm_ws_messages_total{direction="in|out"}
cm_token_resolver_cache_hits_total
cm_token_resolver_cache_misses_total
cm_rate_limit_rejections_total{reason}
```

### Executor metrics

```
cm_executor_jobs_total{cloud, status="completed|failed", action_family}
cm_executor_job_seconds{cloud, action_family}                         # histogram
cm_executor_heartbeat_gap_seconds{cloud}                              # histogram
cm_executor_claim_attempts_total{cloud, outcome}
cm_executor_artifact_upload_seconds{cloud}                            # histogram
cm_executor_artifact_upload_bytes{cloud}                              # histogram
```

### Cross-system saturation

```
cm_process_cpu_seconds_total
cm_process_resident_memory_bytes
cm_process_virtual_memory_bytes
cm_process_open_fds
cm_nodejs_eventloop_lag_seconds                                       # MCP
cm_python_gc_collections_total{generation}                            # executors
```

**Cardinality budget:** ~50 unique labels per service. Avoid `tenantId` as a label — too high cardinality. Use `tenant_tier` as proxy. For per-tenant drill-down, query against the audit log instead of metrics.

---

## 1.3 Tracing (OpenTelemetry)

One trace chain per request. Spans:

```
mcp.receive
├── token.resolve (cache hit/miss)
├── intent.match
│   ├── intent.match.lexical
│   ├── intent.match.semantic    (if Stage 1 ambiguous)
│   └── intent.match.llm_judge   (if Stage 2 ambiguous)
├── policy.evaluate
│   ├── policy.cache.lookup
│   └── policy.opa.evaluate      (if cache miss)
├── upstream.call                (executor lifecycle, only if action requires)
│   └── executor.run
│       └── cloud.api.<service>.<verb>   (S3, EC2, etc.)
└── audit.emit
    └── audit.sink.<name>
```

Backend choices:
- **Hosted SaaS:** OTel Collector → AWS X-Ray (cheap, AWS-native) and/or Honeycomb (richer querying).
- **Self-hosted:** OTLP endpoint env var; customer points at Jaeger / Tempo / their backend.

Trace context propagation:
- `traceparent` header on every HTTP call (W3C Trace Context)
- Embedded in JSON-RPC `id` extension (custom convention, MCP spec extension)
- Embedded in audit events as `traceId` field

**Effort:** 6h MCP server instrumentation + 8h executor instrumentation + 4h trace backend integration. Block H.

---

## 1.4 Logs

JSON-line structured logs to stdout. Standard fields:

```
{"ts":"2026-04-23T10:00:00.123Z","level":"info","service":"cloudmorph-mcp","sha":"abc1234","tenant_tier":"pro","trace_id":"...","span_id":"...","msg":"decision.made","outcome":"allow","action":"aws.s3.list_buckets","eval_ms":1.7,"cache_hit":true}
```

Routing:
- Container stdout → log driver (CloudWatch Logs, GCP Logging, Datadog) per deployment.
- Sensitive data redacted at emission (token prefixes only, payload key counts not values).
- Severity-tagged (`level`, `severity` in Cloud Logging compatible form for cross-cloud).

The MCP server's `logEvent` function (`routes.ts:197-201`) and each executor's `_log` function should consolidate into one JSON-logger from `cloudmorph_common.log` / `cloudmorph_common_ts/log`.

**Effort:** 4h for the log helper unification (covered in Block C). 1h to verify log levels per environment in Block H.

---

## 1.5 Customer-facing observability

The product's value is **explainable decisions**. Customer-facing surfaces:

### `cloudmorph_explain_decision` MCP tool

Returns the full `PolicyDecision` (matched rules, evaluation trace, evidence) for a `decisionId`. Already designed in [mcp/01 §1.2](../mcp/01_server_audit.md). Effort: 4h, Block D.

### Per-tenant decision dashboard

Console UI fetches:
- Decisions over time (chart by outcome)
- Top denied actions (list)
- Top intents declared (list with counts)
- Mismatch rate (chart)
- Decisions per agent (chart)
- Average eval time (chart)
- Bundle reload history (timeline)

Backed by audit events queried from customer's audit sink (S3 + Athena, or BigQuery, or ClickHouse). MVP: ship a sample Athena view spec; customer queries directly. Post-MVP: managed dashboard.

**Effort:** 4h sample Athena view. Post-MVP for managed dashboard (UI is a Console concern).

### Decision query API

```
GET /api/v1/decisions?tenantId=...&since=...&outcome=deny&limit=100
GET /api/v1/decisions/{decisionId}
GET /api/v1/audit/verify?bundle=s3://...
```

**Effort:** 8h. Block I (MVP) — at minimum, a `GET /decisions/{id}` for design-partner debugging.

---

## 1.6 Cost-of-goods (per-tenant unit economics)

Hosted SaaS COG breakdown per tenant per month (estimates, hosted on AWS):

| Component | Cost basis | Per-tenant baseline ($/mo) | Notes |
|---|---|---:|---|
| Fargate task (1 vCPU, 2GB, 24/7) | $0.04/hr × 730 = $29 | shared across tenants × 3 instances = $87/mo total | Per-tenant marginal: ~$0.50 unless dedicated |
| ALB hours | $0.025/hr × 730 = $18 | shared = $18 total | Marginal: ~$0.10 |
| ALB LCU | $0.008/LCU-hr | depends on RPS — assume 100 LCU/mo @ 1k decisions/day = $5 | Marginal: $5 |
| Data transfer out (decisions only — small) | $0.09/GB | 1 GB/mo at 1k decisions/day = $0.09 | Marginal: $0.09 |
| ElastiCache Redis (cache.t4g.small) | $13/mo | shared = $13 | Marginal: ~$0.30 |
| S3 audit storage | $0.023/GB/mo | 100 MB/mo at 1k decisions/day = $0.002 | Marginal: $0.002 |
| S3 PUT requests | $0.005/1k | 1k/day × 30 = $0.15 | Marginal: $0.15 |
| OPA WASM eval (in-process) | CPU-only | 0 marginal | included in Fargate cost |
| LLM judge calls (Haiku) | $0.80/1M input, $4/1M output | 100 escalations/day × 200 tokens = $0.10/mo | Marginal: $0.10 |
| Embedding (local model) | CPU-only | 0 marginal | included in Fargate |
| OTel + logs to CloudWatch | $0.50/GB ingested | 200 MB/mo = $0.10 | Marginal: $0.10 |
| **Per-tenant marginal total** | | **~$5.50/mo** | for 1k decisions/day |

**Heavy tenant (100k decisions/day = 100×):** ~$50-80/mo (LCU + ALB scale).

**Pricing implications for tier comparison:**

| Tier | Price/mo | Decisions allowance | Margin per tenant |
|---|---:|---|---:|
| Free | $0 | 100/day | $0 - $5 = **-$5** loss leader |
| Pro | $50 | 1k/day | $50 - $5 = $45 = **90% gross margin** |
| Enterprise | $500-2000 | 100k/day | $1000 - $80 = $920 = **92% gross margin** |
| Hosted-MCP-only seat | $20 | 500/day | $20 - $3 = $17 = **85% gross margin** |

Healthy SaaS economics. Validates per-decision pricing as the right meter (caps Free tier abuse cost at $5 to acquire a free user).

---

## 1.7 Alerting

| Alert | Trigger | Severity | On-call action |
|---|---|---|---|
| MCP p99 > 100ms for 5 min | Prometheus alertmanager | P1 | Investigate; likely cache cold or upstream slow |
| Decision cache hit ratio < 50% for 30 min | Prometheus | P2 | Tune cache TTL or inspect bundle for hash-key fragmentation |
| Audit sink failures > 1/min for 5 min | Prometheus | P1 | Check S3 / Kafka health |
| Audit buffer > 80% full | Prometheus gauge alert | P0 | Scale audit emission throughput; sink down? |
| Policy bundle reload failed | Audit event | P1 | Inspect bundle artifact; check signing key |
| MCP availability < 99.9% in 7d window | StatusGator | P1 | Postmortem |
| Tenant fail-open active > 1h | Prometheus | P0 | Tenant-impacting; restore engine fast |
| `cm_intent_mismatch_total` spike > 10x baseline | Prometheus rate alert | P2 | Customer's agent might be malfunctioning; inform |

PagerDuty for P0/P1; Slack for P2. Block H wiring.

---

## 1.8 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| `/metrics` Prometheus endpoint on MCP | P0 | 4h | H |
| `/metrics` on each executor | P1 | 6h | H |
| Metrics catalog implementation (~30 metrics) | P0 | 12h | H |
| OTel tracing in MCP | P0 | 6h | H |
| OTel tracing in executors | P1 | 8h | H |
| OTel collector deployment (hosted) | P0 | 4h | I |
| Logs unification (`cloudmorph_common.log`) | P1 | 4h | C |
| Log severity / Cloud Logging compat | P1 | 1h | C |
| `cloudmorph_explain_decision` tool | P0 | (in mcp/01) | D |
| Decision query API (`/api/v1/decisions`) | P1 | 8h | I |
| Sample Athena view for audit S3 | P2 | 4h | post-MVP |
| Per-tenant dashboard (Console-side) | P2 | — | Console concern |
| Alerting wired (PagerDuty + Slack) | P1 | 4h | H |
| Cost dashboard (own) | P2 | 8h | post-MVP |

**MVP critical-path total: ~50h.** Block H + I.

---

## 1.9 Out of scope

- APM-style profiling (pyroscope, parca). Post-MVP if perf becomes a problem.
- Custom Grafana dashboards. Use Cloud-native (CloudWatch, Cloud Monitoring) for hosted; ship Prometheus exposition for self-hosted; community dashboards via the Prometheus exporter pattern post-MVP.
- Per-customer SLA dashboards. Console concern.

---

## 1.10 Source links

- [mcp/01_server_audit.md](../mcp/01_server_audit.md)
- [policy/05_policy_engine_design.md](../policy/05_policy_engine_design.md)
- [intent/06_intent_system_design.md](../intent/06_intent_system_design.md)
- [cross/10_security_and_tenancy_audit.md](10_security_and_tenancy_audit.md) — failure mode catalog
