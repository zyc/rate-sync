# Testing Pattern

Best practices for testing rate-limited code effectively without flaky tests or production coupling.

---

## Problem

Testing rate-limited code is challenging:

1. **Time-based behavior** - Tests become slow or flaky
2. **Shared state** - Tests interfere with each other
3. **Non-determinism** - Token bucket refills unpredictably
4. **Production coupling** - Tests require Redis/external services

Without proper patterns:
- ❌ Tests take minutes to run (waiting for windows)
- ❌ Tests fail randomly (race conditions)
- ❌ Tests require Docker/Redis (slow CI)
- ❌ Tests pollute each other's state

---

## Solution

Use **test-optimized configuration** with proper isolation:

1. **Memory backend** for unit tests (fast, isolated)
2. **Sliding window** for deterministic behavior
3. **Short windows** for fast execution
4. **Explicit reset** between tests
5. **Redis backend** only for integration tests

---

## Unit Testing Pattern

### Test Configuration

```python
import pytest
from ratesync import configure_store, configure_limiter, reset_limiter

@pytest.fixture(autouse=True)
async def setup_rate_limiters():
    """Configure test limiters before each test."""
    # Use memory backend for isolation
    configure_store("test", strategy="memory")

    # Sliding window with SHORT windows for speed
    configure_limiter(
        "test_limiter",
        store_id="test",
        algorithm="sliding_window",
        limit=5,
        window_seconds=1,  # 1 second window, not 60!
    )

    yield

    # Cleanup after test
    await reset_limiter("test_limiter")
```

### Basic Test Example

```python
from ratesync import get_or_clone_limiter

@pytest.mark.asyncio
async def test_rate_limit_blocks_after_quota():
    """Test that limiter blocks after exhausting quota."""
    limiter = await get_or_clone_limiter("test_limiter", "user123")

    # Exhaust quota (5 requests)
    for _ in range(5):
        allowed = await limiter.try_acquire(timeout=0)
        assert allowed, "Should allow within quota"

    # Next request should block
    allowed = await limiter.try_acquire(timeout=0)
    assert not allowed, "Should block after quota exhausted"
```

### Testing Window Reset

```python
import asyncio

@pytest.mark.asyncio
async def test_rate_limit_resets_after_window():
    """Test that quota resets after window expires."""
    limiter = await get_or_clone_limiter("test_limiter", "user456")

    # Exhaust quota
    for _ in range(5):
        await limiter.try_acquire(timeout=0)

    # Should be blocked now
    allowed = await limiter.try_acquire(timeout=0)
    assert not allowed

    # Wait for window to expire (1 second + buffer)
    await asyncio.sleep(1.1)

    # Should allow again
    allowed = await limiter.try_acquire(timeout=0)
    assert allowed, "Should allow after window reset"
```

---

## Testing Composite Rate Limiting

### Setup Multiple Limiters

```python
@pytest.fixture(autouse=True)
async def setup_composite_limiters():
    configure_store("test", strategy="memory")

    # Layer 1: IP-based (permissive)
    configure_limiter(
        "test_ip",
        store_id="test",
        algorithm="sliding_window",
        limit=10,
        window_seconds=1,
    )

    # Layer 2: Credential-based (restrictive)
    configure_limiter(
        "test_credential",
        store_id="test",
        algorithm="sliding_window",
        limit=3,
        window_seconds=1,
    )
```

### Test Most Restrictive Strategy

```python
from ratesync.composite import CompositeRateLimiter

@pytest.mark.asyncio
async def test_composite_triggers_on_most_restrictive():
    """Test that composite correctly identifies triggered layer."""
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "test_ip",
            "by_credential": "test_credential",
        },
        strategy="most_restrictive",
    )

    # Exhaust credential limit (3 requests)
    for _ in range(3):
        result = await composite.check(
            identifiers={
                "by_ip": "192.168.1.1",
                "by_credential": "user@example.com",
            }
        )
        assert result.allowed

    # Next request blocked by credential layer
    result = await composite.check(
        identifiers={
            "by_ip": "192.168.1.1",
            "by_credential": "user@example.com",
        }
    )
    assert not result.allowed
    assert result.triggered_by == "by_credential"
    assert result.most_restrictive.limit == 3
```

### Test Layer Isolation

```python
@pytest.mark.asyncio
async def test_composite_layers_are_independent():
    """Test that different credentials don't affect each other."""
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "test_ip",
            "by_credential": "test_credential",
        },
        strategy="most_restrictive",
    )

    # Exhaust quota for user1
    for _ in range(3):
        await composite.check(
            identifiers={
                "by_ip": "192.168.1.1",
                "by_credential": "user1@example.com",
            }
        )

    # user1 should be blocked
    result = await composite.check(
        identifiers={
            "by_ip": "192.168.1.1",
            "by_credential": "user1@example.com",
        }
    )
    assert not result.allowed

    # user2 should still be allowed (different credential)
    result = await composite.check(
        identifiers={
            "by_ip": "192.168.1.1",
            "by_credential": "user2@example.com",
        }
    )
    assert result.allowed
```

---

## Testing FastAPI Integration

### Test Setup with FastAPI TestClient

```python
import pytest
from fastapi import FastAPI, Depends
from httpx import AsyncClient
from ratesync import configure_store, configure_limiter
from ratesync.contrib.fastapi import RateLimitExceededError, rate_limit_exception_handler

@pytest.fixture
def app():
    """Create test FastAPI app."""
    app = FastAPI()

    # Register exception handler
    app.add_exception_handler(
        RateLimitExceededError,
        rate_limit_exception_handler,
    )

    async def rate_limit_dependency(request: Request):
        from ratesync import get_or_clone_limiter

        client_ip = request.client.host
        limiter = await get_or_clone_limiter("test_limiter", client_ip)

        if not await limiter.try_acquire(timeout=0):
            state = await limiter.get_state()
            raise RateLimitExceededError(
                identifier=client_ip,
                limit=state.current_usage + state.remaining,
                remaining=0,
                reset_at=state.reset_at,
                retry_after=int(state.reset_at - time.time()),
            )

    @app.get("/test")
    async def test_endpoint(_: None = Depends(rate_limit_dependency)):
        return {"success": True}

    return app

@pytest.fixture(autouse=True)
async def setup_test_limiter():
    configure_store("test", strategy="memory")
    configure_limiter(
        "test_limiter",
        store_id="test",
        algorithm="sliding_window",
        limit=3,
        window_seconds=1,
    )
```

### Test Rate Limit Response

```python
@pytest.mark.asyncio
async def test_endpoint_returns_429_when_limited(app):
    """Test that endpoint returns 429 after quota exhausted."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Make 3 successful requests
        for _ in range(3):
            response = await client.get("/test")
            assert response.status_code == 200

        # 4th request should be rate limited
        response = await client.get("/test")
        assert response.status_code == 429
        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Remaining"] == "0"
```

### Test Rate Limit Headers

```python
@pytest.mark.asyncio
async def test_endpoint_includes_rate_limit_headers(app):
    """Test that successful responses include rate limit headers."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/test")

        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

        # First request should show 2 remaining
        assert response.headers["X-RateLimit-Limit"] == "3"
        assert response.headers["X-RateLimit-Remaining"] == "2"
```

---

## Integration Testing with Redis

### Docker Compose for Tests

```yaml
# docker-compose.test.yml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 1s
      timeout: 3s
      retries: 30
```

### Integration Test Setup

```python
import pytest
import os
from ratesync import configure_store, configure_limiter

@pytest.fixture(scope="session")
def redis_url():
    """Get Redis URL from environment."""
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")

@pytest.fixture
async def setup_redis_limiter(redis_url):
    """Configure limiter with Redis backend."""
    configure_store("redis_test", strategy="redis", url=redis_url)

    configure_limiter(
        "integration_limiter",
        store_id="redis_test",
        algorithm="sliding_window",
        limit=5,
        window_seconds=10,
    )

    yield

    # Cleanup Redis keys
    import redis
    client = redis.from_url(redis_url)
    client.flushdb()
    client.close()
```

### Test Distributed Limiting

```python
@pytest.mark.asyncio
@pytest.mark.integration
async def test_redis_limiter_enforces_across_instances(setup_redis_limiter):
    """Test that Redis limiter works across multiple instances."""
    from ratesync import get_or_clone_limiter

    # Simulate two different app instances
    limiter1 = await get_or_clone_limiter("integration_limiter", "user123")
    limiter2 = await get_or_clone_limiter("integration_limiter", "user123")

    # Instance 1 makes 3 requests
    for _ in range(3):
        allowed = await limiter1.try_acquire(timeout=0)
        assert allowed

    # Instance 2 can only make 2 more (shared quota of 5)
    for _ in range(2):
        allowed = await limiter2.try_acquire(timeout=0)
        assert allowed

    # Both instances should now be blocked
    assert not await limiter1.try_acquire(timeout=0)
    assert not await limiter2.try_acquire(timeout=0)
```

---

## Test Isolation Best Practices

### Avoid Test Pollution

```python
# ❌ BAD - Tests share state
def test_first():
    limiter = get_limiter("shared")
    limiter.acquire()  # Affects next test!

def test_second():
    limiter = get_limiter("shared")  # Already consumed by first test
    assert limiter.try_acquire()  # May fail!


# ✅ GOOD - Tests are isolated
@pytest.fixture(autouse=True)
async def reset_between_tests():
    yield
    await reset_limiter("shared")

async def test_first():
    limiter = await get_or_clone_limiter("shared", "user1")
    await limiter.acquire()

async def test_second():
    limiter = await get_or_clone_limiter("shared", "user2")
    # Different identifier = independent quota
    assert await limiter.try_acquire()
```

### Use Unique Identifiers

```python
import uuid

@pytest.mark.asyncio
async def test_with_unique_identifier():
    """Test using unique identifier to avoid pollution."""
    unique_id = str(uuid.uuid4())
    limiter = await get_or_clone_limiter("test_limiter", unique_id)

    # This test's quota is completely independent
    for _ in range(5):
        assert await limiter.try_acquire(timeout=0)
```

---

## Performance Testing

### Load Test Example

```python
import asyncio
import time

@pytest.mark.asyncio
@pytest.mark.slow
async def test_throughput_under_load():
    """Test that limiter maintains target throughput."""
    configure_limiter(
        "throughput_test",
        store_id="test",
        algorithm="sliding_window",
        limit=1000,
        window_seconds=10,
    )

    limiter = await get_or_clone_limiter("throughput_test", "perf_test")

    start = time.time()
    successful = 0

    # Try to acquire 1000 times rapidly
    tasks = []
    for _ in range(1000):
        tasks.append(limiter.try_acquire(timeout=0))

    results = await asyncio.gather(*tasks)
    successful = sum(1 for r in results if r)

    elapsed = time.time() - start

    # Should allow all 1000 requests within quota
    assert successful == 1000
    # Should complete quickly (memory backend)
    assert elapsed < 1.0
```

---

## Mocking for Unit Tests

### Mock Rate Limiter for Upstream Tests

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_business_logic_without_rate_limiting():
    """Test business logic while mocking rate limiter."""

    # Mock the rate limiter to always allow
    with patch('ratesync.get_or_clone_limiter') as mock_get_limiter:
        mock_limiter = AsyncMock()
        mock_limiter.try_acquire.return_value = True
        mock_limiter.get_state.return_value = MagicMock(
            remaining=100,
            reset_at=int(time.time()) + 60
        )
        mock_get_limiter.return_value = mock_limiter

        # Test your business logic here
        # Rate limiting is mocked out
        result = await my_business_function()
        assert result.success
```

---

## CI/CD Configuration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install poetry
          poetry install

      - name: Run unit tests (memory backend)
        run: poetry run pytest tests/unit -v

  integration-tests:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install poetry
          poetry install --extras redis

      - name: Run integration tests (Redis backend)
        env:
          REDIS_URL: redis://localhost:6379/0
        run: poetry run pytest tests/integration -v -m integration
```

---

## Common Pitfalls

### ❌ Using Token Bucket for Exact Quotas

```python
# BAD - Token bucket refills continuously
configure_limiter(
    "test",
    algorithm="token_bucket",
    rate_per_second=10.0,
)

# Tests will be flaky because tokens refill during test
```

### ✅ Use Sliding Window Instead

```python
# GOOD - Exact quota enforcement
configure_limiter(
    "test",
    algorithm="sliding_window",
    limit=10,
    window_seconds=1,
)
```

### ❌ Long Windows in Tests

```python
# BAD - Test takes 60 seconds to complete
configure_limiter("test", limit=5, window_seconds=60)

# ... exhaust quota ...
await asyncio.sleep(61)  # Wait for reset
```

### ✅ Short Windows for Speed

```python
# GOOD - Test completes in ~1 second
configure_limiter("test", limit=5, window_seconds=1)

# ... exhaust quota ...
await asyncio.sleep(1.1)  # Quick reset
```

---

## See Also

- [Authentication Protection](./authentication-protection.md) - Multi-layer testing examples
- [Production Deployment](./production-deployment.md) - Production vs test configuration
- [Monitoring](./monitoring.md) - Testing instrumentation
