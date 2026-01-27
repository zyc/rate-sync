"""PostgreSQL engine specific tests.

Tests for behavior unique to the PostgreSQL engine, such as:
- Connection pooling
- Table auto-creation
- Schema configuration
- Distributed coordination
"""

from __future__ import annotations

import os
import uuid

import pytest

import sys
from pathlib import Path

# Add tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from conftest import POSTGRES_AVAILABLE


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(not POSTGRES_AVAILABLE, reason="asyncpg library not installed"),
]


POSTGRES_URL = os.environ.get(
    "POSTGRES_URL",
    os.environ.get(
        "POSTGRES_INTEGRATION_URL",
        "postgresql://postgres:postgres@localhost:5432/ratesync",
    ),
)


class TestPostgresEngineSpecific:
    """Tests for PostgreSQL engine implementation details."""

    async def test_auto_create_table(self) -> None:
        """auto_create=True creates the required tables."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_auto_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="auto_create_test",
            rate_per_second=10.0,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Verify table exists
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                result = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = $1
                    )
                    """,
                    table_name,
                )
                assert result is True, "Table should be created"
            finally:
                await conn.close()

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_custom_schema(self) -> None:
        """Custom schema_name is respected."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_schema_{uuid.uuid4().hex[:8]}"

        # Create test schema
        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS test_ratesync")
        finally:
            await conn.close()

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="schema_test",
            rate_per_second=10.0,
            table_name=table_name,
            schema_name="test_ratesync",
            auto_create=True,
        )

        try:
            await limiter.initialize()
            await limiter.acquire()

            # Verify table exists in custom schema
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                result = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'test_ratesync'
                        AND table_name = $1
                    )
                    """,
                    table_name,
                )
                assert result is True, "Table should be in custom schema"
            finally:
                await conn.close()

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(
                    f'DROP TABLE IF EXISTS "test_ratesync"."{table_name}"'
                )
                await conn.execute(
                    f'DROP TABLE IF EXISTS "test_ratesync"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_distributed_group_coordination(self) -> None:
        """Multiple limiters with same group_id share state."""
        from ratesync.engines.postgres import PostgresRateLimiter

        table_name = f"test_dist_{uuid.uuid4().hex[:8]}"
        group_id = f"distributed_{uuid.uuid4().hex[:8]}"

        limiter1 = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id=group_id,
            rate_per_second=1.0,
            table_name=table_name,
            auto_create=True,
        )
        limiter2 = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id=group_id,
            rate_per_second=1.0,
            table_name=table_name,
            auto_create=False,
        )

        try:
            await limiter1.initialize()
            await limiter2.initialize()

            # Acquire on limiter1
            await limiter1.acquire()

            # limiter2 should see the rate limit
            result = await limiter2.try_acquire(timeout=0)
            assert result is False, "Distributed state should be shared"

        finally:
            await limiter1.disconnect()
            await limiter2.disconnect()

            # Cleanup
            import asyncpg

            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_sliding_window_creates_window_table(self) -> None:
        """Sliding window algorithm creates a separate window table."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_sw_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="sw_table_test",
            limit=10,
            window_seconds=60,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Verify window table exists
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                result = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = $1
                    )
                    """,
                    f"{table_name}_window",
                )
                assert result is True, "Window table should be created"
            finally:
                await conn.close()

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_connection_pool_configuration(self) -> None:
        """Connection pool respects min/max size configuration."""
        from ratesync.engines.postgres import PostgresRateLimiter

        table_name = f"test_pool_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="pool_test",
            rate_per_second=100.0,
            table_name=table_name,
            auto_create=True,
            pool_min_size=1,
            pool_max_size=5,
        )

        try:
            await limiter.initialize()

            # Make many requests to exercise the pool
            for _ in range(20):
                await limiter.try_acquire(timeout=0.1)

            metrics = limiter.get_metrics()
            assert metrics.total_acquisitions > 0

        finally:
            await limiter.disconnect()

            # Cleanup
            import asyncpg

            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
            finally:
                await conn.close()

    async def test_algorithm_property(self) -> None:
        """Algorithm property reflects configuration."""
        from ratesync.engines.postgres import PostgresRateLimiter

        # Token bucket
        limiter1 = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="alg_tb",
            rate_per_second=10.0,
            table_name="test_alg_tb",
        )
        assert limiter1.algorithm == "token_bucket"

        # Sliding window
        limiter2 = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="alg_sw",
            limit=10,
            window_seconds=60,
            table_name="test_alg_sw",
        )
        assert limiter2.algorithm == "sliding_window"

    async def test_get_state_sliding_window(self) -> None:
        """PostgreSQL engine get_state() works with sliding window algorithm."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_state_sw_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="state_sw_test",
            limit=5,
            window_seconds=60,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Initial state
            state = await limiter.get_state()
            assert state.allowed is True
            assert state.remaining == 5

            # Use some quota
            await limiter.acquire()
            await limiter.acquire()

            state = await limiter.get_state()
            assert state.remaining == 3
            assert state.current_usage >= 2

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_get_state_token_bucket(self) -> None:
        """PostgreSQL engine get_state() works with token bucket algorithm."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_state_tb_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="state_tb_test",
            rate_per_second=10.0,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Initial state
            state = await limiter.get_state()
            assert state.allowed is True

            # After acquire
            await limiter.acquire()

            state = await limiter.get_state()
            assert isinstance(state.reset_at, int)

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_get_state_with_concurrency(self) -> None:
        """PostgreSQL engine get_state() reflects concurrency state."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_state_conc_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="state_conc_test",
            limit=10,
            window_seconds=60,
            max_concurrent=2,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Fill concurrency slots
            await limiter.acquire()
            await limiter.acquire()

            state = await limiter.get_state()
            # Should show limited due to concurrency
            assert state.current_usage >= 2

            # Release slots
            await limiter.release()
            await limiter.release()

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_from_config_creates_limiter(self) -> None:
        """PostgreSQL engine from_config() creates properly configured limiter."""
        from ratesync.engines.postgres import PostgresRateLimiter
        from ratesync.schemas import PostgresEngineConfig

        config = PostgresEngineConfig(
            url=POSTGRES_URL,
            table_name="from_config_test",
            auto_create=False,
        )

        limiter = PostgresRateLimiter.from_config(
            config,
            group_id="from_config_test",
            rate_per_second=50.0,
            max_concurrent=10,
        )

        assert limiter.group_id == "from_config_test"
        assert limiter.rate_per_second == 50.0
        assert limiter.max_concurrent == 10

    async def test_get_config_returns_all_fields(self) -> None:
        """PostgreSQL engine get_config() returns all configuration fields."""
        from ratesync.engines.postgres import PostgresRateLimiter

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="config_test",
            limit=100,
            window_seconds=300,
            max_concurrent=50,
            default_timeout=10.0,
            fail_closed=True,
            table_name="config_test",
        )

        config = limiter.get_config()

        assert config.id == "config_test"
        assert config.algorithm == "sliding_window"
        assert config.store_id == "postgres"
        assert config.limit == 100
        assert config.window_seconds == 300
        assert config.max_concurrent == 50
        assert config.timeout == 10.0
        assert config.fail_closed is True

    async def test_reset_clears_all_data(self) -> None:
        """PostgreSQL engine reset() clears all data for the limiter."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_reset_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="reset_test",
            limit=5,
            window_seconds=60,
            max_concurrent=2,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Use the limiter
            await limiter.acquire()

            # Reset
            await limiter.reset()

            # Should be back to initial state
            state = await limiter.get_state()
            assert state.remaining == 5
            assert state.current_usage == 0

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_concurrent_with_rate_limiting(self) -> None:
        """PostgreSQL engine handles combined rate + concurrency limiting."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_combined_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="combined_test",
            rate_per_second=100.0,  # High rate to not interfere
            max_concurrent=2,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Fill concurrency
            await limiter.acquire()
            await limiter.acquire()

            # Third should fail due to concurrency
            result = await limiter.try_acquire(timeout=0)
            assert result is False

            # Release one
            await limiter.release()

            # Now should work
            result = await limiter.try_acquire(timeout=0.1)
            assert result is True

            # Cleanup
            await limiter.release()
            await limiter.release()

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
            finally:
                await conn.close()

    async def test_disconnect_is_safe_to_call_multiple_times(self) -> None:
        """PostgreSQL engine disconnect() can be called multiple times safely."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_disconnect_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="disconnect_test",
            rate_per_second=10.0,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()
            await limiter.disconnect()
            # Should not raise
            await limiter.disconnect()

        finally:
            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()

    async def test_fail_closed_property(self) -> None:
        """PostgreSQL engine respects fail_closed setting."""
        from ratesync.engines.postgres import PostgresRateLimiter

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="fail_closed_test",
            rate_per_second=10.0,
            table_name="fail_closed_test",
            fail_closed=True,
        )

        assert limiter.fail_closed is True

        limiter_open = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="fail_open_test",
            rate_per_second=10.0,
            table_name="fail_open_test",
            fail_closed=False,
        )

        assert limiter_open.fail_closed is False

    async def test_sliding_window_full_workflow(self) -> None:
        """PostgreSQL sliding window works correctly end-to-end."""
        from ratesync.engines.postgres import PostgresRateLimiter
        import asyncpg

        table_name = f"test_sw_full_{uuid.uuid4().hex[:8]}"

        limiter = PostgresRateLimiter(
            connection_url=POSTGRES_URL,
            group_id="sw_full_test",
            limit=3,
            window_seconds=60,
            table_name=table_name,
            auto_create=True,
        )

        try:
            await limiter.initialize()

            # Should allow 3 requests
            for i in range(3):
                result = await limiter.try_acquire(timeout=0)
                assert result is True, f"Request {i+1} should succeed"

            # 4th should fail
            result = await limiter.try_acquire(timeout=0)
            assert result is False, "Should be rate limited"

            # Check state
            state = await limiter.get_state()
            assert state.allowed is False
            assert state.remaining == 0
            assert state.current_usage == 3

        finally:
            await limiter.disconnect()

            # Cleanup
            conn = await asyncpg.connect(POSTGRES_URL)
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "public"."{table_name}"')
                await conn.execute(
                    f'DROP TABLE IF EXISTS "public"."{table_name}_window"'
                )
            finally:
                await conn.close()
