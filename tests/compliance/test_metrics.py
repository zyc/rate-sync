"""Compliance tests for metrics tracking (get_metrics).

All engines must track basic metrics correctly.
"""

from __future__ import annotations

import pytest

from ratesync.core import RateLimiterMetrics
from compliance.utils import (
    EngineFactory,
    get_unit_test_engines,
    get_engines_with,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

UNIT_ENGINES = get_unit_test_engines()
CONCURRENCY_UNIT_ENGINES = [
    e for e in get_engines_with("concurrency") if e in get_unit_test_engines()
]


class TestMetricsTracking:
    """Test metrics compliance."""

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_get_metrics_returns_correct_type(
        self, engine_name: str, get_factory
    ) -> None:
        """get_metrics() returns RateLimiterMetrics."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        metrics = limiter.get_metrics()
        assert isinstance(metrics, RateLimiterMetrics)

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_initial_metrics_are_zero(
        self, engine_name: str, get_factory
    ) -> None:
        """Initial metrics should be zero/empty."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        metrics = limiter.get_metrics()
        assert metrics.total_acquisitions == 0
        assert metrics.timeouts == 0

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_total_acquisitions_increments(
        self, engine_name: str, get_factory
    ) -> None:
        """total_acquisitions should increment on each acquire."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=100.0)

        for i in range(3):
            await limiter.acquire()
            metrics = limiter.get_metrics()
            assert metrics.total_acquisitions == i + 1

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_timeouts_tracked(self, engine_name: str, get_factory) -> None:
        """timeouts should increment on try_acquire timeout."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=1.0)

        await limiter.acquire()
        await limiter.try_acquire(timeout=0.01)  # Should timeout

        metrics = limiter.get_metrics()
        assert metrics.timeouts >= 1

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_current_concurrent_tracked(
        self, engine_name: str, get_factory
    ) -> None:
        """current_concurrent should track active operations."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=5)

        await limiter.acquire()
        await limiter.acquire()

        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 2

        await limiter.release()

        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 1

        await limiter.release()

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_total_releases_tracked(
        self, engine_name: str, get_factory
    ) -> None:
        """total_releases should increment on each release."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=5)

        for i in range(3):
            await limiter.acquire()
            await limiter.release()

            metrics = limiter.get_metrics()
            assert metrics.total_releases == i + 1

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_last_acquisition_at_updated(
        self, engine_name: str, get_factory
    ) -> None:
        """last_acquisition_at should update on acquire."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=100.0)

        metrics_before = limiter.get_metrics()
        assert metrics_before.last_acquisition_at is None

        await limiter.acquire()

        metrics_after = limiter.get_metrics()
        assert metrics_after.last_acquisition_at is not None
        assert metrics_after.last_acquisition_at > 0

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_metrics_are_independent_per_limiter(
        self, engine_name: str, get_factory
    ) -> None:
        """Each limiter should have independent metrics."""
        factory: EngineFactory = get_factory(engine_name)
        limiter1 = await factory(rate_per_second=100.0)
        limiter2 = await factory(rate_per_second=100.0)

        await limiter1.acquire()
        await limiter1.acquire()
        await limiter2.acquire()

        metrics1 = limiter1.get_metrics()
        metrics2 = limiter2.get_metrics()

        assert metrics1.total_acquisitions == 2
        assert metrics2.total_acquisitions == 1

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_avg_wait_time_calculated_correctly(
        self, engine_name: str, get_factory
    ) -> None:
        """avg_wait_time_ms should be total_wait_time / total_acquisitions."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)  # 0.1s interval

        # First acquire is instant
        await limiter.acquire()
        # Second acquire waits ~0.1s
        await limiter.acquire()

        metrics = limiter.get_metrics()
        assert metrics.total_acquisitions == 2
        # avg should be calculated correctly (not zero for second acquire)
        expected_avg = metrics.total_wait_time_ms / metrics.total_acquisitions
        assert abs(metrics.avg_wait_time_ms - expected_avg) < 0.01

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_max_wait_time_tracked(self, engine_name: str, get_factory) -> None:
        """max_wait_time_ms should track the longest wait."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)  # 0.1s interval

        # Several acquisitions - max should be >= 0
        await limiter.acquire()  # instant
        await limiter.acquire()  # wait ~0.1s
        await limiter.acquire()  # wait ~0.1s

        metrics = limiter.get_metrics()
        assert metrics.max_wait_time_ms >= 0
        # max should be >= avg
        assert metrics.max_wait_time_ms >= metrics.avg_wait_time_ms - 1  # 1ms tolerance

    @pytest.mark.parametrize("engine_name", CONCURRENCY_UNIT_ENGINES)
    async def test_max_concurrent_reached_tracked(
        self, engine_name: str, get_factory
    ) -> None:
        """max_concurrent_reached should increment when limit is hit."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        # Acquire the only slot
        await limiter.acquire()

        # Try to acquire again - should fail and record max_concurrent_reached
        result = await limiter.try_acquire(timeout=0)
        assert result is False

        metrics = limiter.get_metrics()
        assert metrics.max_concurrent_reached >= 1

        await limiter.release()

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_metrics_wait_time_increases_with_slow_rate(
        self, engine_name: str, get_factory
    ) -> None:
        """total_wait_time should accumulate as rate limiting causes waits."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=5.0)  # 0.2s interval

        # Multiple acquires to accumulate wait time
        for _ in range(3):
            await limiter.acquire()

        metrics = limiter.get_metrics()
        # Should have accumulated some wait time (at least 2 waits of ~0.2s = ~400ms)
        assert metrics.total_wait_time_ms >= 200, (
            f"Expected accumulated wait time >= 200ms, got {metrics.total_wait_time_ms}ms"
        )

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_try_acquire_success_does_not_increment_timeouts(
        self, engine_name: str, get_factory
    ) -> None:
        """Successful try_acquire should not increment timeouts."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=100.0)

        result = await limiter.try_acquire(timeout=0)
        assert result is True

        metrics = limiter.get_metrics()
        assert metrics.timeouts == 0
