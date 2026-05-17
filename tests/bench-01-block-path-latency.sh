#!/usr/bin/env bash
# bench-01-block-path-latency.sh
#
# What it measures
#   p50/p95/p99/min/max wall-clock latency of 100 sequential POSTs that get
#   DENIED by the s3-data-block policy. This is the "deterministic firewall"
#   hot path: HTTP -> auth -> policy eval -> audit write -> error response.
#   Upstream is never invoked (Tessera blocks before forwarding).
#
# Why this matters
#   Pitch claim: "block expensive calls in milliseconds, not seconds."
#   This number is the headline for that claim — full proxy overhead on a
#   guaranteed-deny path, with the engine, audit chain, and HTTP middleware
#   all in the loop.
#
# Output
#   tests/results/bench-01-block-path-latency.json
#
# Pass criterion
#   p50 < 5 ms.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/_bench_lib.sh"

RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "$RESULTS_DIR"
OUT="${RESULTS_DIR}/bench-01-block-path-latency.json"

bench_boot
trap "bench_teardown" EXIT

# Warmup — 5 deny calls to prime connection pool + python lazy-imports.
bench_warmup_tessera 5

# Measured loop — 100 sequential deny calls. Use python for sub-ms precision
# (date +%s%N is only millisecond-precise on WSL2 anyway, but we want a single
# consistent timer across runs).
"$VENV_PY" - <<PY
import http.client, json, time, statistics, pathlib
HOST, PORT = "127.0.0.1", 9000
TOKEN = "${TESSERA_BENCH_TOKEN}"
BODY = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "aws_s3_ListBuckets", "arguments": {}},
}).encode()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# Reuse a single connection — measures Tessera, not TCP handshake.
conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
samples_ms = []
denied = 0
errors = 0

# Warmup again on this connection
for _ in range(5):
    conn.request("POST", "/mcp/aws", BODY, HEADERS)
    r = conn.getresponse()
    r.read()

# Measured
for i in range(100):
    t0 = time.perf_counter_ns()
    conn.request("POST", "/mcp/aws", BODY, HEADERS)
    r = conn.getresponse()
    payload = r.read()
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    samples_ms.append(elapsed_ms)
    try:
        j = json.loads(payload)
        if "error" in j and "s3-data-block" in str(j.get("error", {})):
            denied += 1
        else:
            errors += 1
    except Exception:
        errors += 1

conn.close()
samples_ms.sort()

def pct(p):
    if not samples_ms: return 0.0
    k = int(round((p/100.0) * (len(samples_ms) - 1)))
    return samples_ms[k]

result = {
    "benchmark": "bench-01-block-path-latency",
    "tessera_version": "0.5.1",
    "n_total": len(samples_ms),
    "n_denied": denied,
    "n_errors": errors,
    "latency_ms": {
        "min": round(min(samples_ms), 3),
        "p50": round(pct(50), 3),
        "p95": round(pct(95), 3),
        "p99": round(pct(99), 3),
        "max": round(max(samples_ms), 3),
        "mean": round(statistics.mean(samples_ms), 3),
        "stddev": round(statistics.stdev(samples_ms), 3) if len(samples_ms) > 1 else 0.0,
    },
    "pass_criterion": "p50 < 5 ms",
    "pass": pct(50) < 5.0,
    "notes": "100 sequential deny calls, single keep-alive connection, after 10-call warmup.",
}
pathlib.Path("${OUT}").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
PY

echo
echo "[bench-01] result: ${OUT}"
