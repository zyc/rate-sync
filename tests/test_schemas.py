"""Tests for rate limiter configuration schemas.

These tests verify the schema validation logic, particularly:
- LimiterConfig requires at least one limiting strategy
- Validation of positive values for rate_per_second and max_concurrent
- Engine config schemas
"""

import pytest

from ratesync.schemas import (
    ENGINE_SCHEMAS,
    LimiterConfig,
    MemoryEngineConfig,
    NatsEngineConfig,
    PostgresEngineConfig,
    RedisEngineConfig,
)


class TestLimiterConfig:
    """Test LimiterConfig validation and behavior."""

    def test_rate_per_second_only(self):
        """Test creating limiter with only rate_per_second."""
        config = LimiterConfig(store="test", rate_per_second=10.0)

        assert config.store == "test"
        assert config.rate_per_second == 10.0
        assert config.max_concurrent is None
        assert config.timeout is None

    def test_max_concurrent_only(self):
        """Test creating limiter with only max_concurrent."""
        config = LimiterConfig(store="test", max_concurrent=5)

        assert config.store == "test"
        assert config.rate_per_second is None
        assert config.max_concurrent == 5
        assert config.timeout is None

    def test_both_strategies(self):
        """Test creating limiter with both rate_per_second and max_concurrent."""
        config = LimiterConfig(
            store="test",
            rate_per_second=100.0,
            max_concurrent=50,
            timeout=30.0,
        )

        assert config.store == "test"
        assert config.rate_per_second == 100.0
        assert config.max_concurrent == 50
        assert config.timeout == 30.0

    def test_neither_strategy_raises_error(self):
        """Test that creating limiter without any strategy raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(store="test")

        assert "rate_per_second" in str(exc_info.value)
        assert "max_concurrent" in str(exc_info.value)

    def test_explicit_none_values_raises_error(self):
        """Test that explicitly passing None for both raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(store="test", rate_per_second=None, max_concurrent=None)

        assert "rate_per_second" in str(exc_info.value)
        assert "max_concurrent" in str(exc_info.value)

    def test_negative_rate_per_second_raises_error(self):
        """Test that negative rate_per_second raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(store="test", rate_per_second=-1.0)

        assert "rate_per_second must be > 0" in str(exc_info.value)

    def test_zero_rate_per_second_raises_error(self):
        """Test that zero rate_per_second raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(store="test", rate_per_second=0.0)

        assert "rate_per_second must be > 0" in str(exc_info.value)

    def test_negative_max_concurrent_raises_error(self):
        """Test that negative max_concurrent raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(store="test", max_concurrent=-1)

        assert "max_concurrent must be > 0" in str(exc_info.value)

    def test_zero_max_concurrent_raises_error(self):
        """Test that zero max_concurrent raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(store="test", max_concurrent=0)

        assert "max_concurrent must be > 0" in str(exc_info.value)

    def test_small_rate_per_second(self):
        """Test creating limiter with very small rate_per_second (valid)."""
        config = LimiterConfig(store="test", rate_per_second=0.001)

        assert config.rate_per_second == 0.001

    def test_large_max_concurrent(self):
        """Test creating limiter with large max_concurrent (valid)."""
        config = LimiterConfig(store="test", max_concurrent=10000)

        assert config.max_concurrent == 10000


class TestSlidingWindowLimiterConfig:
    """Test sliding window algorithm configuration."""

    def test_sliding_window_valid_config(self):
        """Test creating a valid sliding window limiter."""
        config = LimiterConfig(
            store="test",
            algorithm="sliding_window",
            limit=5,
            window_seconds=300,
        )

        assert config.algorithm == "sliding_window"
        assert config.limit == 5
        assert config.window_seconds == 300
        assert config.rate_per_second is None
        assert config.max_concurrent is None

    def test_sliding_window_with_timeout(self):
        """Test sliding window limiter with timeout."""
        config = LimiterConfig(
            store="test",
            algorithm="sliding_window",
            limit=10,
            window_seconds=60,
            timeout=0.0,
        )

        assert config.timeout == 0.0

    def test_sliding_window_missing_limit_raises_error(self):
        """Test that sliding window without limit raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(
                store="test",
                algorithm="sliding_window",
                window_seconds=300,
            )

        assert "limit" in str(exc_info.value)
        assert "window_seconds" in str(exc_info.value)

    def test_sliding_window_missing_window_raises_error(self):
        """Test that sliding window without window_seconds raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(
                store="test",
                algorithm="sliding_window",
                limit=5,
            )

        assert "limit" in str(exc_info.value)
        assert "window_seconds" in str(exc_info.value)

    def test_sliding_window_zero_limit_raises_error(self):
        """Test that zero limit raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(
                store="test",
                algorithm="sliding_window",
                limit=0,
                window_seconds=300,
            )

        assert "limit must be > 0" in str(exc_info.value)

    def test_sliding_window_negative_limit_raises_error(self):
        """Test that negative limit raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(
                store="test",
                algorithm="sliding_window",
                limit=-1,
                window_seconds=300,
            )

        assert "limit must be > 0" in str(exc_info.value)

    def test_sliding_window_zero_window_raises_error(self):
        """Test that zero window_seconds raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(
                store="test",
                algorithm="sliding_window",
                limit=5,
                window_seconds=0,
            )

        assert "window_seconds must be > 0" in str(exc_info.value)

    def test_sliding_window_negative_window_raises_error(self):
        """Test that negative window_seconds raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            LimiterConfig(
                store="test",
                algorithm="sliding_window",
                limit=5,
                window_seconds=-60,
            )

        assert "window_seconds must be > 0" in str(exc_info.value)


class TestEngineSchemas:
    """Test engine configuration schemas."""

    def test_engine_schemas_registry(self):
        """Test that ENGINE_SCHEMAS contains all expected engines."""
        assert "memory" in ENGINE_SCHEMAS
        assert "nats" in ENGINE_SCHEMAS
        assert "postgres" in ENGINE_SCHEMAS
        assert "redis" in ENGINE_SCHEMAS

    def test_memory_config_defaults(self):
        """Test MemoryEngineConfig default values."""
        config = MemoryEngineConfig()

        assert config.engine == "memory"

    def test_nats_config_defaults(self):
        """Test NatsEngineConfig with required url."""
        config = NatsEngineConfig(url="nats://localhost:4222")

        assert config.url == "nats://localhost:4222"
        assert config.engine == "nats"
        assert config.token is None
        assert config.bucket_name == "rate_limits"
        assert config.auto_create is False
        assert config.retry_interval == 0.05
        assert config.max_retries == 100

    def test_postgres_config_defaults(self):
        """Test PostgresEngineConfig with required url."""
        config = PostgresEngineConfig(url="postgresql://localhost:5432/test")

        assert config.url == "postgresql://localhost:5432/test"
        assert config.engine == "postgres"
        assert config.table_name == "rate_limiter_state"
        assert config.schema_name == "public"
        assert config.auto_create is False
        assert config.pool_min_size == 2
        assert config.pool_max_size == 10

    def test_redis_config_defaults(self):
        """Test RedisEngineConfig with required url."""
        config = RedisEngineConfig(url="redis://localhost:6379/0")

        assert config.url == "redis://localhost:6379/0"
        assert config.engine == "redis"
        assert config.db == 0
        assert config.password is None
        assert config.pool_min_size == 2
        assert config.pool_max_size == 10
        assert config.key_prefix == "rate_limit"

    def test_redis_config_custom_values(self):
        """Test RedisEngineConfig with custom values."""
        config = RedisEngineConfig(
            url="redis://prod:6379/1",
            password="secret",
            db=2,
            pool_max_size=50,
            key_prefix="myapp_rate",
        )

        assert config.url == "redis://prod:6379/1"
        assert config.password == "secret"
        assert config.db == 2
        assert config.pool_max_size == 50
        assert config.key_prefix == "myapp_rate"
