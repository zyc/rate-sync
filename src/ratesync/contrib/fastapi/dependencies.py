"""FastAPI dependency injection for rate limiting.

This module provides dependency classes for integrating rate limiting
into FastAPI endpoints using the dependency injection system.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ratesync import clone_limiter, get_limiter
from ratesync.contrib.fastapi.handlers import RateLimitExceededError
from ratesync.contrib.fastapi.headers import RateLimitResult, set_rate_limit_headers
from ratesync.contrib.fastapi.ip_utils import get_client_ip
from ratesync.exceptions import LimiterNotFoundError, RateLimiterAcquisitionError

if TYPE_CHECKING:
    pass

# Lazy import for FastAPI - allows graceful handling if not installed
try:
    from starlette.requests import Request
    from starlette.responses import Response

    FASTAPI_AVAILABLE = True
except ImportError:
    Request = None  # type: ignore[misc, assignment]
    Response = None  # type: ignore[misc, assignment]
    FASTAPI_AVAILABLE = False


logger = logging.getLogger(__name__)


# Type alias for identifier extractor functions
IdentifierExtractor = Callable[["Request"], str | Awaitable[str]]


def _default_identifier_extractor(request: "Request") -> str:
    """Default identifier extractor using client IP.

    Args:
        request: The FastAPI/Starlette request.

    Returns:
        Client IP address as the identifier.
    """
    return get_client_ip(request)


class RateLimitDependency:
    """FastAPI dependency for rate limiting endpoints.

    This class implements the FastAPI dependency injection protocol,
    allowing it to be used with Depends() in endpoint signatures.

    The dependency will:
    1. Extract a client identifier (default: IP address)
    2. Check the rate limit using the configured limiter
    3. Set rate limit headers on the response
    4. Raise RateLimitExceededError if the limit is exceeded

    Attributes:
        limiter_id: ID of the rate limiter to use (from rate-sync.toml).
        identifier_extractor: Callable to extract client identifier from request.
        timeout: Timeout for non-blocking acquire (0 = fail-fast).
        fail_open: If True, allow request on limiter errors (default: True).
        trusted_proxies: Optional list of trusted proxy networks for IP extraction.

    Example:
        Basic usage with default IP-based limiting:

        >>> from fastapi import Depends, FastAPI
        >>> from ratesync.contrib.fastapi import RateLimitDependency
        >>>
        >>> app = FastAPI()
        >>>
        >>> @app.get("/api/data")
        >>> async def get_data(
        ...     _: None = Depends(RateLimitDependency("api")),
        ... ):
        ...     return {"data": "value"}

        With custom identifier extraction (e.g., user ID):

        >>> async def get_user_id(request: Request) -> str:
        ...     # Extract user ID from JWT token, session, etc.
        ...     user_id = request.state.user_id  # Example
        ...     return str(user_id)
        >>>
        >>> @app.get("/api/user/data")
        >>> async def get_user_data(
        ...     _: None = Depends(RateLimitDependency(
        ...         "user_api",
        ...         identifier_extractor=get_user_id,
        ...     )),
        ... ):
        ...     return {"data": "value"}

        Combining IP + user identifier:

        >>> async def get_ip_and_user(request: Request) -> str:
        ...     ip = get_client_ip(request)
        ...     user_id = getattr(request.state, "user_id", "anonymous")
        ...     return f"{ip}:{user_id}"
        >>>
        >>> @app.post("/api/action")
        >>> async def do_action(
        ...     _: None = Depends(RateLimitDependency(
        ...         "action",
        ...         identifier_extractor=get_ip_and_user,
        ...     )),
        ... ):
        ...     return {"status": "ok"}
    """

    def __init__(
        self,
        limiter_id: str,
        identifier_extractor: IdentifierExtractor | None = None,
        timeout: float = 0,
        fail_open: bool = True,
        trusted_proxies: list[str] | None = None,
    ) -> None:
        """Initialize the rate limit dependency.

        Args:
            limiter_id: ID of the rate limiter configured in ratesync.
                This corresponds to a limiter defined in rate-sync.toml
                or configured programmatically.
            identifier_extractor: Optional callable that extracts the client
                identifier from the request. Can be sync or async. If None,
                uses the client IP address.
            timeout: Timeout in seconds for try_acquire. If 0 (default),
                the check is non-blocking and fails immediately if limit
                exceeded.
            fail_open: If True (default), allows the request to proceed when
                the rate limiter fails (e.g., Redis unavailable). If False,
                raises an error on limiter failures.
            trusted_proxies: Optional list of trusted proxy networks in CIDR
                notation for IP extraction. Only used if identifier_extractor
                is None.
        """
        if not FASTAPI_AVAILABLE:
            raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

        self.limiter_id = limiter_id
        self.identifier_extractor = identifier_extractor
        self.timeout = timeout
        self.fail_open = fail_open
        self.trusted_proxies = trusted_proxies

    async def __call__(
        self,
        request: "Request",
        response: "Response",
    ) -> None:
        """Execute the rate limit check.

        This method is called by FastAPI's dependency injection system.

        Args:
            request: The incoming HTTP request.
            response: The response object for setting headers.

        Raises:
            RateLimitExceededError: If the rate limit is exceeded.
            RuntimeError: If fail_open is False and the limiter fails.
        """
        # Extract identifier
        identifier = await self._get_identifier(request)

        # Check rate limit
        result = await self._check_limit(identifier)

        # Set headers on response
        set_rate_limit_headers(response, result)

        # Raise if not allowed
        if not result.allowed:
            raise RateLimitExceededError(
                identifier=identifier,
                limit=result.limit,
                remaining=result.remaining,
                reset_at=result.reset_at,
                retry_after=result.retry_after,
                limiter_id=self.limiter_id,
            )

    async def _get_identifier(self, request: "Request") -> str:
        """Extract the client identifier from the request.

        Args:
            request: The incoming HTTP request.

        Returns:
            Client identifier string.
        """
        if self.identifier_extractor is None:
            return get_client_ip(request, self.trusted_proxies)

        # Call the extractor (may be sync or async)
        result = self.identifier_extractor(request)

        # Handle async extractors
        if hasattr(result, "__await__"):
            return await result  # type: ignore[return-value]

        return result  # type: ignore[return-value]

    async def _check_limit(self, identifier: str) -> RateLimitResult:
        """Check the rate limit for the given identifier.

        Uses ratesync's clone_limiter to create per-identifier limiters
        dynamically from the base limiter configuration.

        Args:
            identifier: Client identifier to check.

        Returns:
            RateLimitResult with the check outcome.
        """
        # Create dynamic limiter ID for this identifier
        dynamic_id = f"{self.limiter_id}:{identifier}"

        try:
            # Get or create the limiter for this identifier
            try:
                limiter = get_limiter(dynamic_id)
            except LimiterNotFoundError:
                # Clone the base limiter for this identifier
                try:
                    clone_limiter(self.limiter_id, dynamic_id)
                    limiter = get_limiter(dynamic_id)
                except LimiterNotFoundError:
                    logger.warning(
                        "Rate limiter '%s' not configured. Check rate-sync.toml",
                        self.limiter_id,
                    )
                    return self._unlimited_result()

            # Initialize if needed
            if not limiter.is_initialized:
                await limiter.initialize()

            # Try to acquire slot (non-blocking)
            allowed = await limiter.try_acquire(timeout=self.timeout)

            # Get limit info from limiter
            limit, window = self._get_limiter_params(limiter)
            metrics = limiter.get_metrics()

            # Calculate remaining
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
            # Rate limit exceeded
            limiter = get_limiter(dynamic_id)
            limit, window = self._get_limiter_params(limiter)

            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_in=float(window),
            )

        except Exception as e:
            # Handle limiter errors
            logger.error(
                "Rate limit check failed for '%s': %s",
                self.limiter_id,
                e,
            )

            if self.fail_open:
                return self._unlimited_result()

            raise RuntimeError(f"Rate limiter '{self.limiter_id}' failed: {e}") from e

    def _get_limiter_params(self, limiter: object) -> tuple[int, int]:
        """Extract limit and window parameters from a limiter.

        Args:
            limiter: The rate limiter object.

        Returns:
            Tuple of (limit, window_seconds).
        """
        # Try sliding_window style params
        if hasattr(limiter, "limit") and hasattr(limiter, "window_seconds"):
            return int(limiter.limit), int(limiter.window_seconds)  # type: ignore[attr-defined]

        # Try token_bucket style params
        if hasattr(limiter, "rate_per_second"):
            rps = limiter.rate_per_second  # type: ignore[attr-defined]
            if rps:
                return int(rps * 60), 60

        # Default fallback
        return 100, 60

    def _unlimited_result(self) -> RateLimitResult:
        """Create a permissive result for fail-open scenarios.

        Returns:
            RateLimitResult that allows the request.
        """
        return RateLimitResult(
            allowed=True,
            limit=1000,
            remaining=999,
            reset_in=60.0,
        )


def rate_limit(
    limiter_id: str,
    identifier_extractor: IdentifierExtractor | None = None,
    timeout: float = 0,
    fail_open: bool = True,
) -> RateLimitDependency:
    """Factory function for creating rate limit dependencies.

    This is a convenience function that creates a RateLimitDependency
    instance. It can be used as a more readable alternative to the class.

    Args:
        limiter_id: ID of the rate limiter to use.
        identifier_extractor: Optional callable to extract client identifier.
        timeout: Timeout for non-blocking acquire.
        fail_open: Allow requests on limiter errors.

    Returns:
        Configured RateLimitDependency instance.

    Example:
        >>> from fastapi import Depends
        >>> from ratesync.contrib.fastapi import rate_limit
        >>>
        >>> @app.get("/api/data")
        >>> async def get_data(
        ...     _: None = Depends(rate_limit("api")),
        ... ):
        ...     return {"data": "value"}
    """
    return RateLimitDependency(
        limiter_id=limiter_id,
        identifier_extractor=identifier_extractor,
        timeout=timeout,
        fail_open=fail_open,
    )


__all__ = [
    "RateLimitDependency",
    "IdentifierExtractor",
    "rate_limit",
]
