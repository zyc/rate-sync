"""Compliance tests for concurrency limiting (max_concurrent).

All engines supporting concurrency must correctly limit parallel operations.
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

# Only test concurrency on engines that support it and don't need infrastructure
CONCURRENCY_UNIT_ENGINES = [
    e for e in get_engines_with("concurrency") if e in get_unit_test_engines()
]


class TestConcurrencyLimiting:
    """Test concurrency limiting compliance."""

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_enforces_max_concurrent(
        self, engine_name: str, get_factory
    ) -> None:
        """Only max_concurrent operations should run simultaneously."""
        factory: EngineFactory = get_factory(engine_name)
        max_concurrent = 2
        limiter = await factory(max_concurrent=max_concurrent)

        acquired_count = 0
        blocked = False

        async def acquire_and_hold(duration: float) -> None:
            nonlocal acquired_count
            await limiter.acquire()
            acquired_count += 1
            await asyncio.sleep(duration)
            await limiter.release()

        async def try_third() -> None:
            nonlocal blocked
            await asyncio.sleep(0.05)  # Let first two acquire
            result = await limiter.try_acquire(timeout=0.05)
            blocked = not result

        await asyncio.gather(
            acquire_and_hold(0.3),
            acquire_and_hold(0.3),
            try_third(),
        )

        assert acquired_count == 2
        assert blocked is True, "Third should have been blocked"

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_release_allows_new_acquire(
        self, engine_name: str, get_factory
    ) -> None:
        """release() should free slot for new acquires."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        await limiter.acquire()
        await limiter.release()

        # Should succeed immediately
        result = await limiter.try_acquire(timeout=0)
        assert result is True

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_acquire_context_auto_releases(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire_context() should auto-release on exit."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        async with limiter.acquire_context():
            pass

        # Slot should be released
        result = await limiter.try_acquire(timeout=0)
        assert result is True

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_acquire_context_releases_on_exception(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire_context() releases slot even on exception."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        with pytest.raises(ValueError):
            async with limiter.acquire_context():
                raise ValueError("test error")

        # Slot should still be released
        result = await limiter.try_acquire(timeout=0)
        assert result is True

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_max_concurrent_property(
        self, engine_name: str, get_factory
    ) -> None:
        """max_concurrent property returns configured value."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=7)

        assert limiter.max_concurrent == 7

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_concurrent_acquire_wait(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire() blocks until slot available when at limit."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        await limiter.acquire()

        async def delayed_release():
            await asyncio.sleep(0.2)
            await limiter.release()

        # Start release task and track it for cleanup
        release_task = asyncio.create_task(delayed_release())

        try:
            # This should block until release
            start = time.perf_counter()
            await limiter.acquire()
            elapsed = time.perf_counter() - start

            assert elapsed >= 0.15, f"Should have waited ~0.2s, waited {elapsed:.3f}s"

            await limiter.release()
        finally:
            # Ensure task completes to avoid warnings
            if not release_task.done():
                await release_task

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_negative_max_concurrent_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Negative max_concurrent should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", max_concurrent=-1)

        assert "max_concurrent" in str(exc_info.value)

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_zero_max_concurrent_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """Zero max_concurrent should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test", max_concurrent=0)

        assert "max_concurrent" in str(exc_info.value)

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_multiple_sequential_acquires_and_releases(
        self, engine_name: str, get_factory
    ) -> None:
        """Multiple acquire/release cycles work correctly."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=2)

        for _ in range(5):
            await limiter.acquire()
            await limiter.acquire()
            await limiter.release()
            await limiter.release()

        # Should still work
        result = await limiter.try_acquire(timeout=0)
        assert result is True
        await limiter.release()

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_acquire_context_with_timeout_success(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire_context with timeout succeeds when slot available."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=2)

        async with limiter.acquire_context(timeout=1.0):
            # Should acquire successfully
            metrics = limiter.get_metrics()
            assert metrics.current_concurrent == 1

        # Slot should be released
        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 0

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_acquire_context_with_timeout_failure(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire_context with timeout raises error when slot unavailable."""
        from ratesync.exceptions import RateLimiterAcquisitionError

        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        # Hold the only slot
        await limiter.acquire()

        try:
            # Try to acquire with short timeout - should fail
            with pytest.raises(RateLimiterAcquisitionError):
                async with limiter.acquire_context(timeout=0.1):
                    pass
        finally:
            await limiter.release()

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_release_without_acquire_is_safe(
        self, engine_name: str, get_factory
    ) -> None:
        """release() without acquire should not cause issues (may over-release)."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=2)

        # This tests implementation resilience - release without acquire
        # Behavior may vary: some implementations allow it, others may raise
        # The important thing is it doesn't crash or leave limiter in bad state
        try:
            await limiter.release()
        except Exception:
            pass  # Some implementations may raise, that's acceptable

        # Limiter should still be usable
        result = await limiter.try_acquire(timeout=0)
        assert result is True
        await limiter.release()

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_try_acquire_timeout_zero_returns_immediately(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire(timeout=0) returns immediately when blocked."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        await limiter.acquire()

        start = time.perf_counter()
        result = await limiter.try_acquire(timeout=0)
        elapsed = time.perf_counter() - start

        assert result is False
        assert elapsed < 0.1, f"Should return immediately, took {elapsed:.3f}s"

        await limiter.release()

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_release_no_op_without_max_concurrent(
        self, engine_name: str, get_factory
    ) -> None:
        """release() is no-op when max_concurrent is not set."""
        factory: EngineFactory = get_factory(engine_name)
        # Create limiter with only rate limiting, no concurrency
        limiter = await factory(rate_per_second=1000.0)  # Very fast rate

        await limiter.acquire()

        # release() should be no-op (doesn't crash or change state)
        await limiter.release()
        await limiter.release()
        await limiter.release()

        # Should still work normally (wait a bit for rate slot)
        result = await limiter.try_acquire(timeout=0.1)
        assert result is True

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_concurrent_try_acquire_race_condition(
        self, engine_name: str, get_factory
    ) -> None:
        """Multiple concurrent try_acquire calls should correctly limit concurrency."""
        factory: EngineFactory = get_factory(engine_name)
        max_concurrent = 3
        limiter = await factory(max_concurrent=max_concurrent)

        success_count = 0
        lock = asyncio.Lock()

        async def try_acquire_task() -> bool:
            nonlocal success_count
            result = await limiter.try_acquire(timeout=0)
            if result:
                async with lock:
                    success_count += 1
            return result

        # Launch many concurrent try_acquire calls
        tasks = [try_acquire_task() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # Exactly max_concurrent should succeed
        assert sum(results) == max_concurrent
        assert success_count == max_concurrent

        # Cleanup
        for _ in range(max_concurrent):
            await limiter.release()

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_acquire_context_blocking_without_timeout(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire_context() without timeout blocks until slot available."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        acquired_order: list[int] = []
        lock = asyncio.Lock()

        async def task(task_id: int, delay_before_release: float = 0) -> None:
            async with limiter.acquire_context():
                async with lock:
                    acquired_order.append(task_id)
                if delay_before_release > 0:
                    await asyncio.sleep(delay_before_release)

        # Start first task that holds slot for 0.2s
        task1 = asyncio.create_task(task(1, delay_before_release=0.2))

        # Give task1 time to acquire
        await asyncio.sleep(0.05)

        # Start second task - should block until task1 releases
        task2 = asyncio.create_task(task(2))

        # Wait for both to complete
        await asyncio.gather(task1, task2)

        # Task 1 should have acquired first
        assert acquired_order == [1, 2]

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_high_concurrency_stress(
        self, engine_name: str, get_factory
    ) -> None:
        """Stress test with many concurrent operations."""
        factory: EngineFactory = get_factory(engine_name)
        max_concurrent = 5
        limiter = await factory(max_concurrent=max_concurrent)

        active_count = 0
        max_active_seen = 0
        violations = 0
        lock = asyncio.Lock()

        async def task() -> None:
            nonlocal active_count, max_active_seen, violations
            async with limiter.acquire_context():
                async with lock:
                    active_count += 1
                    max_active_seen = max(max_active_seen, active_count)
                    if active_count > max_concurrent:
                        violations += 1

                await asyncio.sleep(0.01)  # Simulate work

                async with lock:
                    active_count -= 1

        # Run many tasks
        await asyncio.gather(*[task() for _ in range(50)])

        assert violations == 0, f"Concurrency limit violated {violations} times"
        assert max_active_seen <= max_concurrent, (
            f"Max active {max_active_seen} exceeded limit {max_concurrent}"
        )
