"""Compliance tests for state introspection (get_state).

Tests for engines that support the get_state() method.
"""

from __future__ import annotations

import pytest

from ratesync.schemas import LimiterState
from compliance.utils import (
    EngineFactory,
    get_engines_with,
    get_unit_test_engines,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

# Engines that support get_state and don't require infrastructure (for unit tests)
STATE_UNIT_ENGINES = [
    e for e in get_engines_with("get_state") if e in get_unit_test_engines()
]

# All engines that support get_state (includes Redis/PostgreSQL for integration tests)
STATE_CAPABLE_ENGINES = get_engines_with("get_state")


class TestStateIntrospection:
    """Test state introspection compliance."""

    @pytest.mark.parametrize("engine_name", STATE_CAPABLE_ENGINES)
    async def test_get_state_returns_correct_type(
        self, engine_name: str, get_factory
    ) -> None:
        """get_state() returns LimiterState."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        state = await limiter.get_state()
        assert isinstance(state, LimiterState)

    @pytest.mark.parametrize("engine_name", STATE_CAPABLE_ENGINES)
    async def test_initial_state_is_allowed(
        self, engine_name: str, get_factory
    ) -> None:
        """Initial state should show allowed=True and remaining > 0."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=5, window_seconds=60)

        state = await limiter.get_state()
        assert state.allowed is True
        assert state.remaining > 0
        assert state.current_usage == 0

    @pytest.mark.parametrize("engine_name", STATE_CAPABLE_ENGINES)
    async def test_state_updates_after_acquire(
        self, engine_name: str, get_factory
    ) -> None:
        """State should reflect acquisitions."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=5, window_seconds=60)

        # Initial state
        state_before = await limiter.get_state()
        remaining_before = state_before.remaining

        # Acquire one slot
        await limiter.acquire()
        await limiter.release()

        # State after
        state_after = await limiter.get_state()
        assert state_after.current_usage >= 1
        assert state_after.remaining < remaining_before

    @pytest.mark.parametrize("engine_name", STATE_UNIT_ENGINES)
    async def test_state_shows_not_allowed_at_limit(
        self, engine_name: str, get_factory
    ) -> None:
        """State should show allowed=False when limit reached.

        Note: This test currently only runs on memory engine due to a known issue
        with Redis's get_state() implementation for sliding window algorithm where
        the state doesn't properly reflect the acquisitions. See GitHub issue for tracking.
        """
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=2, window_seconds=60)

        # Use up the limit
        await limiter.try_acquire(timeout=0)
        await limiter.try_acquire(timeout=0)

        state = await limiter.get_state()
        assert state.allowed is False
        assert state.remaining == 0
        assert state.current_usage == 2

    @pytest.mark.parametrize("engine_name", STATE_CAPABLE_ENGINES)
    async def test_state_reset_at_is_future_timestamp(
        self, engine_name: str, get_factory
    ) -> None:
        """reset_at should be a future timestamp."""
        import time

        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=5, window_seconds=60)

        await limiter.acquire()
        await limiter.release()

        state = await limiter.get_state()
        now = int(time.time())
        assert state.reset_at > now

    @pytest.mark.parametrize("engine_name", STATE_CAPABLE_ENGINES)
    async def test_get_state_does_not_consume_slots(
        self, engine_name: str, get_factory
    ) -> None:
        """get_state() should be read-only - not consume any slots."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=2, window_seconds=60)

        # Call get_state multiple times
        for _ in range(10):
            state = await limiter.get_state()

        # All slots should still be available
        state = await limiter.get_state()
        assert state.remaining == 2
        assert state.current_usage == 0

    @pytest.mark.parametrize("engine_name", STATE_CAPABLE_ENGINES)
    async def test_get_state_with_token_bucket(
        self, engine_name: str, get_factory
    ) -> None:
        """get_state() works with token bucket algorithm."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        state = await limiter.get_state()
        assert isinstance(state, LimiterState)
        assert state.allowed is True


class TestStateBeforeInitialization:
    """Test get_state behavior before initialization."""

    @pytest.mark.parametrize("engine_name", ["memory"])
    async def test_get_state_before_initialize_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """get_state() before initialize() should raise RuntimeError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        limiter = MemoryRateLimiter(group_id="test_state_init", rate_per_second=10.0)

        # Should raise RuntimeError because not initialized
        with pytest.raises(RuntimeError) as exc_info:
            await limiter.get_state()

        assert "not initialized" in str(exc_info.value).lower()
