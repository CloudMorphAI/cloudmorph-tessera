# Installing Tessera v0.8

Docker is the recommended path for production. pip install is for local development, CI, and programmatic embedding.

---

## Table of contents

1. [Docker install (recommended)](#1-docker-install-recommended)
2. [pip install (secondary)](#2-pip-install-secondary)
3. [docker-compose walkthrough](#3-docker-compose-walkthrough)
4. [Volume mount cheatsheet](#4-volume-mount-cheatsheet)
5. [`tessera init` 60-second walkthrough](#5-tessera-init-60-second-walkthrough)
6. [Production hardening checklist](#6-production-hardening-checklist)
7. [Verifying signed images](#7-verifying-signed-images)
8. [Reproducible builds](#8-reproducible-builds)

---

## 1. Docker install (recommended)

```bash
docker pull ghcr.io/cloudmorphai/tessera:0.9.0
```

```bash
docker run -d \
  --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" \
  ghcr.io/cloudmorphai/tessera:0.9.0
```

| Flag | Purpose |
|---|---|
| `-p 8080:8080` | Host port. Change the left side to remap. |
| `-v "$PWD/tessera.yaml:…:ro"` | Config file, read-only. Required — Tessera exits if absent. |
| `-v "$PWD/policies:…:ro"` | Policy directory, read-only. Auto-reloaded on change. |
| `-v tessera_audit:/var/lib/tessera` | Named volume for the SQLite audit DB. Survives restarts. |
| `-e TESSERA_BEARER_TOKEN="tk_…"` | Inbound bearer token. Store the generated value for your MCP client config. |

**Verify:** `curl -s http://localhost:8080/healthz | python3 -m json.tool` — response contains `"status": "ok"` and `"errored": []`. If `errored` is non-empty, check which policy file failed.

```bash
docker logs tessera          # one-shot
docker logs -f tessera       # follow
```

---

## 2. pip install (secondary)

Requires Python 3.12+. Package name on PyPI: `cloudmorph-tessera`. Import name: `tessera`.

```bash
python3 -m venv .venv
source .venv/bin/activate    # Linux/macOS  |  .venv\Scripts\Activate.ps1 on Windows
pip install cloudmorph-tessera && tessera --help
```

**Dev / source install:**

```bash
git clone https://github.com/cloudmorphai/cloudmorph-tessera.git && cd tessera
pip install -e ".[dev]" && pre-commit install
```

---

## 3. docker-compose walkthrough

`docker-compose.example.yaml` (in the repo root) starts Tessera alongside a mock MCP server — fastest end-to-end evaluation without a real upstream.

```bash
git clone https://github.com/cloudmorphai/cloudmorph-tessera.git && cd tessera
docker compose -f docker-compose.example.yaml up
```

Services started: **tessera** on port 8080, **mock-upstream** on port 8081 (accepts any `tools/call` and returns a canned response).

**Test request:**

```bash
curl -s -X POST http://localhost:8080/mcp/mock \
  -H "Authorization: Bearer tk_demo_REPLACE_ME_minimum_16_chars" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "list_buckets", "arguments": {}},
    "id": 1
  }'
```

Response headers include `X-Tessera-Mode: log_only` and `X-Tessera-Decision: would_allow` (default mode).

**Inspect audit chain:** `docker compose -f docker-compose.example.yaml exec tessera tessera audit verify --db /var/lib/tessera/audit.db`

**Tear down:** `docker compose -f docker-compose.example.yaml down` (keeps volume) or append `-v` to remove volume.

**Adapting for a real upstream:** replace the `mock` upstream entry in `tessera.example.yaml` (same `upstreams:` YAML shape as section 5). Set upstream token env vars in the compose `environment:` block.

---

## 4. Volume mount cheatsheet

| Container path | Contents | Mount type | Notes |
|---|---|---|---|
| `/etc/tessera/tessera.yaml` | Main config | Bind (file, `:ro`) | Required. |
| `/etc/tessera/policies` | Policy YAML directory | Bind (dir, `:ro`) | Required. Empty dir is valid. |
| `/var/lib/tessera` | SQLite audit database | Named volume or bind (rw) | Required for persistence. |

**Named volume backup:**

```bash
docker run --rm -v tessera_audit:/data -v "$PWD/backup:/backup" \
  alpine tar czf /backup/audit-$(date +%Y%m%d).tar.gz -C /data .
```

**Bind mount:** `-v "/srv/tessera/audit:/var/lib/tessera"` — host dir must be writable by UID 10001 (`chown 10001:10001`).

**Multi-token file:** `-v "$PWD/tokens.yaml:/etc/tessera/tokens.yaml:ro" -e TESSERA_BEARER_TOKENS_FILE="/etc/tessera/tokens.yaml"` — see `tokens.example.yaml` for format.

**Config path override:** `-e TESSERA_CONFIG_PATH="/etc/tessera/custom.yaml"`

---

## 5. `tessera init` 60-second walkthrough

```bash
tessera init   # pip install
# Docker: docker run --rm -v "$PWD:/out" ghcr.io/cloudmorphai/tessera:0.7.0 tessera init --dir /out
```

Output files: `tessera.yaml` (mode: `log_only`), `policies/`, `.env.example`.

**Edit upstream in `tessera.yaml`:** set `upstreams[0].url` to your MCP server URL. Add `credentials.header`/`credentials.value` if auth is required; use `${ENV_VAR}` interpolation for secrets.

**Set bearer token:**

```bash
export TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)"
```

**Start:** use the `docker run` command from [section 1](#1-docker-install-recommended), substituting `-e TESSERA_BEARER_TOKEN="$TESSERA_BEARER_TOKEN"` for the inline `openssl` subshell.

**Configure MCP client** (Cursor `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "aws-via-tessera": {
      "url": "http://localhost:8080/mcp/aws",
      "headers": {"Authorization": "Bearer tk_your_token_here"}
    }
  }
}
```

See [docs/INTEGRATIONS.md](./INTEGRATIONS.md) for Claude Code, Claude Desktop, and Windsurf snippets.

**Verify audit log:** `docker exec tessera tessera audit verify` — shows `status: ok` and event count.

**Switch to enforcement** when ready: set `policies.mode: enforcement` in `tessera.yaml`, then `docker restart tessera`.

---

## 6. Production hardening checklist

### Token management

- [ ] Generate token: `openssl rand -hex 16` (32 hex chars = 16 bytes entropy).
- [ ] Unique token per environment (dev / staging / prod).
- [ ] Multi-client: use `TESSERA_BEARER_TOKENS_FILE` — one named token per client, revokable individually.
- [ ] Rotate quarterly minimum, or immediately after suspected exposure. Add new token before removing old one to avoid downtime.
- [ ] Store in a secrets manager (AWS Secrets Manager, Vault, Doppler). Never commit real tokens.
- [ ] Mount tokens file `:ro`, permissions `600` on host.

### Audit database

- [ ] Volume included in backup schedule. File: `/var/lib/tessera/audit.db`.
- [ ] Test restore before going live — verify with `tessera audit verify`.
- [ ] Use local SSD-backed storage. SQLite WAL mode is not safe on NFS/EFS without provisioned IOPS.
- [ ] Monitor file size. No auto-rotation. Archive old events if storage is constrained — retain the latest head row per scope.

### Reverse proxy and rate limiting

Tessera v0.1 has no built-in rate limiting. Place behind a reverse proxy before exposing to the network.

**nginx** (site config snippet):

```nginx
upstream tessera { server 127.0.0.1:8080; }
server {
    listen 443 ssl;
    server_name tessera.example.com;
    ssl_certificate     /etc/letsencrypt/live/tessera.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tessera.example.com/privkey.pem;
    limit_req_zone $binary_remote_addr zone=tessera_limit:10m rate=60r/m;
    location /mcp/ {
        limit_req zone=tessera_limit burst=20 nodelay;
        proxy_pass http://tessera;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
    location /healthz { proxy_pass http://tessera; }
}
```

Caddy: `reverse_proxy /mcp/* localhost:8080` with a `rate_limit` block (60 req/min per host). Cloudflare: dashboard rate limiting rule on `/mcp/*`.

### Network exposure

- [ ] Bind to `127.0.0.1`, not `0.0.0.0`. Let the reverse proxy handle TLS and public exposure.
- [ ] Firewall/security group: only the reverse proxy reaches port 8080.
- [ ] Docker bridge networking: use `--network` to isolate Tessera from other services.

### Mode selection

- [ ] Start in `log_only`. Monitor for at least one full day before switching to `enforcement`.
- [ ] Review `X-Tessera-Decision: would_block` events before flipping mode.
- [ ] Emergency kill switch: set `runtime.lockdown: true` and send `SIGHUP`. Reverse the same way.

### Secrets in config

- [ ] Use `${ENV_VAR}` interpolation in `upstreams[].credentials.value`. Tessera never logs resolved values.
- [ ] Commit only `tessera.example.yaml`, never files with real tokens.

### Metrics endpoint

- [ ] Disabled by default (`metrics.enabled: false`). Enable only with a Prometheus scraper behind access controls.
- [ ] Use a dedicated `TESSERA_METRICS_TOKEN` separate from the main bearer token.
- [ ] Restrict `/metrics` to internal addresses at the reverse proxy.

---

## 7. Verifying signed images

Images are signed with Sigstore (keyless OIDC) via cosign.

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/cloudmorphai/cloudmorph-tessera/.github/workflows/release.yml' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/cloudmorphai/tessera:0.9.0
```

**SBOM:**

```bash
cosign download attestation \
  ghcr.io/cloudmorphai/tessera:0.9.0 \
  | jq '.payload | @base64d | fromjson'
```

---

## 8. Reproducible builds

Two independent builds from the same source commit and base image digest produce identical image SHAs. The Dockerfile pins the base image to a specific digest; `SOURCE_DATE_EPOCH` is set to the Git commit timestamp (`git log -1 --format=%ct`) so embedded timestamps are deterministic.

```bash
make docker-build-repro
SHA1=$(docker inspect tessera-repro:dev --format '{{.Id}}')

make docker-build-repro
SHA2=$(docker inspect tessera-repro:dev --format '{{.Id}}')

echo "Build 1: $SHA1"
echo "Build 2: $SHA2"
[ "$SHA1" = "$SHA2" ] && echo "REPRODUCIBLE" || echo "NOT REPRODUCIBLE"
```

To update the pinned base image digest after testing: `docker manifest inspect python:3.12-slim`.
