"""
In-memory rate limiting strategy using local asyncio primitives.

This strategy is useful for:
- Development and testing (no external dependencies)
- Single-process applications
- Applications that don't need distributed coordination

Note: This does NOT coordinate across multiple processes or containers.
"""

import asyncio
import time
from collections import deque
from typing import Callable

from ratesync.core import RateLimiter, RateLimiterMetrics
from ratesync.exceptions import RateLimiterNotInitializedError
from ratesync.schemas import LimiterReadOnlyConfig, LimiterState, MemoryEngineConfig

# Try to import prometheus metrics functions at module level
try:
    from ratesync.contrib.prometheus.metrics import record_acquisition as _prom_acquisition
    from ratesync.contrib.prometheus.metrics import record_timeout as _prom_timeout

    _PROM_RECORD_ACQUISITION: Callable[[str, str, float], None] | None = _prom_acquisition
    _PROM_RECORD_TIMEOUT: Callable[[str, str], None] | None = _prom_timeout
except ImportError:
    _PROM_RECORD_ACQUISITION = None
    _PROM_RECORD_TIMEOUT = None


def _record_acquisition(group_id: str, duration_s: float) -> None:
    """Record acquisition to Prometheus if available."""
    if _PROM_RECORD_ACQUISITION is not None:
        _PROM_RECORD_ACQUISITION(group_id, "memory", duration_s)


def _record_timeout(group_id: str) -> None:
    """Record timeout to Prometheus if available."""
    if _PROM_RECORD_TIMEOUT is not None:
        _PROM_RECORD_TIMEOUT(group_id, "memory")


class MemoryRateLimiter(RateLimiter):
    """In-memory rate limiter supporting token bucket and sliding window algorithms.

    Supports multiple limiting strategies:
    - **token_bucket**: Rate limiting with token bucket (rate_per_second)
    - **sliding_window**: Quota-based limiting in time window (limit + window_seconds)
    - **max_concurrent**: Parallelism control (works with both algorithms)

    At least one rate limiting strategy (token_bucket or sliding_window) must be configured.

    Attributes:
        _group_id: Identifier for this rate limiter
        _algorithm: "token_bucket" or "sliding_window"
        _rate_per_second: Operations per second (token_bucket only)
        _limit: Maximum requests in window (sliding_window only)
        _window_seconds: Window size in seconds (sliding_window only)
        _max_concurrent: Maximum simultaneous operations (None = unlimited)
        _default_timeout: Default timeout for acquire operations
        _interval: Minimum interval between operations (token_bucket only)
        _lock: Asyncio lock for rate limiting synchronization
        _semaphore: Asyncio semaphore for concurrency limiting
        _timestamps: Deque of recent operation timestamps
        _initialized: Whether the limiter has been initialized
        _metrics: Observability metrics
    """

    def __init__(
        self,
        group_id: str,
        rate_per_second: float | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        max_concurrent: int | None = None,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> None:
        """Initialize memory rate limiter.

        Args:
            group_id: Identifier for this rate limiter
            rate_per_second: Operations per second (token_bucket algorithm)
            limit: Max requests in window (sliding_window algorithm)
            window_seconds: Window size in seconds (sliding_window algorithm)
            max_concurrent: Maximum simultaneous operations (None = unlimited concurrency)
            default_timeout: Default timeout in seconds for acquire operations
            fail_closed: If True, blocks on backend failure. For memory engine,
                        this is a no-op since there's no external backend.

        Raises:
            ValueError: If neither rate_per_second nor (limit + window_seconds) is specified
            ValueError: If both token_bucket and sliding_window params are specified
            ValueError: If sliding_window specified but missing limit or window_seconds
            ValueError: If rate_per_second <= 0 or limit <= 0 or window_seconds <= 0
        """
        # Validate algorithm configuration
        has_token_bucket = rate_per_second is not None
        has_sliding_window = limit is not None or window_seconds is not None

        if not has_token_bucket and not has_sliding_window and max_concurrent is None:
            raise ValueError(
                "At least one of (rate_per_second) or (limit+window_seconds) "
                "or max_concurrent must be specified"
            )

        if has_token_bucket and has_sliding_window:
            raise ValueError(
                "Cannot specify both token_bucket (rate_per_second) and "
                "sliding_window (limit+window_seconds) parameters"
            )

        if has_sliding_window and (limit is None or window_seconds is None):
            raise ValueError("sliding_window algorithm requires both 'limit' and 'window_seconds'")

        # Validate parameter values
        if rate_per_second is not None and rate_per_second <= 0:
            raise ValueError(f"rate_per_second must be > 0, got {rate_per_second}")

        if limit is not None and limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")

        if window_seconds is not None and window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")

        if max_concurrent is not None and max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got {max_concurrent}")

        self._group_id = group_id
        self._rate_per_second = rate_per_second
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_concurrent = max_concurrent
        self._default_timeout = default_timeout
        self._fail_closed = fail_closed

        # Determine algorithm
        if has_token_bucket:
            self._algorithm = "token_bucket"
            self._interval = 1.0 / rate_per_second if rate_per_second else None
        elif has_sliding_window:
            self._algorithm = "sliding_window"
            self._interval = None
        else:
            # Only max_concurrent specified
            self._algorithm = "token_bucket"  # Default, but rate is unlimited
            self._interval = None

        # Rate limiting: lock and timestamps
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()

        # Concurrency limiting: semaphore
        self._semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None

        self._initialized = False
        self._metrics = RateLimiterMetrics()

    @classmethod
    def from_config(
        cls,
        config: MemoryEngineConfig,
        group_id: str,
        rate_per_second: float | None = None,
        **kwargs,
    ) -> "MemoryRateLimiter":
        """Create memory rate limiter from configuration.

        Args:
            config: Memory strategy configuration
            group_id: Identifier for this rate limiter
            rate_per_second: Operations per second (token_bucket algorithm)
            **kwargs: Additional parameters (limit, window_seconds, timeout,
                     max_concurrent, fail_closed, etc.)

        Returns:
            Configured MemoryRateLimiter instance
        """
        default_timeout = kwargs.get("timeout", None)
        max_concurrent = kwargs.get("max_concurrent", None)
        fail_closed = kwargs.get("fail_closed", False)
        limit = kwargs.get("limit", None)
        window_seconds = kwargs.get("window_seconds", None)

        return cls(
            group_id=group_id,
            rate_per_second=rate_per_second,
            limit=limit,
            window_seconds=window_seconds,
            max_concurrent=max_concurrent,
            default_timeout=default_timeout,
            fail_closed=fail_closed,
        )

    async def initialize(self) -> None:
        """Initialize the memory rate limiter.

        This is idempotent and can be called multiple times.
        """
        self._initialized = True

    async def _acquire_rate_slot(self) -> float:
        """Acquire rate limiting slot (throughput control).

        Returns:
            Wait time in seconds
        """
        if self._algorithm == "sliding_window":
            return await self._acquire_sliding_window_slot()
        # token_bucket algorithm
        return await self._acquire_token_bucket_slot()

    async def _acquire_token_bucket_slot(self) -> float:
        """Acquire slot using token bucket algorithm.

        Returns:
            Wait time in seconds
        """
        if self._interval is None:
            return 0.0

        start_time = time.time()

        async with self._lock:
            now = time.time()

            # Clean old timestamps outside the current window
            cutoff = now - 1.0  # Keep last 1 second of timestamps
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            # Calculate wait time
            if self._timestamps:
                last_timestamp = self._timestamps[-1]
                elapsed = now - last_timestamp

                if elapsed < self._interval:
                    wait_time = self._interval - elapsed
                    await asyncio.sleep(wait_time)
                    now = time.time()

            # Record this acquisition
            self._timestamps.append(now)

        return time.time() - start_time

    async def _acquire_sliding_window_slot(self) -> float:
        """Acquire slot using sliding window algorithm.

        Returns:
            Wait time in seconds
        """
        if self._limit is None or self._window_seconds is None:
            return 0.0

        start_time = time.time()

        async with self._lock:
            while True:
                now = time.time()

                # Clean old timestamps outside the window
                cutoff = now - self._window_seconds
                while self._timestamps and self._timestamps[0] < cutoff:
                    self._timestamps.popleft()

                # Check if we can acquire now
                if len(self._timestamps) < self._limit:
                    # Can acquire - record timestamp and exit
                    self._timestamps.append(now)
                    break

                # Need to wait - find when oldest will expire
                if self._timestamps:
                    oldest = self._timestamps[0]
                    wait_until = oldest + self._window_seconds
                    wait_time = max(0.001, wait_until - now)  # Min 1ms
                    await asyncio.sleep(wait_time)
                else:
                    # Shouldn't happen, but handle gracefully
                    break

        return time.time() - start_time

    async def _acquire_concurrent_slot(self) -> None:
        """Acquire concurrency slot (parallelism control)."""
        if self._semaphore is None:
            return

        # Check if we'll hit the limit
        if self._semaphore.locked():
            self._metrics.record_max_concurrent_reached()

        await self._semaphore.acquire()
        self._metrics.record_concurrent_acquire()

    async def acquire(self) -> None:
        """Wait until slot is available for rate-limited operation.

        Acquires both rate slot (if rate_per_second is set) and concurrency
        slot (if max_concurrent is set). Order: rate first, then concurrency.

        IMPORTANT: If max_concurrent is configured, you MUST call release()
        when the operation is complete, or use acquire_context() instead.

        Raises:
            RateLimiterNotInitializedError: If not initialized
        """
        if not self._initialized:
            raise RateLimiterNotInitializedError(self._group_id)

        start_time = time.time()

        # Step 1: Acquire rate slot (throughput control)
        await self._acquire_rate_slot()

        # Step 2: Acquire concurrency slot (parallelism control)
        await self._acquire_concurrent_slot()

        # Update metrics
        wait_time_s = time.time() - start_time
        self._metrics.record_acquisition(wait_time_s * 1000)
        _record_acquisition(self._group_id, wait_time_s)

    async def release(self) -> None:
        """Release a concurrency slot after operation completes.

        This method MUST be called after acquire() when max_concurrent is set.
        If max_concurrent is None (unlimited), this is a no-op.

        Raises:
            RateLimiterNotInitializedError: If not initialized
        """
        if not self._initialized:
            raise RateLimiterNotInitializedError(self._group_id)

        if self._semaphore is not None:
            self._semaphore.release()
            self._metrics.record_concurrent_release()

    async def try_acquire(self, timeout: float = 0) -> bool:
        """Try to acquire slot without waiting indefinitely.

        Args:
            timeout: Maximum wait time in seconds (0 = don't wait)

        Returns:
            True if acquired slot within timeout, False otherwise

        Raises:
            RateLimiterNotInitializedError: If not initialized
        """
        if not self._initialized:
            raise RateLimiterNotInitializedError(self._group_id)

        if timeout == 0:
            # Try to acquire immediately without waiting
            return await self._try_acquire_immediate()

        # Try with timeout
        try:
            await asyncio.wait_for(self.acquire(), timeout=timeout)
            return True
        except TimeoutError:
            self._metrics.record_timeout()
            _record_timeout(self._group_id)
            return False

    async def _try_acquire_immediate(self) -> bool:
        """Try to acquire immediately without waiting."""
        # Check rate limiting based on algorithm
        if self._algorithm == "sliding_window":
            if not await self._try_acquire_sliding_window_immediate():
                return False
        elif self._algorithm == "token_bucket":
            if not await self._try_acquire_token_bucket_immediate():
                return False

        # Check concurrency limiting
        if self._semaphore is not None:
            if self._semaphore.locked():
                self._metrics.record_timeout()
                self._metrics.record_max_concurrent_reached()
                _record_timeout(self._group_id)
                return False

            await self._semaphore.acquire()
            self._metrics.record_concurrent_acquire()

        self._metrics.record_acquisition(0)
        _record_acquisition(self._group_id, 0)
        return True

    async def _try_acquire_token_bucket_immediate(self) -> bool:
        """Try to acquire token bucket slot immediately."""
        if self._interval is None:
            return True

        if self._lock.locked():
            self._metrics.record_timeout()
            _record_timeout(self._group_id)
            return False

        async with self._lock:
            now = time.time()

            # Clean old timestamps
            cutoff = now - 1.0
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            # Check if we can acquire now
            if self._timestamps:
                last_timestamp = self._timestamps[-1]
                elapsed = now - last_timestamp

                if elapsed < self._interval:
                    self._metrics.record_timeout()
                    _record_timeout(self._group_id)
                    return False

            # Can acquire rate slot
            self._timestamps.append(now)

        return True

    async def _try_acquire_sliding_window_immediate(self) -> bool:
        """Try to acquire sliding window slot immediately."""
        if self._limit is None or self._window_seconds is None:
            return True

        if self._lock.locked():
            self._metrics.record_timeout()
            _record_timeout(self._group_id)
            return False

        async with self._lock:
            now = time.time()

            # Clean old timestamps outside window
            cutoff = now - self._window_seconds
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            # Check if we can acquire now
            if len(self._timestamps) >= self._limit:
                self._metrics.record_timeout()
                _record_timeout(self._group_id)
                return False

            # Can acquire - record timestamp
            self._timestamps.append(now)

        return True

    def get_metrics(self) -> RateLimiterMetrics:
        """Return observability metrics for the rate limiter.

        Returns:
            Current snapshot of collected metrics
        """
        return self._metrics

    def get_config(self) -> LimiterReadOnlyConfig:
        """Get this limiter's configuration (read-only).

        Returns:
            LimiterReadOnlyConfig with all configuration values.
        """
        return LimiterReadOnlyConfig(
            id=self._group_id,
            algorithm=self._algorithm,
            store_id="memory",
            rate_per_second=self._rate_per_second,
            max_concurrent=self._max_concurrent,
            timeout=self._default_timeout,
            limit=self._limit,
            window_seconds=self._window_seconds,
            fail_closed=self._fail_closed,
        )

    async def get_state(self) -> LimiterState:
        """Get current state without consuming slots.

        For memory engine, this provides current usage and remaining slots
        based on the configured algorithm (token_bucket or sliding_window).

        Returns:
            LimiterState with current usage and availability.

        Raises:
            RuntimeError: If rate limiter wasn't initialized
        """
        if not self._initialized:
            raise RuntimeError(f"Rate limiter '{self._group_id}' not initialized")

        now = time.time()

        # Calculate remaining based on algorithm
        if self._algorithm == "sliding_window":
            remaining_rate, current_usage_rate, reset_at = await self._get_sliding_window_state(now)
        else:  # token_bucket
            remaining_rate, current_usage_rate, reset_at = await self._get_token_bucket_state(now)

        # Calculate remaining based on concurrency
        remaining_concurrent = 0
        current_concurrent = 0
        if self._semaphore is not None:
            # Get semaphore value (available slots)
            # Python's Semaphore doesn't expose _value directly in a thread-safe way
            # So we use the metrics
            current_concurrent = self._metrics.current_concurrent
            remaining_concurrent = max(0, self._max_concurrent - current_concurrent)
        else:
            remaining_concurrent = 999  # Unlimited concurrency

        # Most restrictive determines state
        remaining = min(remaining_rate, remaining_concurrent)
        allowed = remaining > 0

        # Current usage is max of rate and concurrent
        current_usage = max(current_usage_rate, current_concurrent)

        return LimiterState(
            allowed=allowed,
            remaining=remaining,
            reset_at=reset_at,
            current_usage=current_usage,
        )

    async def _get_token_bucket_state(self, now: float) -> tuple[int, int, int]:
        """Get state for token bucket algorithm.

        Returns:
            Tuple of (remaining, current_usage, reset_at)
        """
        if self._rate_per_second is None:
            return 999, 0, int(now + 1.0)

        # Clean old timestamps
        cutoff = now - 1.0
        async with self._lock:
            clean_timestamps = [ts for ts in self._timestamps if ts >= cutoff]
            current_usage = len(clean_timestamps)

            # Approximate remaining in next second
            max_in_window = int(self._rate_per_second)
            remaining = max(0, max_in_window - current_usage)

        # Reset is based on the interval
        if self._interval is not None:
            reset_at = int(now + self._interval)
        else:
            reset_at = int(now + 1.0)

        return remaining, current_usage, reset_at

    async def _get_sliding_window_state(self, now: float) -> tuple[int, int, int]:
        """Get state for sliding window algorithm.

        Returns:
            Tuple of (remaining, current_usage, reset_at)
        """
        if self._limit is None or self._window_seconds is None:
            return 999, 0, int(now + 1.0)

        # Clean old timestamps outside window
        cutoff = now - self._window_seconds
        async with self._lock:
            clean_timestamps = [ts for ts in self._timestamps if ts >= cutoff]
            current_usage = len(clean_timestamps)

            # Calculate remaining slots
            remaining = max(0, self._limit - current_usage)

        # Reset is when oldest timestamp expires
        if clean_timestamps:
            oldest = min(clean_timestamps)
            reset_at = int(oldest + self._window_seconds)
        else:
            reset_at = int(now + self._window_seconds)

        return remaining, current_usage, reset_at

    @property
    def group_id(self) -> str:
        """Identifier for this rate limiter."""
        return self._group_id

    @property
    def algorithm(self) -> str:
        """Algorithm used: 'token_bucket' or 'sliding_window'."""
        return self._algorithm

    @property
    def rate_per_second(self) -> float | None:
        """Rate of operations per second allowed (None = unlimited, token_bucket only)."""
        return self._rate_per_second

    @property
    def limit(self) -> int | None:
        """Maximum requests in window (sliding_window only)."""
        return self._limit

    @property
    def window_seconds(self) -> int | None:
        """Window size in seconds (sliding_window only)."""
        return self._window_seconds

    @property
    def max_concurrent(self) -> int | None:
        """Maximum number of concurrent operations allowed (None = unlimited)."""
        return self._max_concurrent

    @property
    def is_initialized(self) -> bool:
        """Check if the rate limiter was initialized."""
        return self._initialized

    @property
    def default_timeout(self) -> float | None:
        """Default timeout in seconds for acquire operations."""
        return self._default_timeout

    @property
    def fail_closed(self) -> bool:
        """Behavior when backend fails.

        For memory engine, this has no effect since there's no external backend.
        """
        return self._fail_closed

    async def reset(self) -> None:
        """Reset this limiter's state (for testing).

        Clears all timestamps and resets the semaphore to initial state.
        This allows tests to run with fresh limiter state.

        Example:
            >>> limiter = MemoryRateLimiter("test", rate_per_second=10)
            >>> await limiter.initialize()
            >>> await limiter.acquire()
            >>> await limiter.reset()  # Fresh state
        """
        async with self._lock:
            self._timestamps.clear()

        # Reset semaphore by releasing all held slots and recreating
        if self._semaphore is not None and self._max_concurrent is not None:
            # Recreate semaphore to reset to initial state
            self._semaphore = asyncio.Semaphore(self._max_concurrent)

        # Reset metrics
        self._metrics = RateLimiterMetrics()

    async def reset_all(self) -> None:
        """Reset all state for this backend (for testing).

        For memory backend, this is the same as reset() since each
        limiter instance has its own isolated state.
        """
        await self.reset()
