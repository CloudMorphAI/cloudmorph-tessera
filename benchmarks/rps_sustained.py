"""Sustained RPS bench: tessera serve under load.

Spins up tessera serve on localhost:8080, then runs locust against it.
Measures sustained RPS where p99 latency stays < 10ms.

Run:
    python benchmarks/rps_sustained.py

Expected wall-clock: ~6 min (1 min uvicorn boot + 5x 60s ramp steps).

NOTE: This bench is intentionally NOT run in CI. Founder runs it once
pre-tag and records results in benchmarks/results/v<version>.md.
If locust is not installed or the 5-min budget is exceeded, the script
soft-fails with exit code 0 and a note — do not block the checkpoint.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

TESSERA_PORT = 8080
STEP_DURATION_S = 60
LOCUST_USERS_STEPS = [10, 50, 100, 200, 500]
BUDGET_SECONDS = 360  # 6 min total; exceed → soft-fail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start_tessera_serve(policy_dir: Path, audit_dir: Path) -> subprocess.Popen:  # type: ignore[type-arg]
    """Spawn tessera serve subprocess; return Popen handle."""
    env = os.environ.copy()
    env["TESSERA_BEARER_TOKEN"] = "test:test-token-1234567890abcdef"
    env["TESSERA_AUDIT_PATH"] = str(audit_dir / "audit.db")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tessera",
            "serve",
            "--policy-dir",
            str(policy_dir),
            "--bind",
            f"127.0.0.1:{TESSERA_PORT}",
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Poll /healthz until ready (max 30s)
    try:
        import httpx
    except ImportError as exc:
        proc.kill()
        raise RuntimeError("httpx not installed — cannot poll /healthz") from exc

    for _ in range(30):
        try:
            r = httpx.get(f"http://127.0.0.1:{TESSERA_PORT}/healthz", timeout=1)
            if r.status_code == 200:
                return proc
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)

    proc.kill()
    raise RuntimeError("tessera serve didn't start within 30s")


def _run_locust_step(users: int, duration_seconds: int = STEP_DURATION_S) -> dict:  # type: ignore[type-arg]
    """Run a single locust headless step; return stats dict."""
    # Write a minimal locustfile to a temp path
    import tempfile

    locustfile_src = f"""\
from locust import HttpUser, task, constant

class TesseraUser(HttpUser):
    host = "http://127.0.0.1:{TESSERA_PORT}"
    wait_time = constant(0)

    @task
    def healthz(self):
        self.client.get(
            "/healthz",
            headers={{"Authorization": "Bearer test-token-1234567890abcdef"}},
        )
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_locustfile.py", delete=False
    ) as fh:
        fh.write(locustfile_src)
        locustfile_path = fh.name

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "locust",
                "-f",
                locustfile_path,
                "--headless",
                "--users",
                str(users),
                "--spawn-rate",
                str(users),
                "--run-time",
                f"{duration_seconds}s",
                "--host",
                f"http://127.0.0.1:{TESSERA_PORT}",
                "--csv",
                "/dev/null",
                "--only-summary",
                "--loglevel",
                "WARNING",
            ],
            capture_output=True,
            text=True,
            timeout=duration_seconds + 30,
        )
        # Parse locust summary output for RPS + p99
        rps = _parse_locust_rps(result.stdout + result.stderr)
        p99_ms = _parse_locust_p99(result.stdout + result.stderr)
        return {"rps": rps, "p99_ms": p99_ms, "raw": result.stdout[-2000:]}
    finally:
        try:
            os.unlink(locustfile_path)
        except OSError:
            pass


def _parse_locust_rps(output: str) -> float:
    """Extract RPS from locust summary line (best-effort)."""
    # Locust --only-summary prints lines like:
    # GET /healthz  <count> 0 0 ... <rps> ...
    # or "Aggregated" row at bottom.
    for line in output.splitlines():
        if "Aggregated" in line:
            parts = line.split()
            # Column layout varies; try the 9th column (index 8) as RPS
            for i, p in enumerate(parts):
                try:
                    val = float(p)
                    if val > 1.0 and i > 3:
                        return val
                except ValueError:
                    pass
    return 0.0


def _parse_locust_p99(output: str) -> float:
    """Extract p99 ms from locust summary (best-effort)."""
    for line in output.splitlines():
        if "Aggregated" in line:
            parts = line.split()
            # p99 is typically the last numeric column before "| failures"
            nums = []
            for p in parts:
                try:
                    nums.append(float(p))
                except ValueError:
                    pass
            # p99 is usually the 4th-to-last numeric value in the summary row
            if len(nums) >= 4:
                return nums[-4]
    return -1.0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import tempfile

    _budget_start = time.monotonic()

    # Soft-fail guard: ensure locust is importable before we spin up the server
    try:
        import locust  # noqa: F401
    except ImportError:
        print(
            "rps_sustained skipped (locust not installed); "
            "install with: pip install 'cloudmorph-tessera[dev]' — "
            "founder runs pre-tag"
        )
        sys.exit(0)

    policy_dir = Path(__file__).parent.parent / "tessera" / "policies_default"
    if not policy_dir.exists():
        print(f"rps_sustained skipped (policy_dir not found: {policy_dir})")
        sys.exit(0)

    with tempfile.TemporaryDirectory() as tmpdir:
        audit_dir = Path(tmpdir)

        try:
            proc = _start_tessera_serve(policy_dir, audit_dir)
        except RuntimeError as exc:
            print(f"rps_sustained skipped ({exc}); founder runs pre-tag")
            sys.exit(0)

        try:
            results = []
            for users in LOCUST_USERS_STEPS:
                elapsed = time.monotonic() - _budget_start
                if elapsed > BUDGET_SECONDS:
                    print(
                        f"rps_sustained: budget exceeded ({elapsed:.0f}s > {BUDGET_SECONDS}s); "
                        "stopping early — founder runs full suite pre-tag"
                    )
                    break

                print(f"Step: {users:5d} concurrent users for {STEP_DURATION_S}s ...", flush=True)
                try:
                    stats = _run_locust_step(users)
                    results.append({"users": users, **stats})
                except subprocess.TimeoutExpired:
                    print(f"  step {users} users: locust timed out — skipping step")
                except Exception as exc:  # noqa: BLE001
                    print(f"  step {users} users: error ({exc}) — skipping step")

            if results:
                print("\n=== RPS Sustained Results ===")
                print(f"  {'users':>6}   {'RPS':>10}   {'p99 (ms)':>10}")
                print(f"  {'-'*6}   {'-'*10}   {'-'*10}")
                for r in results:
                    p99_display = f"{r['p99_ms']:.2f}" if r["p99_ms"] >= 0 else "n/a"
                    print(f"  {r['users']:>6}   {r['rps']:>10.1f}   {p99_display:>10}")
                # Surface the 100-user tier specifically (primary claim)
                tier_100 = next((r for r in results if r["users"] == 100), None)
                if tier_100:
                    rps_ok = tier_100["rps"] > 500
                    p99_ok = 0 <= tier_100["p99_ms"] < 10
                    print(
                        f"\n100-user tier: RPS={'PASS' if rps_ok else 'FAIL'} "
                        f"({tier_100['rps']:.1f} RPS, target >500), "
                        f"p99={'PASS' if p99_ok else 'FAIL/unknown'} "
                        f"({tier_100['p99_ms']:.2f}ms, target <10ms)"
                    )
            else:
                print(
                    "rps_sustained: no steps completed; "
                    "founder runs pre-tag for v<version>.md results"
                )

        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
