"""Prometheus metrics definitions for rate-sync.

This module defines all Prometheus metrics used by rate-sync and provides
functions to record metric values. Metrics are lazily initialized to avoid
import errors when prometheus-client is not installed.

Metrics:
    ratesync_acquisitions_total: Counter of successful rate limit acquisitions
    ratesync_acquisition_duration_seconds: Histogram of wait times for acquisitions
    ratesync_timeouts_total: Counter of rate limit timeouts
    ratesync_redis_operation_duration_seconds: Histogram of Redis operation latencies
    ratesync_circuit_breaker_state: Gauge of circuit breaker state (0=closed, 1=half_open, 2=open)
    ratesync_fallback_activations_total: Counter of fallback activations
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# Try to import prometheus_client classes at module level
try:
    from prometheus_client import Counter as _Counter
    from prometheus_client import Gauge as _Gauge
    from prometheus_client import Histogram as _Histogram

    _PROMETHEUS_CLASSES: dict[str, Any] | None = {
        "Counter": _Counter,
        "Gauge": _Gauge,
        "Histogram": _Histogram,
    }
except ImportError:
    _PROMETHEUS_CLASSES = None


# Configurable settings
NAMESPACE = "ratesync"
DEFAULT_LATENCY_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
)

# Shorter buckets for Redis operations (typically < 100ms)
REDIS_LATENCY_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    1.0,
)


class _MetricsState:
    """Encapsulates metrics state to avoid global variables."""

    def __init__(self) -> None:
        self.initialized: bool = False
        self.acquisitions_total: Counter | None = None
        self.acquisition_duration: Histogram | None = None
        self.timeouts_total: Counter | None = None
        self.redis_duration: Histogram | None = None
        self.circuit_breaker_state: Gauge | None = None
        self.fallback_activations: Counter | None = None


_state = _MetricsState()


def is_enabled() -> bool:
    """Check if Prometheus metrics are enabled.

    Returns:
        True if metrics have been initialized and are being recorded.
    """
    return _state.initialized


def _init_metrics() -> None:
    """Initialize Prometheus metrics (lazy).

    This function is idempotent - calling it multiple times has no effect
    after the first successful initialization.
    """
    if _state.initialized:
        return

    if _PROMETHEUS_CLASSES is None:
        logger.debug("prometheus-client not installed, metrics disabled")
        return

    counter_cls = _PROMETHEUS_CLASSES["Counter"]
    gauge_cls = _PROMETHEUS_CLASSES["Gauge"]
    histogram_cls = _PROMETHEUS_CLASSES["Histogram"]

    _state.acquisitions_total = counter_cls(
        f"{NAMESPACE}_acquisitions_total",
        "Total number of successful rate limit acquisitions",
        ["group_id", "engine"],
    )

    _state.acquisition_duration = histogram_cls(
        f"{NAMESPACE}_acquisition_duration_seconds",
        "Time spent waiting for rate limit slot",
        ["group_id", "engine"],
        buckets=DEFAULT_LATENCY_BUCKETS,
    )

    _state.timeouts_total = counter_cls(
        f"{NAMESPACE}_timeouts_total",
        "Total number of rate limit timeouts",
        ["group_id", "engine"],
    )

    _state.redis_duration = histogram_cls(
        f"{NAMESPACE}_redis_operation_duration_seconds",
        "Redis operation latency",
        ["group_id", "operation"],
        buckets=REDIS_LATENCY_BUCKETS,
    )

    _state.circuit_breaker_state = gauge_cls(
        f"{NAMESPACE}_circuit_breaker_state",
        "Circuit breaker state (0=closed, 1=half_open, 2=open)",
        ["group_id"],
    )

    _state.fallback_activations = counter_cls(
        f"{NAMESPACE}_fallback_activations_total",
        "Number of times fallback was activated due to Redis failure",
        ["group_id"],
    )

    _state.initialized = True
    logger.info("Prometheus metrics initialized for rate-sync")


def record_acquisition(group_id: str, engine: str, duration_seconds: float) -> None:
    """Record a successful rate limit acquisition.

    Args:
        group_id: The rate limiter group identifier
        engine: The engine type (e.g., "redis", "memory", "postgres", "nats")
        duration_seconds: Time spent waiting for the slot (in seconds)
    """
    if not _state.initialized:
        return
    if _state.acquisitions_total is not None:
        _state.acquisitions_total.labels(group_id=group_id, engine=engine).inc()
    if _state.acquisition_duration is not None:
        _state.acquisition_duration.labels(group_id=group_id, engine=engine).observe(
            duration_seconds
        )


def record_timeout(group_id: str, engine: str) -> None:
    """Record a rate limit timeout.

    Args:
        group_id: The rate limiter group identifier
        engine: The engine type (e.g., "redis", "memory", "postgres", "nats")
    """
    if not _state.initialized:
        return
    if _state.timeouts_total is not None:
        _state.timeouts_total.labels(group_id=group_id, engine=engine).inc()


def record_redis_operation(group_id: str, operation: str, duration_seconds: float) -> None:
    """Record Redis operation latency.

    Args:
        group_id: The rate limiter group identifier
        operation: The operation type (e.g., "acquire", "script", "ping")
        duration_seconds: Operation duration in seconds
    """
    if not _state.initialized:
        return
    if _state.redis_duration is not None:
        _state.redis_duration.labels(group_id=group_id, operation=operation).observe(
            duration_seconds
        )


def set_circuit_breaker_state(group_id: str, state: int) -> None:
    """Set the circuit breaker state for a rate limiter.

    Args:
        group_id: The rate limiter group identifier
        state: The state value (0=closed, 1=half_open, 2=open)
    """
    if not _state.initialized:
        return
    if _state.circuit_breaker_state is not None:
        _state.circuit_breaker_state.labels(group_id=group_id).set(state)


def record_fallback_activation(group_id: str) -> None:
    """Record when fallback mode is activated due to Redis failure.

    Args:
        group_id: The rate limiter group identifier
    """
    if not _state.initialized:
        return
    if _state.fallback_activations is not None:
        _state.fallback_activations.labels(group_id=group_id).inc()
