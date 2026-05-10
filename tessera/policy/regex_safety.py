"""Load-time corpus test for ReDoS prevention."""

from __future__ import annotations

import time

import regex

from tessera.errors import PolicyError

# Five synthetic corpus strings of increasing lengths (10, 100, ~1000, ~10000, ~100000)
_CORPUS = [
    "a" * 10,
    "ab" * 50,
    "abc123_-." * 111,          # ~999 chars
    "xyzABC123!@#" * 833,       # ~9996 chars
    "hello world test " * 5882 + "X",  # ~99995 chars
]


def validate_pattern(pattern: str) -> None:
    """Compile and run pattern against corpus strings.

    Raises PolicyError(reason="regex_potential_redos") if any corpus string
    takes >= 50ms to match.
    Raises PolicyError(reason="regex_invalid") if the pattern is syntactically
    invalid.
    """
    try:
        compiled = regex.compile(pattern, regex.VERSION1)
    except regex.error as e:
        raise PolicyError(
            f"invalid regex pattern {pattern!r}: {e}",
            reason="regex_invalid",
        ) from e

    for s in _CORPUS:
        start = time.perf_counter()
        try:
            compiled.search(s, timeout=0.1)  # 100ms hard timeout
        except (TimeoutError, regex.error) as e:
            if isinstance(e, TimeoutError) or "timeout" in str(e).lower():
                raise PolicyError(
                    f"pattern {pattern!r} timed out on corpus string of length {len(s)}",
                    reason="regex_potential_redos",
                ) from e
            raise  # re-raise non-timeout regex.error
        elapsed = time.perf_counter() - start
        if elapsed >= 0.05:  # 50ms soft cap
            raise PolicyError(
                f"pattern {pattern!r} took {elapsed * 1000:.0f}ms on corpus string "
                f"of length {len(s)} (limit 50ms)",
                reason="regex_potential_redos",
            )
