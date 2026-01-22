"""Redis-based sliding window rate limiting engine.

This module implements rate limiting using Redis with a sliding window counter
algorithm. It's ideal for auth protection scenarios like login attempts.

The sliding window algorithm counts requests within a time window and blocks
when the limit is reached. Unlike token bucket, it provides exact counting
of requests in the window.

Requirements:
    pip install 'rate-sync[redis]'  or  pip install redis[asyncio]
"""

import asyncio
import logging
import time

try:
    from redis import asyncio as redis_asyncio
    from redis.asyncio import ConnectionPool

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis_asyncio = None  # type: ignore
    ConnectionPool = None  # type: ignore

from ratesync.core import RateLimiter, RateLimiterMetrics
from ratesync.exceptions import RateLimiterAcquisitionError
from ratesync.schemas import LimiterReadOnlyConfig, LimiterState, RedisEngineConfig

logger = logging.getLogger(__name__)

# =============================================================================
# LUA SCRIPT FOR SLIDING WINDOW
# =============================================================================

# Atomic sliding window check and register
# Uses ZSET to store timestamps of requests
# Returns: [allowed (0/1), remaining, reset_in_seconds]
SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

-- Remove entries outside the window
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

-- Count current entries in window
local count = redis.call('ZCARD', key)

if count >= limit then
    -- Blocked - calculate reset time
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_at = oldest[2] and (oldest[2] + window) or (now + window)
    local reset_in = math.ceil(reset_at - now)
    if reset_in < 1 then reset_in = 1 end
    return {0, 0, reset_in}
end

-- Allowed - register request with unique timestamp
local member = now .. ':' .. math.random(1, 1000000)
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)

local remaining = limit - count - 1
return {1, remaining, window}
"""


class RedisSlidingWindowRateLimiter(RateLimiter):
    """Redis-based rate limiter using sliding window counter algorithm.

    Counts requests in a sliding time window and blocks when limit is reached.
    Ideal for auth protection scenarios like:
    - Login attempts (5 per 5 minutes)
    - Password reset requests (3 per hour)
    - Verification codes (10 per 10 minutes)

    Key differences from token bucket:
    - Counts exact requests in window (more predictable)
    - No burst allowance (stricter protection)
    - Reset is gradual as old requests expire

    Example:
        >>> limiter = RedisSlidingWindowRateLimiter(
        ...     url="redis://localhost:6379/0",
        ...     group_id="login",
        ...     limit=5,
        ...     window_seconds=300,
        ... )
        >>> await limiter.initialize()
        >>> # Returns True/False immediately (non-blocking)
        >>> allowed = await limiter.try_acquire(timeout=0)
    """

    def __init__(
        self,
        url: str,
        group_id: str,
        limit: int,
        window_seconds: int,
        db: int = 0,
        password: str | None = None,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        key_prefix: str = "rate_limit",
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> None:
        """Initialize sliding window rate limiter.

        Args:
            url: Redis connection URL
            group_id: Rate limit group identifier (used in key)
            limit: Maximum requests allowed in window
            window_seconds: Size of the sliding window in seconds
            db: Redis database number (0-15)
            password: Optional Redis password
            pool_min_size: Minimum connections in pool
            pool_max_size: Maximum connections in pool
            key_prefix: Prefix for Redis keys (namespace)
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Connection timeout in seconds
            default_timeout: Default timeout for acquire (0 = non-blocking)
            fail_closed: If True, blocks requests when Redis fails. If False (default),
                        allows requests when Redis fails (fail-open behavior).
        """
        if not REDIS_AVAILABLE:
            raise ImportError(
                "\nRedis engine requires redis to be installed.\n"
                "Install with: pip install 'rate-sync[redis]'"
            )

        if limit <= 0:
            raise ValueError(f"limit must be > 0, got: {limit}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got: {window_seconds}")

        self._url = url
        self._group_id = group_id
        self._limit = limit
        self._window_seconds = window_seconds
        self._db = db
        self._password = password
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._key_prefix = key_prefix
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._default_timeout = default_timeout
        self._fail_closed = fail_closed

        # TTL with margin for cleanup
        self._ttl = window_seconds + 60

        self._pool = None
        self._client = None
        self._initialized = False
        self._metrics = RateLimiterMetrics()
        self._script = None

    @property
    def _key(self) -> str:
        """Return Redis key for this limiter."""
        return f"{self._key_prefix}:{self._group_id}:window"

    async def initialize(self) -> None:
        """Initialize Redis connection and register Lua script."""
        if self._initialized:
            return

        if not REDIS_AVAILABLE:
            raise ImportError("redis is required for Redis engine")

        try:
            self._pool = ConnectionPool.from_url(
                self._url,
                db=self._db,
                password=self._password,
                max_connections=self._pool_max_size,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                decode_responses=False,
            )

            self._client = redis_asyncio.Redis(connection_pool=self._pool)
            await self._client.ping()

            self._script = self._client.register_script(SLIDING_WINDOW_SCRIPT)

            self._initialized = True
            logger.info(
                "Sliding window rate limiter initialized for '%s' (limit=%d, window=%ds)",
                self._group_id,
                self._limit,
                self._window_seconds,
            )

        except (OSError, TimeoutError, ConnectionError, ValueError) as e:
            logger.error(
                "Failed to initialize sliding window rate limiter '%s': %s",
                self._group_id,
                e,
            )
            if self._client:
                await self._client.aclose()
                self._client = None
            if self._pool:
                await self._pool.disconnect()
                self._pool = None
            raise

    async def acquire(self) -> None:
        """Wait until slot is available (blocking).

        For sliding window, this polls until a slot opens.
        Consider using try_acquire() with timeout instead.
        """
        if not self._initialized:
            raise RuntimeError(f"Rate limiter '{self._group_id}' not initialized")

        start_time = time.time()

        while True:
            now = time.time()
            result = await self._script(
                keys=[self._key],
                args=[self._limit, self._window_seconds, now, self._ttl],
            )

            allowed = bool(int(result[0]))

            if allowed:
                wait_ms = (time.time() - start_time) * 1000
                self._metrics.record_acquisition(wait_ms)
                return

            # Wait before retry
            reset_in = int(result[2])
            sleep_time = min(reset_in, 1.0)  # Max 1s between checks
            await asyncio.sleep(sleep_time)

    async def try_acquire(self, timeout: float = 0) -> bool:
        """Try to acquire slot within timeout.

        Args:
            timeout: Maximum wait time in seconds (0 = immediate check)

        Returns:
            True if allowed, False if blocked

        Raises:
            RateLimiterAcquisitionError: If fail_closed=True and backend fails
        """
        if not self._initialized:
            raise RuntimeError(f"Rate limiter '{self._group_id}' not initialized")

        start_time = time.time()

        try:
            while True:
                now = time.time()
                result = await self._script(
                    keys=[self._key],
                    args=[self._limit, self._window_seconds, now, self._ttl],
                )

                allowed = bool(int(result[0]))
                remaining = int(result[1])
                reset_in = int(result[2])

                if allowed:
                    wait_ms = (time.time() - start_time) * 1000
                    self._metrics.record_acquisition(wait_ms)
                    logger.debug(
                        "Sliding window '%s': allowed (remaining=%d, reset=%ds)",
                        self._group_id,
                        remaining,
                        reset_in,
                    )
                    return True

                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    self._metrics.record_timeout()
                    logger.debug(
                        "Sliding window '%s': blocked (limit=%d, reset=%ds)",
                        self._group_id,
                        self._limit,
                        reset_in,
                    )
                    return False

                # Wait before retry (min of reset_in, remaining timeout, or 1s)
                remaining_timeout = timeout - elapsed
                sleep_time = min(reset_in, remaining_timeout, 1.0)
                await asyncio.sleep(sleep_time)

        except (KeyError, OSError, ValueError) as e:
            if self._fail_closed:
                raise RateLimiterAcquisitionError(
                    f"Backend failure and fail_closed=True for group '{self._group_id}': {e}",
                    group_id=self._group_id,
                ) from e
            # Fail-open: log warning and allow request
            logger.warning(
                "Rate limiter backend failure for group '%s', allowing request (fail_open): %s",
                self._group_id,
                e,
            )
            return True

    async def release(self) -> None:
        """No-op for sliding window (no concurrency tracking)."""

    async def disconnect(self) -> None:
        """Close Redis connection pool."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except (OSError, ConnectionError, AttributeError, RuntimeError):
                pass
            finally:
                self._client = None

        if self._pool is not None:
            try:
                await self._pool.disconnect()
            except (OSError, ConnectionError, AttributeError, RuntimeError):
                pass
            finally:
                self._pool = None

    def get_metrics(self) -> RateLimiterMetrics:
        """Return rate limiter metrics."""
        return self._metrics

    def get_config(self) -> LimiterReadOnlyConfig:
        """Get this limiter's configuration (read-only).

        Returns:
            LimiterReadOnlyConfig with all configuration values.
        """
        return LimiterReadOnlyConfig(
            id=self._group_id,
            algorithm="sliding_window",
            store_id="redis",  # Redis backend
            rate_per_second=None,
            max_concurrent=None,
            timeout=self._default_timeout,
            limit=self._limit,
            window_seconds=self._window_seconds,
            fail_closed=self._fail_closed,
        )

    async def get_state(self) -> LimiterState:
        """Get current state without consuming slots.

        Returns:
            LimiterState with current usage and remaining slots.

        Raises:
            RuntimeError: If rate limiter wasn't initialized
            RateLimiterAcquisitionError: If fail_closed=True and backend fails
        """
        if not self._initialized:
            raise RuntimeError(f"Rate limiter '{self._group_id}' not initialized")

        try:
            # Check if client is available
            if self._client is None:
                raise ConnectionError("Redis client not initialized or disconnected")

            now = time.time()
            window_start = now - self._window_seconds

            # Count entries in current window (read-only operation)
            current_usage = await self._client.zcount(
                self._key,
                window_start,
                now,
            )

            remaining = max(0, self._limit - current_usage)
            allowed = remaining > 0

            # Calculate reset time (end of current window)
            # For sliding window, the reset is gradual as old entries expire
            # We calculate when the oldest entry will expire
            if current_usage >= self._limit:
                # Find oldest entry
                oldest_entries = await self._client.zrange(self._key, 0, 0, withscores=True)
                if oldest_entries:
                    oldest_timestamp = float(oldest_entries[0][1])
                    reset_at = int(oldest_timestamp + self._window_seconds)
                else:
                    reset_at = int(now + self._window_seconds)
            else:
                # Not at limit, reset is end of window
                reset_at = int(now + self._window_seconds)

            return LimiterState(
                allowed=allowed,
                remaining=remaining,
                reset_at=reset_at,
                current_usage=current_usage,
            )

        except (KeyError, OSError, ValueError) as e:
            if self._fail_closed:
                raise RateLimiterAcquisitionError(
                    f"Backend failure and fail_closed=True for group '{self._group_id}': {e}",
                    group_id=self._group_id,
                ) from e
            # Fail-open: return permissive state
            logger.warning(
                (
                    "Rate limiter backend failure for group '%s', "
                    "returning permissive state (fail_open): %s"
                ),
                self._group_id,
                e,
            )
            return LimiterState(
                allowed=True,
                remaining=self._limit,
                reset_at=int(time.time() + self._window_seconds),
                current_usage=0,
            )

    @property
    def group_id(self) -> str:
        """Return the group ID."""
        return self._group_id

    @property
    def rate_per_second(self) -> float | None:
        """Not applicable for sliding window."""
        return None

    @property
    def max_concurrent(self) -> int | None:
        """Not applicable for sliding window."""
        return None

    @property
    def limit(self) -> int:
        """Return the request limit."""
        return self._limit

    @property
    def window_seconds(self) -> int:
        """Return the window size in seconds."""
        return self._window_seconds

    @property
    def is_initialized(self) -> bool:
        """Check if initialized."""
        return self._initialized

    @property
    def default_timeout(self) -> float | None:
        """Return default timeout."""
        return self._default_timeout

    @property
    def fail_closed(self) -> bool:
        """Return the fail_closed behavior setting."""
        return self._fail_closed

    @classmethod
    def from_config(
        cls,
        config: object,
        group_id: str,
        limit: int | None = None,
        window_seconds: int | None = None,
        **kwargs,
    ) -> "RedisSlidingWindowRateLimiter":
        """Create limiter from configuration.

        Args:
            config: RedisEngineConfig instance
            group_id: Rate limit group identifier
            limit: Maximum requests in window
            window_seconds: Window size in seconds
            **kwargs: Additional parameters (timeout)

        Returns:
            Configured RedisSlidingWindowRateLimiter
        """
        if not isinstance(config, RedisEngineConfig):
            raise ValueError(f"Expected RedisEngineConfig, got {type(config)}")

        if limit is None or window_seconds is None:
            raise ValueError("limit and window_seconds are required for sliding_window")

        return cls(
            url=config.url,
            group_id=group_id,
            limit=limit,
            window_seconds=window_seconds,
            db=config.db,
            password=config.password,
            pool_min_size=config.pool_min_size,
            pool_max_size=config.pool_max_size,
            key_prefix=config.key_prefix,
            socket_timeout=config.socket_timeout,
            socket_connect_timeout=config.socket_connect_timeout,
            default_timeout=kwargs.get("timeout"),
            fail_closed=kwargs.get("fail_closed", False),
        )

    async def reset(self) -> None:
        """Reset this limiter's state (for testing).

        Deletes the Redis ZSET key for this specific limiter.
        This allows tests to run with fresh limiter state.

        Example:
            >>> limiter = RedisSlidingWindowRateLimiter(
            ...     url="redis://localhost:6379/0",
            ...     group_id="login",
            ...     limit=5,
            ...     window_seconds=300
            ... )
            >>> await limiter.initialize()
            >>> await limiter.try_acquire(timeout=0)
            >>> await limiter.reset()  # Fresh state
        """
        if not self._initialized or self._client is None:
            logger.warning(
                "Cannot reset limiter '%s': not initialized",
                self._group_id,
            )
            return

        try:
            await self._client.delete(self._key)
            logger.debug(
                "Reset sliding window limiter '%s' (deleted key: %s)",
                self._group_id,
                self._key,
            )
        except (OSError, ConnectionError, AttributeError) as e:
            logger.warning(
                "Failed to reset limiter '%s': %s",
                self._group_id,
                e,
            )

    async def reset_all(self) -> None:
        """Reset all rate limiter data in Redis (for testing).

        Deletes ALL keys matching the pattern 'rate_limit:*'.

        WARNING: This is destructive and will affect all limiters
        using this Redis instance, not just this limiter.
        Only use in isolated test environments.

        Example:
            >>> limiter = RedisSlidingWindowRateLimiter(
            ...     url="redis://localhost:6379/0",
            ...     group_id="login",
            ...     limit=5,
            ...     window_seconds=300
            ... )
            >>> await limiter.initialize()
            >>> await limiter.reset_all()  # Clears ALL rate_limit:* keys
        """
        if not self._initialized or self._client is None:
            logger.warning(
                "Cannot reset_all for limiter '%s': not initialized",
                self._group_id,
            )
            return

        try:
            pattern = f"{self._key_prefix}:*"
            cursor = 0
            deleted_count = 0

            while True:
                cursor, keys = await self._client.scan(
                    cursor,
                    match=pattern,
                    count=1000,
                )

                if keys:
                    deleted_count += await self._client.delete(*keys)

                if cursor == 0:
                    break

            logger.info(
                "Reset all rate limiters (deleted %d keys with pattern '%s')",
                deleted_count,
                pattern,
            )

        except (OSError, ConnectionError, AttributeError) as e:
            logger.warning(
                "Failed to reset_all for limiter '%s': %s",
                self._group_id,
                e,
            )
