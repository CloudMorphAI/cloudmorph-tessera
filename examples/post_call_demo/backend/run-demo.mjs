import assert from "node:assert/strict";
import { CALL_ID, DEAL_ID, createPostCallFixture } from "./index.mjs";

const runId = "post-call-scripted-demo-001";
const demo = createPostCallFixture({ run_id: runId });
const evaluations = [];

function evaluateAndDispatch(tool_use_id, tool_name, arguments_, request_id = runId) {
  const invocation = { request_id, tool_use_id, tool_name, arguments: arguments_ };
  // Deliberately local demonstration stub. It is not a Tessera gateway or an LLM.
  const deny = tool_name === "crm.update_deal"
    && typeof arguments_?.fields?.discount_pct === "number"
    && arguments_.fields.discount_pct > 10;
  const evaluation = {
    request_id,
    tool_use_id,
    tool_name,
    source: "scripted-local-policy-stub",
    outcome: deny ? "deny" : "allow",
  };
  evaluations.push(evaluation);
  if (deny) {
    return {
      request_id,
      tool_use_id,
      tool_name,
      status: "not_dispatched",
      adapter: { entries: 0, writes: 0 },
    };
  }
  return demo.dispatch(invocation);
}

const call = evaluateAndDispatch("lookup-call", "meetings.get_call", { call_id: CALL_ID });
const deal = evaluateAndDispatch("lookup-deal", "crm.get_deal", { deal_id: DEAL_ID });
const note = evaluateAndDispatch("save-call-notes", "crm.update_deal", {
  deal_id: DEAL_ID,
  fields: {
    notes: "Fictional post-call recap: schedule a technical evaluation.",
    next_step: "Prepare an engineering follow-up.",
  },
});

const blockedDiscount = evaluateAndDispatch("request-30-percent", "crm.update_deal", {
  deal_id: DEAL_ID,
  fields: { discount_pct: 30 },
});
assert.equal(blockedDiscount.status, "not_dispatched");
assert.deepEqual(blockedDiscount.adapter, { entries: 0, writes: 0 });
assert.deepEqual(demo.snapshot().counters.crm, { entries: 2, writes: 1 });

const review = evaluateAndDispatch("fresh-review-task", "tasks.create", {
  task_kind: "internal_discount_review",
  deal_id: DEAL_ID,
  title: "Review requested fictional discount",
  details: "The requested 30% discount remains pending; do not grant or apply it.",
  requested_discount_pct: 30,
  idempotency_key: runId + ":review-task",
}, runId + "-fresh-review");
const dealAfterBlock = evaluateAndDispatch("verify-deal-after-block", "crm.get_deal", { deal_id: DEAL_ID });
const proposal = evaluateAndDispatch("draft-pending-proposal", "drafts.create", {
  draft_kind: "proposal",
  deal_id: DEAL_ID,
  title: "Fictional Acme proposal draft",
  body: "Requested terms are subject to separate internal approval. Draft only; do not send.",
  commercial_terms: { requested_discount_pct: 30, status: "pending" },
  idempotency_key: runId + ":proposal",
});
const proposalRead = evaluateAndDispatch("read-pending-proposal", "drafts.read", { draft_id: proposal.result.draft_id });

assert.equal(call.status, "ok");
assert.equal(deal.status, "ok");
assert.equal(note.status, "ok");
assert.equal(review.status, "ok");
assert.equal(review.result.requested_discount_pct, 30);
assert.equal(review.result.approval_status, "pending");
assert.equal(dealAfterBlock.result.discount_pct, 10);
assert.equal(proposalRead.result.commercial_terms.status, "pending");
assert.equal(proposalRead.result.delivery_status, "not_sent");

console.log(JSON.stringify({
  evidence_kind: "scripted-local-fixture-run",
  fixture_version: demo.fixture_version,
  run_id: runId,
  actual_llm_execution: false,
  actual_tessera_gateway_evaluation: false,
  evaluations,
  scenario: {
    label: call.result.label,
    account: call.result.account_name,
    discount_before: deal.result.discount_pct,
    notes_write: { status: note.status, adapter: note.adapter },
    oversized_discount: {
      request_id: blockedDiscount.request_id,
      tool_use_id: blockedDiscount.tool_use_id,
      status: blockedDiscount.status,
      adapter: blockedDiscount.adapter,
    },
    fresh_review: {
      request_id: review.request_id,
      tool_use_id: review.tool_use_id,
      requested_discount_pct: review.result.requested_discount_pct,
      approval_status: review.result.approval_status,
    },
    discount_after_block: dealAfterBlock.result.discount_pct,
    proposal: {
      status: proposalRead.result.commercial_terms.status,
      delivery_status: proposalRead.result.delivery_status,
    },
    adapter_totals: demo.snapshot().counters,
  },
}, null, 2));
