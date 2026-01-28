# Testing

Test rate-limited code without flaky tests or external dependencies.

## Configuration

```python
import pytest
from ratesync import configure_store, configure_limiter
from ratesync.testing import reset_limiter

@pytest.fixture(autouse=True)
async def setup_limiters():
    # Memory backend for isolation
    configure_store("test", strategy="memory")

    # Short windows for fast tests
    configure_limiter(
        "test_limiter",
        store_id="test",
        algorithm="sliding_window",
        limit=5,
        window_seconds=1,  # 1 second, not 60!
    )

    yield
    await reset_limiter("test_limiter")
```

## Basic Tests

```python
from ratesync import get_or_clone_limiter

@pytest.mark.asyncio
async def test_blocks_after_quota():
    limiter = await get_or_clone_limiter("test_limiter", "user123")

    # Exhaust quota
    for _ in range(5):
        assert await limiter.try_acquire(timeout=0)

    # Should block
    assert not await limiter.try_acquire(timeout=0)

@pytest.mark.asyncio
async def test_resets_after_window():
    limiter = await get_or_clone_limiter("test_limiter", "user456")

    for _ in range(5):
        await limiter.try_acquire(timeout=0)

    await asyncio.sleep(1.1)  # Wait for window reset
    assert await limiter.try_acquire(timeout=0)
```

## Testing Composite Limiters

```python
@pytest.fixture(autouse=True)
async def setup_composite():
    configure_store("test", strategy="memory")
    configure_limiter("test_ip", store_id="test", algorithm="sliding_window", limit=10, window_seconds=1)
    configure_limiter("test_cred", store_id="test", algorithm="sliding_window", limit=3, window_seconds=1)

@pytest.mark.asyncio
async def test_composite_triggers_correct_layer():
    from ratesync import CompositeRateLimiter

    composite = CompositeRateLimiter(
        limiters={"ip": "test_ip", "cred": "test_cred"},
        strategy="most_restrictive",
    )

    for _ in range(3):
        result = await composite.check(
            identifiers={"ip": "192.168.1.1", "cred": "user@test.com"}
        )
        assert result.allowed

    result = await composite.check(
        identifiers={"ip": "192.168.1.1", "cred": "user@test.com"}
    )
    assert not result.allowed
    assert result.triggered_by == "cred"
```

## FastAPI Integration Tests

> **Requires:** `pip install rate-sync[fastapi]`

```python
from fastapi.testclient import TestClient
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_returns_429_when_limited(app):
    async with AsyncClient(app=app, base_url="http://test") as client:
        for _ in range(3):
            response = await client.get("/test")
            assert response.status_code == 200

        response = await client.get("/test")
        assert response.status_code == 429
        assert "X-RateLimit-Remaining" in response.headers
```

## Test Isolation

```python
import uuid

@pytest.mark.asyncio
async def test_with_unique_identifier():
    # Use unique ID to avoid test pollution
    unique_id = str(uuid.uuid4())
    limiter = await get_or_clone_limiter("test_limiter", unique_id)

    for _ in range(5):
        assert await limiter.try_acquire(timeout=0)
```

## Common Pitfalls

```python
# BAD: Token bucket refills during test (flaky)
configure_limiter("test", algorithm="token_bucket", rate_per_second=10.0)

# GOOD: Sliding window has exact quotas
configure_limiter("test", algorithm="sliding_window", limit=10, window_seconds=1)

# BAD: Long window makes tests slow
configure_limiter("test", limit=5, window_seconds=60)

# GOOD: Short window for fast tests
configure_limiter("test", limit=5, window_seconds=1)
```

## See Also

- [Burst Tuning Guide](./burst-tuning.md) — Understanding algorithm differences for tests
- [Gradual Rollout](./gradual-rollout.md) — Shadow mode before enforcement
- [Authentication Protection](./authentication-protection.md) — Testing composite limiters
- [Production Deployment](./production-deployment.md) — Deploying tested code
