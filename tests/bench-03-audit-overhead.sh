#!/usr/bin/env bash
# bench-03-audit-overhead.sh
#
# What it measures
#   Latency delta of the same 100 deny calls with the SQLite audit sink ON
#   vs the no-op NullSink (TESSERA_AUDIT_SINK=tests.fixtures.null_sink:NullSink).
#   This isolates the cost of the hash-chain write + sqlite flush from the
#   rest of the proxy hot path.
#
# Why this matters
#   The pitch claim is that audit is "off the hot path" thanks to the async
#   queue (P0-13). This benchmark turns that claim into a number.
#
# Output
#   tests/results/bench-03-audit-overhead.json
#
# Pass criterion
#   delta_p50 < 1 ms
#
# Discipline notes
#   - NullSink lives at tests/fixtures/null_sink.py — it is a benchmark-only
#     shim. Production deploys MUST use SqliteSink (or a tenant-isolated
#     equivalent) for hash-chain integrity. We do not commit a production
#     env shim or wrapper for the null sink; it's only loaded by the
#     TESSERA_AUDIT_SINK env var inside this script.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/_bench_lib.sh"

RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "$RESULTS_DIR"
OUT="${RESULTS_DIR}/bench-03-audit-overhead.json"

PYTHONPATH_FOR_NULL="${REPO_ROOT}"

# Helper — boot Tessera with a specific audit sink class, run 100 deny calls,
# write json to stdout (which the caller captures).
run_phase() {
  local sink_spec="$1"           # "" for default SqliteSink, or "tests.fixtures.null_sink:NullSink"
  local phase_label="$2"

  bench_kill_existing
  rm -f /tmp/tessera-bench-audit.db* /tmp/tessera-bench-serve.log /tmp/tessera-bench-mcp.log

  # Start MCP server
  AWS_API_MCP_TRANSPORT=streamable-http \
    AWS_API_MCP_HOST=127.0.0.1 \
    AWS_API_MCP_PORT=8000 \
    AWS_REGION=us-east-1 \
    AUTH_TYPE=no-auth \
    nohup "${VENV_BIN}/awslabs.aws-api-mcp-server" > /tmp/tessera-bench-mcp.log 2>&1 &
  AWS_MCP_PID=$!
  _wait_for_port 8000 60 || { echo "MCP failed"; return 1; }

  # Start tessera with chosen sink
  TESSERA_BEARER_TOKENS="alice:${TESSERA_BENCH_TOKEN}" \
    TESSERA_LOG_LEVEL=WARNING \
    TESSERA_AUDIT_SINK="${sink_spec}" \
    PYTHONPATH="${PYTHONPATH_FOR_NULL}" \
    nohup "${VENV_BIN}/tessera" serve --config "${REPO_ROOT}/tests/fixtures/benchmark.yaml" \
    > /tmp/tessera-bench-serve.log 2>&1 &
  TESSERA_PID=$!
  _wait_for_port 9000 45 || { echo "Tessera failed"; tail -20 /tmp/tessera-bench-serve.log; return 1; }

  # Run python loop, output result line to stdout — caller captures via $()
  "$VENV_PY" - "${phase_label}" <<'PY'
import http.client, json, sys, time, statistics
HOST, PORT = "127.0.0.1", 9000
TOKEN = "tk_bench_abcdef0123456789"
BODY = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "aws_s3_ListBuckets", "arguments": {}},
}).encode()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
phase = sys.argv[1]
conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
# Warmup 10
for _ in range(10):
    conn.request("POST", "/mcp/aws", BODY, HEADERS)
    conn.getresponse().read()
samples = []
for _ in range(100):
    t0 = time.perf_counter_ns()
    conn.request("POST", "/mcp/aws", BODY, HEADERS)
    conn.getresponse().read()
    samples.append((time.perf_counter_ns() - t0) / 1_000_000.0)
conn.close()
samples.sort()
def pct(p):
    k = int(round((p/100.0) * (len(samples) - 1)))
    return samples[k]
print(json.dumps({
    "phase": phase,
    "n": len(samples),
    "min_ms": round(min(samples), 3),
    "p50_ms": round(pct(50), 3),
    "p95_ms": round(pct(95), 3),
    "p99_ms": round(pct(99), 3),
    "max_ms": round(max(samples), 3),
    "mean_ms": round(statistics.mean(samples), 3),
}))
PY

  kill "$TESSERA_PID" "$AWS_MCP_PID" 2>/dev/null || true
  sleep 2
}

echo "[bench-03] phase 1/2: audit ON (SqliteSink)"
PHASE_ON=$(run_phase "" "audit_on" | tail -1)
echo "$PHASE_ON"
echo
echo "[bench-03] phase 2/2: audit OFF (NullSink)"
PHASE_OFF=$(run_phase "tests.fixtures.null_sink:NullSink" "audit_off" | tail -1)
echo "$PHASE_OFF"

# Final json — assemble both phases + deltas
"$VENV_PY" - "$PHASE_ON" "$PHASE_OFF" "$OUT" <<'PY'
import json, sys
on = json.loads(sys.argv[1])
off = json.loads(sys.argv[2])
out = sys.argv[3]
delta_p50 = round(on["p50_ms"] - off["p50_ms"], 3)
delta_p95 = round(on["p95_ms"] - off["p95_ms"], 3)
delta_p99 = round(on["p99_ms"] - off["p99_ms"], 3)
result = {
    "benchmark": "bench-03-audit-overhead",
    "tessera_version": "0.5.1",
    "audit_on": on,
    "audit_off": off,
    "delta_ms": {
        "p50": delta_p50,
        "p95": delta_p95,
        "p99": delta_p99,
    },
    "pass_criterion": "delta_p50 < 1 ms",
    "pass": delta_p50 < 1.0,
    "notes": "100 deny calls in each phase. SQLite audit vs benchmark-only NullSink shim.",
}
with open(out, "w") as f:
    json.dump(result, f, indent=2)
print(json.dumps(result, indent=2))
PY

echo
echo "[bench-03] result: ${OUT}"
