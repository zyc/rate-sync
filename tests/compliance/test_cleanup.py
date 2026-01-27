"""Compliance tests for resource cleanup (reset, disconnect).

All engines must properly clean up resources and support state reset.
"""

from __future__ import annotations

import pytest

from compliance.utils import (
    EngineFactory,
    get_unit_test_engines,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

UNIT_ENGINES = get_unit_test_engines()


class TestResourceCleanup:
    """Test resource cleanup compliance."""

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_clears_rate_limit_state(self, engine_name: str, get_factory) -> None:
        """reset() clears rate limit state, allowing immediate acquire."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=1.0)  # Slow rate

        # Acquire
        await limiter.acquire()

        # Would need to wait ~1s for next acquire
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Should be rate limited"

        # Reset state
        await limiter.reset()

        # Should be able to acquire immediately
        result = await limiter.try_acquire(timeout=0)
        assert result is True, "Reset should clear rate limit state"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_clears_sliding_window_state(self, engine_name: str, get_factory) -> None:
        """reset() clears sliding window state."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=2, window_seconds=60)

        # Fill window
        await limiter.try_acquire(timeout=0)
        await limiter.try_acquire(timeout=0)

        # Should be limited
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Should be at window limit"

        # Reset
        await limiter.reset()

        # Should be able to acquire again
        result = await limiter.try_acquire(timeout=0)
        assert result is True, "Reset should clear window state"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_clears_concurrency_state(self, engine_name: str, get_factory) -> None:
        """reset() clears concurrency state."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=1)

        # Acquire without releasing (simulating orphan)
        await limiter.acquire()

        # Should be at limit
        result = await limiter.try_acquire(timeout=0)
        assert result is False, "Should be at concurrency limit"

        # Reset
        await limiter.reset()

        # Should be able to acquire again
        result = await limiter.try_acquire(timeout=0)
        assert result is True, "Reset should clear concurrency state"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_clears_metrics(self, engine_name: str, get_factory) -> None:
        """reset() clears metrics."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=100.0)

        # Accumulate some metrics
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

        metrics_before = limiter.get_metrics()
        assert metrics_before.total_acquisitions == 3

        # Reset
        await limiter.reset()

        metrics_after = limiter.get_metrics()
        assert metrics_after.total_acquisitions == 0

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_is_idempotent(self, engine_name: str, get_factory) -> None:
        """reset() can be called multiple times without error."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        await limiter.reset()
        await limiter.reset()
        await limiter.reset()

        # Should still work
        result = await limiter.try_acquire(timeout=0)
        assert result is True

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_usable_after_reset(self, engine_name: str, get_factory) -> None:
        """Limiter is fully usable after reset."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=100.0, max_concurrent=2)

        # Use it
        await limiter.acquire()
        await limiter.release()

        # Reset
        await limiter.reset()

        # Use again
        await limiter.acquire()
        await limiter.acquire()
        await limiter.release()
        await limiter.release()

        metrics = limiter.get_metrics()
        assert metrics.total_acquisitions == 2
        assert metrics.total_releases == 2


class TestDisconnect:
    """Test disconnect/cleanup compliance."""

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_disconnect_is_idempotent(self, engine_name: str, get_factory) -> None:
        """disconnect() can be called multiple times without error."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        # Call disconnect multiple times (memory engine has no disconnect,
        # but this tests the interface exists)
        if hasattr(limiter, "disconnect"):
            await limiter.disconnect()
            await limiter.disconnect()

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_preserves_configuration(self, engine_name: str, get_factory) -> None:
        """reset() should preserve limiter configuration."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(
            group_id="test_config",
            rate_per_second=42.5,
            max_concurrent=7,
        )

        # Verify initial config
        assert limiter.rate_per_second == 42.5
        assert limiter.max_concurrent == 7
        assert limiter.group_id == "test_config"

        # Reset
        await limiter.reset()

        # Config should be preserved
        assert limiter.rate_per_second == 42.5
        assert limiter.max_concurrent == 7
        assert limiter.group_id == "test_config"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_while_holding_slots(self, engine_name: str, get_factory) -> None:
        """reset() while holding concurrency slots should release them."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=2)

        # Acquire slots
        await limiter.acquire()
        await limiter.acquire()

        # Reset - should clear held slots
        await limiter.reset()

        # Should be able to acquire both slots again
        result1 = await limiter.try_acquire(timeout=0)
        result2 = await limiter.try_acquire(timeout=0)

        assert result1 is True, "First acquire after reset should succeed"
        assert result2 is True, "Second acquire after reset should succeed"

        await limiter.release()
        await limiter.release()


class TestResetAll:
    """Test reset_all method compliance."""

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_all_clears_state(self, engine_name: str, get_factory) -> None:
        """reset_all() clears all limiter state."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=1.0)

        # Acquire to create state
        await limiter.acquire()

        # Should be rate limited
        result = await limiter.try_acquire(timeout=0)
        assert result is False

        # reset_all should clear state (if method exists)
        if hasattr(limiter, "reset_all"):
            await limiter.reset_all()

            # Should be able to acquire again
            result = await limiter.try_acquire(timeout=0)
            assert result is True

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_reset_all_is_idempotent(self, engine_name: str, get_factory) -> None:
        """reset_all() can be called multiple times without error."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        if hasattr(limiter, "reset_all"):
            await limiter.reset_all()
            await limiter.reset_all()
            await limiter.reset_all()

            # Should still work
            result = await limiter.try_acquire(timeout=0)
            assert result is True
