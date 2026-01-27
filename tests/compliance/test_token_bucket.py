"""Compliance tests for token bucket rate limiting (rate_per_second).

All engines supporting token bucket must enforce rate limits correctly.
"""

from __future__ import annotations

import time

import pytest

from compliance.utils import (
    EngineFactory,
    get_engines_with,
    get_unit_test_engines,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

# Only test token bucket on engines that support it and don't need infrastructure
TOKEN_BUCKET_UNIT_ENGINES = [
    e for e in get_engines_with("token_bucket") if e in get_unit_test_engines()
]


class TestTokenBucketRateLimiting:
    """Test token bucket algorithm compliance."""

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_first_acquire_is_immediate(
        self, engine_name: str, get_factory
    ) -> None:
        """First acquire should return immediately."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        start = time.perf_counter()
        await limiter.acquire()
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"First acquire took {elapsed:.3f}s, expected < 0.1s"

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_rate_is_enforced(self, engine_name: str, get_factory) -> None:
        """Subsequent acquires should respect rate limit."""
        factory: EngineFactory = get_factory(engine_name)
        rate = 10.0  # 10 per second = 0.1s interval
        limiter = await factory(rate_per_second=rate)

        # First acquire
        await limiter.acquire()

        # Second acquire should wait
        start = time.perf_counter()
        await limiter.acquire()
        elapsed = time.perf_counter() - start

        expected_interval = 1.0 / rate
        # Allow 20% tolerance
        assert elapsed >= expected_interval * 0.8, (
            f"Second acquire waited {elapsed:.3f}s, "
            f"expected >= {expected_interval * 0.8:.3f}s"
        )

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_try_acquire_immediate_success(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire(timeout=0) returns True if slot available."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        result = await limiter.try_acquire(timeout=0)
        assert result is True

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_try_acquire_immediate_failure(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire(timeout=0) returns False if no slot available."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=1.0)  # 1 per second

        # First should succeed
        result1 = await limiter.try_acquire(timeout=0)
        assert result1 is True

        # Second immediate should fail
        result2 = await limiter.try_acquire(timeout=0)
        assert result2 is False

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_try_acquire_with_timeout_success(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire waits up to timeout for slot."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)  # 0.1s interval

        await limiter.acquire()

        # Wait with sufficient timeout
        result = await limiter.try_acquire(timeout=0.5)
        assert result is True

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_try_acquire_with_timeout_failure(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire returns False when timeout exceeded."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=1.0)  # 1s interval

        await limiter.acquire()

        start = time.perf_counter()
        result = await limiter.try_acquire(timeout=0.1)
        elapsed = time.perf_counter() - start

        assert result is False
        assert 0.08 <= elapsed <= 0.25  # Respected timeout with tolerance

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_rate_per_second_property(
        self, engine_name: str, get_factory
    ) -> None:
        """rate_per_second property returns configured value."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=42.5)

        assert limiter.rate_per_second == 42.5

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_negative_rate_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Negative rate_per_second should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", rate_per_second=-1.0)

        assert "rate_per_second" in str(exc_info.value)

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_zero_rate_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Zero rate_per_second should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", rate_per_second=0.0)

        assert "rate_per_second" in str(exc_info.value)

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_algorithm_property_token_bucket(
        self, engine_name: str, get_factory
    ) -> None:
        """algorithm property returns 'token_bucket' for rate_per_second config."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        assert limiter.algorithm == "token_bucket"

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_algorithm_with_only_max_concurrent(
        self, engine_name: str, get_factory
    ) -> None:
        """algorithm defaults to 'token_bucket' when only max_concurrent is set."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=5)

        # When only max_concurrent is set, algorithm should default to token_bucket
        assert limiter.algorithm == "token_bucket"

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_rate_and_window_mutual_exclusion(
        self, engine_name: str, get_factory
    ) -> None:
        """Cannot specify both rate_per_second and sliding window params."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(
                group_id="test",
                rate_per_second=10.0,
                limit=100,
                window_seconds=60,
            )

        # Should mention the conflict
        assert "token_bucket" in str(exc_info.value).lower() or "sliding_window" in str(exc_info.value).lower()

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_rate_timing_accuracy(self, engine_name: str, get_factory) -> None:
        """Rate limiting should produce accurate timing intervals."""
        factory: EngineFactory = get_factory(engine_name)
        rate = 5.0  # 5 per second = 0.2s interval
        expected_interval = 1.0 / rate
        limiter = await factory(rate_per_second=rate)

        # Acquire multiple times and measure intervals
        intervals: list[float] = []
        for i in range(4):
            start = time.perf_counter()
            await limiter.acquire()
            if i > 0:  # Skip first acquire (instant)
                intervals.append(time.perf_counter() - start)

        # All intervals should be close to expected (within 30% tolerance)
        for i, interval in enumerate(intervals):
            assert interval >= expected_interval * 0.7, (
                f"Interval {i+1}: {interval:.3f}s < expected {expected_interval * 0.7:.3f}s"
            )
            assert interval <= expected_interval * 1.5, (
                f"Interval {i+1}: {interval:.3f}s > expected {expected_interval * 1.5:.3f}s"
            )

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_try_acquire_with_small_timeout_times_out(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire with timeout shorter than interval should timeout."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=2.0)  # 0.5s interval

        # First acquire succeeds
        result1 = await limiter.acquire()

        # Second with short timeout should fail
        start = time.perf_counter()
        result = await limiter.try_acquire(timeout=0.1)
        elapsed = time.perf_counter() - start

        assert result is False
        # Should have waited close to timeout
        assert 0.08 <= elapsed <= 0.2, f"Expected ~0.1s wait, got {elapsed:.3f}s"

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_very_high_rate_allows_rapid_acquires(
        self, engine_name: str, get_factory
    ) -> None:
        """Very high rate should allow rapid consecutive acquires."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=1000.0)  # 0.001s interval

        start = time.perf_counter()
        for _ in range(10):
            await limiter.acquire()
        elapsed = time.perf_counter() - start

        # Should complete quickly (< 0.1s for 10 acquires at 1000/s)
        assert elapsed < 0.2, f"10 acquires at 1000/s took {elapsed:.3f}s, expected < 0.2s"

    @pytest.mark.parametrize("engine_name", TOKEN_BUCKET_UNIT_ENGINES)
    async def test_fractional_rate_works(self, engine_name: str, get_factory) -> None:
        """Fractional rate_per_second values should work correctly."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=0.5)  # 2s interval

        assert limiter.rate_per_second == 0.5

        # First acquire should be instant
        start = time.perf_counter()
        await limiter.acquire()
        first_elapsed = time.perf_counter() - start
        assert first_elapsed < 0.1, "First acquire should be instant"

        # Second acquire should be blocked (we won't wait 2s, just check it's blocked)
        result = await limiter.try_acquire(timeout=0.1)
        assert result is False, "Second acquire should be blocked by slow rate"
