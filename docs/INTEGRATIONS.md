# Tessera Integrations

This document explains how to wire Tessera into the most common MCP-capable AI development tools. After completing the steps for your tool, every MCP call the tool makes will pass through Tessera's policy engine before reaching the upstream server.

**Prerequisites:** Tessera is running locally and reachable at `http://localhost:8080`. You have a bearer token — either from `TESSERA_BEARER_TOKEN` or from your `tokens.yaml`. If you used `tessera init`, the generated `.env.example` shows the token you set.

---

## Table of contents

- [How the proxy URL is formed](#how-the-proxy-url-is-formed)
- [Bearer token requirement](#bearer-token-requirement)
- [Intent-aware vs intent-blind agents](#intent-aware-vs-intent-blind-agents)
- [Cursor](#cursor)
- [Claude Code](#claude-code)
- [Claude Desktop](#claude-desktop)
- [Windsurf](#windsurf)
- [Per-server config templates](#per-server-config-templates)
  - [AWS MCP](#aws-mcp)
  - [GitHub MCP](#github-mcp)
  - [Slack MCP](#slack-mcp)
  - [Linear MCP](#linear-mcp)
- [Verifying the connection](#verifying-the-connection)
- [Troubleshooting](#troubleshooting)

---

## How the proxy URL is formed

Tessera exposes one HTTP endpoint per upstream server you have configured in `tessera.yaml`:

```
POST http://localhost:8080/mcp/<upstream-name>
```

Where `<upstream-name>` matches the `name` field under `upstreams[]` in your `tessera.yaml`. For example:

```yaml
upstreams:
  - name: aws
    url: https://mcp.aws.example.com
    ...
  - name: github
    url: https://mcp.github.example.com
    ...
```

This creates two proxy endpoints:

- `http://localhost:8080/mcp/aws` — proxied to your AWS MCP server
- `http://localhost:8080/mcp/github` — proxied to your GitHub MCP server

In your MCP client config, you point the tool at the **Tessera proxy URL**, not the upstream server URL directly. Tessera handles forwarding, policy evaluation, and audit logging transparently.

---

## Bearer token requirement

Every request to Tessera must include an `Authorization` header:

```
Authorization: Bearer <your-tessera-token>
```

This token is the one you configured via `TESSERA_BEARER_TOKEN`, `TESSERA_BEARER_TOKENS`, or `TESSERA_BEARER_TOKENS_FILE`. It is **not** the token for your upstream MCP server — Tessera manages upstream credentials separately via `tessera.yaml`'s `credentials` block.

If you start Tessera without any token configured (dev mode), requests still pass through, but Tessera logs a warning every 60 seconds. Dev mode is only appropriate for local experimentation — never for shared or production deployments.

---

## Intent-aware vs intent-blind agents

Tessera policies can optionally use **intent** — a structured declaration from the agent describing what it intends to do and why. Intent is passed as a special field inside the MCP request's `_meta` object.

**Intent-aware agents** populate `_meta.tessera_intent` (or whatever key you set via `intent.meta_key` in `tessera.yaml`) with an object like:

```json
{
  "_meta": {
    "tessera_intent": {
      "verbs": ["read.list"],
      "purpose": "Listing S3 buckets to build a cost-attribution report."
    }
  }
}
```

This lets policies reason about *why* a tool is being called, not just *what* tool was called. For example, a policy can block a `write.delete` call that claims a `read.list` intent, because the verb and the declared purpose are inconsistent.

**Intent-blind agents** are off-the-shelf MCP clients (Cursor, Claude Desktop, Windsurf in their current versions) that do not populate `_meta.tessera_intent`. This is the normal case for most users today. Tessera handles this gracefully:

- Policies that have `match.require_intent: true` are **skipped entirely** for intent-blind requests. The call is evaluated only against policies that do not require intent.
- Policies without `require_intent` evaluate normally based on tool name and arguments alone.
- The result is that intent-blind agents can still be protected by tool-name-based and argument-based policies — they just cannot be evaluated against intent-specific policies.

You do **not** need to do anything special to use Tessera with intent-blind clients. Wire the client in as shown below, and policies that do not require intent will apply immediately.

If you want to require that every call include intent (for example, to ensure all agents in your environment are intent-aware), set `intent.required: true` in `tessera.yaml`. With that setting, calls without intent are blocked unconditionally. This is an advanced configuration intended for organizations that have instrumented all their agents.

---

## Cursor

Cursor reads MCP server configuration from `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` at the project root (project-scoped). The file format uses the `mcpServers` key.

To route an MCP server through Tessera, replace the direct upstream URL with the Tessera proxy URL and add the `headers` block with your bearer token.

**`~/.cursor/mcp.json`:**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    },
    "github-via-tessera": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

Replace `tk_your_token_here` with your actual Tessera bearer token. Replace `aws` and `github` with the upstream names defined in your `tessera.yaml`.

**Note on the server name key** (e.g. `"aws-via-tessera"`): This is only a display label inside Cursor. You can name it anything. Using a `-via-tessera` suffix makes it clear in the Cursor UI that traffic is going through the firewall.

After saving the file, restart Cursor or use the MCP panel to reload servers.

---

## Claude Code

Claude Code (the CLI) reads MCP server configuration from `~/.claude.json` (global) or `.mcp.json` at the project root (project-scoped). The format uses the same `mcpServers` structure as Cursor.

**Global configuration (`~/.claude.json`):**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    },
    "github-via-tessera": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Project-scoped configuration (`.mcp.json` at project root):**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

Project-scoped config takes precedence over global config for servers with the same key. Use project-scoped config when different projects need different upstream servers or different Tessera instances.

After adding the config, Claude Code picks it up on the next session start. No restart of Claude Code itself is required if using `.mcp.json` — the CLI reads it fresh each session.

---

## Claude Desktop

Claude Desktop reads MCP server configuration from `claude_desktop_config.json`. The location of this file is platform-dependent:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

**`claude_desktop_config.json`:**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    },
    "github-via-tessera": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    },
    "slack-via-tessera": {
      "url": "http://localhost:8080/mcp/slack",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

After saving, **restart Claude Desktop** for the changes to take effect. Claude Desktop does not hot-reload the config file.

Claude Desktop is an intent-blind client. It does not populate `_meta.tessera_intent`. See [Intent-aware vs intent-blind agents](#intent-aware-vs-intent-blind-agents) above for what this means in practice.

---

## Windsurf

Windsurf reads MCP server configuration from a `mcp_config.json` file. The location is:

- **macOS / Linux:** `~/.codeium/windsurf/mcp_config.json`
- **Windows:** `%USERPROFILE%\.codeium\windsurf\mcp_config.json`

**`mcp_config.json`:**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "serverUrl": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    },
    "github-via-tessera": {
      "serverUrl": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

Note that Windsurf uses the key `serverUrl` rather than `url`. The rest of the structure mirrors the other clients.

After saving, open the Windsurf MCP panel (or restart Windsurf) to reload the server list.

Like Cursor and Claude Desktop, Windsurf is an intent-blind client in the current release.

---

## Per-server config templates

The following templates are ready-to-paste starting points for the four most commonly used MCP servers. In each case:

1. Add the corresponding `upstreams[]` entry to your `tessera.yaml`.
2. Add the corresponding `mcpServers` entry to your MCP client config.
3. Set the referenced environment variables before starting Tessera.

### AWS MCP

**`tessera.yaml` upstream block:**

```yaml
upstreams:
  - name: aws
    url: https://mcp.aws.example.com   # Replace with your AWS MCP server URL
    timeout_seconds: 30
    credentials:
      header: Authorization
      value: "Bearer ${AWS_MCP_TOKEN}"
```

Set `AWS_MCP_TOKEN` in the environment where Tessera runs (or in the `.env` file you pass to Docker).

**Cursor / Claude Code / `.mcp.json`:**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Claude Desktop (`claude_desktop_config.json`):**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Windsurf (`mcp_config.json`):**

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "serverUrl": "http://localhost:8080/mcp/aws",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Reference policies that apply to AWS traffic:** `cost-cap.yaml`, `data-residency-eu.yaml`, `prod-protection.yaml`, `pii-block.yaml`. See `docs/POLICIES.md` for details.

---

### GitHub MCP

**`tessera.yaml` upstream block:**

```yaml
upstreams:
  - name: github
    url: https://mcp.github.example.com   # Replace with your GitHub MCP server URL
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${GITHUB_MCP_TOKEN}"
```

Set `GITHUB_MCP_TOKEN` to a GitHub personal access token (classic or fine-grained) with the scopes your agent needs.

**Cursor / Claude Code / `.mcp.json`:**

```json
{
  "mcpServers": {
    "github-via-tessera": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Claude Desktop (`claude_desktop_config.json`):**

```json
{
  "mcpServers": {
    "github-via-tessera": {
      "url": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Windsurf (`mcp_config.json`):**

```json
{
  "mcpServers": {
    "github-via-tessera": {
      "serverUrl": "http://localhost:8080/mcp/github",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Reference policies that apply to GitHub traffic:** `write-action-approval.yaml` (blocks destructive write actions), `read-only-mode.yaml` (locks all traffic to read-only), `prod-protection.yaml`. See `docs/POLICIES.md`.

---

### Slack MCP

**`tessera.yaml` upstream block:**

```yaml
upstreams:
  - name: slack
    url: https://mcp.slack.example.com   # Replace with your Slack MCP server URL
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${SLACK_MCP_TOKEN}"
```

Set `SLACK_MCP_TOKEN` to your Slack bot token (`xoxb-...`).

**Cursor / Claude Code / `.mcp.json`:**

```json
{
  "mcpServers": {
    "slack-via-tessera": {
      "url": "http://localhost:8080/mcp/slack",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Claude Desktop (`claude_desktop_config.json`):**

```json
{
  "mcpServers": {
    "slack-via-tessera": {
      "url": "http://localhost:8080/mcp/slack",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Windsurf (`mcp_config.json`):**

```json
{
  "mcpServers": {
    "slack-via-tessera": {
      "serverUrl": "http://localhost:8080/mcp/slack",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Policies to consider for Slack:** `pii-block.yaml` (prevents accidental PII broadcast), `secret-leak-block.yaml` (blocks messages containing credential patterns). See `docs/POLICIES.md`.

---

### Linear MCP

**`tessera.yaml` upstream block:**

```yaml
upstreams:
  - name: linear
    url: https://mcp.linear.example.com   # Replace with your Linear MCP server URL
    timeout_seconds: 10
    credentials:
      header: Authorization
      value: "Bearer ${LINEAR_MCP_TOKEN}"
```

Set `LINEAR_MCP_TOKEN` to your Linear API key.

**Cursor / Claude Code / `.mcp.json`:**

```json
{
  "mcpServers": {
    "linear-via-tessera": {
      "url": "http://localhost:8080/mcp/linear",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Claude Desktop (`claude_desktop_config.json`):**

```json
{
  "mcpServers": {
    "linear-via-tessera": {
      "url": "http://localhost:8080/mcp/linear",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Windsurf (`mcp_config.json`):**

```json
{
  "mcpServers": {
    "linear-via-tessera": {
      "serverUrl": "http://localhost:8080/mcp/linear",
      "headers": {
        "Authorization": "Bearer tk_your_token_here"
      }
    }
  }
}
```

**Policies to consider for Linear:** `write-action-approval.yaml` (requires approval before issue deletion or status-changing bulk writes).

---

## Verifying the connection

After wiring up a client, confirm traffic is flowing through Tessera:

**1. Check the health endpoint:**

```bash
curl -s http://localhost:8080/healthz | python -m json.tool
```

Expected output:

```json
{
  "status": "ok",
  "policy_state": {
    "loaded": 7,
    "errored": []
  }
}
```

A non-zero `errored` list means one or more policy files failed to load. Check `errored[].error` for the reason.

**2. Make a tool call from the client:**

Use the MCP client to invoke any tool — for example, ask an AI assistant to list your S3 buckets or open a GitHub issue. The call goes to the client, which sends the MCP request to `http://localhost:8080/mcp/<upstream>`. Tessera evaluates it, forwards it, and logs it.

**3. Inspect the audit log:**

```bash
tessera audit verify --scope default
```

If the call was logged, you will see `events_checked: 1` (or more) and `ok: true`. If running in Docker:

```bash
docker exec tessera tessera audit verify --scope default
```

**4. Check response headers in log_only mode:**

If Tessera is in `log_only` mode (the default after `tessera init`), the HTTP response from Tessera includes:

- `X-Tessera-Mode: log_only`
- `X-Tessera-Decision: would_allow` or `would_block`

Most MCP clients do not expose these headers directly in the UI, but you can capture them by proxying through a tool like `mitmproxy` or by checking Tessera's stdout logs.

---

## Troubleshooting

**The client cannot connect to `http://localhost:8080`.**

Tessera is not running, or it is bound to a different port. Check with:

```bash
curl -s http://localhost:8080/healthz
```

If this fails, verify Tessera is running (`docker ps` or `tessera serve`) and that the port in `tessera.yaml` matches.

**The client receives HTTP 401 Unauthorized.**

The bearer token in the client config does not match any token Tessera knows about. Check:

- The token in the `headers.Authorization` field is exactly `Bearer <token>` (one space, correct capitalisation of "Bearer").
- The token value matches what is in `TESSERA_BEARER_TOKEN`, `TESSERA_BEARER_TOKENS`, or `TESSERA_BEARER_TOKENS_FILE`.
- Tessera was restarted after the token was set.

**Calls pass through but policies are not applying.**

Check the mode. If `policies.mode` is `observation` in `tessera.yaml`, the policy engine is skipped entirely. Switch to `log_only` to evaluate policies without blocking traffic, or `enforcement` to enforce them.

Also confirm the upstream name in the client URL matches a `name` entry under `upstreams[]` in `tessera.yaml`. A misspelled upstream name returns HTTP 404.

**Policies are in `log_only` mode but I expected a block.**

In `log_only` mode, Tessera always forwards traffic. The `would_block` tag appears in the response header and audit log, but the call is not stopped. This is by design — `log_only` is for observing before committing to enforcement. See `docs/CONFIGURATION.md` for the transition path to `enforcement`.

**Calls that should trigger intent-based policies are not being evaluated.**

Standard MCP clients (Cursor, Claude Desktop, Windsurf) do not send intent. Policies with `match.require_intent: true` are silently skipped for these clients. This is expected behavior. If you need intent-based enforcement, you must use a custom agent that populates `_meta.tessera_intent`. See [Intent-aware vs intent-blind agents](#intent-aware-vs-intent-blind-agents) above.

For more detailed troubleshooting, see `docs/TROUBLESHOOTING.md`.
