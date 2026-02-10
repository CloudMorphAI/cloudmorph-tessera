import type { Request, Response } from "express";
import { Router } from "express";
import { getBearerToken } from "./auth";
import { RateLimiter, hashToken } from "./ratelimit";
import type { McpEventPayload } from "./ws";

type RouterOptions = {
  controlCenterUrl: string;
  publishEvent?: (event: McpEventPayload) => void;
  eventSecret?: string;
  waitForEvent?: (
    criteria: { requestId?: string; jobId?: string; requireTerminal?: boolean },
    timeoutMs: number,
  ) => Promise<McpEventPayload | null>;
  log?: (level: "debug" | "info" | "warn" | "error", message: string, fields?: Record<string, unknown>) => void;
  rateLimiter?: RateLimiter;
};

type ForwardOptions = {
  method: "GET" | "POST";
  path: string;
  token: string;
  body?: unknown;
};

type ForwardResult = {
  ok: boolean;
  status: number;
  data?: unknown;
  text?: string;
  contentType?: string | null;
};

type JsonRpcId = string | number | null;

type JsonRpcRequest = {
  jsonrpc?: string;
  id?: JsonRpcId;
  method?: string;
  params?: Record<string, unknown>;
};

const MCP_JSONRPC_VERSION = "2.0";
const MCP_TOOL_NAME = "cloudmorph_request";
const MCP_REQUEST_STATUS_TOOL_NAME = "cloudmorph_request_status";
const MCP_JOB_STATUS_TOOL_NAME = "cloudmorph_job_status";

const MCP_TOOL = {
  name: MCP_TOOL_NAME,
  description:
    "Submit a CloudMorph policy request for a tool action; returns allow or block decision plus metadata.",
  inputSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      action: { type: "string", description: "Action name (example: aws.s3.delete_bucket)." },
      targets: { type: "array", items: { type: "string" } },
      payload: { type: "object", additionalProperties: true },
      waitSeconds: { type: "number", minimum: 0, maximum: 55, description: "Wait for terminal output up to N seconds." },
      wait: { type: "boolean", description: "Wait for terminal output using the default timeout." }
    },
    required: ["action"]
  }
};

const MCP_REQUEST_STATUS_TOOL = {
  name: MCP_REQUEST_STATUS_TOOL_NAME,
  description: "Fetch the latest status for a CloudMorph request by requestId.",
  inputSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      requestId: { type: "string" }
    },
    required: ["requestId"]
  }
};

const MCP_JOB_STATUS_TOOL = {
  name: MCP_JOB_STATUS_TOOL_NAME,
  description: "Fetch the latest status for a CloudMorph job by jobId.",
  inputSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      jobId: { type: "string" }
    },
    required: ["jobId"]
  }
};

const MCP_TOOLS = [MCP_TOOL, MCP_REQUEST_STATUS_TOOL, MCP_JOB_STATUS_TOOL];

const jsonRpcResult = (id: JsonRpcId, result: unknown) => ({
  jsonrpc: MCP_JSONRPC_VERSION,
  id: id ?? null,
  result
});

const jsonRpcError = (id: JsonRpcId, code: number, message: string, data?: unknown) => ({
  jsonrpc: MCP_JSONRPC_VERSION,
  id: id ?? null,
  error: {
    code,
    message,
    data
  }
});

async function forwardToControlCenterRequest(
  options: ForwardOptions,
  baseUrl: string
): Promise<ForwardResult> {
  const url = `${baseUrl}${options.path}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${options.token}`
  };

  let body: string | undefined;
  if (options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body ?? {});
  }

  try {
    const response = await fetch(url, {
      method: options.method,
      headers,
      body
    });

    const text = await response.text();
    const contentType = response.headers.get("content-type");

    if (!text) {
      return { ok: response.ok, status: response.status, contentType };
    }

    try {
      const data = JSON.parse(text);
      return { ok: response.ok, status: response.status, data, contentType };
    } catch {
      return { ok: response.ok, status: response.status, text, contentType };
    }
  } catch (error) {
    return {
      ok: false,
      status: 502,
      data: {
        error: "control_center_unavailable",
        message: error instanceof Error ? error.message : "Unknown error"
      }
    };
  }
}

async function forwardToControlCenter(
  res: Response,
  options: ForwardOptions,
  baseUrl: string
): Promise<void> {
  const result = await forwardToControlCenterRequest(options, baseUrl);
  res.status(result.status);

  if (result.data !== undefined) {
    res.json(result.data);
    return;
  }

  if (result.text) {
    if (result.contentType) {
      res.setHeader("content-type", result.contentType);
    }
    res.send(result.text);
    return;
  }

  res.end();
}

function requireToken(req: Request, res: Response): string | null {
  const token = getBearerToken(req);
  if (!token) {
    res.status(401).json({ error: "missing_integration_token" });
    return null;
  }
  return token;
}

export function buildRouter(options: RouterOptions): Router {
  const router = Router();
  const baseUrl = options.controlCenterUrl.replace(/\/$/, "");
  const eventSecret = (options.eventSecret || "").trim();
  const defaultWaitSeconds = Number(process.env.MCP_WAIT_SECONDS || "55");
  const maxWaitSeconds = Number(process.env.MCP_WAIT_MAX_SECONDS || "55");
  const log = options.log;
  const logEvent = (level: "debug" | "info" | "warn" | "error", message: string, fields?: Record<string, unknown>) => {
    if (log) {
      log(level, message, fields);
    }
  };

  const rateLimiter = options.rateLimiter;

  router.get("/mcp", (_req, res) => {
    res.json({ ok: true, service: "cloudmorph-mcp" });
  });

  router.post("/mcp", async (req, res) => {
    const token = requireToken(req, res);
    if (!token) {
      return;
    }

    // Rate limiting check
    if (rateLimiter) {
      const tokenKey = hashToken(token);
      const check = rateLimiter.checkRequest(tokenKey);
      if (!check.allowed) {
        logEvent("warn", "mcp.ratelimit.rejected", {
          reason: check.reason,
          retryAfterSeconds: check.retryAfterSeconds,
          remaining: check.remaining,
        });
        if (check.retryAfterSeconds) {
          res.setHeader("Retry-After", String(check.retryAfterSeconds));
        }
        res.status(429).json({
          error: check.reason || "rate_limit_exceeded",
          message: `Rate limit exceeded. ${check.reason === "daily_limit_exceeded" ? "Daily request quota reached." : "Too many requests per minute."}`,
          retryAfterSeconds: check.retryAfterSeconds,
          remaining: check.remaining,
        });
        return;
      }
      rateLimiter.consumeRequest(tokenKey);
    }

    const body = (req.body || {}) as JsonRpcRequest;
    const id = typeof body.id === "string" || typeof body.id === "number" || body.id === null ? body.id : null;
    const method = body.method || "";

    if (body.jsonrpc && body.jsonrpc !== MCP_JSONRPC_VERSION) {
      res.json(jsonRpcError(id, -32600, "invalid_request"));
      return;
    }

    if (method === "initialize") {
      res.json(
        jsonRpcResult(id, {
          protocolVersion: "2024-11-05",
          serverInfo: { name: "cloudmorph-mcp", version: "0.1.0" },
          capabilities: { tools: {} }
        })
      );
      return;
    }

    if (method === "notifications/initialized") {
      res.status(204).end();
      return;
    }

    if (method === "tools/list") {
      res.json(jsonRpcResult(id, { tools: MCP_TOOLS }));
      return;
    }

    if (method === "tools/call") {
      const params = (body.params || {}) as Record<string, unknown>;
      const name = typeof params.name === "string" ? params.name : "";
      const args = params.arguments;

      if (!name) {
        res.json(jsonRpcError(id, -32602, "missing_tool_name"));
        return;
      }
      if (name !== MCP_TOOL_NAME) {
        if (name !== MCP_REQUEST_STATUS_TOOL_NAME && name !== MCP_JOB_STATUS_TOOL_NAME) {
          res.json(jsonRpcError(id, -32601, "tool_not_found"));
          return;
        }
      }
      if (!args || typeof args !== "object") {
        res.json(jsonRpcError(id, -32602, "invalid_tool_arguments"));
        return;
      }

      let forwardResult: ForwardResult;
      if (name === MCP_TOOL_NAME) {
        const argsRecord = args as Record<string, unknown>;
        const action = typeof argsRecord.action === "string" ? String(argsRecord.action).trim() : "";
        const targets = Array.isArray(argsRecord.targets) ? argsRecord.targets : [];
        const payload = argsRecord.payload && typeof argsRecord.payload === "object"
          ? (argsRecord.payload as Record<string, unknown>)
          : {};
        const waitSeconds = typeof argsRecord.waitSeconds === "number" ? argsRecord.waitSeconds : undefined;
        const waitFlag = argsRecord.wait === true;
        const rawWaitSeconds =
          waitSeconds !== undefined
            ? waitSeconds
            : waitFlag
            ? defaultWaitSeconds
            : 0;
        const cappedWaitSeconds = Math.min(Math.max(0, rawWaitSeconds), maxWaitSeconds);
        const waitTimeout = Math.round(cappedWaitSeconds * 1000);
        const { waitSeconds: _ignoredWaitSeconds, wait: _ignoredWaitFlag, ...forwardArgs } = argsRecord;
        const payloadKeys = Object.keys(payload);
        logEvent("info", "mcp.request.submit", {
          action,
          targetsCount: targets.length,
          payloadKeyCount: payloadKeys.length,
          payloadKeys: payloadKeys.slice(0, 20),
          waitSeconds: rawWaitSeconds,
        });
        forwardResult = await forwardToControlCenterRequest(
          {
            method: "POST",
            path: "/controlcenter/mcp/requests",
            token,
            body: forwardArgs
          },
          baseUrl
        );
        if (
          waitTimeout > 0 &&
          options.waitForEvent &&
          forwardResult.ok &&
          forwardResult.data &&
          typeof forwardResult.data === "object"
        ) {
          const data = forwardResult.data as Record<string, unknown>;
          const requestId = typeof data.requestId === "string" ? data.requestId : "";
          const jobId = typeof data.jobId === "string" ? data.jobId : "";
          const decision = typeof data.decision === "string" ? data.decision : "";
          if ((requestId || jobId) && decision !== "block") {
            const event = await options.waitForEvent(
              { requestId, jobId, requireTerminal: true },
              waitTimeout,
            );
            if (event) {
              logEvent("info", "mcp.request.event", {
                requestId,
                jobId,
                status: (event as any).status ?? null,
                decision: (event as any).decision ?? null,
                hasOutput: Boolean((event as any).output),
              });
              forwardResult = {
                ok: true,
                status: forwardResult.status,
                data: {
                  ...data,
                  status: (event as any).status ?? data.status,
                  decision: (event as any).decision ?? data.decision,
                  reason: (event as any).reason ?? data.reason,
                  output: (event as any).output ?? data.output,
                  event,
                },
              };
            }
          }
        }
        if (
          forwardResult.ok &&
          forwardResult.data &&
          typeof forwardResult.data === "object"
        ) {
          const data = forwardResult.data as Record<string, unknown>;
          const requestId = typeof data.requestId === "string" ? data.requestId : "";
          const decision = typeof data.decision === "string" ? data.decision : "";
          const output = (data as any).output;
          if (requestId && decision !== "block" && output == null) {
            const statusResult = await forwardToControlCenterRequest(
              {
                method: "GET",
                path: `/controlcenter/mcp/requests/${requestId}`,
                token,
              },
              baseUrl
            );
            if (statusResult.ok && statusResult.data && typeof statusResult.data === "object") {
              const statusData = statusResult.data as Record<string, unknown>;
              logEvent("info", "mcp.request.status", {
                requestId,
                status: statusData.status ?? null,
                decision: statusData.decision ?? null,
                hasOutput: Boolean((statusData as any).output),
              });
              forwardResult = {
                ok: true,
                status: forwardResult.status,
                data: {
                  ...data,
                  status: (statusData as any).status ?? data.status,
                  decision: (statusData as any).decision ?? data.decision,
                  reason: (statusData as any).reason ?? data.reason,
                  output: (statusData as any).output ?? data.output,
                },
              };
            }
          }
        }
      } else if (name === MCP_REQUEST_STATUS_TOOL_NAME) {
        const requestId = typeof (args as Record<string, unknown>).requestId === "string"
          ? String((args as Record<string, unknown>).requestId).trim()
          : "";
        if (!requestId) {
          res.json(jsonRpcError(id, -32602, "missing_request_id"));
          return;
        }
        forwardResult = await forwardToControlCenterRequest(
          {
            method: "GET",
            path: `/controlcenter/mcp/requests/${requestId}`,
            token
          },
          baseUrl
        );
      } else {
        const jobId = typeof (args as Record<string, unknown>).jobId === "string"
          ? String((args as Record<string, unknown>).jobId).trim()
          : "";
        if (!jobId) {
          res.json(jsonRpcError(id, -32602, "missing_job_id"));
          return;
        }
        forwardResult = await forwardToControlCenterRequest(
          {
            method: "GET",
            path: `/controlcenter/mcp/jobs/${jobId}`,
            token
          },
          baseUrl
        );
      }

      const responsePayload =
        forwardResult.data ?? (forwardResult.text ? { raw: forwardResult.text } : {});

      res.json(
        jsonRpcResult(id, {
          content: [
            {
              type: "text",
              text: JSON.stringify(responsePayload)
            }
          ],
          isError: !forwardResult.ok
        })
      );
      return;
    }

    res.json(jsonRpcError(id, -32601, "method_not_found"));
  });

  router.post("/controlcenter/mcp/requests", async (req, res) => {
    const token = requireToken(req, res);
    if (!token) {
      return;
    }

    // Input validation
    const body = req.body;
    if (body && typeof body === "object") {
      const action = body.action;
      if (action && typeof action === "string") {
        const normalized = action.trim().toLowerCase();
        if (normalized.length > 200) {
          res.status(400).json({ error: "invalid_action", message: "Action name too long." });
          return;
        }
        // Block obviously dangerous patterns
        if (/[;&|`$]/.test(action)) {
          res.status(400).json({ error: "invalid_action", message: "Action contains invalid characters." });
          return;
        }
      }
    }

    // Rate limiting for REST endpoint
    if (rateLimiter) {
      const tokenKey = hashToken(token);
      const check = rateLimiter.checkRequest(tokenKey);
      if (!check.allowed) {
        if (check.retryAfterSeconds) {
          res.setHeader("Retry-After", String(check.retryAfterSeconds));
        }
        res.status(429).json({
          error: check.reason || "rate_limit_exceeded",
          retryAfterSeconds: check.retryAfterSeconds,
          remaining: check.remaining,
        });
        return;
      }
      rateLimiter.consumeRequest(tokenKey);
    }

    await forwardToControlCenter(
      res,
      {
        method: "POST",
        path: "/controlcenter/mcp/requests",
        token,
        body: req.body
      },
      baseUrl
    );
  });

  router.get("/controlcenter/mcp/requests/:requestId", async (req, res) => {
    const token = requireToken(req, res);
    if (!token) {
      return;
    }
    await forwardToControlCenter(
      res,
      {
        method: "GET",
        path: `/controlcenter/mcp/requests/${req.params.requestId}`,
        token
      },
      baseUrl
    );
  });

  router.get("/controlcenter/mcp/jobs/:jobId", async (req, res) => {
    const token = requireToken(req, res);
    if (!token) {
      return;
    }
    await forwardToControlCenter(
      res,
      {
        method: "GET",
        path: `/controlcenter/mcp/jobs/${req.params.jobId}`,
        token
      },
      baseUrl
    );
  });

  router.post(
    "/controlcenter/mcp/requests/:requestId/cancel",
    async (req, res) => {
      const token = requireToken(req, res);
      if (!token) {
        return;
      }
      await forwardToControlCenter(
        res,
        {
          method: "POST",
          path: `/controlcenter/mcp/requests/${req.params.requestId}/cancel`,
          token,
          body: req.body
        },
        baseUrl
      );
    }
  );

  router.post("/mcp/events", (req, res) => {
    if (!options.publishEvent) {
      res.status(501).json({ error: "events_not_configured" });
      return;
    }
    if (eventSecret) {
      const auth = req.headers.authorization || "";
      const [scheme, token] = String(auth).split(" ");
      if (!scheme || scheme.toLowerCase() !== "bearer" || token !== eventSecret) {
        res.status(403).json({ error: "invalid_event_secret" });
        return;
      }
    }
    const payload = req.body && typeof req.body === "object" ? req.body : {};
    options.publishEvent(payload as McpEventPayload);
    res.json({ ok: true });
  });

  return router;
}
