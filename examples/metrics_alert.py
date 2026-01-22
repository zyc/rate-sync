#!/usr/bin/env python3
"""
Simple monitoring loop for rate-sync limiters.

This script polls `RateLimiterMetrics` for every configured limiter and emits
alerts when either the average wait time or the number of timeouts crosses the
threshold defined by environment variables.

Usage:
    RATE_SYNC_MAX_AVG_WAIT_MS=1500 RATE_SYNC_MAX_TIMEOUTS=3 python examples/metrics_alert.py

Environment variables:
    RATE_SYNC_ALERT_INTERVAL   - Poll interval in seconds (default: 5)
    RATE_SYNC_MAX_AVG_WAIT_MS  - Alert when avg wait exceeds this value (default: 1000)
    RATE_SYNC_MAX_TIMEOUTS     - Alert when total timeouts reach this value (default: 5)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable

from ratesync import get_limiter, initialize_all_limiters, list_limiters

POLL_INTERVAL = float(os.getenv("RATE_SYNC_ALERT_INTERVAL", "5"))
MAX_AVG_WAIT_MS = float(os.getenv("RATE_SYNC_MAX_AVG_WAIT_MS", "1000"))
MAX_TIMEOUTS = int(os.getenv("RATE_SYNC_MAX_TIMEOUTS", "5"))

logger = logging.getLogger("rate_sync.metrics_alert")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def emit_alert(limiter_id: str, avg_wait_ms: float, timeouts: int) -> None:
    """Emit a structured log that monitoring backends can scrape."""
    logger.warning(
        "Limiter %s nearing capacity (avg_wait_ms=%.2f, timeouts=%d)",
        limiter_id,
        avg_wait_ms,
        timeouts,
    )


async def poll_limiters(limiter_ids: Iterable[str]) -> None:
    """Continuously poll metrics for the provided limiter IDs."""
    while True:
        for limiter_id in limiter_ids:
            limiter = get_limiter(limiter_id)
            metrics = limiter.get_metrics()

            if metrics.avg_wait_time_ms >= MAX_AVG_WAIT_MS or metrics.timeouts >= MAX_TIMEOUTS:
                await emit_alert(limiter_id, metrics.avg_wait_time_ms, metrics.timeouts)

        await asyncio.sleep(POLL_INTERVAL)


async def monitor_all_configured_limiters() -> None:
    """Discover limiters dynamically before starting the monitoring loop."""
    limiter_ids = list(list_limiters().keys())
    if not limiter_ids:
        logger.warning("No limiters configured — nothing to monitor.")
        return

    logger.info("Monitoring limiters: %s", ", ".join(limiter_ids))
    await poll_limiters(limiter_ids)


async def main() -> None:
    """Initialize all limiters and start monitoring."""
    await initialize_all_limiters()
    await monitor_all_configured_limiters()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Metrics monitor stopped by user.")
