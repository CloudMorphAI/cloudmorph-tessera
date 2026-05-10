# Tessera v0.1 — Open Items

These items were found during the rewrite session but NOT fixed (Phase 2 rule: no code changes after checkpoint).

## Build / Docker

- **Docker not available in build environment** — `docker build -t tessera-test:dev .` could not be run
  because Docker is not installed on this Windows machine. The Dockerfile is correct per SPEC §7
  (multi-stage, python:3.12-slim, non-root user, HEALTHCHECK). Founder must run `docker build` to verify
  < 200 MB target and do the manual smoke test from Task 14.

## Notes

- All Python tests pass (≥80% coverage); audit chain modules at 100%.
- `tessera policy lint --policy-dir policies/` exits 0 against all 7 reference policies.
- `tessera version` exits 0, prints 0.1.0.
