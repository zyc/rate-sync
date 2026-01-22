"""Tests for limiter state introspection (get_state).

These tests verify the get_state() functionality across different engines:
- MemoryRateLimiter (token bucket)
- RedisSlidingWindowRateLimiter (sliding window)

State introspection allows consumers to check current usage and remaining
slots without consuming any slots (read-only operation).
"""

import asyncio
import os
import time

import pytest

from ratesync.engines.memory import MemoryRateLimiter
from ratesync.schemas import LimiterState

# Conditional Redis import and connectivity check
REDIS_AVAILABLE = False
try:
    from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

    # Verificar se Redis está disponível (não apenas importável)
    try:
        import redis.asyncio as aioredis

        # Tentar conectar ao Redis para verificar disponibilidade
        async def _check_redis_available():
            password = os.getenv("REDIS_PASSWORD", "").strip()
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))

            if password:
                url = f"redis://:{password}@{host}:{port}/{db}"
            else:
                url = f"redis://{host}:{port}/{db}"

            try:
                client = aioredis.from_url(url, decode_responses=False)
                await client.ping()
                await client.close()
                return True
            except Exception:
                return False

        # Executar check synchronously (pytest precisa de valor determinado antes dos testes)
        try:
            REDIS_AVAILABLE = asyncio.run(_check_redis_available())
        except (RuntimeError, OSError):
            # Se houver problema ao rodar asyncio.run, marcar como indisponível
            REDIS_AVAILABLE = False
    except ImportError:
        REDIS_AVAILABLE = False
except ImportError:
    REDIS_AVAILABLE = False


class TestMemoryLimiterStateIntrospection:
    """Test get_state() for MemoryRateLimiter (token bucket)."""

    @pytest.mark.asyncio
    async def test_get_state_initial_state(self):
        """Test get_state() returns correct initial state."""
        limiter = MemoryRateLimiter(
            group_id="api",
            rate_per_second=10.0,
        )
        await limiter.initialize()

        state = await limiter.get_state()

        # Verify it's the correct type
        assert isinstance(state, LimiterState)

        # Initial state: allowed, full remaining
        assert state.allowed is True
        assert state.remaining > 0
        assert state.current_usage == 0
        assert state.reset_at >= int(time.time())

    @pytest.mark.asyncio
    async def test_get_state_after_single_acquire(self):
        """Test get_state() after acquiring one slot."""
        limiter = MemoryRateLimiter(
            group_id="api",
            rate_per_second=10.0,
        )
        await limiter.initialize()

        # Acquire one slot
        await limiter.acquire()

        state = await limiter.get_state()

        # Should still be allowed (have remaining slots)
        assert state.allowed is True
        assert state.current_usage >= 1
        assert state.remaining >= 0

    @pytest.mark.asyncio
    async def test_get_state_with_concurrency_limiter(self):
        """Test get_state() with concurrency limiting."""
        limiter = MemoryRateLimiter(
            group_id="db_pool",
            max_concurrent=3,
        )
        await limiter.initialize()

        # Initial state
        state = await limiter.get_state()
        assert state.allowed is True
        assert state.remaining > 0

        # Acquire 2 concurrent slots
        await limiter.acquire()
        await limiter.acquire()

        state = await limiter.get_state()
        assert state.current_usage == 2
        # Should have 1 remaining (3 max - 2 used)
        assert state.remaining >= 1

    @pytest.mark.asyncio
    async def test_get_state_does_not_consume_slots(self):
        """Test that get_state() doesn't consume any slots."""
        limiter = MemoryRateLimiter(
            group_id="api",
            rate_per_second=10.0,
        )
        await limiter.initialize()

        # Get state multiple times
        state1 = await limiter.get_state()
        state2 = await limiter.get_state()
        state3 = await limiter.get_state()

        # All should show same usage (0)
        assert state1.current_usage == 0
        assert state2.current_usage == 0
        assert state3.current_usage == 0

        # Now actually acquire
        await limiter.acquire()

        state4 = await limiter.get_state()
        assert state4.current_usage >= 1

    @pytest.mark.asyncio
    async def test_get_state_not_initialized_raises_error(self):
        """Test get_state() raises error if limiter not initialized."""
        limiter = MemoryRateLimiter(
            group_id="not_initialized",
            rate_per_second=10.0,
        )

        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="not initialized"):
            await limiter.get_state()

    @pytest.mark.asyncio
    async def test_get_state_reset_at_is_future(self):
        """Test that reset_at is always in the future."""
        limiter = MemoryRateLimiter(
            group_id="api",
            rate_per_second=5.0,
        )
        await limiter.initialize()

        state = await limiter.get_state()

        now = int(time.time())
        assert state.reset_at >= now
        # Should be within reasonable range (not too far in future)
        assert state.reset_at <= now + 10  # Within 10 seconds


@pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not installed")
class TestRedisSlidingWindowStateIntrospection:
    """Test get_state() for RedisSlidingWindowRateLimiter."""

    @pytest.mark.asyncio
    async def test_get_state_initial_state_redis(self, redis_url):
        """Test get_state() returns correct initial state for Redis."""
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_state_init",
            limit=10,
            window_seconds=60,
        )
        await limiter.initialize()
        await limiter.reset()  # Clean state

        state = await limiter.get_state()

        # Initial state: allowed, full remaining
        assert state.allowed is True
        assert state.remaining == 10
        assert state.current_usage == 0
        assert state.reset_at > int(time.time())

        await limiter.disconnect()

    @pytest.mark.asyncio
    async def test_get_state_after_acquiring_slots_redis(self, redis_url):
        """Test get_state() after acquiring slots in sliding window."""
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_state_after_acquire",
            limit=5,
            window_seconds=60,
        )
        await limiter.initialize()
        await limiter.reset()

        # Acquire 3 slots
        for _ in range(3):
            result = await limiter.try_acquire(timeout=0)
            assert result is True

        state = await limiter.get_state()

        assert state.current_usage == 3
        assert state.remaining == 2
        assert state.allowed is True

        await limiter.disconnect()

    @pytest.mark.asyncio
    async def test_get_state_when_limit_reached_redis(self, redis_url):
        """Test get_state() when limit is reached."""
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_state_limit_reached",
            limit=3,
            window_seconds=60,
        )
        await limiter.initialize()
        await limiter.reset()

        # Fill all slots
        for _ in range(3):
            result = await limiter.try_acquire(timeout=0)
            assert result is True

        state = await limiter.get_state()

        assert state.current_usage == 3
        assert state.remaining == 0
        assert state.allowed is False

        await limiter.disconnect()

    @pytest.mark.asyncio
    async def test_get_state_does_not_consume_slots_redis(self, redis_url):
        """Test that get_state() doesn't consume slots in Redis."""
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_state_no_consume",
            limit=5,
            window_seconds=60,
        )
        await limiter.initialize()
        await limiter.reset()

        # Get state multiple times
        for _ in range(10):
            state = await limiter.get_state()
            assert state.current_usage == 0
            assert state.remaining == 5

        # State calls shouldn't have consumed slots
        # Now actually acquire
        result = await limiter.try_acquire(timeout=0)
        assert result is True

        state = await limiter.get_state()
        assert state.current_usage == 1
        assert state.remaining == 4

        await limiter.disconnect()

    @pytest.mark.asyncio
    async def test_get_state_reset_at_accurate_redis(self, redis_url):
        """Test that reset_at reflects when oldest entry expires."""
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_state_reset_at",
            limit=3,
            window_seconds=5,  # Short window for testing
        )
        await limiter.initialize()
        await limiter.reset()

        # Fill limit
        for _ in range(3):
            await limiter.try_acquire(timeout=0)

        state = await limiter.get_state()

        # reset_at should be ~5 seconds from now (window_seconds)
        now = int(time.time())
        assert state.reset_at > now
        assert state.reset_at <= now + 6  # Within window + 1s margin

        await limiter.disconnect()


class TestLimiterStateUseCases:
    """Test real-world use cases for state introspection."""

    @pytest.mark.asyncio
    async def test_showing_remaining_attempts_to_user(self):
        """Test using state to show remaining attempts in UI.

        This mimics what a UI would do: check state and warn user
        when they're running low on attempts.
        """
        limiter = MemoryRateLimiter(
            group_id="login",
            rate_per_second=1.0,  # 1 per second
            max_concurrent=5,
        )
        await limiter.initialize()

        # Simulate 3 login attempts
        for _ in range(3):
            await limiter.acquire()

        state = await limiter.get_state()

        # Warning logic
        if state.remaining < 3:
            warning_msg = f"Warning: Only {state.remaining} attempts remaining"
            assert "Warning" in warning_msg

    @pytest.mark.asyncio
    @pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not installed")
    async def test_building_rate_limit_headers_from_state(self, redis_url):
        """Test using state to build accurate X-RateLimit headers.

        This is what mobile-api needs: accurate remaining count
        for rate limit response headers.
        """
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_headers",
            limit=100,
            window_seconds=60,
        )
        await limiter.initialize()
        await limiter.reset()

        # Make some requests
        for _ in range(5):
            await limiter.try_acquire(timeout=0)

        state = await limiter.get_state()

        # Build headers
        headers = {
            "X-RateLimit-Limit": str(100),  # From config
            "X-RateLimit-Remaining": str(state.remaining),
            "X-RateLimit-Reset": str(state.reset_at),
        }

        assert headers["X-RateLimit-Remaining"] == "95"
        assert int(headers["X-RateLimit-Reset"]) > int(time.time())

        await limiter.disconnect()

    @pytest.mark.asyncio
    async def test_observability_metrics_from_state(self):
        """Test using state for observability/monitoring."""
        limiter = MemoryRateLimiter(
            group_id="api",
            rate_per_second=10.0,
            max_concurrent=5,
        )
        await limiter.initialize()

        # Simulate usage
        await limiter.acquire()
        await limiter.acquire()

        state = await limiter.get_state()

        # Collect metrics
        metrics = {
            "limiter_id": limiter.group_id,
            "current_usage": state.current_usage,
            "remaining_slots": state.remaining,
            "is_allowed": state.allowed,
            "reset_at": state.reset_at,
        }

        assert metrics["limiter_id"] == "api"
        assert metrics["current_usage"] >= 2
        assert metrics["is_allowed"] is True


class TestStateIntrospectionEdgeCases:
    """Test edge cases for state introspection."""

    @pytest.mark.asyncio
    async def test_get_state_after_release(self):
        """Test get_state() after releasing concurrency slot."""
        limiter = MemoryRateLimiter(
            group_id="concurrent",
            max_concurrent=3,
        )
        await limiter.initialize()

        # Acquire and release
        await limiter.acquire()
        state_before = await limiter.get_state()
        assert state_before.current_usage == 1

        await limiter.release()
        state_after = await limiter.get_state()
        assert state_after.current_usage == 0

    @pytest.mark.asyncio
    async def test_state_consistency_under_concurrent_access(self):
        """Test that get_state() is consistent under concurrent access."""
        limiter = MemoryRateLimiter(
            group_id="concurrent_state",
            rate_per_second=100.0,
        )
        await limiter.initialize()

        # Multiple concurrent state checks
        states = await asyncio.gather(
            limiter.get_state(),
            limiter.get_state(),
            limiter.get_state(),
            limiter.get_state(),
            limiter.get_state(),
        )

        # All should return valid state objects
        assert all(isinstance(s, LimiterState) for s in states)
        assert all(s.allowed is True for s in states)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not installed")
    async def test_state_with_fail_open_on_redis_error(self, redis_url):
        """Test get_state() with fail_open when Redis fails."""
        limiter = RedisSlidingWindowRateLimiter(
            url=redis_url,
            group_id="test_fail_open",
            limit=10,
            window_seconds=60,
            fail_closed=False,  # fail-open
        )
        await limiter.initialize()

        # Disconnect to simulate failure
        await limiter.disconnect()

        # get_state() should return permissive state (fail-open)
        state = await limiter.get_state()

        assert state.allowed is True
        assert state.remaining > 0


# Pytest fixtures
@pytest.fixture
def redis_url():
    """Redis URL for testing with optional authentication.

    Suporta autenticação via variável de ambiente REDIS_PASSWORD.
    Se a senha estiver definida, a URL incluirá credenciais.

    Exemplos:
    - REDIS_PASSWORD='' -> redis://localhost:6379/0
    - REDIS_PASSWORD='abc123' -> redis://:abc123@localhost:6379/0
    """
    import os

    password = os.getenv("REDIS_PASSWORD", "").strip()
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    db = os.getenv("REDIS_DB", "0")

    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"
