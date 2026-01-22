"""Push Gateway integration for workers.

This module provides functions to push metrics to a Prometheus Push Gateway,
which is useful for short-lived jobs and workers that don't expose HTTP endpoints.

Usage:
    from ratesync.contrib.prometheus import enable_metrics
    from ratesync.contrib.prometheus.pushgateway import push_to_gateway, start_periodic_push

    # Enable metrics collection
    enable_metrics()

    # Option 1: Push on demand (e.g., at shutdown)
    await push_to_gateway("http://pushgateway:9091", job="my-worker")

    # Option 2: Periodic push (e.g., every 30 seconds)
    stop_event = await start_periodic_push(
        "http://pushgateway:9091",
        job="my-worker",
        interval=30.0,
    )
    # ... do work ...
    stop_event.set()  # Stop periodic push
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ratesync.contrib.prometheus import PROMETHEUS_AVAILABLE, enable_metrics

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Try to import prometheus_client functions at module level
try:
    from prometheus_client import REGISTRY as _REGISTRY
    from prometheus_client import delete_from_gateway as _delete_from_gateway
    from prometheus_client import push_to_gateway as _push_to_gateway

    _PROMETHEUS_PUSH: tuple[Any, Callable[..., None]] | None = (_REGISTRY, _push_to_gateway)
    _PROMETHEUS_DELETE: Callable[..., None] | None = _delete_from_gateway
except ImportError:
    _PROMETHEUS_PUSH = None
    _PROMETHEUS_DELETE = None


async def push_to_gateway(
    gateway_url: str,
    job: str,
    grouping_key: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> bool:
    """Push metrics to Prometheus Push Gateway.

    This function pushes all metrics from the default registry to the
    specified Push Gateway. It runs the synchronous push operation in
    a thread pool to avoid blocking the event loop.

    Args:
        gateway_url: Push Gateway URL (e.g., "http://pushgateway:9091")
        job: Job name for grouping metrics
        grouping_key: Additional grouping labels (e.g., {"instance": "worker-1"})
        timeout: HTTP timeout in seconds

    Returns:
        True if push succeeded, False otherwise.

    Example:
        >>> await push_to_gateway(
        ...     "http://pushgateway:9091",
        ...     job="etl-extract",
        ...     grouping_key={"instance": "worker-1"},
        ... )
    """
    if not PROMETHEUS_AVAILABLE:
        logger.warning("prometheus-client not installed, cannot push metrics")
        return False

    # Enable metrics if not already
    enable_metrics()

    try:
        if _PROMETHEUS_PUSH is None:
            logger.warning("prometheus-client push_to_gateway not available")
            return False

        registry, sync_push = _PROMETHEUS_PUSH

        # Run sync push in thread pool
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: sync_push(
                gateway_url,
                job=job,
                registry=registry,
                grouping_key=dict(grouping_key) if grouping_key else {},
                timeout=timeout,
            ),
        )

        logger.debug("Metrics pushed to %s for job %s", gateway_url, job)
        return True

    except Exception as exc:
        logger.error("Failed to push metrics to %s: %s", gateway_url, exc)
        return False


class PeriodicPushHandle:
    """Handle for controlling periodic push to Push Gateway.

    This class provides methods to stop the periodic push and wait for
    it to complete gracefully.

    Attributes:
        event: The asyncio.Event used to signal stop
        task: The background task running the periodic push
    """

    def __init__(self, event: asyncio.Event, task: asyncio.Task) -> None:
        self.event = event
        self.task = task

    def set(self) -> None:
        """Signal the periodic push to stop (compatibility with asyncio.Event)."""
        self.event.set()

    def is_set(self) -> bool:
        """Check if stop has been signaled (compatibility with asyncio.Event)."""
        return self.event.is_set()

    async def stop(self) -> None:
        """Stop the periodic push and wait for it to complete.

        This method signals the push to stop and waits for the final push
        to complete before returning.
        """
        self.event.set()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    def cancel(self) -> None:
        """Cancel the periodic push immediately without waiting."""
        self.event.set()
        self.task.cancel()


async def start_periodic_push(
    gateway_url: str,
    job: str,
    interval: float = 30.0,
    grouping_key: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> PeriodicPushHandle:
    """Start periodic metric push to Push Gateway.

    This function starts a background task that periodically pushes metrics
    to the Push Gateway. The task runs until the returned handle is stopped.

    Args:
        gateway_url: Push Gateway URL (e.g., "http://pushgateway:9091")
        job: Job name for grouping metrics
        interval: Push interval in seconds (default: 30.0)
        grouping_key: Additional grouping labels
        timeout: HTTP timeout in seconds per push

    Returns:
        A PeriodicPushHandle that can be used to stop the periodic push.
        The handle has the following methods:
        - set(): Signal stop (backwards compatible with asyncio.Event)
        - stop(): Signal stop and wait for completion
        - cancel(): Cancel immediately without waiting

    Example:
        >>> handle = await start_periodic_push(
        ...     "http://pushgateway:9091",
        ...     job="etl-extract",
        ...     interval=30.0,
        ... )
        >>> # ... do work ...
        >>> await handle.stop()  # Stop and wait for final push
        >>>
        >>> # Or for backwards compatibility:
        >>> handle.set()  # Just signal stop (don't wait)
    """
    stop_event = asyncio.Event()

    async def _periodic_push() -> None:
        logger.info(
            "Starting periodic metric push to %s every %.1fs",
            gateway_url,
            interval,
        )
        while not stop_event.is_set():
            await push_to_gateway(
                gateway_url,
                job=job,
                grouping_key=grouping_key,
                timeout=timeout,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                # Normal timeout - continue pushing
                pass

        # Final push on stop
        logger.info("Stopping periodic metric push, final push...")
        await push_to_gateway(
            gateway_url,
            job=job,
            grouping_key=grouping_key,
            timeout=timeout,
        )

    # Start background task
    task = asyncio.create_task(_periodic_push())

    return PeriodicPushHandle(stop_event, task)


async def delete_from_gateway(
    gateway_url: str,
    job: str,
    grouping_key: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> bool:
    """Delete metrics from Push Gateway.

    This function removes metrics previously pushed with the same job
    and grouping key. Useful for cleanup when a worker shuts down.

    Args:
        gateway_url: Push Gateway URL (e.g., "http://pushgateway:9091")
        job: Job name used when pushing
        grouping_key: Grouping labels used when pushing
        timeout: HTTP timeout in seconds

    Returns:
        True if delete succeeded, False otherwise.
    """
    if not PROMETHEUS_AVAILABLE:
        logger.warning("prometheus-client not installed, cannot delete metrics")
        return False

    try:
        if _PROMETHEUS_DELETE is None:
            logger.warning("prometheus-client delete_from_gateway not available")
            return False

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: _PROMETHEUS_DELETE(
                gateway_url,
                job=job,
                grouping_key=dict(grouping_key) if grouping_key else {},
                timeout=timeout,
            ),
        )

        logger.debug("Metrics deleted from %s for job %s", gateway_url, job)
        return True

    except Exception as exc:
        logger.error("Failed to delete metrics from %s: %s", gateway_url, exc)
        return False
