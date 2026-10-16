# Synthetic post-call CRM workflow fixture

**Synthetic local fixture only.** Every Acme name, call, deal, request, task and draft in this example is fictional. This backend never contacts a CRM, meeting service, customer, vendor or email provider. It does not send drafts, process payments, call a model provider, or connect to cloud services.

This CM-096 slice supplies a resettable in-memory fixture and a policy-agnostic adapter contract for the YC post-call workflow: read the fictional Acme call/deal, update call notes and next step, create/read an engineering task, create/read an unsent follow-up/proposal draft, and preserve a requested 30% discount in a separate internal review task when a caller-side policy denial prevents dispatch. The scripted hero run starts with discount 0 and leaves it at 0 after the denial; separate policy-boundary tests use the fixture's 10% seed.

## Integration boundary

The module exports `FIXTURE_VERSION`, `FIXTURE_LABEL`, `CALL_ID`, `DEAL_ID`, `getPostCallToolCatalog()`, and `createPostCallFixture()`. The fixture's `dispatch(invocation)` is policy-agnostic. Its constructor accepts `{ run_id?: string, starting_discount_pct?: number }`; the default seed is 10% for the separate boundary tests, while `run-demo.mjs` explicitly seeds 0%. Reset restores that selected seed. It does **not** implement or claim Tessera policy enforcement. Arjun's CM-098 gateway integration must evaluate each proposed action before it calls dispatch; a denied `crm.update_deal` call must never enter the CRM adapter. The scripted demo and test suite use clearly labeled caller-side stubs only to demonstrate the boundary; they are not Tessera results or model execution. The actual adapter contract for CM-098 is the in-process fixture API and strict tool catalog described below. The raw adapter must not be exposed directly to a browser or bypassed by UI code. There is no policy-administration tool or action in the catalog.

The proposed local transport for the frontend/policy integration is one call per request:

    POST /api/post-call/tools/{tool_name}

    {
      "request_id": "run-postcall-001",
      "tool_use_id": "call-001",
      "arguments": { "deal_id": "DEAL-ACME-001" }
    }

The route supplies `tool_name`; the corresponding adapter call is `fixture.dispatch({ request_id, tool_use_id, tool_name, arguments })`.

The following successful `crm.get_deal` response is from the hero run's 0% seed (the constructor default for separate boundary tests is 10%):

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
        "discount_pct": 0
      },
      "adapter": {
        "name": "crm",
        "call_id": "ADAPTER-<deterministic-16-hex-id>",
        "entries": 1,
        "writes": 0,
        "totals": { "entries": 1, "writes": 0 }
      }
    }

A fixture error shape is `{ "request_id", "tool_use_id", "tool_name", "status": "error", "error": { "code", "message" }, "adapter": { "name", "call_id", "entries", "writes", "totals" } }`. Unknown tools use `UNKNOWN_TOOL`, missing fixture IDs use `FIXTURE_RECORD_NOT_FOUND`, and idempotency-key or invocation-pair reuse with conflicting content uses `IDEMPOTENCY_CONFLICT`. Exact invocation replay adds `replayed: true` and reports zero new entries/writes. A policy block response is owned by the policy gateway and is **not** produced by this fixture. The HTTP transport is a proposed integration contract; this CM-096 backend slice provides the in-process dispatcher, not a server or hosted service.

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

The review-task and proposal APIs preserve the requested 30% as a request only. In the hero run, deal state remains 0% when the scripted caller stub blocks the update before calling the adapter. The separate 10% boundary test shows an unchanged 10% seed under a denied call. Creating a review task is a separate tool call that needs its own fresh CM-098 gateway evaluation; this fixture never auto-creates it after a denial.

## Example caller sequence

1. An integration caller separately evaluates meetings.get_call, crm.get_deal, and call-note/next-step crm.update_deal. Only allowed calls enter the fixture.
2. For a 30% crm.update_deal proposal, the policy gateway must record its denial and not call the adapter, yielding zero CRM adapter entries/writes for that proposed call.
3. The caller separately evaluates a new tasks.create call for internal_discount_review with requested_discount_pct: 30 and pending approval.
4. A fresh crm.get_deal reads the unchanged seed (0% in the hero run; 10% in the separate boundary test). Follow-up and proposal drafts remain local, pending where terms are present, and unsent.

`run-demo.mjs` scripts this sequence with a local stub that denies discounts above 10%; it does not execute Tessera or prove a real gateway decision. CM-098 must provide the actual gateway evaluations and their audit evidence.

The caller owns policy decision/audit correlation. The fixture returns request_id, tool_use_id, and deterministic adapter.call_id for a caller to correlate; it does not fabricate a Tessera receipt or claim the policy block happened.

## Run and test

Node.js 20+; no install or network required:

    node examples/post_call_demo/backend/run-demo.mjs
    node --test examples/post_call_demo/backend/tests/fixture.test.mjs

`run-demo.mjs` prints a concise scenario receipt and labels the caller and policy decision as scripted stubs. Tests cover positive reads/writes/readback, a caller-side denied-dispatch boundary with zero CRM adapter entry/write, separately correlated fresh review-task preservation, distinct 0% hero and 10% boundary seeds, strict schemas, malformed numeric values, no partial mutation, request/tool-use and task/draft idempotency conflicts and replays, generated-ID validation, deterministic IDs and reset. The code under test is `backend/index.mjs`; the eleven scenarios are in `backend/tests/fixture.test.mjs`. This is synthetic fixture evidence only: no actual model/provider request, Tessera gateway call, customer acceptance or vendor integration is performed in this slice.

Integration reminder: the repository-root `README.md` is outside this backend ownership slice; add a pointer to this example when the shared integration documentation is updated.
