# Installing the staged Tessera source

**Staged source, not a confirmed distribution:** this checkout declares version
1.0.0. Publication of its PyPI wheel and `ghcr.io` container tag has not been
verified. An unversioned `pip install` resolves whatever the package index
currently serves, which may differ from this checkout. Treat the image pull,
signature-verification, and reproducible-build commands below as release
procedures that require a separately confirmed artifact; they are not evidence
that an image or signature is currently available. See the repository's
[current maintenance note](../README.md#repository-role-and-current-maintenance).

The project metadata requires Python `>=3.12` and classifies Python 3.12. Retained
local evidence covers a CPython 3.13.7 Windows editable Tessera install and
existing CPython 3.12.3 WSL tests. A separate Hermes client environment needed
a dependency-marker workaround; that was not a Tessera installation step.
This is limited environment evidence, not a complete platform support matrix. No
production installation path is established by these local checks. The offline
quickstart pilot passed 15 checks on Linux/WSL; its IP-socket guard currently
prevents Windows asyncio startup. See [the quickstart](../QUICKSTART.md).

---

## Table of contents

1. [Docker image install (publication unverified)](#1-docker-image-install-publication-unverified)
2. [Python source install](#2-python-source-install)
3. [docker-compose walkthrough](#3-docker-compose-walkthrough)
4. [Volume mount cheatsheet](#4-volume-mount-cheatsheet)
5. [`tessera init` 60-second walkthrough](#5-tessera-init-60-second-walkthrough)
6. [Deployment hardening notes](#6-deployment-hardening-notes-not-release-acceptance)
7. [Verifying an image signature](#7-verifying-an-image-signature-when-published)
8. [Checking build reproducibility](#8-checking-build-reproducibility-not-yet-verified)

---

## 1. Docker image install (publication unverified)

Use these example commands only after confirming that the exact image tag and
its signature were published. Publication of `1.0.0` is currently unverified.

```bash
# Example only; availability of this tag has not been confirmed.
docker pull ghcr.io/cloudmorphai/tessera:1.0.0
```

```bash
docker run -d \
  --name tessera \
  -p 8080:8080 \
  -v "$PWD/tessera.yaml:/etc/tessera/tessera.yaml:ro" \
  -v "$PWD/policies:/etc/tessera/policies:ro" \
  -v tessera_audit:/var/lib/tessera \
  -e TESSERA_BEARER_TOKEN="tk_$(openssl rand -hex 16)" \
  ghcr.io/cloudmorphai/tessera:1.0.0
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

## 2. Python source install

The distribution name is `cloudmorph-tessera`; the import name is `tessera`.
PyPI publication of the source version in this checkout has not been verified.
The editable-install command below is for a source checkout, not a PyPI release.
The retained local environment evidence is limited to CPython 3.13.7 on Windows
for an editable Tessera install and existing
CPython 3.12.3 tests in WSL.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
```

**Dev / source install:**

```bash
git clone https://github.com/cloudmorphai/cloudmorph-tessera.git
cd cloudmorph-tessera
pip install -e ".[dev]" && pre-commit install
```

---

## 3. docker-compose walkthrough

`docker-compose.example.yaml` (in the repo root) starts Tessera alongside a mock MCP server — fastest end-to-end evaluation without a real upstream.

```bash
git clone https://github.com/cloudmorphai/cloudmorph-tessera.git
cd cloudmorph-tessera
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
# Docker: docker run --rm -v "$PWD:/out" ghcr.io/cloudmorphai/tessera:1.0.0 tessera init --dir /out
```

Output files: `tessera.yaml` (mode: `log_only`), `.env.example`, and a `policies/`
directory with two selected bundled guards: `secret-leak-block.yaml` and
`prompt-injection-heuristic.yaml`. The latter is a heuristic and can flag
legitimate text discussing injection patterns. Other illustrative/vendor/cost
policies are not automatically activated. Inspect and add policies for your actual
tool names and argument shapes before enabling enforcement. Bundled schema
acceptance alone does not establish that a rule matches your upstream's requests.

An existing policy directory is left unchanged unless `--force` is supplied.
With `--force`, the two selected starter files are overwritten; unrelated policy
files are retained. Existing configuration and `.env.example` are also overwritten
only with `--force`.

**Add an upstream in `tessera.yaml`:** the scaffold starts with `upstreams: []`.
Add an entry with a `name` and your MCP server `url`, following
[the configuration guide](CONFIGURATION.md). Add `credentials.header`/
`credentials.value` if auth is required; use `${ENV_VAR}` interpolation for secrets.

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

**Switch to enforcement** after checking configured allow/block decisions:
set `policies.mode: enforcement` in `tessera.yaml`, then `docker restart tessera`.
The scaffold uses `default_action: block`; the two starter guards add no allow
rules, so unapproved calls also block in enforcement mode. Add narrow allow rules
for the operations you intend to permit and verify both outcomes. In `log_only`,
decisions are recorded but calls are forwarded.

---

## 6. Deployment hardening notes (not release acceptance)

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

This runtime has no native rate limiter. Place it behind a reverse proxy before exposing it to a network.

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

## 7. Verifying an image signature (when published)

The commands below describe a Sigstore/cosign verification procedure. They do
not establish that the `1.0.0` image or its signature is published; confirm the
release artifact and its attestation independently before relying on them.

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/cloudmorphai/cloudmorph-tessera/.github/workflows/release.yml' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/cloudmorphai/tessera:1.0.0
```

**SBOM:**

```bash
cosign download attestation \
  ghcr.io/cloudmorphai/tessera:1.0.0 \
  | jq '.payload | @base64d | fromjson'
```

---

## 8. Checking build reproducibility (not yet verified)

The commands below are a procedure for checking whether two builds from the same
source commit and base image digest produce identical image SHAs. The procedure
does not itself establish a reproducible-build result.

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
