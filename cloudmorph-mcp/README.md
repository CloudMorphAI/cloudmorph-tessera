# cloudmorph-mcp

**MCP server for governing AI agent cloud actions.**

`cloudmorph-mcp` implements the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) and acts as a policy gateway between AI coding assistants (Cursor, Claude Code, GitHub Copilot, Windsurf) and your cloud environment. Every cloud action an AI agent wants to take is submitted as a request; the Control Center evaluates it against your policies before allowing execution.

---

## Features

| Feature | Details |
|---------|---------|
| **MCP JSON-RPC** | Full MCP protocol over HTTP (`POST /mcp`), compatible with all major AI tools |
| **3 MCP tools** | `cloudmorph_request`, `cloudmorph_request_status`, `cloudmorph_job_status` |
| **Bearer auth** | Integration token extracted from `Authorization: Bearer <token>` |
| **Rate limiting** | Per-token token-bucket: daily cap, burst-per-minute, concurrent-job limit |
| **WebSocket hub** | Real-time status push at `ws://<host>/mcp/ws` — subscribe by requestId or jobId |
| **Health check** | `GET /health` → `{ "status": "ok" }` |
| **Structured logs** | JSON lines, configurable level via `MCP_LOG_LEVEL` |
| **Docker ready** | Multi-stage Alpine image, non-root, ~60 MB |

---

## Architecture

```
AI Tool (Cursor / Claude Code / Copilot / Windsurf)
  │
  │  JSON-RPC 2.0 over HTTP POST /mcp
  ▼
┌─────────────────────────────────────────────┐
│              cloudmorph-mcp                 │
│                                             │
│  index.ts  ──►  routes.ts  ──►  auth.ts    │
│      │               │                     │
│      │          ratelimit.ts               │
│      │               │                     │
│      └──────►  ws.ts (WebSocket hub)        │
│                       │                    │
│               health.ts (/health)           │
└─────────────────────────────────────────────┘
  │
  │  Bearer token forwarded upstream
  ▼
Control Center API  (CONTROL_CENTER_API_URL)
```

Request flow:
1. AI tool calls `cloudmorph_request` with an action (e.g. `aws.s3.delete_bucket`)
2. MCP server validates the bearer token and checks rate limits
3. Request is forwarded to the Control Center API for policy evaluation
4. Decision (`allow` / `block`) is returned to the AI tool
5. Optional: wait for job terminal status via WebSocket

---

## Installation

### Docker (recommended)

```bash
docker pull ghcr.io/cloudmorphai/cloudmorph-mcp:latest

docker run -d \
  -p 8080:8080 \
  -e CONTROL_CENTER_API_URL=https://api.yourcontrolcenter.example.com \
  -e MCP_EVENT_SECRET=your-webhook-secret \
  ghcr.io/cloudmorphai/cloudmorph-mcp:latest
```

### npm

```bash
npm install @cloudmorph/mcp-server
npm run build
CONTROL_CENTER_API_URL=https://api.yourcontrolcenter.example.com npm start
```

### From source

```bash
git clone https://github.com/CloudMorphAI/cloudmorph-mcp.git
cd cloudmorph-mcp
npm install
cp .env.example .env        # fill in your values
npm run build
npm start
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in your values.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONTROL_CENTER_API_URL` | **Yes** | — | Base URL of the CloudMorph Control Center API |
| `PORT` | No | `8080` | HTTP port to listen on |
| `MCP_LOG_LEVEL` | No | `info` | Log level: `debug` \| `info` \| `warn` \| `error` \| `silent` |
| `MCP_ALLOWED_ORIGINS` | No | `""` | Comma-separated CORS origins. Use `*` to allow all |
| `MCP_RATE_LIMIT_DAILY` | No | `100` | Daily request cap per token (resets midnight UTC) |
| `MCP_RATE_LIMIT_BURST` | No | `30` | Burst cap per token per minute |
| `MCP_RATE_LIMIT_CONCURRENT` | No | `1` | Max concurrent in-flight jobs per token |
| `MCP_WS_VALIDATE_TOKENS` | No | `true` | Validate WS tokens against Control Center. Set `false` for local dev |
| `MCP_EVENT_SECRET` | No | `""` | Shared secret for the `POST /mcp/events` webhook |
| `MCP_WAIT_SECONDS` | No | `55` | Default seconds to wait for terminal event when `wait=true` |
| `MCP_WAIT_MAX_SECONDS` | No | `55` | Hard cap on `waitSeconds` accepted from callers |

---

## MCP Tools

### `cloudmorph_request`

Submit a cloud action for policy evaluation.

```json
{
  "action": "aws.s3.delete_bucket",
  "targets": ["my-bucket"],
  "payload": { "region": "us-east-1" },
  "wait": true
}
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | **Yes** | Action name (e.g. `aws.ec2.stop_instance`) |
| `targets` | string[] | No | Resource identifiers |
| `payload` | object | No | Action-specific parameters |
| `wait` | boolean | No | Wait for terminal status (uses `MCP_WAIT_SECONDS`) |
| `waitSeconds` | number (0–55) | No | Wait up to N seconds for terminal status |

Response includes `requestId`, `decision` (`allow`/`block`), `reason`, and (when terminal) `output`.

### `cloudmorph_request_status`

Poll the latest status for a request.

```json
{ "requestId": "req_abc123" }
```

### `cloudmorph_job_status`

Poll the latest status for a job.

```json
{ "jobId": "job_xyz789" }
```

---

## Usage with AI Tools

### Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "cloudmorph": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_INTEGRATION_TOKEN"
      }
    }
  }
}
```

### Claude Code

Add to `~/.claude/mcp.json` (or project `.mcp.json`):

```json
{
  "mcpServers": {
    "cloudmorph": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_INTEGRATION_TOKEN"
      }
    }
  }
}
```

### GitHub Copilot (VS Code)

Add to VS Code `settings.json`:

```json
{
  "github.copilot.chat.mcp.servers": {
    "cloudmorph": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_INTEGRATION_TOKEN"
      }
    }
  }
}
```

### Windsurf

Add to Windsurf MCP config:

```json
{
  "mcpServers": {
    "cloudmorph": {
      "serverUrl": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_INTEGRATION_TOKEN"
      }
    }
  }
}
```

---

## WebSocket Events

Connect to `ws://<host>/mcp/ws` with `Authorization: Bearer <token>` (or `?token=<token>` query param).

**Subscribe to a request:**
```json
{ "type": "subscribe", "requestId": "req_abc123" }
```

**Subscribe to a job:**
```json
{ "type": "subscribe", "jobId": "job_xyz789" }
```

**Inbound event (server → client):**
```json
{ "type": "event", "event": { "type": "request.status", "requestId": "req_abc123", "status": "completed", "output": "..." } }
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Health check |
| `GET` | `/mcp` | None | Service info |
| `POST` | `/mcp` | Bearer | JSON-RPC 2.0 MCP endpoint |
| `WS` | `/mcp/ws` | Bearer | WebSocket event hub |
| `POST` | `/mcp/events` | Event secret | Inbound webhook from Control Center |
| `POST` | `/controlcenter/mcp/requests` | Bearer | Direct request submission |
| `GET` | `/controlcenter/mcp/requests/:id` | Bearer | Get request status |
| `GET` | `/controlcenter/mcp/jobs/:id` | Bearer | Get job status |
| `POST` | `/controlcenter/mcp/requests/:id/cancel` | Bearer | Cancel a request |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
