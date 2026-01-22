"""
Configuration schemas for rate limiter stores and limiters.

This module defines the configuration structures using dataclasses for type safety
and clear documentation. Each engine (NATS, Memory, etc.) defines its own config schema.

Also includes result types for rate limit checks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class NatsEngineConfig:
    """Configuration for NATS KV-based rate limiting engine.

    Attributes:
        url: NATS server URL (supports env var expansion via ${VAR})
        engine: Engine identifier (always "nats")
        token: Optional authentication token
        bucket_name: KV bucket name for storing rate limit state
        auto_create: If True, automatically creates KV bucket on initialize()
                    If False, requires pre-created bucket (safer for production)
        retry_interval: Seconds to wait between CAS retry attempts
        max_retries: Maximum number of CAS retry attempts
        timing_margin_ms: Safety margin in milliseconds for timing calculations
    """

    url: str
    engine: Literal["nats"] = "nats"
    token: str | None = None
    bucket_name: str = "rate_limits"
    auto_create: bool = False
    retry_interval: float = 0.05
    max_retries: int = 100
    timing_margin_ms: float = 10.0


@dataclass
class MemoryEngineConfig:
    """Configuration for in-memory (local) rate limiting engine.

    This engine uses local asyncio primitives and doesn't coordinate across processes.
    Useful for development, testing, or single-process applications.

    Attributes:
        engine: Engine identifier (always "memory")
    """

    engine: Literal["memory"] = "memory"


@dataclass
class PostgresEngineConfig:
    """Configuration for PostgreSQL-based rate limiting engine.

    Uses PostgreSQL for distributed coordination across processes/containers.
    Requires PostgreSQL 12+ for proper advisory lock support.

    Attributes:
        url: PostgreSQL connection URL (supports env var expansion via ${VAR})
              Format: postgresql://user:password@host:port/database
        engine: Engine identifier (always "postgres")
        table_name: Name of the table for storing rate limit state
        schema_name: PostgreSQL schema name (default: public)
        auto_create: If True, automatically creates table structure on initialize()
                    If False, requires pre-created table (safer for production)
        pool_min_size: Minimum number of connections in pool
        pool_max_size: Maximum number of connections in pool
        timing_margin_ms: Safety margin in milliseconds for timing calculations
    """

    url: str
    engine: Literal["postgres"] = "postgres"
    table_name: str = "rate_limiter_state"
    schema_name: str = "public"
    auto_create: bool = False
    pool_min_size: int = 2
    pool_max_size: int = 10
    timing_margin_ms: float = 10.0


@dataclass
class RedisEngineConfig:
    """Configuration for Redis-based rate limiting engine.

    Uses Redis for distributed coordination across processes/containers.
    Requires Redis 5.0+ for Lua script support.

    Attributes:
        url: Redis connection URL (supports env var expansion via ${VAR})
             Format: redis://[:password@]host[:port][/database]
        engine: Engine identifier (always "redis")
        db: Redis database number (0-15)
        password: Optional Redis password (can also be in URL)
        pool_min_size: Minimum number of connections in pool
        pool_max_size: Maximum number of connections in pool
        key_prefix: Prefix for Redis keys to namespace rate limiter data
        timing_margin_ms: Safety margin in milliseconds for timing calculations
        socket_timeout: Socket timeout in seconds for Redis operations
        socket_connect_timeout: Connection timeout in seconds
    """

    url: str
    engine: Literal["redis"] = "redis"
    db: int = 0
    password: str | None = None
    pool_min_size: int = 2
    pool_max_size: int = 10
    key_prefix: str = "rate_limit"
    timing_margin_ms: float = 10.0
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0


@dataclass
class LimiterConfig:
    """Configuration for a rate limiter.

    Limiters define the actual rate limiting parameters and reference a store
    that provides the coordination mechanism.

    Supports two algorithms:
    - **token_bucket** (default): Controls throughput using rate_per_second.
      Good for API rate limiting (e.g., 100 req/min).
    - **sliding_window**: Counts requests in a time window using limit/window_seconds.
      Good for auth protection (e.g., 5 login attempts per 5 minutes).

    Token bucket parameters:
    - **rate_per_second**: Operations per second allowed (None = unlimited throughput)
    - **max_concurrent**: Maximum simultaneous operations (None = unlimited concurrency)

    Sliding window parameters:
    - **limit**: Maximum requests allowed in the window
    - **window_seconds**: Size of the sliding window in seconds

    At least one limiting strategy must be specified based on the algorithm.

    Attributes:
        store: ID of the store to use for coordination
        algorithm: Rate limiting algorithm ("token_bucket" or "sliding_window")
        rate_per_second: Operations per second (token_bucket only)
        max_concurrent: Maximum concurrent operations (token_bucket only)
        limit: Maximum requests in window (sliding_window only)
        window_seconds: Window size in seconds (sliding_window only)
        timeout: Default timeout in seconds for acquire operations (None = wait indefinitely)
        fail_closed: If True, blocks requests when backend fails. If False (default),
                     allows requests when backend fails (fail-open behavior)

    Examples:
        >>> # Token bucket: 100 req/min
        >>> LimiterConfig(store="redis", rate_per_second=1.67)
        >>>
        >>> # Sliding window: 5 requests per 5 minutes (auth protection)
        >>> LimiterConfig(
        ...     store="redis",
        ...     algorithm="sliding_window",
        ...     limit=5,
        ...     window_seconds=300
        ... )
        >>>
        >>> # Token bucket with concurrency limit
        >>> LimiterConfig(store="redis", rate_per_second=100.0, max_concurrent=50)

    Raises:
        ValueError: If configuration is invalid for the selected algorithm
    """

    store: str
    algorithm: Literal["token_bucket", "sliding_window"] = "token_bucket"
    rate_per_second: float | None = None
    max_concurrent: int | None = None
    limit: int | None = None
    window_seconds: int | None = None
    timeout: float | None = None
    fail_closed: bool = False

    def __post_init__(self) -> None:
        """Validate configuration based on algorithm."""
        if self.algorithm == "sliding_window":
            # Sliding window requires limit and window_seconds
            if self.limit is None or self.window_seconds is None:
                raise ValueError(
                    "sliding_window algorithm requires both 'limit' and 'window_seconds'. "
                    f"Got limit={self.limit}, window_seconds={self.window_seconds}"
                )
            if self.limit <= 0:
                raise ValueError(f"limit must be > 0, got {self.limit}")
            if self.window_seconds <= 0:
                raise ValueError(f"window_seconds must be > 0, got {self.window_seconds}")
            # rate_per_second and max_concurrent are not used in sliding_window
        else:
            # Token bucket requires at least one of rate_per_second or max_concurrent
            if self.rate_per_second is None and self.max_concurrent is None:
                raise ValueError(
                    "token_bucket algorithm requires at least one of "
                    "'rate_per_second' or 'max_concurrent'. Got both as None."
                )
            if self.rate_per_second is not None and self.rate_per_second <= 0:
                raise ValueError(f"rate_per_second must be > 0, got {self.rate_per_second}")
            if self.max_concurrent is not None and self.max_concurrent <= 0:
                raise ValueError(f"max_concurrent must be > 0, got {self.max_concurrent}")


@dataclass(frozen=True, slots=True)
class LimiterReadOnlyConfig:
    """Read-only configuration snapshot of a rate limiter.

    This dataclass provides a read-only view of a limiter's configuration,
    useful for introspection and debugging. It cannot be modified after creation.

    Use RateLimiter.get_config() to obtain this object.

    Attributes:
        id: Identifier of the rate limiter
        algorithm: Rate limiting algorithm ("token_bucket" or "sliding_window")
        store_id: ID of the store used for coordination

        # Token bucket fields (None if algorithm != "token_bucket")
        rate_per_second: Operations per second (token_bucket only)
        max_concurrent: Maximum concurrent operations (token_bucket only)
        timeout: Default timeout for acquire operations

        # Sliding window fields (None if algorithm != "sliding_window")
        limit: Maximum requests in window (sliding_window only)
        window_seconds: Window size in seconds (sliding_window only)

        # Common fields
        fail_closed: If True, blocks requests when backend fails

    Example:
        >>> limiter = get_limiter("api")
        >>> config = limiter.get_config()
        >>> print(f"Algorithm: {config.algorithm}")
        >>> if config.algorithm == "sliding_window":
        ...     print(f"Limit: {config.limit} requests per {config.window_seconds}s")
        >>> else:
        ...     print(f"Rate: {config.rate_per_second} req/s")
    """

    id: str
    algorithm: str  # "token_bucket" | "sliding_window"
    store_id: str

    # Token bucket fields
    rate_per_second: float | None = None
    max_concurrent: int | None = None
    timeout: float | None = None

    # Sliding window fields
    limit: int | None = None
    window_seconds: int | None = None

    # Common fields
    fail_closed: bool = False


@dataclass(frozen=True, slots=True)
class LimiterState:
    """Current state snapshot of a rate limiter.

    This dataclass provides the current state of the limiter without
    consuming any slots. It's useful for client-side UX (e.g., showing
    "3 attempts remaining") and observability.

    Attributes:
        allowed: Whether the next acquire() would succeed
        remaining: Number of slots/requests remaining in current window
        reset_at: Unix timestamp (seconds) when the limit resets
        current_usage: Current usage count in the window

    Example:
        >>> limiter = get_limiter("login")
        >>> state = await limiter.get_state()
        >>> if state.remaining < 3:
        ...     logger.warning(f"Only {state.remaining} login attempts remaining")
        >>> print(f"Limit resets at: {state.reset_at}")
    """

    allowed: bool
    remaining: int
    reset_at: int  # Unix timestamp (seconds)
    current_usage: int


# Registry mapping engine names to their config classes
ENGINE_SCHEMAS: dict[str, type] = {
    "nats": NatsEngineConfig,
    "memory": MemoryEngineConfig,
    "postgres": PostgresEngineConfig,
    "redis": RedisEngineConfig,
}


# =============================================================================
# Result Types for Rate Limit Checks
# =============================================================================


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Result of a rate limit check.

    Contains information about whether the request was allowed and
    the current state of the rate limiter for the client.

    Attributes:
        allowed: Whether the request is allowed to proceed.
        limit: Maximum number of requests allowed in the window.
        remaining: Number of requests remaining in the current window.
        reset_in: Seconds until the rate limit window resets.
        limiter_id: Optional ID of the limiter that was checked.

    Example:
        >>> result = RateLimitResult(
        ...     allowed=True,
        ...     limit=100,
        ...     remaining=99,
        ...     reset_in=60.0,
        ... )
        >>> if result.allowed:
        ...     # Process request
        ...     pass
        >>> else:
        ...     # Return 429 Too Many Requests
        ...     pass
    """

    allowed: bool
    limit: int
    remaining: int
    reset_in: float  # seconds
    limiter_id: str | None = None

    @property
    def reset_at(self) -> int:
        """Unix timestamp when the rate limit window resets.

        Returns:
            Unix timestamp (seconds since epoch) of reset time.
        """
        return int(time.time() + self.reset_in)

    @property
    def retry_after(self) -> int:
        """Seconds to wait before retrying (for 429 responses).

        Returns:
            Integer seconds to include in Retry-After header.
        """
        return max(1, int(self.reset_in))


@dataclass(frozen=True, slots=True)
class MultiLimiterResult:
    """Result of checking multiple rate limiters.

    When checking multiple limiters (e.g., per-IP and per-user), this result
    contains the most restrictive outcome along with details from all limiters.

    Attributes:
        allowed: Whether ALL limiters allow the request.
        limit: Limit from the most restrictive limiter.
        remaining: Remaining from the most restrictive limiter.
        reset_in: Longest reset time (most restrictive).
        blocking_limiter_id: ID of the limiter that blocked (if any).
        results: Individual results from each limiter checked.

    Example:
        >>> # Check both IP and user limiters
        >>> result = await check_multi_limiters(["api:ip", "api:user"])
        >>> if not result.allowed:
        ...     print(f"Blocked by: {result.blocking_limiter_id}")
        ...     print(f"Retry after: {result.retry_after}s")
    """

    allowed: bool
    limit: int
    remaining: int
    reset_in: float  # seconds (longest of all limiters)
    blocking_limiter_id: str | None = None
    results: list[RateLimitResult] = field(default_factory=list)

    @property
    def reset_at(self) -> int:
        """Unix timestamp when the rate limit window resets.

        Returns:
            Unix timestamp (seconds since epoch) of reset time.
        """
        return int(time.time() + self.reset_in)

    @property
    def retry_after(self) -> int:
        """Seconds to wait before retrying (for 429 responses).

        Returns:
            Integer seconds to include in Retry-After header.
        """
        return max(1, int(self.reset_in))

    @classmethod
    def from_results(cls, results: list[RateLimitResult]) -> "MultiLimiterResult":
        """Create a MultiLimiterResult from a list of individual results.

        Determines the most restrictive outcome:
        - allowed: True only if ALL limiters allow
        - remaining: Minimum remaining across all limiters
        - reset_in: Maximum reset time (longest wait)
        - blocking_limiter_id: First limiter that blocked (if any)

        Args:
            results: List of RateLimitResult from each limiter.

        Returns:
            Combined MultiLimiterResult with most restrictive values.
        """
        if not results:
            # No limiters checked - allow by default
            return cls(
                allowed=True,
                limit=1000,
                remaining=999,
                reset_in=60.0,
                blocking_limiter_id=None,
                results=[],
            )

        # Find most restrictive values
        all_allowed = all(r.allowed for r in results)
        min_remaining = min(r.remaining for r in results)
        max_reset_in = max(r.reset_in for r in results)

        # Find blocking limiter (first one that blocked)
        blocking_id: str | None = None
        for r in results:
            if not r.allowed:
                blocking_id = r.limiter_id
                break

        # Use limit from the most restrictive (lowest remaining) limiter
        most_restrictive = min(results, key=lambda r: r.remaining)

        return cls(
            allowed=all_allowed,
            limit=most_restrictive.limit,
            remaining=min_remaining,
            reset_in=max_reset_in,
            blocking_limiter_id=blocking_id,
            results=list(results),
        )
