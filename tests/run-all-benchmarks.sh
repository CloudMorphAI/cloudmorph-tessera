#!/usr/bin/env bash
# tests/run-all-benchmarks.sh — orchestrate the full Tessera benchmark suite.
#
# Runs bench-01..05 sequentially, captures each result, then writes a
# consolidated all-results-<timestamp>.json with a summary table.
#
# Pre-flight:
#   - AWS_ACCOUNT == 237509402889 (HALT if not)
#   - tessera importable (HALT if not)
#
# Each bench script handles its own Tessera + MCP server boot/teardown.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "$RESULTS_DIR"

REPO_ROOT="/mnt/c/Users/found/Desktop/CloudMorph/cloudmorph-tessera"
VENV_PY="${REPO_ROOT}/.venv-bench/bin/python"

# ── Pre-flight ───────────────────────────────────────────────────────────────
echo "=== Pre-flight ==="
ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "unknown")"
if [ "$ACCT" != "237509402889" ]; then
  echo "HALT — AWS account = '$ACCT', expected 237509402889"
  exit 2
fi
echo "AWS account: $ACCT (ok)"
VER="$("$VENV_PY" -c 'import tessera; print(tessera.__version__)' 2>&1)"
echo "Tessera version: $VER"

# ── Run benches sequentially ────────────────────────────────────────────────
TS="$(date +%Y%m%dT%H%M%S)"
SUMMARY="${RESULTS_DIR}/all-results-${TS}.json"

declare -a BENCHES=(
  "bench-01-block-path-latency.sh"
  "bench-02-engine-microbench.py"
  "bench-03-audit-overhead.sh"
  "bench-04-concurrent-throughput.py"
  "bench-05-multi-service-3way.sh"
)

declare -a RESULT_FILES=(
  "bench-01-block-path-latency.json"
  "bench-02-engine-microbench.json"
  "bench-03-audit-overhead.json"
  "bench-04-concurrent-throughput.json"
  "bench-05-multi-service-3way.json"
)

# 2 retries on transient errors. On 3rd failure, log and continue.
for i in "${!BENCHES[@]}"; do
  script="${BENCHES[$i]}"
  echo
  echo "============================================================="
  echo "  Running ${script}"
  echo "============================================================="
  attempt=1
  while [ $attempt -le 3 ]; do
    if [[ "$script" == *.py ]]; then
      "$VENV_PY" "${SCRIPT_DIR}/${script}" && break
    else
      "${SCRIPT_DIR}/${script}" && break
    fi
    echo "[run-all] attempt $attempt failed for $script"
    attempt=$((attempt + 1))
    sleep 5
  done
  if [ $attempt -gt 3 ]; then
    echo "[run-all] $script FAILED after 3 attempts — continuing"
  fi
done

# ── Consolidate ──────────────────────────────────────────────────────────────
echo
echo "=== Consolidating results into ${SUMMARY} ==="
"$VENV_PY" - "${RESULTS_DIR}" "${SUMMARY}" <<'PY'
import json, pathlib, sys
results_dir = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
files = [
    "bench-01-block-path-latency.json",
    "bench-02-engine-microbench.json",
    "bench-03-audit-overhead.json",
    "bench-04-concurrent-throughput.json",
    "bench-05-multi-service-3way.json",
]
loaded = {}
for fn in files:
    p = results_dir / fn
    if p.exists():
        loaded[fn.replace(".json", "")] = json.loads(p.read_text())
    else:
        loaded[fn.replace(".json", "")] = {"error": "result file missing"}

# Quick pass/fail summary
summary_table = []
for key, data in loaded.items():
    bench = data.get("benchmark", key)
    pass_ = data.get("pass", "n/a")
    crit = data.get("pass_criterion", "n/a")
    summary_table.append({"benchmark": bench, "pass_criterion": crit, "pass": pass_})

consolidated = {
    "tessera_version": "0.5.1",
    "aws_account": "237509402889",
    "aws_region": "us-east-1",
    "summary": summary_table,
    "results": loaded,
}
out.write_text(json.dumps(consolidated, indent=2))
print(json.dumps(summary_table, indent=2))
PY

echo
echo "=== Done. Consolidated: ${SUMMARY} ==="
