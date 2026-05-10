# Reproducible Builds

Tessera's Docker image is built reproducibly. Two independent builds from the same
source commit and base image digest should produce identical image SHAs.

## Verifying a build

```bash
# Build once
make docker-build-repro

# Get the image SHA
SHA1=$(docker inspect tessera-repro:dev --format '{{.Id}}')

# Build again (different shell)
make docker-build-repro

SHA2=$(docker inspect tessera-repro:dev --format '{{.Id}}')

echo "Build 1: $SHA1"
echo "Build 2: $SHA2"
[ "$SHA1" = "$SHA2" ] && echo "REPRODUCIBLE" || echo "NOT REPRODUCIBLE"
```

## Base image

The Dockerfile pins the base image to a specific digest to prevent upstream changes
from affecting the build. Update the digest after testing with `docker manifest inspect python:3.12-slim`.

## SOURCE_DATE_EPOCH

The `SOURCE_DATE_EPOCH` build arg is set to the Git commit timestamp via:

```bash
git log -1 --format=%ct
```

This ensures timestamps embedded in the image are deterministic.
