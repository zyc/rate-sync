"""FastAPI integration for Prometheus metrics.

This module provides helpers for integrating rate-sync Prometheus metrics
with FastAPI applications.

Usage:
    from fastapi import FastAPI
    from ratesync.contrib.prometheus.fastapi import add_metrics_endpoint

    app = FastAPI()
    add_metrics_endpoint(app)  # Adds GET /metrics endpoint

If you already have a metrics endpoint (like Discovery), just call enable_metrics():
    from ratesync.contrib.prometheus import enable_metrics
    enable_metrics()  # Metrics will appear in your existing endpoint
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ratesync.contrib.prometheus import PROMETHEUS_AVAILABLE, enable_metrics

if TYPE_CHECKING:
    from fastapi import FastAPI


# Try to import FastAPI Response at module level
try:
    from fastapi import Response as _Response

    _FASTAPI_RESPONSE: type | None = _Response
except ImportError:
    _FASTAPI_RESPONSE = None

# Try to import prometheus_client functions at module level
try:
    from prometheus_client import CONTENT_TYPE_LATEST as _CONTENT_TYPE_LATEST
    from prometheus_client import generate_latest as _generate_latest

    _PROMETHEUS_GENERATE: tuple[Callable[[], bytes], str] | None = (
        _generate_latest,
        _CONTENT_TYPE_LATEST,
    )
except ImportError:
    _PROMETHEUS_GENERATE = None


def add_metrics_endpoint(
    app: "FastAPI",
    path: str = "/metrics",
    include_in_schema: bool = False,
) -> bool:
    """Add Prometheus metrics endpoint to FastAPI app.

    This function adds a GET endpoint that returns Prometheus metrics in the
    text exposition format. It also enables metrics collection automatically.

    If prometheus-client is not installed, this function returns False and
    no endpoint is added.

    Args:
        app: FastAPI application instance
        path: Endpoint path (default: /metrics)
        include_in_schema: Include in OpenAPI schema (default: False)

    Returns:
        True if endpoint was added, False if prometheus-client not installed.

    Example:
        >>> from fastapi import FastAPI
        >>> from ratesync.contrib.prometheus.fastapi import add_metrics_endpoint
        >>> app = FastAPI()
        >>> if add_metrics_endpoint(app):
        ...     print("Metrics endpoint added at /metrics")
    """
    if not PROMETHEUS_AVAILABLE:
        return False

    # Enable metrics collection
    enable_metrics()

    if _FASTAPI_RESPONSE is None or _PROMETHEUS_GENERATE is None:
        return False

    generate_latest, content_type_latest = _PROMETHEUS_GENERATE

    @app.get(path, include_in_schema=include_in_schema)
    def prometheus_metrics() -> Any:
        """Prometheus metrics endpoint."""
        return _FASTAPI_RESPONSE(
            content=generate_latest(),
            media_type=content_type_latest,
        )

    return True
