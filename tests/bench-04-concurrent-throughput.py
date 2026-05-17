#!/usr/bin/env python3
"""bench-04-concurrent-throughput.py

What it measures
  Sustained RPS and tail latency of Tessera under concurrent load on the
  guaranteed-deny path (`aws_s3_ListBuckets` blocked by s3-data-block). The
  full middleware stack runs — auth, engine eval, audit write — but the
  upstream is never invoked.

  We also fire a small set of live-AWS calls (50 sequential) through the
  allow path against `aws sts get-caller-identity` to confirm end-to-end
  works under load — captured as `live_aws_sequential` in the JSON.

Why this matters
  Throughput claim from v0.4.0-production: ~2k RPS per single uvicorn
  worker (wrk-driven, 8 threads x 25 conns). This bench is the pure-Python
  reproducer using a thread pool with per-thread keep-alive HTTP
  connections so reviewers can verify the number without wrk.

Methodology
  - Block path: 2,000 requests via ThreadPoolExecutor with N persistent
    http.client.HTTPConnection objects (one per worker thread). Each
    worker pulls a connection from a queue, fires a request, returns
    the connection.
  - Plus 50 sequential live-AWS sts calls to prove end-to-end allow path.

Output
  tests/results/bench-04-concurrent-throughput.json

Pass criterion
  RPS > 500 on block path.

Discipline notes
  - awslabs.aws-api-mcp-server v1.3.36 serializes per-session requests,
    which is why the live-AWS variant is sequential rather than concurrent.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import pathlib
import queue
import socket
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "tests" / "results" / "bench-04-concurrent-throughput.json"
VENV_BIN = REPO_ROOT / ".venv-bench" / "bin"


def _port_open(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False


def _wait_for_port(port: int, max_s: int) -> bool:
    for _ in range(max_s):
        if _port_open(port):
            return True
        time.sleep(1)
    return False


def boot_stack() -> tuple[subprocess.Popen, subprocess.Popen] | None:
    """Boot MCP server + Tessera if they aren't already running on 8000/9000.

    Returns (mcp_proc, tessera_proc) for the procs we spawned, or None if
    both ports already in use (caller-side boot via _bench_lib.sh).
    """
    mcp_proc = None
    tess_proc = None
    if not _port_open(8000):
        env = dict(os.environ)
        env.update({
            "AWS_API_MCP_TRANSPORT": "streamable-http",
            "AWS_API_MCP_HOST": "127.0.0.1",
            "AWS_API_MCP_PORT": "8000",
            "AWS_REGION": "us-east-1",
            "AUTH_TYPE": "no-auth",
        })
        log = open("/tmp/tessera-bench-mcp.log", "wb")
        mcp_proc = subprocess.Popen(
            [str(VENV_BIN / "awslabs.aws-api-mcp-server")],
            env=env, stdout=log, stderr=log, start_new_session=True,
        )
        if not _wait_for_port(8000, 60):
            raise RuntimeError("MCP server failed to bind 8000 in 60s")
    if not _port_open(9000):
        env = dict(os.environ)
        env.update({
            "TESSERA_BEARER_TOKENS": "alice:tk_bench_abcdef0123456789",
            "TESSERA_LOG_LEVEL": "WARNING",
        })
        log = open("/tmp/tessera-bench-serve.log", "wb")
        tess_proc = subprocess.Popen(
            [
                str(VENV_BIN / "tessera"), "serve",
                "--config", str(REPO_ROOT / "tests" / "fixtures" / "benchmark.yaml"),
            ],
            env=env, stdout=log, stderr=log, start_new_session=True,
        )
        if not _wait_for_port(9000, 45):
            raise RuntimeError("tessera failed to bind 9000 in 45s")
    return (mcp_proc, tess_proc)


def kill_proc(p: subprocess.Popen | None) -> None:
    if p is None:
        return
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass

HOST, PORT = "127.0.0.1", 9000
TOKEN = "tk_bench_abcdef0123456789"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
BLOCK_BODY = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "aws_s3_ListBuckets", "arguments": {}},
}).encode()


def _make_conn() -> http.client.HTTPConnection:
    c = http.client.HTTPConnection(HOST, PORT, timeout=10)
    c.connect()
    return c


def run_block_path() -> dict[str, Any]:
    n_total = 2000
    concurrency = 32

    # Pool of N persistent connections. Workers borrow + return.
    conn_pool: queue.Queue[http.client.HTTPConnection] = queue.Queue()
    for _ in range(concurrency):
        conn_pool.put(_make_conn())

    # Warmup — single connection, 10 calls
    warm = _make_conn()
    for _ in range(10):
        warm.request("POST", "/mcp/aws", BLOCK_BODY, HEADERS)
        warm.getresponse().read()
    warm.close()

    def worker(idx: int) -> tuple[float, str]:
        conn = conn_pool.get()
        t0 = time.perf_counter_ns()
        try:
            conn.request("POST", "/mcp/aws", BLOCK_BODY, HEADERS)
            r = conn.getresponse()
            text = r.read().decode("utf-8", errors="replace")
            latency_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
            outcome = "ok" if "s3-data-block" in text else "tessera_error"
            return (latency_ms, outcome)
        except Exception:
            # Rebuild the connection if it dies
            try:
                conn.close()
            except Exception:
                pass
            conn = _make_conn()
            return ((time.perf_counter_ns() - t0) / 1_000_000.0, "tessera_error")
        finally:
            conn_pool.put(conn)

    results: list[tuple[float, str]] = []
    wall_t0 = time.perf_counter_ns()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, i + 1) for i in range(n_total)]
        for fut in as_completed(futures):
            results.append(fut.result())
    wall_elapsed_s = (time.perf_counter_ns() - wall_t0) / 1_000_000_000.0

    # Cleanup
    while not conn_pool.empty():
        try:
            conn_pool.get_nowait().close()
        except Exception:
            pass

    latencies = [r[0] for r in results]
    ok_count = sum(1 for r in results if r[1] == "ok")
    tessera_errors = sum(1 for r in results if r[1] == "tessera_error")
    rps = n_total / wall_elapsed_s if wall_elapsed_s > 0 else 0.0

    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = int(round((p / 100.0) * (len(latencies) - 1)))
        return latencies[k]

    return {
        "path": "block (s3-data-block)",
        "driver": "ThreadPoolExecutor + persistent http.client connection pool",
        "concurrency": concurrency,
        "n_total": n_total,
        "n_ok": ok_count,
        "n_tessera_errors": tessera_errors,
        "wall_clock_s": round(wall_elapsed_s, 3),
        "rps": round(rps, 1),
        "latency_ms": {
            "min": round(min(latencies), 3) if latencies else 0.0,
            "p50": round(pct(50), 3),
            "p95": round(pct(95), 3),
            "p99": round(pct(99), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
            "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
        },
    }


async def _fire_async(client: httpx.AsyncClient, body: dict[str, Any], idx: int, expect: str) -> tuple[float, str]:
    payload = {**body, "id": idx}
    t0 = time.perf_counter_ns()
    try:
        r = await client.post(
            f"http://{HOST}:{PORT}/mcp/aws", json=payload, headers=HEADERS, timeout=30.0
        )
        latency_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        text = r.text
        if "Throttling" in text or "RateLimit" in text or "TooManyRequests" in text:
            return (latency_ms, "aws_throttle")
        if expect == "allow" and r.status_code < 400 and '"result"' in text:
            return (latency_ms, "ok")
        return (latency_ms, "tessera_error")
    except Exception:
        return ((time.perf_counter_ns() - t0) / 1_000_000.0, "tessera_error")


async def run_live_aws_sequential() -> dict[str, Any]:
    n = 50
    ALLOW_BODY = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "call_aws",
            "arguments": {"cli_command": "aws sts get-caller-identity"},
        },
    }
    async with httpx.AsyncClient() as client:
        for _ in range(5):
            await _fire_async(client, ALLOW_BODY, idx=-1, expect="allow")
        t0 = time.perf_counter_ns()
        results = []
        for i in range(n):
            results.append(await _fire_async(client, ALLOW_BODY, idx=i + 1, expect="allow"))
        elapsed_s = (time.perf_counter_ns() - t0) / 1_000_000_000.0
    latencies = sorted([r[0] for r in results])
    ok = sum(1 for r in results if r[1] == "ok")
    throttle = sum(1 for r in results if r[1] == "aws_throttle")
    err = sum(1 for r in results if r[1] == "tessera_error")
    rps = n / elapsed_s if elapsed_s > 0 else 0.0

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = int(round((p / 100.0) * (len(latencies) - 1)))
        return latencies[k]

    return {
        "path": "allow (live AWS sts get-caller-identity)",
        "n": n,
        "n_ok": ok,
        "n_aws_throttle": throttle,
        "n_tessera_errors": err,
        "wall_clock_s": round(elapsed_s, 3),
        "rps": round(rps, 1),
        "latency_ms": {
            "min": round(latencies[0], 3) if latencies else 0.0,
            "p50": round(pct(50), 3),
            "p95": round(pct(95), 3),
            "p99": round(pct(99), 3),
            "max": round(latencies[-1], 3) if latencies else 0.0,
            "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
        },
    }


def main() -> int:
    mcp_proc, tess_proc = boot_stack()
    try:
        block = run_block_path()
        live = asyncio.run(run_live_aws_sequential())
    finally:
        kill_proc(tess_proc)
        kill_proc(mcp_proc)
    result = {
        "benchmark": "bench-04-concurrent-throughput",
        "tessera_version": "0.5.1",
        "aws_account": "237509402889",
        "aws_region": "us-east-1",
        "block_path_concurrent": block,
        "live_aws_sequential": live,
        "pass_criterion": "block_path RPS > 500",
        "pass": block["rps"] > 500.0,
        "notes": (
            "Concurrent throughput measured on the block path (s3-data-block) so "
            "the upstream session lock in awslabs.aws-api-mcp-server v1.3.36 is "
            "not on the hot path. Driver is ThreadPoolExecutor (32 workers) "
            "with a queue of persistent http.client.HTTPConnection objects "
            "so each request hits a kept-alive socket. Closer to wrk semantics "
            "than asyncio.gather's single-event-loop pool."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
