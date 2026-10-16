import { createHash } from "node:crypto";

export const FIXTURE_VERSION = "post-call-fixtures/1.0.0";
export const FIXTURE_LABEL = "SYNTHETIC LOCAL FIXTURE - NO VENDOR CONNECTION";
export const CALL_ID = "CALL-ACME-001";
export const DEAL_ID = "DEAL-ACME-001";

const identifierPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const idempotencyPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const recordIdPattern = /^[A-Z0-9][A-Z0-9-]{0,63}$/;

const toolDefinitions = [
  {
    name: "meetings.get_call",
    description: "Read the fixed fictional Acme post-call record.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["call_id"],
      properties: { call_id: { type: "string", enum: [CALL_ID] } },
    },
  },
  {
    name: "crm.get_deal",
    description: "Read the fixed fictional Acme CRM deal.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["deal_id"],
      properties: { deal_id: { type: "string", enum: [DEAL_ID] } },
    },
  },
  {
    name: "crm.update_deal",
    description: "Update only supplied notes, next_step, or discount_pct fields on the fictional deal.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["deal_id", "fields"],
      properties: {
        deal_id: { type: "string", enum: [DEAL_ID] },
        fields: {
          type: "object",
          additionalProperties: false,
          minProperties: 1,
          properties: {
            notes: { type: "string", minLength: 1, maxLength: 1000 },
            next_step: { type: "string", minLength: 1, maxLength: 320 },
            discount_pct: { type: "number", minimum: 0, maximum: 100, multipleOf: 0.01 },
          },
        },
      },
    },
  },
  {
    name: "tasks.create",
    description: "Create an engineering follow-up or a separate pending internal discount-review task.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["task_kind", "deal_id", "title", "details", "idempotency_key"],
      properties: {
        task_kind: { type: "string", enum: ["engineering_follow_up", "internal_discount_review"] },
        deal_id: { type: "string", enum: [DEAL_ID] },
        title: { type: "string", minLength: 1, maxLength: 160 },
        details: { type: "string", minLength: 1, maxLength: 1200 },
        idempotency_key: { type: "string", minLength: 1, maxLength: 128, pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]*$" },
        requested_discount_pct: { type: "number", minimum: 0, maximum: 100, multipleOf: 0.01 },
      },
      allOf: [
        {
          if: { properties: { task_kind: { const: "internal_discount_review" } }, required: ["task_kind"] },
          then: { required: ["requested_discount_pct"] },
          else: { not: { required: ["requested_discount_pct"] } },
        },
      ],
    },
  },
  {
    name: "tasks.read",
    description: "Read a task created in this fixture run.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["task_id"],
      properties: { task_id: { type: "string", pattern: "^TASK-[A-F0-9]{16}$" } },
    },
  },
  {
    name: "drafts.create",
    description: "Create a local follow-up or proposal draft; it is never sent and commercial terms remain pending.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["draft_kind", "deal_id", "title", "body", "idempotency_key"],
      properties: {
        draft_kind: { type: "string", enum: ["follow_up", "proposal"] },
        deal_id: { type: "string", enum: [DEAL_ID] },
        title: { type: "string", minLength: 1, maxLength: 160 },
        body: { type: "string", minLength: 1, maxLength: 1600 },
        idempotency_key: { type: "string", minLength: 1, maxLength: 128, pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]*$" },
        commercial_terms: {
          type: "object",
          additionalProperties: false,
          required: ["requested_discount_pct", "status"],
          properties: {
            requested_discount_pct: { type: "number", minimum: 0, maximum: 100, multipleOf: 0.01 },
            status: { type: "string", const: "pending" },
          },
        },
      },
      allOf: [
        {
          if: { properties: { draft_kind: { const: "proposal" } }, required: ["draft_kind"] },
          then: { required: ["commercial_terms"] },
        },
      ],
    },
  },
  {
    name: "drafts.read",
    description: "Read a draft created in this fixture run; there is no send action.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["draft_id"],
      properties: { draft_id: { type: "string", pattern: "^DRAFT-[A-F0-9]{16}$" } },
    },
  },
];

const toolNames = new Set(toolDefinitions.map((definition) => definition.name));
const adapterForTool = (toolName) => toolName.split(".")[0];
const adapterNames = ["meetings", "crm", "tasks", "drafts"];

class FixtureError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "FixtureError";
    this.code = code;
  }
}

function fail(code, message) {
  throw new FixtureError(code, message);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function exactObject(value, label, required, optional = []) {
  if (!isPlainObject(value)) fail("INVALID_ARGUMENTS", label + " must be an object.");
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail("INVALID_ARGUMENTS", label + " contains an unsupported field.");
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail("INVALID_ARGUMENTS", label + "." + key + " is required.");
  }
  return value;
}

function identifier(value, label) {
  if (typeof value !== "string" || !identifierPattern.test(value)) {
    fail("INVALID_ARGUMENTS", label + " must be a bounded identifier string.");
  }
  return value;
}

function recordId(value, label, prefix = null) {
  const expectedPattern = prefix ? new RegExp("^" + prefix + "-[A-F0-9]{16}$") : recordIdPattern;
  if (typeof value !== "string" || !expectedPattern.test(value)) {
    fail("INVALID_ARGUMENTS", label + " must be a bounded fixture identifier.");
  }
  return value;
}

function idempotencyKey(value) {
  if (typeof value !== "string" || !idempotencyPattern.test(value)) {
    fail("INVALID_ARGUMENTS", "idempotency_key must be a bounded identifier string.");
  }
  return value;
}

function text(value, label, maximum) {
  if (typeof value !== "string" || value.trim().length === 0 || value.length > maximum) {
    fail("INVALID_ARGUMENTS", label + " must be a non-empty string of at most " + maximum + " characters.");
  }
  return value.trim();
}

function percentage(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) {
    fail("INVALID_ARGUMENTS", label + " must be a finite number from 0 through 100.");
  }
  if (Math.round(value * 100) !== value * 100) {
    fail("INVALID_ARGUMENTS", label + " may have at most two decimal places.");
  }
  return value;
}

function validateMetadata(invocation) {
  exactObject(invocation, "invocation", ["request_id", "tool_use_id", "tool_name", "arguments"]);
  if (typeof invocation.tool_name !== "string" || !toolNames.has(invocation.tool_name)) {
    fail("UNKNOWN_TOOL", "tool_name is not in the synthetic fixture catalog.");
  }
  return {
    request_id: identifier(invocation.request_id, "request_id"),
    tool_use_id: identifier(invocation.tool_use_id, "tool_use_id"),
    tool_name: invocation.tool_name,
  };
}

function validateArguments(toolName, value) {
  switch (toolName) {
    case "meetings.get_call": {
      exactObject(value, "arguments", ["call_id"]);
      return { call_id: recordId(value.call_id, "call_id") };
    }
    case "crm.get_deal": {
      exactObject(value, "arguments", ["deal_id"]);
      return { deal_id: recordId(value.deal_id, "deal_id") };
    }
    case "crm.update_deal": {
      exactObject(value, "arguments", ["deal_id", "fields"]);
      const fields = exactObject(value.fields, "fields", [], ["notes", "next_step", "discount_pct"]);
      const keys = Object.keys(fields);
      if (keys.length === 0) fail("INVALID_ARGUMENTS", "fields must contain at least one supported update.");
      const normalized = {};
      if (Object.hasOwn(fields, "notes")) normalized.notes = text(fields.notes, "fields.notes", 1000);
      if (Object.hasOwn(fields, "next_step")) normalized.next_step = text(fields.next_step, "fields.next_step", 320);
      if (Object.hasOwn(fields, "discount_pct")) normalized.discount_pct = percentage(fields.discount_pct, "fields.discount_pct");
      return { deal_id: recordId(value.deal_id, "deal_id"), fields: normalized };
    }
    case "tasks.create": {
      exactObject(value, "arguments", ["task_kind", "deal_id", "title", "details", "idempotency_key"], ["requested_discount_pct"]);
      if (!["engineering_follow_up", "internal_discount_review"].includes(value.task_kind)) {
        fail("INVALID_ARGUMENTS", "task_kind must be engineering_follow_up or internal_discount_review.");
      }
      const output = {
        task_kind: value.task_kind,
        deal_id: recordId(value.deal_id, "deal_id"),
        title: text(value.title, "title", 160),
        details: text(value.details, "details", 1200),
        idempotency_key: idempotencyKey(value.idempotency_key),
      };
      if (value.task_kind === "internal_discount_review") {
        if (!Object.hasOwn(value, "requested_discount_pct")) {
          fail("INVALID_ARGUMENTS", "requested_discount_pct is required for an internal discount-review task.");
        }
        output.requested_discount_pct = percentage(value.requested_discount_pct, "requested_discount_pct");
      } else if (Object.hasOwn(value, "requested_discount_pct")) {
        fail("INVALID_ARGUMENTS", "requested_discount_pct is only valid on an internal discount-review task.");
      }
      return output;
    }
    case "tasks.read": {
      exactObject(value, "arguments", ["task_id"]);
      return { task_id: recordId(value.task_id, "task_id", "TASK") };
    }
    case "drafts.create": {
      exactObject(value, "arguments", ["draft_kind", "deal_id", "title", "body", "idempotency_key"], ["commercial_terms"]);
      if (!["follow_up", "proposal"].includes(value.draft_kind)) {
        fail("INVALID_ARGUMENTS", "draft_kind must be follow_up or proposal.");
      }
      const output = {
        draft_kind: value.draft_kind,
        deal_id: recordId(value.deal_id, "deal_id"),
        title: text(value.title, "title", 160),
        body: text(value.body, "body", 1600),
        idempotency_key: idempotencyKey(value.idempotency_key),
      };
      if (value.draft_kind === "proposal" && !Object.hasOwn(value, "commercial_terms")) {
        fail("INVALID_ARGUMENTS", "commercial_terms are required for a proposal draft.");
      }
      if (Object.hasOwn(value, "commercial_terms")) {
        const terms = exactObject(value.commercial_terms, "commercial_terms", ["requested_discount_pct", "status"]);
        if (terms.status !== "pending") fail("INVALID_ARGUMENTS", "Commercial terms must remain pending.");
        output.commercial_terms = {
          requested_discount_pct: percentage(terms.requested_discount_pct, "commercial_terms.requested_discount_pct"),
          status: "pending",
        };
      }
      return output;
    }
    case "drafts.read": {
      exactObject(value, "arguments", ["draft_id"]);
      return { draft_id: recordId(value.draft_id, "draft_id", "DRAFT") };
    }
    default:
      fail("UNKNOWN_TOOL", "tool_name is not in the synthetic fixture catalog.");
  }
}
function deterministicId(prefix, runId, key) {
  const payload = runId + "\n" + prefix + "\n" + key;
  const digest = createHash("sha256").update(payload).digest("hex").slice(0, 16).toUpperCase();
  return prefix + "-" + digest;
}

function fingerprint(value) {
  return JSON.stringify(value);
}

function newSeed() {
  return {
    calls: {
      [CALL_ID]: {
        call_id: CALL_ID,
        label: FIXTURE_LABEL,
        account_name: "Acme Field Systems (fictional)",
        summary: "Fictional post-call: the team requested an engineering evaluation and asked about a 30% discount. No real customer or meeting data.",
      },
    },
    deals: {
      [DEAL_ID]: {
        deal_id: DEAL_ID,
        label: FIXTURE_LABEL,
        account_name: "Acme Field Systems (fictional)",
        stage: "evaluation",
        notes: "",
        next_step: "",
        discount_pct: 10,
      },
    },
    tasks: {},
    drafts: {},
  };
}

function newCounters() {
  return Object.fromEntries(adapterNames.map((name) => [name, { entries: 0, writes: 0 }]));
}

function safeCorrelation(value) {
  return typeof value === "string" && identifierPattern.test(value) ? value : null;
}

function cleanError(error) {
  if (error instanceof FixtureError) return { code: error.code, message: error.message };
  return { code: "FIXTURE_ERROR", message: "The synthetic fixture operation failed." };
}

export function getPostCallToolCatalog() {
  return structuredClone(toolDefinitions);
}

/**
 * Create one isolated, resettable synthetic fixture run.
 * dispatch() is a policy-agnostic adapter surface: a caller must evaluate each
 * proposed call through its policy gateway before invoking it.
 */
export function createPostCallFixture({ run_id = "post-call-demo-001" } = {}) {
  const runId = identifier(run_id, "run_id");
  let state;
  let counters;
  let taskKeys;
  let draftKeys;
  let invocationKeys;

  function reset() {
    state = newSeed();
    counters = newCounters();
    taskKeys = new Map();
    draftKeys = new Map();
    invocationKeys = new Map();
    return snapshot();
  }

  function snapshot() {
    return structuredClone({ fixture_version: FIXTURE_VERSION, run_id: runId, state, counters });
  }

  function requireRecord(collection, id, kind) {
    const record = collection[id];
    if (!record) fail("FIXTURE_RECORD_NOT_FOUND", "No synthetic " + kind + " exists for that identifier in this run.");
    return record;
  }

  function createIdempotent({ collection, keyStore, prefix, key, normalized, makeRecord, adapterName }) {
    const requestedFingerprint = fingerprint(normalized);
    const prior = keyStore.get(key);
    if (prior) {
      if (prior.fingerprint !== requestedFingerprint) {
        fail("IDEMPOTENCY_CONFLICT", "That idempotency_key was already used with different content.");
      }
      return structuredClone(collection[prior.id]);
    }
    const id = deterministicId(prefix, runId, key);
    const record = makeRecord(id);
    collection[id] = record;
    keyStore.set(key, { id, fingerprint: requestedFingerprint });
    counters[adapterName].writes += 1;
    return structuredClone(record);
  }

  const adapters = {
    meetings: {
      get_call(args) {
        return structuredClone(requireRecord(state.calls, args.call_id, "call"));
      },
    },
    crm: {
      get_deal(args) {
        return structuredClone(requireRecord(state.deals, args.deal_id, "deal"));
      },
      update_deal(args) {
        const deal = requireRecord(state.deals, args.deal_id, "deal");
        const changedFields = Object.keys(args.fields).filter((key) => deal[key] !== args.fields[key]);
        if (changedFields.length > 0) {
          for (const key of changedFields) deal[key] = args.fields[key];
          counters.crm.writes += 1;
        }
        return { deal: structuredClone(deal), changed_fields: changedFields };
      },
    },
    tasks: {
      create(args) {
        requireRecord(state.deals, args.deal_id, "deal");
        return createIdempotent({
          collection: state.tasks,
          keyStore: taskKeys,
          prefix: "TASK",
          key: args.idempotency_key,
          normalized: args,
          adapterName: "tasks",
          makeRecord: (taskId) => ({
            task_id: taskId,
            deal_id: args.deal_id,
            task_kind: args.task_kind,
            title: args.title,
            details: args.details,
            status: args.task_kind === "internal_discount_review" ? "awaiting_internal_review" : "open",
            ...(args.task_kind === "internal_discount_review"
              ? { requested_discount_pct: args.requested_discount_pct, approval_status: "pending" }
              : {}),
          }),
        });
      },
      read(args) {
        return structuredClone(requireRecord(state.tasks, args.task_id, "task"));
      },
    },
    drafts: {
      create(args) {
        requireRecord(state.deals, args.deal_id, "deal");
        return createIdempotent({
          collection: state.drafts,
          keyStore: draftKeys,
          prefix: "DRAFT",
          key: args.idempotency_key,
          normalized: args,
          adapterName: "drafts",
          makeRecord: (draftId) => ({
            draft_id: draftId,
            deal_id: args.deal_id,
            draft_kind: args.draft_kind,
            title: args.title,
            body: args.body,
            status: "draft",
            delivery_status: "not_sent",
            commercial_terms: args.commercial_terms ? structuredClone(args.commercial_terms) : null,
          }),
        });
      },
      read(args) {
        return structuredClone(requireRecord(state.drafts, args.draft_id, "draft"));
      },
    },
  };

  function dispatch(invocation) {
    const rawTool = isPlainObject(invocation) && typeof invocation.tool_name === "string" ? invocation.tool_name : "";
    const namespace = adapterNames.includes(adapterForTool(rawTool)) ? adapterForTool(rawTool) : null;
    let metadata;
    let args;
    try {
      metadata = validateMetadata(invocation);
      args = validateArguments(metadata.tool_name, invocation.arguments);
    } catch (error) {
      return {
        request_id: safeCorrelation(invocation?.request_id),
        tool_use_id: safeCorrelation(invocation?.tool_use_id),
        tool_name: rawTool || null,
        status: "error",
        error: cleanError(error),
        adapter: { name: namespace, call_id: null, entries: 0, writes: 0, totals: namespace ? structuredClone(counters[namespace]) : null },
      };
    }

    const [adapterName, operation] = metadata.tool_name.split(".");
    const invocationKey = JSON.stringify([metadata.request_id, metadata.tool_use_id]);
    const callId = deterministicId("ADAPTER", runId, invocationKey);
    const invocationFingerprint = fingerprint({ tool_name: metadata.tool_name, arguments: args });
    const priorInvocation = invocationKeys.get(invocationKey);
    if (priorInvocation) {
      if (priorInvocation.fingerprint !== invocationFingerprint) {
        return {
          ...metadata,
          status: "error",
          error: { code: "IDEMPOTENCY_CONFLICT", message: "That request_id/tool_use_id pair was already used with different content." },
          adapter: { name: adapterName, call_id: callId, entries: 0, writes: 0, totals: structuredClone(counters[adapterName]) },
        };
      }
      return {
        ...structuredClone(priorInvocation.response),
        replayed: true,
        adapter: { ...priorInvocation.response.adapter, entries: 0, writes: 0, totals: structuredClone(counters[adapterName]) },
      };
    }

    const before = structuredClone(counters[adapterName]);
    let result;
    let errorPayload;
    try {
      counters[adapterName].entries += 1;
      result = adapters[adapterName][operation](args);
    } catch (error) {
      errorPayload = cleanError(error);
    }
    const after = counters[adapterName];
    const adapter = {
      name: adapterName,
      call_id: callId,
      entries: after.entries - before.entries,
      writes: after.writes - before.writes,
      totals: structuredClone(after),
    };
    const response = errorPayload
      ? { ...metadata, status: "error", error: errorPayload, adapter }
      : { ...metadata, status: "ok", result, adapter };
    invocationKeys.set(invocationKey, { fingerprint: invocationFingerprint, response: structuredClone(response) });
    return response;
  }

  reset();
  return Object.freeze({
    run_id: runId,
    fixture_version: FIXTURE_VERSION,
    fixture_label: FIXTURE_LABEL,
    get_tool_catalog: getPostCallToolCatalog,
    dispatch,
    snapshot,
    reset,
  });
}
