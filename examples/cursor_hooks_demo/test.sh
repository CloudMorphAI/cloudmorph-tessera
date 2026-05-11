#!/usr/bin/env bash
# Tessera Cursor Hooks Demo — auto-starts mock upstream + Tessera, runs 2
# `tools/call` requests, then cleans up. No external state required.
#
# Run: `bash test.sh`
#
# Exit code: 0 on both tests passing, 1 if either expectation fails.

set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DEMO_DIR"

MOCK_PORT="${MOCK_PORT:-9999}"
TESSERA_PORT="${TESSERA_PORT:-8080}"
TESSERA_URL="http://localhost:${TESSERA_PORT}"
AUDIT_DB="/tmp/tessera-demo-audit-$$.db"

MOCK_PID=""
TESSERA_PID=""

cleanup() {
  echo ""
  echo "=== Cleaning up ==="
  [ -n "$TESSERA_PID" ] && kill "$TESSERA_PID" 2>/dev/null && echo "stopped tessera (pid $TESSERA_PID)" || true
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null && echo "stopped mock (pid $MOCK_PID)" || true
  rm -f "$AUDIT_DB" "$AUDIT_DB-shm" "$AUDIT_DB-wal" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not in PATH"; exit 2; }
}

require python
require curl
require tessera

echo "=== Tessera Cursor Hooks Demo ==="
echo "Demo dir: $DEMO_DIR"
echo "Tessera:  $TESSERA_URL"
echo "Mock:     http://localhost:$MOCK_PORT"
echo ""

# ── Start mock upstream ──────────────────────────────────────────────────────
echo "[1/4] starting mock MCP server on port $MOCK_PORT..."
python mock_mcp_server.py >/dev/null 2>&1 &
MOCK_PID=$!
sleep 1
if ! kill -0 "$MOCK_PID" 2>/dev/null; then
  echo "ERROR: mock_mcp_server.py failed to start"
  exit 1
fi

# ── Start Tessera ────────────────────────────────────────────────────────────
echo "[2/4] starting tessera serve..."
TESSERA_AUDIT_PATH="$AUDIT_DB" \
  tessera serve --config "$DEMO_DIR/tessera.yaml" --bind "127.0.0.1:$TESSERA_PORT" \
  >/dev/null 2>&1 &
TESSERA_PID=$!

# Wait for /healthz to come up (max 10s).
ready=0
for _ in $(seq 1 20); do
  if curl -fsS "$TESSERA_URL/healthz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
if [ "$ready" -ne 1 ]; then
  echo "ERROR: tessera /healthz never returned 200 within 10s"
  exit 1
fi
echo "      tessera ready (pid $TESSERA_PID)"

# ── Test 1 — read action, expect allow ────────────────────────────────────────
echo ""
echo "[3/4] aws_s3_list_buckets (read.list — expect ALLOW)"
RESP=$(curl -s -X POST "$TESSERA_URL/mcp/mock" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"aws_s3_list_buckets","arguments":{}}}')

if echo "$RESP" | grep -q '"result"'; then
  echo "      [ALLOW] read passed through to upstream"
else
  echo "      [FAIL] read was not allowed"
  echo "      response: $RESP"
  exit 1
fi

# ── Test 2 — destructive write, expect block ─────────────────────────────────
echo ""
echo "[4/4] aws_s3_delete_bucket (write.delete — expect BLOCK)"
RESP=$(curl -s -X POST "$TESSERA_URL/mcp/mock" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"aws_s3_delete_bucket","arguments":{"bucket":"my-bucket"}}}')

if echo "$RESP" | grep -q '"-32603"' || echo "$RESP" | grep -q '"code":\s*-32603'; then
  echo "      [BLOCK] delete blocked with JSON-RPC -32603"
else
  echo "      [FAIL] delete was NOT blocked"
  echo "      response: $RESP"
  exit 1
fi

echo ""
echo "=== Demo passed ==="
