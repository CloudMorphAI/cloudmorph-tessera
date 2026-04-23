# 02 — Contracts Audit (`contracts/`)

_3 schemas, 200 LoC, all draft-07, all `additionalProperties: true`. Today they are documentation. The 14-day plan turns them into enforcement._

---

## 2.1 What's there today

### [contracts/request.schema.json](../../contracts/request.schema.json) — 73 LoC

`Request` — the central object the MCP gateway and the upstream API exchange.

Required fields (8): `requestId`, `tenantId`, `integrationId`, `action`, `targets`, `payload`, `status`, `createdAt`.

Optional fields: `updatedAt`, `jobId`, `decision`, `reason`. Status enum: `queued | running | completed | failed | cancelled | block`.

**Per-field analysis:**
- `requestId: string` — no format constraint. Should be `pattern: "^req_[a-zA-Z0-9_]{20,40}$"` and validated as such by the resolver.
- `tenantId: string` — no format. Should be `pattern: "^tnt_..."` or UUID.
- `integrationId: string` — same.
- `action: string` — no length cap, no shape constraint. The runtime gateway has a 200-char cap (`routes.ts:470`) — should be in the schema.
- `targets: string[]` — no `minItems`, no per-item pattern, no `uniqueItems`.
- `payload: object` — `additionalProperties: true` makes this opaque. Acceptable for the request envelope but the *inner* payload should be validated against an action-specific schema (see §2.4).
- `status: enum[6]` — `block` is a status but not a verb the engine emits — confusing co-mingling of statuses and decisions. **Untangle: `decision: enum["allow", "deny", "block", "approve", "mutate", "redact", "throttle", "audit_only"]` separate from `status: enum["queued", "running", "completed", "failed", "cancelled"]`.** Removing `block` from status enum is a breaking change — `schemaVersion` bump to 2.
- `createdAt: date-time` — fine.
- `decision: string|null` — should be a constrained enum, not free-form string.
- `reason: string|null` — fine. Add length cap (e.g., 1024).

### [contracts/job.schema.json](../../contracts/job.schema.json) — 76 LoC

`Job` — the executor-facing object. Required (7): `jobId`, `requestId`, `status`, `executorTarget`, `payload`, `createdAt`, `updatedAt`. Optional: `artifacts`, `leaseUntil`, `claimedBy`, `attempts`, `jobToken` (marked `readOnly: true` — good).

**Per-field analysis:**
- Status enum is **5 values** (`queued|running|completed|failed|cancelled`), missing `block` — inconsistent with Request enum. Pick one model (recommend dropping `block` from both, since "blocked" is a *decision* not a *status*).
- `attempts: integer minimum: 0` — needs `maximum` to bound retry storms (suggest 10).
- `artifacts: object[]` with `additionalProperties: true` — opaque. Define an `Artifact` contract (`{kind: enum, uri: string, sizeBytes: int, contentHash: string?}`) and reference it.
- `leaseUntil: date-time` — fine; consider also `leaseStartedAt` for measuring lease duration.
- `claimedBy: string` — should be `executorIdentity: {executorId, hostname, version}` for richer ops.
- `executorTarget: string` — what does this mean? Unclear from the schema alone (description says "account or environment selector"). Either constrain to a known shape (`"<cloud>:<accountId>"`) or split into `cloud` and `accountId`.

### [contracts/approval.schema.json](../../contracts/approval.schema.json) — 51 LoC

`Approval` — pending human-in-the-loop decision. Required (6): `approvalId`, `requestId`, `status`, `requestedBy`, `approvedBy`, `decisionAt`. Status: `pending|approved|denied`.

**Per-field analysis:**
- `requestedBy: string` — string only. Should be `Actor = {kind: enum["agent","human","system"], id: string, displayName: string?}`.
- `approvedBy: string|null` — same.
- `decisionAt: date-time|null` — required but nullable. Inconsistent — drop from `required` if it can be null.
- No `expiresAt`. Approvals should auto-expire (default 1h) — currently nothing forces a decision.
- No multi-party approval (M-of-N). Required for high-blast-radius prod actions.

---

## 2.2 Tightening the existing 3 schemas

For all three:

- (**P1**) Add `schemaVersion: { type: "string", pattern: "^v[0-9]+\\.[0-9]+$" }` to required fields.
- (**P1**) Flip `additionalProperties: false`. Add `x_meta: { type: "object", additionalProperties: true }` for forward-compat.
- (**P0**) Enforce server-side. Reject unknown fields with `{error: "unknown_field", fields: [...]}` rather than silently accepting.
- (**P1**) Untangle `status` (lifecycle) from `decision` (policy outcome).
- (**P1**) Constrain identifiers via `pattern` per the conventions above.
- (**P2**) Add `minLength`/`maxLength` on free-form strings.

**Effort:** 6h tightening + 4h server-side enforcement plumbing. Block B.

---

## 2.3 Missing contracts (the firewall ones)

Five must land in Block B before Block D MCP work proceeds. Three more are nice-to-have for MVP. All authored to draft-07 with `additionalProperties: false`, `schemaVersion: "v0.1"`.

### IntentDeclaration — **P0**, 4h

```json
{
  "$id": "https://contracts.cloudmorph.io/v0.1/intent-declaration.schema.json",
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Intent Declaration",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schemaVersion", "intentId", "sessionId", "tenantId",
    "agentName", "agentVersion", "statedGoal", "structuredVerbs",
    "createdAt"
  ],
  "properties": {
    "schemaVersion": { "type": "string", "pattern": "^v[0-9]+\\.[0-9]+$" },
    "intentId": { "type": "string", "pattern": "^int_[a-zA-Z0-9_]{20,40}$" },
    "sessionId": { "type": "string", "pattern": "^ses_[a-zA-Z0-9_]{20,40}$" },
    "tenantId": { "type": "string" },
    "agentName": { "type": "string", "minLength": 1, "maxLength": 200 },
    "agentVersion": { "type": "string" },
    "agentVendor": { "type": "string", "enum": ["anthropic","openai","cursor","custom","other"] },
    "statedGoal": { "type": "string", "minLength": 1, "maxLength": 4000, "description": "Free-form natural-language goal." },
    "statedSteps": {
      "type": "array",
      "items": { "type": "string", "maxLength": 1000 },
      "maxItems": 50,
      "description": "Optional step plan the agent declared."
    },
    "structuredVerbs": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "read.list","read.describe","read.get","read.search","read.aggregate",
          "analyze","summarize","compare",
          "write.create","write.update","write.delete",
          "execute.run","execute.deploy",
          "notify.send","notify.publish",
          "escalate.approve","escalate.deny",
          "audit.log","audit.export",
          "simulate","dry_run"
        ]
      },
      "minItems": 1,
      "uniqueItems": true,
      "description": "Strict verb taxonomy from the intent vocabulary."
    },
    "constraints": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "maxCostUsd": { "type": "number", "minimum": 0 },
        "maxRowsRead": { "type": "integer", "minimum": 0 },
        "scopeTargets": { "type": "array", "items": { "type": "string" } },
        "scopeRegions": { "type": "array", "items": { "type": "string" } },
        "expiresAt": { "type": "string", "format": "date-time" }
      }
    },
    "ttlSeconds": { "type": "integer", "minimum": 1, "maximum": 3600, "default": 300 },
    "createdAt": { "type": "string", "format": "date-time" },
    "x_meta": { "type": "object", "additionalProperties": true }
  }
}
```

The structured verb vocabulary is **the** non-obvious design choice. Locked at hybrid (free-form `statedGoal` + strict `structuredVerbs[]`) — see [../intent/06_intent_system_design.md](../intent/06_intent_system_design.md). Verbs are extensible per major version bump but not at runtime.

### PolicyDecision — **P0**, 4h

```json
{
  "$id": "https://contracts.cloudmorph.io/v0.1/policy-decision.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schemaVersion", "decisionId", "tenantId", "requestId",
    "outcome", "policyBundleId", "policyBundleVersion",
    "evalTimeMs", "decidedAt"
  ],
  "properties": {
    "schemaVersion": { "type": "string", "pattern": "^v[0-9]+\\.[0-9]+$" },
    "decisionId": { "type": "string", "pattern": "^dec_..." },
    "tenantId": { "type": "string" },
    "requestId": { "type": "string" },
    "intentId": { "type": "string" },
    "sessionId": { "type": "string" },
    "outcome": {
      "type": "string",
      "enum": ["allow","deny","approve","mutate","redact","throttle","audit_only"]
    },
    "reason": { "type": "string", "maxLength": 1024 },
    "matchedRules": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["ruleId","outcome"],
        "properties": {
          "ruleId": { "type": "string" },
          "outcome": { "type": "string" },
          "weight": { "type": "number" }
        }
      }
    },
    "evaluationTrace": {
      "type": "array",
      "items": { "type": "object", "additionalProperties": true },
      "description": "Deterministic step-by-step trace of policy evaluation. Used by cloudmorph_explain_decision."
    },
    "mutatedArguments": {
      "type": "object",
      "additionalProperties": true,
      "description": "When outcome=mutate, the modified args the executor should use."
    },
    "redactionFields": {
      "type": "array",
      "items": { "type": "string" },
      "description": "When outcome=redact, JSON-pointer paths to redact in the response."
    },
    "throttleDelayMs": { "type": "integer", "minimum": 0 },
    "approvalRequest": {
      "type": "object",
      "description": "When outcome=approve, the approval request shape (refs Approval contract)."
    },
    "policyBundleId": { "type": "string" },
    "policyBundleVersion": { "type": "string" },
    "evalTimeMs": { "type": "number", "minimum": 0 },
    "cacheHit": { "type": "boolean" },
    "evidence": {
      "type": "object",
      "additionalProperties": true,
      "description": "Auxiliary signals the policy engine used (intent score, cost estimate, etc.)."
    },
    "decidedAt": { "type": "string", "format": "date-time" },
    "x_meta": { "type": "object", "additionalProperties": true }
  }
}
```

This is the object `cloudmorph_explain_decision` returns. It must be stable forever — change semantics by bumping `schemaVersion`.

### AuditEvent — **P0**, 4h

```json
{
  "$id": "https://contracts.cloudmorph.io/v0.1/audit-event.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schemaVersion", "eventId", "tenantId", "eventType",
    "payload", "occurredAt", "eventHash"
  ],
  "properties": {
    "schemaVersion": { "type": "string", "pattern": "^v[0-9]+\\.[0-9]+$" },
    "eventId": { "type": "string", "pattern": "^evt_..." },
    "prevEventHash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "eventHash": { "type": "string", "pattern": "^[a-f0-9]{64}$", "description": "sha256(canonicalJson({...event without eventHash, signature}))" },
    "signature": { "type": "string", "description": "Optional Ed25519 signature, base64." },
    "tenantId": { "type": "string" },
    "sessionId": { "type": "string" },
    "actorId": { "type": "string" },
    "eventType": {
      "type": "string",
      "enum": [
        "intent.declared","intent.revoked","intent.expired",
        "decision.made",
        "request.received","request.allowed","request.denied","request.mutated","request.approved","request.redacted","request.throttled",
        "approval.requested","approval.granted","approval.rejected","approval.expired",
        "session.started","session.ended",
        "policy.bundle.loaded","policy.bundle.reload.failed",
        "executor.job.claimed","executor.job.heartbeat","executor.job.completed","executor.job.failed",
        "audit.sink.failure","audit.buffer.overflow"
      ]
    },
    "payload": {
      "type": "object",
      "additionalProperties": true,
      "description": "Event-type-specific payload."
    },
    "occurredAt": { "type": "string", "format": "date-time" },
    "x_meta": { "type": "object", "additionalProperties": true }
  }
}
```

Hash chain: `eventHash = sha256(canonicalJson({...event, signature: "", eventHash: ""}))`. Verifier walks the chain and asserts `event[i].prevEventHash === event[i-1].eventHash` for all `i`.

Canonical-JSON: RFC 8785 (JCS). Stable serialization across implementations. No exotic JSON features (no NaN, no `+0`/`-0` distinction, sorted keys).

### RuntimeContext — **P0**, 3h

```json
{
  "$id": "https://contracts.cloudmorph.io/v0.1/runtime-context.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion","sessionId","agentIdentity","capturedAt"],
  "properties": {
    "schemaVersion": { "type": "string" },
    "sessionId": { "type": "string" },
    "agentIdentity": {
      "type": "object",
      "additionalProperties": false,
      "required": ["name","version"],
      "properties": {
        "name": { "type": "string" },
        "version": { "type": "string" },
        "vendor": { "type": "string" },
        "build": { "type": "string" }
      }
    },
    "hostEnv": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "platform": { "type": "string", "enum": ["linux","darwin","win32","other"] },
        "containerized": { "type": "boolean" },
        "kubernetes": { "type": "boolean" }
      }
    },
    "networkContext": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "sourceIp": { "type": "string" },
        "userAgent": { "type": "string" }
      }
    },
    "callerChain": {
      "type": "array",
      "description": "Stack of caller services if this request transited multiple proxies.",
      "items": { "type": "string" }
    },
    "sessionTags": {
      "type": "object",
      "additionalProperties": { "type": "string" },
      "description": "Free-form labels customers can attach to a session for filtering."
    },
    "capturedAt": { "type": "string", "format": "date-time" }
  }
}
```

Passed with every `cloudmorph_request` (and `cloudmorph_proxy`). Optional in MVP — defaults to whatever the MCP server can derive from the connection. Required by the policy engine for time-of-day, network-context, and stack-aware rules.

### ToolCallRequest — **P0**, 3h

The normalized form policy evaluates against. Both `cloudmorph_request` and `cloudmorph_proxy` synthesize this object before evaluation.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion","action","arguments","tenantId"],
  "properties": {
    "schemaVersion": { "type": "string" },
    "action": { "type": "string", "maxLength": 200, "pattern": "^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$" },
    "arguments": { "type": "object", "additionalProperties": true },
    "targets": { "type": "array", "items": { "type": "string" } },
    "tenantId": { "type": "string" },
    "sessionId": { "type": "string" },
    "intentId": { "type": "string" },
    "runtimeContext": { "$ref": "runtime-context.schema.json" },
    "originatingTransport": { "type": "string", "enum": ["http","ws","stdio","sse","proxy"] },
    "receivedAt": { "type": "string", "format": "date-time" }
  }
}
```

The `action` pattern (`^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$`) requires at least one dot — i.e., `cloud.service.verb` shape. Aligns with the existing executor handler taxonomy (`aws.s3.list_buckets`, `databricks.workspace.list_clusters`).

### Session — P1, 2h

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion","sessionId","tenantId","status","startedAt"],
  "properties": {
    "schemaVersion": { "type": "string" },
    "sessionId": { "type": "string" },
    "tenantId": { "type": "string" },
    "agentIdentity": { "$ref": "runtime-context.schema.json#/properties/agentIdentity" },
    "intentIds": { "type": "array", "items": { "type": "string" }, "uniqueItems": true },
    "decisionIds": { "type": "array", "items": { "type": "string" }, "uniqueItems": true },
    "status": { "type": "string", "enum": ["active","ended","expired","abandoned"] },
    "startedAt": { "type": "string", "format": "date-time" },
    "endedAt": { "type": "string", "format": "date-time" },
    "tags": { "type": "object", "additionalProperties": { "type": "string" } }
  }
}
```

### PolicyBundle — P1, 2h

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion","bundleId","version","tenantId","signedAt","signature","contentHash"],
  "properties": {
    "schemaVersion": { "type": "string" },
    "bundleId": { "type": "string" },
    "version": { "type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$" },
    "tenantId": { "type": "string" },
    "rules": { "type": "array", "items": { "type": "object" } },
    "metadata": { "type": "object", "additionalProperties": true },
    "contentHash": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "signature": { "type": "string", "description": "HMAC-SHA256 over contentHash + bundleId + version." },
    "signedAt": { "type": "string", "format": "date-time" }
  }
}
```

Note: the *Rego source* lives in the bundle tarball, not in this schema. This contract describes the bundle envelope.

### RedactionRule — P2, 2h

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion","ruleId","fieldPath","redactionMode"],
  "properties": {
    "schemaVersion": { "type": "string" },
    "ruleId": { "type": "string" },
    "fieldPath": { "type": "string", "description": "JSON Pointer (RFC 6901)." },
    "redactionMode": { "type": "string", "enum": ["mask","drop","hash","tokenize"] },
    "tokenizationKeyId": { "type": "string", "description": "When mode=tokenize, the KMS key id." },
    "appliesTo": {
      "type": "array",
      "items": { "type": "string", "description": "Action patterns (glob) the rule applies to." }
    }
  }
}
```

---

## 2.4 Action-payload schemas (per-action contracts)

Today: every action's `payload` is `{type:"object", additionalProperties:true}`. Means `aws.s3.list_buckets` and `aws.s3.delete_bucket` accept any payload. Bad for the policy engine — can't reason about field-level constraints.

**Proposal:** `contracts/actions/<cloud>/<service>/<verb>.schema.json` per action. Examples:
- `contracts/actions/aws/s3/list_buckets.schema.json`
- `contracts/actions/aws/s3/list_objects.schema.json`
- `contracts/actions/databricks/workspace/list_clusters.schema.json`

Each is a JSON Schema draft-07 object describing the legal shape of `payload`. The MCP server validates `ToolCallRequest.arguments` against `actions/${actionName}.schema.json` at request time; reject 400 on mismatch.

**MVP scope:** ship schemas for the 6 AWS read actions, 6 GCP, 5 Azure, 6 Databricks, 5 Snowflake = **28 schemas, 1h each in autopilot mode = 28h work**. Out of MVP critical path. Block G post-MVP polish, or generate from the resolver function bodies via a small AST walk.

---

## 2.5 Generated types pipeline

Single source of truth: `contracts/*.schema.json`. Generated artifacts:

- **Python (Pydantic):** `cloudmorph-common-py/cloudmorph_common/contracts/*.py` via [datamodel-code-generator](https://github.com/koxudaxi/datamodel-code-generator). One model per schema. Imported by SDK and executors.
- **TypeScript (interfaces):** `cloudmorph-common-ts/src/contracts/*.ts` via [json-schema-to-typescript](https://github.com/bcherny/json-schema-to-typescript). Imported by MCP server and future TS SDK.

Generation is triggered by:
- Pre-commit hook on any change in `contracts/`
- CI step: `make contracts` regenerates and verifies clean diff (fails CI if generated files are out of sync with schemas).

**Scripts:**
```makefile
contracts:
	datamodel-codegen --input contracts/*.schema.json --output cloudmorph-common-py/cloudmorph_common/contracts/ --output-model-type pydantic_v2.BaseModel --use-schema-description
	json2ts -i 'contracts/*.schema.json' -o cloudmorph-common-ts/src/contracts/
	pre-commit run --files cloudmorph-common-py/cloudmorph_common/contracts/* cloudmorph-common-ts/src/contracts/*

contracts-verify:
	$(MAKE) contracts
	git diff --exit-code cloudmorph-common-py/cloudmorph_common/contracts/ cloudmorph-common-ts/src/contracts/
```

**Effort:** 6h to wire generation + CI gate. Block B.

---

## 2.6 Versioning & migration policy

```
v0.x  — pre-MVP. Breaking changes allowed at any time. Contracts are not stable.
v0.1  — MVP target. Changes allowed if all 5 design partners are notified + 7d window.
v1.0  — first stable. Breaking changes require major bump.

Per change:
  * Adding optional field         → minor bump (v1.0 → v1.1)
  * Adding required field         → major bump (v1.1 → v2.0)
  * Removing/renaming field       → major bump
  * Tightening pattern/enum       → major bump (it might reject previously valid input)
  * Loosening pattern/enum        → minor bump
  * Bug fixes (no semantic change)→ patch bump
```

CI gate (`scripts/contracts-version-check.sh`):
1. Diff `schemaVersion` of every changed file against `main`.
2. Diff field-level changes; categorize per the matrix above.
3. Fail CI if `schemaVersion` bump category < required category.

**SDK behavior on version mismatch:** SDK pins the contract schema versions it targets. On request, server returns its own contract version in `serverInfo.contractVersions`. SDK warns on minor mismatch, hard-rejects on major mismatch with `CloudMorphError("contract_version_mismatch", expected=..., actual=...)`.

---

## 2.7 Cross-language readiness

The MCP server (TypeScript) and SDK / executors (Python) both need every contract. The intermediate Common packages (cross/07) own the generated types so neither language can drift.

Verification matrix:

| Contract | Pydantic generated | TS interface generated | Used by |
|---|:-:|:-:|---|
| Request | ✓ | ✓ | SDK (request body), MCP (response shape), upstream API |
| Job | ✓ | ✓ | Executors (incoming claim payload), upstream API |
| Approval | ✓ | ✓ | MCP `cloudmorph_approve`/`cloudmorph_deny`, upstream API |
| IntentDeclaration | ✓ | ✓ | SDK `firewall.declare_intent`, MCP `cloudmorph_declare_intent`, policy engine |
| PolicyDecision | ✓ | ✓ | MCP `cloudmorph_explain_decision`, policy engine output, audit emitter |
| AuditEvent | ✓ | ✓ | Audit emitter (TS), executors (Py — they emit too) |
| RuntimeContext | ✓ | ✓ | MCP request handler (synthesizes), policy engine input |
| ToolCallRequest | ✓ | ✓ | MCP request handler, policy engine input |
| Session | ✓ | ✓ | MCP session store, audit |
| PolicyBundle | ✓ | ✓ | MCP bundle loader, ops tooling |
| RedactionRule | ✓ | ✓ | MCP `cloudmorph_redact_preview`, policy engine |

Future Go/Rust SDKs: same generation flow with `quicktype` or `oapi-codegen`-style tools.

---

## 2.8 Severity table

| Item | Severity | Effort |
|---|---|---:|
| Author IntentDeclaration | P0 | 4h |
| Author PolicyDecision | P0 | 4h |
| Author AuditEvent | P0 | 4h |
| Author RuntimeContext | P0 | 3h |
| Author ToolCallRequest | P0 | 3h |
| Author Session | P1 | 2h |
| Author PolicyBundle | P1 | 2h |
| Author RedactionRule | P2 | 2h |
| Add `schemaVersion` to existing 3 + flip `additionalProperties: false` | P1 | 6h |
| Untangle `status`/`decision` in Request | P1 | 2h |
| Server-side validation enforcement at MCP boundary | P0 | 4h |
| Generated types pipeline (Pydantic + TS) | P1 | 6h |
| CI gate diffing `schemaVersion` against base branch | P1 | 3h |
| Per-action payload schemas (28 of them) | P2 | 28h |

**MVP critical-path total: ~38h. Block B.** Per-action payload schemas slip to post-MVP.

---

## 2.9 Out of scope (deliberately)

- Protobuf/gRPC contracts. JSON Schema is enough until we have a second-language SDK in flight.
- OpenAPI for the upstream Control Center HTTP API. Different concern, lives upstream.
- Avro/Schema Registry for audit events. Premature; revisit if we adopt Kafka.
- Contract documentation site (`docs.cloudmorph.io/contracts`). Spin up after v1.0 lock.

---

## 2.10 Source links

- [contracts/request.schema.json](../../contracts/request.schema.json)
- [contracts/job.schema.json](../../contracts/job.schema.json)
- [contracts/approval.schema.json](../../contracts/approval.schema.json)

Implementation: Block B. Consumers: Block C (common-py), Block D (MCP server), Block F (SDK), Block G (executors).
