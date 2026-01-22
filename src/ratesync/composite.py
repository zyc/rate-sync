"""Composite rate limiting for multi-layer protection strategies.

This module provides tools for combining multiple rate limiters with
configurable strategies, enabling defense-in-depth patterns commonly
used in authentication and abuse prevention.

Use cases:
- Auth endpoints: IP-level + (IP + credential) level
- Tiered limiting: Global + Per-user + Per-endpoint
- Abuse prevention: Different windows for different threat models
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

StrategyType = Literal["most_restrictive", "all_must_pass", "any_must_pass"]


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
    """

    allowed: bool
    limit: int
    remaining: int
    reset_in: float  # seconds

    @property
    def reset_at(self) -> int:
        """Unix timestamp when the rate limit window resets."""
        return int(time.time() + self.reset_in)

    @property
    def retry_after(self) -> int:
        """Seconds to wait before retrying (for 429 responses)."""
        return max(1, int(self.reset_in))


@dataclass(frozen=True, slots=True)
class CompositeLimitCheck:
    """Result of a composite rate limit check.

    Attributes:
        allowed: Whether all checks passed according to the strategy.
        results: Dictionary mapping check names to their individual results.
        most_restrictive: The most restrictive result among all checks.
        triggered_by: Name of the check that triggered denial, or None if allowed.

    Example:
        >>> result = CompositeLimitCheck(
        ...     allowed=True,
        ...     results={"ip": ip_result, "credential": cred_result},
        ...     most_restrictive=cred_result,
        ...     triggered_by=None,
        ... )
        >>> if not result.allowed:
        ...     print(f"Rate limited by: {result.triggered_by}")
    """

    allowed: bool
    results: dict[str, RateLimitResult]
    most_restrictive: RateLimitResult
    triggered_by: str | None


class CompositeRateLimiter:
    """Combines multiple rate limiters with configurable strategies.

    Use cases:
    - Auth endpoints: IP-level + (IP + credential) level
    - Tiered limiting: Global + Per-user + Per-endpoint
    - Abuse prevention: Different windows for different threat models

    Strategies:
    - most_restrictive: All must pass, reports most restrictive (recommended)
    - all_must_pass: All must pass, fails on first failure
    - any_must_pass: At least one must allow

    Example:
        Basic composite limiting (IP + credential):

        >>> composite = CompositeRateLimiter(
        ...     limiters={
        ...         "by_ip": "verify_code_ip",
        ...         "by_ip_email": "verify_code_email",
        ...     },
        ...     strategy="most_restrictive",
        ... )
        >>> result = await composite.check(
        ...     identifiers={
        ...         "by_ip": client_ip,
        ...         "by_ip_email": f"{client_ip}:{email_hash}",
        ...     }
        ... )
        >>> if not result.allowed:
        ...     raise RateLimitExceededError(...)

        Triple-layer protection:

        >>> composite = CompositeRateLimiter(
        ...     limiters={
        ...         "global": "api_global",
        ...         "per_user": "api_user",
        ...         "per_endpoint": "api_endpoint",
        ...     },
        ...     strategy="all_must_pass",
        ... )
        >>> result = await composite.check(
        ...     identifiers={
        ...         "global": "api",
        ...         "per_user": str(user_id),
        ...         "per_endpoint": f"{user_id}:{endpoint_name}",
        ...     }
        ... )
    """

    def __init__(
        self,
        limiters: dict[str, str],
        strategy: StrategyType = "most_restrictive",
    ) -> None:
        """Initialize composite rate limiter.

        Args:
            limiters: Mapping of check names to limiter IDs.
                Example: {"by_ip": "verify_code_ip", "by_email": "verify_code_email"}
            strategy: How to combine results:
                - "most_restrictive": All must pass, reports most restrictive
                - "all_must_pass": All must pass, fails on first failure
                - "any_must_pass": At least one must pass

        Raises:
            ValueError: If limiters dict is empty.
        """
        if not limiters:
            raise ValueError("At least one limiter must be provided")

        self.limiters = limiters
        self.strategy = strategy

    async def check(
        self,
        identifiers: dict[str, str],
        timeout: float | None = None,
    ) -> CompositeLimitCheck:
        """Check all limiters with given identifiers.

        Args:
            identifiers: Mapping of check names to identifiers.
                Must match keys in self.limiters.
            timeout: Optional timeout override for all checks (seconds).
                If None, uses each limiter's default timeout.

        Returns:
            CompositeLimitCheck with aggregated results.

        Raises:
            ValueError: If identifier keys don't match limiter keys.

        Example:
            >>> result = await composite.check(
            ...     identifiers={
            ...         "by_ip": "192.168.1.1",
            ...         "by_ip_email": "192.168.1.1:abc123",
            ...     },
            ...     timeout=0,  # Non-blocking
            ... )
            >>> if result.allowed:
            ...     print(f"Remaining: {result.most_restrictive.remaining}")
        """
        # Lazy import to avoid circular dependency
        from ratesync import get_or_clone_limiter

        if set(identifiers.keys()) != set(self.limiters.keys()):
            raise ValueError(
                f"Identifier keys {set(identifiers.keys())} "
                f"must match limiter keys {set(self.limiters.keys())}"
            )

        results: dict[str, RateLimitResult] = {}

        # Use provided timeout or default to 0 (non-blocking)
        effective_timeout = timeout if timeout is not None else 0

        for check_name, limiter_id in self.limiters.items():
            identifier = identifiers[check_name]

            # Get or clone limiter for this identifier
            limiter = await get_or_clone_limiter(limiter_id, identifier)

            # Try to acquire - this consumes a slot if successful
            allowed = await limiter.try_acquire(timeout=effective_timeout)

            # Get current state to determine remaining and limit
            try:
                state = await limiter.get_state()
                remaining = state.remaining
                reset_in = float(state.reset_at - int(time.time()))
                limit = state.current_usage + remaining
            except (NotImplementedError, AttributeError):
                # Fallback: estimate from limiter params
                limit, window = self._get_limiter_params(limiter)
                metrics = limiter.get_metrics()

                if allowed:
                    remaining = max(
                        0, limit - (metrics.total_acquisitions % limit) if limit > 0 else 999
                    )
                else:
                    remaining = 0

                reset_in = float(window)

            results[check_name] = RateLimitResult(
                allowed=allowed,
                limit=limit,
                remaining=remaining,
                reset_in=reset_in,
            )

        # Apply strategy
        return self._apply_strategy(results)

    def _apply_strategy(
        self,
        results: dict[str, RateLimitResult],
    ) -> CompositeLimitCheck:
        """Apply the configured strategy to combine results.

        Args:
            results: Dictionary of check names to their results.

        Returns:
            CompositeLimitCheck with aggregated outcome.
        """
        if self.strategy == "all_must_pass":
            # First failure wins
            for check_name, result in results.items():
                if not result.allowed:
                    return CompositeLimitCheck(
                        allowed=False,
                        results=results,
                        most_restrictive=result,
                        triggered_by=check_name,
                    )

            # All passed - return most restrictive for headers
            most_restrictive = min(results.values(), key=lambda r: r.remaining)
            return CompositeLimitCheck(
                allowed=True,
                results=results,
                most_restrictive=most_restrictive,
                triggered_by=None,
            )

        if self.strategy == "any_must_pass":
            # At least one must pass
            allowed = any(r.allowed for r in results.values())
            most_restrictive = min(results.values(), key=lambda r: r.remaining)

            return CompositeLimitCheck(
                allowed=allowed,
                results=results,
                most_restrictive=most_restrictive,
                triggered_by=None if allowed else "all",
            )

        # most_restrictive (default)
        # All must pass, report most restrictive
        all_allowed = all(r.allowed for r in results.values())
        most_restrictive = min(results.values(), key=lambda r: r.remaining)

        if not all_allowed:
            # Find which one blocked
            triggered_by = next(name for name, r in results.items() if not r.allowed)
        else:
            triggered_by = None

        return CompositeLimitCheck(
            allowed=all_allowed,
            results=results,
            most_restrictive=most_restrictive,
            triggered_by=triggered_by,
        )

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


__all__ = [
    "CompositeLimitCheck",
    "CompositeRateLimiter",
    "StrategyType",
    "RateLimitResult",
]
