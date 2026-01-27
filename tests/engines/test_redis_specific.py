"""Redis engine specific tests.

Tests for behavior unique to the Redis engine, such as:
- Connection pooling
- Key prefix handling
- Distributed coordination
- Lua script behavior
"""

from __future__ import annotations

import os

import pytest

import sys
from pathlib import Path

# Add tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from conftest import REDIS_AVAILABLE


pytestmark = [
    pytest.mark.redis,
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis library not installed"),
]


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class TestRedisEngineSpecific:
    """Tests for Redis engine implementation details."""

    async def test_key_prefix_isolation(self) -> None:
        """Different key prefixes isolate rate limit state."""
        from ratesync.engines.redis import RedisRateLimiter

        limiter1 = RedisRateLimiter(
            url=REDIS_URL,
            group_id="shared_group",
            rate_per_second=1.0,
            key_prefix="prefix_a",
        )
        limiter2 = RedisRateLimiter(
            url=REDIS_URL,
            group_id="shared_group",
            rate_per_second=1.0,
            key_prefix="prefix_b",
        )

        try:
            await limiter1.initialize()
            await limiter2.initialize()

            # Exhaust limiter1
            await limiter1.acquire()
            result1 = await limiter1.try_acquire(timeout=0)
            assert result1 is False

            # limiter2 with different prefix should work
            result2 = await limiter2.try_acquire(timeout=0)
            assert result2 is True

        finally:
            await limiter1.reset()
            await limiter2.reset()
            await limiter1.disconnect()
            await limiter2.disconnect()

    async def test_connection_pool_reuse(self) -> None:
        """Multiple acquires reuse the connection pool."""
        from ratesync.engines.redis import RedisRateLimiter

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id="pool_test",
            rate_per_second=100.0,
            key_prefix="test_pool",
            pool_min_size=2,
            pool_max_size=10,
        )

        try:
            await limiter.initialize()

            # Make many requests
            for _ in range(20):
                await limiter.try_acquire(timeout=0.1)

            # Should complete without connection errors
            metrics = limiter.get_metrics()
            assert metrics.total_acquisitions > 0

        finally:
            await limiter.reset()
            await limiter.disconnect()

    async def test_fail_closed_on_connection_error(self) -> None:
        """fail_closed=True raises error on connection failure."""
        from ratesync.engines.redis import RedisRateLimiter

        # Use invalid URL
        limiter = RedisRateLimiter(
            url="redis://invalid-host:6379/0",
            group_id="fail_closed_test",
            rate_per_second=10.0,
            fail_closed=True,
        )

        with pytest.raises(Exception):
            await limiter.initialize()

    async def test_distributed_group_coordination(self) -> None:
        """Multiple limiters with same group_id share state."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"distributed_{uuid.uuid4().hex[:8]}"
        prefix = f"test_dist_{uuid.uuid4().hex[:8]}"

        limiter1 = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            rate_per_second=1.0,
            key_prefix=prefix,
        )
        limiter2 = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            rate_per_second=1.0,
            key_prefix=prefix,
        )

        try:
            await limiter1.initialize()
            await limiter2.initialize()

            # Acquire on limiter1
            await limiter1.acquire()

            # limiter2 should see the rate limit
            result = await limiter2.try_acquire(timeout=0)
            assert result is False, "Distributed state should be shared"

        finally:
            await limiter1.reset()
            await limiter1.disconnect()
            await limiter2.disconnect()

    async def test_sliding_window_with_redis(self) -> None:
        """Test sliding window algorithm with Redis."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"sw_{uuid.uuid4().hex[:8]}"
        prefix = f"test_sw_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            limit=5,
            window_seconds=60,
            key_prefix=prefix,
        )

        try:
            await limiter.initialize()

            # Should allow 5 requests
            for i in range(5):
                result = await limiter.try_acquire(timeout=0)
                assert result is True, f"Request {i+1} should succeed"

            # 6th should fail
            result = await limiter.try_acquire(timeout=0)
            assert result is False, "Should be rate limited"

        finally:
            await limiter.reset()
            await limiter.disconnect()

    async def test_algorithm_property(self) -> None:
        """Algorithm property reflects configuration."""
        from ratesync.engines.redis import RedisRateLimiter

        # Token bucket
        limiter1 = RedisRateLimiter(
            url=REDIS_URL,
            group_id="alg_tb",
            rate_per_second=10.0,
            key_prefix="test_alg_tb",
        )
        assert limiter1.algorithm == "token_bucket"

        # Sliding window
        limiter2 = RedisRateLimiter(
            url=REDIS_URL,
            group_id="alg_sw",
            limit=10,
            window_seconds=60,
            key_prefix="test_alg_sw",
        )
        assert limiter2.algorithm == "sliding_window"

    async def test_get_state_sliding_window(self) -> None:
        """Redis engine get_state() works with sliding window algorithm."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"state_sw_{uuid.uuid4().hex[:8]}"
        prefix = f"test_state_sw_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            limit=5,
            window_seconds=60,
            key_prefix=prefix,
        )

        try:
            await limiter.initialize()

            # Initial state
            state = await limiter.get_state()
            assert state.allowed is True
            assert state.remaining == 5

            # Use some quota
            await limiter.acquire()
            await limiter.acquire()

            state = await limiter.get_state()
            assert state.remaining == 3
            assert state.current_usage >= 2

        finally:
            await limiter.reset()
            await limiter.disconnect()

    async def test_get_state_token_bucket(self) -> None:
        """Redis engine get_state() works with token bucket algorithm."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"state_tb_{uuid.uuid4().hex[:8]}"
        prefix = f"test_state_tb_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            rate_per_second=10.0,
            key_prefix=prefix,
        )

        try:
            await limiter.initialize()

            # Initial state
            state = await limiter.get_state()
            assert state.allowed is True

            # After acquire
            await limiter.acquire()

            state = await limiter.get_state()
            assert isinstance(state.reset_at, int)

        finally:
            await limiter.reset()
            await limiter.disconnect()

    async def test_get_state_with_concurrency(self) -> None:
        """Redis engine get_state() reflects concurrency state."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"state_conc_{uuid.uuid4().hex[:8]}"
        prefix = f"test_state_conc_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            limit=10,
            window_seconds=60,
            max_concurrent=2,
            key_prefix=prefix,
        )

        try:
            await limiter.initialize()

            # Fill concurrency slots
            await limiter.acquire()
            await limiter.acquire()

            state = await limiter.get_state()
            # Should show limited due to concurrency
            assert state.current_usage >= 2

            # Release slots
            await limiter.release()
            await limiter.release()

        finally:
            await limiter.reset()
            await limiter.disconnect()

    async def test_from_config_creates_limiter(self) -> None:
        """Redis engine from_config() creates properly configured limiter."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix="from_config_test",
        )

        limiter = RedisRateLimiter.from_config(
            config,
            group_id="from_config_test",
            rate_per_second=50.0,
            max_concurrent=10,
        )

        assert limiter.group_id == "from_config_test"
        assert limiter.rate_per_second == 50.0
        assert limiter.max_concurrent == 10

    async def test_get_config_returns_all_fields(self) -> None:
        """Redis engine get_config() returns all configuration fields."""
        from ratesync.engines.redis import RedisRateLimiter

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id="config_test",
            limit=100,
            window_seconds=300,
            max_concurrent=50,
            default_timeout=10.0,
            fail_closed=True,
            key_prefix="config_test",
        )

        config = limiter.get_config()

        assert config.id == "config_test"
        assert config.algorithm == "sliding_window"
        assert config.store_id == "redis"
        assert config.limit == 100
        assert config.window_seconds == 300
        assert config.max_concurrent == 50
        assert config.timeout == 10.0
        assert config.fail_closed is True

    async def test_reset_clears_all_keys(self) -> None:
        """Redis engine reset() clears all related keys."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"reset_{uuid.uuid4().hex[:8]}"
        prefix = f"test_reset_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            limit=5,
            window_seconds=60,
            max_concurrent=2,
            key_prefix=prefix,
        )

        try:
            await limiter.initialize()

            # Use the limiter
            await limiter.acquire()

            # Reset
            await limiter.reset()

            # Should be back to initial state
            state = await limiter.get_state()
            assert state.remaining == 5
            assert state.current_usage == 0

        finally:
            await limiter.disconnect()

    async def test_concurrent_with_rate_limiting(self) -> None:
        """Redis engine handles combined rate + concurrency limiting."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"combined_{uuid.uuid4().hex[:8]}"
        prefix = f"test_combined_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            rate_per_second=100.0,  # High rate to not interfere
            max_concurrent=2,
            key_prefix=prefix,
        )

        try:
            await limiter.initialize()

            # Fill concurrency
            await limiter.acquire()
            await limiter.acquire()

            # Third should fail due to concurrency
            result = await limiter.try_acquire(timeout=0)
            assert result is False

            # Release one
            await limiter.release()

            # Now should work
            result = await limiter.try_acquire(timeout=0.1)
            assert result is True

            # Cleanup
            await limiter.release()
            await limiter.release()

        finally:
            await limiter.reset()
            await limiter.disconnect()

    async def test_disconnect_is_safe_to_call_multiple_times(self) -> None:
        """Redis engine disconnect() can be called multiple times safely."""
        from ratesync.engines.redis import RedisRateLimiter
        import uuid

        group_id = f"disconnect_{uuid.uuid4().hex[:8]}"
        prefix = f"test_disconnect_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=REDIS_URL,
            group_id=group_id,
            rate_per_second=10.0,
            key_prefix=prefix,
        )

        await limiter.initialize()
        await limiter.disconnect()
        # Should not raise
        await limiter.disconnect()
