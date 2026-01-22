"""Core abstractions and base types for distributed rate limiting.

This module defines the generic interface for rate limiting implementations,
supporting different stores (NATS KV, Redis, PostgreSQL, etc).
"""

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ratesync.exceptions import RateLimiterAcquisitionError

if TYPE_CHECKING:
    from ratesync.schemas import LimiterReadOnlyConfig, LimiterState


@dataclass
class RateLimiterMetrics:
    """Observability metrics for rate limiter.

    Attributes:
        total_acquisitions: Total number of slots acquired
        total_wait_time_ms: Total accumulated wait time (ms)
        avg_wait_time_ms: Average wait time per acquisition (ms)
        max_wait_time_ms: Maximum wait time recorded (ms)
        cas_failures: Number of CAS (Compare-And-Set) failures - NATS specific
        timeouts: Number of timeouts in try_acquire()
        last_acquisition_at: Timestamp of last successful acquisition
        current_concurrent: Current number of concurrent operations in-flight
        max_concurrent_reached: Times the max concurrent limit was reached
        total_releases: Total number of concurrent slot releases
    """

    total_acquisitions: int = 0
    total_wait_time_ms: float = 0.0
    avg_wait_time_ms: float = 0.0
    max_wait_time_ms: float = 0.0
    cas_failures: int = 0
    timeouts: int = 0
    last_acquisition_at: float | None = None
    current_concurrent: int = 0
    max_concurrent_reached: int = 0
    total_releases: int = 0

    def record_acquisition(self, wait_time_ms: float) -> None:
        """Record a successful acquisition."""
        self.total_acquisitions += 1
        self.total_wait_time_ms += wait_time_ms
        self.avg_wait_time_ms = self.total_wait_time_ms / self.total_acquisitions
        self.max_wait_time_ms = max(self.max_wait_time_ms, wait_time_ms)
        self.last_acquisition_at = time.time()

    def record_cas_failure(self) -> None:
        """Record a CAS failure."""
        self.cas_failures += 1

    def record_timeout(self) -> None:
        """Record a timeout."""
        self.timeouts += 1

    def record_concurrent_acquire(self) -> None:
        """Record a concurrent slot acquisition."""
        self.current_concurrent += 1

    def record_concurrent_release(self) -> None:
        """Record a concurrent slot release."""
        self.current_concurrent = max(0, self.current_concurrent - 1)
        self.total_releases += 1

    def record_max_concurrent_reached(self) -> None:
        """Record that max concurrent limit was reached."""
        self.max_concurrent_reached += 1


class RateLimiter(ABC):
    """Abstract interface for distributed rate limiting implementations.

    Concrete implementations must provide distributed coordination mechanisms
    to ensure multiple processes/containers respect the same limits.

    Supports two complementary limiting strategies:
    - **Rate limiting** (rate_per_second): Controls throughput - minimum interval
      between operation STARTS (e.g., max 10 req/sec)
    - **Concurrency limiting** (max_concurrent): Controls parallelism - maximum
      simultaneous operations in-flight (e.g., max 5 concurrent)

    At least one strategy must be configured. Both can be used together.

    Example usage:
        >>> # Rate limiting only
        >>> rate_limiter = SomeRateLimiter(group_id="prod", rate_per_second=1.0)
        >>>
        >>> # Concurrency limiting only
        >>> rate_limiter = SomeRateLimiter(group_id="prod", max_concurrent=5)
        >>>
        >>> # Both (recommended for production)
        >>> rate_limiter = SomeRateLimiter(
        ...     group_id="prod", rate_per_second=10.0, max_concurrent=5
        ... )
        >>> await rate_limiter.initialize()
        >>>
        >>> # Context manager (recommended - auto-releases concurrency slot)
        >>> async with rate_limiter.acquire_context():
        >>>     await http_client.get(url)
        >>>
        >>> # Manual acquire/release (for advanced use cases)
        >>> await rate_limiter.acquire()
        >>> try:
        >>>     await http_client.get(url)
        >>> finally:
        >>>     await rate_limiter.release()  # Required if max_concurrent is set
        >>>
        >>> # Observability
        >>> metrics = rate_limiter.get_metrics()
        >>> print(f"Avg wait: {metrics.avg_wait_time_ms}ms")
        >>> print(f"Current concurrent: {metrics.current_concurrent}")
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the rate limiter.

        This operation should be called once during application startup,
        after configuring the rate limiter.

        It is idempotent - can be called multiple times without side effects.

        Raises:
            RuntimeError: If there's an error during initialization
        """

    @abstractmethod
    async def acquire(self) -> None:
        """Wait until slot is available for rate-limited operation.

        This method blocks (using asyncio.sleep) until it's possible to
        perform the operation while respecting the configured limits.

        Behavior depends on configuration:
        - If rate_per_second is set: waits for rate slot (throughput)
        - If max_concurrent is set: waits for concurrency slot (parallelism)
        - If both are set: waits for both (rate first, then concurrency)

        IMPORTANT: If max_concurrent is configured, you MUST call release()
        when the operation is complete, or use acquire_context() instead.

        Raises:
            RuntimeError: If rate limiter wasn't initialized
            RuntimeError: If can't acquire after max_retries (impl-specific)
        """

    @abstractmethod
    async def try_acquire(self, timeout: float = 0) -> bool:
        """Try to acquire slot without waiting indefinitely.

        Args:
            timeout: Maximum wait time in seconds (0 = don't wait)

        Returns:
            True if acquired slot within timeout, False otherwise

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """

    @abstractmethod
    def get_metrics(self) -> RateLimiterMetrics:
        """Return observability metrics for the rate limiter.

        Returns:
            Current snapshot of collected metrics

        Note:
            Some metrics may be implementation-specific
            (e.g., cas_failures is specific to NATS KV).
        """

    @property
    @abstractmethod
    def group_id(self) -> str:
        """Identifier for distributed coordination group.

        The group_id is used to coordinate rate limiting between multiple
        processes/containers that share the same limit.

        Example: Services running on the same public IP use the same group_id.
        """

    @property
    @abstractmethod
    def rate_per_second(self) -> float | None:
        """Rate of operations per second allowed.

        Returns:
            Float value if rate limiting is enabled, None if unlimited.
        """

    @property
    @abstractmethod
    def max_concurrent(self) -> int | None:
        """Maximum number of concurrent operations allowed.

        Returns:
            Int value if concurrency limiting is enabled, None if unlimited.
        """

    @abstractmethod
    async def release(self) -> None:
        """Release a concurrency slot after operation completes.

        This method MUST be called after acquire() when max_concurrent is set.
        If max_concurrent is None (unlimited), this is a no-op.

        The recommended pattern is to use acquire_context() which handles
        release automatically:

            async with rate_limiter.acquire_context():
                await http_client.get(url)
                # release() called automatically on exit

        For manual control:

            await rate_limiter.acquire()
            try:
                await http_client.get(url)
            finally:
                await rate_limiter.release()

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """

    @property
    @abstractmethod
    def is_initialized(self) -> bool:
        """Check if the rate limiter was initialized."""

    @property
    @abstractmethod
    def default_timeout(self) -> float | None:
        """Default timeout in seconds for acquire operations.

        If set, this timeout will be used when calling acquire() or when
        using decorators without an explicit timeout parameter.

        None means wait indefinitely by default.
        """

    @property
    @abstractmethod
    def fail_closed(self) -> bool:
        """Behavior when backend fails.

        If True (fail_closed), blocks requests when the backend (Redis, NATS, etc.)
        fails, raising RateLimiterAcquisitionError.

        If False (fail_open, default), allows requests to proceed when the backend
        fails, logging a warning but not blocking the request.

        Returns:
            True for fail-closed behavior, False for fail-open behavior.
        """

    @classmethod
    @abstractmethod
    def from_config(cls, config: object, **kwargs):
        """Create rate limiter instance from configuration object.

        Args:
            config: Engine-specific configuration dataclass
            **kwargs: Additional runtime parameters (e.g., jetstream for NATS)

        Returns:
            Instance of the rate limiter

        Raises:
            ConfigValidationError: If configuration is invalid
        """

    @abstractmethod
    def get_config(self) -> "LimiterReadOnlyConfig":
        """Get this limiter's configuration (read-only).

        Returns a read-only snapshot of the limiter's configuration.
        This is useful for introspection, debugging, and building
        proper rate limit headers (knowing the limit values).

        Returns:
            LimiterReadOnlyConfig with all configuration values.

        Example:
            >>> limiter = get_limiter("api")
            >>> config = limiter.get_config()
            >>> print(f"Algorithm: {config.algorithm}")
            >>> if config.algorithm == "sliding_window":
            ...     print(f"Limit: {config.limit} requests per {config.window_seconds}s")
            >>> else:
            ...     print(f"Rate: {config.rate_per_second} req/s")
        """

    async def get_state(self) -> "LimiterState":
        """Get current state of the limiter without consuming slots.

        Returns the current state of the rate limiter, including whether
        the next acquire() would succeed, how many slots remain, when the
        limit resets, and current usage.

        This method does NOT consume any slots - it's read-only.

        IMPORTANT: Not all backends support this. Backends that don't support
        get_state() will raise NotImplementedError.

        Returns:
            LimiterState with current state information.

        Raises:
            NotImplementedError: If the backend doesn't support state introspection
            RuntimeError: If rate limiter wasn't initialized

        Example:
            >>> limiter = get_limiter("login")
            >>> state = await limiter.get_state()
            >>> if state.remaining < 3:
            ...     logger.warning(f"Only {state.remaining} attempts remaining")
            >>> print(f"Current usage: {state.current_usage}")
            >>> print(f"Resets at: {state.reset_at}")
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support get_state(). "
            "This feature is only available in: RedisSlidingWindowRateLimiter, MemoryRateLimiter"
        )

    @asynccontextmanager
    async def acquire_context(self, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Context manager to acquire rate limit slot with automatic cleanup.

        This is the RECOMMENDED way to use the rate limiter, especially when
        max_concurrent is configured. It automatically releases the concurrency
        slot when the context exits.

        Args:
            timeout: If specified, uses try_acquire() with timeout.
                    If None, uses acquire() (waits indefinitely).

        Yields:
            None after successfully acquiring slot

        Raises:
            RuntimeError: If rate limiter wasn't initialized
            RateLimiterAcquisitionError: If timeout specified and can't acquire

        Example:
            >>> # Basic usage (recommended)
            >>> async with rate_limiter.acquire_context():
            >>>     await http_client.get(url)
            >>>     # Concurrency slot automatically released on exit
            >>>
            >>> # With timeout (non-blocking)
            >>> try:
            >>>     async with rate_limiter.acquire_context(timeout=1.0):
            >>>         await http_client.get(url)
            >>> except RateLimiterAcquisitionError:
            >>>     # Couldn't get slot in 1 second
            >>>     pass
        """
        if timeout is not None:
            # Non-blocking mode with timeout
            success = await self.try_acquire(timeout=timeout)
            if not success:
                raise RateLimiterAcquisitionError(
                    f"Unable to acquire slot in {timeout}s (group_id={self.group_id})"
                )
        else:
            # Blocking mode
            await self.acquire()

        try:
            yield
        finally:
            # Release concurrency slot (no-op if max_concurrent is None)
            await self.release()
