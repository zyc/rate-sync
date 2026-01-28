"""Exception handlers for rate limiting in FastAPI applications.

This module provides exception handlers for converting rate limit exceptions
into proper HTTP 429 Too Many Requests responses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ratesync.contrib.fastapi.headers import RateLimitResult, get_rate_limit_headers

if TYPE_CHECKING:
    pass

# Lazy import for FastAPI - allows graceful handling if not installed
try:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    FASTAPI_AVAILABLE = True
except ImportError:
    Request = None  # type: ignore[misc, assignment]
    JSONResponse = None  # type: ignore[misc, assignment]
    FASTAPI_AVAILABLE = False


logger = logging.getLogger(__name__)


class RateLimitExceededError(Exception):
    """Exception raised when a rate limit is exceeded.

    This exception should be raised by rate limiting code and will be
    converted to an HTTP 429 response by the exception handler.

    Attributes:
        identifier: Client identifier (IP, user ID, etc.) that hit the limit.
        limit: Maximum requests allowed per window.
        remaining: Requests remaining (typically 0 when this is raised).
        reset_at: Unix timestamp when the limit resets.
        retry_after: Seconds until the client should retry.
        limiter_id: Optional identifier of the rate limiter that was exceeded.

    Example:
        >>> raise RateLimitExceededError(
        ...     identifier="192.168.1.1",
        ...     limit=100,
        ...     remaining=0,
        ...     reset_at=1699999999,
        ...     retry_after=60,
        ... )
    """

    def __init__(
        self,
        identifier: str,
        limit: int,
        remaining: int,
        reset_at: int,
        retry_after: int,
        limiter_id: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            identifier: Client identifier (IP, user ID, etc.).
            limit: Maximum requests allowed per window.
            remaining: Requests remaining (typically 0).
            reset_at: Unix timestamp when the limit resets.
            retry_after: Seconds until the client should retry.
            limiter_id: Optional identifier of the rate limiter.
        """
        self.identifier = identifier
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        self.retry_after = retry_after
        self.limiter_id = limiter_id

        super().__init__(
            f"Rate limit exceeded for {identifier}: {remaining}/{limit} (retry in {retry_after}s)"
        )

    def to_result(self) -> RateLimitResult:
        """Convert to a RateLimitResult for header generation.

        Returns:
            RateLimitResult with allowed=False and the limit information.
        """
        return RateLimitResult(
            allowed=False,
            limit=self.limit,
            remaining=self.remaining,
            reset_in=float(self.retry_after),
        )


async def rate_limit_exception_handler(
    _request: "Request",
    exc: RateLimitExceededError,
) -> "JSONResponse":
    """FastAPI exception handler for rate limit errors.

    Converts RateLimitExceededError into an HTTP 429 Too Many Requests
    response with appropriate headers and error body.

    Response format:
        {
            "detail": [
                {
                    "type": "rate_limit_exceeded",
                    "msg": "Rate limit exceeded. Retry in X seconds.",
                    "context": {
                        "retry_after_seconds": X,
                        "limit": Y,
                        "identifier": "..."
                    }
                }
            ]
        }

    Headers:
        - X-RateLimit-Limit: Maximum requests per window
        - X-RateLimit-Remaining: Requests remaining (0)
        - X-RateLimit-Reset: Unix timestamp of reset
        - Retry-After: Seconds to wait

    Args:
        _request: The FastAPI/Starlette Request (unused).
        exc: The RateLimitExceededError exception.

    Returns:
        JSONResponse with status 429 and rate limit information.

    Example:
        Register the handler in your FastAPI application:

        >>> from fastapi import FastAPI
        >>> from ratesync.contrib.fastapi import (
        ...     RateLimitExceededError,
        ...     rate_limit_exception_handler,
        ... )
        >>>
        >>> app = FastAPI()
        >>> app.add_exception_handler(
        ...     RateLimitExceededError,
        ...     rate_limit_exception_handler,
        ... )
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

    # Get headers from result
    result = exc.to_result()
    headers = get_rate_limit_headers(result)

    # Build context dict
    context: dict[str, Any] = {
        "retry_after_seconds": exc.retry_after,
        "limit": exc.limit,
    }
    if exc.limiter_id:
        context["limiter"] = exc.limiter_id

    # Log the rate limit event
    logger.warning(
        "Rate limit exceeded: identifier=%s, limiter=%s, limit=%d, retry_after=%ds",
        exc.identifier,
        exc.limiter_id or "unknown",
        exc.limit,
        exc.retry_after,
    )

    return JSONResponse(
        status_code=429,
        content={
            "detail": [
                {
                    "type": "rate_limit_exceeded",
                    "msg": f"Rate limit exceeded. Retry in {exc.retry_after} seconds.",
                    "context": context,
                }
            ]
        },
        headers=headers,
    )


def create_rate_limit_response(
    result: RateLimitResult,
    message: str | None = None,
    identifier: str | None = None,
    limiter_id: str | None = None,
) -> "JSONResponse":
    """Create a 429 response manually from a RateLimitResult.

    Useful when you want to create a rate limit response directly without
    raising an exception, or in middleware.

    Args:
        result: The RateLimitResult with limit information.
        message: Optional custom error message.
        identifier: Optional client identifier for logging.
        limiter_id: Optional limiter identifier for the response.

    Returns:
        JSONResponse with status 429 and rate limit headers.

    Example:
        >>> result = RateLimitResult(
        ...     allowed=False,
        ...     limit=100,
        ...     remaining=0,
        ...     reset_in=60.0,
        ... )
        >>> response = create_rate_limit_response(result)
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

    headers = get_rate_limit_headers(result)

    if message is None:
        message = f"Rate limit exceeded. Retry in {result.retry_after} seconds."

    context: dict[str, Any] = {
        "retry_after_seconds": result.retry_after,
        "limit": result.limit,
    }
    if limiter_id:
        context["limiter"] = limiter_id

    if identifier:
        logger.warning(
            "Rate limit exceeded: identifier=%s, limiter=%s, limit=%d",
            identifier,
            limiter_id or "unknown",
            result.limit,
        )

    return JSONResponse(
        status_code=429,
        content={
            "detail": [
                {
                    "type": "rate_limit_exceeded",
                    "msg": message,
                    "context": context,
                }
            ]
        },
        headers=headers,
    )


__all__ = [
    "RateLimitExceededError",
    "rate_limit_exception_handler",
    "create_rate_limit_response",
]
