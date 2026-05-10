# Launch Day Checklist — 2026-05-12

This is the tactical checklist for Tuesday morning. Work through it in order.

## Pre-launch smoke (do this Monday night / Tuesday morning)

- [ ] Run the worked example end-to-end on a clean machine:

  ```bash
  cd examples/cursor_hooks_demo
  python mock_mcp_server.py &
  tessera serve --config tessera.yaml &
  bash test.sh
  ```

  Expected: `[PASS] List buckets: allowed` and `[PASS] Delete bucket: blocked by Tessera`

- [ ] Verify Docker image works (requires GHCR push first — see TODOS.md):

  ```bash
  docker pull ghcr.io/cloudmorph-ai/tessera:0.1.0
  docker run --rm ghcr.io/cloudmorph-ai/tessera:0.1.0 tessera version
  ```

  Expected: `tessera 0.1.0`

- [ ] Verify all recipe walkthroughs work locally:
  - `recipes/cursor-mcp-json.md`
  - `recipes/cursor-hooks.md`
  - `recipes/claude-code.md`

- [ ] Push the branch and create PR / merge to main (if not done):

  ```bash
  git push origin feature/days-0-90-tessera
  ```

- [ ] Apply GitHub UI settings (see `docs/_internal/manual-actions.md`):
  - [ ] Enable Private Vulnerability Reporting
  - [ ] Verify CodeQL workflow is active

## Launch fire sequence (9:00 AM - 1:00 PM PT)

Follow the full timing in `docs/_internal/launch-schedule.md`. Summary:

- [ ] 9:00 AM PT — Post to Hacker News (Show HN)
- [ ] 9:15 AM PT — Post to Reddit (r/LocalLLaMA weekly thread)
- [ ] 9:30 AM PT — LinkedIn post
- [ ] 10:00 AM PT — dev.to article
- [ ] 10:30 AM PT — Hashnode article
- [ ] 11:00 AM PT — Twitter/X thread

## Response handling rules

- **HN comments**: respond within 30 min for substantive questions. Be direct, no marketing. Acknowledge known limitations.
- **"Why not Rego/OPA?"**: Rego in enforcement path is a v0.2+ feature, deferred to avoid OPA runtime dependency.
- **"What about Windsurf/Cline?"**: Cursor + Claude Code at launch. Windsurf/Cline/Aider deferred.
- **"Where's the cloud version?"**: Days 30-60 plan. Design partners welcome.

## DM sending

Fire DMs from `docs/_internal/dm-targets.md` in the 30-minute window before HN post (8:30 AM - 9:00 AM PT).

## Post-launch

- [ ] Tag v0.1.0 in git and push tag:

  ```bash
  git tag v0.1.0
  git push origin v0.1.0
  ```

  This triggers `release.yml`: SBOM generation, Docker sign, cosign attestation.

- [ ] Record 60-second screencasts (see `examples/cursor_hooks_demo/SCREENCAST.md`)

- [ ] Update the HN submission if significantly downvoted (do not delete — edits are fine)
