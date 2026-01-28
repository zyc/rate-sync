"""FastAPI dependency for composite rate limiting.

This module provides FastAPI integration for composite rate limiting,
enabling multi-layer protection patterns with minimal boilerplate.

Example:
    IP + credential protection for authentication endpoints:

    >>> from fastapi import Depends, FastAPI, Request
    >>> from ratesync.contrib.fastapi import (
    ...     CompositeRateLimitDependency,
    ...     get_client_ip,
    ...     hash_identifier,
    ... )
    >>>
    >>> app = FastAPI()
    >>>
    >>> async def get_verify_identifiers(
    ...     request: Request,
    ...     email: str,
    ... ) -> dict[str, str]:
    ...     ip = get_client_ip(request)
    ...     return {
    ...         "by_ip": ip,
    ...         "by_ip_email": f"{ip}:{hash_identifier(email)}",
    ...     }
    >>>
    >>> @app.post("/auth/verify-code")
    >>> async def verify_code(
    ...     email: str,
    ...     request: Request,
    ...     _: None = Depends(CompositeRateLimitDependency(
    ...         limiters={
    ...             "by_ip": "verify_code_ip",
    ...             "by_ip_email": "verify_code_email",
    ...         },
    ...         identifier_extractor=get_verify_identifiers,
    ...         strategy="most_restrictive",
    ...     )),
    ... ):
    ...     # ... verification logic
    ...     return {"status": "ok"}
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ratesync.composite import CompositeRateLimiter, RateLimitResult, StrategyType
from ratesync.contrib.fastapi.handlers import RateLimitExceededError

# Lazy import for FastAPI - allows graceful handling if not installed
try:
    from ratesync.contrib.fastapi.headers import RateLimitResult as FastAPIResult
    from ratesync.contrib.fastapi.headers import set_rate_limit_headers
    from starlette.requests import Request
    from starlette.responses import Response

    FASTAPI_AVAILABLE = True
except ImportError:
    Request = None  # type: ignore[misc, assignment]
    Response = None  # type: ignore[misc, assignment]
    FastAPIResult = None  # type: ignore[misc, assignment]
    set_rate_limit_headers = None  # type: ignore[misc, assignment]
    FASTAPI_AVAILABLE = False

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Type alias for identifier extractor functions
# Must return a dict matching the limiter keys
IdentifierExtractor = Callable[["Request"], dict[str, str] | Awaitable[dict[str, str]]]


def _convert_to_fastapi_result(result: RateLimitResult) -> "RateLimitResult":
    """Convert composite RateLimitResult to FastAPI RateLimitResult.

    This is a no-op since they have the same structure, but provides
    type compatibility with the FastAPI headers module.

    Args:
        result: Composite RateLimitResult instance.

    Returns:
        Same instance (compatible with FastAPI).
    """
    if FastAPIResult is None:
        raise ImportError("FastAPI not available")

    return FastAPIResult(
        allowed=result.allowed,
        limit=result.limit,
        remaining=result.remaining,
        reset_in=result.reset_in,
    )


class CompositeRateLimitDependency:
    """FastAPI dependency for composite rate limiting.

    This class implements the FastAPI dependency injection protocol,
    allowing it to be used with Depends() for multi-layer rate limiting.

    The dependency will:
    1. Extract identifiers using the provided extractor function
    2. Check all limiters using the composite strategy
    3. Set rate limit headers based on most restrictive limit
    4. Raise RateLimitExceededError if any limit is exceeded

    Attributes:
        composite: The CompositeRateLimiter instance.
        identifier_extractor: Callable to extract identifiers from request.
        fail_open: If True, allow request on limiter errors (default: True).

    Example:
        Basic IP + credential protection:

        >>> async def get_login_identifiers(
        ...     request: Request,
        ...     email: str,
        ... ) -> dict[str, str]:
        ...     ip = get_client_ip(request)
        ...     return {
        ...         "by_ip": ip,
        ...         "by_credential": f"{ip}:{hash_identifier(email)}",
        ...     }
        >>>
        >>> @app.post("/auth/login")
        >>> async def login(
        ...     email: str,
        ...     password: str,
        ...     request: Request,
        ...     _: None = Depends(CompositeRateLimitDependency(
        ...         limiters={
        ...             "by_ip": "login_ip",
        ...             "by_credential": "login_credential",
        ...         },
        ...         identifier_extractor=get_login_identifiers,
        ...     )),
        ... ):
        ...     # ... login logic
        ...     return {"token": "..."}

        Triple-layer protection:

        >>> async def get_api_identifiers(
        ...     request: Request,
        ...     user_id: int,
        ... ) -> dict[str, str]:
        ...     return {
        ...         "global": "api",
        ...         "per_user": str(user_id),
        ...         "per_endpoint": f"{user_id}:create",
        ...     }
        >>>
        >>> @app.post("/api/resource")
        >>> async def create_resource(
        ...     user_id: int,
        ...     request: Request,
        ...     _: None = Depends(CompositeRateLimitDependency(
        ...         limiters={
        ...             "global": "api_global",
        ...             "per_user": "api_user",
        ...             "per_endpoint": "api_endpoint",
        ...         },
        ...         identifier_extractor=get_api_identifiers,
        ...         strategy="all_must_pass",
        ...     )),
        ... ):
        ...     # ... create logic
        ...     return {"id": 123}
    """

    def __init__(
        self,
        limiters: dict[str, str],
        identifier_extractor: IdentifierExtractor,
        strategy: StrategyType = "most_restrictive",
        timeout: float | None = None,
        fail_open: bool = True,
    ) -> None:
        """Initialize composite dependency.

        Args:
            limiters: Mapping of check names to limiter IDs.
                Example: {"by_ip": "verify_code_ip", "by_email": "verify_code_email"}
            identifier_extractor: Async function to extract identifiers from request.
                Must return dict matching limiter keys. Can be sync or async.
            strategy: How to combine results (default: most_restrictive).
                - "most_restrictive": All must pass, reports most restrictive
                - "all_must_pass": All must pass, fails on first failure
                - "any_must_pass": At least one must pass
            timeout: Optional timeout override for all checks (seconds).
                If None, uses each limiter's default timeout.
            fail_open: If True (default), allows the request to proceed when
                any rate limiter fails (e.g., Redis unavailable). If False,
                raises an error on limiter failures.

        Raises:
            RuntimeError: If FastAPI/Starlette is not installed.
            ValueError: If limiters dict is empty.
        """
        if not FASTAPI_AVAILABLE:
            raise RuntimeError("FastAPI/Starlette not installed. Install with: pip install rate-sync[fastapi]")

        self.composite = CompositeRateLimiter(limiters, strategy)
        self.identifier_extractor = identifier_extractor
        self.timeout = timeout
        self.fail_open = fail_open

    async def __call__(
        self,
        request: "Request",
        response: "Response",
    ) -> None:
        """Execute the composite rate limit check.

        This method is called by FastAPI's dependency injection system.

        Args:
            request: The incoming HTTP request.
            response: The response object for setting headers.

        Raises:
            RateLimitExceededError: If any limit is exceeded.
            RuntimeError: If fail_open is False and any limiter fails.
        """
        if set_rate_limit_headers is None:
            raise ImportError("FastAPI not available")

        try:
            # Extract identifiers
            identifiers = await self._get_identifiers(request)

            # Check composite limit
            result = await self.composite.check(identifiers, timeout=self.timeout)

            # Convert to FastAPI result and set headers
            fastapi_result = _convert_to_fastapi_result(result.most_restrictive)
            set_rate_limit_headers(response, fastapi_result)

            # Raise if not allowed
            if not result.allowed:
                raise RateLimitExceededError(
                    identifier=str(identifiers),
                    limit=result.most_restrictive.limit,
                    remaining=0,
                    reset_at=result.most_restrictive.reset_at,
                    retry_after=result.most_restrictive.retry_after,
                    limiter_id=result.triggered_by or "composite",
                )

        except RateLimitExceededError:
            # Re-raise rate limit errors
            raise

        except Exception as e:
            # Handle other errors
            logger.error(
                "Composite rate limit check failed: %s",
                e,
                exc_info=True,
            )

            if self.fail_open:
                # Allow request on error
                logger.warning("Allowing request due to fail_open=True")
                return

            raise RuntimeError(f"Composite rate limiter failed: {e}") from e

    async def _get_identifiers(self, request: "Request") -> dict[str, str]:
        """Extract identifiers from the request.

        Args:
            request: The incoming HTTP request.

        Returns:
            Dictionary of check names to identifiers.

        Raises:
            ValueError: If extractor returns invalid data.
        """
        # Call the extractor (may be sync or async)
        result = self.identifier_extractor(request)

        # Handle async extractors
        if hasattr(result, "__await__"):
            identifiers = await result  # type: ignore[misc]
        else:
            identifiers = result  # type: ignore[assignment]

        # Validate result
        if not isinstance(identifiers, dict):
            raise ValueError(f"identifier_extractor must return dict, got {type(identifiers)}")

        return identifiers


__all__ = [
    "CompositeRateLimitDependency",
    "IdentifierExtractor",
]
