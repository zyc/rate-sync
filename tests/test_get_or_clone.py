"""Tests for get_or_clone_limiter functionality.

Tests the canonical pattern for per-identifier rate limiting:
cloning a base limiter configuration for dynamic identifiers.
"""

import pytest

from ratesync import (
    configure_limiter,
    configure_store,
    get_or_clone_limiter,
    get_limiter,
    clone_limiter,
)
from ratesync.exceptions import LimiterNotFoundError


@pytest.fixture(autouse=True)
async def setup_memory_store():
    """Setup memory store and base limiter for all tests."""
    configure_store("memory", strategy="memory")

    # Configure base limiter for token bucket
    configure_limiter(
        "base_api",
        store_id="memory",
        rate_per_second=10.0,
        max_concurrent=5,
        timeout=1.0,
    )

    # Note: Sliding window requires Redis backend, so we don't configure it here
    # Tests for sliding window will be skipped or use a different approach

    yield

    # Cleanup - reset registry
    from ratesync.registry import _registry

    _registry._limiters.clear()
    _registry._backends.clear()


@pytest.mark.asyncio
async def test_get_or_clone_creates_new_limiter():
    """Test that get_or_clone creates a new limiter on first call."""
    # Act
    limiter = await get_or_clone_limiter("base_api", "192.168.1.1")

    # Assert
    assert limiter is not None
    assert limiter.group_id == "base_api:192.168.1.1"
    assert limiter.is_initialized is True
    assert limiter.rate_per_second == 10.0
    assert limiter.max_concurrent == 5


@pytest.mark.asyncio
async def test_get_or_clone_returns_existing_limiter():
    """Test that get_or_clone returns existing limiter on second call."""
    # Arrange - create limiter first
    limiter1 = await get_or_clone_limiter("base_api", "192.168.1.1")

    # Act - call again with same identifier
    limiter2 = await get_or_clone_limiter("base_api", "192.168.1.1")

    # Assert - should be the same instance
    assert limiter1 is limiter2
    assert limiter1.group_id == limiter2.group_id


@pytest.mark.asyncio
async def test_get_or_clone_different_identifiers():
    """Test that different identifiers create separate limiters."""
    # Act
    limiter1 = await get_or_clone_limiter("base_api", "192.168.1.1")
    limiter2 = await get_or_clone_limiter("base_api", "192.168.1.2")
    limiter3 = await get_or_clone_limiter("base_api", "user:123")

    # Assert - all are different instances
    assert limiter1 is not limiter2
    assert limiter1 is not limiter3
    assert limiter2 is not limiter3

    # Assert - all have correct IDs
    assert limiter1.group_id == "base_api:192.168.1.1"
    assert limiter2.group_id == "base_api:192.168.1.2"
    assert limiter3.group_id == "base_api:user:123"


@pytest.mark.asyncio
async def test_get_or_clone_inherits_configuration():
    """Test that cloned limiters inherit configuration from base."""
    # Act
    limiter = await get_or_clone_limiter("base_api", "test-id")

    # Assert - inherited config from base_api
    assert limiter.rate_per_second == 10.0
    assert limiter.max_concurrent == 5
    assert limiter.default_timeout == 1.0


@pytest.mark.asyncio
async def test_get_or_clone_independent_state():
    """Test that cloned limiters have independent state."""
    # Arrange
    limiter1 = await get_or_clone_limiter("base_api", "id1")
    limiter2 = await get_or_clone_limiter("base_api", "id2")

    # Act - acquire from limiter1
    allowed1_before = await limiter1.try_acquire(timeout=0)
    allowed2_before = await limiter2.try_acquire(timeout=0)

    # Assert - both allowed initially
    assert allowed1_before is True
    assert allowed2_before is True

    # Metrics should be independent
    metrics1 = limiter1.get_metrics()
    metrics2 = limiter2.get_metrics()

    assert metrics1.total_acquisitions >= 1
    assert metrics2.total_acquisitions >= 1


@pytest.mark.asyncio
async def test_get_or_clone_with_nonexistent_base():
    """Test that get_or_clone raises error for nonexistent base limiter."""
    # Act & Assert
    with pytest.raises(LimiterNotFoundError) as exc_info:
        await get_or_clone_limiter("nonexistent", "test-id")

    assert "nonexistent" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_or_clone_auto_initialize():
    """Test that get_or_clone auto-initializes the limiter."""
    # Act
    limiter = await get_or_clone_limiter("base_api", "test-id")

    # Assert
    assert limiter.is_initialized is True

    # Should be able to use immediately
    allowed = await limiter.try_acquire(timeout=0)
    assert allowed is True


@pytest.mark.asyncio
@pytest.mark.skip(reason="Sliding window requires Redis backend, memory backend doesn't support it")
async def test_get_or_clone_with_sliding_window():
    """Test get_or_clone works with sliding window limiters.

    This test requires Redis and is skipped by default.
    Sliding window algorithm is not supported by memory backend.
    """
    # This test would need Redis setup:
    # configure_store("redis", strategy="redis", url="redis://localhost:6379/0")
    # configure_limiter(
    #     "base_login", store_id="redis", algorithm="sliding_window",
    #     limit=5, window_seconds=60
    # )
    # limiter = await get_or_clone_limiter("base_login", "user@example.com")
    # ...
    pass


@pytest.mark.asyncio
async def test_manual_clone_vs_get_or_clone():
    """Test that manual clone_limiter + get_limiter is equivalent to get_or_clone_limiter."""
    # Arrange - manual clone
    clone_limiter("base_api", "base_api:manual")
    manual_limiter = get_limiter("base_api:manual")
    await manual_limiter.initialize()

    # Act - get_or_clone
    auto_limiter = await get_or_clone_limiter("base_api", "auto")

    # Assert - same configuration
    assert manual_limiter.rate_per_second == auto_limiter.rate_per_second
    assert manual_limiter.max_concurrent == auto_limiter.max_concurrent
    assert manual_limiter.default_timeout == auto_limiter.default_timeout
    assert manual_limiter.is_initialized == auto_limiter.is_initialized


@pytest.mark.asyncio
async def test_get_or_clone_with_special_characters():
    """Test that get_or_clone handles special characters in identifiers."""
    # Act
    limiter1 = await get_or_clone_limiter("base_api", "user@example.com")
    limiter2 = await get_or_clone_limiter("base_api", "192.168.1.1:8080")
    limiter3 = await get_or_clone_limiter("base_api", "hash:abc123")

    # Assert
    assert limiter1.group_id == "base_api:user@example.com"
    assert limiter2.group_id == "base_api:192.168.1.1:8080"
    assert limiter3.group_id == "base_api:hash:abc123"

    # All should be different
    assert limiter1 is not limiter2
    assert limiter1 is not limiter3
    assert limiter2 is not limiter3


@pytest.mark.asyncio
async def test_get_or_clone_concurrent_access():
    """Test that get_or_clone is safe for concurrent access."""
    import asyncio

    # Act - call get_or_clone concurrently for same identifier
    limiters = await asyncio.gather(
        get_or_clone_limiter("base_api", "concurrent-test"),
        get_or_clone_limiter("base_api", "concurrent-test"),
        get_or_clone_limiter("base_api", "concurrent-test"),
    )

    # Assert - all should be the same instance (or at least same ID)
    assert limiters[0].group_id == limiters[1].group_id == limiters[2].group_id
    assert limiters[0].group_id == "base_api:concurrent-test"
