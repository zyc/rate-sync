"""rate-sync: Distributed rate limiter with coordinated synchronization.

This package provides tools for distributed rate limiting using coordination
via NATS Key-Value Store, in-memory, or other implementations.

Features:
- Abstract interface for multiple engine implementations
- Distributed coordination across processes/containers (NATS, PostgreSQL, Redis)
- Local in-memory rate limiting for development (Memory)
- Dual limiting strategies: rate limiting (req/sec) AND concurrency limiting (max_concurrent)
- Separate store and limiter configuration
- Configuration file support (TOML with env var expansion)
- Integrated metrics for observability
- Support for blocking acquire() and non-blocking try_acquire()
- Decorators for declarative rate limiting

Basic example (rate limiting):
    >>> from ratesync import configure_store, configure_limiter, acquire
    >>>
    >>> # Configure store and limiter with rate limit
    >>> configure_store("local", strategy="memory")
    >>> configure_limiter("api", store_id="local", rate_per_second=1.0, timeout=30)
    >>> await acquire("api")

Concurrency limiting example:
    >>> from ratesync import configure_store, configure_limiter, acquire
    >>>
    >>> # Limit to 5 concurrent operations (no rate limit)
    >>> configure_store("local", strategy="memory")
    >>> configure_limiter("db_pool", store_id="local", max_concurrent=5)
    >>>
    >>> # IMPORTANT: Use context manager to auto-release the slot
    >>> async with acquire("db_pool"):
    ...     result = await db.execute(query)

Combined rate + concurrency limiting (recommended for production):
    >>> from ratesync import configure_store, configure_limiter, acquire
    >>>
    >>> configure_store("prod", strategy="redis", url="redis://localhost:6379")
    >>> configure_limiter(
    ...     "external_api",
    ...     store_id="prod",
    ...     rate_per_second=10.0,   # Max 10 req/s
    ...     max_concurrent=5,       # Max 5 simultaneous requests
    ...     timeout=30.0
    ... )
    >>>
    >>> async with acquire("external_api"):
    ...     response = await http_client.get(url)

Decorator example:
    >>> from ratesync import rate_limited
    >>>
    >>> @rate_limited("payments")
    >>> async def fetch_payment_data():
    ...     return await http_client.get(url)
    >>>
    >>> @rate_limited("payments", timeout=5)
    >>> async def fetch_with_custom_timeout():
    ...     return await http_client.get(url)
"""

from ratesync.config import (
    configure_fastapi,
    get_fastapi_config,
    load_config,
)
from ratesync.core import RateLimiter, RateLimiterMetrics
from ratesync.decorators import rate_limited
from ratesync.domain.value_objects.identifier import (
    combine_identifiers,
    hash_identifier,
)
from ratesync.exceptions import (
    StoreNotFoundError,
    ConfigValidationError,
    LimiterNotFoundError,
    RateLimiterAcquisitionError,
    RateLimiterAlreadyConfiguredError,
    RateLimiterError,
    RateLimiterNotConfiguredError,
    RateLimiterNotInitializedError,
)
from ratesync.registry import (
    acquire,
    configure_store,
    configure_limiter,
    clone_limiter,
    get_limiter,
    get_or_clone_limiter,
    check_limiter,
    check_multi_limiters,
    initialize_all_limiters,
    initialize_limiter,
    list_stores,
    list_limiters,
    # Backward compatibility (deprecated)
    configure_rate_limiter,
    get_rate_limiter,
    initialize_rate_limiter,
)
from ratesync.schemas import (
    LimiterReadOnlyConfig,
    LimiterState,
    MultiLimiterResult,
    RateLimitResult,
)
from ratesync.engines.memory import MemoryRateLimiter
from ratesync.engines.nats import NatsKvRateLimiter

# Composite rate limiting
from ratesync.composite import (
    CompositeLimitCheck,
    CompositeRateLimiter,
    StrategyType,
)

# Testing utilities
from ratesync import testing

# PostgreSQL engine is lazy-loaded to avoid import error if asyncpg not installed
try:
    from ratesync.engines.postgres import PostgresRateLimiter

    _POSTGRES_AVAILABLE = True
except ImportError:
    PostgresRateLimiter = None
    _POSTGRES_AVAILABLE = False

# Redis engine is lazy-loaded to avoid import error if redis not installed
try:
    from ratesync.engines.redis import RedisRateLimiter

    _REDIS_AVAILABLE = True
except ImportError:
    RedisRateLimiter = None
    _REDIS_AVAILABLE = False

__version__ = "1.0.0"

__all__ = [
    # Version
    "__version__",
    # Core abstractions
    "RateLimiter",
    "RateLimiterMetrics",
    # Result types
    "RateLimitResult",
    "MultiLimiterResult",
    "LimiterReadOnlyConfig",
    "LimiterState",
    # Engine implementations
    "NatsKvRateLimiter",
    "MemoryRateLimiter",
    "PostgresRateLimiter",
    "RedisRateLimiter",
    # Composite rate limiting
    "CompositeRateLimiter",
    "CompositeLimitCheck",
    "StrategyType",
    # Configuration
    "load_config",
    "configure_store",
    "configure_limiter",
    "configure_fastapi",
    "get_fastapi_config",
    # Clone and check operations (for dynamic per-identifier limiting)
    "clone_limiter",
    "get_or_clone_limiter",
    "check_limiter",
    "check_multi_limiters",
    # Limiter operations
    "acquire",
    "initialize_limiter",
    "initialize_all_limiters",
    "get_limiter",
    # Listing
    "list_stores",
    "list_limiters",
    # Decorators
    "rate_limited",
    # Domain utilities (identifier handling)
    "hash_identifier",
    "combine_identifiers",
    # Testing utilities
    "testing",
    # Backward compatibility (deprecated)
    "configure_rate_limiter",
    "get_rate_limiter",
    "initialize_rate_limiter",
    # Exceptions
    "RateLimiterError",
    "RateLimiterNotConfiguredError",
    "RateLimiterNotInitializedError",
    "RateLimiterAcquisitionError",
    "RateLimiterAlreadyConfiguredError",
    "ConfigValidationError",
    "StoreNotFoundError",
    "LimiterNotFoundError",
]

# Auto-load configuration from rate-sync.toml if it exists
try:
    from ratesync.config import _auto_load_config

    _auto_load_config()
except (FileNotFoundError, ImportError, ValueError, OSError):
    # Silently ignore expected errors during auto-loading:
    # - FileNotFoundError: rate-sync.toml not found
    # - ImportError: required dependencies not available
    # - ValueError: invalid configuration
    # - OSError: file access issues
    # User can still configure programmatically
    pass
