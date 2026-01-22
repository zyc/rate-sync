"""Global registry for managing backends and rate limiters.

This module provides a centralized registry with dual storage:
- Stores: reusable connection configurations (NATS, Memory, Redis, etc.)
- Limiters: rate limiter instances that reference a backend

This separation allows multiple limiters to share the same backend connection.
"""

import logging
from typing import Any

from ratesync.core import RateLimiter
from ratesync.engines.memory import MemoryRateLimiter
from ratesync.engines.nats import NatsKvRateLimiter
from ratesync.exceptions import (
    LimiterNotFoundError,
    RateLimiterAcquisitionError,
    StoreNotFoundError,
)
from ratesync.schemas import MultiLimiterResult, RateLimitResult
from ratesync.validation import validate_store_config

# Try to import PostgreSQL engine, but don't fail if asyncpg is not installed
try:
    from ratesync.engines.postgres import PostgresRateLimiter

    POSTGRES_ENGINE_AVAILABLE = True
except ImportError:
    PostgresRateLimiter = None  # type: ignore
    POSTGRES_ENGINE_AVAILABLE = False

# Try to import Redis engine, but don't fail if redis is not installed
try:
    from ratesync.engines.redis import RedisRateLimiter
    from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

    REDIS_ENGINE_AVAILABLE = True
except ImportError:
    RedisRateLimiter = None  # type: ignore
    RedisSlidingWindowRateLimiter = None  # type: ignore
    REDIS_ENGINE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Strategy name to implementation class mapping
ENGINE_CLASSES: dict[str, type[RateLimiter] | None] = {
    "nats": NatsKvRateLimiter,
    "memory": MemoryRateLimiter,
    "postgres": PostgresRateLimiter,  # May be None if asyncpg not installed
    "redis": RedisRateLimiter,  # May be None if redis not installed
}


class RateLimiterRegistry:
    """Registry for managing stores and rate limiters.

    The registry maintains:
    - Stores: configuration + optional runtime kwargs (e.g., jetstream connection)
    - Limiters: limiter_id → store_id mapping + rate limiter instance (lazy created)

    Example:
        >>> registry = RateLimiterRegistry()
        >>> # Configure store
        >>> registry.configure_backend("prod", strategy="nats", url="nats://prod")
        >>>
        >>> # Configure limiter with rate limiting only
        >>> registry.configure_limiter("payments", backend="prod", rate_per_second=1.0)
        >>>
        >>> # Configure limiter with concurrency limiting only
        >>> registry.configure_limiter("api", backend="prod", max_concurrent=10)
        >>>
        >>> # Configure limiter with both (recommended)
        >>> registry.configure_limiter(
        ...     "both", backend="prod", rate_per_second=10.0, max_concurrent=5
        ... )
        >>>
        >>> # Use limiter
        >>> await registry.acquire("payments")
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        # Store storage: store_id → (config_dataclass, runtime_kwargs)
        self._backends: dict[str, tuple[object, dict[str, Any]]] = {}

        # Limiter storage: limiter_id → (store_id, rate, max_concurrent, timeout, limiter,
        #                                algorithm, limit, window, fail_closed)
        self._limiters: dict[
            str,
            tuple[
                str,
                float | None,
                int | None,
                float | None,
                RateLimiter | None,
                str,
                int | None,
                int | None,
                bool,
            ],
        ] = {}

    def configure_store(self, store_id: str, strategy: str, **kwargs: Any) -> None:
        """Configure a backend with specific strategy.

        Args:
            store_id: Unique identifier for this backend (e.g., "prod_nats")
            strategy: Strategy name ("nats", "memory", etc.)
            **kwargs: Strategy-specific configuration parameters

        Raises:
            ConfigValidationError: If strategy unknown or parameters invalid

        Example:
            >>> registry.configure_backend(
            ...     "prod", strategy="nats", url="nats://prod.example.com"
            ... )
        """
        # Validate and create config dataclass
        config = validate_store_config(strategy, kwargs)

        # Separate runtime kwargs from config kwargs
        # Runtime kwargs are things like 'jetstream' that aren't in config
        config_field_names = {f.name for f in config.__dataclass_fields__.values()}
        runtime_kwargs = {k: v for k, v in kwargs.items() if k not in config_field_names}

        self._backends[store_id] = (config, runtime_kwargs)

        logger.info(
            "Store '%s' configured (strategy=%s)",
            store_id,
            strategy,
        )

    def configure_limiter(
        self,
        limiter_id: str,
        store_id: str,
        rate_per_second: float | None = None,
        max_concurrent: int | None = None,
        timeout: float | None = None,
        algorithm: str = "token_bucket",
        limit: int | None = None,
        window_seconds: int | None = None,
        fail_closed: bool = False,
    ) -> None:
        """Configure a rate limiter.

        Supports two algorithms:
        - token_bucket (default): Uses rate_per_second and/or max_concurrent
        - sliding_window: Uses limit and window_seconds

        Args:
            limiter_id: Unique identifier for this limiter (e.g., "payments")
            store_id: ID of backend to use for coordination
            rate_per_second: Operations per second (token_bucket only)
            max_concurrent: Max concurrent operations (token_bucket only)
            timeout: Default timeout in seconds for acquire operations
            algorithm: "token_bucket" or "sliding_window"
            limit: Max requests in window (sliding_window only)
            window_seconds: Window size in seconds (sliding_window only)
            fail_closed: If True, blocks requests when backend fails. If False (default),
                        allows requests when backend fails (fail-open behavior).

        Raises:
            StoreNotFoundError: If store_id doesn't exist
            ValueError: If configuration is invalid for the algorithm

        Example:
            >>> # Token bucket: 100 req/min
            >>> registry.configure_limiter("api", store_id="redis", rate_per_second=1.67)
            >>>
            >>> # Sliding window: 5 attempts per 5 minutes
            >>> registry.configure_limiter(
            ...     "login",
            ...     store_id="redis",
            ...     algorithm="sliding_window",
            ...     limit=5,
            ...     window_seconds=300
            ... )
        """
        if store_id not in self._backends:
            raise StoreNotFoundError(store_id)

        if algorithm == "sliding_window":
            # Validate sliding window parameters
            if limit is None or window_seconds is None:
                raise ValueError("sliding_window requires both 'limit' and 'window_seconds'")
            if limit <= 0:
                raise ValueError(f"limit must be > 0, got {limit}")
            if window_seconds <= 0:
                raise ValueError(f"window_seconds must be > 0, got {window_seconds}")
        else:
            # Validate token bucket parameters
            if rate_per_second is None and max_concurrent is None:
                raise ValueError(
                    "token_bucket requires at least one of 'rate_per_second' or 'max_concurrent'"
                )
            if rate_per_second is not None and rate_per_second <= 0:
                raise ValueError(f"rate_per_second must be > 0, got {rate_per_second}")
            if max_concurrent is not None and max_concurrent <= 0:
                raise ValueError(f"max_concurrent must be > 0, got {max_concurrent}")

        # Store limiter config with algorithm info
        # Format: (store_id, rate, max_concurrent, timeout, limiter,
        #          algorithm, limit, window, fail_closed)
        self._limiters[limiter_id] = (
            store_id,
            rate_per_second,
            max_concurrent,
            timeout,
            None,
            algorithm,
            limit,
            window_seconds,
            fail_closed,
        )

        # Build log message
        if algorithm == "sliding_window":
            logger.info(
                "Limiter '%s' configured (backend=%s, algorithm=sliding_window, "
                "limit=%d, window=%ds, timeout=%s)",
                limiter_id,
                store_id,
                limit,
                window_seconds,
                timeout,
            )
        else:
            rate_str = f"{rate_per_second:.2f} req/s" if rate_per_second else "unlimited"
            conc_str = f"{max_concurrent} concurrent" if max_concurrent else "unlimited"
            logger.info(
                "Limiter '%s' configured (backend=%s, algorithm=token_bucket, "
                "rate=%s, max_concurrent=%s, timeout=%s)",
                limiter_id,
                store_id,
                rate_str,
                conc_str,
                timeout,
            )

    def _get_or_create_limiter_sync(self, limiter_id: str) -> RateLimiter:
        """Get or lazily create rate limiter (synchronous, no initialization).

        Args:
            limiter_id: ID of the limiter

        Returns:
            Rate limiter instance (may not be initialized yet)

        Raises:
            LimiterNotFoundError: If limiter not configured
            StoreNotFoundError: If limiter's backend not found
        """
        if limiter_id not in self._limiters:
            raise LimiterNotFoundError(limiter_id)

        limiter_config = self._limiters[limiter_id]
        # Handle old format (5 elements), intermediate (8), and new (9)
        if len(limiter_config) == 5:
            store_id, rate, max_concurrent, timeout, limiter = limiter_config
            algorithm = "token_bucket"
            limit = None
            window_seconds = None
            fail_closed = False
        elif len(limiter_config) == 8:
            (store_id, rate, max_concurrent, timeout, limiter, algorithm, limit, window_seconds) = (
                limiter_config
            )
            fail_closed = False
        else:
            (
                store_id,
                rate,
                max_concurrent,
                timeout,
                limiter,
                algorithm,
                limit,
                window_seconds,
                fail_closed,
            ) = limiter_config

        # Return existing limiter if already created
        if limiter is not None:
            return limiter

        # Create new limiter
        if store_id not in self._backends:
            raise StoreNotFoundError(store_id)

        config, runtime_kwargs = self._backends[store_id]
        engine_name = config.engine

        # Check if engine is available
        if engine_name == "postgres" and PostgresRateLimiter is None:
            raise ImportError(
                "\nPostgreSQL engine is not available.\n"
                "Install with: pip install 'rate-sync[postgres]'"
            )

        if engine_name == "redis" and RedisRateLimiter is None:
            raise ImportError(
                "\nRedis engine is not available.\nInstall with: pip install 'rate-sync[redis]'"
            )

        # Create limiter based on algorithm
        if algorithm == "sliding_window":
            # Sliding window works with Redis and Memory backends
            if engine_name not in ("redis", "memory"):
                raise ValueError(
                    f"sliding_window algorithm requires Redis or Memory engine, got '{engine_name}'"
                )
            if engine_name == "redis" and RedisSlidingWindowRateLimiter is None:
                raise ImportError(
                    "\nRedis sliding window engine not available.\n"
                    "Install with: pip install 'rate-sync[redis]'"
                )

            # Choose sliding window implementation based on engine
            if engine_name == "redis":
                limiter = RedisSlidingWindowRateLimiter.from_config(
                    config,
                    group_id=limiter_id,
                    limit=limit,
                    window_seconds=window_seconds,
                    timeout=timeout,
                    fail_closed=fail_closed,
                    **runtime_kwargs,
                )
            else:  # memory
                limiter_class = ENGINE_CLASSES["memory"]
                if limiter_class is None:
                    raise ImportError("Memory engine is not available")

                limiter = limiter_class.from_config(
                    config,
                    group_id=limiter_id,
                    limit=limit,
                    window_seconds=window_seconds,
                    timeout=timeout,
                    fail_closed=fail_closed,
                    **runtime_kwargs,
                )

            logger.info(
                "Sliding window limiter created for '%s' (engine=%s, limit=%d, window=%ds)",
                limiter_id,
                engine_name,
                limit,
                window_seconds,
            )
        else:
            # Token bucket - use standard engine
            if engine_name not in ENGINE_CLASSES:
                available = ", ".join(ENGINE_CLASSES.keys())
                raise ValueError(f"Unknown engine '{engine_name}'. Available: {available}")

            limiter_class = ENGINE_CLASSES[engine_name]
            if limiter_class is None:
                raise ImportError(f"Engine '{engine_name}' is not available")

            limiter = limiter_class.from_config(
                config,
                group_id=limiter_id,
                rate_per_second=rate,
                max_concurrent=max_concurrent,
                timeout=timeout,
                fail_closed=fail_closed,
                **runtime_kwargs,
            )
            logger.info(
                "Token bucket limiter created for '%s' (engine=%s)",
                limiter_id,
                engine_name,
            )

        # Store limiter instance with full config
        self._limiters[limiter_id] = (
            store_id,
            rate,
            max_concurrent,
            timeout,
            limiter,
            algorithm,
            limit,
            window_seconds,
        )

        return limiter

    async def _get_or_create_limiter(self, limiter_id: str) -> RateLimiter:
        """Get or lazily create and initialize rate limiter.

        This method automatically initializes the limiter on first use if not already initialized.

        Args:
            limiter_id: ID of the limiter

        Returns:
            Initialized rate limiter instance

        Raises:
            LimiterNotFoundError: If limiter not configured
            StoreNotFoundError: If limiter's backend not found
        """
        limiter = self._get_or_create_limiter_sync(limiter_id)

        # Lazy initialization: initialize on first use if not yet initialized
        if not limiter.is_initialized:
            await limiter.initialize()
            logger.info(
                "Rate limiter auto-initialized for limiter '%s' (lazy initialization)",
                limiter_id,
            )

        return limiter

    async def acquire(self, limiter_id: str, timeout: float | None = None) -> None:
        """Acquire rate limit slot for limiter.

        Args:
            limiter_id: ID of the limiter
            timeout: Optional timeout override (uses limiter's default if None)

        Raises:
            LimiterNotFoundError: If limiter not configured
            RateLimiterNotInitializedError: If limiter not initialized
            RateLimiterAcquisitionError: If can't acquire within timeout
        """
        limiter = await self._get_or_create_limiter(limiter_id)

        # Determine timeout to use
        effective_timeout = timeout if timeout is not None else limiter.default_timeout

        if effective_timeout is not None:
            # Use try_acquire with timeout
            success = await limiter.try_acquire(timeout=effective_timeout)
            if not success:
                raise RateLimiterAcquisitionError(
                    f"Unable to acquire slot for limiter '{limiter_id}' within "
                    f"{effective_timeout}s",
                    group_id=limiter_id,
                )
        else:
            # Blocking acquire
            await limiter.acquire()

    async def initialize_limiter(self, limiter_id: str) -> None:
        """Initialize rate limiter.

        Note: This is optional. Limiters auto-initialize on first use.
        Call this explicitly if you want fail-fast behavior on startup.

        Args:
            limiter_id: ID of the limiter to initialize

        Raises:
            LimiterNotFoundError: If limiter not configured
        """
        limiter = await self._get_or_create_limiter(limiter_id)
        # _get_or_create_limiter already initializes, but call again
        # in case it was already initialized to ensure idempotency
        if not limiter.is_initialized:
            await limiter.initialize()

        logger.info("Rate limiter initialized for limiter '%s'", limiter_id)

    async def initialize_all_limiters(self) -> None:
        """Initialize all configured limiters."""
        for limiter_id in self._limiters:
            await self.initialize_limiter(limiter_id)

    def get_limiter(self, limiter_id: str) -> RateLimiter:
        """Get rate limiter instance.

        Note: The returned limiter may not be initialized yet.
        It will auto-initialize on first acquire() call.

        Args:
            limiter_id: ID of the limiter

        Returns:
            Rate limiter instance (may not be initialized)

        Raises:
            LimiterNotFoundError: If limiter not configured
        """
        return self._get_or_create_limiter_sync(limiter_id)

    def list_stores(self) -> dict[str, dict[str, Any]]:
        """List all configured backends with their info.

        Returns:
            Dict mapping store_id to backend info (strategy, params, etc.)

        Example:
            >>> registry.list_backends()
            {'prod_nats': {'strategy': 'nats', 'url': 'nats://...', 'initialized': False}}
        """
        result = {}

        for store_id, (config, runtime_kwargs) in self._backends.items():
            # Convert config dataclass to dict
            config_dict = {
                field.name: getattr(config, field.name)
                for field in config.__dataclass_fields__.values()
            }

            # Check if any limiter using this backend is initialized
            initialized = False
            for limiter_data in self._limiters.values():
                bid = limiter_data[0]
                limiter = limiter_data[4]
                if bid == store_id and limiter is not None:
                    initialized = limiter.is_initialized
                    break

            result[store_id] = {
                **config_dict,
                "initialized": initialized,
                "runtime_kwargs": list(runtime_kwargs.keys()),
            }

        return result

    def has_limiter(self, limiter_id: str) -> bool:
        """Check if a limiter exists in the registry.

        Args:
            limiter_id: ID of the limiter to check

        Returns:
            True if the limiter is configured, False otherwise
        """
        return limiter_id in self._limiters

    def get_limiter_config(
        self, limiter_id: str
    ) -> tuple[
        str,
        float | None,
        int | None,
        float | None,
        RateLimiter | None,
        str,
        int | None,
        int | None,
        bool,
    ]:
        """Get the configuration of a limiter.

        Args:
            limiter_id: ID of the limiter

        Returns:
            Tuple of (store_id, rate, max_concurrent, timeout, limiter,
                     algorithm, limit, window, fail_closed)

        Raises:
            LimiterNotFoundError: If limiter doesn't exist
        """
        if limiter_id not in self._limiters:
            raise LimiterNotFoundError(limiter_id)
        return self._limiters[limiter_id]

    def list_limiters(self) -> dict[str, dict[str, Any]]:
        """List all configured limiters with their info.

        Returns:
            Dict mapping limiter_id to limiter info (backend, rate, max_concurrent, etc.)

        Example:
            >>> registry.list_limiters()
            {'payments': {'backend': 'prod_nats', 'rate': 1.0, 'max_concurrent': 5}}
        """
        result = {}

        for limiter_id, limiter_data in self._limiters.items():
            # Handle old and new tuple formats
            if len(limiter_data) == 8:
                (store_id, rate, max_concurrent, timeout, limiter, algorithm, limit, window) = (
                    limiter_data
                )
                limiter_fail_closed = False
            else:
                (
                    store_id,
                    rate,
                    max_concurrent,
                    timeout,
                    limiter,
                    algorithm,
                    limit,
                    window,
                    limiter_fail_closed,
                ) = limiter_data
            result[limiter_id] = {
                "backend": store_id,
                "algorithm": algorithm,
                "rate_per_second": rate,
                "max_concurrent": max_concurrent,
                "limit": limit,
                "window_seconds": window,
                "timeout": timeout,
                "fail_closed": limiter_fail_closed,
                "initialized": limiter.is_initialized if limiter else False,
                "metrics": limiter.get_metrics() if limiter else None,
            }

        return result


# Global singleton
_registry = RateLimiterRegistry()


def configure_store(store_id: str, strategy: str, **kwargs: Any) -> None:
    """Configure a backend in the global registry.

    Args:
        store_id: Unique identifier for this backend
        strategy: Strategy name ("nats", "memory", etc.)
        **kwargs: Strategy-specific configuration parameters

    Example:
        >>> configure_store("prod", strategy="nats", url="nats://prod.example.com")
    """
    _registry.configure_store(store_id, strategy, **kwargs)


def configure_limiter(
    limiter_id: str,
    store_id: str,
    rate_per_second: float | None = None,
    max_concurrent: int | None = None,
    timeout: float | None = None,
    algorithm: str = "token_bucket",
    limit: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """Configure a rate limiter in the global registry.

    Supports two algorithms:
    - token_bucket (default): Uses rate_per_second and/or max_concurrent
    - sliding_window: Uses limit and window_seconds

    Args:
        limiter_id: Unique identifier for this limiter
        store_id: ID of backend to use
        rate_per_second: Operations per second allowed (token_bucket only)
        max_concurrent: Maximum simultaneous operations (token_bucket only)
        timeout: Default timeout in seconds
        algorithm: "token_bucket" or "sliding_window"
        limit: Max requests in window (sliding_window only)
        window_seconds: Window size in seconds (sliding_window only)

    Example:
        >>> # Token bucket: Rate limiting only
        >>> configure_limiter("payments", store_id="prod", rate_per_second=1.0)
        >>>
        >>> # Token bucket: Concurrency limiting only
        >>> configure_limiter("api", store_id="prod", max_concurrent=10)
        >>>
        >>> # Token bucket: Both (recommended)
        >>> configure_limiter("both", store_id="prod", rate_per_second=10.0, max_concurrent=5)
        >>>
        >>> # Sliding window: Auth protection
        >>> configure_limiter(
        ...     "login", store_id="redis", algorithm="sliding_window",
        ...     limit=5, window_seconds=300
        ... )
    """
    _registry.configure_limiter(
        limiter_id,
        store_id,
        rate_per_second,
        max_concurrent,
        timeout,
        algorithm,
        limit,
        window_seconds,
    )


def clone_limiter(
    source_id: str,
    new_id: str,
    *,
    rate_per_second: float | None = None,
    max_concurrent: int | None = None,
    timeout: float | None = None,
    limit: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """Clone configuration from existing limiter with optional overrides.

    Creates a new limiter with the same configuration as an existing one,
    optionally overriding parameters. Works with both token_bucket and sliding_window.

    Args:
        source_id: ID of limiter to clone from
        new_id: ID for new limiter
        rate_per_second: Override for rate (token_bucket, uses source's if None)
        max_concurrent: Override for max concurrent (token_bucket, uses source's if None)
        timeout: Override for timeout (uses source's timeout if None)
        limit: Override for limit (sliding_window, uses source's if None)
        window_seconds: Override for window (sliding_window, uses source's if None)

    Raises:
        LimiterNotFoundError: If source_id doesn't exist
        ValueError: If new_id already exists

    Example:
        >>> # Clone exact config
        >>> clone_limiter("payments", "payments-ip-1")
        >>>
        >>> # Clone with modified rate (token_bucket)
        >>> clone_limiter("payments", "payments-fast", rate_per_second=2.0)
        >>>
        >>> # Clone with modified limit (sliding_window)
        >>> clone_limiter("login", "login-premium", limit=10)
    """
    # Check if source exists
    if not _registry.has_limiter(source_id):
        raise LimiterNotFoundError(source_id)

    # Check if new_id already exists
    if _registry.has_limiter(new_id):
        raise ValueError(
            f"Limiter '{new_id}' already exists. Cannot clone to an existing limiter ID."
        )

    # Get source config (without limiter instance)
    (
        store_id,
        source_rate,
        source_max_concurrent,
        source_timeout,
        _,
        algorithm,
        source_limit,
        source_window,
        source_fail_closed,
    ) = _registry.get_limiter_config(source_id)

    # Use overrides or source values
    final_rate = rate_per_second if rate_per_second is not None else source_rate
    final_max_concurrent = max_concurrent if max_concurrent is not None else source_max_concurrent
    final_timeout = timeout if timeout is not None else source_timeout
    final_limit = limit if limit is not None else source_limit
    final_window = window_seconds if window_seconds is not None else source_window

    # Create new limiter with same config
    _registry.configure_limiter(
        new_id,
        store_id,
        final_rate,
        final_max_concurrent,
        final_timeout,
        algorithm,
        final_limit,
        final_window,
        source_fail_closed,
    )

    # Build log message based on algorithm
    if algorithm == "sliding_window":
        logger.info(
            "Limiter '%s' cloned from '%s' (algorithm=sliding_window, limit=%d, window=%ds)",
            new_id,
            source_id,
            final_limit,
            final_window,
        )
    else:
        rate_str = f"{final_rate:.2f} req/s" if final_rate else "unlimited"
        conc_str = f"{final_max_concurrent} concurrent" if final_max_concurrent else "unlimited"
        logger.info(
            "Limiter '%s' cloned from '%s' (rate=%s, max_concurrent=%s, timeout=%s)",
            new_id,
            source_id,
            rate_str,
            conc_str,
            final_timeout,
        )


async def get_or_clone_limiter(
    base_limiter_id: str,
    unique_id: str,
) -> RateLimiter:
    """Get or create a cloned limiter for a unique identifier.

    Creates a clone of a base limiter configuration for per-identifier rate limiting.
    The clone inherits all settings from the base limiter but operates independently.

    This is useful for rate limiting per-user, per-IP, per-email, etc.

    Args:
        base_limiter_id: ID of the template limiter from rate-sync.toml
        unique_id: Identifier to append (IP, user_id, email_hash, etc.)

    Returns:
        Initialized RateLimiter with ID: "{base_limiter_id}:{unique_id}"

    Raises:
        LimiterNotFoundError: If base_limiter_id doesn't exist

    Example:
        >>> # Rate limit per IP
        >>> limiter = await get_or_clone_limiter("login", "192.168.1.1")
        >>> allowed = await limiter.try_acquire(timeout=0)
        >>>
        >>> # Rate limit per user
        >>> limiter = await get_or_clone_limiter("api", str(user.id))
        >>> await limiter.acquire()
    """
    # Create composite limiter ID
    dynamic_id = f"{base_limiter_id}:{unique_id}"

    try:
        limiter = _registry.get_limiter(dynamic_id)
    except LimiterNotFoundError:
        # Clone base limiter for this identifier
        clone_limiter(base_limiter_id, dynamic_id)
        limiter = _registry.get_limiter(dynamic_id)

    # Initialize if needed
    if not limiter.is_initialized:
        await limiter.initialize()

    return limiter


async def check_limiter(
    limiter_id: str,
    acquire_if_allowed: bool = True,
    timeout: float = 0,
) -> RateLimitResult:
    """Check a rate limiter and optionally acquire a slot.

    Args:
        limiter_id: ID of the limiter to check (can include unique suffix)
        acquire_if_allowed: If True, acquires a slot when allowed (default)
        timeout: Timeout for try_acquire (0 = fail-fast)

    Returns:
        RateLimitResult with allowed status and rate limit info

    Raises:
        LimiterNotFoundError: If limiter_id doesn't exist

    Example:
        >>> result = await check_limiter("api:192.168.1.1")
        >>> if not result.allowed:
        ...     return Response(status_code=429)
    """
    limiter = await _registry._get_or_create_limiter(limiter_id)

    # Get limit config for result
    limit, window = _get_limiter_params(limiter)

    try:
        if acquire_if_allowed:
            allowed = await limiter.try_acquire(timeout=timeout)
        else:
            # Just check without acquiring (peek)
            # Note: For sliding_window, try_acquire with timeout=0 is essentially a peek
            # For token_bucket, we need a different approach
            allowed = await limiter.try_acquire(timeout=0)
            if allowed:
                # Release immediately since we only wanted to check
                await limiter.release()

        if allowed:
            metrics = limiter.get_metrics()
            remaining = max(0, limit - (metrics.total_acquisitions % limit) if limit > 0 else 999)
        else:
            remaining = 0

        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_in=float(window),
            limiter_id=limiter_id,
        )

    except RateLimiterAcquisitionError:
        return RateLimitResult(
            allowed=False,
            limit=limit,
            remaining=0,
            reset_in=float(window),
            limiter_id=limiter_id,
        )


async def check_multi_limiters(
    limiter_ids: list[str],
    acquire_if_allowed: bool = True,
    timeout: float = 0,
) -> MultiLimiterResult:
    """Check multiple rate limiters and return the most restrictive result.

    Useful for endpoints with multiple rate limiting policies
    (e.g., per-IP AND per-user limits).

    If acquire_if_allowed is True, acquires from ALL limiters only if
    ALL of them allow the request. Otherwise, none are acquired.

    Args:
        limiter_ids: List of limiter IDs to check
        acquire_if_allowed: If True and all allow, acquire from all limiters
        timeout: Timeout for each try_acquire (0 = fail-fast)

    Returns:
        MultiLimiterResult with combined outcome:
        - allowed: True only if ALL limiters allow
        - remaining: Minimum remaining across all
        - reset_in: Maximum reset time
        - blocking_limiter_id: First limiter that blocked (if any)

    Example:
        >>> # Check both IP-based and user-based limits
        >>> result = await check_multi_limiters([
        ...     "api:192.168.1.1",
        ...     "api:user:123",
        ... ])
        >>> if not result.allowed:
        ...     print(f"Blocked by: {result.blocking_limiter_id}")
        ...     print(f"Retry in: {result.retry_after}s")
    """
    if not limiter_ids:
        return MultiLimiterResult(
            allowed=True,
            limit=1000,
            remaining=999,
            reset_in=60.0,
            blocking_limiter_id=None,
            results=[],
        )

    # First pass: check all limiters without acquiring
    results: list[RateLimitResult] = []

    for limiter_id in limiter_ids:
        try:
            result = await check_limiter(
                limiter_id,
                acquire_if_allowed=False,  # Don't acquire yet
                timeout=timeout,
            )
            results.append(result)
        except LimiterNotFoundError:
            logger.warning(
                "Limiter '%s' not found during multi-check. Skipping.",
                limiter_id,
            )
            # Allow if limiter not configured (fail-open)
            results.append(
                RateLimitResult(
                    allowed=True,
                    limit=1000,
                    remaining=999,
                    reset_in=60.0,
                    limiter_id=limiter_id,
                )
            )

    # Combine results
    combined = MultiLimiterResult.from_results(results)

    # Second pass: if all allowed and acquire_if_allowed, actually acquire from all
    if combined.allowed and acquire_if_allowed:
        for limiter_id in limiter_ids:
            try:
                limiter = await _registry._get_or_create_limiter(limiter_id)
                await limiter.try_acquire(timeout=timeout)
            except (LimiterNotFoundError, RateLimiterAcquisitionError):
                # Already checked, should not happen
                pass

    return combined


def _get_limiter_params(limiter: RateLimiter) -> tuple[int, int]:
    """Extract limit and window parameters from a limiter.

    Args:
        limiter: The rate limiter object.

    Returns:
        Tuple of (limit, window_seconds).
    """
    # Try sliding_window style params
    if hasattr(limiter, "limit") and hasattr(limiter, "window_seconds"):
        limit = getattr(limiter, "limit", None)
        window_seconds = getattr(limiter, "window_seconds", None)
        if limit is not None and window_seconds is not None:
            return int(limit), int(window_seconds)

    # Try token_bucket style params
    if hasattr(limiter, "rate_per_second"):
        rps = getattr(limiter, "rate_per_second", None)
        if rps:
            return int(rps * 60), 60

    # Default fallback
    return 100, 60


class AcquireContext:
    """Helper class that allows acquire() to work as both awaitable and context manager.

    This enables two usage patterns:
    1. await acquire("api") - Acquires the slot (manual release for max_concurrent)
    2. async with acquire("api"): - Context manager with auto-release (RECOMMENDED)

    Example:
        >>> # Pattern 1: Simple acquire (manual release needed for concurrency)
        >>> await acquire("api")
        >>> # ... do work ...
        >>> # Remember: if max_concurrent is set, you need to release manually!
        >>>
        >>> # Pattern 2: Context manager (recommended - auto-releases)
        >>> async with acquire("api"):
        >>>     response = await http_client.get(url)
        >>>     # Concurrency slot automatically released on exit
    """

    def __init__(self, limiter_id: str, timeout: float | None = None):
        """Initialize the acquire context.

        Args:
            limiter_id: ID of the limiter to acquire
            timeout: Optional timeout override
        """
        self.limiter_id = limiter_id
        self.timeout = timeout
        self._limiter: RateLimiter | None = None
        self._context = None

    def __await__(self):
        """Allow using await acquire("api") syntax."""
        return self._acquire_only().__await__()

    async def _acquire_only(self) -> None:
        """Simple acquire without context manager."""
        await _registry.acquire(self.limiter_id, self.timeout)

    async def __aenter__(self) -> None:
        """Enter the context manager by acquiring from the limiter's context."""
        # Get or create limiter
        self._limiter = await _registry._get_or_create_limiter(self.limiter_id)

        # Determine effective timeout
        effective_timeout = (
            self.timeout if self.timeout is not None else self._limiter.default_timeout
        )

        # Use the limiter's context manager (handles release automatically)
        self._context = self._limiter.acquire_context(timeout=effective_timeout)
        await self._context.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager and release concurrency slot."""
        if self._context:
            return await self._context.__aexit__(exc_type, exc_val, exc_tb)


def acquire(limiter_id: str, timeout: float | None = None) -> AcquireContext:
    """Acquire rate limit slot for limiter from global registry.

    Can be used in two ways:
    1. As a simple awaitable: await acquire("api")
    2. As a context manager: async with acquire("api"):

    Args:
        limiter_id: ID of the limiter
        timeout: Optional timeout override

    Example:
        >>> # Simple acquire (just waits for slot)
        >>> await acquire("payments")  # Uses limiter's default timeout
        >>> await acquire("payments", timeout=5.0)  # Override with 5s timeout
        >>>
        >>> # Context manager (acquires and auto-releases)
        >>> async with acquire("api"):
        >>>     response = await http_client.get(url)
        >>>     # Slot automatically released on exit
    """
    return AcquireContext(limiter_id, timeout)


async def initialize_limiter(limiter_id: str) -> None:
    """Initialize rate limiter in global registry.

    Args:
        limiter_id: ID of the limiter to initialize

    Example:
        >>> await initialize_limiter("payments")
    """
    await _registry.initialize_limiter(limiter_id)


async def initialize_all_limiters() -> None:
    """Initialize all configured limiters in global registry."""
    await _registry.initialize_all_limiters()


def get_limiter(limiter_id: str) -> RateLimiter:
    """Get rate limiter instance from global registry.

    Args:
        limiter_id: ID of the limiter

    Returns:
        Rate limiter instance

    Example:
        >>> limiter = get_limiter("payments")
        >>> await limiter.acquire()
    """
    return _registry.get_limiter(limiter_id)


def list_stores() -> dict[str, dict[str, Any]]:
    """List all configured stores from global registry.

    Returns:
        Dict mapping store_id to store info

    Example:
        >>> list_stores()
        {'prod_nats': {'engine': 'nats', 'url': 'nats://...', ...}}
    """
    return _registry.list_stores()


def list_limiters() -> dict[str, dict[str, Any]]:
    """List all configured limiters from global registry.

    Returns:
        Dict mapping limiter_id to limiter info

    Example:
        >>> list_limiters()
        {'payments': {'backend': 'prod_nats', 'rate_per_second': 1.0, ...}}
    """
    return _registry.list_limiters()


# Keep old API for backward compatibility (will be deprecated)
def configure_rate_limiter(
    _name: str,
    _group_id: str,
    _rate_per_second: float,
    **_kwargs: Any,
) -> None:
    """DEPRECATED: Use configure_backend() + configure_group() instead.

    This function is kept for backward compatibility but will be removed
    in a future version.
    """
    logger.warning(
        "configure_rate_limiter() is deprecated. "
        "Use configure_backend() + configure_group() instead."
    )
    # This old API mixed backend and group config, so we can't fully support it
    # Just log a warning for now
    raise NotImplementedError(
        "Old API configure_rate_limiter() is no longer supported. "
        "Use configure_backend() + configure_group() instead."
    )


def get_rate_limiter(name: str) -> RateLimiter:
    """DEPRECATED: Use get_limiter() instead."""
    logger.warning("get_rate_limiter() is deprecated. Use get_limiter() instead.")
    return get_limiter(name)


async def initialize_rate_limiter(name: str) -> None:
    """DEPRECATED: Use initialize_limiter() instead."""
    logger.warning("initialize_rate_limiter() is deprecated. Use initialize_limiter() instead.")
    await initialize_limiter(name)


def list_configured_rate_limiters() -> list[str]:
    """DEPRECATED: Use list_limiters() instead."""
    logger.warning("list_configured_rate_limiters() is deprecated. Use list_limiters() instead.")
    return list(_registry.list_limiters().keys())
