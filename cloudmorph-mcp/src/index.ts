import express from "express";
import http from "http";
import { buildRouter } from "./routes";
import { healthRouter } from "./health";
import { createWsHub } from "./ws";
import { RateLimiter } from "./ratelimit";

const controlCenterUrl = process.env.CONTROL_CENTER_API_URL;
if (!controlCenterUrl) {
  console.error("CONTROL_CENTER_API_URL is required");
  process.exit(1);
}

const LOG_LEVELS: Record<string, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
  silent: 100,
};
const resolvedLogLevel = (process.env.MCP_LOG_LEVEL || "info").toLowerCase();
const logThreshold = LOG_LEVELS[resolvedLogLevel] ?? LOG_LEVELS.info;
const log = (level: keyof typeof LOG_LEVELS, message: string, fields?: Record<string, unknown>) => {
  if (LOG_LEVELS[level] < logThreshold) return;
  const payload = {
    ts: new Date().toISOString(),
    level,
    message,
    ...fields,
  };
  console.log(JSON.stringify(payload));
};

const parseOrigins = (value: string | undefined) =>
  String(value || "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
const allowedOrigins = parseOrigins(
  process.env.MCP_ALLOWED_ORIGINS || process.env.CONTROL_CENTER_ALLOWED_ORIGINS || "",
);
const allowAllOrigins = allowedOrigins.includes("*");
const resolveOrigin = (origin: string) => {
  if (allowAllOrigins) return "*";
  if (origin && allowedOrigins.includes(origin)) return origin;
  return allowedOrigins[0] || "";
};

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use((req, res, next) => {
  const originHeader = typeof req.headers.origin === "string" ? req.headers.origin : "";
  const allowOrigin = resolveOrigin(originHeader);
  if (allowOrigin) {
    res.setHeader("Access-Control-Allow-Origin", allowOrigin);
    res.setHeader("Vary", "Origin");
  }
  res.setHeader("Access-Control-Allow-Headers", "authorization,content-type,x-correlation-id");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  if (allowOrigin && allowOrigin !== "*") {
    res.setHeader("Access-Control-Allow-Credentials", "true");
  }
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  next();
});
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    log("info", "mcp.request", {
      method: req.method,
      path: req.originalUrl,
      status: res.statusCode,
      durationMs: Date.now() - start,
    });
  });
  next();
});
const rateLimiter = new RateLimiter({
  requestsPerDay: Number(process.env.MCP_RATE_LIMIT_DAILY || 100),
  burstPerMinute: Number(process.env.MCP_RATE_LIMIT_BURST || 30),
  concurrentJobs: Number(process.env.MCP_RATE_LIMIT_CONCURRENT || 1),
});

app.use(healthRouter());
const wsHub = createWsHub({
  controlCenterUrl,
  validateTokens: process.env.MCP_WS_VALIDATE_TOKENS !== "false",
  log,
});
app.use(
  buildRouter({
    controlCenterUrl,
    publishEvent: wsHub.publish,
    eventSecret: process.env.MCP_EVENT_SECRET || "",
    waitForEvent: wsHub.waitForEvent,
    log,
    rateLimiter,
  }),
);

const port = Number(process.env.PORT || 8080);
const server = http.createServer(app);
server.on("upgrade", (req, socket, head) => {
  wsHub.handleUpgrade(req, socket, head);
});
server.listen(port, () => {
  console.log(`cloudmorph-mcp listening on ${port}`);
});
