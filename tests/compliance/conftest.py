"""Fixtures for engine compliance tests.

This module provides factory fixtures that create engine instances for testing.
Each engine type provides a factory that can create limiters with different
configurations (token_bucket, sliding_window, concurrency).

Usage:
    @pytest.mark.parametrize("engine_name", get_engines_with("token_bucket"))
    async def test_something(get_factory):
        factory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)
        # test...
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

import pytest

# Import utilities
from compliance.utils import EngineFactory

if TYPE_CHECKING:
    from ratesync.core import RateLimiter


# =============================================================================
# ENVIRONMENT DETECTION
# =============================================================================


def is_redis_available() -> bool:
    """Check if Redis library is installed."""
    try:
        import redis.asyncio  # noqa: F401

        return True
    except ImportError:
        return False


def is_postgres_available() -> bool:
    """Check if asyncpg library is installed."""
    try:
        import asyncpg  # noqa: F401

        return True
    except ImportError:
        return False


REDIS_AVAILABLE = is_redis_available()
POSTGRES_AVAILABLE = is_postgres_available()


# =============================================================================
# MEMORY ENGINE FACTORY
# =============================================================================


@pytest.fixture
async def memory_engine_factory() -> AsyncIterator[EngineFactory]:
    """Factory for MemoryRateLimiter instances.

    Memory engine requires no infrastructure and supports all features.
    """
    from ratesync.engines.memory import MemoryRateLimiter

    created_limiters: list[MemoryRateLimiter] = []

    async def factory(
        group_id: str | None = None,
        *,
        rate_per_second: float | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        max_concurrent: int | None = None,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> MemoryRateLimiter:
        if group_id is None:
            group_id = f"test_{uuid.uuid4().hex[:8]}"

        limiter = MemoryRateLimiter(
            group_id=group_id,
            rate_per_second=rate_per_second,
            limit=limit,
            window_seconds=window_seconds,
            max_concurrent=max_concurrent,
            default_timeout=default_timeout,
            fail_closed=fail_closed,
        )
        await limiter.initialize()
        created_limiters.append(limiter)
        return limiter

    yield factory

    # Cleanup
    for limiter in created_limiters:
        try:
            await limiter.reset()
        except Exception:
            pass


# =============================================================================
# REDIS ENGINE FACTORY
# =============================================================================


@pytest.fixture
async def redis_engine_factory(redis_url: str) -> AsyncIterator[EngineFactory]:
    """Factory for RedisRateLimiter instances.

    Only available when Redis library is installed.
    Skips tests if Redis is not accessible.
    """
    if not REDIS_AVAILABLE:
        pytest.skip("Redis library not installed")

    from ratesync.engines.redis import RedisRateLimiter

    created_limiters: list[RedisRateLimiter] = []
    unique_prefix = f"test_{uuid.uuid4().hex[:8]}"

    async def factory(
        group_id: str | None = None,
        *,
        rate_per_second: float | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        max_concurrent: int | None = None,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> RedisRateLimiter:
        if group_id is None:
            group_id = f"test_{uuid.uuid4().hex[:8]}"

        limiter = RedisRateLimiter(
            url=redis_url,
            group_id=group_id,
            rate_per_second=rate_per_second,
            limit=limit,
            window_seconds=window_seconds,
            max_concurrent=max_concurrent,
            default_timeout=default_timeout,
            fail_closed=fail_closed,
            key_prefix=unique_prefix,
        )

        try:
            await limiter.initialize()
        except Exception as e:
            pytest.skip(f"Redis not accessible: {e}")

        created_limiters.append(limiter)
        return limiter

    yield factory

    # Cleanup
    for limiter in created_limiters:
        try:
            await limiter.reset()
            await limiter.disconnect()
        except Exception:
            pass


# =============================================================================
# POSTGRES ENGINE FACTORY
# =============================================================================


@pytest.fixture
async def postgres_engine_factory(postgres_url: str) -> AsyncIterator[EngineFactory]:
    """Factory for PostgresRateLimiter instances.

    Only available when asyncpg library is installed.
    Skips tests if PostgreSQL is not accessible.
    """
    if not POSTGRES_AVAILABLE:
        pytest.skip("asyncpg library not installed")

    from ratesync.engines.postgres import PostgresRateLimiter

    created_limiters: list[PostgresRateLimiter] = []
    table_names: list[str] = []

    async def factory(
        group_id: str | None = None,
        *,
        rate_per_second: float | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        max_concurrent: int | None = None,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> PostgresRateLimiter:
        if group_id is None:
            group_id = f"test_{uuid.uuid4().hex[:8]}"

        table_name = f"rate_limiter_test_{uuid.uuid4().hex[:8]}"
        table_names.append(table_name)

        limiter = PostgresRateLimiter(
            connection_url=postgres_url,
            group_id=group_id,
            rate_per_second=rate_per_second,
            limit=limit,
            window_seconds=window_seconds,
            max_concurrent=max_concurrent,
            default_timeout=default_timeout,
            table_name=table_name,
            auto_create=True,
            fail_closed=fail_closed,
        )

        try:
            await limiter.initialize()
        except Exception as e:
            pytest.skip(f"PostgreSQL not accessible: {e}")

        created_limiters.append(limiter)
        return limiter

    yield factory

    # Cleanup limiters
    for limiter in created_limiters:
        try:
            await limiter.disconnect()
        except Exception:
            pass

    # Drop test tables
    if table_names:
        try:
            import asyncpg

            conn = await asyncpg.connect(postgres_url)
            for table_name in table_names:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}_window"')
            await conn.close()
        except Exception:
            pass


# =============================================================================
# PARAMETRIZED ENGINE FIXTURE
# =============================================================================


@pytest.fixture
async def engine_factory(
    request,
    memory_engine_factory,
    redis_engine_factory,
    postgres_engine_factory,
) -> EngineFactory:
    """Parametrized engine factory.

    Use with @pytest.mark.parametrize("engine_name", [...])
    and request the engine_factory fixture.

    The engine_name parameter must be set via indirect parametrization:
        @pytest.mark.parametrize("engine_factory", ["memory"], indirect=True)

    Or use the helper pattern with engine_name:
        @pytest.mark.parametrize("engine_name", get_all_engines())
        async def test_something(engine_name, request):
            factory = request.getfixturevalue(f"{engine_name}_engine_factory")
    """
    engine_name = getattr(request, "param", "memory")

    factories = {
        "memory": memory_engine_factory,
        "redis": redis_engine_factory,
        "postgres": postgres_engine_factory,
    }

    if engine_name not in factories:
        pytest.fail(f"Unknown engine: {engine_name}")

    return factories[engine_name]


# =============================================================================
# HELPER FIXTURE FOR ENGINE NAME PARAMETRIZATION
# =============================================================================


@pytest.fixture
def get_factory(
    request,
    memory_engine_factory,
    redis_engine_factory,
    postgres_engine_factory,
) -> Callable[[str], EngineFactory]:
    """Helper to get factory by engine name.

    Usage:
        @pytest.mark.parametrize("engine_name", get_all_engines())
        async def test_something(engine_name, get_factory):
            factory = get_factory(engine_name)
            limiter = await factory(rate_per_second=10.0)
    """
    factories = {
        "memory": memory_engine_factory,
        "redis": redis_engine_factory,
        "postgres": postgres_engine_factory,
    }

    def _get_factory(engine_name: str) -> EngineFactory:
        if engine_name not in factories:
            pytest.fail(f"Unknown engine: {engine_name}")
        return factories[engine_name]

    return _get_factory
