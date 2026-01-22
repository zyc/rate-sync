"""Prometheus metrics integration for rate-sync.

This module provides Prometheus metrics collection for rate limiting operations.
Requires the `prometheus-client` package to be installed.

Installation:
    pip install 'rate-sync[prometheus]'

Usage:
    from ratesync.contrib.prometheus import enable_metrics

    # Enable metrics collection (call once at startup)
    enable_metrics()

    # Metrics are automatically recorded when using rate limiters
    # They appear in the default Prometheus registry

For FastAPI applications:
    from ratesync.contrib.prometheus.fastapi import add_metrics_endpoint
    add_metrics_endpoint(app)

For workers (push gateway):
    from ratesync.contrib.prometheus.pushgateway import push_to_gateway
    await push_to_gateway("http://pushgateway:9091", job="my-worker")
"""

from ratesync.contrib.prometheus.metrics import _init_metrics, is_enabled

# Check if prometheus-client is available
try:
    import prometheus_client  # noqa: F401

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


def enable_metrics() -> bool:
    """Enable Prometheus metrics collection.

    This function initializes the Prometheus metrics. It should be called
    once at application startup. If prometheus-client is not installed,
    this function returns False and metrics collection is disabled.

    Returns:
        True if metrics were enabled, False if prometheus-client not installed.

    Example:
        >>> from ratesync.contrib.prometheus import enable_metrics
        >>> if enable_metrics():
        ...     print("Prometheus metrics enabled")
        ... else:
        ...     print("prometheus-client not installed")
    """
    if not PROMETHEUS_AVAILABLE:
        return False
    _init_metrics()
    return True


__all__ = ["enable_metrics", "is_enabled", "PROMETHEUS_AVAILABLE"]
