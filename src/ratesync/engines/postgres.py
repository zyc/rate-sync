"""PostgreSQL-based distributed rate limiting engine.

This module implements rate limiting using PostgreSQL for state coordination across
multiple processes and containers. It uses optimistic locking with version numbers
to handle concurrent updates safely.

The engine requires PostgreSQL 12+ for proper support of row-level locking and
atomic operations.

Requirements:
    pip install 'rate-sync[postgres]'  or  pip install asyncpg
"""

import asyncio
import logging
import time

# Lazy import: only fail if PostgreSQL engine is actually used
try:
    import asyncpg as asyncpg_module

    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    asyncpg_module = None  # type: ignore

from ratesync.core import RateLimiter, RateLimiterMetrics
from ratesync.exceptions import RateLimiterAcquisitionError
from ratesync.schemas import PostgresEngineConfig

logger = logging.getLogger(__name__)


class PostgresRateLimiter(RateLimiter):
    """PostgreSQL-based rate limiter with distributed coordination.

    Uses a PostgreSQL table to store rate limit state and coordinate across
    multiple processes/containers. Implements optimistic locking with version
    numbers for safe concurrent updates.

    Supports two complementary limiting strategies:
    - **rate_per_second**: Controls throughput using timestamp tracking
    - **max_concurrent**: Controls parallelism using a distributed counter

    At least one strategy must be configured. Both can be used together
    for fine-grained control.

    Example:
        >>> from ratesync.engines.postgres import PostgresRateLimiter
        >>> limiter = PostgresRateLimiter(
        ...     connection_url="postgresql://user:pass@localhost/db",
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
        connection_url: str,
        group_id: str,
        rate_per_second: float | None = None,
        max_concurrent: int | None = None,
        table_name: str = "rate_limiter_state",
        schema_name: str = "public",
        auto_create: bool = False,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        timing_margin_ms: float = 10.0,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> None:
        """Initialize PostgreSQL rate limiter.

        Args:
            connection_url: PostgreSQL connection URL
            group_id: Rate limit group identifier
            rate_per_second: Operations per second allowed (None = unlimited throughput)
            max_concurrent: Maximum simultaneous operations (None = unlimited concurrency)
            table_name: Name of the table for storing state
            schema_name: PostgreSQL schema name
            auto_create: If True, creates table automatically on initialize()
                        If False, requires pre-created table (production recommended)
            pool_min_size: Minimum connections in pool
            pool_max_size: Maximum connections in pool
            timing_margin_ms: Safety margin in ms for timing calculations
            default_timeout: Default timeout in seconds for acquire operations
            fail_closed: If True, blocks requests when PostgreSQL fails. If False (default),
                        allows requests when PostgreSQL fails (fail-open behavior).

        Raises:
            ValueError: If neither rate_per_second nor max_concurrent is specified
            ValueError: If rate_per_second <= 0 or max_concurrent <= 0
            ImportError: If asyncpg is not installed
        """
        if not POSTGRES_AVAILABLE:
            raise ImportError(
                "\nPostgreSQL engine requires asyncpg to be installed.\n"
                "Install with one of these commands:\n"
                "  pip install 'rate-sync[postgres]'\n"
                "  pip install 'rate-sync[all]'\n"
                "  pip install asyncpg"
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

        self._connection_url = connection_url
        self._group_id = group_id.strip()
        self._rate = rate_per_second
        self._max_concurrent = max_concurrent
        self._table_name = table_name
        self._schema_name = schema_name
        self._auto_create = auto_create
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._timing_margin_s = timing_margin_ms / 1000.0
        self._default_timeout = default_timeout
        self._fail_closed = fail_closed

        # Computed values
        self._interval = 1.0 / rate_per_second if rate_per_second else None

        self._pool = None
        self._initialized = False
        self._metrics = RateLimiterMetrics()
        self._owned_pool = None

    @property
    def _full_table_name(self) -> str:
        """Return fully qualified table name."""
        return f"{self._schema_name}.{self._table_name}"

    async def initialize(self) -> None:
        """Initialize connection pool and optionally create table structure.

        If auto_create is True, creates the necessary table structure.
        If False, assumes table already exists.

        This operation is idempotent - can be called multiple times.
        """
        if self._initialized:
            return

        if not POSTGRES_AVAILABLE:
            raise ImportError(
                "asyncpg is required for PostgreSQL engine. Install it with: pip install asyncpg"
            )

        try:
            # Create connection pool
            self._pool = await asyncpg_module.create_pool(
                self._connection_url,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
            )
            self._owned_pool = self._pool

            logger.info(
                "Created PostgreSQL connection pool for group '%s' (min=%d, max=%d)",
                self._group_id,
                self._pool_min_size,
                self._pool_max_size,
            )

            # Create table if auto_create is enabled
            if self._auto_create:
                await self._create_table_if_not_exists()

            self._initialized = True

            logger.info(
                "PostgreSQL rate limiter initialized for group '%s' (rate=%s, max_concurrent=%s)",
                self._group_id,
                f"{self._rate}/s" if self._rate else "unlimited",
                self._max_concurrent if self._max_concurrent else "unlimited",
            )

        except (OSError, TimeoutError, ConnectionError, ValueError) as e:
            logger.error(
                "Failed to initialize PostgreSQL rate limiter for group '%s': %s",
                self._group_id,
                e,
            )
            # Cleanup on failure
            if self._pool:
                await self._pool.close()
                self._pool = None
                self._owned_pool = None
            raise

    async def _create_table_if_not_exists(self) -> None:
        """Create rate limiter table if it doesn't exist."""
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self._full_table_name} (
            group_id VARCHAR(255) PRIMARY KEY,
            last_acquisition_at TIMESTAMPTZ NOT NULL,
            concurrent_count INTEGER NOT NULL DEFAULT 0,
            version BIGINT NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_{self._table_name}_group
        ON {self._full_table_name}(group_id);
        """

        async with self._pool.acquire() as conn:
            await conn.execute(create_table_sql)

        logger.info(
            "Created table %s (if not exists) for rate limiting",
            self._full_table_name,
        )

    async def acquire(self) -> None:
        """Wait until slot is available for rate-limited operation.

        Blocks until it's possible to perform the operation while respecting
        the configured rate limit and concurrency limit. Uses PostgreSQL row
        locking and optimistic updates for coordination.

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

        while True:
            now_ts = time.time()

            async with self._pool.acquire() as conn:
                # Try to get current state
                row = await conn.fetchrow(
                    f"""
                    SELECT last_acquisition_at, concurrent_count, version
                    FROM {self._full_table_name}
                    WHERE group_id = $1
                    FOR UPDATE
                    """,
                    self._group_id,
                )

                if row is None:
                    # First acquisition for this group - insert new row
                    try:
                        await conn.execute(
                            f"""
                            INSERT INTO {self._full_table_name}
                            (group_id, last_acquisition_at, concurrent_count, version)
                            VALUES ($1, to_timestamp($2), $3, 1)
                            """,
                            self._group_id,
                            now_ts,
                            1 if self._max_concurrent else 0,
                        )
                        # Successfully acquired
                        wait_time_ms = (time.time() - start_time) * 1000
                        self._metrics.record_acquisition(wait_time_ms)
                        if self._max_concurrent:
                            self._metrics.record_concurrent_acquire()
                        logger.debug(
                            "Group '%s': First acquisition (waited %.2fms)",
                            self._group_id,
                            wait_time_ms,
                        )
                        return
                    except (KeyError, OSError, ValueError):
                        # Race condition - another process inserted first
                        continue

                # Check rate limiting
                rate_ok = True
                if self._interval is not None:
                    last_acq = row["last_acquisition_at"].timestamp()
                    elapsed = now_ts - last_acq
                    required_interval = self._interval - self._timing_margin_s
                    rate_ok = elapsed >= required_interval

                # Check concurrency limiting
                concurrent_ok = True
                current_concurrent = row["concurrent_count"]
                if self._max_concurrent is not None:
                    if current_concurrent >= self._max_concurrent:
                        concurrent_ok = False
                        self._metrics.record_max_concurrent_reached()

                if rate_ok and concurrent_ok:
                    # Can acquire - update atomically
                    new_concurrent = (
                        current_concurrent + 1 if self._max_concurrent else current_concurrent
                    )

                    result = await conn.execute(
                        f"""
                        UPDATE {self._full_table_name}
                        SET last_acquisition_at = to_timestamp($1),
                            concurrent_count = $2,
                            version = version + 1
                        WHERE group_id = $3 AND version = $4
                        """,
                        now_ts,
                        new_concurrent,
                        self._group_id,
                        row["version"],
                    )

                    if result == "UPDATE 1":
                        # Successfully acquired
                        wait_time_ms = (time.time() - start_time) * 1000
                        self._metrics.record_acquisition(wait_time_ms)
                        if self._max_concurrent:
                            self._metrics.record_concurrent_acquire()
                        logger.debug(
                            "Group '%s': Acquired (waited %.2fms, concurrent=%d)",
                            self._group_id,
                            wait_time_ms,
                            new_concurrent,
                        )
                        return

                    # Optimistic lock failed, retry
                    await asyncio.sleep(0.001)
                    continue

                # Need to wait
                if not rate_ok:
                    # Wait for rate interval
                    last_acq = row["last_acquisition_at"].timestamp()
                    wait_time = self._interval - (now_ts - last_acq)
                    await asyncio.sleep(max(wait_time, 0.01))
                else:
                    # Wait for concurrency slot
                    await asyncio.sleep(0.01)

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

        # Decrement counter atomically
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE {self._full_table_name}
                SET concurrent_count = GREATEST(concurrent_count - 1, 0),
                    version = version + 1
                WHERE group_id = $1
                """,
                self._group_id,
            )

            if result == "UPDATE 1":
                self._metrics.record_concurrent_release()
                logger.debug(
                    "Group '%s': Released concurrency slot",
                    self._group_id,
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

        try:
            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    self._metrics.record_timeout()
                    return False

                now_ts = time.time()

                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        f"""
                        SELECT last_acquisition_at, concurrent_count, version
                        FROM {self._full_table_name}
                        WHERE group_id = $1
                        FOR UPDATE NOWAIT
                        """,
                        self._group_id,
                    )

                    if row is None:
                        # First acquisition
                        try:
                            await conn.execute(
                                f"""
                                INSERT INTO {self._full_table_name}
                                (group_id, last_acquisition_at, concurrent_count, version)
                                VALUES ($1, to_timestamp($2), $3, 1)
                                """,
                                self._group_id,
                                now_ts,
                                1 if self._max_concurrent else 0,
                            )
                            wait_time_ms = (time.time() - start_time) * 1000
                            self._metrics.record_acquisition(wait_time_ms)
                            if self._max_concurrent:
                                self._metrics.record_concurrent_acquire()
                            return True
                        except (KeyError, OSError, ValueError):
                            continue

                    # Check rate limiting
                    rate_ok = True
                    if self._interval is not None:
                        last_acq = row["last_acquisition_at"].timestamp()
                        elapsed_since_last = now_ts - last_acq
                        required_interval = self._interval - self._timing_margin_s
                        rate_ok = elapsed_since_last >= required_interval

                    # Check concurrency limiting
                    concurrent_ok = True
                    current_concurrent = row["concurrent_count"]
                    if self._max_concurrent is not None:
                        concurrent_ok = current_concurrent < self._max_concurrent

                    if rate_ok and concurrent_ok:
                        new_concurrent = (
                            current_concurrent + 1 if self._max_concurrent else current_concurrent
                        )

                        result = await conn.execute(
                            f"""
                            UPDATE {self._full_table_name}
                            SET last_acquisition_at = to_timestamp($1),
                                concurrent_count = $2,
                                version = version + 1
                            WHERE group_id = $3 AND version = $4
                            """,
                            now_ts,
                            new_concurrent,
                            self._group_id,
                            row["version"],
                        )

                        if result == "UPDATE 1":
                            wait_time_ms = (time.time() - start_time) * 1000
                            self._metrics.record_acquisition(wait_time_ms)
                            if self._max_concurrent:
                                self._metrics.record_concurrent_acquire()
                            return True

                    # Wait a bit and retry
                    remaining = timeout - (time.time() - start_time)
                    if remaining > 0:
                        await asyncio.sleep(min(0.01, remaining))
                    else:
                        self._metrics.record_timeout()
                        return False

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
        """Close connection pool if owned by this instance."""
        if self._owned_pool is not None:
            try:
                await self._owned_pool.close()
                logger.info(
                    "Closed PostgreSQL connection pool for group '%s'",
                    self._group_id,
                )
            except (OSError, ConnectionError, AttributeError, RuntimeError) as e:
                logger.warning(
                    "Error closing PostgreSQL pool for group '%s': %s",
                    self._group_id,
                    e,
                )
            finally:
                self._owned_pool = None
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
    ) -> "PostgresRateLimiter":
        """Create PostgreSQL rate limiter from configuration.

        Args:
            config: PostgreSQL engine configuration (PostgresEngineConfig)
            group_id: Rate limit group identifier
            rate_per_second: Operations per second allowed (None = unlimited)
            **kwargs: Additional runtime parameters (timeout, max_concurrent)

        Returns:
            Configured PostgresRateLimiter instance

        Raises:
            ValueError: If config is not PostgresEngineConfig

        Example:
            >>> config = PostgresEngineConfig(url="postgresql://localhost/db")
            >>> # Rate limiting only
            >>> limiter = PostgresRateLimiter.from_config(config, "api", rate_per_second=1.0)
            >>>
            >>> # Concurrency limiting only
            >>> limiter = PostgresRateLimiter.from_config(config, "api", max_concurrent=10)
            >>>
            >>> # Both (recommended for production)
            >>> limiter = PostgresRateLimiter.from_config(
            ...     config, "api", rate_per_second=10.0, max_concurrent=5
            ... )
            >>> await limiter.initialize()
        """
        if not isinstance(config, PostgresEngineConfig):
            raise ValueError(f"Expected PostgresEngineConfig, got {type(config)}")

        timeout = kwargs.get("timeout", None)
        max_concurrent = kwargs.get("max_concurrent", None)
        fail_closed = kwargs.get("fail_closed", False)

        return cls(
            connection_url=config.url,
            group_id=group_id,
            rate_per_second=rate_per_second,
            max_concurrent=max_concurrent,
            table_name=config.table_name,
            schema_name=config.schema_name,
            auto_create=config.auto_create,
            pool_min_size=config.pool_min_size,
            pool_max_size=config.pool_max_size,
            timing_margin_ms=config.timing_margin_ms,
            default_timeout=timeout,
            fail_closed=fail_closed,
        )
