# Manual Actions Checklist

These 3 actions require GitHub Settings UI access. Sonnet cannot perform these — the
founder applies them manually Monday morning before pushing.

## 1. Enable Security Advisories + Private Vulnerability Reporting

GitHub Settings → Security → Code security and analysis → Private vulnerability reporting → Enable.

**Steps:**

1. Navigate to `https://github.com/cloudmorph-ai/cloudmorph-tessera/settings/security_analysis`
2. Under "Private vulnerability reporting" → click **Enable**
3. Under "Dependency graph" → ensure it is **Enabled**

**Verification:**

```bash
curl -s https://api.github.com/repos/cloudmorph-ai/cloudmorph-tessera \
  | jq '.security_and_analysis.private_vulnerability_reporting.status'
# Expected: "enabled"
```

---

## 2. Enable CodeQL (Code Scanning)

The workflow file `.github/workflows/codeql.yml` is already created by this run. GitHub must
be told to activate it.

**Steps:**

1. After pushing `feature/days-0-90-tessera`, open a PR (or push to main after review).
2. GitHub Actions will automatically pick up `.github/workflows/codeql.yml` on the next push.
3. Alternatively: Settings → Code security and analysis → Code scanning → **Set up (Advanced)** →
   select the existing `codeql.yml` workflow.

**Verification:**

After the first CI run: Settings → Code security and analysis → Code scanning should show
the last scan result and a green or yellow status badge.

```bash
# Check via API (requires a GitHub token with repo scope)
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/cloudmorph-ai/cloudmorph-tessera/code-scanning/analyses \
  | jq '.[0] | {created_at, tool: .tool.name, ref}'
# Expected: object with tool "CodeQL" and a recent created_at timestamp
```

---

## 3. Push v0.1.0 Docker Image to GHCR

The Docker image must be built on a Mac/Linux machine (Docker Desktop required).
Sonnet cannot push to GHCR from this run.

**Steps:**

```bash
# Run on a machine with Docker installed and GHCR access
export GHCR_PAT="<your-github-personal-access-token>"
docker login ghcr.io -u cloudmorph-ai -p "$GHCR_PAT"

docker build -t ghcr.io/cloudmorph-ai/tessera:0.1.0 .
docker tag ghcr.io/cloudmorph-ai/tessera:0.1.0 ghcr.io/cloudmorph-ai/tessera:latest

docker push ghcr.io/cloudmorph-ai/tessera:0.1.0
docker push ghcr.io/cloudmorph-ai/tessera:latest
```

**Verification:**

```bash
docker pull ghcr.io/cloudmorph-ai/tessera:0.1.0
docker run --rm ghcr.io/cloudmorph-ai/tessera:0.1.0 tessera --version
# Expected: tessera 0.1.0
```

To confirm the image is publicly visible in the GHCR registry:

```bash
curl -s https://ghcr.io/v2/cloudmorph-ai/tessera/tags/list \
  -H "Authorization: Bearer $(echo $GHCR_PAT | base64)" \
  | jq '.tags'
# Expected: ["0.1.0","latest"]
```
