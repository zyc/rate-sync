"""FastAPI integration for ratesync rate limiting.

This module provides seamless integration between ratesync and FastAPI,
including dependency injection, middleware, exception handlers, and utilities.

Installation:
    The FastAPI integration requires FastAPI/Starlette to be installed.
    ratesync itself is framework-agnostic and doesn't require FastAPI.

    pip install rate-sync[fastapi]

Quick Start:
    >>> from fastapi import Depends, FastAPI
    >>> from ratesync.contrib.fastapi import (
    ...     RateLimitDependency,
    ...     RateLimitExceededError,
    ...     rate_limit_exception_handler,
    ... )
    >>>
    >>> app = FastAPI()
    >>>
    >>> # Register exception handler
    >>> app.add_exception_handler(
    ...     RateLimitExceededError,
    ...     rate_limit_exception_handler,
    ... )
    >>>
    >>> # Use dependency for rate limiting
    >>> @app.get("/api/data")
    >>> async def get_data(
    ...     _: None = Depends(RateLimitDependency("api")),
    ... ):
    ...     return {"data": "value"}

Components:
    - **RateLimitDependency**: FastAPI dependency for endpoint rate limiting
    - **RateLimitMiddleware**: ASGI middleware for global/path rate limiting
    - **RateLimitExceededError**: Exception for rate limit violations
    - **rate_limit_exception_handler**: FastAPI exception handler for 429 responses
    - **RateLimitResult**: Dataclass with rate limit check results
    - **get_client_ip**: Utility for extracting client IP from requests
    - **set_rate_limit_headers**: Utility for setting X-RateLimit-* headers

Usage Patterns:

    1. **Dependency-based** (recommended for most use cases):
       Apply rate limiting to specific endpoints with full control.

       >>> @app.get("/api/resource")
       >>> async def get_resource(
       ...     _: None = Depends(RateLimitDependency("resource_limiter")),
       ... ):
       ...     return {"resource": "data"}

    2. **Middleware-based** (for global rate limiting):
       Apply rate limiting to all requests or path patterns.

       >>> app.add_middleware(
       ...     RateLimitMiddleware,
       ...     limiter_id="global",
       ...     exclude_paths=[r"^/health$", r"^/metrics$"],
       ... )

    3. **Custom identifier extraction**:
       Rate limit by user ID, API key, or custom identifier.

       >>> async def get_user_id(request: Request) -> str:
       ...     return request.headers.get("X-User-ID", get_client_ip(request))
       >>>
       >>> @app.get("/api/user/data")
       >>> async def get_user_data(
       ...     _: None = Depends(RateLimitDependency(
       ...         "user_api",
       ...         identifier_extractor=get_user_id,
       ...     )),
       ... ):
       ...     return {"data": "user_data"}

Configuration:
    Rate limiters should be configured in rate-sync.toml:

    [limiters.api]
    store = "redis"
    algorithm = "sliding_window"
    limit = 100
    window_seconds = 60

    [limiters.user_api]
    store = "redis"
    algorithm = "sliding_window"
    limit = 300
    window_seconds = 60
"""

from __future__ import annotations

# Check if FastAPI/Starlette is available
try:
    import starlette.requests  # noqa: F401

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

# Import components (they handle missing FastAPI gracefully)
from ratesync.contrib.fastapi.dependencies import (
    IdentifierExtractor,
    RateLimitDependency,
    rate_limit,
)
from ratesync.contrib.fastapi.handlers import (
    RateLimitExceededError,
    create_rate_limit_response,
    rate_limit_exception_handler,
)
from ratesync.contrib.fastapi.headers import (
    RateLimitResult,
    get_rate_limit_headers,
    set_rate_limit_headers,
)
from ratesync.contrib.fastapi.ip_utils import (
    DEFAULT_TRUSTED_PROXY_NETWORKS,
    combine_identifiers,
    get_client_ip,
    hash_identifier,
    is_private_ip,
    validate_ip,
)
from ratesync.contrib.fastapi.middleware import RateLimitMiddleware

# Composite rate limiting (lazy import to avoid circular deps)
try:
    from ratesync.contrib.fastapi.composite import CompositeRateLimitDependency

    COMPOSITE_AVAILABLE = True
except ImportError:
    CompositeRateLimitDependency = None  # type: ignore[misc, assignment]
    COMPOSITE_AVAILABLE = False

__all__ = [
    # Availability flag
    "FASTAPI_AVAILABLE",
    "COMPOSITE_AVAILABLE",
    # Dependencies
    "RateLimitDependency",
    "CompositeRateLimitDependency",
    "IdentifierExtractor",
    "rate_limit",
    # Handlers
    "RateLimitExceededError",
    "rate_limit_exception_handler",
    "create_rate_limit_response",
    # Headers
    "RateLimitResult",
    "set_rate_limit_headers",
    "get_rate_limit_headers",
    # IP utilities
    "get_client_ip",
    "validate_ip",
    "is_private_ip",
    "hash_identifier",
    "combine_identifiers",
    "DEFAULT_TRUSTED_PROXY_NETWORKS",
    # Middleware
    "RateLimitMiddleware",
]
