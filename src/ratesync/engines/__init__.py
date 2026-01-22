"""Engine implementations for rate limiting.

This module contains concrete implementations of rate limiters
using different engines.

Available engines:
- NATS: Distributed rate limiting using NATS JetStream Key-Value Store
- Memory: In-memory rate limiting for local/development use
- PostgreSQL: Distributed rate limiting using PostgreSQL advisory locks
- Redis: Distributed rate limiting using Redis (token bucket algorithm)
- Redis Sliding Window: Request counting in time windows (auth protection)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ratesync.engines.memory import MemoryRateLimiter
from ratesync.engines.nats import NatsKvRateLimiter

if TYPE_CHECKING:
    from ratesync.engines.postgres import PostgresRateLimiter
    from ratesync.engines.redis import RedisRateLimiter
    from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

__all__ = ["NatsKvRateLimiter", "MemoryRateLimiter"]

# Optional PostgreSQL engine - lazy loaded to avoid ImportError
try:
    from ratesync.engines.postgres import PostgresRateLimiter

    __all__.append("PostgresRateLimiter")
    del PostgresRateLimiter  # Only needed for __all__
except ImportError:
    pass

# Optional Redis engines - lazy loaded to avoid ImportError
try:
    from ratesync.engines.redis import RedisRateLimiter
    from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

    __all__.extend(["RedisRateLimiter", "RedisSlidingWindowRateLimiter"])
    del RedisRateLimiter, RedisSlidingWindowRateLimiter  # Only needed for __all__
except ImportError:
    pass
