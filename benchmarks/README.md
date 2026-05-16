# Tessera Benchmarks

Pin Tessera's "sub-millisecond decision latency" and "1000+ RPS sustained" claims to reproducible numbers.

## Benches

| File | Tool | Measures | Wall-clock |
|------|------|----------|------------|
| `decision_latency.py` | pytest-benchmark | `engine.evaluate()` p50/p95/p99 over the 18-policy default set | ~30s |
| `blast_radius_latency.py` | standalone Python | Prefetch ON vs OFF, simulated 100ms IAM RTT | ~30s |
| `rps_sustained.py` | locust | Sustained RPS at p99 < 10ms, 5-step ramp 10/50/100/200/500 users | ~6 min |

## Running

```bash
# Microbench (fastest):
pytest benchmarks/decision_latency.py --benchmark-only \
  --benchmark-min-rounds=1000 --benchmark-max-time=30

# Blast-radius comparison:
python benchmarks/blast_radius_latency.py

# Sustained RPS (requires tessera serve to start, slow):
python benchmarks/rps_sustained.py
```

> **Note on rps_sustained.py**: This bench boots a real uvicorn worker, ramps through 5 concurrency steps, and takes ~6 min end-to-end. It is NOT run in CI. Founder runs it once pre-tag and records numbers in `benchmarks/results/v<version>.md`. The script soft-fails with exit 0 if locust is not installed or the 6-min budget is exceeded.

## Hardware spec

When publishing results to `benchmarks/results/v<version>.md`, record:

- CPU model + core count (e.g., "Apple M2 Max, 12 cores")
- RAM (e.g., "32 GB LPDDR5")
- OS + kernel (e.g., "Ubuntu 24.04 on WSL2 6.6.87")
- Python version (`python --version`)
- tessera version under test (`python -m tessera --version`)

Results from different machines are not directly comparable. Always publish the hardware spec alongside numbers.

## What "good" looks like

| Bench | Pass threshold | Notes |
|-------|---------------|-------|
| decision_latency p50 | < 0.5ms | 18-policy default set; cold args; 4-core/16GB baseline |
| decision_latency p99 | < 2ms | Includes ReDoS-corpus-validated regex evals |
| rps_sustained @ 100 users | > 500 RPS, p99 < 10ms | Single uvicorn worker, no upstream calls |
| blast_radius prefetch win | > 2x latency reduction | vs prefetch OFF, 100ms simulated IAM RTT |

## Published results

Per-release numbers live in `benchmarks/results/`:

- `results/v0.4.0.md` — bench numbers for the v0.4.0 release.

Results files are committed by the founder after a manual pre-tag run. They are NOT auto-committed by CI (CI only runs decision_latency + blast_radius; see `.github/workflows/bench.yml`).

## Re-running on every release

`.github/workflows/bench.yml` (SA-6C) runs `decision_latency` and `blast_radius_latency` on every tagged release and commits updated numbers to `results/v<tag>.md`. `rps_sustained` is founder-run pre-tag because it requires a stable server boot and is not safe to run in a shared CI runner.
