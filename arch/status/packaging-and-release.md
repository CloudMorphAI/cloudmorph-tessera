# Packaging and Release

The distribution architecture. How source becomes a PyPI wheel, a GHCR multi-arch container image, and an ECR-mirrored image consumable by the Fargate stack in `cloudmorph-mono-repo`. Plus the schema contracts that bound what the package consumes and emits.

## Distribution targets

Three publishing destinations, all driven by `.github/workflows/release.yml` on `git tag v*.*.*`:

| Target | Consumer | Auth |
|--------|----------|------|
| PyPI: `cloudmorph-tessera` | Developers running `pip install` locally / in CI | Manual twine upload from WSL (PyPI Trusted Publisher OIDC deferred per credit budget). **Current source-tree version: `0.6.0`** (per `pyproject.toml` + `tessera/_version.py` fallback). PyPI version listed below as last verified upload — see `arch/improvements/arch-scan-tessera-version-drift.md` for the full ladder of versions (0.2.1 → 0.6.0) and current PyPI delta. Last verified PyPI publish: `0.2.1` at 2026-05-15T13:51Z. |
| GHCR: `ghcr.io/cloudmorphai/tessera:<version>` | Public Docker pull, customer Docker-mode deployments | `GITHUB_TOKEN` for push. `0.2.1` multi-arch (amd64 + arm64) live on GHCR. |
| ECR: `237509402889.dkr.ecr.us-east-1.amazonaws.com/cloudmorph/tessera-cloud-wrapper:<version>` (renamed 2026-05-16 from `tessera-cloud-prod`) | Fargate pull from `cloudmorph-mono-repo`'s CDK stack — ECS service `tessera-cloud-prod` consumes `:main` (floating tag, currently == `:0.2.1`) | AWS-CLI manual push via `tessera-cloud-wrapper/build.sh`. ECS service rolled to 0.2.1 image 2026-05-16T07:01Z (deploy `ecs-svc/7011306979647899462` Completed). |

The PyPI distribution name is `cloudmorph-tessera`. The import name stays `tessera`. The rename is a PyPI pre-flight constraint — the unprefixed `tessera` is taken by an unrelated tessellation library (v0.10.0). Documented in `pyproject.toml`'s header comment.

## Python package shape

`pyproject.toml` configures setuptools with `[tool.setuptools.packages.find] where = ["."] include = ["tessera*"]`, so every subpackage under `tessera/` is shipped. Package data is curated explicitly:

```toml
[tool.setuptools.package-data]
tessera = [
    "intelligence/*.pem",         # Ed25519 public-key trust anchor
    "intelligence/*.json",        # Any bundled intelligence catalogs
    "policies_default/*.yaml",    # 18 bundled reference policies (7 generic + 5 AWS-illustrative + 6 AWS-MCP)
]
```

The PEM is the load-bearing artifact for the trust chain (see `intelligence-and-licensing.md`). The YAMLs let `tessera init` scaffold a starter policy set without a separate download.

Optional-dependency groups (`pyproject.toml:[project.optional-dependencies]`):

- `[dev]` — testing tooling (pytest, hypothesis, mypy, ruff, bandit, pip-audit, cyclonedx-bom).
- `[runtime]` — same as `[project.dependencies]` for explicit runtime-only installs.
- `[aws]` — `mcp-proxy-for-aws`, `boto3`. Required for `kind: aws_mcp` upstream and the blast-radius backend.
- `[gemini]`, `[anthropic]`, `[openai]`, `[bedrock]`, `[azure-openai]` — one per LLM provider.
- `[all-llm]` — aggregate of `[gemini, anthropic, openai, azure-openai]` (bedrock pulled via `boto3` shared with `[aws]`).
- `[oidc]` — `python-jose[cryptography]`, for JWT validation in `jwt_mcp.py` and `oidc.py`.
- `[intelligence]` — `cryptography`, for Ed25519 verification of intelligence content.
- `[infracost]` — `gql[all]`, for the GraphQL client.

The core `[project.dependencies]` set is FastAPI, uvicorn, httpx, pydantic, typer, regex (the timeout-supporting library, not stdlib `re`), watchdog, PyYAML, jmespath, **PyJWT (≥2.8)**. Every optional dep is lazily imported at the call site, so a minimal install runs the proxy with bearer auth, SQLite audit, no LLM, no cost backend, no AWS upstream.

**PyJWT note (P-cross-repo-audit, commit `9d84d82`).** The license-validator and OAuth introspection paths use `jwt.decode` / `jwt.get_unverified_header`. `PyJWT` was previously a transitive dep of `python-jose` and a runtime survival was accidental. It is now an explicit `>=2.8` pin in `[project.dependencies]` so a `pip install cloudmorph-tessera` without the `[oidc]` extra still has license-JWT decode. The `[oidc]` extra continues to layer `python-jose[cryptography]` on top for the JWKS path.

`requires-python = ">=3.12"` — driven by usage of `zoneinfo`, `match` statements in `proxy.py`, and `Annotated[..., Field(discriminator=...)]` discriminated unions in `policy/schema.py`.

## Dockerfile shape and the HOME-directory fix

`Dockerfile` is a two-stage build pinned to a SHA-256 digest of `python:3.12-slim`:

```dockerfile
FROM python:3.12-slim@sha256:401f6e1a...
```

The digest is resolved via `docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim` and committed alongside the resolution date. Refresh is manual; pinning is reproducibility-driven, not security-driven.

The runtime stage creates the `tessera` user and home directory:

```dockerfile
RUN groupadd -g 10001 tessera && \
    useradd -u 10001 -g 10001 -m -d /home/tessera -s /usr/sbin/nologin tessera && \
    mkdir -p /etc/tessera/policies /var/lib/tessera && \
    chown -R tessera:tessera /etc/tessera /var/lib/tessera
```

The `-m -d /home/tessera` flag ensures `useradd` creates the home directory; without it, libraries calling `Path.home()` fail EACCES. The previous shape was `useradd -M -d /home/tessera` (capital `M` — "do not create home"), which left `/home/tessera` non-existent. Inside the container, `Path.home()` returns `/home/tessera`, and several subsystems (`DailySpendState` defaulting to `~/.tessera/state/`, `IntelligenceClient` defaulting to `~/.tessera/intelligence/`, `LicenseValidator` writing to `cache_dir/license.json`) attempt to `mkdir(parents=True)` under that path. Without the home directory, the `mkdir` succeeded but on a path with no owner-write permission for the `tessera` user, then subsequent writes failed.

Fixing this required swapping `-M` (don't create) for `-m` (do create) and adding the explicit `-d /home/tessera`. The lowercase `m` is the load-bearing letter. The fix is captured in the Dockerfile already; it's a v0.2.1 ship-out item.

Build args: `SOURCE_DATE_EPOCH` is threaded through both stages for reproducible-build timestamps. The `make docker-build-repro` target wires this to `git log -1 --format=%ct` so the build is bit-for-bit identical across rebuilds at the same commit.

Other runtime properties:

- `EXPOSE 8080` — the FastAPI port.
- `USER tessera` — non-root execution (UID/GID 10001).
- `HEALTHCHECK` — every 30s polls `http://localhost:8080/healthz`. Three retries with a 5-second timeout each, 10-second start period.
- `ENV TESSERA_CONFIG_PATH=/etc/tessera/tessera.yaml` — config bind-mount point.
- `ENV TESSERA_POLICY_DIR=/etc/tessera/policies` — policy directory bind-mount point.
- `ENV TESSERA_AUDIT_PATH=/var/lib/tessera/audit.db` — SQLite audit DB; intended to be a named volume so the chain survives container replacement.

The pip-CVE remediation lives in both stages: `RUN pip install --no-cache-dir --upgrade "pip>=26.1.1"` to close CVE-2026-6357. The CVE is dormant in the running container (Tessera never invokes `pip install` at runtime) but image scanners flag the dormant finding; upgrading removes the noise.

## Release workflow: `.github/workflows/release.yml`

The release pipeline fires on `git tag v*.*.*` push. Five jobs, executed in this order:

```
sbom            ──┬──> sign  ──> attest-sbom
                  ├──> pypi-publish
                  └─sign──> push-ecr
```

### `sbom` (sequential gate)

Generates a CycloneDX SBOM via `cyclonedx-py environment -o sbom.json`. Pinned to `cyclonedx-bom==7.3.0`. The SBOM is uploaded both as a workflow artifact (for `attest-sbom` to consume) and as a GitHub Release asset (for direct download).

### `sign` (Sigstore keyless)

Sets up QEMU + buildx, logs into GHCR with `GITHUB_TOKEN`, builds the multi-arch image (`linux/amd64,linux/arm64`), pushes to `ghcr.io/cloudmorphai/tessera:<version>` and `:latest`. Then installs `sigstore/cosign-installer@v3` and runs `cosign sign --yes ghcr.io/cloudmorphai/tessera@<digest>` with `COSIGN_EXPERIMENTAL=1` for keyless OIDC signing. The signature lands in Sigstore's transparency log keyed on the workflow's OIDC identity (`repo:CloudMorphAI/cloudmorph-tessera`, the tag SHA).

### `attest-sbom`

Downloads the SBOM artifact, logs into GHCR, runs `cosign attest --yes --predicate sbom.json --type cyclonedx ghcr.io/cloudmorphai/tessera:<version>`. The attestation binds the SBOM to the signed image digest. Consumers can `cosign verify-attestation --type cyclonedx --certificate-identity-regexp ...` to confirm the SBOM came from this workflow.

### `pypi-publish`

Builds the wheel + sdist with `python -m build`, then publishes via `pypa/gh-action-pypi-publish@release/v1`. Authentication is OIDC via PyPI Trusted Publisher — no API token in GitHub secrets. The setup is one-time at https://pypi.org/manage/project/cloudmorph-tessera/settings/publishing/ : owner CloudMorphAI, repo cloudmorph-tessera, workflow release.yml, no environment. Trusted Publisher is intentionally not configured — GHA release.yml is deferred until credit budget allows.

### `push-ecr`

Mirrors the just-published GHCR image to the private ECR repo for Fargate consumption. The auth pattern is GitHub OIDC → AWS IAM role assumption — no long-lived AWS credentials in GitHub secrets. Specifically:

1. `aws-actions/configure-aws-credentials@v4` requests an OIDC token from GitHub (`token.actions.githubusercontent.com`).
2. AWS STS evaluates the token against the trust policy of `arn:aws:iam::237509402889:role/cloudmorph-github-ecr-push`.
3. If the trust matches, STS issues short-lived (1-hour) credentials scoped to the role's permissions.
4. `aws-actions/amazon-ecr-login@v2` does `ecr get-authorization-token` with those credentials.
5. `docker pull ghcr.io/cloudmorphai/tessera:<version>`, retag to ECR, `docker push`.

The trust policy condition restricts assumption to tag-push workflow runs of this specific repo:

```jsonc
"Condition": {
  "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
  "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:CloudMorphAI/cloudmorph-tessera:ref:refs/tags/v*" }
}
```

Any other workflow context — PR builds, push-to-branch, workflow_dispatch from a fork, builds in any other repo in the org — produces an OIDC token whose `sub` claim does not match. STS rejects the assumption. The role has zero standing permissions; the only way to use it is via a tag-push from this repo.

The permissions policy is scoped to the single ECR repository (`cloudmorph/tessera-cloud-prod`) with the layer-upload + image-push action set. `ecr:GetAuthorizationToken` is necessarily `Resource: "*"` per AWS docs — it returns a tokenization endpoint and isn't scoped to specific repositories.

Current operating mode is manual docker pull from GHCR + tag + push to ECR. The `cloudmorph-github-ecr-push` IAM role is intentionally not created. GHA release.yml exists in the repo but is not invoked due to credit budget constraints.

## Cross-publish coordination

The version string must agree across five places (per the comment block in `pyproject.toml`):

1. `pyproject.toml:[project].version`
2. `tessera/__init__.py:__version__`
3. `README.md` — every `tessera:<version>` and `tessera <version>` reference
4. `docs/INSTALL.md` — same search-and-replace
5. `CHANGELOG.md` — new section

This is fragile. A stale `README.md` renders on the GitHub project page and on PyPI's project page; customers copy-paste from there. Single-source-of-truth automation (a `bump-version.py` script, or pulling `__version__` from `pyproject.toml` at runtime via `importlib.metadata.version`) would close this gap; it's not a v0.2.x priority.

**0.2.0 → 0.2.1 bump (commit `18ffa13`, 2026-05-15).** All 5 places were updated in lockstep. The 0.2.1 release packages the cross-repo audit fixes (tier `scale`/`team` aliasing, `bundle_url` mapping URL, mandatory `manifest.signed.json` verify, `tarball_sha256` consumer check, base64 signature decoding) plus the explicit PyJWT dep.

**0.2.1 close-out — DONE 2026-05-16** (Batch 1 of `plan/tessera-improvements-plan-2026-05-16.md`):
- PyPI `cloudmorph-tessera 0.2.1` uploaded 2026-05-15T13:51Z (manual twine from WSL).
- `v0.2.1` git tag pushed to `origin/main` pointing at commit `18ffa13`.
- GHCR `ghcr.io/cloudmorphai/tessera:0.2.1` multi-arch (amd64 + arm64) live.
- ECR `cloudmorph/tessera-cloud-wrapper:0.2.1` + `:main` pushed 2026-05-16T01:42Z (repo renamed from `tessera-cloud-prod` same day).
- ECS service `tessera-cloud-prod` (cluster `tessera-cloud-prod`) force-new-deployment Completed 2026-05-16T07:01Z, deploy `ecs-svc/7011306979647899462`; 1/1 task running fresh image.
- `tessera-ratelimits-prod` DDB table ACTIVE (CDK construct `TesseraRateLimitsTable` in `cloudmorph-mono-repo/amplify/backend/tessera.ts:219`; granted R/W on the wrapper task role line 493).
- Clean-venv install verify: `pip install cloudmorph-tessera==0.2.1` → `tessera.__version__ == "0.2.1"`, 18 bundled policies present, `tessera/intelligence/public_key.pem` shipped (113 bytes).

## Schemas as the consumed/emitted contract

Three JSON Schemas live at `schemas/*.json`. They define what the package promises to consume and emit:

| Schema | Contract direction | Bound by |
|--------|-------------------|----------|
| `policy.schema.json` | Consumed | The shape of every YAML the engine loads. Pydantic models in `tessera/policy/schema.py` enforce. |
| `audit_event.schema.json` | Emitted | The shape of every event the SQLite/Stdout/Buffered sinks store. The hash chain depends on `eventHash` matching the recomputed canonical-JSON SHA-256. |
| `config.schema.json` | Consumed | The `tessera.yaml` shape. Pydantic models in `tessera/config.py` enforce. |

The schemas are JSON-Schema Draft-07 and are the externally publishable form. The authoritative runtime check is pydantic, which is stricter (extra-keys forbidden, custom validators, discriminated unions). The JSON Schemas are intended for third-party tooling (editor lint plugins, policy validators outside the Python ecosystem).

Folding schema descriptions into this doc rather than spinning a separate `status/schemas.md` keeps the file count under cap. When schemas grow non-trivially (e.g., adding a `policy_v2.schema.json` during a major schema bump), this section may split out.

## Security tooling

Three static-analysis configs at the repo root:

- `bandit.yaml` — Python AST security linter. Skipped checks: `B101` (assert), `B311` (random — not used cryptographically), and pytest-fixture-specific allowances under `[tool.ruff.lint.per-file-ignores]`.
- `gitleaks-config.toml` — secret scanner. Allowlists `tests/fixtures/**` so `*_secret*` fixture filenames don't trip the rule.
- `semgrep.yaml` — security pattern matcher. Installed separately in CI (conflicts with `click` on Python 3.13+).

These run in `.github/workflows/security.yml` (CodeQL is in a separate `codeql.yml`) on every PR.

## How Fargate consumes the image

The Fargate side lives in `cloudmorph-mono-repo`'s CDK at `amplify/backend/`. The CDK construct references the ECR image by digest (not by tag) for reproducible deploys. The wiring is:

1. Tag a release in this repo (`git tag v0.3.0 && git push origin v0.3.0`).
2. `release.yml` builds the image, signs it, pushes to GHCR + ECR.
3. Engineer (or CI in cloudmorph-mono-repo) updates the CDK construct's `ContainerImage.fromEcrRepository(repo, digest)` to the new digest.
4. CDK redeploys the Fargate service. The new image rolls out per the service's deployment config.

The boundary is intentionally a digest, not a tag — `:latest` could be rebuilt after CDK references it, and tag-mutability would produce surprising rollbacks. Digest-pinning means the Fargate side cannot accidentally pick up a different image with the same tag; promotion is explicit. Details of the Fargate construct are out of scope here and will eventually live in `cloudmorph-mono-repo/arch/`.

## Reproducible build invariants

A combination of techniques keeps the build reproducible at a given commit:

- Base image pinned to SHA-256 digest.
- `SOURCE_DATE_EPOCH` threaded through both Dockerfile stages and into pip's install timestamps.
- `pip install --no-cache-dir` so layers don't carry pip's per-build wheel cache.
- All Python dependencies pinned to exact versions in `pyproject.toml` (`==X.Y.Z`, not `>=`).
- Multi-arch built with buildx so both amd64 and arm64 emerge from one workflow run.

These are not absolute (Python wheel content can vary across upload timestamps; some transitive deps may have unpinned sub-deps), but they're tight enough that two builds at the same commit produce byte-identical layers for the typical case.

## Cross-references

- For what the wheel ships (the package surface): `overview.md`.
- For why `intelligence/public_key.pem` is in package-data: `intelligence-and-licensing.md`.
- For the bundled `policies_default/` content: `policy-engine.md`.
- For the ECR-side IAM setup the workflow needs: `WORKFLOW_REQUIRES_AWS_OIDC_SETUP.md` at the repo root.
