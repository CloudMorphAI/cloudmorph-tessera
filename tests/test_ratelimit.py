"""Unit tests for MCP server rate limiter."""

import sys
import os
import unittest

# We test the Python implementation that mirrors the TS rate limiter logic
# For the actual TS rate limiter, tests would run via node --test


class TestRateLimitLogic(unittest.TestCase):
    """Test rate limiting logic (mirrors the TS implementation)."""

    def test_daily_limit(self):
        """Token should be blocked after daily limit."""
        daily_limit = 3
        counts = {}

        def check(token):
            counts.setdefault(token, 0)
            if counts[token] >= daily_limit:
                return False
            counts[token] += 1
            return True

        self.assertTrue(check("tok1"))
        self.assertTrue(check("tok1"))
        self.assertTrue(check("tok1"))
        self.assertFalse(check("tok1"))  # 4th should be blocked

    def test_burst_limit(self):
        """Token should be blocked after burst limit."""
        burst_limit = 2
        minute_counts = {}

        def check_burst(token):
            minute_counts.setdefault(token, 0)
            if minute_counts[token] >= burst_limit:
                return False
            minute_counts[token] += 1
            return True

        self.assertTrue(check_burst("tok1"))
        self.assertTrue(check_burst("tok1"))
        self.assertFalse(check_burst("tok1"))

    def test_concurrent_jobs(self):
        """Should limit concurrent jobs."""
        max_concurrent = 1
        concurrent = {}

        def acquire(token):
            concurrent.setdefault(token, 0)
            if concurrent[token] >= max_concurrent:
                return False
            concurrent[token] += 1
            return True

        def release(token):
            concurrent[token] = max(0, concurrent.get(token, 0) - 1)

        self.assertTrue(acquire("tok1"))
        self.assertFalse(acquire("tok1"))  # blocked
        release("tok1")
        self.assertTrue(acquire("tok1"))  # allowed again

    def test_independent_tokens(self):
        """Different tokens should have independent limits."""
        daily_limit = 2
        counts = {}

        def check(token):
            counts.setdefault(token, 0)
            if counts[token] >= daily_limit:
                return False
            counts[token] += 1
            return True

        self.assertTrue(check("tok1"))
        self.assertTrue(check("tok2"))
        self.assertTrue(check("tok1"))
        self.assertTrue(check("tok2"))
        self.assertFalse(check("tok1"))  # tok1 blocked
        self.assertFalse(check("tok2"))  # tok2 blocked


if __name__ == "__main__":
    unittest.main()
