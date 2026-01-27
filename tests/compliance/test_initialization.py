"""Compliance tests for engine initialization.

All engines must implement idempotent initialization and proper state tracking.
"""

from __future__ import annotations

import pytest

from compliance.utils import (
    EngineFactory,
    get_all_engines,
    get_unit_test_engines,
)
from ratesync.exceptions import RateLimiterNotInitializedError


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]


class TestInitialization:
    """Test engine initialization compliance."""

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_initialize_is_idempotent(self, engine_name: str, get_factory) -> None:
        """initialize() can be called multiple times without error."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        # Already initialized by factory, call again
        await limiter.initialize()
        await limiter.initialize()

        assert limiter.is_initialized is True

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_is_initialized_false_before_init(self, engine_name: str, get_factory) -> None:
        """is_initialized returns False before initialize() is called."""
        # Only test memory engine directly (others need connection)
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        limiter = MemoryRateLimiter(group_id="test_init", rate_per_second=10.0)
        assert limiter.is_initialized is False

        await limiter.initialize()
        assert limiter.is_initialized is True

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_acquire_before_initialize_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """acquire() raises error if not initialized."""
        # Only test memory engine directly (others need connection)
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        limiter = MemoryRateLimiter(group_id="test_acquire", rate_per_second=10.0)

        with pytest.raises(RateLimiterNotInitializedError):
            await limiter.acquire()

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_try_acquire_before_initialize_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """try_acquire() raises error if not initialized."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        limiter = MemoryRateLimiter(group_id="test_try", rate_per_second=10.0)

        with pytest.raises(RateLimiterNotInitializedError):
            await limiter.try_acquire(timeout=0)

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_group_id_is_accessible(self, engine_name: str, get_factory) -> None:
        """group_id property returns the configured value."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(group_id="my_test_group", rate_per_second=10.0)

        assert limiter.group_id == "my_test_group"

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_default_timeout_property(self, engine_name: str, get_factory) -> None:
        """default_timeout property returns the configured value."""
        factory: EngineFactory = get_factory(engine_name)

        # Test with default_timeout set
        limiter = await factory(rate_per_second=10.0, default_timeout=5.0)
        assert limiter.default_timeout == 5.0

        # Test without default_timeout (should be None)
        limiter2 = await factory(rate_per_second=10.0)
        assert limiter2.default_timeout is None

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_fail_closed_property(self, engine_name: str, get_factory) -> None:
        """fail_closed property returns the configured value."""
        factory: EngineFactory = get_factory(engine_name)

        # Test with fail_closed=True
        limiter = await factory(rate_per_second=10.0, fail_closed=True)
        assert limiter.fail_closed is True

        # Test with fail_closed=False (default)
        limiter2 = await factory(rate_per_second=10.0, fail_closed=False)
        assert limiter2.fail_closed is False

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_release_before_initialize_raises_error(
        self, engine_name: str, get_factory
    ) -> None:
        """release() raises error if not initialized."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        limiter = MemoryRateLimiter(group_id="test_release", max_concurrent=5)

        with pytest.raises(RateLimiterNotInitializedError):
            await limiter.release()

    @pytest.mark.parametrize("engine_name", get_unit_test_engines())
    async def test_no_config_raises_error(self, engine_name: str, get_factory) -> None:
        """Creating limiter without any configuration should raise ValueError."""
        if engine_name != "memory":
            pytest.skip("Direct instantiation only for memory engine")

        from ratesync.engines.memory import MemoryRateLimiter

        with pytest.raises(ValueError) as exc_info:
            MemoryRateLimiter(group_id="test")

        # Should mention the required parameters
        assert "rate_per_second" in str(exc_info.value) or "limit" in str(exc_info.value)
