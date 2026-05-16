# Integrations and Cost

The AWS-specific surface (IAM-signed MCP routing, blast-radius computation) and the cost-evaluation surface (Infracost backend, mapping shim, price-table consumer pattern). These are the data sources for the v0.2.0 semantic conditions (`predicted_cost`, `blast_radius`, `affected_resource_count`, `cumulative_spend_today`); the engine's hot path consults them via the evaluation context (see `policy-engine.md`).

## Integrations module shape

`tessera/integrations/` is organized as one Python subpackage per integration target:

```
integrations/
├── cursor_hooks.py         # Cursor v1.7 beforeMCPExecution / afterMCPExecution
└── aws/
    ├── upstream.py         # AWSMcpUpstream — kind: aws_mcp
    └── blast_radius.py     # BlastRadiusBackend — boto3-driven principal counter
```

The `aws/` subpackage is the only multi-file integration today. Other vendor MCPs (GitHub, Jira, Postgres, Salesforce, Slack) do not have explicit subpackages in this repo — their integration with Tessera is via the generic HTTP-MCP upstream path (`upstream.kind: bearer`) plus the policy YAMLs that live in the `vendor-mcp-protection` premium pack (described in `tessera-intelligence/arch/status/policy-packs.md`). The principle: an integration gets a code subpackage only when it needs adapter-level logic that doesn't fit the generic httpx-forward path (AWS does; others don't).

## Adding a new MCP-server integration

The contract for the generic path is short: a customer configures `upstream.kind: bearer` (the default) and supplies a `url` + optional `credentials.header`/`value` pair in `tessera.yaml`. Tessera's lifespan instantiates one `httpx.AsyncClient` per upstream, the proxy routes `POST /mcp/<name>` to `client.post("/", json=body)`. Vendor-specific protection comes from the policy YAMLs that match `tool_name_in` or `match.tool_pattern`, not from per-vendor code.

The adapter path (the AWS pattern) is the exception, gated on three conditions:

1. The vendor's MCP server requires non-HTTP-Bearer authentication (AWS requires SigV4 over streamable HTTP).
2. The auth machinery is too heavy to inline into `tessera.yaml` (boto3 chain, role chaining, IAM signature versions).
3. The vendor exposes useful side-channel metadata that audit should capture (AWS service-context headers).

When all three apply, a new subpackage under `integrations/<vendor>/` carries an `Upstream` class implementing `__aenter__` / `__aexit__` / `forward(body)` and registered against a new `upstream.kind` discriminator in `tessera/config.py`. The proxy dispatches on `upstream.kind` in `_forward_upstream` via a `match` statement.

## AWS MCP upstream (`kind: aws_mcp`)

`tessera/integrations/aws/upstream.py:AWSMcpUpstream` is the IAM-signed routing client for the official AWS MCP server. It wraps `mcp_proxy_for_aws.client.aws_iam_streamablehttp_client`, which is the AWS-supplied client doing SigV4 over streamable HTTP. Configured under `tessera.yaml`:

```yaml
upstreams:
  - name: aws
    kind: aws_mcp
    url: https://mcp.amazonaws.com
    aws_region: us-east-1
    # aws_service defaults to "aws-mcp"
    # aws_endpoint_override is for LocalStack testing
```

Credentials are resolved via the boto3 default chain — env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`), `~/.aws/credentials` profile, EC2/ECS/Fargate instance metadata service. No Tessera-side credential storage. This is deliberate: AWS credentials never appear in Tessera config; Tessera assumes the runtime environment is already authenticated.

The `forward(body)` method:

1. Posts the JSON-RPC body through the IAM-signed client. Per-request timeout is `upstream.timeout_seconds` (default 30s).
2. On `NoCredentialsError`-class exceptions, returns a JSON-RPC `-32603` with reason `AWS credentials not found — check boto3 chain`. This is the most common operator-facing error and is given a specific reason for easier triage.
3. On 5xx from the AWS upstream, returns `-32603` with reason `AWS upstream 5xx error`.
4. Captures the response headers `aws:ViaAWSMCPService` and `aws:CalledViaAWSMCP` and attaches them to the parsed response under the key `_aws_context`. The proxy includes this in the audit payload, so audit events for AWS-MCP calls carry which AWS service handled the routing and where the call ultimately landed.

The model validator in `UpstreamConfig._require_aws_region_for_aws_mcp` rejects an `aws_mcp` upstream that omits `aws_region` — required for SigV4 region scoping.

## Blast-radius: production boto3 evaluator

`tessera/integrations/aws/blast_radius.py:BlastRadiusBackend` is the live-AWS-calls counterpart to the in-test stub in `tessera-intelligence/tests/blast_radius_stub.py`. The two implementations share the algorithm contract (described in `tessera-intelligence/arch/status/blast-radius.md`); they differ in how they resolve principal counts:

- **Stub** — operates only on the policy document handed to it. Returns count=1 for role ARNs without dereferencing trust policies, treats all OIDC providers as count=1 without inspecting their `StringEquals` Conditions, fallbacks to count=1 on parse ambiguity.
- **Production (this repo)** — calls live IAM/S3/KMS reads via boto3 to resolve trust policies, count account principals, parse OIDC trust Conditions. Caches results for 300s (`_CACHE_TTL`) keyed `(scope, region, kind)` to bound the IAM-API hit rate.

The supported tool names and their dispatch:

| Tool name | Dispatcher | Live AWS call |
|-----------|------------|---------------|
| `iam:PutRolePolicy` / `aws_iam_PutRolePolicy` | `_compute_iam_role_policy` | `iam.GetRole(RoleName)` for trust policy |
| `iam:AttachRolePolicy` / `aws_iam_AttachRolePolicy` | `_compute_iam_attach_role_policy` | Same `iam.GetRole` lookup |
| `s3:PutBucketPolicy` / `aws_s3_PutBucketPolicy` | `_compute_s3_bucket_policy` | Counts principals in the supplied policy document |
| `kms:PutKeyPolicy` / `aws_kms_PutKeyPolicy` | `_compute_kms_key_policy` | Counts principals in the supplied policy document |

The supplied policy document (S3 or KMS) is parsed by `_parse_policy_doc` (accepts string or dict), then `_count_policy_principals` walks `Statement[].Principal` entries. The principal-counting rules mirror the stub:

- `Principal: "*"` or `{AWS: "*"}` → `_WILDCARD_PRINCIPAL` (999_999, an "unbounded" sentinel).
- `Principal: {AWS: "...:root"}` → live IAM `list_users` + `list_roles` for the account; on failure falls back to 100 (conservative).
- `Principal: {AWS: "arn:..."}` → 1 per ARN.
- `Principal: {Service: "..."}` → 1 per service principal.
- `Principal: {Federated: "..."}` → 1 per federation principal. (The stub treats OIDC as always-1; production should descend into the trust-policy `StringEquals` Condition, but the boto3 path today returns 1 as well — closing this gap is an alignment task with the producer side described in `tessera-intelligence/arch/status/blast-radius.md`.)

The backend is opt-in via `TESSERA_BLAST_RADIUS_ENABLED=1`. When unset, `context["blast_radius_backend"]` is `None`, and the `blast_radius` condition fail-closes in the block direction (described in `policy-engine.md`). Operators who don't want to grant Tessera's runtime IAM-read permissions can leave it off; the condition then over-blocks rather than under-blocking.

The 300-second cache TTL is uniform with the producer-side rule YAMLs (every rule in `blast-radius/aws/v1.0.0/` declares `cache_ttl_seconds: 300`). Principal expansion is expensive (live IAM reads) but IAM topology changes slowly; 5-minute caching balances freshness against API cost.

**Async prefetch (P0-14).** `BlastRadiusBackend.compute(tool_name, args)` is synchronous boto3 — calling it from inside `async def proxy(...)` would block the event loop for the duration of the IAM round-trip (200–2000 ms for a slow `iam:GetRole`). The proxy now prefetches the count at the start of the hot path, off the event loop:

1. `PolicyEngine.policies_need_blast_radius(tool_name, upstream_name)` walks the loaded policies and returns True only if some matching policy uses a `BlastRadius` condition that targets this tool. When False, the IAM call is skipped entirely.
2. When True, the proxy runs `count = await asyncio.to_thread(blast_radius_backend.compute, tool_name, arguments)` and stores the result in `context["blast_radius_cache"][tool_name]`.
3. The condition evaluator (`_evaluate_blast_radius` in `tessera/policy/conditions.py`) consults `context["blast_radius_cache"]` first and only calls `compute()` synchronously if the cache is empty (fixture/test path; in production every gated request hits the cache).
4. Prefetch failures bump `blast_radius_prefetch_failures_total` and leave the cache empty, which makes the evaluator's `blast_radius_backend is None` branch fail-closed — same semantics as a missing backend.

Concurrent requests for `aws_iam_PutRolePolicy` now run their IAM reads in worker threads, so two parallel requests complete in ~max(t1, t2) instead of ~t1+t2.

Cross-references: producer-side rules and algorithm rationale live in `tessera-intelligence/arch/status/blast-radius.md`.

## Unified cost_for_call API (v0.3.0)

`tessera.cost.cost_for_call(operation, args, region)` is the canonical cost-resolution entry point as of v0.3.0.

### Contract

```python
tessera.cost.cost_for_call(
    operation: str,
    args: dict,
    region: str | None = None,
) -> CostResult
```

### `CostResult` shape

| Field | Type | Description |
|-------|------|-------------|
| `price_usd` | `float \| None` | Resolved unit price in USD; `None` on miss |
| `unit` | `str` | Pricing unit (e.g. `"Hrs"`, `"GB-month"`, `"1M requests"`) |
| `confidence_band` | `"high" \| "medium" \| "ceiling"` | Certainty of the estimate |
| `source` | `"price_table" \| "infracost_live" \| "miss"` | Which backend resolved the price |
| `operation` | `str` | Echo of the requested operation name |

### Routing

The `operation` string carries a provider prefix (`aws_*` / `azure_*` / `gcp_*`) which determines the registered `PriceTable` instance to consult. Price tables are populated at lifespan startup via `IntelligenceClient._load_price_tables_from_cache()`, which scans the local intelligence cache for signed `<cloud>-prices-<version>.json` artifacts and loads each into a `PriceTable` per provider.

### Resolution flow

1. **Price-table hit** (sub-millisecond) — the operation is present in the loaded price artifact; `source="price_table"`.
2. **Live Infracost fallback** (200 ms cap) — operation not in price table; `InfracostClient.query_sku()` is called via the configured Infracost backend; `source="infracost_live"`.
3. **Miss** — operation has no mapping in either path; `price_usd=None`, `source="miss"`. The `predicted_cost` condition returns `False` on miss (don't block).

### Audit linkage

When `cost_for_call` resolves a price, the proxy records `cost_source` and `cost_band` on the emitted audit event alongside `pricingSnapshotId`. This lets operators filter audit logs by resolution path (`source == "price_table"` vs `"infracost_live"` vs `"miss"`).

### Deprecation timeline

| Version | Status |
|---------|--------|
| v0.3.x | `tessera.cost.aws_mapping` raises `DeprecationWarning` at import; legacy `map_request()` direct-call pattern is the fallback |
| v0.4.0 | `aws_mapping` module removed; `map_request()` + `_BUILTIN_MAPPING` gone |
| Indefinite | `InfracostClient` remains as supported live-query fallback backend |

## Cost: AWS mapping shim

**v0.3.0 update**: this surface is now the **fallback path**. The primary cost-resolution entry point is `tessera.cost.cost_for_call()` (see § "Unified cost_for_call API"). The legacy `aws_mapping.map_request()` + `InfracostClient.query_sku()` direct-call pattern remains callable for backwards-compatibility and is scheduled for removal in v0.4.0 (with `DeprecationWarning` raised at import in v0.3.x).

`tessera/cost/aws_mapping.py` is the lookup table from MCP tool name to Infracost GraphQL query parameters. Two tiers:

- **Builtin (10 operations)** — hardcoded in `_BUILTIN_MAPPING`. Covers `aws_ec2_RunInstances`, `aws_s3_PutObject`, `aws_s3_GetObject`, `aws_rds_CreateDBInstance`, `aws_lambda_InvokeFunction`, `aws_bedrock_InvokeModel`, `aws_eks_CreateCluster`, `aws_ec2_CreateNatGateway`, `aws_ebs_CreateVolume`, `aws_cloudfront_CreateDistribution`. Each is a Python function `(tool_name, args) → InfracostQuery | None` that pulls the relevant args (InstanceType, DBInstanceClass + Engine + MultiAZ, modelId, etc.) and assembles the query.
- **Extended** — loaded at runtime from a YAML cache directory via `load_extended_mappings(cache_dir)`. Each YAML file is a list of `{tool_name, service, attributes, confidence_band, args_used}` entries. The customer's intelligence cache (`~/.tessera/intelligence/mappings/`) is the typical source. Extended wins over builtin when keys collide, so a premium mapping bundle can override a builtin mapping with a more refined query.

`map_request(tool_name, args)` returns `InfracostQuery | None`. `None` means "no mapping" and the caller fail-closes in the don't-block direction (the `predicted_cost` condition returns `False`).

`InfracostQuery` carries:

- `service` — Infracost product family (`"Compute Instance"`, `"AWS S3"`, `"Database Instance"`, `"Amazon Bedrock"`, etc.).
- `region` — AWS region; defaults to `us-east-1` when args don't carry one.
- `attributes` — dict of `{key: value}` filter pairs passed as `attributeFilters` to the GraphQL query.
- `confidence_band` — `"high"` / `"medium"` / `"ceiling"`, propagated to the `predicted_cost` condition's band-multiplier logic (1.0 / 1.5 / 3.0 respectively).
- `args_used` — list of which args fed the query (for debug logging).

## Cost: Infracost GraphQL client

`tessera/cost/infracost.py:InfracostClient` is an async GraphQL client targeting the self-hosted Infracost Cloud Pricing API container. `tessera pricing serve` (a CLI subcommand) launches `infracost/cloud-pricing-api:latest` in Docker as a local sidecar; the operator points `TESSERA_INFRACOST_URL` at `http://localhost:4000/graphql`.

Two queries:

- `Products(...)` — looks up unit price for a SKU given product family, vendor name (`"aws"`), region, and attribute filters. Returns `SkuResult(usd_per_unit, unit, currency, confidence_band)`.
- `usageLastUpdatedAt` — the pricing snapshot identifier. Cached for 1 hour; the proxy refreshes once per minute in a background task and surfaces the result as `pricingSnapshotId` on emitted audit events (see `proxy-enforcement-and-audit.md` for the audit-event integration).

Operational properties:

- **Per-call timeout 200ms** (`timeout_ms` argument). At 100–300ms typical GraphQL response time, this is aggressive — designed to either succeed fast or fail-closed quickly. The proxy's pre-fetch step runs once per `tools/call`; a 200ms cap keeps the hot path bounded.
- **300s cache TTL** for individual SKU results, keyed on `(service, region, attributes)` canonicalized as JSON. A 5-minute cache is the right balance: AWS prices update slowly, but pricing-data corrections happen ~weekly.
- **Fail-closed on every error** — timeout, HTTP error, empty result, missing price field all return `None`. The `predicted_cost` condition then returns `False` (don't block). Cost data being unavailable cannot, by itself, deny a call.

The client is initialized only when `TESSERA_INFRACOST_URL` is set. No client = no cost backend = `predicted_cost` conditions silently skip.

## The price-table consumer pattern (active architecture)

The cost-resolution architecture (Option A in `tessera-intelligence/arch/status/cloud-mappings.md`) is build-time price materialization, active as of v0.3.0:

1. The producer (`tessera-intelligence`) runs every mapping YAML's `infracost_query` once per release, materializes results into `aws-prices-<version>.json`, signs the artifact with the Ed25519 key, ships it alongside the mapping bundle.
2. This repo's `IntelligenceClient` fetches the price-table artifact at refresh time, scans the mappings cache for `*-prices-*.json` files, and loads each into a `PriceTable` instance via `tessera/cost/price_table.py`.
3. The proxy pre-fetch step consults `PriceTable.cost_for_call()` first. On a hit, the result populates `context["cost_cache"][tool_name]` and the live Infracost call is skipped. On a miss, the proxy falls back to `InfracostClient.query_sku()`.
4. The `predicted_cost` condition reads `context["cost_cache"]` — unchanged contract, sub-millisecond at call time when the price table is loaded, no external dependency.

Falls back to live Infracost on cache miss (operation in mappings but not yet in price table — a transitional state when the producer hasn't materialized that operation yet). Infracost remains as the fallback; it is not deprecated.

Ceiling-band cost handling (the Bedrock case) is part of the price-table contract. `price_realm: per_token` entries are multiplied at runtime by `args.maxTokens` to produce a ceiling estimate. Today the InfracostClient stores per-call rates as USD/unit and the band multiplier (3.0 for ceiling) is applied in the condition evaluator; the price-table architecture moves this into the artifact format itself. Same numerical outcome at the policy decision point; cleaner separation between content and evaluator.

## DataVolume async prefetch (P0-15)

`_evaluate_data_volume` in `tessera/policy/conditions.py` supports three estimators: `static_arg_size` (in-memory, free), `s3_get_byte_estimate` (boto3 `s3.head_object`), and `rds_query_result_estimate` (boto3 `rds-data.execute_statement` with an EXPLAIN). The S3/RDS estimators are synchronous boto3 calls — running them inline inside `async def proxy(...)` blocks the event loop for the round-trip (~50–500 ms typical, multi-second on throttling).

The current shape (P0-15) mirrors the blast-radius pattern: prefetch into a per-request cache at the start of the hot path.

- **Gate** — `PolicyEngine.policies_need_data_volume(tool_name, upstream_name)` returns the set of estimators in use by matching policies (or empty set if none). The proxy only prefetches `s3_get_byte_estimate` / `rds_query_result_estimate`; `static_arg_size` doesn't need any I/O.
- **Pure-sync helpers** — `tessera.policy.conditions.s3_head_size_sync(args)` and `rds_explain_size_sync(args)` are top-level functions that take only the tool args and return `(cache_key, size_or_None)`. They are designed to be wrapped in `asyncio.to_thread` and populate the cross-request `_DATA_VOL_LRU` (a `cachetools.TTLCache` of 1000 entries × 300s TTL).
- **Two-tier cache** — Per-request `_data_vol_cache` (dict in `context`, populated by prefetch and consulted by the evaluator first), plus the module-level `_DATA_VOL_LRU` (consulted next, so two concurrent requests for the same `(bucket, key)` make exactly one HeadObject). Both layers fall back to the live boto3 path only when prefetch was bypassed (fixture/test or moto).
- **Failure isolation** — Prefetch exceptions bump `data_volume_prefetch_failures_total` and leave the cache empty; the evaluator falls back to the static-byte-size approximation (`len(json.dumps(args).encode())`) so the call still gets a decision.

The optional `cachetools` dependency is soft: when missing (lean install), `_DATA_VOL_LRU` falls back to a plain dict with no TTL. That's fine for tests; production deployments should install `cachetools` for the eviction semantics.

## Daily-spend state backend

`tessera/state/daily_spend.py:DailySpendState` is the data source for the `cumulative_spend_today` condition. SQLite-backed, per-scope, day-keyed (UTC). The proxy reads it at evaluation time via `context["state_backend"].get_today_spend(scope)`; the engine compares against the policy's `usd_threshold`.

The backend is initialized at lifespan startup and persists across restarts. State directory defaults to `~/.tessera/state/` with override via `TESSERA_STATE_DIR`.

**Auto-write integration (P0-18, shipped 2026-05).** The proxy now writes back per-call cost after a successful allow / observation forward. `_record_daily_spend(state, scope, tool_name, cost_cache)` in `tessera/proxy.py` reads the prefetched estimate from `cost_cache[tool_name]` and schedules `state_backend.add_spend(scope, usd)` via `asyncio.create_task` + `asyncio.to_thread`. The write is non-blocking — the customer's response returns before the SQLite WAL fsync completes. Failures bump `daily_spend_write_failures_total` and never affect the response.

The estimate semantics: for fixed-rate ops (EC2 `RunInstances` $/hr × hours, RDS `CreateDBInstance` $/hr × hours), the pre-fetched value is a good post-call approximation. For usage-priced ops (Bedrock `InvokeModel` $/token, S3 GET egress $/GB), the estimate is a ceiling — the actual cost depends on what the upstream returns. Post-call usage-based reconciliation (reading `response.usage` blocks) is a v0.4.0 candidate; today the v1 write-back uses the pre-call estimate.

Before this wire-up landed, `add_spend()` had zero callers in audited scope — every `cumulative_spend_today` policy silently no-opped. The verification was the P0-18 task body; the gap is now closed. Regression coverage in `tests/unit/state/test_daily_spend.py` pins the persistence contract (scan → record → re-scan → row count + total grow as expected; survives a backend re-open).

## Cross-repo coupling map

The intelligence-content consumer architecture and the producer architecture are intentionally split across repos. The boundary points:

| Consumer (this repo) | Producer (tessera-intelligence) |
|----------------------|----------------------------------|
| `tessera/intelligence/public_key.pem` | `_metadata/public-key.pem` — must be byte-identical |
| `tessera/integrations/aws/blast_radius.py:BlastRadiusBackend` | `tests/blast_radius_stub.py` — share algorithm contract |
| `tessera/cost/aws_mapping.py:map_request` (10 builtins) | `mappings/aws/v1.0.0/*.yaml` (37 ops; richer schema) |
| `tessera/cost/aws_mapping.py:load_extended_mappings` | `mappings/aws/v1.0.0/*.yaml` shipped via pack |
| `tessera/cost/price_table.py:PriceTable` | `aws-prices-v1.0.0.json` signed artifact |
| Catalog fetcher trusts edge tier gate as opportunistic | CloudFront Function does structural-only JWT parse |
| Vendor-MCP policies loaded as pack from cache dir | `vendor-mcp-protection` pack — 7 policies migrated from OSS |

The producer side of the cost/blast-radius/policy-pack chain is the load-bearing source-of-truth; this repo's role is verify-then-consume. Detailed producer-side architecture lives in `tessera-intelligence/arch/status/`:

- Mapping schema + cost-resolution architecture: `cloud-mappings.md`
- Pack file conventions + manifest + tier model: `policy-packs.md`
- Signing chain end-to-end: `signing-and-trust.md`
- Blast-radius rules + stub algorithm: `blast-radius.md`
- Build pipeline + version-immutable publish: `build-publish.md`
- CDN + edge license gating: `distribution-cdn.md`

When Azure mappings ship from the producer side (`tessera-intelligence/arch/improvements/v1.1.0-azure-mappings.md`), the consumer-side change is mechanical: another `load_extended_mappings` directory in the cache, an `azure-prices-<version>.json` artifact loaded identically to the AWS one, no new code paths beyond the parallel-artifact loader. The architecture generalizes across providers; only the `_BUILTIN_MAPPING` per-vendor functions need new entries.
