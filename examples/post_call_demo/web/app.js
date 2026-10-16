(() => {
  "use strict";

  const API = "/api";
  const state = { health: null, policy: null, result: null, busy: false, serviceReady: false };
  const $ = (id) => document.getElementById(id);
  const STEP_SPECS = [
    { title: "Review call & deal context", tool: "meetings.get_call · crm.get_deal", match: (events) => events.filter((event) => toolName(event) === "meetings.get_call" || (toolName(event) === "crm.get_deal" && events.filter((entry) => toolName(entry) === "crm.get_deal").indexOf(event) === 0)) },
    { title: "Save call notes & next step", tool: "crm.update_deal", match: (events) => events.filter((event) => toolName(event) === "crm.update_deal" && (Object.prototype.hasOwnProperty.call(fields(event), "notes") || Object.prototype.hasOwnProperty.call(fields(event), "next_step"))) },
    { title: "Create engineering follow-up task", tool: "tasks.create", match: (events) => events.filter((event) => toolName(event) === "tasks.create" && (args(event).task_kind || args(event).kind) === "engineering_follow_up") },
    { title: "Prepare unsent follow-up draft", tool: "drafts.create", match: (events) => events.filter((event) => toolName(event) === "drafts.create" && (args(event).draft_kind || args(event).kind) === "follow_up") },
    { title: "Request 30% discount", tool: "crm.update_deal", match: (events) => events.filter((event) => toolName(event) === "crm.update_deal" && Number(fields(event).discount_pct) === 30) },
    { title: "Create separate discount-review task", tool: "tasks.create", match: (events) => events.filter((event) => toolName(event) === "tasks.create" && (args(event).task_kind || args(event).kind) === "internal_discount_review") },
    { title: "Read final deal & related work", tool: "crm.get_deal", match: (events) => events.filter((event) => toolName(event) === "crm.get_deal").slice(-1) },
  ];

  function asObject(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function text(value, fallback = "—") {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
    return JSON.stringify(value);
  }
  function safeJson(value) {
    try { return JSON.stringify(value, null, 2); } catch { return "Response data unavailable"; }
  }
  function args(event) {
    return asObject(event?.arguments || event?.tool_arguments || event?.args || event?.input);
  }
  function fields(event) { return asObject(args(event).fields); }
  function toolName(event) { return text(event?.tool || event?.tool_name || event?.name, "unknown tool"); }
  function decision(event) {
    const value = text(event?.decision || event?.outcome || event?.status, "observed").toLowerCase();
    if (value.includes("block") || value.includes("deny")) return "block";
    if (value.includes("allow")) return "allow";
    if (value.includes("error") || value.includes("fail")) return "error";
    return "observed";
  }
  function eventsFrom(result) {
    if (Array.isArray(result?.events)) return result.events;
    if (Array.isArray(result?.audit?.events)) return result.audit.events;
    if (Array.isArray(result?.audit)) return result.audit;
    return [];
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  async function apiRequest(path, init = {}) {
    const response = await fetch(`${API}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init.headers || {}) },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload?.detail || payload?.error || `Local service returned HTTP ${response.status}`;
      throw new Error(text(detail, `Local service returned HTTP ${response.status}`));
    }
    if (!payload || typeof payload !== "object") throw new Error("Local service returned an unreadable response.");
    return payload;
  }

  function idempotencyKey() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
    else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
    return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function novaConfigured(health) {
    const modes = Array.isArray(health?.modes) ? health.modes : [];
    return modes.includes("nova") || health?.nova_configured === true || health?.live_model_available === true;
  }

  function renderService() {
    const status = $("service-status");
    const scripted = $("run-scripted");
    const nova = $("run-nova");
    const description = $("mode-description");
    if (!state.serviceReady) {
      status.dataset.state = "error";
      status.textContent = "Local service unavailable";
      scripted.disabled = true;
      nova.disabled = true;
      description.textContent = "Start the local fixture service to run this scenario.";
      return;
    }
    status.dataset.state = "ready";
    status.textContent = "Local service ready";
    scripted.disabled = state.busy;
    nova.disabled = state.busy || !novaConfigured(state.health);
    description.textContent = nova.disabled && !state.busy
      ? "Scripted mode runs the deterministic fixture with no model request. Nova is enabled only when the backend reports it configured."
      : "Choose a run mode. Scripted mode is deterministic; Nova mode is an explicit local backend request.";
    if (!novaConfigured(state.health)) nova.title = "Nova is unavailable until the local backend reports it configured.";
    else nova.removeAttribute("title");
  }

  function renderPolicy(policy) {
    const root = $("policy-content");
    clear(root);
    if (!policy) {
      root.appendChild(element("p", "muted", "The local policy endpoint did not return policy data."));
      return;
    }
    const body = asObject(policy.policy || policy);
    const id = body.policy_id || body.id || policy.policy_id || policy.id;
    const cap = body.discount_cap_pct ?? body.max_discount_pct ?? body.discount_cap ?? body.cap_pct ?? body.cap;
    const rules = Array.isArray(body.rules) ? body.rules : Array.isArray(body.policies) ? body.policies : [];
    if (id || cap !== undefined || rules.length) {
      if (id) root.appendChild(element("div", "policy-title", text(id)));
      if (cap !== undefined) addPolicyRule(root, "Discount cap", `${text(cap)}%`);
      rules.forEach((rule, index) => {
        const item = asObject(rule);
        addPolicyRule(root, text(item.id || item.policy_id || item.name, `Rule ${index + 1}`), text(item.action || item.decision));
      });
      if (body.description) root.appendChild(element("p", "muted", text(body.description)));
    } else {
      root.appendChild(element("p", "muted", "Policy returned by the local service"));
      root.appendChild(element("pre", "policy-raw", safeJson(policy)));
    }

    const toolCatalog = Array.isArray(policy.tools) ? policy.tools : Array.isArray(body.tools) ? body.tools : [];
    const catalogRoot = $("policy-tools");
    clear(catalogRoot);
    catalogRoot.appendChild(element("span", "subtle-label", "AGENT TOOL CATALOG"));
    if (toolCatalog.length) {
      toolCatalog.forEach((tool) => catalogRoot.appendChild(element("span", "tool-chip", typeof tool === "string" ? tool : text(tool.name || tool.tool))));
    } else {
      catalogRoot.appendChild(element("p", "muted", "No tool catalog was included in the policy response."));
    }
  }

  function addPolicyRule(parent, label, value) {
    const row = element("div", "policy-rule");
    row.appendChild(element("span", "", label));
    row.appendChild(element("strong", "", value));
    parent.appendChild(row);
  }

  function renderJourney(result) {
    const list = $("journey-list");
    clear(list);
    const events = result ? eventsFrom(result) : [];
    STEP_SPECS.forEach((spec) => {
      const matchedEvents = result ? spec.match(events) : [];
      const matched = matchedEvents.length > 0;
      const decisions = matchedEvents.map(decision);
      const stateName = !matched ? "pending" : (decisions.includes("block") ? "blocked" : decisions.includes("error") ? "error" : decisions.every((value) => value === "allow") ? "allowed" : "observed");
      const item = element("li", "journey-step");
      item.dataset.state = stateName;
      const marker = element("span", "step-marker");
      marker.setAttribute("aria-hidden", "true");
      const content = element("div", "step-content");
      const title = element("div", "step-title");
      title.appendChild(element("span", "", spec.title));
      title.appendChild(element("span", "step-state", matched ? `${stateName}${matchedEvents.length > 1 ? ` ×${matchedEvents.length}` : ""}` : "not verified"));
      content.appendChild(title);
      content.appendChild(element("span", "step-tool", matched ? [...new Set(matchedEvents.map(toolName))].join(" · ") : spec.tool));
      item.append(marker, content);
      list.appendChild(item);
    });
  }

  function eventValue(event, keys, fallback = "—") {
    for (const key of keys) if (event?.[key] !== undefined && event[key] !== null) return text(event[key], fallback);
    return fallback;
  }

  function renderEvents(result) {
    const events = eventsFrom(result);
    const root = $("event-list");
    clear(root);
    $("event-count").textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
    if (!events.length) {
      root.appendChild(element("p", "empty-state", "The backend response contained no tool events. No workflow completion is asserted."));
      return;
    }
    events.forEach((event, index) => {
      const verdict = decision(event);
      const card = element("article", "event-card");
      card.dataset.decision = verdict;
      const head = element("div", "event-head");
      head.appendChild(element("code", "event-tool", toolName(event)));
      head.appendChild(element("span", "decision-chip", verdict));
      const body = element("div", "event-body");
      const reason = eventValue(event, ["reason", "decision_reason", "policy_reason"], "No reason supplied by the backend.");
      body.appendChild(element("p", "event-reason", reason));
      const meta = element("div", "event-meta");
      const policyId = eventValue(event, ["policy_id", "matched_policy_id"]);
      const auditId = eventValue(event, ["audit_id", "receipt_id", "event_id", "head_hash"]);
      const contacts = eventValue(event, ["adapter_contacts", "upstream_calls", "adapter_call_count"]);
      meta.append(element("span", "", `Policy ${policyId}`), element("span", "", `Audit ${auditId}`), element("span", "", `Adapter contacts ${contacts}`));
      body.appendChild(meta);
      const argumentsValue = args(event);
      if (Object.keys(argumentsValue).length) body.appendChild(element("pre", "event-args", safeJson(argumentsValue)));
      const details = element("details", "event-details");
      details.appendChild(element("summary", "", `Raw event ${index + 1}`));
      details.appendChild(element("pre", "event-args", safeJson(event)));
      body.appendChild(details);
      card.append(head, body);
      root.appendChild(card);
    });
  }

  function listItems(value) {
    if (Array.isArray(value)) return value;
    if (value === undefined || value === null) return [];
    return [value];
  }

  function renderItemList(rootId, items, emptyMessage, labelKeys) {
    const root = $(rootId);
    clear(root);
    const values = listItems(items);
    if (!values.length) {
      root.appendChild(element("li", "muted", emptyMessage));
      return;
    }
    values.forEach((item) => {
      const value = asObject(item);
      const label = typeof item === "string" ? item : labelKeys.map((key) => value[key]).find(Boolean);
      const row = element("li", "", text(label, "Backend item"));
      const metaParts = [value.task_id || value.draft_id || value.id, value.task_kind || value.draft_kind || value.kind, value.status || value.state, value.approval_status || value.review_state];
      if (value.requested_discount_pct !== undefined) metaParts.push(`requested discount ${text(value.requested_discount_pct)}%`);
      const meta = metaParts.filter(Boolean).join(" · ");
      if (meta) row.appendChild(element("span", "item-meta", meta));
      const detail = typeof item === "object" && item !== null
        ? item.body || item.content || item.draft_text || item.proposal || item.description || item.summary || item.text
        : null;
      if (detail && detail !== label) row.appendChild(element("span", "item-detail", text(detail)));
      root.appendChild(row);
    });
  }

  function readbackFrom(result) {
    return result?.readback || result?.final_readback || result?.final_state || {};
  }

  function renderReadback(result) {
    const readback = asObject(readbackFrom(result));
    const deal = asObject(readback.deal || readback);
    const hasDeal = Object.keys(deal).length > 0;
    $("readback-state").textContent = hasDeal ? "Returned by backend" : "No deal readback returned";
    if (hasDeal) {
      const discount = deal.discount_pct ?? deal.discount;
      $("deal-discount").textContent = discount === undefined ? "Not returned" : `${text(discount)}%`;
      $("deal-next-step").textContent = text(deal.next_step, "Not returned");
      $("deal-notes").textContent = text(deal.notes, "Not returned");
    } else {
      $("deal-discount").textContent = "Not returned";
      $("deal-next-step").textContent = "Not returned";
      $("deal-notes").textContent = "Not returned";
    }
    renderItemList("task-list", readback.tasks || readback.related_tasks, "No tasks returned", ["summary", "title", "description"]);
    renderItemList("draft-list", readback.drafts || readback.followups, "No drafts returned", ["subject", "title", "text", "body", "content"]);
  }

  function renderAudit(result) {
    const audit = asObject(result?.audit);
    const receipts = listItems(audit.receipts || audit.events || result?.receipts || result?.audit_events);
    $("audit-count").textContent = text(audit.count ?? audit.receipt_count ?? result?.summary?.audit_events ?? receipts.length, "—");
    const chain = $("audit-chain");
    if (audit.chain_verified === true) {
      chain.textContent = "Audit chain verified by backend";
      chain.dataset.state = "verified";
    } else if (audit.chain_verified === false) {
      chain.textContent = "Audit chain verification failed";
      chain.dataset.state = "failed";
    } else {
      chain.textContent = "Audit chain status not returned";
      chain.dataset.state = "unknown";
    }
    const summary = asObject(result?.summary);
    $("adapter-count").textContent = text(summary.adapter_contacts ?? summary.upstream_calls ?? result?.adapter_contacts, "—");
    $("blocked-adapter-count").textContent = text(summary.blocked_adapter_contacts, "—");
    const root = $("audit-list");
    clear(root);
    if (!receipts.length) {
      root.appendChild(element("p", "muted", "No audit receipts returned."));
    } else {
      receipts.slice(0, 8).forEach((receipt, index) => {
        const item = asObject(receipt);
        const row = element("div", "audit-receipt");
        row.appendChild(element("span", "", text(item.tool || item.name, `Receipt ${index + 1}`)));
        row.appendChild(element("code", "", text(item.audit_id || item.receipt_id || item.event_id || item.head_hash || item.id, "id unavailable")));
        root.appendChild(row);
      });
    }
  }

  function renderUsage(result) {
    const root = $("usage-detail");
    const responseMode = text(result?.mode, "").toLowerCase();
    const usage = asObject(result?.usage);
    if (responseMode === "scripted") {
      root.textContent = "Scripted response; no model request reported.";
      return;
    }
    if (!responseMode.includes("nova") && !responseMode.includes("live_model")) {
      root.textContent = "No model usage details returned.";
      return;
    }
    const model = result?.model ? `Model ${text(result.model)}` : "Model not identified by response";
    const tokensIn = usage.input_tokens ?? usage.inputTokens;
    const tokensOut = usage.output_tokens ?? usage.outputTokens;
    const parts = [model];
    if (tokensIn !== undefined || tokensOut !== undefined) parts.push(`${text(tokensIn, "?")} in / ${text(tokensOut, "?")} out tokens`);
    if (usage.estimated_cost_usd !== undefined) parts.push(`$${text(usage.estimated_cost_usd)} estimated`);
    if (result?.provider) parts.push(`Provider ${text(result.provider)}`);
    root.textContent = parts.join(" · ");
  }

  function renderResult(result) {
    state.result = result;
    renderJourney(result);
    renderEvents(result);
    renderReadback(result);
    renderAudit(result);
    renderUsage(result);
    const responseMode = text(result?.mode, "mode not reported");
    const replayed = result?.replayed === true || result?.idempotency?.replayed === true;
    $("run-label").textContent = replayed ? "REPLAYED RESPONSE" : (result?.replayed === false ? "FRESH RUN" : "RUN RESPONSE");
    const summary = asObject(result?.summary);
    const parts = [`Backend mode: ${responseMode}`];
    if (summary.allowed !== undefined) parts.push(`${text(summary.allowed)} allowed`);
    if (summary.blocked !== undefined) parts.push(`${text(summary.blocked)} blocked`);
    if (result?.run_id) parts.push(`Run ${text(result.run_id)}`);
    $("run-status").textContent = parts.join(" · ");
  }

  async function runScenario(mode) {
    if (state.busy || !state.serviceReady) return;
    state.busy = true;
    renderService();
    $("run-error").hidden = true;
    $("run-status").textContent = mode === "nova" ? "Requesting the configured Nova scenario from the local backend…" : "Running the deterministic scripted fixture…";
    $("run-label").textContent = "RUNNING";
    state.result = null;
    renderJourney(null);
    $("event-count").textContent = "0 events";
    $("event-list").replaceChildren(element("p", "empty-state", "Waiting for the backend response."));
    $("audit-count").textContent = "—";
    $("adapter-count").textContent = "—";
    $("blocked-adapter-count").textContent = "—";
    $("audit-list").replaceChildren(element("p", "muted", "Waiting for run receipts."));
    $("audit-chain").textContent = "Waiting for audit result.";
    $("readback-state").textContent = "Waiting for backend readback";
    try {
      const key = idempotencyKey();
      const result = await apiRequest("/run", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify({ mode, idempotency_key: key }),
      });
      renderResult(result);
    } catch (error) {
      $("run-label").textContent = "RUN FAILED";
      $("run-status").textContent = "The local backend did not confirm a completed run.";
      $("run-error").textContent = `${error.message || "Local run failed."} No fallback result was shown.`;
      $("run-error").hidden = false;
      renderJourney(null);
      $("event-list").replaceChildren(element("p", "empty-state", "No completed run response is available."));
      $("readback-state").textContent = "No readback confirmed";
      $("audit-count").textContent = "—";
      $("adapter-count").textContent = "—";
      $("blocked-adapter-count").textContent = "—";
      $("audit-list").replaceChildren(element("p", "muted", "No receipts confirmed."));
      $("audit-chain").textContent = "No audit result confirmed.";
    } finally {
      state.busy = false;
      renderService();
    }
  }

  async function loadInitialState() {
    try {
      state.health = await apiRequest("/health");
      state.serviceReady = true;
      renderService();
    } catch (error) {
      state.serviceReady = false;
      renderService();
      $("run-error").textContent = `Could not reach the local demo service: ${error.message || "request failed"}`;
      $("run-error").hidden = false;
    }
    try {
      state.policy = await apiRequest("/policy");
      renderPolicy(state.policy);
    } catch (error) {
      renderPolicy(null);
      const note = element("p", "policy-load-error", `Policy could not be loaded: ${error.message || "request failed"}`);
      $("policy-content").appendChild(note);
    }
  }

  $("run-scripted").addEventListener("click", () => runScenario("scripted"));
  $("run-nova").addEventListener("click", () => runScenario("nova"));
  renderJourney(null);
  loadInitialState();
})();
