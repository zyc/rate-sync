"""
Integration test verifying that NATS-backed limiters share the same rate window.

Skipped by default unless `NATS_INTEGRATION_URL` points to a reachable server.
Optional environment variables:
    NATS_INTEGRATION_TOKEN  - token/credentials for the server
    NATS_INTEGRATION_BUCKET - bucket name (random suffix by default)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.nats]

nats_module = pytest.importorskip(
    "nats",
    reason="nats-py extras are required for NATS integration tests",
)


@pytest.mark.asyncio
async def test_nats_limiters_share_slots() -> None:
    """Two different limiter instances must serialize acquisitions."""
    from nats import connect
    from nats.js.errors import BadRequestError

    from ratesync.engines.nats import NatsKvRateLimiter

    async def acquire_and_record(limiter: NatsKvRateLimiter) -> float:
        """Acquire a slot and return the completion timestamp."""
        await limiter.acquire()
        return time.perf_counter()

    url = os.getenv("NATS_INTEGRATION_URL")
    if not url:
        pytest.skip("Set NATS_INTEGRATION_URL to run NATS integration tests")

    token = os.getenv("NATS_INTEGRATION_TOKEN")
    bucket_name = os.getenv("NATS_INTEGRATION_BUCKET", f"rate_sync_it_{uuid.uuid4().hex[:8]}")
    group_id = f"it_nats_{uuid.uuid4().hex[:6]}"

    nc = await connect(url, token=token) if token else await connect(url)
    js = nc.jetstream()

    limiter_a: NatsKvRateLimiter | None = None
    limiter_b: NatsKvRateLimiter | None = None

    try:
        try:
            await js.delete_key_value(bucket_name)
        except BadRequestError:
            pass

        limiter_a = NatsKvRateLimiter(
            jetstream=js,
            group_id=group_id,
            rate_per_second=1.0,
            bucket_name=bucket_name,
            auto_create=True,
            retry_interval=0.01,
        )
        limiter_b = NatsKvRateLimiter(
            jetstream=js,
            group_id=group_id,
            rate_per_second=1.0,
            bucket_name=bucket_name,
        )

        await limiter_a.initialize()
        await limiter_b.initialize()

        first, second = await asyncio.gather(
            acquire_and_record(limiter_a),
            acquire_and_record(limiter_b),
        )
        delta = abs(second - first)

        assert delta >= 0.9, f"Expected roughly 1s separation, got {delta:.3f}s"

    finally:
        if limiter_a is not None:
            await limiter_a.disconnect()
        if limiter_b is not None:
            await limiter_b.disconnect()
        try:
            await js.delete_key_value(bucket_name)
        except BadRequestError:
            pass
        await nc.close()
