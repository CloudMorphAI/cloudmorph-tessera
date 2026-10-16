# Synthetic post-call workflow: real Tessera gate, local adapters

**Synthetic local data only.** Every Acme call, deal, task and draft is fictional.
No CRM, meeting, task vendor, email or payment service is connected. Drafts remain
unsent. The actual Tessera proxy checks each proposed call before the isolated
Node fixture can execute it. Operator policy comes from local read-only YAML;
this example does not establish authenticated policy administration or tenant isolation.

## Run the integrated local UI

Use WSL Ubuntu, Python3.12+ and Linux Node20+ on PATH. From this repository root,
create an isolated virtual environment and install the checked-out runtime with
its `runtime`, `intelligence` and `dev` extras (the latter supplies jsonschema and
checks):

```bash
python3 -m venv .venv-post-call
. .venv-post-call/bin/activate
python -m pip install '.[runtime,intelligence,dev]'
python examples/post_call_demo/server.py
```

Open `http://127.0.0.1:8795`. The default **scripted** mode makes zero provider calls.
The UI runs a fixed synthetic Acme sequence: read call/deal, save notes and next
step, create engineering follow-up, draft unsent follow-up, propose a30% discount
through the same `crm.update_deal` tool, create a separate pending30% review task
after the block, and read the unchanged0% discount. Every call has its own actual
Tessera audit receipt. Local SQLite audit records are drained, correlated and
hash-chain verified before a completed run is returned. A full valid mixed
notes+30% update blocks atomically; exhaustive integer0..100 checks prove the cap.
The gateway deliberately accepts only integer percentages; malformed input is a
contract rejection, never a fabricated policy block.

The actual HTTP interface is GET `/api/health`, GET `/api/policy` and POST
`/api/run` with exactly `{ "mode": "scripted", "idempotency_key": "unique-key" }`
and matching `Idempotency-Key`. POST requires the exact loopback Host and Origin.
There is no public arbitrary-tool or policy-write endpoint. These loopback checks
are not authentication. A ten-run process limit and cached idempotency receipts
prevent accidental repeated execution; changed mode under the same key conflicts.

### Explicit real-model mode

Only after offline checks and independent review, the operator may start the
server with `--enable-nova --output-dir <new-evidence-directory>`. This opts into
**one** bounded Amazon Bedrock Nova Micro attempt using the existing Karn provider,
verified STS identity, current model availability and AWS Price List rates.
Startup fails closed if the eight-call conservative estimate exceeds USD0.01.
The UI requires a separate explicit Nova click. No automatic retries, model
fallback, provider setup or cloud resource changes occur. A failed attempt is
consumed and its incomplete evidence cannot be presented as a completed run.

This is a constrained eight-turn model workflow: one selected tool is offered
per turn. The real model supplies arguments and generated notes/task/draft text;
actual gate and fixture results return to that same session. It does not prove
general autonomous planning, a vendor integration, human approval or customer
acceptance. Model/provider labels are returned only after actual provider calls.
Historical AP or Hermes transcripts are separate evidence.

```bash
python -m pytest -q examples/post_call_demo/policy_tests.py
python -m unittest -v examples.post_call_demo.model_tests
node --test examples/post_call_demo/backend/tests/fixture.test.mjs
python examples/post_call_demo/verification/verify_http.py
```

`verification/README.md` describes raw actual browser capture. Preserve the
unmodified master and JSON traces; Krish's CM100 storyboard determines editorial
cuts. A saved customer-policy segment must be omitted unless its authentication
is independently verified. No deployed or published demo is implied by this source.

## Standalone fixture contract (CM096)

The remainder documents the policy-agnostic Node module. Its separate
`backend/run-demo.mjs` uses an explicitly labeled scripted gate stub; it is not
Tessera or model evidence. The integrated server above replaces that stub with
`policy_integration.py` and an actual Tessera MCP proxy, keeping raw adapters private.
The hero explicitly seeds0%; the fixture constructor's default10% remains only
for separate adapter boundary tests.

## Integration boundary

The module exports `FIXTURE_VERSION`, `FIXTURE_LABEL`, `CALL_ID`, `DEAL_ID`, `getPostCallToolCatalog()`, and `createPostCallFixture()`. The fixture's `dispatch(invocation)` is policy-agnostic. Its constructor accepts `{ run_id?: string, starting_discount_pct?: number }`; the default seed is 10% for the separate boundary tests, while `run-demo.mjs` explicitly seeds 0%. Reset restores that selected seed. It does **not** implement or claim Tessera policy enforcement. The CM-098 gateway evaluates each proposed action before it calls dispatch; a denied `crm.update_deal` call must never enter the CRM adapter. The scripted demo and test suite use clearly labeled caller-side stubs only to demonstrate the boundary; they are not Tessera results or model execution. The actual adapter contract for CM-098 is the in-process fixture API and strict tool catalog described below. The raw adapter must not be exposed directly to a browser or bypassed by UI code. There is no policy-administration tool or action in the catalog.

The earlier fixture-only transport proposal below is **not exposed** by the integrated server; it illustrates the internal invocation shape only:

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
