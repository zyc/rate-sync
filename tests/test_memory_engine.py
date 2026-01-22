"""Tests for in-memory rate limiting engine.

These tests verify the Memory engine implementation, including:
- Rate limiting (rate_per_second)
- Concurrency limiting (max_concurrent)
- Combined strategies
- Release behavior
- Metrics tracking
"""

import asyncio
import time

import pytest

from ratesync.core import RateLimiterMetrics
from ratesync.engines.memory import MemoryRateLimiter
from ratesync.exceptions import RateLimiterNotInitializedError
from ratesync.schemas import MemoryEngineConfig


class TestMemoryEngineConfiguration:
    """Test Memory engine configuration and validation."""

    def test_create_with_rate_per_second(self):
        """Test creating limiter with only rate_per_second."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=10.0)

        assert limiter.group_id == "test"
        assert limiter.rate_per_second == 10.0
        assert limiter.max_concurrent is None
        assert not limiter.is_initialized

    def test_create_with_max_concurrent(self):
        """Test creating limiter with only max_concurrent."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=5)

        assert limiter.group_id == "test"
        assert limiter.rate_per_second is None
        assert limiter.max_concurrent == 5
        assert not limiter.is_initialized

    def test_create_with_both_strategies(self):
        """Test creating limiter with both strategies."""
        limiter = MemoryRateLimiter(
            group_id="test",
            rate_per_second=10.0,
            max_concurrent=5,
            default_timeout=30.0,
        )

        assert limiter.rate_per_second == 10.0
        assert limiter.max_concurrent == 5
        assert limiter.default_timeout == 30.0

    def test_create_without_strategy_raises_error(self):
        """Test that creating without any strategy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test")

        expected_msg = (
            "At least one of (rate_per_second) or (limit+window_seconds) "
            "or max_concurrent must be specified"
        )
        assert expected_msg in str(exc_info.value)

    def test_negative_rate_per_second_raises_error(self):
        """Test that negative rate_per_second raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", rate_per_second=-1.0)

        assert "rate_per_second must be > 0" in str(exc_info.value)

    def test_zero_rate_per_second_raises_error(self):
        """Test that zero rate_per_second raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", rate_per_second=0.0)

        assert "rate_per_second must be > 0" in str(exc_info.value)

    def test_negative_max_concurrent_raises_error(self):
        """Test that negative max_concurrent raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", max_concurrent=-1)

        assert "max_concurrent must be > 0" in str(exc_info.value)

    def test_zero_max_concurrent_raises_error(self):
        """Test that zero max_concurrent raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", max_concurrent=0)

        assert "max_concurrent must be > 0" in str(exc_info.value)

    def test_from_config(self):
        """Test creating limiter from config."""
        config = MemoryEngineConfig()
        limiter = MemoryRateLimiter.from_config(
            config, group_id="test", rate_per_second=5.0, timeout=10.0, max_concurrent=3
        )

        assert limiter.group_id == "test"
        assert limiter.rate_per_second == 5.0
        assert limiter.max_concurrent == 3
        assert limiter.default_timeout == 10.0


@pytest.mark.asyncio
class TestMemoryEngineInitialization:
    """Test Memory engine initialization."""

    async def test_initialize_idempotent(self):
        """Test that initialize() is idempotent."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=1.0)

        await limiter.initialize()
        assert limiter.is_initialized

        await limiter.initialize()
        assert limiter.is_initialized

    async def test_acquire_before_initialize_fails(self):
        """Test that acquire before initialize raises error."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=1.0)

        with pytest.raises(RateLimiterNotInitializedError):
            await limiter.acquire()

    async def test_release_before_initialize_fails(self):
        """Test that release before initialize raises error."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=1)

        with pytest.raises(RateLimiterNotInitializedError):
            await limiter.release()


@pytest.mark.asyncio
class TestMemoryEngineRateLimiting:
    """Test Memory engine rate limiting (rate_per_second)."""

    async def test_acquire_enforces_rate_limit(self):
        """Test that acquire enforces rate limiting."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=10.0)  # 0.1s interval
        await limiter.initialize()

        # First acquire should be immediate
        start = time.time()
        await limiter.acquire()
        first_elapsed = time.time() - start

        # Second acquire should wait for interval
        start = time.time()
        await limiter.acquire()
        second_elapsed = time.time() - start

        assert first_elapsed < 0.05  # First should be fast
        assert second_elapsed >= 0.08  # Second should wait ~0.1s

    async def test_try_acquire_with_timeout(self):
        """Test try_acquire with timeout."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=2.0)  # 0.5s interval
        await limiter.initialize()

        # First acquire should succeed
        result = await limiter.try_acquire(timeout=1.0)
        assert result is True

        # Second acquire with short timeout should fail
        result = await limiter.try_acquire(timeout=0.1)
        assert result is False

    async def test_try_acquire_immediate_failure(self):
        """Test try_acquire with timeout=0."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=1.0)
        await limiter.initialize()

        # First acquire should succeed
        result = await limiter.try_acquire(timeout=0)
        assert result is True

        # Immediate second acquire should fail
        result = await limiter.try_acquire(timeout=0)
        assert result is False


@pytest.mark.asyncio
class TestMemoryEngineConcurrencyLimiting:
    """Test Memory engine concurrency limiting (max_concurrent)."""

    async def test_max_concurrent_enforces_limit(self):
        """Test that max_concurrent enforces concurrency limit."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=2)
        await limiter.initialize()

        acquired_count = 0
        blocked = False

        async def acquire_and_hold(duration: float):
            nonlocal acquired_count, blocked
            await limiter.acquire()
            acquired_count += 1
            await asyncio.sleep(duration)
            await limiter.release()

        async def try_third():
            nonlocal blocked
            await asyncio.sleep(0.05)  # Let first two acquire
            # Third should be blocked
            result = await limiter.try_acquire(timeout=0.1)
            blocked = not result

        await asyncio.gather(
            acquire_and_hold(0.3),
            acquire_and_hold(0.3),
            try_third(),
        )

        assert acquired_count == 2
        assert blocked is True

    async def test_release_allows_new_acquire(self):
        """Test that release() allows new acquires."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=1)
        await limiter.initialize()

        # Acquire and release
        await limiter.acquire()
        await limiter.release()

        # Should be able to acquire again
        result = await limiter.try_acquire(timeout=0.1)
        assert result is True

    async def test_release_without_acquire_is_safe(self):
        """Test that release without acquire doesn't break semaphore."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=1)
        await limiter.initialize()

        # Release without acquire (allowed by semaphore, but increases permits)
        await limiter.release()

        # Should be able to acquire twice now (permits increased)
        await limiter.acquire()
        result = await limiter.try_acquire(timeout=0.1)
        assert result is True

    async def test_release_with_no_max_concurrent_is_noop(self):
        """Test that release() is a no-op when max_concurrent is None."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=10.0)
        await limiter.initialize()

        # Acquire and release should not fail
        await limiter.acquire()
        await limiter.release()  # Should be a no-op

    async def test_concurrent_operations_serialize(self):
        """Test that concurrent operations are serialized up to limit."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=2)
        await limiter.initialize()

        operation_times: list[tuple[float, float]] = []

        async def timed_operation():
            await limiter.acquire()
            start = time.time()
            await asyncio.sleep(0.1)
            end = time.time()
            await limiter.release()
            operation_times.append((start, end))

        # Run 4 operations with max_concurrent=2
        await asyncio.gather(*[timed_operation() for _ in range(4)])

        # Check that at most 2 operations overlap at any time
        for i, (start_i, end_i) in enumerate(operation_times):
            overlapping = 0
            for j, (start_j, end_j) in enumerate(operation_times):
                if i != j:
                    # Check if operations overlap
                    if start_j < end_i and start_i < end_j:
                        overlapping += 1
            # Each operation should overlap with at most 1 other (max_concurrent=2)
            assert overlapping <= 1, f"Operation {i} overlapped with {overlapping} others"


@pytest.mark.asyncio
class TestMemoryEngineCombinedStrategies:
    """Test Memory engine with both rate and concurrency limiting."""

    async def test_both_strategies_enforced(self):
        """Test that both strategies are enforced together."""
        # 2 req/sec with max 2 concurrent
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=2.0, max_concurrent=2)
        await limiter.initialize()

        times: list[float] = []

        async def operation():
            await limiter.acquire()
            times.append(time.time())
            await asyncio.sleep(0.1)
            await limiter.release()

        start = time.time()
        await asyncio.gather(*[operation() for _ in range(4)])
        total_time = time.time() - start

        # 4 operations at 2 req/sec should take at least 1.5s (0.5s between each start)
        # But with max_concurrent=2, we can start 2 immediately
        # So we expect: t=0 start 2, t=0.5 start 1, t=1.0 start 1
        assert total_time >= 1.0  # At least 3 intervals of 0.5s minus concurrency benefit


@pytest.mark.asyncio
class TestMemoryEngineMetrics:
    """Test Memory engine metrics tracking."""

    async def test_metrics_tracking_rate_limiting(self):
        """Test that metrics are tracked for rate limiting."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=10.0)
        await limiter.initialize()

        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

        metrics = limiter.get_metrics()
        assert isinstance(metrics, RateLimiterMetrics)
        assert metrics.total_acquisitions == 3
        assert metrics.timeouts == 0

    async def test_metrics_tracking_timeout(self):
        """Test that timeout metrics are tracked."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=1.0)
        await limiter.initialize()

        await limiter.acquire()
        await limiter.try_acquire(timeout=0.01)  # Should timeout

        metrics = limiter.get_metrics()
        assert metrics.total_acquisitions == 1
        assert metrics.timeouts == 1

    async def test_metrics_tracking_concurrency(self):
        """Test that concurrency metrics are tracked."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=2)
        await limiter.initialize()

        # Acquire both slots
        await limiter.acquire()
        await limiter.acquire()

        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 2
        assert metrics.total_releases == 0

        # Release one
        await limiter.release()

        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 1
        assert metrics.total_releases == 1

    async def test_metrics_max_concurrent_reached(self):
        """Test that max_concurrent_reached is tracked."""
        limiter = MemoryRateLimiter(group_id="test", max_concurrent=1)
        await limiter.initialize()

        # Fill up the slot
        await limiter.acquire()

        # Try to acquire when full (should record max_concurrent_reached)
        result = await limiter.try_acquire(timeout=0.01)
        assert result is False

        metrics = limiter.get_metrics()
        assert metrics.max_concurrent_reached >= 1
