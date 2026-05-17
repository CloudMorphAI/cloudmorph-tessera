#!/usr/bin/env bash
# Helper: stop any prior bench-spawned tessera/awslabs procs.
# Standalone so callers from /bin/bash can use it without inheriting set -u etc.
for p in $(pgrep -f "tessera serve" 2>/dev/null) $(pgrep -f "awslabs.aws-api-mcp-server" 2>/dev/null); do
  kill -KILL "$p" 2>/dev/null || true
done
sleep 2
