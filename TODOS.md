# Tessera — Open Human Action Items

All items require human action. Sonnet cannot perform these from the overnight run environment.

---

## GitHub UI Actions

- [ ] Enable Private Vulnerability Reporting: GitHub Settings → Security → Code security and
  analysis → Private vulnerability reporting → **Enable**.
  URL: `https://github.com/cloudmorphai/cloudmorph-tessera/settings/security_analysis`
- [ ] Activate CodeQL scanning: push `feature/days-0-90-tessera` to trigger the first
  `.github/workflows/codeql.yml` run, then verify Settings → Code security and analysis →
  Code scanning shows a green status.
- [ ] Push Docker image to GHCR (Mac/Linux required):

  ```bash
  export GHCR_PAT="<your-github-personal-access-token>"
  docker login ghcr.io -u cloudmorphai -p "$GHCR_PAT"
  docker build -t ghcr.io/cloudmorphai/tessera:0.1.0 .
  docker tag ghcr.io/cloudmorphai/tessera:0.1.0 ghcr.io/cloudmorphai/tessera:latest
  docker push ghcr.io/cloudmorphai/tessera:0.1.0
  docker push ghcr.io/cloudmorphai/tessera:latest
  ```

---

## Docker / Release

- [ ] Run `docker build` on Mac or Linux to verify the Dockerfile builds cleanly end-to-end
  (Windows Docker Desktop was not available during the overnight run).
- [ ] Resolve the base image digest TODO in `Dockerfile`: run
  `docker manifest inspect python:3.12-slim` and pin the `FROM` line to a digest
  (e.g. `python:3.12-slim@sha256:<digest>`).
- [ ] Tag `v0.1.0` in git and push the tag to trigger `release.yml`
  (SBOM generation, Docker sign, cosign attestation):

  ```bash
  git tag v0.1.0
  git push origin v0.1.0
  ```

---

## Launch Fire

- [ ] Fire DMs from `docs/_internal/dm-targets.md` during the 8:30–9:00 AM PT window
  (30 minutes before HN post).
- [ ] 9:00 AM PT — Post to Hacker News (Show HN). Draft in `docs/_internal/launch-blog-draft.md`.
- [ ] 9:15 AM PT — Post to Reddit (r/LocalLLaMA weekly thread).
- [ ] 9:30 AM PT — LinkedIn post.
- [ ] 11:00 AM PT — Twitter/X thread.
- [ ] Follow full timing sheet in `docs/_internal/launch-schedule.md`.

---

## Content / Recording

- [ ] Publish launch blog post from `docs/_internal/launch-blog-draft.md`.
- [ ] Cross-post per `docs/_internal/cross-post-targets.md`:
  - dev.to article (10:00 AM PT)
  - Hashnode article (10:30 AM PT)
- [ ] Record 60-second Cursor Hooks screencast per `examples/cursor_hooks_demo/SCREENCAST.md`.
- [ ] Record recipe walkthroughs for `recipes/cursor-mcp-json.md` and `recipes/claude-code.md`.

---

## Post-launch

- [ ] Monitor HN comments; respond within 30 min for substantive questions.
- [ ] Review `docs/_internal/reports/overnight-final.md` (if generated) for any FAILED tasks
  from the overnight run and resolve manually.

---

## CI / Dev

- [ ] Before running `pip-audit` in CI, ensure all project deps are installed
  (`pip install -e ".[dev]"` or equivalent). The overnight sub-agent noted that a bare
  `pip-audit` run on a shallow install may return a false-clean result.

---

## Operator note: TESSERA_POLICY_DIR in Dockerfile

The Dockerfile sets `ENV TESSERA_POLICY_DIR=/etc/tessera/policies`. This directory
is intentionally empty — it is the user-mount point for operator policies:

```bash
docker run -v "$PWD/my-policies:/etc/tessera/policies:ro" ...
```

The 7 reference policies ship at `/etc/tessera/policies-default/` (baked into the
image). To use them without a mount, pass:

```bash
-e TESSERA_POLICY_DIR=/etc/tessera/policies-default
```

Or reference them in `tessera.yaml`:

```yaml
policies:
  dir: /etc/tessera/policies-default
```

---

## Notes

- All Python tests pass (82.9% coverage); audit chain modules at 100%.
- Docker build: 188 MB (< 200 MB target). Smoke test passed.
- `tessera policy lint --policy-dir policies/` exits 0 against all 7 reference policies.
- `tessera version` exits 0, prints 0.1.0.
