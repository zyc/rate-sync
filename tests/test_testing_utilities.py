"""Tests for testing utilities.

Tests the reset_limiter, reset_all_limiters, and reset_backend_store functions.
"""

import pytest

from ratesync import (
    configure_limiter,
    configure_store,
    get_limiter,
    get_or_clone_limiter,
)
from ratesync.testing import (
    reset_limiter,
    reset_all_limiters,
    reset_backend_store,
)


@pytest.fixture(autouse=True)
async def cleanup():
    """Cleanup registry before each test."""
    from ratesync.registry import _registry

    _registry._limiters.clear()
    _registry._backends.clear()

    yield

    _registry._limiters.clear()
    _registry._backends.clear()


@pytest.fixture
async def setup_memory_limiters():
    """Setup memory store with multiple limiters."""
    configure_store("memory", strategy="memory")

    configure_limiter(
        "api",
        store_id="memory",
        rate_per_second=10.0,
        max_concurrent=5,
    )

    configure_limiter(
        "db",
        store_id="memory",
        rate_per_second=5.0,
        max_concurrent=3,
    )

    yield


@pytest.mark.asyncio
async def test_reset_limiter_clears_state(setup_memory_limiters):
    """Test that reset_limiter clears the limiter's state."""
    # Arrange
    limiter = get_limiter("api")
    await limiter.initialize()

    # Acquire some slots
    await limiter.acquire()
    metrics_before = limiter.get_metrics()
    assert metrics_before.total_acquisitions >= 1

    # Act
    await reset_limiter(limiter)

    # Assert - metrics should be reset
    metrics_after = limiter.get_metrics()
    assert metrics_after.total_acquisitions == 0
    assert metrics_after.timeouts == 0


@pytest.mark.asyncio
async def test_reset_limiter_allows_fresh_acquisitions(setup_memory_limiters):
    """Test that reset_limiter allows starting fresh with rate limiting."""
    # Arrange - configure very restrictive limiter
    configure_limiter(
        "strict",
        store_id="memory",
        rate_per_second=1.0,  # Only 1 req/s
    )

    limiter = get_limiter("strict")
    await limiter.initialize()

    # Acquire first slot
    allowed1 = await limiter.try_acquire(timeout=0)
    assert allowed1 is True

    # Should be rate limited immediately
    allowed2 = await limiter.try_acquire(timeout=0)
    assert allowed2 is False

    # Act - reset
    await reset_limiter(limiter)

    # Assert - should be able to acquire again immediately
    allowed3 = await limiter.try_acquire(timeout=0)
    assert allowed3 is True


@pytest.mark.asyncio
async def test_reset_all_limiters_resets_multiple(setup_memory_limiters):
    """Test that reset_all_limiters resets all configured limiters."""
    # Arrange
    api_limiter = get_limiter("api")
    db_limiter = get_limiter("db")

    await api_limiter.initialize()
    await db_limiter.initialize()

    # Acquire from both
    await api_limiter.acquire()
    await db_limiter.acquire()

    assert api_limiter.get_metrics().total_acquisitions >= 1
    assert db_limiter.get_metrics().total_acquisitions >= 1

    # Act
    await reset_all_limiters()

    # Assert - both should be reset
    assert api_limiter.get_metrics().total_acquisitions == 0
    assert db_limiter.get_metrics().total_acquisitions == 0


@pytest.mark.asyncio
async def test_reset_all_limiters_with_no_limiters():
    """Test that reset_all_limiters handles empty registry gracefully."""
    # Act & Assert - should not raise
    await reset_all_limiters()


@pytest.mark.asyncio
async def test_reset_backend_store_with_memory(setup_memory_limiters):
    """Test that reset_backend_store works with memory backend."""
    # Arrange
    limiter = get_limiter("api")
    await limiter.initialize()
    await limiter.acquire()

    # Act
    await reset_backend_store("memory")

    # Assert - limiter should be reset (memory backend calls reset())
    # Note: For memory backend, reset_all() is the same as reset()
    metrics = limiter.get_metrics()
    assert metrics.total_acquisitions == 0


@pytest.mark.asyncio
async def test_reset_backend_store_invalid_store(setup_memory_limiters):
    """Test that reset_backend_store raises error for invalid store."""
    # Act & Assert
    with pytest.raises(ValueError) as exc_info:
        await reset_backend_store("nonexistent")

    assert "nonexistent" in str(exc_info.value)
    assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_reset_backend_store_no_initialized_limiter():
    """Test reset_backend_store handles store with no initialized limiters."""
    # Arrange - configure store but don't initialize any limiters
    configure_store("memory", strategy="memory")
    configure_limiter("api", store_id="memory", rate_per_second=10.0)

    # Act & Assert - should log warning but not raise
    await reset_backend_store("memory")


@pytest.mark.asyncio
async def test_reset_limiter_with_concurrency(setup_memory_limiters):
    """Test that reset_limiter resets concurrency tracking."""
    # Arrange - configure limiter with concurrency limit
    configure_limiter(
        "concurrent",
        store_id="memory",
        max_concurrent=2,
    )

    limiter = get_limiter("concurrent")
    await limiter.initialize()

    # Acquire both slots (use context manager to release later)
    async with limiter.acquire_context():
        # One slot held
        metrics = limiter.get_metrics()
        assert metrics.current_concurrent == 1

        # Act - reset while slot is held
        await reset_limiter(limiter)

    # Assert - concurrency should be reset
    metrics_after = limiter.get_metrics()
    assert metrics_after.current_concurrent == 0


@pytest.mark.asyncio
async def test_reset_works_with_cloned_limiters(setup_memory_limiters):
    """Test that reset utilities work with dynamically cloned limiters."""
    # Arrange - create cloned limiters
    limiter1 = await get_or_clone_limiter("api", "user1")
    limiter2 = await get_or_clone_limiter("api", "user2")

    await limiter1.acquire()
    await limiter2.acquire()

    assert limiter1.get_metrics().total_acquisitions >= 1
    assert limiter2.get_metrics().total_acquisitions >= 1

    # Act - reset specific limiter
    await reset_limiter(limiter1)

    # Assert - only limiter1 should be reset
    assert limiter1.get_metrics().total_acquisitions == 0
    assert limiter2.get_metrics().total_acquisitions >= 1


@pytest.mark.asyncio
async def test_reset_all_includes_cloned_limiters(setup_memory_limiters):
    """Test that reset_all_limiters includes dynamically cloned limiters."""
    # Arrange - create cloned limiters
    limiter1 = await get_or_clone_limiter("api", "user1")
    limiter2 = await get_or_clone_limiter("api", "user2")
    base_limiter = get_limiter("api")
    await base_limiter.initialize()

    await limiter1.acquire()
    await limiter2.acquire()
    await base_limiter.acquire()

    # Act
    await reset_all_limiters()

    # Assert - all should be reset (base + clones)
    assert limiter1.get_metrics().total_acquisitions == 0
    assert limiter2.get_metrics().total_acquisitions == 0
    assert base_limiter.get_metrics().total_acquisitions == 0


@pytest.mark.asyncio
async def test_reset_limiter_idempotent(setup_memory_limiters):
    """Test that calling reset multiple times is safe."""
    # Arrange
    limiter = get_limiter("api")
    await limiter.initialize()
    await limiter.acquire()

    # Act - reset multiple times
    await reset_limiter(limiter)
    await reset_limiter(limiter)
    await reset_limiter(limiter)

    # Assert - should still work
    assert limiter.get_metrics().total_acquisitions == 0
    allowed = await limiter.try_acquire(timeout=0)
    assert allowed is True


@pytest.mark.asyncio
async def test_reset_preserves_configuration(setup_memory_limiters):
    """Test that reset clears state but preserves configuration."""
    # Arrange
    limiter = get_limiter("api")
    await limiter.initialize()

    rate_before = limiter.rate_per_second
    concurrent_before = limiter.max_concurrent

    await limiter.acquire()

    # Act
    await reset_limiter(limiter)

    # Assert - config should be unchanged
    assert limiter.rate_per_second == rate_before
    assert limiter.max_concurrent == concurrent_before

    # But state should be reset
    assert limiter.get_metrics().total_acquisitions == 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    True,  # Skip by default - requires Redis
    reason="Requires Redis to be running",
)
async def test_reset_with_redis_backend():
    """Test that reset works with Redis backend.

    This test is skipped by default as it requires Redis.
    Run with: pytest -m "" tests/test_testing_utilities.py::test_reset_with_redis_backend
    """
    # Arrange
    configure_store("redis", strategy="redis", url="redis://localhost:6379/0")
    configure_limiter(
        "redis_api",
        store_id="redis",
        algorithm="sliding_window",
        limit=10,
        window_seconds=60,
    )

    limiter = get_limiter("redis_api")
    await limiter.initialize()

    # Acquire some slots
    for _ in range(5):
        await limiter.try_acquire(timeout=0)

    # Act
    await reset_limiter(limiter)

    # Assert - should be able to acquire fresh
    for _ in range(10):
        allowed = await limiter.try_acquire(timeout=0)
        assert allowed is True

    # Cleanup
    await limiter.disconnect()
