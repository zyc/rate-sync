"""
Integration test that ensures PostgreSQL-backed limiters enforce shared rate windows.

Skipped unless POSTGRES_URL points to a real database. The test
creates and drops temporary tables automatically.

Run with docker compose:
    docker compose up -d
    export POSTGRES_URL="postgresql://postgres:postgres@localhost:5432/ratesync"
    poetry run pytest -m postgres
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

# Support both POSTGRES_URL (preferred) and legacy POSTGRES_INTEGRATION_URL
POSTGRES_URL = os.environ.get(
    "POSTGRES_URL",
    os.environ.get("POSTGRES_INTEGRATION_URL", ""),
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

    if not POSTGRES_URL:
        pytest.skip("Set POSTGRES_URL to run PostgreSQL integration tests")

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
            connection_url=POSTGRES_URL,
            group_id=group_id,
            rate_per_second=1.0,
            table_name=table_name,
            schema_name=schema,
            auto_create=True,
            pool_min_size=1,
            pool_max_size=2,
        )
        limiter_b = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
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

        # Drop temporary tables to keep database clean (state + window tables)
        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}"')
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}_window"')
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_postgres_sliding_window() -> None:
    """Test sliding window algorithm with PostgreSQL backend."""
    import asyncpg

    from ratesync.engines.postgres import PostgresRateLimiter

    if not POSTGRES_URL:
        pytest.skip("Set POSTGRES_URL to run PostgreSQL integration tests")

    schema = "public"
    table_name = f"rate_limiter_sw_{uuid.uuid4().hex[:8]}"
    group_id = f"sw_pg_{uuid.uuid4().hex[:6]}"

    limiter: PostgresRateLimiter | None = None

    try:
        # Create limiter with sliding window: 5 requests per 2 seconds
        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id=group_id,
            limit=5,
            window_seconds=2,
            table_name=table_name,
            schema_name=schema,
            auto_create=True,
            pool_min_size=1,
            pool_max_size=2,
        )

        await limiter.initialize()

        # Verify algorithm is set correctly
        assert limiter.algorithm == "sliding_window"
        assert limiter.limit == 5
        assert limiter.window_seconds == 2

        # Acquire 5 slots (should all succeed quickly)
        start = time.perf_counter()
        for _ in range(5):
            acquired = await limiter.try_acquire(timeout=1.0)
            assert acquired, "Should acquire within limit"

        # 6th should fail immediately (window is full)
        acquired = await limiter.try_acquire(timeout=0.1)
        assert not acquired, "Should be rate limited after 5 requests"

        elapsed = time.perf_counter() - start
        assert elapsed < 1.5, f"5 quick acquisitions took too long: {elapsed:.2f}s"

        # Wait for window to expire and try again
        await asyncio.sleep(2.1)
        acquired = await limiter.try_acquire(timeout=1.0)
        assert acquired, "Should succeed after window expires"

    finally:
        if limiter is not None:
            await limiter.disconnect()

        # Cleanup tables
        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}"')
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}_window"')
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_postgres_sliding_window_distributed() -> None:
    """Test sliding window with multiple PostgreSQL limiter instances."""
    import asyncpg

    from ratesync.engines.postgres import PostgresRateLimiter

    if not POSTGRES_URL:
        pytest.skip("Set POSTGRES_URL to run PostgreSQL integration tests")

    schema = "public"
    table_name = f"rate_limiter_swd_{uuid.uuid4().hex[:8]}"
    group_id = f"swd_pg_{uuid.uuid4().hex[:6]}"

    limiters: list[PostgresRateLimiter] = []

    try:
        # Create 3 limiter instances pointing to the same table
        # Limit: 10 requests per 5 seconds
        for i in range(3):
            limiter = PostgresRateLimiter(
                connection_url=POSTGRES_URL,
                group_id=group_id,
                limit=10,
                window_seconds=5,
                table_name=table_name,
                schema_name=schema,
                auto_create=(i == 0),  # Only first creates table
                pool_min_size=1,
                pool_max_size=2,
            )
            await limiter.initialize()
            limiters.append(limiter)

        # Each limiter tries to acquire 5 slots concurrently
        async def acquire_many(limiter: PostgresRateLimiter, count: int) -> int:
            """Try to acquire count slots, return how many succeeded."""
            successes = 0
            for _ in range(count):
                if await limiter.try_acquire(timeout=0.5):
                    successes += 1
            return successes

        # Run all 3 limiters concurrently, each trying to get 5 slots
        results = await asyncio.gather(
            acquire_many(limiters[0], 5),
            acquire_many(limiters[1], 5),
            acquire_many(limiters[2], 5),
        )

        total = sum(results)
        # Should get exactly 10 (the limit) with some tolerance for timing
        assert 9 <= total <= 11, f"Expected ~10 total, got {total}: {results}"

    finally:
        for limiter in limiters:
            await limiter.disconnect()

        # Cleanup tables
        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}"')
            await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}_window"')
        finally:
            await conn.close()
