"""
Integration test that ensures PostgreSQL-backed limiters enforce shared rate windows.

Skipped unless POSTGRES_INTEGRATION_URL points to a real database. The test
creates and drops a temporary table automatically.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

asyncpg_module = pytest.importorskip(
    "asyncpg",
    reason="asyncpg is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_limiters_share_slots() -> None:
    """Multiple instances pointing to same table must serialize acquisitions."""
    import asyncpg

    from ratesync.engines.postgres import PostgresRateLimiter

    async def acquire_and_record(limiter: PostgresRateLimiter) -> float:
        """Acquire a slot and return the completion timestamp."""
        await limiter.acquire()
        return time.perf_counter()

    url = os.getenv("POSTGRES_INTEGRATION_URL")
    if not url:
        pytest.skip("Set POSTGRES_INTEGRATION_URL to run PostgreSQL integration tests")

    schema = os.getenv("POSTGRES_INTEGRATION_SCHEMA", "public")
    table_name = os.getenv(
        "POSTGRES_INTEGRATION_TABLE",
        f"rate_limiter_it_{uuid.uuid4().hex[:8]}",
    )
    group_id = f"it_pg_{uuid.uuid4().hex[:6]}"

    limiter_a: PostgresRateLimiter | None = None
    limiter_b: PostgresRateLimiter | None = None

    try:
        limiter_a = PostgresRateLimiter(
            connection_url=url,
            group_id=group_id,
            rate_per_second=1.0,
            table_name=table_name,
            schema_name=schema,
            auto_create=True,
            pool_min_size=1,
            pool_max_size=2,
        )
        limiter_b = PostgresRateLimiter(
            connection_url=url,
            group_id=group_id,
            rate_per_second=1.0,
            table_name=table_name,
            schema_name=schema,
            auto_create=False,
            pool_min_size=1,
            pool_max_size=2,
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

        # Drop temporary table to keep database clean
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}"')
        finally:
            await conn.close()
