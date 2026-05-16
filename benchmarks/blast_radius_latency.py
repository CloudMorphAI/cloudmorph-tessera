"""Compare blast-radius latency: with prefetch vs without.

The async prefetch (P0-14) moves the blast-radius compute off the event loop.
This benchmark quantifies the win: under concurrent load, prefetch-on should be
~2-3x faster for blast-radius-gated policies.
"""
import time


class _StubBlastRadius:
    """Synthetic blast-radius backend with controllable latency."""

    def __init__(self, simulated_latency_ms: float = 100):
        self.simulated_latency_ms = simulated_latency_ms

    def compute(self, tool_name: str, args: dict) -> int:  # noqa: ARG002
        # Simulate IAM API round-trip
        time.sleep(self.simulated_latency_ms / 1000)
        return 50  # static principal count


async def _run_n_parallel_requests(n: int, prefetch: bool) -> tuple[float, float]:  # noqa: ARG001
    """Return (mean_latency_ms, total_wall_ms).

    Build minimal engine + ctx with blast_radius condition.
    If prefetch=True: pre-populate context["blast_radius_cache"][tool_name] = count.
    If prefetch=False: condition evaluator does sync compute() in async context.
    Measures timing for both paths.
    """
    return 0.0, 0.0  # scaffold — implementation fills in timing


def main() -> None:
    print("=== Blast-radius latency: prefetch ON vs OFF ===")
    _backend = _StubBlastRadius(simulated_latency_ms=100)
    # ... run 20 sequential requests, both modes
    # Report mean + total wall


if __name__ == "__main__":
    main()
