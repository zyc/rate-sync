"""Compliance tests for combined rate limiting strategies.

Tests combining rate limiting with concurrency limiting.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from compliance.utils import (
    EngineFactory,
    get_unit_test_engines,
    ENGINE_CAPABILITIES,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

# Engines that support both token_bucket and concurrency
COMBINED_RATE_CONCURRENCY_ENGINES = [
    e
    for e in get_unit_test_engines()
    if ENGINE_CAPABILITIES[e].supports_token_bucket and ENGINE_CAPABILITIES[e].supports_concurrency
]

# Engines that support both sliding_window and concurrency
COMBINED_WINDOW_CONCURRENCY_ENGINES = [
    e
    for e in get_unit_test_engines()
    if ENGINE_CAPABILITIES[e].supports_sliding_window
    and ENGINE_CAPABILITIES[e].supports_concurrency
]


class TestCombinedStrategies:
    """Test combined rate + concurrency limiting."""

    @pytest.mark.parametrize("engine_name", COMBINED_RATE_CONCURRENCY_ENGINES)
    async def test_rate_plus_concurrency_both_enforced(self, engine_name: str, get_factory) -> None:
        """Both rate and concurrency limits should be enforced."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(
            rate_per_second=10.0,  # 0.1s interval
            max_concurrent=1,
        )

        # Acquire and hold
        await limiter.acquire()

        # Try to acquire again - should fail (concurrency limit)
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Concurrency limit should block"

        # Release
        await limiter.release()

        # Now should be blocked by rate limit
        # (need to wait 0.1s since last acquire)
        start = time.perf_counter()
        await limiter.acquire()
        elapsed = time.perf_counter() - start

        # Should have waited for rate limit
        assert elapsed >= 0.05, f"Rate limit should have caused wait, got {elapsed:.3f}s"

        await limiter.release()

    @pytest.mark.parametrize("engine_name", COMBINED_RATE_CONCURRENCY_ENGINES)
    async def test_rate_plus_concurrency_context_manager(
        self, engine_name: str, get_factory
    ) -> None:
        """Context manager works with combined strategies."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(
            rate_per_second=1000.0,  # Very fast rate to avoid rate limiting interference
            max_concurrent=2,
        )

        async with limiter.acquire_context():
            async with limiter.acquire_context():
                # Both slots acquired
                result = await limiter.try_acquire(timeout=0)
                assert result is False, "Should be at concurrency limit"

        # Slots released - with small timeout to allow token regeneration
        result = await limiter.try_acquire(timeout=0.1)
        assert result is True, "Should acquire after context managers exit"

    @pytest.mark.parametrize("engine_name", COMBINED_WINDOW_CONCURRENCY_ENGINES)
    async def test_window_plus_concurrency_both_enforced(
        self, engine_name: str, get_factory
    ) -> None:
        """Both window and concurrency limits should be enforced."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(
            limit=5,
            window_seconds=60,
            max_concurrent=2,
        )

        # Fill concurrency limit
        await limiter.acquire()
        await limiter.acquire()

        # Concurrency limit should block
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Concurrency limit should block"

        # Release one
        await limiter.release()

        # Now can acquire (still within window limit)
        result = await limiter.try_acquire(timeout=0)
        assert result is True

        await limiter.release()
        await limiter.release()

    @pytest.mark.parametrize("engine_name", COMBINED_WINDOW_CONCURRENCY_ENGINES)
    async def test_window_exhausted_concurrency_available(
        self, engine_name: str, get_factory
    ) -> None:
        """Window limit blocks even when concurrency is available."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(
            limit=2,
            window_seconds=60,
            max_concurrent=5,  # More concurrency than window limit
        )

        # Use up window limit
        await limiter.acquire()
        await limiter.release()
        await limiter.acquire()
        await limiter.release()

        # Window exhausted, should fail
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Window limit should block"

    @pytest.mark.parametrize("engine_name", COMBINED_RATE_CONCURRENCY_ENGINES)
    async def test_parallel_operations_respect_both_limits(
        self, engine_name: str, get_factory
    ) -> None:
        """Parallel operations respect both rate and concurrency."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(
            rate_per_second=100.0,  # Fast rate
            max_concurrent=3,
        )

        active_count = 0
        max_active = 0
        lock = asyncio.Lock()

        async def task() -> None:
            nonlocal active_count, max_active
            async with limiter.acquire_context():
                async with lock:
                    active_count += 1
                    max_active = max(max_active, active_count)
                await asyncio.sleep(0.05)
                async with lock:
                    active_count -= 1

        # Run 10 tasks
        await asyncio.gather(*[task() for _ in range(10)])

        # Max active should never exceed max_concurrent
        assert max_active <= 3, f"Max active was {max_active}, expected <= 3"
