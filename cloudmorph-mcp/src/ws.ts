import type { IncomingMessage } from "http";
import { WebSocketServer, WebSocket } from "ws";
import type { RawData } from "ws";

type SubscriptionSet = {
  requestIds: Set<string>;
  jobIds: Set<string>;
};

type ClientState = {
  socket: WebSocket;
  subscriptions: SubscriptionSet;
  token: string;
  connectedAt: number;
};

export type McpEventPayload = {
  type: string;
  requestId?: string;
  jobId?: string;
  [key: string]: unknown;
};

type WsHubOptions = {
  controlCenterUrl: string;
  validateTokens: boolean;
  log?: (level: "debug" | "info" | "warn" | "error", message: string, fields?: Record<string, unknown>) => void;
};

type WaitCriteria = {
  requestId?: string;
  jobId?: string;
  requireTerminal?: boolean;
};

type Waiter = {
  requestId?: string;
  jobId?: string;
  requireTerminal: boolean;
  resolved: boolean;
  resolve: (event: McpEventPayload | null) => void;
  timeout: NodeJS.Timeout;
};

const normalizeBaseUrl = (value: string) => value.replace(/\/$/, "");

const extractToken = (req: IncomingMessage) => {
  const authHeader = req.headers.authorization || "";
  if (authHeader) {
    const [scheme, token] = authHeader.split(" ");
    if (scheme && scheme.toLowerCase() === "bearer" && token) {
      return token.trim();
    }
  }
  const url = req.url || "";
  const queryIndex = url.indexOf("?");
  if (queryIndex >= 0) {
    const query = url.slice(queryIndex + 1);
    const params = new URLSearchParams(query);
    const token = params.get("token") || "";
    if (token) return token.trim();
  }
  return "";
};

const validateToken = async (controlCenterUrl: string, token: string) => {
  if (!controlCenterUrl || !token) return false;
  try {
    const resp = await fetch(`${normalizeBaseUrl(controlCenterUrl)}/controlcenter/mcp/requests?limit=1`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    return resp.ok;
  } catch {
    return false;
  }
};

const parseMessage = (raw: RawData) => {
  try {
    return JSON.parse(raw.toString());
  } catch {
    return null;
  }
};

export const createWsHub = (options: WsHubOptions) => {
  const { controlCenterUrl, validateTokens, log } = options;
  const wss = new WebSocketServer({ noServer: true });
  const clients = new Set<ClientState>();
  const requestWaiters = new Map<string, Set<Waiter>>();
  const jobWaiters = new Map<string, Set<Waiter>>();

  const terminalStatus = new Set(["completed", "failed", "cancelled", "canceled", "blocked", "block"]);
  const isTerminalEvent = (event: McpEventPayload, requireTerminal: boolean) => {
    if (!requireTerminal) return true;
    const type = String(event.type || "");
    const status = String((event as any).status || "").toLowerCase();
    if (type === "job.status") return terminalStatus.has(status);
    if (type === "request.status") return terminalStatus.has(status) || String((event as any).decision || "") === "block";
    return false;
  };

  const attachWaiter = (map: Map<string, Set<Waiter>>, key: string, waiter: Waiter) => {
    if (!key) return;
    const bucket = map.get(key) || new Set();
    bucket.add(waiter);
    map.set(key, bucket);
  };

  const detachWaiter = (map: Map<string, Set<Waiter>>, key: string, waiter: Waiter) => {
    const bucket = map.get(key);
    if (!bucket) return;
    bucket.delete(waiter);
    if (!bucket.size) map.delete(key);
  };

  const heartbeat = setInterval(() => {
    clients.forEach((client) => {
      if ((client.socket as any).isAlive === false) {
        client.socket.terminate();
        clients.delete(client);
        return;
      }
      (client.socket as any).isAlive = false;
      client.socket.ping();
    });
  }, 30000);

  wss.on("connection", async (socket, req) => {
    const token = extractToken(req);
    if (!token) {
      socket.close(1008, "missing_token");
      return;
    }

    const client: ClientState = {
      socket,
      token,
      subscriptions: { requestIds: new Set(), jobIds: new Set() },
      connectedAt: Date.now(),
    };

    if (validateTokens) {
      const ok = await validateToken(controlCenterUrl, token);
      if (!ok) {
        socket.close(1008, "invalid_token");
        return;
      }
    }

    clients.add(client);
    (socket as any).isAlive = true;
    socket.on("pong", () => {
      (socket as any).isAlive = true;
    });

    socket.on("message", (data) => {
      const message = parseMessage(data);
      if (!message || typeof message !== "object") return;
      const type = String(message.type || "");
      if (type === "ping") {
        socket.send(JSON.stringify({ type: "pong", ts: new Date().toISOString() }));
        return;
      }
      if (type === "subscribe") {
        const requestId = typeof message.requestId === "string" ? message.requestId.trim() : "";
        const jobId = typeof message.jobId === "string" ? message.jobId.trim() : "";
        if (requestId) client.subscriptions.requestIds.add(requestId);
        if (jobId) client.subscriptions.jobIds.add(jobId);
        socket.send(
          JSON.stringify({
            type: "subscribed",
            requestId: requestId || null,
            jobId: jobId || null,
          }),
        );
        return;
      }
      if (type === "unsubscribe") {
        const requestId = typeof message.requestId === "string" ? message.requestId.trim() : "";
        const jobId = typeof message.jobId === "string" ? message.jobId.trim() : "";
        if (requestId) client.subscriptions.requestIds.delete(requestId);
        if (jobId) client.subscriptions.jobIds.delete(jobId);
        socket.send(
          JSON.stringify({
            type: "unsubscribed",
            requestId: requestId || null,
            jobId: jobId || null,
          }),
        );
      }
    });

    socket.on("close", () => {
      clients.delete(client);
    });
  });

  const handleUpgrade = (req: IncomingMessage, socket: any, head: Buffer) => {
    if (!req.url || !req.url.startsWith("/mcp/ws")) {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      wss.emit("connection", ws, req);
    });
  };

  const publish = (event: McpEventPayload) => {
    if (!event || typeof event !== "object") return;
    const payload = JSON.stringify({ type: "event", event });
    clients.forEach((client) => {
      const { requestIds, jobIds } = client.subscriptions;
      const matchesRequest = event.requestId && requestIds.has(String(event.requestId));
      const matchesJob = event.jobId && jobIds.has(String(event.jobId));
      if (!matchesRequest && !matchesJob) return;
      if (client.socket.readyState === WebSocket.OPEN) {
        client.socket.send(payload);
      }
    });

    const matched = new Set<Waiter>();
    if (event.requestId) {
      const waiters = requestWaiters.get(String(event.requestId));
      if (waiters) waiters.forEach((waiter) => matched.add(waiter));
    }
    if (event.jobId) {
      const waiters = jobWaiters.get(String(event.jobId));
      if (waiters) waiters.forEach((waiter) => matched.add(waiter));
    }
    matched.forEach((waiter) => {
      if (waiter.resolved) return;
      if (!isTerminalEvent(event, waiter.requireTerminal)) return;
      waiter.resolved = true;
      clearTimeout(waiter.timeout);
      if (waiter.requestId) detachWaiter(requestWaiters, waiter.requestId, waiter);
      if (waiter.jobId) detachWaiter(jobWaiters, waiter.jobId, waiter);
      if (log) {
        log("info", "mcp.waiter.match", {
          requestId: waiter.requestId || null,
          jobId: waiter.jobId || null,
          eventType: event.type || null,
          status: (event as any).status || null,
        });
      }
      waiter.resolve(event);
    });
  };

  const waitForEvent = (criteria: WaitCriteria, timeoutMs: number) => {
    const requestId = typeof criteria.requestId === "string" ? criteria.requestId : undefined;
    const jobId = typeof criteria.jobId === "string" ? criteria.jobId : undefined;
    const requireTerminal = criteria.requireTerminal !== false;
    if (!requestId && !jobId) {
      return Promise.resolve(null);
    }
    return new Promise<McpEventPayload | null>((resolve) => {
      const waiter: Waiter = {
        requestId,
        jobId,
        requireTerminal,
        resolved: false,
        resolve,
        timeout: setTimeout(() => {
          if (waiter.resolved) return;
          waiter.resolved = true;
          if (requestId) detachWaiter(requestWaiters, requestId, waiter);
          if (jobId) detachWaiter(jobWaiters, jobId, waiter);
          if (log) {
            log("info", "mcp.waiter.timeout", {
              requestId: requestId || null,
              jobId: jobId || null,
              timeoutMs,
              requireTerminal,
            });
          }
          resolve(null);
        }, Math.max(0, timeoutMs)),
      };
      if (requestId) attachWaiter(requestWaiters, requestId, waiter);
      if (jobId) attachWaiter(jobWaiters, jobId, waiter);
      if (log) {
        log("info", "mcp.waiter.add", {
          requestId: requestId || null,
          jobId: jobId || null,
          timeoutMs,
          requireTerminal,
        });
      }
    });
  };

  const close = () => {
    clearInterval(heartbeat);
    wss.close();
  };

  return { handleUpgrade, publish, close, waitForEvent };
};
