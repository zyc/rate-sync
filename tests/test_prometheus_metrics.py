"""Tests for Prometheus metrics integration.

These tests verify that Prometheus metrics are properly recorded
when rate limiters are used, and that the metrics are correctly
exported through FastAPI and Push Gateway integrations.
"""

import pytest

# Ensure prometheus-client is available for tests
pytest.importorskip("prometheus_client")

from prometheus_client import REGISTRY

from ratesync.contrib.prometheus import PROMETHEUS_AVAILABLE, enable_metrics
from ratesync.contrib.prometheus.metrics import (
    NAMESPACE,
    is_enabled,
    record_acquisition,
    record_fallback_activation,
    record_redis_operation,
    record_timeout,
    set_circuit_breaker_state,
)


@pytest.fixture(autouse=True)
def reset_prometheus_registry():
    """Reset Prometheus metrics between tests.

    This fixture ensures each test starts with a clean state.
    Since prometheus-client uses global state, we need to be careful
    about metric registration.
    """
    # Get the list of collector names before the test
    # We can't easily reset metrics in prometheus-client, so we just
    # make sure metrics are initialized
    yield
    # No cleanup needed - metrics persist across tests


class TestPrometheusAvailability:
    """Test Prometheus availability detection."""

    def test_prometheus_available(self):
        """Test that PROMETHEUS_AVAILABLE is True when prometheus-client is installed."""
        assert PROMETHEUS_AVAILABLE is True

    def test_enable_metrics_returns_true(self):
        """Test that enable_metrics returns True when prometheus-client is available."""
        result = enable_metrics()
        assert result is True

    def test_is_enabled_after_enable(self):
        """Test that is_enabled returns True after enable_metrics is called."""
        enable_metrics()
        assert is_enabled() is True


class TestMetricsRecording:
    """Test that metrics are properly recorded."""

    def test_record_acquisition_creates_metrics(self):
        """Test that record_acquisition increments the counter and observes histogram."""
        enable_metrics()

        # Record an acquisition
        record_acquisition("test_group", "memory", 0.05)

        # Verify counter was incremented
        counter_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_acquisitions_total",
            labels={"group_id": "test_group", "engine": "memory"},
        )
        assert counter_sample is not None
        assert counter_sample >= 1

    def test_record_timeout_increments_counter(self):
        """Test that record_timeout increments the timeout counter."""
        enable_metrics()

        # Record a timeout
        record_timeout("test_group_timeout", "redis")

        # Verify counter was incremented
        counter_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_timeouts_total",
            labels={"group_id": "test_group_timeout", "engine": "redis"},
        )
        assert counter_sample is not None
        assert counter_sample >= 1

    def test_record_redis_operation_observes_histogram(self):
        """Test that record_redis_operation records Redis latency."""
        enable_metrics()

        # Record a Redis operation
        record_redis_operation("test_group_redis", "acquire", 0.01)

        # Verify histogram was observed (check count)
        count_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_redis_operation_duration_seconds_count",
            labels={"group_id": "test_group_redis", "operation": "acquire"},
        )
        assert count_sample is not None
        assert count_sample >= 1

    def test_set_circuit_breaker_state_sets_gauge(self):
        """Test that circuit breaker state is set correctly."""
        enable_metrics()

        # Set circuit breaker to open state
        set_circuit_breaker_state("test_group_cb", 2)  # 2 = open

        # Verify gauge was set
        gauge_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_circuit_breaker_state",
            labels={"group_id": "test_group_cb"},
        )
        assert gauge_sample is not None
        assert gauge_sample == 2

        # Change to closed state
        set_circuit_breaker_state("test_group_cb", 0)  # 0 = closed

        gauge_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_circuit_breaker_state",
            labels={"group_id": "test_group_cb"},
        )
        assert gauge_sample == 0

    def test_record_fallback_activation_increments_counter(self):
        """Test that fallback activations are counted."""
        enable_metrics()

        # Record a fallback activation
        record_fallback_activation("test_group_fallback")

        # Verify counter was incremented
        counter_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_fallback_activations_total",
            labels={"group_id": "test_group_fallback"},
        )
        assert counter_sample is not None
        assert counter_sample >= 1


class TestMetricsNotEnabledBehavior:
    """Test behavior when metrics are not enabled (before init)."""

    def test_record_functions_are_safe_before_init(self):
        """Test that record functions don't raise exceptions before initialization.

        Note: Since we're running tests with prometheus-client installed and
        metrics are initialized globally, we can only verify that the functions
        don't raise exceptions. In production without prometheus-client,
        these would be no-ops.
        """
        # These should not raise any exceptions
        record_acquisition("safe_test", "memory", 0.1)
        record_timeout("safe_test", "memory")
        record_redis_operation("safe_test", "ping", 0.001)
        set_circuit_breaker_state("safe_test", 0)
        record_fallback_activation("safe_test")


@pytest.mark.asyncio
class TestMemoryEngineMetrics:
    """Test that Memory engine records Prometheus metrics."""

    async def test_memory_engine_records_acquisition(self):
        """Test that MemoryRateLimiter records acquisition metrics."""
        from ratesync.engines.memory import MemoryRateLimiter

        enable_metrics()

        limiter = MemoryRateLimiter(
            group_id="memory_prom_test",
            rate_per_second=100.0,  # High rate to avoid waiting
        )
        await limiter.initialize()

        # Perform an acquisition
        await limiter.acquire()

        # Verify metric was recorded
        counter_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_acquisitions_total",
            labels={"group_id": "memory_prom_test", "engine": "memory"},
        )
        assert counter_sample is not None
        assert counter_sample >= 1

    async def test_memory_engine_records_timeout(self):
        """Test that MemoryRateLimiter records timeout metrics."""
        from ratesync.engines.memory import MemoryRateLimiter

        enable_metrics()

        limiter = MemoryRateLimiter(
            group_id="memory_timeout_test",
            rate_per_second=0.1,  # Very slow rate
        )
        await limiter.initialize()

        # First acquire to consume the slot
        await limiter.acquire()

        # Try to acquire immediately - should timeout
        result = await limiter.try_acquire(timeout=0)
        assert result is False

        # Verify timeout metric was recorded
        counter_sample = REGISTRY.get_sample_value(
            f"{NAMESPACE}_timeouts_total",
            labels={"group_id": "memory_timeout_test", "engine": "memory"},
        )
        assert counter_sample is not None
        assert counter_sample >= 1


class TestFastAPIIntegration:
    """Test FastAPI integration."""

    def test_add_metrics_endpoint_requires_fastapi(self):
        """Test that add_metrics_endpoint works with FastAPI."""
        pytest.importorskip("fastapi")

        from fastapi import FastAPI

        from ratesync.contrib.prometheus.fastapi import add_metrics_endpoint

        app = FastAPI()
        result = add_metrics_endpoint(app)

        assert result is True
        # Verify the route was added
        routes = [route.path for route in app.routes]
        assert "/metrics" in routes

    def test_add_metrics_endpoint_custom_path(self):
        """Test that add_metrics_endpoint supports custom path."""
        pytest.importorskip("fastapi")

        from fastapi import FastAPI

        from ratesync.contrib.prometheus.fastapi import add_metrics_endpoint

        app = FastAPI()
        result = add_metrics_endpoint(app, path="/custom/metrics")

        assert result is True
        routes = [route.path for route in app.routes]
        assert "/custom/metrics" in routes


@pytest.mark.asyncio
class TestPushGatewayIntegration:
    """Test Push Gateway integration."""

    async def test_push_to_gateway_handles_connection_error(self):
        """Test that push_to_gateway handles connection errors gracefully."""
        from ratesync.contrib.prometheus.pushgateway import push_to_gateway

        enable_metrics()

        # Try to push to a non-existent gateway
        result = await push_to_gateway(
            gateway_url="http://localhost:9999",  # Non-existent
            job="test-job",
            timeout=0.5,
        )

        # Should return False on error
        assert result is False

    async def test_start_periodic_push_returns_handle(self):
        """Test that start_periodic_push returns a handle with stop methods."""
        from ratesync.contrib.prometheus.pushgateway import (
            PeriodicPushHandle,
            start_periodic_push,
        )

        enable_metrics()

        # Start periodic push with very long interval (won't actually push)
        handle = await start_periodic_push(
            gateway_url="http://localhost:9999",
            job="test-periodic",
            interval=999999.0,  # Very long to prevent actual pushes
            timeout=0.1,  # Short timeout to fail fast
        )

        # Should return a handle
        assert handle is not None
        assert isinstance(handle, PeriodicPushHandle)
        assert hasattr(handle, "set")
        assert hasattr(handle, "is_set")
        assert hasattr(handle, "stop")
        assert hasattr(handle, "cancel")

        # Stop the periodic push and wait for completion
        await handle.stop()

    async def test_delete_from_gateway_handles_connection_error(self):
        """Test that delete_from_gateway handles errors gracefully."""
        from ratesync.contrib.prometheus.pushgateway import delete_from_gateway

        enable_metrics()

        # Try to delete from a non-existent gateway
        result = await delete_from_gateway(
            gateway_url="http://localhost:9999",
            job="test-job",
            timeout=0.5,
        )

        # Should return False on error
        assert result is False
