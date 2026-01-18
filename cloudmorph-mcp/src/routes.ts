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

async function forwardToControlCenter(
  res: Response,
  options: ForwardOptions,
  baseUrl: string
): Promise<void> {
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
    res.status(response.status);

    if (!text) {
      res.end();
      return;
    }

    try {
      const data = JSON.parse(text);
      res.json(data);
    } catch {
      const contentType = response.headers.get("content-type");
      if (contentType) {
        res.setHeader("content-type", contentType);
      }
      res.send(text);
    }
  } catch (error) {
    res.status(502).json({
      error: "control_center_unavailable",
      message: error instanceof Error ? error.message : "Unknown error"
    });
  }
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
