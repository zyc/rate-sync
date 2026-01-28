"""Optional rate limiting middleware for FastAPI applications.

This module provides ASGI middleware for applying rate limiting to all
requests or specific paths without requiring endpoint-level dependencies.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ratesync import clone_limiter, get_limiter
from ratesync.contrib.fastapi.handlers import create_rate_limit_response
from ratesync.contrib.fastapi.headers import RateLimitResult, get_rate_limit_headers
from ratesync.contrib.fastapi.ip_utils import get_client_ip
from ratesync.exceptions import LimiterNotFoundError, RateLimiterAcquisitionError

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Lazy import for FastAPI/Starlette
try:
    from starlette.datastructures import MutableHeaders
    from starlette.requests import Request

    FASTAPI_AVAILABLE = True
except ImportError:
    Request = None  # type: ignore[misc, assignment]
    MutableHeaders = None  # type: ignore[misc, assignment]
    FASTAPI_AVAILABLE = False


logger = logging.getLogger(__name__)


# Type alias for path matcher
PathMatcher = Callable[[str], bool]


def _create_path_matcher(
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> PathMatcher:
    """Create a path matching function.

    Args:
        include_paths: List of regex patterns to include (if None, all paths).
        exclude_paths: List of regex patterns to exclude.

    Returns:
        Function that returns True if path should be rate limited.
    """
    include_patterns: list[re.Pattern[str]] | None = None
    exclude_patterns: list[re.Pattern[str]] = []

    if include_paths:
        include_patterns = [re.compile(p) for p in include_paths]

    if exclude_paths:
        exclude_patterns = [re.compile(p) for p in exclude_paths]

    def matcher(path: str) -> bool:
        # Check excludes first
        for pattern in exclude_patterns:
            if pattern.match(path):
                return False

        # If includes specified, must match
        if include_patterns:
            return any(p.match(path) for p in include_patterns)

        # No includes = match all
        return True

    return matcher


class RateLimitMiddleware:
    """ASGI middleware for rate limiting requests.

    This middleware applies rate limiting at the ASGI level, before
    requests reach endpoint handlers. It's useful for:
    - Applying a global rate limit to all endpoints
    - Rate limiting specific path patterns
    - Adding rate limit headers to all responses

    For endpoint-specific rate limiting, prefer using RateLimitDependency
    as it provides more control and better integration with FastAPI's DI.

    Attributes:
        app: The ASGI application to wrap.
        limiter_id: ID of the rate limiter to use.
        include_paths: Optional list of regex patterns to rate limit.
        exclude_paths: Optional list of regex patterns to exclude.
        fail_open: Allow requests on limiter errors.
        trusted_proxies: Trusted proxy networks for IP extraction.

    Example:
        Basic usage - rate limit all requests:

        >>> from fastapi import FastAPI
        >>> from ratesync.contrib.fastapi import RateLimitMiddleware
        >>>
        >>> app = FastAPI()
        >>> app.add_middleware(
        ...     RateLimitMiddleware,
        ...     limiter_id="global",
        ... )

        Rate limit specific paths:

        >>> app.add_middleware(
        ...     RateLimitMiddleware,
        ...     limiter_id="api",
        ...     include_paths=[r"^/api/.*"],
        ...     exclude_paths=[r"^/api/health$", r"^/api/metrics$"],
        ... )

        Combined with exception handler:

        >>> from ratesync.contrib.fastapi import (
        ...     RateLimitMiddleware,
        ...     RateLimitExceededError,
        ...     rate_limit_exception_handler,
        ... )
        >>>
        >>> app = FastAPI()
        >>> app.add_middleware(RateLimitMiddleware, limiter_id="global")
        >>> # Note: Middleware returns 429 directly, handler is for dependencies
        >>> app.add_exception_handler(
        ...     RateLimitExceededError,
        ...     rate_limit_exception_handler,
        ... )
    """

    def __init__(
        self,
        app: "ASGIApp",
        limiter_id: str,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        fail_open: bool = True,
        trusted_proxies: list[str] | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            app: The ASGI application to wrap.
            limiter_id: ID of the rate limiter configured in ratesync.
            include_paths: Optional list of regex patterns. Only paths
                matching at least one pattern will be rate limited.
                If None, all paths are included.
            exclude_paths: Optional list of regex patterns. Paths matching
                any pattern will NOT be rate limited. Exclusions take
                precedence over inclusions.
            fail_open: If True (default), allow requests when the limiter
                fails (e.g., Redis unavailable).
            trusted_proxies: Optional list of trusted proxy networks for
                IP extraction.
        """
        if not FASTAPI_AVAILABLE:
            raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

        self.app = app
        self.limiter_id = limiter_id
        self.fail_open = fail_open
        self.trusted_proxies = trusted_proxies
        self._path_matcher = _create_path_matcher(include_paths, exclude_paths)

    async def __call__(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        """Handle ASGI request.

        Args:
            scope: ASGI scope dictionary.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        # Only handle HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check if path should be rate limited
        path = scope.get("path", "/")
        if not self._path_matcher(path):
            await self.app(scope, receive, send)
            return

        # Create request object for IP extraction
        request = Request(scope, receive, send)

        # Get client identifier
        identifier = get_client_ip(request, self.trusted_proxies)

        # Check rate limit
        result = await self._check_limit(identifier)

        # If not allowed, return 429 response
        if not result.allowed:
            response = create_rate_limit_response(
                result=result,
                identifier=identifier,
                limiter_id=self.limiter_id,
            )
            await response(scope, receive, send)
            return

        # Allowed - call app and inject headers
        await self._call_with_headers(scope, receive, send, result)

    async def _check_limit(self, identifier: str) -> RateLimitResult:
        """Check rate limit for identifier.

        Args:
            identifier: Client identifier.

        Returns:
            RateLimitResult with check outcome.
        """
        dynamic_id = f"{self.limiter_id}:{identifier}"

        try:
            try:
                limiter = get_limiter(dynamic_id)
            except LimiterNotFoundError:
                try:
                    clone_limiter(self.limiter_id, dynamic_id)
                    limiter = get_limiter(dynamic_id)
                except LimiterNotFoundError:
                    logger.warning(
                        "Rate limiter '%s' not configured. Check rate-sync.toml",
                        self.limiter_id,
                    )
                    return self._unlimited_result()

            if not limiter.is_initialized:
                await limiter.initialize()

            allowed = await limiter.try_acquire(timeout=0)

            limit, window = self._get_limiter_params(limiter)
            metrics = limiter.get_metrics()

            if allowed:
                remaining = max(
                    0, limit - (metrics.total_acquisitions % limit) if limit > 0 else 999
                )
            else:
                remaining = 0

            return RateLimitResult(
                allowed=allowed,
                limit=limit,
                remaining=remaining,
                reset_in=float(window),
            )

        except RateLimiterAcquisitionError:
            limiter = get_limiter(dynamic_id)
            limit, window = self._get_limiter_params(limiter)
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_in=float(window),
            )

        except Exception as e:
            logger.error(
                "Rate limit check failed for '%s': %s",
                self.limiter_id,
                e,
            )
            if self.fail_open:
                return self._unlimited_result()
            raise

    def _get_limiter_params(self, limiter: object) -> tuple[int, int]:
        """Extract limit parameters from limiter."""
        if hasattr(limiter, "limit") and hasattr(limiter, "window_seconds"):
            return int(limiter.limit), int(limiter.window_seconds)  # type: ignore[attr-defined]

        if hasattr(limiter, "rate_per_second"):
            rps = limiter.rate_per_second  # type: ignore[attr-defined]
            if rps:
                return int(rps * 60), 60

        return 100, 60

    def _unlimited_result(self) -> RateLimitResult:
        """Create permissive result for fail-open."""
        return RateLimitResult(
            allowed=True,
            limit=1000,
            remaining=999,
            reset_in=60.0,
        )

    async def _call_with_headers(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
        result: RateLimitResult,
    ) -> None:
        """Call the app and inject rate limit headers into response.

        Args:
            scope: ASGI scope.
            receive: ASGI receive.
            send: ASGI send.
            result: Rate limit result with header values.
        """
        rate_limit_headers = get_rate_limit_headers(result)

        async def send_with_headers(message: "Message") -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=list(message.get("headers", [])))
                for name, value in rate_limit_headers.items():
                    headers.append(name, value)
                message["headers"] = headers.raw
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "RateLimitMiddleware",
]
