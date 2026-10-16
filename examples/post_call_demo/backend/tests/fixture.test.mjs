import test from "node:test";
import assert from "node:assert/strict";
import { CALL_ID, DEAL_ID, FIXTURE_VERSION, createPostCallFixture, getPostCallToolCatalog } from "../index.mjs";

const context = (tool_use_id, request_id = "run-postcall-001") => ({ request_id, tool_use_id });
const call = (fixture, tool_name, arguments_, tool_use_id, request_id) => fixture.dispatch({
  ...context(tool_use_id, request_id), tool_name, arguments: arguments_,
});

function fixture() {
  return createPostCallFixture({ run_id: "cm095-test-run" });
}

test("fixture catalog is strict and exposes only the seven synthetic data actions", () => {
  const catalog = getPostCallToolCatalog();
  assert.deepEqual(catalog.map((tool) => tool.name), [
    "meetings.get_call", "crm.get_deal", "crm.update_deal", "tasks.create", "tasks.read", "drafts.create", "drafts.read",
  ]);
  for (const tool of catalog) assert.equal(tool.inputSchema.additionalProperties, false);
  assert.equal(catalog.some((tool) => tool.name.startsWith("policy.")), false);
  assert.equal(catalog.some((tool) => tool.name.endsWith(".send")), false);
  assert.equal(FIXTURE_VERSION, "post-call-fixtures/1.0.0");
});

test("synthetic call, deal note update, task, and pending proposal draft read back in one run", () => {
  const demo = fixture();
  const meeting = call(demo, "meetings.get_call", { call_id: CALL_ID }, "use-call");
  assert.equal(meeting.status, "ok");
  assert.match(meeting.result.label, /SYNTHETIC LOCAL FIXTURE/);

  const updated = call(demo, "crm.update_deal", {
    deal_id: DEAL_ID,
    fields: { notes: "Fictional post-call recap: prepare a technical evaluation.", next_step: "Create an engineering follow-up task." },
  }, "use-crm-note");
  assert.equal(updated.status, "ok");
  assert.equal(updated.adapter.entries, 1);
  assert.equal(updated.adapter.writes, 1);

  const dealRead = call(demo, "crm.get_deal", { deal_id: DEAL_ID }, "read-deal");
  assert.equal(dealRead.result.notes, "Fictional post-call recap: prepare a technical evaluation.");
  assert.equal(dealRead.result.discount_pct, 10);

  const task = call(demo, "tasks.create", {
    task_kind: "engineering_follow_up",
    deal_id: DEAL_ID,
    title: "Prepare synthetic engineering evaluation",
    details: "Review fictional integration requirements; do not contact an external party.",
    idempotency_key: "run-postcall-001:engineering-task",
  }, "create-engineering-task");
  assert.equal(task.status, "ok");
  assert.match(task.result.task_id, /^TASK-[A-F0-9]{16}$/);
  const taskRead = call(demo, "tasks.read", { task_id: task.result.task_id }, "read-engineering-task");
  assert.equal(taskRead.result.title, "Prepare synthetic engineering evaluation");

  const draft = call(demo, "drafts.create", {
    draft_kind: "proposal",
    deal_id: DEAL_ID,
    title: "Fictional Acme proposal - draft only",
    body: "Synthetic terms are pending internal review and have not been granted or sent.",
    commercial_terms: { requested_discount_pct: 30, status: "pending" },
    idempotency_key: "run-postcall-001:proposal-draft",
  }, "create-proposal-draft");
  assert.equal(draft.status, "ok");
  assert.equal(draft.result.commercial_terms.status, "pending");
  assert.equal(draft.result.delivery_status, "not_sent");
  const draftRead = call(demo, "drafts.read", { draft_id: draft.result.draft_id }, "read-proposal-draft");
  assert.equal(draftRead.result.commercial_terms.requested_discount_pct, 30);
  assert.equal(draftRead.result.commercial_terms.status, "pending");
  assert.equal(draftRead.result.delivery_status, "not_sent");

  const totals = demo.snapshot().counters;
  assert.deepEqual(totals.meetings, { entries: 1, writes: 0 });
  assert.deepEqual(totals.crm, { entries: 2, writes: 1 });
  assert.deepEqual(totals.tasks, { entries: 2, writes: 1 });
  assert.deepEqual(totals.drafts, { entries: 2, writes: 1 });
});

test("a caller-side denial skips dispatch; a fresh review task preserves 30% and CRM remains at 10%", () => {
  const demo = fixture();
  const proposed = {
    ...context("blocked-discount-tool-use"),
    tool_name: "crm.update_deal",
    arguments: { deal_id: DEAL_ID, fields: { discount_pct: 30 } },
  };
  const before = demo.snapshot().counters.crm;

  // This is only a caller contract test double. It is not Tessera and is not part of the fixture adapter.
  const decision = { source: "synthetic-caller-test-double", outcome: "deny" };
  const result = decision.outcome === "allow"
    ? demo.dispatch(proposed)
    : { request_id: proposed.request_id, tool_use_id: proposed.tool_use_id, tool_name: proposed.tool_name, status: "not_dispatched", adapter: { entries: 0, writes: 0 } };
  assert.equal(result.status, "not_dispatched");
  assert.deepEqual(result.adapter, { entries: 0, writes: 0 });
  assert.deepEqual(demo.snapshot().counters.crm, before);

  const review = call(demo, "tasks.create", {
    task_kind: "internal_discount_review",
    deal_id: DEAL_ID,
    title: "Review requested fictional discount",
    details: "The requested 30% discount is pending internal review; do not grant or apply it.",
    requested_discount_pct: 30,
    idempotency_key: "run-postcall-001:review-after-denial",
  }, "fresh-review-task-use", "run-postcall-review-002");
  assert.equal(review.status, "ok");
  assert.equal(review.result.requested_discount_pct, 30);
  assert.equal(review.result.approval_status, "pending");
  assert.equal(review.result.status, "awaiting_internal_review");

  const unchanged = call(demo, "crm.get_deal", { deal_id: DEAL_ID }, "read-deal-after-denial", "run-postcall-review-002");
  assert.equal(unchanged.result.discount_pct, 10);
  assert.deepEqual(demo.snapshot().counters.crm, { entries: before.entries + 1, writes: before.writes });
});

test("strict validation rejects unknown and mixed fields without partial mutation or adapter entry", () => {
  const demo = fixture();
  const initial = demo.snapshot().state.deals[DEAL_ID];
  const unknownField = call(demo, "crm.update_deal", {
    deal_id: DEAL_ID,
    fields: { notes: "would be partial", region: "unsupported" },
  }, "unknown-field");
  assert.equal(unknownField.status, "error");
  assert.equal(unknownField.error.code, "INVALID_ARGUMENTS");
  assert.equal(unknownField.adapter.entries, 0);

  const mixedPayload = call(demo, "crm.update_deal", {
    deal_id: DEAL_ID,
    fields: { notes: "would be partial", discount_pct: "30" },
  }, "mixed-payload");
  assert.equal(mixedPayload.status, "error");
  assert.equal(mixedPayload.adapter.entries, 0);
  assert.deepEqual(demo.snapshot().state.deals[DEAL_ID], initial);
  assert.deepEqual(demo.snapshot().counters.crm, { entries: 0, writes: 0 });

  const unsupportedTool = call(demo, "policy.update", { policy_id: "cap" }, "policy-admin-probe");
  assert.equal(unsupportedTool.status, "error");
  assert.equal(unsupportedTool.error.code, "UNKNOWN_TOOL");
  assert.equal(demo.snapshot().counters.crm.entries, 0);
});

test("discount numeric validation rejects strings, non-finite values, out-of-range and excess precision", () => {
  const demo = fixture();
  for (const value of ["30", Number.NaN, Number.POSITIVE_INFINITY, -1, 101, 10.123]) {
    const result = call(demo, "crm.update_deal", { deal_id: DEAL_ID, fields: { discount_pct: value } }, "bad-discount-" + String(value));
    assert.equal(result.status, "error");
    assert.equal(result.error.code, "INVALID_ARGUMENTS");
    assert.equal(result.adapter.entries, 0);
  }
  assert.equal(demo.snapshot().state.deals[DEAL_ID].discount_pct, 10);
  assert.deepEqual(demo.snapshot().counters.crm, { entries: 0, writes: 0 });
});

test("create idempotency returns stable IDs, avoids repeat writes, and rejects changed content", () => {
  const demo = fixture();
  const args = {
    task_kind: "engineering_follow_up",
    deal_id: DEAL_ID,
    title: "Prepare evaluation",
    details: "Fictional task only.",
    idempotency_key: "same-task-key",
  };
  const first = call(demo, "tasks.create", args, "task-create-1");
  const replay = call(demo, "tasks.create", args, "task-create-2");
  assert.equal(first.result.task_id, replay.result.task_id);
  assert.equal(first.adapter.writes, 1);
  assert.equal(replay.adapter.entries, 1);
  assert.equal(replay.adapter.writes, 0);
  assert.deepEqual(demo.snapshot().counters.tasks, { entries: 2, writes: 1 });

  const conflict = call(demo, "tasks.create", { ...args, title: "Different task" }, "task-create-conflict");
  assert.equal(conflict.status, "error");
  assert.equal(conflict.error.code, "IDEMPOTENCY_CONFLICT");
  assert.equal(conflict.adapter.writes, 0);
  assert.equal(Object.keys(demo.snapshot().state.tasks).length, 1);
});

test("request/tool-use replay is idempotent and changed content is rejected before adapter entry", () => {
  const demo = fixture();
  const args = { deal_id: DEAL_ID, fields: { notes: "First correlated update." } };
  const first = call(demo, "crm.update_deal", args, "same-tool-use", "same-request");
  const replay = call(demo, "crm.update_deal", args, "same-tool-use", "same-request");
  assert.equal(first.result.deal.notes, "First correlated update.");
  assert.equal(replay.replayed, true);
  assert.equal(replay.result.deal.notes, "First correlated update.");
  assert.deepEqual(replay.adapter, {
    ...first.adapter,
    entries: 0,
    writes: 0,
    totals: { entries: 1, writes: 1 },
  });

  const conflict = call(demo, "crm.update_deal", {
    deal_id: DEAL_ID,
    fields: { notes: "Changed content under same correlation." },
  }, "same-tool-use", "same-request");
  assert.equal(conflict.status, "error");
  assert.equal(conflict.error.code, "IDEMPOTENCY_CONFLICT");
  assert.equal(conflict.adapter.entries, 0);
  assert.equal(demo.snapshot().state.deals[DEAL_ID].notes, "First correlated update.");
  assert.deepEqual(demo.snapshot().counters.crm, { entries: 1, writes: 1 });
});

test("proposal drafts stay pending, replay idempotently, and reject granted terms before adapter entry", () => {
  const demo = fixture();
  const args = {
    draft_kind: "proposal",
    deal_id: DEAL_ID,
    title: "Synthetic proposal",
    body: "Terms remain subject to review.",
    commercial_terms: { requested_discount_pct: 30, status: "pending" },
    idempotency_key: "same-draft-key",
  };
  const first = call(demo, "drafts.create", args, "draft-create-1");
  const replay = call(demo, "drafts.create", args, "draft-create-2");
  assert.equal(first.result.draft_id, replay.result.draft_id);
  assert.equal(first.result.commercial_terms.status, "pending");
  assert.equal(first.result.delivery_status, "not_sent");
  assert.equal(replay.adapter.entries, 1);
  assert.equal(replay.adapter.writes, 0);
  assert.deepEqual(demo.snapshot().counters.drafts, { entries: 2, writes: 1 });

  const granted = call(demo, "drafts.create", {
    ...args,
    commercial_terms: { requested_discount_pct: 30, status: "granted" },
    idempotency_key: "invalid-granted-draft",
  }, "draft-granted-probe");
  assert.equal(granted.status, "error");
  assert.equal(granted.error.code, "INVALID_ARGUMENTS");
  assert.equal(granted.adapter.entries, 0);
  assert.deepEqual(demo.snapshot().counters.drafts, { entries: 2, writes: 1 });

  const conflict = call(demo, "drafts.create", { ...args, body: "Changed body" }, "draft-create-conflict");
  assert.equal(conflict.status, "error");
  assert.equal(conflict.error.code, "IDEMPOTENCY_CONFLICT");
  assert.equal(conflict.adapter.writes, 0);
  assert.equal(Object.keys(demo.snapshot().state.drafts).length, 1);
});

test("generated read IDs are validated before fixture adapter entry", () => {
  const demo = fixture();
  const badTask = call(demo, "tasks.read", { task_id: "DRAFT-AAAAAAAAAAAAAAAA" }, "wrong-task-prefix");
  const badDraft = call(demo, "drafts.read", { draft_id: "DRAFT-not-a-generated-id" }, "bad-draft-id");
  assert.equal(badTask.status, "error");
  assert.equal(badDraft.status, "error");
  assert.equal(badTask.adapter.entries, 0);
  assert.equal(badDraft.adapter.entries, 0);
  assert.deepEqual(demo.snapshot().counters.tasks, { entries: 0, writes: 0 });
  assert.deepEqual(demo.snapshot().counters.drafts, { entries: 0, writes: 0 });
});

test("reset restores fixed records and counters and deterministic IDs remain stable per run/key", () => {
  const demo = fixture();
  const args = {
    task_kind: "engineering_follow_up",
    deal_id: DEAL_ID,
    title: "Resettable task",
    details: "Fictional task only.",
    idempotency_key: "stable-reset-key",
  };
  const first = call(demo, "tasks.create", args, "task-before-reset");
  call(demo, "crm.update_deal", { deal_id: DEAL_ID, fields: { notes: "temporary" } }, "change-before-reset");
  const reset = demo.reset();
  assert.equal(reset.state.deals[DEAL_ID].notes, "");
  assert.deepEqual(reset.counters, {
    meetings: { entries: 0, writes: 0 }, crm: { entries: 0, writes: 0 },
    tasks: { entries: 0, writes: 0 }, drafts: { entries: 0, writes: 0 },
  });
  const after = call(demo, "tasks.create", args, "task-after-reset");
  assert.equal(after.result.task_id, first.result.task_id);
  assert.equal(after.adapter.writes, 1);
});
