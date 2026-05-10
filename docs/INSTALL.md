# Installing Tessera v0.1

Tessera is distributed as a Docker image and as a Python package on PyPI. Docker is the recommended path for production use. The pip install is intended for local development, CI, and embedding Tessera programmatically.

---

## Table of contents

1. [Docker install (recommended)](#1-docker-install-recommended)
2. [pip install (secondary)](#2-pip-install-secondary)
3. [docker-compose walkthrough](#3-docker-compose-walkthrough)
4. [Volume mount cheatsheet](#4-volume-mount-cheatsheet)
5. [`tessera init` 60-second walkthrough](#5-tessera-init-60-second-walkthrough)
6. [Production hardening checklist](#6-production-hardening-checklist)

---

## 1. Docker install (recommended)

### Pull the image

```bash
docker pull ghcr.io/cloudmorph-ai/tessera:0.1.0
```

### Full `docker run` command

```bash
docker run -d \
  --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" \
  ghcr.io/cloudmorph-ai/tessera:0.1.0
```

#### Flag-by-flag explanation

| Flag | Purpose |
|---|---|
| `-d` | Run in detached (background) mode. |
| `--name tessera` | Give the container a stable name for `docker logs`, `docker stop`, etc. |
| `-p 8080:8080` | Expose Tessera on host port 8080. Change the left side to use a different host port. |
| `-v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro"` | Mount your config file read-only into the container. The `:ro` flag prevents the container from writing back to your host. |
| `-v "$PWD/policies:/etc/tessera/policies:ro"` | Mount your policy directory read-only. Tessera watches this directory for changes and reloads policies automatically (default `reload: watch`). |
| `-v tessera_audit:/var/lib/tessera` | Mount a named Docker volume for the SQLite audit database. Named volumes survive container restarts and upgrades. |
| `-e TESSERA_BEARER_TOKEN="tk_..."` | Set the bearer token used to authenticate incoming MCP requests. The `openssl rand -hex 16` subshell generates a 32-character random token at startup. Store this value — you will need it in your MCP client configuration. |

#### Verify the container is healthy

```bash
curl -s http://localhost:8080/healthz | python3 -m json.tool
```

Expected output shape:

```json
{
  "status": "ok",
  "policy_state": {
    "loaded": 7,
    "errored": []
  }
}
```

If `errored` is non-empty, read the error strings to identify which policy file failed validation.

#### Check logs

```bash
docker logs tessera
docker logs -f tessera   # follow
```

---

## 2. pip install (secondary)

The PyPI distribution name is `cloudmorph-tessera`. The import name inside Python is `tessera`.

```bash
pip install cloudmorph-tessera
```

After install, the `tessera` CLI is available on your PATH:

```bash
tessera --help
tessera version
```

### Python version requirement

Tessera requires Python 3.12 or later. Check your version:

```bash
python3 --version
```

If your system Python is older, use `pyenv`, `mise`, or a virtual environment with the correct version.

### Virtual environment (recommended for pip installs)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows PowerShell

pip install cloudmorph-tessera
tessera --help
```

### Development install (from source)

```bash
git clone https://github.com/cloudmorph-ai/tessera.git
cd tessera
pip install -e ".[dev]"
pre-commit install
```

The `[dev]` extras add pytest, ruff, mypy, hypothesis, and pre-commit hooks.

---

## 3. docker-compose walkthrough

The repository ships a `docker-compose.example.yaml` that starts Tessera alongside a minimal mock MCP server. This is the fastest way to evaluate Tessera end-to-end without needing a real upstream MCP server.

### Step 1 — Clone the repo (or copy the compose file)

```bash
git clone https://github.com/cloudmorph-ai/tessera.git
cd tessera
```

### Step 2 — Start the stack

```bash
docker compose -f docker-compose.example.yaml up
```

This starts two services:

- **tessera** — the firewall on port 8080, using `tessera.example.yaml` as its config and `./policies` as the policy directory. The audit database is stored in the `tessera_audit` named volume.
- **mock-upstream** — a tiny Python HTTP server on port 8081 that accepts any `tools/call` JSON-RPC request and returns `{"content": [{"type": "text", "text": "mock response ok"}]}`. It exists only for local evaluation; replace it with your real MCP server in production.

### Step 3 — Send a test request

In a second terminal:

```bash
curl -s -X POST http://localhost:8080/mcp/mock \
  -H "Authorization: Bearer tk_demo_REPLACE_ME_minimum_16_chars" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "list_buckets",
      "arguments": {}
    },
    "id": 1
  }'
```

Because the default mode in `tessera.example.yaml` is `log_only`, Tessera forwards the request to the mock upstream and returns the upstream response. Response headers include:

```
X-Tessera-Mode: log_only
X-Tessera-Decision: would_allow
```

### Step 4 — Inspect the audit log

In a third terminal (while the stack is still running):

```bash
docker compose -f docker-compose.example.yaml exec tessera \
  tessera audit verify --db /var/lib/tessera/audit.db
```

This prints the audit chain status for every scope that has written events. A clean chain shows `status: ok`.

### Step 5 — Tear down

```bash
docker compose -f docker-compose.example.yaml down
```

The `tessera_audit` named volume persists. To also remove it:

```bash
docker compose -f docker-compose.example.yaml down -v
```

### Adapting the compose file for a real upstream

Edit `tessera.example.yaml` (or a copy) and replace the `mock` upstream entry:

```yaml
upstreams:
  - name: aws
    url: https://your-real-mcp-server.example.com
    timeout_seconds: 30
    credentials:
      header: Authorization
      value: "Bearer ${AWS_MCP_TOKEN}"
```

Then set `AWS_MCP_TOKEN` in the `environment:` block of your `docker-compose.yaml`:

```yaml
services:
  tessera:
    environment:
      TESSERA_BEARER_TOKEN: "tk_your_token_here"
      AWS_MCP_TOKEN: "your_upstream_token_here"
```

---

## 4. Volume mount cheatsheet

Tessera reads three host-side paths at runtime. All three can be supplied as bind mounts (host directory or file) or Docker named volumes.

| Container path | What it holds | Recommended mount type | Notes |
|---|---|---|---|
| `/etc/tessera/tessera.yaml` | Main configuration file | Bind mount (single file, read-only) | Required. Tessera exits at startup if absent. |
| `/etc/tessera/policies` | Policy YAML directory | Bind mount (directory, read-only) | Required. An empty directory is valid — no policies load, default action applies. |
| `/var/lib/tessera` | SQLite audit database directory | Named volume or bind mount (read-write) | Required for audit persistence. Named volumes are simpler; bind mounts give direct host access for backup scripts. |

### Bind mount vs named volume for audit data

**Named volume (default in the provided `docker run` command):**

```bash
-v tessera_audit:/var/lib/tessera
```

Docker manages the volume. Data persists across container restarts and image upgrades. To back it up:

```bash
docker run --rm \
  -v tessera_audit:/data \
  -v "$PWD/backup:/backup" \
  alpine tar czf /backup/audit-$(date +%Y%m%d).tar.gz -C /data .
```

**Bind mount (direct host path):**

```bash
-v "/srv/tessera/audit:/var/lib/tessera"
```

The host directory must exist and be writable by UID 1000 (the non-root user Tessera runs as inside the container):

```bash
mkdir -p /srv/tessera/audit
chown 1000:1000 /srv/tessera/audit
```

Use a bind mount when you want your existing backup agent (rsync, Restic, etc.) to pick up the database file directly from the host filesystem.

### Tokens file mount

If you use `TESSERA_BEARER_TOKENS_FILE` instead of a single token, mount the tokens file similarly:

```bash
-v "$PWD/tokens.yaml:/etc/tessera/tokens.yaml:ro" \
-e TESSERA_BEARER_TOKENS_FILE="/etc/tessera/tokens.yaml"
```

See `tokens.example.yaml` in the repository root for the expected YAML format.

### Config path override

By default Tessera reads `/etc/tessera/tessera.yaml`. Override with:

```bash
-e TESSERA_CONFIG_PATH="/etc/tessera/custom-name.yaml"
```

---

## 5. `tessera init` 60-second walkthrough

`tessera init` scaffolds a starter `tessera.yaml` and a `policies/` directory so you have a working config without copying files by hand. This is the fastest path from zero to a running firewall.

### Step 1 — Run init in the current directory

```bash
# Via pip install
tessera init

# Via Docker (writes output files to the current directory)
docker run --rm \
  -v "$PWD:/out" \
  ghcr.io/cloudmorph-ai/tessera:0.1.0 \
  tessera init --dir /out
```

After this command completes, the current directory contains:

```
tessera.yaml          # main config, mode: log_only
policies/             # empty policy directory
.env.example          # annotated environment variable reference
```

### Step 2 — Open `tessera.yaml` and set your upstream

The scaffolded file has a placeholder upstream entry:

```yaml
upstreams:
  - name: my-mcp-server
    url: https://your-mcp-server.example.com
    timeout_seconds: 30
```

Replace `url` with the URL of your real MCP server. Add a `credentials:` block if the upstream requires authentication:

```yaml
upstreams:
  - name: aws
    url: https://mcp.aws.example.com
    timeout_seconds: 30
    credentials:
      header: Authorization
      value: "Bearer ${AWS_MCP_TOKEN}"
```

The `${AWS_MCP_TOKEN}` syntax is resolved from the container environment at startup. Set it via `-e` or in your `.env` file.

### Step 3 — Set your bearer token

Copy `.env.example` to `.env` (or export directly):

```bash
export TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)"
```

Write the generated value down. You will paste it into your MCP client configuration.

### Step 4 — Start Tessera

```bash
docker run -d \
  --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKEN="$TESSERA_BEARER_TOKEN" \
  ghcr.io/cloudmorph-ai/tessera:0.1.0
```

### Step 5 — Configure your MCP client

In Cursor, add to `~/.cursor/mcp.json`:

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

Replace `aws` with the `name` value from your upstream config. Replace `tk_your_token_here` with the token you generated in Step 3.

See `docs/INTEGRATIONS.md` for client snippets for Claude Code, Claude Desktop, and Windsurf.

### Step 6 — Send a tool call and check the audit log

Send any tool call through your MCP client, then verify it was logged:

```bash
tessera audit verify --db /var/lib/tessera/audit.db
# or inside the container:
docker exec tessera tessera audit verify
```

A clean output shows `status: ok` and the number of events recorded. The default `log_only` mode means every call is forwarded and logged — nothing is blocked yet.

### Step 7 (optional) — Switch to enforcement mode

Once you have reviewed the audit log and are satisfied with what your policies would have blocked, change `tessera.yaml`:

```yaml
policies:
  mode: enforcement
```

Restart the container:

```bash
docker restart tessera
```

From this point, policy decisions are enforced: calls matching a `block` rule return a JSON-RPC error and are not forwarded to the upstream.

---

## 6. Production hardening checklist

Work through this list before exposing Tessera outside localhost.

### Token management

- [ ] Generate a unique bearer token with at least 16 bytes of entropy: `openssl rand -hex 16` produces 32 hex characters, which is sufficient.
- [ ] Never reuse a token across deployments or environments (dev, staging, production should each have distinct tokens).
- [ ] For multi-client deployments, use `TESSERA_BEARER_TOKENS` or `TESSERA_BEARER_TOKENS_FILE` to issue a named token per client rather than sharing one token. This lets you audit which client made each call and revoke individual clients without cycling all tokens.
- [ ] Rotate tokens on a schedule (quarterly at minimum, or immediately after a suspected exposure). To rotate without downtime, add the new token to your token list before removing the old one, then deploy, then remove the old token and redeploy.
- [ ] Store tokens in a secrets manager (AWS Secrets Manager, HashiCorp Vault, Doppler) rather than in plain `.env` files committed to version control. Inject them at container startup via your orchestration platform.
- [ ] If using `TESSERA_BEARER_TOKENS_FILE`, mount the file read-only (`:ro`) and set file permissions to `600` on the host.

### Audit database

- [ ] Mount the audit database on a volume that is included in your regular backup schedule. The SQLite file at `/var/lib/tessera/audit.db` is the complete, tamper-evident audit record.
- [ ] Test your backup and restore procedure before going live. Confirm the restored database passes `tessera audit verify`.
- [ ] Keep the database on local SSD-backed storage if possible. The SQLite WAL mode used by Tessera is not suitable for network filesystems (NFS, EFS without provisioned IOPS).
- [ ] For long-running deployments, monitor database file size. Tessera does not auto-rotate or truncate the audit log. Set up a cron job to archive old events if storage is constrained — retain at least the most recent head row per scope so the chain can be extended after the archive.
- [ ] If you eventually migrate to a different deployment (new host, new volume), copy the `audit.db` file to the new location and verify the chain before decommissioning the old instance.

### Reverse proxy and rate limiting

Tessera v0.1 does not include built-in rate limiting. Place Tessera behind a reverse proxy before exposing it to the network.

**nginx example** (add to your `nginx.conf` or a site config file):

```nginx
upstream tessera {
    server 127.0.0.1:8080;
}

server {
    listen 443 ssl;
    server_name tessera.example.com;

    # TLS — use certbot or your cert provider
    ssl_certificate     /etc/letsencrypt/live/tessera.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tessera.example.com/privkey.pem;

    # Rate limit: 60 requests per minute per client IP
    limit_req_zone $binary_remote_addr zone=tessera_limit:10m rate=60r/m;

    location /mcp/ {
        limit_req zone=tessera_limit burst=20 nodelay;
        proxy_pass http://tessera;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }

    location /healthz {
        proxy_pass http://tessera;
    }
}
```

**Caddy example** (`Caddyfile`):

```
tessera.example.com {
    rate_limit {
        zone dynamic {
            key {remote_host}
            events 60
            window 1m
        }
    }

    reverse_proxy /mcp/* localhost:8080
    reverse_proxy /healthz localhost:8080
}
```

**Cloudflare** — if your deployment is behind Cloudflare, enable rate limiting rules in the Cloudflare dashboard targeting the `/mcp/*` path. Set the threshold appropriate to your expected agent request volume.

### Network exposure

- [ ] Never bind Tessera's port directly to `0.0.0.0` on a host that has a public IP. Bind to `127.0.0.1` (or the internal Docker bridge) and let the reverse proxy handle TLS termination and public exposure.
- [ ] Restrict inbound traffic to Tessera's port (8080 by default) at the firewall or security group level. Only the reverse proxy should be able to reach it.
- [ ] If running in Docker with bridge networking, use `--network` to place Tessera and any other internal services on an isolated network, and expose only the reverse proxy to the host.

### Mode selection

- [ ] Deploy in `log_only` mode first. Monitor the audit log for at least one full day of representative traffic before switching to `enforcement`. This prevents legitimate calls from being blocked by policies that need tuning.
- [ ] Review `X-Tessera-Decision: would_block` events in the audit log. Confirm each blocked call is intended before flipping to enforcement.
- [ ] Keep the lockdown kill switch available: set `runtime.lockdown: true` and send `SIGHUP` to the container to block all traffic immediately in an emergency. Reverse it by setting `lockdown: false` and sending another `SIGHUP`.

### Secrets in config

- [ ] Use `${ENV_VAR}` interpolation in `upstreams[].credentials.value` rather than hardcoding upstream tokens in `tessera.yaml`. Tessera resolves these at startup and never logs the resolved values.
- [ ] Do not commit `tessera.yaml` files containing real tokens to version control. Commit only the `tessera.example.yaml` template.
- [ ] Audit your Docker run commands and compose files for any `-e TOKEN=value` flags that contain real credentials before sharing those files.

### Metrics endpoint

- [ ] The `/metrics` endpoint is disabled by default (`metrics.enabled: false`). Enable it only if you have a Prometheus scraper and can protect the endpoint.
- [ ] When enabling metrics, set `TESSERA_METRICS_TOKEN` to a dedicated read-only token separate from the main bearer token. This limits exposure if the metrics token is ever leaked.
- [ ] Configure your reverse proxy to restrict `/metrics` to internal network addresses only, even if a metrics token is set.

---

## Verifying signed images

Tessera's Docker images are signed with Sigstore (keyless OIDC) via cosign.
Verify before pulling:

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/cloudmorph-ai/cloudmorph-tessera/.github/workflows/release.yml' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/cloudmorph-ai/tessera:0.1.0
```

---

## Inspecting the SBOM

```bash
cosign download attestation \
  ghcr.io/cloudmorph-ai/tessera:0.1.0 \
  | jq '.payload | @base64d | fromjson'
```
