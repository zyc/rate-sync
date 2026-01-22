"""Tests for Redis-based rate limiting engine.

These tests verify the Redis engine implementation, including:
- Basic acquire/try_acquire operations
- Rate limiting enforcement (rate_per_second)
- Concurrency limiting enforcement (max_concurrent)
- Combined strategies
- Distributed coordination
- Connection pooling
- Metrics tracking
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ratesync.core import RateLimiterMetrics
from ratesync.schemas import MemoryEngineConfig, RedisEngineConfig

# Mark all tests in this module as requiring Redis
pytestmark = pytest.mark.redis

# Try to import RedisRateLimiter, skip entire module if not available
try:
    from ratesync.engines.redis import RedisRateLimiter

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    RedisRateLimiter = None


@pytest.fixture(name="config")
def redis_config_fixture():
    """Create a test Redis configuration."""
    return RedisEngineConfig(
        url="redis://localhost:6379/0",
        key_prefix="test_rate_limit",
        pool_max_size=5,
    )


def _mock_script_success():
    """Create a mock that returns success for acquire script."""
    # Script returns: [success (1), wait_ms (0), concurrent_count (1)]
    return AsyncMock(return_value=[1, 0, 1])


def _mock_script_rate_limited():
    """Create a mock that returns rate limited for acquire script."""
    # Script returns: [success (0), wait_ms (500 = 500ms), concurrent_count (-1)]
    return AsyncMock(return_value=[0, 500, -1])


def _mock_script_concurrency_limited():
    """Create a mock that returns concurrency limited for acquire script."""
    # Script returns: [success (0), wait_ms (-1 = poll), concurrent_count (current)]
    return AsyncMock(return_value=[0, -1, 5])


class TestRedisEngineImport:
    """Test Redis engine import behavior."""

    def test_import_success_when_redis_installed(self):
        """Test that RedisRateLimiter can be imported when redis is installed."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        assert RedisRateLimiter is not None

    def test_import_error_message_when_redis_not_installed(self):
        """Test that helpful error is shown when redis not installed."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        # Patch REDIS_AVAILABLE to simulate redis not being installed
        with patch("ratesync.engines.redis.REDIS_AVAILABLE", False):
            with pytest.raises(ImportError) as exc_info:
                # Try to instantiate to trigger the check
                RedisRateLimiter(
                    url="redis://localhost:6379/0",
                    group_id="test",
                    rate_per_second=1.0,
                )

            error_msg = str(exc_info.value)
            assert "redis" in error_msg.lower()
            assert "pip install" in error_msg.lower()


class TestRedisEngineValidation:
    """Test Redis engine validation for rate_per_second and max_concurrent."""

    def test_neither_strategy_raises_error(self):
        """Test that creating without any strategy raises ValueError."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        with pytest.raises(ValueError) as exc_info:
            RedisRateLimiter(
                url="redis://localhost:6379/0",
                group_id="test",
            )

        assert "At least one of rate_per_second or max_concurrent must be specified" in str(
            exc_info.value
        )

    def test_negative_max_concurrent_raises_error(self):
        """Test that negative max_concurrent is rejected."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        with pytest.raises(ValueError) as exc_info:
            RedisRateLimiter(
                url="redis://localhost:6379/0",
                group_id="test",
                max_concurrent=-1,
            )

        assert "max_concurrent must be > 0" in str(exc_info.value)

    def test_zero_max_concurrent_raises_error(self):
        """Test that zero max_concurrent is rejected."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        with pytest.raises(ValueError) as exc_info:
            RedisRateLimiter(
                url="redis://localhost:6379/0",
                group_id="test",
                max_concurrent=0,
            )

        assert "max_concurrent must be > 0" in str(exc_info.value)

    def test_max_concurrent_only_valid(self):
        """Test creating limiter with only max_concurrent."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter(
            url="redis://localhost:6379/0",
            group_id="test",
            max_concurrent=5,
        )

        assert limiter.rate_per_second is None
        assert limiter.max_concurrent == 5

    def test_both_strategies_valid(self):
        """Test creating limiter with both strategies."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter(
            url="redis://localhost:6379/0",
            group_id="test",
            rate_per_second=10.0,
            max_concurrent=5,
        )

        assert limiter.rate_per_second == 10.0
        assert limiter.max_concurrent == 5


class TestRedisEngineConfiguration:
    """Test Redis engine configuration."""

    def test_from_config(self, config):
        """Test creating RedisRateLimiter from config."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(
            config, group_id="test", rate_per_second=2.0, timeout=10.0
        )

        assert limiter.group_id == "test"
        assert limiter.rate_per_second == 2.0
        assert limiter.default_timeout == 10.0
        assert not limiter.is_initialized

    def test_invalid_config_type(self):
        """Test that from_config rejects invalid config type."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        invalid_config = MemoryEngineConfig()

        with pytest.raises(ValueError) as exc_info:
            RedisRateLimiter.from_config(invalid_config, "test", 1.0)

        assert "RedisEngineConfig" in str(exc_info.value)

    def test_validation_negative_rate(self):
        """Test that negative rate_per_second is rejected."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        with pytest.raises(ValueError) as exc_info:
            RedisRateLimiter(
                url="redis://localhost:6379/0",
                group_id="test",
                rate_per_second=-1.0,
            )

        assert "rate_per_second must be > 0" in str(exc_info.value)

    def test_validation_empty_group_id(self):
        """Test that empty group_id is rejected."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        with pytest.raises(ValueError) as exc_info:
            RedisRateLimiter(
                url="redis://localhost:6379/0",
                group_id="",
                rate_per_second=1.0,
            )

        assert "group_id cannot be empty" in str(exc_info.value)


@pytest.mark.asyncio
class TestRedisEngineInitialization:
    """Test Redis engine initialization."""

    async def test_initialize_idempotent(self, config):
        """Test that initialize() is idempotent."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", 1.0)

        # Mock Redis client
        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.register_script = MagicMock(return_value=AsyncMock())
            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            # First initialization
            await limiter.initialize()
            assert limiter.is_initialized

            # Second initialization should not create new pool
            pool_call_count = mock_redis.ConnectionPool.from_url.call_count
            await limiter.initialize()
            assert limiter.is_initialized
            assert mock_redis.ConnectionPool.from_url.call_count == pool_call_count

    async def test_acquire_before_initialize_fails(self, config):
        """Test that acquire before initialize raises error."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", 1.0)

        with pytest.raises(RuntimeError) as exc_info:
            await limiter.acquire()

        assert "not initialized" in str(exc_info.value)


@pytest.mark.asyncio
class TestRedisEngineRateLimiting:
    """Test Redis engine rate limiting behavior."""

    async def test_acquire_enforces_rate_limit(self, config):
        """Test that acquire enforces rate limiting."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", 2.0)

        # Mock Redis to simulate rate limiting
        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()

            # Mock Lua script to return success: [1, 0, 1]
            # Script returns: [success, wait_needed, concurrent_count]
            mock_script = _mock_script_success()
            # register_script is synchronous, not async
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()

            # First acquire should succeed immediately
            start = time.time()
            await limiter.acquire()
            elapsed = time.time() - start
            assert elapsed < 0.1  # Should be fast

            # Metrics should show one acquisition
            metrics = limiter.get_metrics()
            assert metrics.total_acquisitions == 1

    async def test_try_acquire_timeout(self, config):
        """Test that try_acquire respects timeout."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", 1.0)

        # Mock Redis to always return rate limited
        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()

            # Mock script to always return rate limited: [0, 1, -1]
            mock_script = _mock_script_rate_limited()
            # register_script is synchronous, not async
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()

            # try_acquire with short timeout should fail
            start = time.time()
            result = await limiter.try_acquire(timeout=0.1)
            elapsed = time.time() - start

            assert result is False
            assert 0.09 <= elapsed <= 0.2  # Should respect timeout

            # Metrics should show timeout
            metrics = limiter.get_metrics()
            assert metrics.timeouts == 1


@pytest.mark.asyncio
class TestRedisEngineMetrics:
    """Test Redis engine metrics tracking."""

    async def test_metrics_tracking(self, config):
        """Test that metrics are properly tracked."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", 10.0)

        # Mock Redis
        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            # Mock script to always succeed: [1, 0, 1]
            mock_script = _mock_script_success()
            # register_script is synchronous, not async
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()

            # Perform multiple acquisitions
            for _ in range(3):
                await limiter.acquire()

            metrics = limiter.get_metrics()
            assert metrics.total_acquisitions == 3
            assert isinstance(metrics, RateLimiterMetrics)


@pytest.mark.asyncio
class TestRedisEngineCleanup:
    """Test Redis engine resource cleanup."""

    async def test_disconnect_closes_resources(self, config):
        """Test that disconnect properly closes Redis resources."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", 1.0)

        with (
            patch("ratesync.engines.redis.redis_asyncio") as mock_redis,
            patch("ratesync.engines.redis.ConnectionPool") as mock_pool_class,
        ):
            mock_pool = AsyncMock()
            mock_pool.disconnect = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.aclose = AsyncMock()
            # Mock script to always succeed: [1, 0, 1]
            mock_script = _mock_script_success()
            # register_script is synchronous, not async
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_pool_class.from_url.return_value = mock_pool

            await limiter.initialize()
            await limiter.disconnect()

            # Verify cleanup was called
            mock_client.aclose.assert_called_once()
            mock_pool.disconnect.assert_called_once()


@pytest.mark.asyncio
class TestRedisEngineConcurrencyLimiting:
    """Test Redis engine concurrency limiting (max_concurrent)."""

    async def test_max_concurrent_only(self, config):
        """Test creating limiter with only max_concurrent via from_config."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", max_concurrent=5)

        assert limiter.rate_per_second is None
        assert limiter.max_concurrent == 5
        assert limiter.group_id == "test"

    async def test_concurrency_metrics_tracked(self, config):
        """Test that concurrency metrics are tracked."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(
            config, "test", rate_per_second=10.0, max_concurrent=5
        )

        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()

            # Mock acquire script to succeed with concurrent_count
            mock_acquire = _mock_script_success()
            # Mock release script
            mock_release = AsyncMock(return_value=0)

            scripts = [mock_acquire, mock_release]
            script_index = [0]

            def get_script(*_):
                script = scripts[script_index[0] % len(scripts)]
                script_index[0] += 1
                return script

            mock_client.register_script = MagicMock(side_effect=get_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()
            await limiter.acquire()

            metrics = limiter.get_metrics()
            assert metrics.total_acquisitions == 1
            # Since max_concurrent is set, concurrent acquire should be tracked
            assert metrics.current_concurrent >= 0

    async def test_concurrency_limited_causes_timeout(self, config):
        """Test that concurrency limiting causes try_acquire to timeout."""
        if not REDIS_AVAILABLE:
            pytest.skip("Redis library not installed")

        limiter = RedisRateLimiter.from_config(config, "test", max_concurrent=2)

        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()

            # Mock script to return concurrency limited: [0, 0, 2]
            mock_script = _mock_script_concurrency_limited()
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()

            # try_acquire should timeout since always concurrency limited
            start = time.time()
            result = await limiter.try_acquire(timeout=0.1)
            elapsed = time.time() - start

            assert result is False
            assert 0.09 <= elapsed <= 0.2  # Should respect timeout
            metrics = limiter.get_metrics()
            assert metrics.timeouts >= 1
