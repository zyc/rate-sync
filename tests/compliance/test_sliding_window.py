"""Compliance tests for sliding window rate limiting (limit + window_seconds).

All engines supporting sliding window must enforce quotas correctly.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from compliance.utils import (
    EngineFactory,
    get_engines_with,
    get_unit_test_engines,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

# Only test sliding window on engines that support it and don't need infrastructure
SLIDING_WINDOW_UNIT_ENGINES = [
    e for e in get_engines_with("sliding_window") if e in get_unit_test_engines()
]


class TestSlidingWindowRateLimiting:
    """Test sliding window algorithm compliance."""

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_allows_up_to_limit(self, engine_name: str, get_factory) -> None:
        """Should allow exactly 'limit' requests in window."""
        factory: EngineFactory = get_factory(engine_name)
        limit = 5
        window = 60
        limiter = await factory(limit=limit, window_seconds=window)

        # All 5 should succeed
        for i in range(limit):
            result = await limiter.try_acquire(timeout=0)
            assert result is True, f"Request {i+1} should succeed"

        # 6th should fail
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Request beyond limit should fail"

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_window_resets_after_expiry(
        self, engine_name: str, get_factory
    ) -> None:
        """After window expires, new requests should be allowed."""
        factory: EngineFactory = get_factory(engine_name)
        limit = 3
        window = 1  # 1 second window for fast testing
        limiter = await factory(limit=limit, window_seconds=window)

        # Fill the limit
        for _ in range(limit):
            await limiter.try_acquire(timeout=0)

        # Verify limit reached
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Should be limited"

        # Wait for window to expire
        await asyncio.sleep(1.1)

        # Should be allowed again
        result = await limiter.try_acquire(timeout=0)
        assert result is True, "Should succeed after window expires"

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_acquire_blocks_until_slot_available(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire() blocks when limit reached until slot opens."""
        factory: EngineFactory = get_factory(engine_name)
        limit = 2
        window = 1  # 1 second window
        limiter = await factory(limit=limit, window_seconds=window)

        # Fill limit
        for _ in range(limit):
            await limiter.try_acquire(timeout=0)

        # Third acquire should block until window expires
        start = time.perf_counter()
        await limiter.acquire()
        elapsed = time.perf_counter() - start

        assert elapsed >= 0.8, f"acquire() should have blocked ~1s, took {elapsed:.2f}s"

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_limit_property(self, engine_name: str, get_factory) -> None:
        """limit property returns configured value."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=42, window_seconds=60)

        assert limiter.limit == 42

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_window_seconds_property(
        self, engine_name: str, get_factory
    ) -> None:
        """window_seconds property returns configured value."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=10, window_seconds=300)

        assert limiter.window_seconds == 300

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_algorithm_property(self, engine_name: str, get_factory) -> None:
        """algorithm property returns 'sliding_window'."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=10, window_seconds=60)

        assert limiter.algorithm == "sliding_window"

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_limit_without_window_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """limit without window_seconds should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", limit=10)

        assert "window_seconds" in str(exc_info.value).lower() or "limit" in str(
            exc_info.value
        ).lower()

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_window_without_limit_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """window_seconds without limit should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", window_seconds=60)

        assert "window_seconds" in str(exc_info.value).lower() or "limit" in str(
            exc_info.value
        ).lower()

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_zero_limit_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Zero limit should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError):
            MemoryRateLimiter(group_id="test", limit=0, window_seconds=60)

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_negative_limit_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Negative limit should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError):
            MemoryRateLimiter(group_id="test", limit=-1, window_seconds=60)

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_zero_window_seconds_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Zero window_seconds should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError):
            MemoryRateLimiter(group_id="test", limit=10, window_seconds=0)

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_negative_window_seconds_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Negative window_seconds should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError):
            MemoryRateLimiter(group_id="test", limit=10, window_seconds=-1)

    @pytest.mark.parametrize("engine_name", SLIDING_WINDOW_UNIT_ENGINES)
    async def test_try_acquire_with_timeout_waits_for_slot(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire with sufficient timeout waits for slot to become available."""
        factory: EngineFactory = get_factory(engine_name)
        limit = 2
        window = 1  # 1 second window
        limiter = await factory(limit=limit, window_seconds=window)

        # Fill limit
        await limiter.try_acquire(timeout=0)
        await limiter.try_acquire(timeout=0)

        # Third acquire with timeout should wait for window to expire
        start = time.perf_counter()
        result = await limiter.try_acquire(timeout=1.5)
        elapsed = time.perf_counter() - start

        assert result is True, "Should succeed after waiting for window"
        assert elapsed >= 0.8, f"Should have waited ~1s, waited {elapsed:.3f}s"
