#!/usr/bin/env bash
# tests/_bench_lib.sh — common helpers for bench-0{1..5}*.sh
# Source from a bench script with:  source "$(dirname "$0")/_bench_lib.sh"
#
# Exports:
#   TESSERA_BENCH_TOKEN   — bearer token (same value for all benches)
#   TESSERA_URL           — http://127.0.0.1:9000
#   MCP_URL               — http://127.0.0.1:8000
#   REPO_ROOT             — absolute path to the cloudmorph-tessera repo root
#   VENV_PY               — absolute path to the venv-bench python
#
# Provides functions:
#   bench_boot              — start MCP server then Tessera + warmup
#   bench_teardown          — kill both background procs
#   bench_kill_existing     — kill any stale procs (defensive)
#   bench_warmup_tessera N  — fire N discarded calls to prime the connection pool

set -u

TESSERA_BENCH_TOKEN="tk_bench_abcdef0123456789"
TESSERA_URL="http://127.0.0.1:9000"
MCP_URL="http://127.0.0.1:8000"
REPO_ROOT="/mnt/c/Users/found/Desktop/CloudMorph/cloudmorph-tessera"
VENV_PY="${REPO_ROOT}/.venv-bench/bin/python"
VENV_BIN="${REPO_ROOT}/.venv-bench/bin"

bench_kill_existing() {
  for p in $(pgrep -f "tessera serve" 2>/dev/null) $(pgrep -f "awslabs.aws-api-mcp-server" 2>/dev/null); do
    kill -KILL "$p" 2>/dev/null || true
  done
  sleep 2
}

# Poll until a port is bound, or fail after N seconds.
_wait_for_port() {
  local port="$1"
  local max="${2:-60}"
  local i=0
  while [ $i -lt $max ]; do
    if ss -tlnp 2>/dev/null | grep -q ":${port}\b"; then return 0; fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

bench_boot() {
  bench_kill_existing
  rm -f /tmp/tessera-bench-audit.db* /tmp/tessera-bench-serve.log /tmp/tessera-bench-mcp.log

  echo "[boot] starting awslabs.aws-api-mcp-server on :8000 (may take 20-30s on /mnt filesystem)"
  AWS_API_MCP_TRANSPORT=streamable-http \
    AWS_API_MCP_HOST=127.0.0.1 \
    AWS_API_MCP_PORT=8000 \
    AWS_REGION=us-east-1 \
    AUTH_TYPE=no-auth \
    nohup "${VENV_BIN}/awslabs.aws-api-mcp-server" > /tmp/tessera-bench-mcp.log 2>&1 &
  AWS_MCP_PID=$!
  if ! _wait_for_port 8000 60; then
    echo "[boot] FAIL — MCP server did not bind 8000 in 60s"
    tail -30 /tmp/tessera-bench-mcp.log
    return 1
  fi
  echo "[boot] MCP server ready (pid $AWS_MCP_PID)"

  echo "[boot] starting tessera serve on :9000"
  TESSERA_BEARER_TOKENS="alice:${TESSERA_BENCH_TOKEN}" \
    TESSERA_LOG_LEVEL=WARNING \
    nohup "${VENV_BIN}/tessera" serve --config "${REPO_ROOT}/tests/fixtures/benchmark.yaml" \
    > /tmp/tessera-bench-serve.log 2>&1 &
  TESSERA_PID=$!
  if ! _wait_for_port 9000 45; then
    echo "[boot] FAIL — tessera did not bind 9000 in 45s"
    tail -30 /tmp/tessera-bench-serve.log
    return 1
  fi
  echo "[boot] tessera ready (pid $TESSERA_PID)"

  # Sanity health
  curl -sf "${TESSERA_URL}/healthz" >/dev/null || {
    echo "[boot] FAIL — /healthz did not return 2xx"
    tail -30 /tmp/tessera-bench-serve.log
    return 1
  }
  export AWS_MCP_PID TESSERA_PID
  echo "[boot] ready — TESSERA_PID=$TESSERA_PID AWS_MCP_PID=$AWS_MCP_PID"
}

bench_warmup_tessera() {
  local n="${1:-5}"
  for ((i = 1; i <= n; i++)); do
    curl -sS -o /dev/null \
      -H "Authorization: Bearer ${TESSERA_BENCH_TOKEN}" \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -X POST \
      -d '{"jsonrpc":"2.0","id":'"$i"',"method":"tools/call","params":{"name":"aws_s3_ListBuckets","arguments":{}}}' \
      "${TESSERA_URL}/mcp/aws"
  done
}

bench_teardown() {
  kill "${TESSERA_PID:-0}" 2>/dev/null || true
  kill "${AWS_MCP_PID:-0}" 2>/dev/null || true
  sleep 1
}

# millisecond-precision wall clock
now_ms() {
  date +%s%N | awk '{ printf "%d\n", $1/1000000 }'
}
