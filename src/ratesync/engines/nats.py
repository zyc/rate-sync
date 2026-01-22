"""NATS Key-Value Store rate limiter implementation.

This implementation uses NATS JetStream KV with Compare-And-Set (CAS)
for distributed coordination across multiple processes/containers.

Requirements:
    pip install 'rate-sync[nats]'  or  pip install nats-py
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

# Lazy import: only fail if NATS engine is actually used
try:
    from nats import connect
    from nats.js.errors import BadRequestError, KeyNotFoundError

    NATS_AVAILABLE = True
except ImportError:
    NATS_AVAILABLE = False

    # Placeholder exceptions for type checking - use unique base classes
    # to avoid pylint E0701 (bad-except-order) when NATS is not installed
    class BadRequestError(BaseException):  # type: ignore
        """Placeholder for nats.js.errors.BadRequestError when NATS is not installed."""

    class KeyNotFoundError(BaseException):  # type: ignore
        """Placeholder for nats.js.errors.KeyNotFoundError when NATS is not installed."""

    connect = None  # type: ignore

from ratesync.core import RateLimiter, RateLimiterMetrics
from ratesync.exceptions import (
    RateLimiterAcquisitionError,
    RateLimiterNotInitializedError,
)
from ratesync.schemas import NatsEngineConfig

if TYPE_CHECKING:
    from nats.js import JetStreamContext

logger = logging.getLogger(__name__)


class NatsKvRateLimiter(RateLimiter):
    """Distributed rate limiter using NATS Key-Value Store.

    Uses Compare-And-Set (CAS) to guarantee distributed coordination
    across multiple processes and services.

    Supports two complementary limiting strategies:
    - **rate_per_second**: Controls throughput using timestamp tracking
    - **max_concurrent**: Controls parallelism using a distributed counter

    At least one strategy must be configured. Both can be used together
    for fine-grained control.

    Features:
    - Controls minimum interval between operation STARTS (rate limiting)
    - Limits maximum simultaneous "in-flight" operations (concurrency)
    - Dynamic TTL based on configured rate
    - Integrated metrics for observability
    - Support for try_acquire() with timeout

    Example usage:
        >>> rate_limiter = NatsKvRateLimiter(
        ...     jetstream=js,
        ...     group_id="production-ip1",
        ...     rate_per_second=1.0,
        ...     max_concurrent=10,
        ... )
        >>> await rate_limiter.initialize()
        >>> async with rate_limiter.acquire_context():
        >>>     # Perform rate-limited operation here
        >>>     pass
    """

    def __init__(
        self,
        jetstream: "JetStreamContext",
        group_id: str,
        rate_per_second: float | None = None,
        max_concurrent: int | None = None,
        bucket_name: str = "rate_limits",
        auto_create: bool = False,
        retry_interval: float = 0.05,
        max_retries: int = 100,
        timing_margin_ms: float = 10.0,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> None:
        """Initialize NATS KV rate limiter.

        Args:
            jetstream: NATS JetStream context
            group_id: Rate limit group identifier (e.g., "production-ip1")
            rate_per_second: Operations per second allowed (None = unlimited throughput)
            max_concurrent: Maximum simultaneous operations (None = unlimited concurrency)
            bucket_name: KV bucket name (default: "rate_limits")
            auto_create: If True, create bucket on initialize(); if False, bucket must exist
            retry_interval: Interval between CAS retry attempts in seconds
            max_retries: Maximum attempts before failing
            timing_margin_ms: Safety margin in ms to avoid race conditions
            default_timeout: Default timeout in seconds for acquire operations
            fail_closed: If True, blocks requests when NATS fails. If False (default),
                        allows requests when NATS fails (fail-open behavior).

        Raises:
            ValueError: If neither rate_per_second nor max_concurrent is specified
            ValueError: If rate_per_second <= 0 or max_concurrent <= 0
            ValueError: If group_id is empty or contains invalid characters
            ImportError: If nats-py is not installed
        """
        if not NATS_AVAILABLE:
            raise ImportError(
                "\nNATS engine requires nats-py to be installed.\n"
                "Install with one of these commands:\n"
                "  pip install 'rate-sync[nats]'\n"
                "  pip install 'rate-sync[all]'\n"
                "  pip install nats-py"
            )

        # Validate at least one limiting strategy is specified
        if rate_per_second is None and max_concurrent is None:
            raise ValueError("At least one of rate_per_second or max_concurrent must be specified")

        if rate_per_second is not None and rate_per_second <= 0:
            raise ValueError(f"rate_per_second must be > 0, got: {rate_per_second}")

        if max_concurrent is not None and max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got: {max_concurrent}")

        if not group_id or not group_id.strip():
            raise ValueError("group_id cannot be empty")

        # Validate group_id for NATS KV compatibility
        # NATS keys cannot contain: space, tab, ., *, >, path separators
        group_id_clean = group_id.strip()
        invalid_chars = [" ", "\t", "*", ">", "/", "\\"]
        for char in invalid_chars:
            if char in group_id_clean:
                raise ValueError(
                    f"group_id cannot contain '{char}'. "
                    f"Use alphanumeric characters, hyphens, and underscores only."
                )

        self._js = jetstream
        self._group_id = group_id_clean
        self._rate = rate_per_second
        self._max_concurrent = max_concurrent
        self._bucket_name = bucket_name
        self._auto_create = auto_create
        self._retry_interval = retry_interval
        self._max_retries = max_retries
        self._timing_margin_s = timing_margin_ms / 1000.0  # Convert to seconds
        self._default_timeout = default_timeout
        self._fail_closed = fail_closed

        # Computed values
        self._interval = 1.0 / rate_per_second if rate_per_second else None

        # Dynamic TTL: 3x the interval or minimum 10 seconds
        self._ttl = max(int(self._interval * 3), 10) if self._interval else 60

        self._kv = None
        self._initialized = False
        self._metrics = RateLimiterMetrics()

        # Track if we own the NATS connection (for auto-connect mode cleanup)
        self._owned_connection = None

        # Initialize attributes for auto-connect mode (set by from_config)
        self._needs_auto_connect = False
        self._config_url = None
        self._config_token = None

    @property
    def _rate_key(self) -> str:
        """Return KV key for rate limiting."""
        return f"rate_limit.{self._group_id}.rate"

    @property
    def _concurrent_key(self) -> str:
        """Return KV key for concurrency limiting."""
        return f"rate_limit.{self._group_id}.concurrent"

    async def initialize(self) -> None:
        """Initialize or get the KV bucket.

        For auto-connect mode, this also establishes the NATS connection.
        This operation should be called once during startup.
        It is idempotent - can be called multiple times without effect.
        """
        if self._initialized:
            return

        # Auto-connect if needed
        if hasattr(self, "_needs_auto_connect") and self._needs_auto_connect:
            try:
                # Auto-connect using stored configuration
                connect_opts = {"servers": [self._config_url]}

                # Add token if provided
                if self._config_token:
                    connect_opts["token"] = self._config_token

                # Create connection
                nc = await connect(**connect_opts)
                self._js = nc.jetstream()
                self._owned_connection = nc
                self._needs_auto_connect = False

                logger.info(
                    "Auto-connected to NATS server at %s for group '%s'",
                    self._config_url,
                    self._group_id,
                )

            except (OSError, TimeoutError, ConnectionError, ValueError) as e:
                raise ConnectionError(
                    f"Failed to auto-connect to NATS server at {self._config_url}: {e}"
                ) from e

        if self._auto_create:
            try:
                # Try to create bucket
                self._kv = await self._js.create_key_value(
                    bucket=self._bucket_name,
                    history=1,
                    ttl=self._ttl,
                )
                logger.info(
                    "KV bucket '%s' created for rate limiting (group=%s, ttl=%ds)",
                    self._bucket_name,
                    self._group_id,
                    self._ttl,
                )
            except BadRequestError:
                # Bucket already exists, get reference
                self._kv = await self._js.key_value(self._bucket_name)
                logger.info(
                    "KV bucket '%s' obtained for rate limiting (group=%s)",
                    self._bucket_name,
                    self._group_id,
                )
        else:
            # auto_create=False: bucket must already exist
            try:
                self._kv = await self._js.key_value(self._bucket_name)
                logger.info(
                    "KV bucket '%s' obtained for rate limiting (group=%s)",
                    self._bucket_name,
                    self._group_id,
                )
            except (KeyError, AttributeError, OSError) as e:
                raise ConnectionError(
                    f"KV bucket '{self._bucket_name}' not found. "
                    f"Set auto_create=true or create bucket manually."
                ) from e

        self._initialized = True

    async def _acquire_rate_slot(self) -> bool:
        """Acquire rate limiting slot using CAS.

        Returns:
            True if acquired, False if need to wait
        """
        if self._interval is None:
            return True  # Rate limiting disabled

        try:
            # Try to get last operation timestamp
            entry = await self._kv.get(self._rate_key)
            last_operation_time = float(entry.value.decode())

            now = time.time()
            elapsed = now - last_operation_time

            if elapsed >= self._interval:
                # Slot available, try to update
                try:
                    await self._kv.update(
                        self._rate_key,
                        str(now).encode(),
                        last=entry.revision,
                    )
                    return True
                except (BadRequestError, KeyError, OSError, ValueError):
                    # CAS failed, another instance updated
                    self._metrics.record_cas_failure()
                    return False
            else:
                return False

        except KeyNotFoundError:
            # First operation for this group
            try:
                await self._kv.create(self._rate_key, str(time.time()).encode())
                return True
            except (BadRequestError, OSError, ValueError):
                # Another instance created it first (race condition)
                return False

    async def _acquire_concurrent_slot(self) -> bool:
        """Acquire concurrency slot using CAS.

        Returns:
            True if acquired, False if at max capacity
        """
        if self._max_concurrent is None:
            return True  # Concurrency limiting disabled

        try:
            # Try to get current count
            entry = await self._kv.get(self._concurrent_key)
            current_count = int(entry.value.decode())

            if current_count >= self._max_concurrent:
                # At max capacity
                self._metrics.record_max_concurrent_reached()
                return False

            # Try to increment
            try:
                await self._kv.update(
                    self._concurrent_key,
                    str(current_count + 1).encode(),
                    last=entry.revision,
                )
                self._metrics.record_concurrent_acquire()
                return True
            except (BadRequestError, KeyError, OSError, ValueError):
                # CAS failed
                self._metrics.record_cas_failure()
                return False

        except KeyNotFoundError:
            # First operation - create with count 1
            try:
                await self._kv.create(self._concurrent_key, b"1")
                self._metrics.record_concurrent_acquire()
                return True
            except (BadRequestError, OSError, ValueError):
                # Another instance created it first
                return False

    async def acquire(self) -> None:
        """Wait until slot is available for operation.

        This method blocks (using asyncio.sleep) until it's possible
        to perform the operation while respecting the group's rate limit
        and concurrency limit.

        IMPORTANT: If max_concurrent is configured, you MUST call release()
        when the operation is complete, or use acquire_context() instead.

        Raises:
            RateLimiterNotInitializedError: If rate limiter wasn't initialized
            RateLimiterAcquisitionError: If can't acquire after max_retries
        """
        if not self._initialized:
            raise RateLimiterNotInitializedError()

        start_time = time.time()
        rate_acquired = self._interval is None  # True if rate limiting disabled
        concurrent_acquired = self._max_concurrent is None  # True if concurrency disabled

        for attempt in range(self._max_retries):
            # Step 1: Acquire rate slot (if not already acquired)
            if not rate_acquired:
                rate_acquired = await self._acquire_rate_slot()
                if not rate_acquired:
                    # Wait and retry
                    wait_time = self._interval - (time.time() - start_time) % self._interval
                    wait_time = max(wait_time, self._retry_interval)
                    await asyncio.sleep(min(wait_time, self._interval) + self._timing_margin_s)
                    continue

            # Step 2: Acquire concurrency slot (if not already acquired)
            if not concurrent_acquired:
                concurrent_acquired = await self._acquire_concurrent_slot()
                if not concurrent_acquired:
                    # Wait and retry
                    await asyncio.sleep(self._retry_interval)
                    continue

            # Both acquired successfully
            wait_time_ms = (time.time() - start_time) * 1000
            self._metrics.record_acquisition(wait_time_ms)

            logger.debug(
                "Rate limit: slot acquired (group=%s, attempt=%d, wait=%.2fms)",
                self._group_id,
                attempt + 1,
                wait_time_ms,
            )
            return

        # Exhausted attempts
        raise RateLimiterAcquisitionError(
            f"Unable to acquire rate limit slot for group '{self._group_id}' "
            f"after {self._max_retries} attempts",
            group_id=self._group_id,
            attempts=self._max_retries,
        )

    async def release(self) -> None:
        """Release a concurrency slot after operation completes.

        This method MUST be called after acquire() when max_concurrent is set.
        If max_concurrent is None (unlimited), this is a no-op.

        Raises:
            RateLimiterNotInitializedError: If rate limiter wasn't initialized
        """
        if not self._initialized:
            raise RateLimiterNotInitializedError()

        if self._max_concurrent is None:
            return  # No-op if concurrency limiting is disabled

        # Decrement counter with CAS
        for _ in range(self._max_retries):
            try:
                entry = await self._kv.get(self._concurrent_key)
                current_count = int(entry.value.decode())

                if current_count > 0:
                    await self._kv.update(
                        self._concurrent_key,
                        str(current_count - 1).encode(),
                        last=entry.revision,
                    )
                    self._metrics.record_concurrent_release()
                    return

                # Count is already 0, nothing to release
                return

            except KeyNotFoundError:
                # Key doesn't exist, nothing to release
                return
            except (BadRequestError, OSError, ValueError):
                # CAS failed, retry
                await asyncio.sleep(self._retry_interval)
                continue

        logger.warning(
            "Failed to release concurrency slot for group '%s' after %d attempts",
            self._group_id,
            self._max_retries,
        )

    async def try_acquire(self, timeout: float = 0) -> bool:
        """Try to acquire slot without waiting indefinitely.

        Args:
            timeout: Maximum wait time in seconds (0 = don't wait)

        Returns:
            True if acquired slot within timeout, False otherwise

        Raises:
            RateLimiterNotInitializedError: If rate limiter wasn't initialized
            RateLimiterAcquisitionError: If fail_closed=True and backend fails
        """
        if not self._initialized:
            raise RateLimiterNotInitializedError()

        try:
            if timeout <= 0:
                # Try to acquire immediately without waiting
                rate_ok = await self._acquire_rate_slot() if self._interval else True
                if not rate_ok:
                    self._metrics.record_timeout()
                    return False

                concurrent_ok = (
                    await self._acquire_concurrent_slot() if self._max_concurrent else True
                )
                if not concurrent_ok:
                    self._metrics.record_timeout()
                    return False

                self._metrics.record_acquisition(0.0)
                return True

            # With timeout: use acquire with timeout via asyncio
            await asyncio.wait_for(self.acquire(), timeout=timeout)
            return True

        except TimeoutError:
            self._metrics.record_timeout()
            logger.debug(
                "Rate limit: timeout after %.2fs (group=%s)",
                timeout,
                self._group_id,
            )
            return False
        except (KeyError, OSError, ValueError, ConnectionError) as e:
            if self._fail_closed:
                raise RateLimiterAcquisitionError(
                    f"Backend failure and fail_closed=True for group '{self._group_id}': {e}",
                    group_id=self._group_id,
                ) from e
            # Fail-open: log warning and allow request
            logger.warning(
                "Rate limiter backend failure for group '%s', allowing request (fail_open): %s",
                self._group_id,
                e,
            )
            return True

    def get_metrics(self) -> RateLimiterMetrics:
        """Return rate limiter observability metrics.

        Returns:
            Current snapshot of collected metrics
        """
        return self._metrics

    async def disconnect(self) -> None:
        """Disconnect from NATS server if connection is owned by this instance.

        This method should be called when the rate limiter is no longer needed,
        especially if it was created using auto-connect mode (from_config without
        passing jetstream parameter).

        For pre-connected mode (where jetstream was provided), this method does
        nothing - the caller is responsible for managing the connection lifecycle.
        """
        if self._owned_connection is not None:
            try:
                await self._owned_connection.close()
                logger.info(
                    "Closed auto-connected NATS connection for group '%s'",
                    self._group_id,
                )
            except (OSError, ConnectionError, AttributeError, RuntimeError) as e:
                logger.warning(
                    "Error closing NATS connection for group '%s': %s",
                    self._group_id,
                    e,
                )
            finally:
                self._owned_connection = None

    @property
    def group_id(self) -> str:
        """Return the group ID."""
        return self._group_id

    @property
    def rate_per_second(self) -> float | None:
        """Return the operations per second rate (None = unlimited)."""
        return self._rate

    @property
    def max_concurrent(self) -> int | None:
        """Return the maximum concurrent operations (None = unlimited)."""
        return self._max_concurrent

    @property
    def is_initialized(self) -> bool:
        """Check if the rate limiter was initialized."""
        return self._initialized

    @property
    def default_timeout(self) -> float | None:
        """Return the default timeout in seconds for acquire operations."""
        return self._default_timeout

    @property
    def fail_closed(self) -> bool:
        """Return the fail_closed behavior setting."""
        return self._fail_closed

    @classmethod
    def from_config(
        cls, config: object, group_id: str, rate_per_second: float | None = None, **kwargs
    ) -> "NatsKvRateLimiter":
        """Create NATS KV rate limiter from configuration.

        Supports two modes:
        1. **Pre-connected mode** (advanced): Pass jetstream=js with an existing connection
        2. **Auto-connect mode** (simple): Uses config.url, auto-connects on initialize()

        Args:
            config: NATS engine configuration (NatsEngineConfig)
            group_id: Rate limit group identifier
            rate_per_second: Operations per second allowed (None = unlimited)
            **kwargs: Additional runtime parameters:
                - jetstream: Pre-connected JetStream context (optional)
                - timeout: Default timeout in seconds (optional)
                - max_concurrent: Maximum simultaneous operations (optional)

        Returns:
            Configured NatsKvRateLimiter instance

        Raises:
            ValueError: If neither jetstream nor config.url provided

        Example:
            >>> # Rate limiting only
            >>> limiter = NatsKvRateLimiter.from_config(config, "api", rate_per_second=1.0)
            >>>
            >>> # Concurrency limiting only
            >>> limiter = NatsKvRateLimiter.from_config(config, "api", max_concurrent=10)
            >>>
            >>> # Both (recommended for production)
            >>> limiter = NatsKvRateLimiter.from_config(
            ...     config, "api", rate_per_second=10.0, max_concurrent=5
            ... )
            >>> await limiter.initialize()
        """
        if not isinstance(config, NatsEngineConfig):
            raise ValueError(f"Expected NatsEngineConfig, got {type(config)}")

        jetstream = kwargs.get("jetstream")
        timeout = kwargs.get("timeout", None)
        max_concurrent = kwargs.get("max_concurrent", None)
        fail_closed = kwargs.get("fail_closed", False)

        # Mode 1: Pre-connected (jetstream provided)
        if jetstream is not None:
            # Use provided jetstream directly
            return cls(
                jetstream=jetstream,
                group_id=group_id,
                rate_per_second=rate_per_second,
                max_concurrent=max_concurrent,
                bucket_name=config.bucket_name,
                auto_create=config.auto_create,
                retry_interval=config.retry_interval,
                max_retries=config.max_retries,
                timing_margin_ms=config.timing_margin_ms,
                default_timeout=timeout,
                fail_closed=fail_closed,
            )

        # Mode 2: Auto-connect (store config for lazy connection)
        if not config.url:
            raise ValueError(
                "Either provide 'jetstream' runtime parameter (pre-connected mode) "
                "or set 'url' in configuration (auto-connect mode)"
            )

        # Create a placeholder instance that will connect on initialize()
        # We use a dummy jetstream for now
        instance = cls.__new__(cls)

        # Store all parameters for later initialization
        instance._config_url = config.url
        instance._config_token = config.token
        instance._group_id = group_id.strip()
        instance._rate = rate_per_second
        instance._max_concurrent = max_concurrent
        instance._interval = 1.0 / rate_per_second if rate_per_second else None
        instance._bucket_name = config.bucket_name
        instance._auto_create = config.auto_create
        instance._retry_interval = config.retry_interval
        instance._max_retries = config.max_retries
        instance._timing_margin_s = config.timing_margin_ms / 1000.0
        instance._default_timeout = timeout
        instance._fail_closed = fail_closed
        instance._ttl = max(int(1.0 / rate_per_second * 3), 10) if rate_per_second else 60
        instance._kv = None
        instance._initialized = False
        instance._metrics = RateLimiterMetrics()
        instance._js = None  # Will be set on initialize()
        instance._owned_connection = None  # Will be set on initialize()
        instance._needs_auto_connect = True

        return instance
