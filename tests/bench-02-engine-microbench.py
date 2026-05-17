#!/usr/bin/env python3
"""bench-02-engine-microbench.py

What it measures
  Pure in-process PolicyEngine.evaluate() latency across 4 representative
  scenarios from the 24-policy default bundle. No HTTP, no audit write,
  no upstream — just the cost of walking the ruleset.

Why this matters
  Pitch claim: "sub-millisecond decision latency, deterministic."
  This is the cleanest measurement of that claim — every microsecond above
  this floor is plumbing (HTTP, JSON, audit, network) and is captured by
  bench-01 instead.

Methodology
  - 1,000 warmup iterations (discarded) per scenario.
  - 10,000 measured iterations per scenario.
  - time.perf_counter_ns() for sub-microsecond precision.
  - Single thread, no GC tweaks.

Output
  tests/results/bench-02-engine-microbench.json (microseconds)

Pass criterion
  safe_tool_call p50 < 100 us
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time
from typing import Any

from tessera.policy.engine import PolicyEngine
from tessera.policy.loader import FilesystemPolicyLoader

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
POLICIES_DIR = REPO_ROOT / "tessera" / "policies_default"
OUT = REPO_ROOT / "tests" / "results" / "bench-02-engine-microbench.json"


def _ctx(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_call": {"name": tool_name, "arguments": args, "_meta": {}},
        "intent": None,
        "upstream": "aws",
        "runtime": {"lockdown": False},
        "scope": "bench",
        "state_backend": None,
        "blast_radius_backend": None,
        "cost_backend": None,
        "cost_cache": {},
        "aws_mapping": None,
    }


SCENARIOS = {
    "safe_tool_call": _ctx("aws_s3_GetObject", {"Bucket": "mybucket", "Key": "foo.txt"}),
    "blocked_iam_pass_role": _ctx(
        "aws_iam_PassRole",
        {
            "RoleArn": "arn:aws:iam::123456789012:role/AdministratorAccess",
            "RoleSessionName": "test",
        },
    ),
    "blocked_admin_policy_attach": _ctx(
        "aws_iam_AttachRolePolicy",
        {
            "RoleName": "test-role",
            "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
        },
    ),
    "no_match_passthrough": _ctx("custom_no_match_tool", {"arg1": "value1"}),
}


def percentile(samples_us: list[float], p: float) -> float:
    if not samples_us:
        return 0.0
    s = sorted(samples_us)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[k]


def run_scenario(engine: PolicyEngine, ctx: dict[str, Any], n_warmup: int, n_measured: int) -> dict[str, Any]:
    # Warmup
    for _ in range(n_warmup):
        engine.evaluate(ctx)

    samples: list[float] = []
    for _ in range(n_measured):
        t0 = time.perf_counter_ns()
        engine.evaluate(ctx)
        samples.append((time.perf_counter_ns() - t0) / 1000.0)  # ns -> us

    return {
        "n": len(samples),
        "min_us": round(min(samples), 3),
        "p50_us": round(percentile(samples, 50), 3),
        "p95_us": round(percentile(samples, 95), 3),
        "p99_us": round(percentile(samples, 99), 3),
        "max_us": round(max(samples), 3),
        "mean_us": round(statistics.mean(samples), 3),
        "stddev_us": round(statistics.stdev(samples), 3) if len(samples) > 1 else 0.0,
    }


def main() -> int:
    loader = FilesystemPolicyLoader(POLICIES_DIR)
    policies = loader.load_all("default")
    engine = PolicyEngine(policies)
    n_policies = len(policies)

    results: dict[str, Any] = {}
    for name, ctx in SCENARIOS.items():
        results[name] = run_scenario(engine, ctx, n_warmup=1_000, n_measured=10_000)

    safe_p50 = results["safe_tool_call"]["p50_us"]
    out = {
        "benchmark": "bench-02-engine-microbench",
        "tessera_version": "0.5.1",
        "n_policies_loaded": n_policies,
        "policies_dir": str(POLICIES_DIR.relative_to(REPO_ROOT)),
        "warmup_iterations": 1_000,
        "measured_iterations": 10_000,
        "scenarios": results,
        "pass_criterion": "safe_tool_call p50 < 100 us",
        "pass": safe_p50 < 100.0,
        "notes": "Pure in-process engine eval. time.perf_counter_ns() timing.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
