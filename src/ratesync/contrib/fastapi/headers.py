"""Rate limit header utilities for HTTP responses.

This module provides utilities for setting and managing rate limit headers
in HTTP responses following RFC 6585 and common conventions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Lazy import for Starlette - allows graceful handling if not installed
try:
    from starlette.responses import Response

    FASTAPI_AVAILABLE = True
except ImportError:
    Response = None  # type: ignore[misc, assignment]
    FASTAPI_AVAILABLE = False


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


def set_rate_limit_headers(
    response: "Response",
    result: RateLimitResult,
) -> None:
    """Set rate limit headers on an HTTP response.

    Sets the following headers:
    - X-RateLimit-Limit: Maximum requests allowed per window
    - X-RateLimit-Remaining: Requests remaining in current window
    - X-RateLimit-Reset: Unix timestamp when window resets

    If the request was rate limited (result.allowed is False), also sets:
    - Retry-After: Seconds until the client should retry

    Args:
        response: The Starlette/FastAPI Response object to modify.
        result: The RateLimitResult containing rate limit information.

    Example:
        >>> from fastapi import Response
        >>> from ratesync.contrib.fastapi.headers import (
        ...     RateLimitResult,
        ...     set_rate_limit_headers,
        ... )
        >>>
        >>> result = RateLimitResult(
        ...     allowed=True,
        ...     limit=100,
        ...     remaining=99,
        ...     reset_in=60.0,
        ... )
        >>> response = Response()
        >>> set_rate_limit_headers(response, result)
        >>> print(response.headers["X-RateLimit-Remaining"])
        '99'
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_at)

    if not result.allowed:
        response.headers["Retry-After"] = str(result.retry_after)


def get_rate_limit_headers(result: RateLimitResult) -> dict[str, str]:
    """Get rate limit headers as a dictionary.

    Useful for creating responses manually or for testing.

    Args:
        result: The RateLimitResult containing rate limit information.

    Returns:
        Dictionary of header name to value mappings.

    Example:
        >>> result = RateLimitResult(
        ...     allowed=False,
        ...     limit=100,
        ...     remaining=0,
        ...     reset_in=30.0,
        ... )
        >>> headers = get_rate_limit_headers(result)
        >>> headers["X-RateLimit-Remaining"]
        '0'
        >>> headers["Retry-After"]
        '30'
    """
    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
        "X-RateLimit-Reset": str(result.reset_at),
    }

    if not result.allowed:
        headers["Retry-After"] = str(result.retry_after)

    return headers


__all__ = [
    "RateLimitResult",
    "set_rate_limit_headers",
    "get_rate_limit_headers",
]
