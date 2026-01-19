import type { Request, Response } from "express";
import { Router } from "express";
import { getBearerToken } from "./auth";

type RouterOptions = {
  controlCenterUrl: string;
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
      payload: { type: "object", additionalProperties: true }
    },
    required: ["action"]
  }
};

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

  router.post("/mcp", async (req, res) => {
    const token = requireToken(req, res);
    if (!token) {
      return;
    }

    const body = (req.body || {}) as JsonRpcRequest;
    const id = typeof body.id === "string" || typeof body.id === "number" || body.id === null ? body.id : null;
    const method = body.method || "";

    if (body.jsonrpc && body.jsonrpc !== MCP_JSONRPC_VERSION) {
      res.json(jsonRpcError(id, -32600, "invalid_request"));
      return;
    }

    if (method === "tools/list") {
      res.json(jsonRpcResult(id, { tools: [MCP_TOOL] }));
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
        res.json(jsonRpcError(id, -32601, "tool_not_found"));
        return;
      }
      if (!args || typeof args !== "object") {
        res.json(jsonRpcError(id, -32602, "invalid_tool_arguments"));
        return;
      }

      const forwardResult = await forwardToControlCenterRequest(
        {
          method: "POST",
          path: "/controlcenter/mcp/requests",
          token,
          body: args
        },
        baseUrl
      );

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

  return router;
}
