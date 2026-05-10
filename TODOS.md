# Tessera v0.1 — Open Items

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

Or reference them in tessera.yaml:
```yaml
policies:
  dir: /etc/tessera/policies-default
```

## Notes

- All Python tests pass (82.9% coverage); audit chain modules at 100%.
- Docker build: 188 MB (< 200 MB target). Smoke test passed.
- `tessera policy lint --policy-dir policies/` exits 0 against all 7 reference policies.
- `tessera version` exits 0, prints 0.1.0.
