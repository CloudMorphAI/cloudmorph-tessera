#!/usr/bin/env bash
set -e

BASE_URL="${TESSERA_URL:-http://localhost:8080}"
TOKEN="${TESSERA_BEARER_TOKEN:-}"

auth_header=""
if [ -n "$TOKEN" ]; then
  auth_header="-H \"Authorization: Bearer $TOKEN\""
fi

echo "=== Tessera Cursor Hooks Demo ==="
echo "Base URL: $BASE_URL"
echo ""

# Test 1: List buckets (should allow)
echo "[TEST 1] aws.s3.list_buckets (expect: allow)"
RESP=$(curl -s -X POST "$BASE_URL/mcp/mock" \
  -H "Content-Type: application/json" \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"aws.s3.list_buckets","arguments":{}}}')

if echo "$RESP" | grep -q '"result"'; then
  echo "[PASS] List buckets: allowed"
else
  echo "[INFO] List buckets result: $RESP"
fi

echo ""

# Test 2: Delete bucket (should block)
echo "[TEST 2] aws.s3.delete_bucket (expect: block)"
RESP=$(curl -s -X POST "$BASE_URL/mcp/mock" \
  -H "Content-Type: application/json" \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"aws.s3.delete_bucket","arguments":{"bucket":"my-bucket"}}}')

if echo "$RESP" | grep -q '"-32603"' || echo "$RESP" | grep -q '"code":-32603'; then
  echo "[PASS] Delete bucket: blocked by Tessera"
elif echo "$RESP" | grep -q '"error"'; then
  echo "[PASS] Delete bucket: blocked (error returned)"
  echo "  Response: $RESP"
else
  echo "[FAIL] Delete bucket was NOT blocked"
  echo "  Response: $RESP"
  exit 1
fi

echo ""
echo "=== Demo complete ==="
