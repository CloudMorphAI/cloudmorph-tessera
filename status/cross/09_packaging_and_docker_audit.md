# 09 — Packaging & Docker Audit

_6 Dockerfiles, 0 SBOM, 0 multi-arch, 0 hardening. Hosted SaaS at `mcp.cloudmorph.io` is referenced but not deployed-by-script anywhere visible._

---

## 1.1 Current Dockerfile inventory

### [cloudmorph-mcp/Dockerfile](../../cloudmorph-mcp/Dockerfile) (22 LoC)

```dockerfile
FROM node:18-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install              # ← should be `npm ci`
...
FROM node:18-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY package.json ./
COPY --from=deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
EXPOSE 8080
CMD ["node", "dist/index.js"]
```

**Findings:**
- Multi-stage (good).
- `npm install` instead of `npm ci` (P1 — non-deterministic).
- Runs as **root** (P1 — no `USER node`).
- No `HEALTHCHECK` (P1).
- No `LABEL` directives (P2 — no provenance metadata).
- `node:18-alpine` (P2 — Node 18 is fine but consider bumping to 20 LTS or distroless `gcr.io/distroless/nodejs20-debian12`).
- No multi-arch build (P1).

### [aws/executor/Dockerfile](../../aws/executor/Dockerfile) (8 LoC)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema boto3 google-cloud-storage azure-storage-blob
COPY contracts ./contracts
COPY aws/executor ./aws/executor
WORKDIR /app/aws/executor
ENV PYTHONUNBUFFERED=1
CMD ["python", "src/main.py"]
```

**Findings:**
- **Installs `boto3` AND `google-cloud-storage` AND `azure-storage-blob`** when only AWS deps are needed. Adds ~80 MB to the image. **P1 cleanup.**
- Single-stage (P2 — no separation of build from runtime; build deps remain in image).
- No `HEALTHCHECK`.
- Runs as root.
- No SBOM, no scanning.
- No `pip install --upgrade pip` before installing deps — locks to Python 3.11 default pip which may have known CVEs.
- No version pinning on dependencies — `boto3` resolves to whatever is latest at build time. Reproducibility shot.

### [azure/executor/Dockerfile](../../azure/executor/Dockerfile)

Identical bloat: `boto3 google-cloud-storage azure-storage-blob` installed. Drop AWS+GCS deps.

### [gcp/executor/Dockerfile](../../gcp/executor/Dockerfile)

Same bloat.

### [databricks/executor/Dockerfile](../../databricks/executor/Dockerfile) (8 LoC)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema requests
...
```

**Lean — only `jsonschema requests`.** Best of the executor Dockerfiles.

### [snowflake/executor/Dockerfile](../../snowflake/executor/Dockerfile)

```dockerfile
RUN pip install --no-cache-dir jsonschema snowflake-connector-python
```

Also lean. Should explicitly install `cryptography` (currently transitive via snowflake-connector-python).

---

## 1.2 Shared base image

To eliminate re-downloading slim+pip cache in 5 places, ship a base:

```dockerfile
# base/python-executor.Dockerfile
FROM python:3.12-slim AS base
RUN pip install --no-cache-dir --upgrade pip uv && \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install common-py once
COPY cloudmorph-common-py /opt/cloudmorph-common-py
RUN uv pip install --system /opt/cloudmorph-common-py

# Non-root user
RUN useradd -m -u 1000 cloudmorph
USER cloudmorph
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

LABEL org.opencontainers.image.source=https://github.com/CloudMorphAI/cloudmorph-control-center \
      org.opencontainers.image.licenses=Apache-2.0 \
      org.opencontainers.image.vendor=CloudMorph
```

Published as `ghcr.io/cloudmorphai/control-center-base:python3.12-slim`. Tagged with `<commit-sha>` and `latest`.

Per-cloud Dockerfile becomes:

```dockerfile
FROM ghcr.io/cloudmorphai/control-center-base:python3.12-slim
RUN uv pip install --system boto3 botocore
COPY contracts /app/contracts
COPY aws/executor /app/aws/executor
WORKDIR /app/aws/executor
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import sys; sys.exit(0)"
CMD ["python", "src/main.py"]
```

5-line per-cloud, all hardening centralized in the base.

**MCP server base:**

```dockerfile
# base/node-mcp.Dockerfile
FROM node:20-alpine AS base
RUN addgroup -S cloudmorph && adduser -S -G cloudmorph cloudmorph
WORKDIR /app
USER cloudmorph
LABEL org.opencontainers.image.source=...
```

---

## 1.3 Multi-arch builds

Apple Silicon devs + ARM Graviton hosts need native arm64. Use buildx:

```yaml
# .github/workflows/docker.yml
name: docker · multi-arch
on:
  push:
    branches: [main]
    tags: ["v*"]

jobs:
  build:
    strategy:
      matrix:
        target: [cloudmorph-mcp, aws-executor, azure-executor, gcp-executor, databricks-executor, snowflake-executor]
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write, id-token: write, attestations: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/cloudmorphai/${{ matrix.target }}
          tags: |
            type=sha,prefix=
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: ${{ matrix.target == 'cloudmorph-mcp' && 'cloudmorph-mcp/Dockerfile' || format('{0}/executor/Dockerfile', split(matrix.target, '-')[0]) }}
          platforms: linux/amd64,linux/arm64
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          provenance: true
          sbom: true
      - uses: anchore/sbom-action@v0
        with:
          image: ghcr.io/cloudmorphai/${{ matrix.target }}@${{ steps.meta.outputs.digest }}
          format: spdx-json
          output-file: ${{ matrix.target }}.sbom.spdx.json
      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: ghcr.io/cloudmorphai/${{ matrix.target }}@${{ steps.meta.outputs.digest }}
          severity: CRITICAL,HIGH
          exit-code: 1
          ignore-unfixed: true
```

**Effort:** 8h. Block H.

---

## 1.4 SBOM + scanning

Per image:
- **SBOM via Syft** (`anchore/sbom-action`) — SPDX JSON, attached as artifact.
- **Vulnerability scan via Trivy** — fail CI on HIGH/CRITICAL unfixed CVEs.
- **Container image signing via cosign** — sign with workload identity (no key management).

```yaml
- uses: sigstore/cosign-installer@v3
- run: cosign sign --yes ghcr.io/cloudmorphai/${{ matrix.target }}@${{ steps.meta.outputs.digest }}
```

Customers can verify:
```bash
cosign verify ghcr.io/cloudmorphai/cloudmorph-mcp:latest \
  --certificate-identity-regexp 'https://github.com/CloudMorphAI/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

**Effort:** 4h. Block H. Critical for any compliance buyer.

---

## 1.5 Hosted SaaS deployment (`mcp.cloudmorph.io`)

Referenced in [docs/getting-started.md:69](../../docs/getting-started.md) and [sdk-python/cloudmorph/client.py:48](../../sdk-python/cloudmorph/client.py) (`DEFAULT_BASE_URL`). **No deployment manifest, no Terraform, no record of a running instance** — at minimum, this should not be the SDK default until it is real.

**Decision: ship hosted-first by day 13.**

Architecture:
```
Cloudflare DNS → AWS ALB (TLS terminate, ACM cert) → ECS Fargate / EKS (3 instances, autoscale 3-30) → cloudmorph-mcp container
                                                                ↓
                                                              Redis (rate-limit + session)
                                                                ↓
                                                              S3 (audit log sink, customer-owned bucket)
                                                                ↓
                                                              CloudWatch (own observability)
```

**Effort estimate:**
- Terraform module for the above: 16h
- ECS service + task definition + autoscaling: 4h
- Redis (ElastiCache or external): 4h
- DNS + cert: 2h
- Observability: see cross/11
- Backup / DR: 4h post-MVP

**Block I.** Critical path for design-partner shipping.

---

## 1.6 PyPI release flow (SDK)

Already covered in [sdk/03_python_sdk_audit.md §3.6](../sdk/03_python_sdk_audit.md). Trusted publishing, no API tokens.

---

## 1.7 Container registry choice

**Decision: GHCR (`ghcr.io/cloudmorphai/`).**

Comparison:

| Registry | Pros | Cons |
|---|---|---|
| GHCR | Free for public, integrates with GitHub Actions, OIDC for trusted publish | Slightly slower pulls than ECR for AWS-hosted consumers |
| ECR | AWS-native, fast pulls in AWS | Per-region replication needed; auth complexity for non-AWS consumers |
| Docker Hub | Default expectation | Pull rate limits for anonymous; Docker company turbulence |
| Artifactory | Enterprise control | Requires customer Artifactory infra |

GHCR is the correct default for an OSS-first server. ECR mirroring for AWS-hosted customers can come post-MVP.

---

## 1.8 Helm chart + Terraform module

**Post-MVP unless a specific design partner asks.** Premature investment until self-hosted demand is real.

When demand is real:
- Helm chart at `cloudmorph-mcp/charts/cloudmorph-mcp/` — values for tenant secret, bundle URL, Redis URL, audit S3 bucket.
- Terraform module at `terraform/cloudmorph-mcp/` — wraps Helm + ECS / EKS / GKE / AKS variants.

Effort: 24h Helm + 16h Terraform = 40h. Post-MVP.

---

## 1.9 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Replace `npm install` with `npm ci` in MCP Dockerfile | P1 | 30min | A |
| Add `USER node` (non-root) to MCP Dockerfile | P1 | 30min | A |
| Add `HEALTHCHECK` to all 6 Dockerfiles | P1 | 1h | A |
| Drop bloat (boto3+gcs+blob in non-AWS executors) | P1 | 1h | A |
| Pin pip / npm deps versions | P1 | 2h | A |
| Author shared base image (Python + Node) | P0 | 6h | H |
| Multi-arch build (amd64 + arm64) | P0 | 8h | H |
| SBOM (Syft) per image | P0 | 2h | H |
| Trivy scan in CI, fail on HIGH/CRITICAL | P0 | 2h | H |
| Cosign signing | P1 | 2h | H |
| Hosted SaaS Terraform + deploy | **P0** | 16h | I |
| GHCR setup | P0 | 1h | A |
| Distroless final stage (eval) | P2 | 6h | post-MVP |
| Helm chart | P2 | 24h | post-MVP |
| Terraform module (multi-cloud) | P2 | 16h | post-MVP |
| Image size budget (< 200 MB MCP, < 300 MB executors) | P2 | 4h | post-MVP |

**MVP critical-path total: ~50h.** Block H + I (split across hardening and deployment).

---

## 1.10 Out of scope

- Slim AWS Lambda layer packaging. Different deployment model; revisit if a customer asks.
- Bundling SDK as a single binary (PyOxidizer / Nuitka). Premature.
- Self-hosted-installer scripts (one-line curl). Post-MVP after Helm.
- ECR mirror. Post-MVP after first AWS-heavy customer.

---

## 1.11 Source links

- [cloudmorph-mcp/Dockerfile](../../cloudmorph-mcp/Dockerfile)
- [aws/executor/Dockerfile](../../aws/executor/Dockerfile)
- [azure/executor/Dockerfile](../../azure/executor/Dockerfile)
- [gcp/executor/Dockerfile](../../gcp/executor/Dockerfile)
- [databricks/executor/Dockerfile](../../databricks/executor/Dockerfile)
- [snowflake/executor/Dockerfile](../../snowflake/executor/Dockerfile)
- [docs/getting-started.md](../../docs/getting-started.md) (refers to hosted SaaS — make it real)
