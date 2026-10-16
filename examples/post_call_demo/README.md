# Synthetic post-call CRM workflow fixture

**Synthetic local fixture only.** Every Acme name, call, deal, request, task and draft in this example is fictional. This backend never contacts a CRM, meeting service, customer, vendor or email provider. It does not send drafts, process payments, call a model provider, or connect to cloud services.

This CM-096 slice supplies a resettable in-memory fixture and a policy-agnostic adapter contract for the YC post-call workflow: read the fictional Acme call/deal, update call notes and next step, create/read an engineering task, create/read an unsent follow-up/proposal draft, and preserve a requested 30% discount in a separate internal review task while the deal remains at 10% when a caller-side policy denial prevents dispatch.

## Integration boundary

The module exports createPostCallFixture() and dispatch(invocation) only as an in-process adapter surface. It does **not** implement or claim Tessera policy enforcement. CM-098 must evaluate each proposed action before it calls dispatch; a denied crm.update_deal call must never enter the CRM adapter. The test suite includes a clearly labeled caller-side test double only to prove the zero-dispatch boundary; it is not a Tessera result or model execution. The raw adapter must not be exposed directly to a browser or bypassed by UI code. There is no policy-administration tool or action in the catalog.

The proposed local transport for the frontend/policy integration is one call per request:

    POST /api/post-call/tools/{tool_name}

    {
      "request_id": "run-postcall-001",
      "tool_use_id": "call-001",
      "arguments": { "deal_id": "DEAL-ACME-001" }
    }

A successful adapter response is:

    {
      "request_id": "run-postcall-001",
      "tool_use_id": "call-001",
      "tool_name": "crm.get_deal",
      "status": "ok",
      "result": {
        "deal_id": "DEAL-ACME-001",
        "label": "SYNTHETIC LOCAL FIXTURE - NO VENDOR CONNECTION",
        "account_name": "Acme Field Systems (fictional)",
        "stage": "evaluation",
        "notes": "",
        "next_step": "",
        "discount_pct": 10
      },
      "adapter": {
        "name": "crm",
        "call_id": "ADAPTER-<deterministic-16-hex-id>",
        "entries": 1,
        "writes": 0,
        "totals": { "entries": 1, "writes": 0 }
      }
    }

A fixture error uses status "error" with an error object, for example code INVALID_ARGUMENTS. Unknown tools use UNKNOWN_TOOL, missing fixture IDs use FIXTURE_RECORD_NOT_FOUND, and idempotency-key reuse with different content uses IDEMPOTENCY_CONFLICT. A policy block response is owned by the policy gateway and is **not** produced by this fixture. The HTTP transport is a proposed integration contract; this CM-096 backend slice provides the in-process dispatcher, not a server or hosted service.

adapter.entries and adapter.writes are per-call deltas; totals are cumulative within the run. Validation happens before adapter entry and a mixed invalid update makes no partial mutation. The request_id/tool_use_id pair is the invocation idempotency key, and adapter.call_id is deterministic from that pair within the run. Replaying the same pair and same normalized call returns the saved response with replayed=true and zero new adapter entries/writes; reusing the pair with changed content returns IDEMPOTENCY_CONFLICT before adapter entry. Task/draft IDs are also deterministic within a run from their explicit idempotency_key, so equivalent create calls under a fresh tool-use ID still return the existing record without a second write.

## Tool contract

All argument objects reject unknown fields. getPostCallToolCatalog() exposes strict JSON schemas with additionalProperties=false for policy/UI integration. The catalog contains only:

| Tool | Arguments and behavior |
| --- | --- |
| meetings.get_call | { "call_id": "CALL-ACME-001" }; reads a fixed fictional call summary. |
| crm.get_deal | { "deal_id": "DEAL-ACME-001" }; reads current fixture fields. |
| crm.update_deal | { "deal_id": "DEAL-ACME-001", "fields": { "notes"?: string, "next_step"?: string, "discount_pct"?: number } }; updates only supplied fields. Percentage must be finite, 0–100, at most two decimal places. This schema does not encode the Acme 10% policy; CM-098 owns that policy. |
| tasks.create | Creates either engineering_follow_up or internal_discount_review, with a required idempotency_key. Review tasks require numeric requested_discount_pct and return approval_status: "pending". |
| tasks.read | { "task_id": "TASK-<16 uppercase hex>" }; readback for this run. |
| drafts.create | Creates follow_up or proposal draft with required idempotency_key. Proposal terms include { "requested_discount_pct": number, "status": "pending" }. Returned drafts always have delivery_status: "not_sent"; there is no send action. |
| drafts.read | { "draft_id": "DRAFT-<16 uppercase hex>" }; readback for this run. |

The review-task and proposal APIs preserve the requested 30% as a request only. Deal state remains 10% when an outer policy gateway blocks the update before calling the adapter. Creating a review task is a separate tool call that needs its own fresh gateway evaluation; this fixture never auto-creates it after a denial.

## Example caller sequence

1. The mediation layer evaluates meetings.get_call, crm.get_deal, then the call-note/next-step crm.update_deal separately. Only allowed calls enter the fixture.
2. It sends the same crm.update_deal tool with { "discount_pct": 30 } through Tessera. On a policy denial, the gateway records its decision and does not call the adapter; the CRM adapter entry/write delta for that proposed call is zero.
3. A new, separately evaluated tasks.create call records an internal_discount_review task with requested_discount_pct: 30, status awaiting_internal_review, and approval pending.
4. A fresh crm.get_deal reads discount_pct: 10. Follow-up and proposal drafts remain local, pending where terms are present, and unsent.

The caller owns policy decision/audit correlation. The fixture returns request_id, tool_use_id, and deterministic adapter.call_id for a caller to correlate; it does not fabricate a Tessera receipt or claim the policy block happened.

## Run and test

Node.js 20+; no install or network required:

    node examples/post_call_demo/backend/run-demo.mjs
    node --test examples/post_call_demo/backend/tests/fixture.test.mjs

`run-demo.mjs` prints a concise scenario receipt and labels the caller and policy decision as scripted stubs. Tests cover positive reads/writes/readback, a caller-side denied-dispatch boundary with zero CRM adapter entry/write, separately correlated fresh review-task preservation, strict schemas, malformed numeric values, no partial mutation, request/tool-use and task/draft idempotency conflicts and replays, generated-ID validation, deterministic IDs and reset. The code under test is `backend/index.mjs`; the ten scenarios are in `backend/tests/fixture.test.mjs`. This is synthetic fixture evidence only: no actual model/provider request, Tessera gateway call, customer acceptance or vendor integration is performed in this slice.
