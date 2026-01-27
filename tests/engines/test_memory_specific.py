"""Memory engine specific tests.

Tests for behavior unique to the in-memory engine.
"""

from __future__ import annotations

import pytest

from ratesync.engines.memory import MemoryRateLimiter
from ratesync.schemas import MemoryEngineConfig


pytestmark = pytest.mark.asyncio


class TestMemoryEngineSpecific:
    """Tests for memory engine implementation details."""

    async def test_no_external_dependencies(self) -> None:
        """Memory engine works without any external services."""
        limiter = MemoryRateLimiter(
            group_id="test_standalone",
            rate_per_second=10.0,
        )

        await limiter.initialize()
        await limiter.acquire()
        await limiter.reset()

        # No exceptions = success

    async def test_from_config_with_memory_config(self) -> None:
        """Memory engine can be created from MemoryEngineConfig."""
        config = MemoryEngineConfig()

        limiter = MemoryRateLimiter.from_config(
            config,
            group_id="test_from_config",
            rate_per_second=10.0,
        )

        assert limiter.group_id == "test_from_config"
        assert limiter.rate_per_second == 10.0

    async def test_single_process_isolation(self) -> None:
        """Different group_ids have independent state."""
        limiter1 = MemoryRateLimiter(
            group_id="group_a",
            rate_per_second=1.0,
        )
        limiter2 = MemoryRateLimiter(
            group_id="group_b",
            rate_per_second=1.0,
        )

        await limiter1.initialize()
        await limiter2.initialize()

        # Exhaust limiter1
        await limiter1.acquire()
        result1 = await limiter1.try_acquire(timeout=0)
        assert result1 is False

        # limiter2 should still work
        result2 = await limiter2.try_acquire(timeout=0)
        assert result2 is True

    async def test_fail_closed_behavior(self) -> None:
        """Memory engine respects fail_closed setting."""
        limiter = MemoryRateLimiter(
            group_id="test_fail_closed",
            rate_per_second=10.0,
            fail_closed=True,
        )

        await limiter.initialize()

        # fail_closed doesn't affect memory engine (no backend failure possible)
        # but the property should be set
        assert limiter.fail_closed is True

    async def test_default_timeout_used(self) -> None:
        """Default timeout is used when not specified in acquire."""
        limiter = MemoryRateLimiter(
            group_id="test_default_timeout",
            rate_per_second=1.0,
            default_timeout=0.05,  # 50ms
        )

        await limiter.initialize()
        await limiter.acquire()

        # try_acquire without explicit timeout should use default
        # Since rate is 1/s and we just acquired, this should timeout
        result = await limiter.try_acquire()
        assert result is False

    async def test_get_state_returns_current_state(self) -> None:
        """Memory engine supports get_state()."""
        limiter = MemoryRateLimiter(
            group_id="test_state",
            limit=5,
            window_seconds=60,
        )

        await limiter.initialize()

        state = await limiter.get_state()
        assert state.allowed is True
        assert state.remaining == 5

        # Use some quota
        await limiter.acquire()
        await limiter.acquire()

        state = await limiter.get_state()
        assert state.remaining == 3

    async def test_reset_is_reusable(self) -> None:
        """Memory engine can be reset and reused."""
        limiter = MemoryRateLimiter(
            group_id="test_reset",
            rate_per_second=10.0,
        )

        await limiter.initialize()
        await limiter.acquire()
        await limiter.reset()

        # Should be able to acquire immediately after reset
        await limiter.initialize()
        result = await limiter.try_acquire(timeout=0)
        assert result is True

    async def test_combined_rate_and_concurrency(self) -> None:
        """Memory engine supports combined rate + concurrency limiting."""
        limiter = MemoryRateLimiter(
            group_id="test_combined",
            rate_per_second=100.0,  # High rate to not interfere with concurrency testing
            max_concurrent=2,
        )

        await limiter.initialize()

        # First acquire should work
        await limiter.acquire()
        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 1

        # Second acquire should work
        await limiter.acquire()
        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 2

        # Third should block on concurrency (not rate)
        result = await limiter.try_acquire(timeout=0)
        assert result is False

        # Release one slot
        await limiter.release()
        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 1

        # Now should be able to acquire again (with timeout for rate limiting)
        result = await limiter.try_acquire(timeout=0.5)
        assert result is True

        # Cleanup
        await limiter.release()
        await limiter.release()

    async def test_metrics_track_max_concurrent_reached(self) -> None:
        """Memory engine tracks when max_concurrent limit is reached."""
        limiter = MemoryRateLimiter(
            group_id="test_metrics_concurrent",
            max_concurrent=1,
        )

        await limiter.initialize()

        # Fill the slot
        await limiter.acquire()

        # Try to acquire when full
        result = await limiter.try_acquire(timeout=0)
        assert result is False

        metrics = limiter.get_metrics()
        assert metrics.max_concurrent_reached >= 1

        await limiter.release()

    async def test_reset_all_same_as_reset(self) -> None:
        """Memory engine reset_all() behaves same as reset() for isolated state."""
        limiter = MemoryRateLimiter(
            group_id="test_reset_all",
            rate_per_second=10.0,
        )

        await limiter.initialize()
        await limiter.acquire()

        # reset_all should work the same as reset for memory engine
        await limiter.reset_all()

        # Should be able to acquire immediately
        result = await limiter.try_acquire(timeout=0)
        assert result is True

    async def test_algorithm_property_with_max_concurrent_only(self) -> None:
        """Algorithm defaults to token_bucket when only max_concurrent is set."""
        limiter = MemoryRateLimiter(
            group_id="test_algo_concurrent",
            max_concurrent=5,
        )

        assert limiter.algorithm == "token_bucket"

    async def test_sliding_window_state_accuracy(self) -> None:
        """Memory engine get_state() accurately reflects sliding window state."""
        limiter = MemoryRateLimiter(
            group_id="test_sw_state",
            limit=3,
            window_seconds=60,
        )

        await limiter.initialize()

        # Initial state
        state = await limiter.get_state()
        assert state.allowed is True
        assert state.remaining == 3
        assert state.current_usage == 0

        # Use one slot
        await limiter.acquire()

        state = await limiter.get_state()
        assert state.allowed is True
        assert state.remaining == 2
        assert state.current_usage == 1

        # Use all slots
        await limiter.acquire()
        await limiter.acquire()

        state = await limiter.get_state()
        assert state.allowed is False
        assert state.remaining == 0
        assert state.current_usage == 3

    async def test_token_bucket_state_accuracy(self) -> None:
        """Memory engine get_state() works with token bucket algorithm."""
        limiter = MemoryRateLimiter(
            group_id="test_tb_state",
            rate_per_second=10.0,
        )

        await limiter.initialize()

        # Initial state
        state = await limiter.get_state()
        assert state.allowed is True

        # After one acquire
        await limiter.acquire()

        state = await limiter.get_state()
        # State should still be mostly permissive for token bucket
        # as it doesn't have a fixed limit like sliding window
        assert isinstance(state.reset_at, int)

    async def test_from_config_with_sliding_window(self) -> None:
        """Memory engine from_config() works with sliding window parameters."""
        config = MemoryEngineConfig()

        limiter = MemoryRateLimiter.from_config(
            config,
            group_id="test_from_config_sw",
            limit=100,
            window_seconds=60,
        )

        assert limiter.group_id == "test_from_config_sw"
        assert limiter.limit == 100
        assert limiter.window_seconds == 60
        assert limiter.algorithm == "sliding_window"

    async def test_get_config_returns_all_fields(self) -> None:
        """Memory engine get_config() returns all configuration fields."""
        limiter = MemoryRateLimiter(
            group_id="test_config",
            limit=50,
            window_seconds=30,
            max_concurrent=10,
            default_timeout=5.0,
            fail_closed=True,
        )

        config = limiter.get_config()

        assert config.id == "test_config"
        assert config.algorithm == "sliding_window"
        assert config.store_id == "memory"
        assert config.limit == 50
        assert config.window_seconds == 30
        assert config.max_concurrent == 10
        assert config.timeout == 5.0
        assert config.fail_closed is True
