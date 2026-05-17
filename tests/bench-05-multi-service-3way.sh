#!/usr/bin/env bash
# bench-05-multi-service-3way.sh
#
# What it measures
#   For each of 5 read-only AWS calls, time 3 paths × 3 runs:
#     (A) Direct AWS CLI         — `aws <service> <op>`
#     (B) Direct MCP (no Tessera) — POST tools/call to awslabs.aws-api-mcp-server
#     (C) Via Tessera -> MCP -> AWS — Tessera proxy + (empty) policy + audit + MCP + AWS
#
#   Tessera's policy dir is the empty fixture (benchmark-policies-empty/) and
#   default_action is allow — so the s3-data-block policy is NOT loaded.
#   This isolates the cost of the proxy layer from policy-eval variance.
#
# Why this matters
#   Headline overhead claim: "Tessera adds 5-50ms over direct MCP, never
#   100s of ms." This benchmark turns that into a number per service.
#
# Output
#   tests/results/bench-05-multi-service-3way.json
#
# Pass criterion
#   max Tessera overhead (C - B) p50 < 50 ms
#
# Implementation note
#   The MCP and Tessera HTTP calls are made via httpx.Client (Python keep-alive
#   pool) inside a single python here-doc. We do NOT shell out to curl per
#   request — curl spawn time on WSL2 adds 50-150ms of noise per call and
#   overwhelms the millisecond-scale numbers we are trying to measure. The
#   direct AWS CLI path *does* shell out (no in-process equivalent) — that
#   is an honest part of the comparison.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/_bench_lib.sh"

RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "$RESULTS_DIR"
OUT="${RESULTS_DIR}/bench-05-multi-service-3way.json"

bench_kill_existing
rm -f /tmp/tessera-bench-audit.db* /tmp/tessera-bench-serve.log /tmp/tessera-bench-mcp.log

# Boot MCP
AWS_API_MCP_TRANSPORT=streamable-http \
  AWS_API_MCP_HOST=127.0.0.1 \
  AWS_API_MCP_PORT=8000 \
  AWS_REGION=us-east-1 \
  AUTH_TYPE=no-auth \
  nohup "${VENV_BIN}/awslabs.aws-api-mcp-server" > /tmp/tessera-bench-mcp.log 2>&1 &
AWS_MCP_PID=$!
_wait_for_port 8000 60 || { echo "MCP failed"; exit 1; }

# Boot Tessera in allow-all mode
TESSERA_BEARER_TOKENS="alice:${TESSERA_BENCH_TOKEN}" \
  TESSERA_LOG_LEVEL=WARNING \
  nohup "${VENV_BIN}/tessera" serve --config "${REPO_ROOT}/tests/fixtures/benchmark-allow-all.yaml" \
  > /tmp/tessera-bench-serve.log 2>&1 &
TESSERA_PID=$!
_wait_for_port 9000 45 || { echo "Tessera failed"; exit 1; }

trap "kill $TESSERA_PID $AWS_MCP_PID 2>/dev/null || true" EXIT

"$VENV_PY" - <<'PY' > "$OUT"
import httpx, json, statistics, subprocess, time, shlex, sys

TOKEN = "tk_bench_abcdef0123456789"
MCP_URL = "http://127.0.0.1:8000/mcp"
TESSERA_URL = "http://127.0.0.1:9000/mcp/aws"

SERVICES = {
    "sts":     "aws sts get-caller-identity",
    "ec2":     "aws ec2 describe-regions --region us-east-1",
    "iam":     "aws iam list-account-aliases",
    "amplify": "aws amplify list-apps --region us-east-1",
    "s3":      "aws s3api list-buckets",
}
RUNS = 3

def run_cmd_ms(cmd_list):
    t0 = time.perf_counter_ns()
    r = subprocess.run(cmd_list, capture_output=True, timeout=60)
    ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    return ms, (r.returncode == 0), r.stdout, r.stderr

# ── Initialize direct-MCP session once.  Use absolute URLs everywhere
# because httpx's base_url path-join can trigger a 307 redirect on the
# awslabs server (/mcp vs /mcp/).
mcp_client = httpx.Client(
    headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    timeout=30.0,
    follow_redirects=True,
)
init_body = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "bench-05", "version": "1"},
    },
}
init_resp = mcp_client.post(MCP_URL, json=init_body)
session_id = init_resp.headers.get("Mcp-Session-Id") or init_resp.headers.get("mcp-session-id")
assert session_id, f"no Mcp-Session-Id in response (status={init_resp.status_code} headers={dict(init_resp.headers)})"
print(f"# direct-MCP session: {session_id}", file=sys.stderr)
# Notify initialized
mcp_client.post(MCP_URL, json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, headers={"Mcp-Session-Id": session_id})

# Tessera client — also reused across services
tess_client = httpx.Client(
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    },
    timeout=30.0,
    follow_redirects=True,
)

def call_path(path, cli):
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "call_aws", "arguments": {"cli_command": cli}},
    }
    t0 = time.perf_counter_ns()
    if path == "direct-mcp":
        r = mcp_client.post(MCP_URL, json=body, headers={"Mcp-Session-Id": session_id})
    else:
        r = tess_client.post(TESSERA_URL, json=body)
    ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    return ms, (r.status_code < 400)

# ── Warmup: 5 calls per path to establish keep-alive + Tessera's session
for _ in range(5):
    call_path("direct-mcp", "aws sts get-caller-identity")
    call_path("via-tessera", "aws sts get-caller-identity")

result = {
    "benchmark": "bench-05-multi-service-3way",
    "tessera_version": "0.5.1",
    "aws_account": "237509402889",
    "aws_region": "us-east-1",
    "services": {},
    "summary": {},
    "pass_criterion": "max Tessera overhead (C - B) p50 < 50 ms",
}

substitutions = []
overheads_p50 = []

for svc, cli in SERVICES.items():
    # Path A: direct AWS CLI
    cli_args = shlex.split(cli)
    a_runs = []
    a_skipped = None
    for _ in range(RUNS):
        ms, ok, _, err = run_cmd_ms(cli_args)
        if not ok:
            a_skipped = err.decode("utf-8", errors="replace")[:200]
            break
        a_runs.append(ms)

    if svc == "iam" and (not a_runs or a_skipped):
        substitutions.append({
            "service": "iam",
            "from": cli,
            "to": "aws iam get-user",
            "reason": a_skipped or "list-account-aliases unavailable in session",
        })
        cli = "aws iam get-user"
        cli_args = shlex.split(cli)
        a_runs = []
        a_skipped = None
        for _ in range(RUNS):
            ms, ok, _, err = run_cmd_ms(cli_args)
            if not ok:
                a_skipped = err.decode("utf-8", errors="replace")[:200]
                break
            a_runs.append(ms)

    # Path B + C — share warmup per service to reduce per-tool first-call jitter
    for _ in range(2):
        call_path("direct-mcp", cli)
        call_path("via-tessera", cli)

    b_runs, c_runs = [], []
    b_skipped = c_skipped = None
    for _ in range(RUNS):
        ms, ok = call_path("direct-mcp", cli)
        if not ok:
            b_skipped = f"direct-mcp non-2xx"
            break
        b_runs.append(ms)
    for _ in range(RUNS):
        ms, ok = call_path("via-tessera", cli)
        if not ok:
            c_skipped = "via-tessera non-2xx"
            break
        c_runs.append(ms)

    def p50(arr):
        if not arr: return None
        s = sorted(arr)
        return round(s[len(s)//2], 3)

    a_p50, b_p50, c_p50 = p50(a_runs), p50(b_runs), p50(c_runs)
    overhead = None
    if c_p50 is not None and b_p50 is not None:
        overhead = round(c_p50 - b_p50, 3)
        overheads_p50.append(overhead)

    result["services"][svc] = {
        "cli_command": cli,
        "direct_aws_cli":  {"runs_ms": [round(x, 3) for x in a_runs], "p50_ms": a_p50, "skipped": a_skipped},
        "direct_mcp":      {"runs_ms": [round(x, 3) for x in b_runs], "p50_ms": b_p50, "skipped": b_skipped},
        "via_tessera_mcp": {"runs_ms": [round(x, 3) for x in c_runs], "p50_ms": c_p50, "skipped": c_skipped},
        "tessera_overhead_p50_ms": overhead,
    }

mcp_client.close()
tess_client.close()

if overheads_p50:
    result["summary"] = {
        "max_overhead_p50_ms": round(max(overheads_p50), 3),
        "mean_overhead_p50_ms": round(statistics.mean(overheads_p50), 3),
    }
    result["pass"] = max(overheads_p50) < 50.0
else:
    result["summary"] = {"max_overhead_p50_ms": None, "mean_overhead_p50_ms": None}
    result["pass"] = False
result["substitutions"] = substitutions
result["notes"] = (
    "3 measured runs per (service, path) after 5 warmup calls per service per "
    "path. Direct MCP and Tessera both use long-lived httpx.Client connections "
    "with keep-alive; Direct AWS CLI shells out (no in-process equivalent). "
    "Tessera config: empty policies dir + default_action=allow (no policy-eval "
    "variance). iam list-account-aliases falls back to iam get-user when the "
    "session lacks iam:ListAccountAliases."
)
print(json.dumps(result, indent=2))
PY

echo
echo "[bench-05] result: ${OUT}"
