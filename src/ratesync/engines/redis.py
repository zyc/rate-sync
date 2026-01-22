"""Redis-based distributed rate limiting engine.

This module implements rate limiting using Redis for state coordination across
multiple processes and containers. It uses Lua scripts for atomic operations
following the token bucket algorithm.

The engine requires Redis 5.0+ for Lua script support.

Requirements:
    pip install 'rate-sync[redis]'  or  pip install redis[asyncio]
"""

import asyncio
import logging
import time

# Lazy import: only fail if Redis engine is actually used
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
from ratesync.schemas import RedisEngineConfig, LimiterReadOnlyConfig, LimiterState

logger = logging.getLogger(__name__)

# =============================================================================
# LUA SCRIPTS FOR MAXIMUM PERFORMANCE
# =============================================================================

# Unified acquire script: handles both rate limiting AND concurrency in ONE call
# Returns: [success (0/1), wait_ms (milliseconds to wait, 0 if success), concurrent_count]
# Args: now, interval (or -1 if disabled), max_concurrent (or -1 if disabled), ttl
#
# PERFORMANCE OPTIMIZATION: Returns exact wait time so client can sleep precisely
# instead of polling. This reduces Redis round-trips from N polls to just 2.
ACQUIRE_SCRIPT = """
local rate_key = KEYS[1]
local concurrent_key = KEYS[2]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local max_concurrent = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

-- Step 1: Rate limiting check (if enabled)
local wait_ms = 0
if interval > 0 then
    local last_time = redis.call('GET', rate_key)
    if last_time then
        local elapsed = now - tonumber(last_time)
        if elapsed < interval then
            -- Rate limited - calculate exact wait time in milliseconds
            wait_ms = math.ceil((interval - elapsed) * 1000)
            return {0, wait_ms, -1}
        end
    end
end

-- Step 2: Concurrency check (if enabled)
local current_concurrent = 0
if max_concurrent > 0 then
    current_concurrent = tonumber(redis.call('GET', concurrent_key) or '0')
    if current_concurrent >= max_concurrent then
        -- Concurrency limited - return -1 as wait_ms to indicate polling needed
        return {0, -1, current_concurrent}
    end
end

-- Step 3: All checks passed - commit the acquisition atomically
if interval > 0 then
    redis.call('SET', rate_key, now, 'EX', ttl)
end

if max_concurrent > 0 then
    current_concurrent = redis.call('INCR', concurrent_key)
    redis.call('EXPIRE', concurrent_key, ttl)
end

return {1, 0, current_concurrent}
"""

# Release script: decrements concurrency counter atomically
# Returns: new concurrent count (or -1 if not using concurrency)
RELEASE_SCRIPT = """
local concurrent_key = KEYS[1]
local current = tonumber(redis.call('GET', concurrent_key) or '0')
if current > 0 then
    return redis.call('DECR', concurrent_key)
end
return 0
"""


class RedisRateLimiter(RateLimiter):
    """Redis-based rate limiter with distributed coordination.

    Uses Redis with Lua scripts to implement atomic rate limiting and
    concurrency limiting across multiple processes/containers.

    Supports two complementary limiting strategies:
    - **rate_per_second**: Controls throughput (token bucket algorithm)
    - **max_concurrent**: Controls parallelism (distributed counter)

    At least one strategy must be configured. Both can be used together
    for fine-grained control.

    PERFORMANCE OPTIMIZATIONS:
    - Single Lua script for both rate + concurrency (one round-trip)
    - Atomic operations (no race conditions)
    - Auto-expiring keys (no manual cleanup needed)
    - Connection pooling

    Example:
        >>> from ratesync.engines.redis import RedisRateLimiter
        >>> limiter = RedisRateLimiter(
        ...     url="redis://localhost:6379/0",
        ...     group_id="api",
        ...     rate_per_second=10.0,
        ...     max_concurrent=5,
        ... )
        >>> await limiter.initialize()
        >>> async with limiter.acquire_context():
        >>>     await http_client.get(url)
    """

    def __init__(
        self,
        url: str,
        group_id: str,
        rate_per_second: float | None = None,
        max_concurrent: int | None = None,
        db: int = 0,
        password: str | None = None,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        key_prefix: str = "rate_limit",
        timing_margin_ms: float = 10.0,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> None:
        """Initialize Redis rate limiter.

        Args:
            url: Redis connection URL
            group_id: Rate limit group identifier
            rate_per_second: Operations per second allowed (None = unlimited throughput)
            max_concurrent: Maximum simultaneous operations (None = unlimited concurrency)
            db: Redis database number (0-15)
            password: Optional Redis password
            pool_min_size: Minimum connections in pool
            pool_max_size: Maximum connections in pool
            key_prefix: Prefix for Redis keys (namespace)
            timing_margin_ms: Safety margin in ms for timing calculations
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Connection timeout in seconds
            default_timeout: Default timeout in seconds for acquire operations
            fail_closed: If True, blocks requests when Redis fails. If False (default),
                        allows requests when Redis fails (fail-open behavior).

        Raises:
            ValueError: If neither rate_per_second nor max_concurrent is specified
            ValueError: If rate_per_second <= 0 or max_concurrent <= 0
            ImportError: If redis is not installed
        """
        if not REDIS_AVAILABLE:
            raise ImportError(
                "\nRedis engine requires redis to be installed.\n"
                "Install with one of these commands:\n"
                "  pip install 'rate-sync[redis]'\n"
                "  pip install 'rate-sync[all]'\n"
                "  pip install 'redis[asyncio]'"
            )

        # Validate at least one limiting strategy is specified
        if rate_per_second is None and max_concurrent is None:
            raise ValueError("At least one of rate_per_second or max_concurrent must be specified")

        if rate_per_second is not None and rate_per_second <= 0:
            raise ValueError(f"rate_per_second must be > 0, got: {rate_per_second}")

        if max_concurrent is not None and max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got: {max_concurrent}")

        if not group_id or not group_id.strip():
            raise ValueError("group_id cannot be empty")

        self._url = url
        self._group_id = group_id.strip()
        self._rate = rate_per_second
        self._max_concurrent = max_concurrent
        self._db = db
        self._password = password
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._key_prefix = key_prefix
        self._timing_margin_s = timing_margin_ms / 1000.0
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._default_timeout = default_timeout
        self._fail_closed = fail_closed

        # Computed values
        self._interval = (1.0 / rate_per_second - self._timing_margin_s) if rate_per_second else -1

        self._pool = None
        self._client = None
        self._initialized = False
        self._metrics = RateLimiterMetrics()
        self._acquire_script = None
        self._release_script = None

    @property
    def _rate_key(self) -> str:
        """Return Redis key for rate limiting."""
        return f"{self._key_prefix}:{self._group_id}:rate"

    @property
    def _concurrent_key(self) -> str:
        """Return Redis key for concurrency limiting."""
        return f"{self._key_prefix}:{self._group_id}:concurrent"

    async def initialize(self) -> None:
        """Initialize Redis connection pool and register Lua scripts.

        This operation is idempotent - can be called multiple times.
        """
        if self._initialized:
            return

        if not REDIS_AVAILABLE:
            raise ImportError(
                "redis is required for Redis engine. Install it with: pip install 'redis[asyncio]'"
            )

        try:
            # Create connection pool
            self._pool = ConnectionPool.from_url(
                self._url,
                db=self._db,
                password=self._password,
                max_connections=self._pool_max_size,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                decode_responses=False,  # We work with bytes for performance
            )

            # Create Redis client
            self._client = redis_asyncio.Redis(connection_pool=self._pool)

            # Test connection
            await self._client.ping()

            # Register Lua scripts
            self._acquire_script = self._client.register_script(ACQUIRE_SCRIPT)
            self._release_script = self._client.register_script(RELEASE_SCRIPT)

            logger.info(
                "Created Redis connection pool for group '%s' (max=%d, url=%s, db=%d)",
                self._group_id,
                self._pool_max_size,
                self._url,
                self._db,
            )

            self._initialized = True

            logger.info(
                "Redis rate limiter initialized for group '%s' (rate=%s, max_concurrent=%s)",
                self._group_id,
                f"{self._rate}/s" if self._rate else "unlimited",
                self._max_concurrent if self._max_concurrent else "unlimited",
            )

        except (OSError, TimeoutError, ConnectionError, ValueError) as e:
            logger.error(
                "Failed to initialize Redis rate limiter for group '%s': %s",
                self._group_id,
                e,
            )
            # Cleanup on failure
            if self._client:
                await self._client.aclose()
                self._client = None
            if self._pool:
                await self._pool.disconnect()
                self._pool = None
            raise

    async def acquire(self) -> None:
        """Wait until slot is available for rate-limited operation.

        Blocks until it's possible to perform the operation while respecting
        the configured rate limit and concurrency limit. Uses Redis Lua scripts
        for atomic operations with maximum performance.

        IMPORTANT: If max_concurrent is configured, you MUST call release()
        when the operation is complete, or use acquire_context() instead.

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """
        if not self._initialized:
            raise RuntimeError(
                f"Rate limiter for group '{self._group_id}' not initialized. "
                "Call initialize() first."
            )

        start_time = time.time()

        # TTL for key cleanup (5x interval or 60 seconds for concurrency-only)
        ttl = int((1.0 / self._rate * 5) + 1) if self._rate else 60

        while True:
            now = time.time()

            # Execute unified Lua script atomically - ONE round-trip!
            result = await self._acquire_script(
                keys=[self._rate_key, self._concurrent_key],
                args=[
                    now,
                    self._interval,
                    self._max_concurrent if self._max_concurrent else -1,
                    ttl,
                ],
            )

            success = int(result[0])
            wait_ms = int(result[1])  # Exact wait time in ms, or -1 for concurrency limited
            concurrent_count = int(result[2])

            if success == 1:
                # Successfully acquired
                total_wait_ms = (time.time() - start_time) * 1000
                self._metrics.record_acquisition(total_wait_ms)

                if self._max_concurrent:
                    self._metrics.record_concurrent_acquire()

                logger.debug(
                    "Group '%s': Acquired (waited %.2fms, concurrent=%d)",
                    self._group_id,
                    total_wait_ms,
                    concurrent_count,
                )
                return

            # Failed to acquire - determine reason and wait appropriately
            if wait_ms > 0:
                # Rate limited - sleep for EXACT wait time (with small margin)
                # This eliminates polling! Only 2 Redis calls: check -> wait -> succeed
                sleep_time = (wait_ms / 1000.0) + 0.001  # Add 1ms margin for clock drift
                logger.debug(
                    "Group '%s': Rate limited, sleeping %.3fs (exact)",
                    self._group_id,
                    sleep_time,
                )
            else:
                # Concurrency limited (wait_ms == -1) - must poll since we don't know
                # when a slot will be released
                self._metrics.record_max_concurrent_reached()
                sleep_time = 0.005  # 5ms polling interval
                logger.debug(
                    "Group '%s': Concurrency limited (%d/%s), polling",
                    self._group_id,
                    concurrent_count,
                    self._max_concurrent or "∞",
                )

            await asyncio.sleep(sleep_time)

    async def release(self) -> None:
        """Release a concurrency slot after operation completes.

        This method MUST be called after acquire() when max_concurrent is set.
        If max_concurrent is None (unlimited), this is a no-op.

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """
        if not self._initialized:
            raise RuntimeError(
                f"Rate limiter for group '{self._group_id}' not initialized. "
                "Call initialize() first."
            )

        if self._max_concurrent is None:
            return  # No-op if concurrency limiting is disabled

        # Execute release script atomically
        new_count = await self._release_script(keys=[self._concurrent_key])
        self._metrics.record_concurrent_release()

        logger.debug(
            "Group '%s': Released (concurrent=%d)",
            self._group_id,
            new_count,
        )

    async def try_acquire(self, timeout: float = 0) -> bool:
        """Try to acquire slot without waiting indefinitely.

        Args:
            timeout: Maximum wait time in seconds (0 = don't wait)

        Returns:
            True if acquired slot within timeout, False otherwise

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """
        if not self._initialized:
            raise RuntimeError(
                f"Rate limiter for group '{self._group_id}' not initialized. "
                "Call initialize() first."
            )

        start_time = time.time()
        warned_at_70_pct = False

        try:
            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    self._metrics.record_timeout()
                    logger.debug(
                        "Group '%s': Timeout after %.3fs",
                        self._group_id,
                        elapsed,
                    )
                    return False

                # Warn at 70% of timeout to help diagnose contention issues
                if not warned_at_70_pct and timeout > 0 and elapsed >= timeout * 0.7:
                    warned_at_70_pct = True
                    logger.warning(
                        "Group '%s': Long wait for rate limiter (%.1fs of %.1fs timeout). "
                        "High contention or low rate limit configured.",
                        self._group_id,
                        elapsed,
                        timeout,
                    )

                now = time.time()
                ttl = int((1.0 / self._rate * 5) + 1) if self._rate else 60

                # Try to acquire
                result = await self._acquire_script(
                    keys=[self._rate_key, self._concurrent_key],
                    args=[
                        now,
                        self._interval,
                        self._max_concurrent if self._max_concurrent else -1,
                        ttl,
                    ],
                )

                success = int(result[0])
                wait_ms = int(result[1])  # Exact wait time or -1 for concurrency limited
                _ = int(result[2])  # concurrent_count - returned but not used

                if success == 1:
                    # Successfully acquired
                    total_wait_ms = (time.time() - start_time) * 1000
                    self._metrics.record_acquisition(total_wait_ms)
                    if self._max_concurrent:
                        self._metrics.record_concurrent_acquire()
                    return True

                # Calculate remaining time
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    self._metrics.record_timeout()
                    return False

                # Determine sleep time based on limit type
                if wait_ms > 0:
                    # Rate limited - use exact wait time (capped by remaining timeout)
                    sleep_time = min((wait_ms / 1000.0) + 0.001, remaining)
                else:
                    # Concurrency limited - poll
                    sleep_time = min(0.005, remaining)

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

    async def disconnect(self) -> None:
        """Close Redis connection pool."""
        if self._client is not None:
            try:
                await self._client.aclose()
                logger.info(
                    "Closed Redis client for group '%s'",
                    self._group_id,
                )
            except (OSError, ConnectionError, AttributeError, RuntimeError) as e:
                logger.warning(
                    "Error closing Redis client for group '%s': %s",
                    self._group_id,
                    e,
                )
            finally:
                self._client = None

        if self._pool is not None:
            try:
                await self._pool.disconnect()
                logger.info(
                    "Closed Redis connection pool for group '%s'",
                    self._group_id,
                )
            except (OSError, ConnectionError, AttributeError, RuntimeError) as e:
                logger.warning(
                    "Error closing Redis pool for group '%s': %s",
                    self._group_id,
                    e,
                )
            finally:
                self._pool = None

    def get_metrics(self) -> RateLimiterMetrics:
        """Return rate limiter observability metrics.

        Returns:
            Current snapshot of collected metrics
        """
        return self._metrics

    @property
    def group_id(self) -> str:
        """Return the group ID."""
        return self._group_id

    @property
    def rate_per_second(self) -> float | None:
        """Return the operations per second rate (None = unlimited)."""
        return self._rate

    @property
    def max_concurrent(self) -> int | None:
        """Return the maximum concurrent operations (None = unlimited)."""
        return self._max_concurrent

    @property
    def is_initialized(self) -> bool:
        """Check if the rate limiter was initialized."""
        return self._initialized

    @property
    def default_timeout(self) -> float | None:
        """Return the default timeout in seconds for acquire operations."""
        return self._default_timeout

    @property
    def fail_closed(self) -> bool:
        """Return the fail_closed behavior setting."""
        return self._fail_closed

    @classmethod
    def from_config(
        cls, config: object, group_id: str, rate_per_second: float | None = None, **kwargs
    ) -> "RedisRateLimiter":
        """Create Redis rate limiter from configuration.

        Args:
            config: Redis engine configuration (RedisEngineConfig)
            group_id: Rate limit group identifier
            rate_per_second: Operations per second allowed (None = unlimited)
            **kwargs: Additional runtime parameters (timeout, max_concurrent, etc.)

        Returns:
            Configured RedisRateLimiter instance

        Raises:
            ValueError: If config is not RedisEngineConfig

        Example:
            >>> config = RedisEngineConfig(url="redis://localhost:6379/0")
            >>> # Rate limiting only
            >>> limiter = RedisRateLimiter.from_config(config, "api", rate_per_second=10.0)
            >>>
            >>> # Concurrency limiting only
            >>> limiter = RedisRateLimiter.from_config(config, "api", max_concurrent=5)
            >>>
            >>> # Both (recommended for production)
            >>> limiter = RedisRateLimiter.from_config(
            ...     config, "api", rate_per_second=100.0, max_concurrent=50
            ... )
            >>> await limiter.initialize()
        """
        if not isinstance(config, RedisEngineConfig):
            raise ValueError(f"Expected RedisEngineConfig, got {type(config)}")

        timeout = kwargs.get("timeout", None)
        max_concurrent = kwargs.get("max_concurrent", None)
        fail_closed = kwargs.get("fail_closed", False)

        return cls(
            url=config.url,
            group_id=group_id,
            rate_per_second=rate_per_second,
            max_concurrent=max_concurrent,
            db=config.db,
            password=config.password,
            pool_min_size=config.pool_min_size,
            pool_max_size=config.pool_max_size,
            key_prefix=config.key_prefix,
            timing_margin_ms=config.timing_margin_ms,
            socket_timeout=config.socket_timeout,
            socket_connect_timeout=config.socket_connect_timeout,
            default_timeout=timeout,
            fail_closed=fail_closed,
        )

    async def reset(self) -> None:
        """Reset this limiter's state (for testing).

        Deletes the Redis keys for this specific limiter (rate and concurrent).
        This allows tests to run with fresh limiter state.

        Example:
            >>> limiter = RedisRateLimiter(
            ...     url="redis://localhost:6379/0",
            ...     group_id="test",
            ...     rate_per_second=10
            ... )
            >>> await limiter.initialize()
            >>> await limiter.acquire()
            >>> await limiter.reset()  # Fresh state
        """
        if not self._initialized or self._client is None:
            logger.warning(
                "Cannot reset limiter '%s': not initialized",
                self._group_id,
            )
            return

        try:
            # Delete both rate and concurrent keys
            await self._client.delete(self._rate_key, self._concurrent_key)
            logger.debug(
                "Reset limiter '%s' (deleted keys: %s, %s)",
                self._group_id,
                self._rate_key,
                self._concurrent_key,
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
            >>> limiter = RedisRateLimiter(
            ...     url="redis://localhost:6379/0",
            ...     group_id="test",
            ...     rate_per_second=10
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

    def get_config(self) -> LimiterReadOnlyConfig:
        """Get this limiter's configuration (read-only).

        Returns:
            LimiterReadOnlyConfig with all configuration values.
        """
        return LimiterReadOnlyConfig(
            id=self._group_id,
            algorithm="token_bucket",
            store_id="redis",
            rate_per_second=self._rate,
            max_concurrent=self._max_concurrent,
            timeout=self._default_timeout,
            limit=None,
            window_seconds=None,
            fail_closed=self._fail_closed,
        )

    async def get_state(self) -> LimiterState:
        """Get current state without consuming slots.

        For Redis token bucket, this provides an approximation based on
        the last rate limit timestamp and concurrent counter.

        Returns:
            LimiterState with current usage approximation.

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """
        if not self._initialized or self._client is None:
            raise RuntimeError(
                f"Rate limiter for group '{self._group_id}' not initialized. "
                "Call initialize() first."
            )

        try:
            now = time.time()

            # For token bucket, we estimate based on last timestamp
            # This is an approximation since exact state requires time-based calculation
            allowed = True
            remaining = 100  # Arbitrary large number for token bucket
            reset_at = int(now + 1.0)

            # Check if rate-limited
            if self._rate and self._interval > 0:
                last_time = await self._client.get(self._rate_key)
                if last_time:
                    last_time_float = float(last_time)
                    elapsed = now - last_time_float
                    if elapsed < self._interval:
                        allowed = False
                        reset_at = int(last_time_float + self._interval + 1)

            # Check concurrent count
            current_usage = 0
            if self._max_concurrent:
                concurrent_count = await self._client.get(self._concurrent_key)
                if concurrent_count:
                    current_usage = int(concurrent_count)
                    if current_usage >= self._max_concurrent:
                        allowed = False
                    remaining = max(0, self._max_concurrent - current_usage)

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
            return LimiterState(
                allowed=True,
                remaining=100,
                reset_at=int(now + 1.0),
                current_usage=0,
            )
