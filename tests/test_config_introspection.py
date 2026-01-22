"""Tests for limiter configuration introspection (get_config).

These tests verify the get_config() functionality across different engines:
- MemoryRateLimiter (token bucket)
- RedisSlidingWindowRateLimiter (sliding window)

Configuration introspection allows consumers to inspect limiter settings
without modifying the configuration (read-only).
"""

import pytest

from ratesync.engines.memory import MemoryRateLimiter
from ratesync.schemas import LimiterReadOnlyConfig

# Conditional Redis import
try:
    from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class TestMemoryLimiterConfigIntrospection:
    """Test get_config() for MemoryRateLimiter (token bucket)."""

    @pytest.mark.asyncio
    async def test_get_config_with_rate_per_second(self):
        """Test get_config() returns correct config for rate-limited limiter."""
        limiter = MemoryRateLimiter(
            group_id="api_rate",
            rate_per_second=10.0,
            default_timeout=30.0,
        )
        await limiter.initialize()

        config = limiter.get_config()

        # Verify it's the correct type
        assert isinstance(config, LimiterReadOnlyConfig)

        # Verify basic fields
        assert config.id == "api_rate"
        assert config.algorithm == "token_bucket"
        assert config.store_id == "memory"

        # Verify token bucket fields
        assert config.rate_per_second == 10.0
        assert config.max_concurrent is None
        assert config.timeout == 30.0

        # Verify sliding window fields are None
        assert config.limit is None
        assert config.window_seconds is None

        # Verify common fields
        assert config.fail_closed is False

    @pytest.mark.asyncio
    async def test_get_config_with_max_concurrent(self):
        """Test get_config() returns correct config for concurrency limiter."""
        limiter = MemoryRateLimiter(
            group_id="db_pool",
            max_concurrent=5,
            fail_closed=True,
        )
        await limiter.initialize()

        config = limiter.get_config()

        assert config.id == "db_pool"
        assert config.algorithm == "token_bucket"
        assert config.store_id == "memory"
        assert config.rate_per_second is None
        assert config.max_concurrent == 5
        assert config.timeout is None
        assert config.fail_closed is True

    @pytest.mark.asyncio
    async def test_get_config_with_both_strategies(self):
        """Test get_config() with both rate and concurrency limiting."""
        limiter = MemoryRateLimiter(
            group_id="api_combined",
            rate_per_second=100.0,
            max_concurrent=10,
            default_timeout=60.0,
            fail_closed=False,
        )
        await limiter.initialize()

        config = limiter.get_config()

        assert config.id == "api_combined"
        assert config.rate_per_second == 100.0
        assert config.max_concurrent == 10
        assert config.timeout == 60.0
        assert config.fail_closed is False

    def test_get_config_is_read_only(self):
        """Test that LimiterReadOnlyConfig is immutable (frozen dataclass)."""
        limiter = MemoryRateLimiter(group_id="test", rate_per_second=10.0)

        config = limiter.get_config()

        # Try to modify - should raise FrozenInstanceError
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            config.rate_per_second = 999.0

    @pytest.mark.asyncio
    async def test_get_config_before_initialize(self):
        """Test get_config() works even before initialize() is called."""
        limiter = MemoryRateLimiter(
            group_id="not_initialized",
            rate_per_second=5.0,
        )

        # Should work without initialize()
        config = limiter.get_config()

        assert config.id == "not_initialized"
        assert config.rate_per_second == 5.0


@pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not installed")
class TestRedisSlidingWindowConfigIntrospection:
    """Test get_config() for RedisSlidingWindowRateLimiter."""

    @pytest.mark.asyncio
    async def test_get_config_sliding_window(self):
        """Test get_config() returns correct config for sliding window."""
        limiter = RedisSlidingWindowRateLimiter(
            url="redis://localhost:6379/0",
            group_id="login",
            limit=5,
            window_seconds=300,
            default_timeout=0,
        )

        config = limiter.get_config()

        # Verify basic fields
        assert config.id == "login"
        assert config.algorithm == "sliding_window"
        assert config.store_id == "redis"

        # Verify sliding window fields
        assert config.limit == 5
        assert config.window_seconds == 300
        assert config.timeout == 0

        # Verify token bucket fields are None
        assert config.rate_per_second is None
        assert config.max_concurrent is None

        # Verify common fields
        assert config.fail_closed is False

    @pytest.mark.asyncio
    async def test_get_config_with_fail_closed(self):
        """Test get_config() returns correct fail_closed setting."""
        limiter = RedisSlidingWindowRateLimiter(
            url="redis://localhost:6379/0",
            group_id="critical_api",
            limit=10,
            window_seconds=60,
            fail_closed=True,
        )

        config = limiter.get_config()

        assert config.id == "critical_api"
        assert config.fail_closed is True

    def test_get_config_is_read_only_redis(self):
        """Test that Redis limiter config is also immutable."""
        limiter = RedisSlidingWindowRateLimiter(
            url="redis://localhost:6379/0",
            group_id="test",
            limit=10,
            window_seconds=60,
        )

        config = limiter.get_config()

        # Try to modify - should raise FrozenInstanceError
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            config.limit = 999


class TestConfigIntrospectionUseCases:
    """Test real-world use cases for config introspection."""

    @pytest.mark.asyncio
    async def test_building_rate_limit_headers_from_config(self):
        """Test using config to build proper rate limit headers.

        This mimics what mobile-api does: inspect config to know the limit
        value for building RateLimitResult objects.
        """
        limiter = MemoryRateLimiter(
            group_id="api",
            rate_per_second=1.67,  # ~100 req/min
        )
        await limiter.initialize()

        config = limiter.get_config()

        # Calculate limit for headers (rate * 60 seconds)
        if config.algorithm == "token_bucket" and config.rate_per_second:
            limit_per_minute = int(config.rate_per_second * 60)
            assert limit_per_minute == 100
        elif config.algorithm == "sliding_window" and config.limit:
            limit_per_minute = config.limit
        else:
            limit_per_minute = 0

        assert limit_per_minute > 0

    @pytest.mark.asyncio
    async def test_logging_limiter_configuration(self):
        """Test logging limiter configuration for debugging."""
        limiter = MemoryRateLimiter(
            group_id="debug_api",
            rate_per_second=50.0,
            max_concurrent=10,
        )

        config = limiter.get_config()

        # Build a debug log message
        log_msg = f"Limiter '{config.id}' using {config.algorithm} algorithm"
        if config.rate_per_second:
            log_msg += f" (rate: {config.rate_per_second} req/s)"
        if config.max_concurrent:
            log_msg += f" (max concurrent: {config.max_concurrent})"

        assert "debug_api" in log_msg
        assert "token_bucket" in log_msg
        assert "rate: 50.0 req/s" in log_msg
        assert "max concurrent: 10" in log_msg

    @pytest.mark.asyncio
    @pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not installed")
    async def test_comparing_configs_across_limiters(self):
        """Test comparing configurations of different limiters."""
        memory_limiter = MemoryRateLimiter(
            group_id="mem_api",
            rate_per_second=10.0,
        )

        redis_limiter = RedisSlidingWindowRateLimiter(
            url="redis://localhost:6379/0",
            group_id="redis_auth",
            limit=5,
            window_seconds=60,
        )

        mem_config = memory_limiter.get_config()
        redis_config = redis_limiter.get_config()

        # Different algorithms
        assert mem_config.algorithm == "token_bucket"
        assert redis_config.algorithm == "sliding_window"

        # Different stores
        assert mem_config.store_id == "memory"
        assert redis_config.store_id == "redis"

        # Different parameters
        assert mem_config.rate_per_second is not None
        assert redis_config.limit is not None


class TestConfigIntrospectionEdgeCases:
    """Test edge cases for config introspection."""

    @pytest.mark.asyncio
    async def test_get_config_multiple_times_returns_same_values(self):
        """Test that calling get_config() multiple times returns consistent values."""
        limiter = MemoryRateLimiter(
            group_id="stable",
            rate_per_second=25.0,
        )

        config1 = limiter.get_config()
        config2 = limiter.get_config()

        # Should return different objects (not cached)
        assert config1 is not config2

        # But with the same values
        assert config1.id == config2.id
        assert config1.rate_per_second == config2.rate_per_second
        assert config1.algorithm == config2.algorithm

    @pytest.mark.asyncio
    async def test_config_does_not_reflect_runtime_state_changes(self):
        """Test that config is a snapshot, not affected by runtime changes."""
        limiter = MemoryRateLimiter(
            group_id="dynamic",
            rate_per_second=10.0,
            max_concurrent=5,
        )
        await limiter.initialize()

        config_before = limiter.get_config()

        # Acquire some slots (changes runtime state)
        await limiter.acquire()

        config_after = limiter.get_config()

        # Configuration should be identical (not affected by acquire)
        assert config_before.rate_per_second == config_after.rate_per_second
        assert config_before.max_concurrent == config_after.max_concurrent
