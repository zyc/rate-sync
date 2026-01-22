# API Tiering Pattern

Different rate limits for different user tiers (free, pro, enterprise) or API consumers.

---

## Problem

One-size-fits-all rate limiting doesn't work for modern APIs:

- Free users need basic access
- Paying customers expect better limits
- Enterprise clients require custom quotas
- Internal services need unlimited access

Without tiering:
- ❌ Paying customers get same limits as free tier
- ❌ Can't monetize higher API access
- ❌ Internal tools get rate limited
- ❌ Can't offer graduated pricing

---

## Solution

Use **identifier-based rate limiting** with different limiter configurations per tier:

```
User Request → Identify Tier → Select Limiter → Check Limit → Allow/Deny
```

---

## Implementation

### Method 1: Multiple Limiters (Recommended)

Create separate limiters for each tier:

```toml
# rate-sync.toml

[stores.redis]
strategy = "redis"
url = "${REDIS_URL}"

# Free tier: 100 req/hour
[limiters.api_free]
store_id = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 3600

# Pro tier: 1000 req/hour
[limiters.api_pro]
store_id = "redis"
algorithm = "sliding_window"
limit = 1000
window_seconds = 3600

# Enterprise tier: 10000 req/hour
[limiters.api_enterprise]
store_id = "redis"
algorithm = "sliding_window"
limit = 10000
window_seconds = 3600

# Internal: Unlimited (very high limit)
[limiters.api_internal]
store_id = "redis"
algorithm = "sliding_window"
limit = 1000000
window_seconds = 3600
```

### FastAPI Implementation

```python
from fastapi import Depends, Header, HTTPException
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

# Tier detection
async def get_user_tier(
    api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    """Determine user tier from API key."""
    # Look up API key in database
    user = await db.get_user_by_api_key(api_key)

    if not user:
        raise HTTPException(401, "Invalid API key")

    return user.tier  # "free", "pro", "enterprise", "internal"


# Rate limit dependency
async def rate_limit_by_tier(
    api_key: str = Header(..., alias="X-API-Key"),
    tier: str = Depends(get_user_tier),
):
    """Apply rate limit based on user tier."""
    # Map tier to limiter ID
    limiter_id = f"api_{tier}"

    # Get or create limiter for this specific API key
    limiter = await get_or_clone_limiter(limiter_id, api_key)

    # Try to acquire (fail-fast)
    allowed = await limiter.try_acquire(timeout=0)

    if not allowed:
        # Get current state for error message
        state = await limiter.get_state()

        raise RateLimitExceededError(
            identifier=api_key,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=state.reset_at - int(time.time()),
        )


# Apply to endpoints
@app.get("/api/data")
async def get_data(
    _: None = Depends(rate_limit_by_tier),
):
    return {"data": "value"}
```

---

## Method 2: Dynamic Configuration

For scenarios where tiers are defined in database:

```python
from ratesync import configure_limiter, get_or_clone_limiter


async def get_or_create_tier_limiter(
    user_id: str,
    tier: str,
) -> RateLimiter:
    """Get or create limiter with tier-specific configuration."""
    limiter_id = f"user_tier_{tier}"

    # Check if base limiter exists
    try:
        return await get_or_clone_limiter(limiter_id, user_id)
    except LimiterNotFoundError:
        # Create tier limiter on-demand
        tier_config = await db.get_tier_config(tier)

        configure_limiter(
            limiter_id,
            store_id="redis",
            algorithm="sliding_window",
            limit=tier_config.requests_per_hour,
            window_seconds=3600,
        )

        return await get_or_clone_limiter(limiter_id, user_id)
```

---

## Per-Endpoint Tiering

Different limits for different endpoints:

```toml
# Read operations - generous limits
[limiters.api_read_free]
limit = 1000
window_seconds = 3600

[limiters.api_read_pro]
limit = 10000
window_seconds = 3600

# Write operations - stricter limits
[limiters.api_write_free]
limit = 100
window_seconds = 3600

[limiters.api_write_pro]
limit = 1000
window_seconds = 3600

# Expensive operations - very strict
[limiters.api_export_free]
limit = 10
window_seconds = 86400  # per day

[limiters.api_export_pro]
limit = 100
window_seconds = 86400
```

```python
async def rate_limit_endpoint(
    endpoint_type: str,  # "read", "write", "export"
    api_key: str = Header(...),
    tier: str = Depends(get_user_tier),
):
    limiter_id = f"api_{endpoint_type}_{tier}"
    limiter = await get_or_clone_limiter(limiter_id, api_key)

    if not await limiter.try_acquire(timeout=0):
        raise RateLimitExceededError(...)


@app.get("/api/users")
async def list_users(
    _: None = Depends(lambda k, t: rate_limit_endpoint("read", k, t)),
):
    return {"users": [...]}


@app.post("/api/users")
async def create_user(
    _: None = Depends(lambda k, t: rate_limit_endpoint("write", k, t)),
):
    return {"created": True}


@app.post("/api/export")
async def export_data(
    _: None = Depends(lambda k, t: rate_limit_endpoint("export", k, t)),
):
    return {"export_id": "..."}
```

---

## Composite Tiering

Combine tier limits with global limits:

```python
from ratesync.composite import CompositeRateLimiter

async def rate_limit_with_global(
    api_key: str,
    tier: str,
):
    """Apply both tier-specific AND global limits."""
    composite = CompositeRateLimiter(
        limiters={
            "tier": f"api_{tier}",
            "global": "api_global",
        },
        strategy="most_restrictive",
    )

    result = await composite.check(
        identifiers={
            "tier": api_key,  # Per-user tier limit
            "global": "all",  # Global limit for all users
        },
        timeout=0,
    )

    if not result.allowed:
        # Inform which limit was hit
        if result.triggered_by == "global":
            raise HTTPException(503, "Service overloaded, try again later")
        else:
            raise RateLimitExceededError(...)
```

---

## Burst Allowances

Give higher tiers better burst handling:

```toml
# Free: Token bucket with low rate
[limiters.burst_free]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 1.0  # 1 req/s, can burst to ~60

# Pro: Token bucket with high rate
[limiters.burst_pro]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 10.0  # 10 req/s, can burst to ~600

# Enterprise: Very permissive
[limiters.burst_enterprise]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 100.0  # 100 req/s, can burst to ~6000
```

---

## Tier Upgrade Handling

Gracefully handle tier changes:

```python
async def handle_tier_change(user_id: str, old_tier: str, new_tier: str):
    """Reset limits when user upgrades/downgrades."""
    from ratesync.testing import reset_limiter

    # Reset old tier limiter
    old_limiter_id = f"user_tier_{old_tier}:{user_id}"
    await reset_limiter(old_limiter_id)

    logger.info(
        "User tier changed: user=%s, old=%s, new=%s",
        user_id,
        old_tier,
        new_tier,
    )

    # New tier limiter will be created on next request
```

---

## Monitoring by Tier

Track usage patterns per tier:

```python
# After rate limit check
if result.allowed:
    metrics.increment(f"api.requests.{tier}.allowed")
    metrics.histogram(f"api.requests.{tier}.remaining", result.remaining)
else:
    metrics.increment(f"api.requests.{tier}.blocked")
    metrics.increment(f"api.limit_exceeded.{result.triggered_by}")

# Alert on high usage
if result.remaining < result.limit * 0.1:  # <10% remaining
    logger.warning(
        "User approaching limit: tier=%s, user=%s, remaining=%d/%d",
        tier,
        user_id,
        result.remaining,
        result.limit,
    )
```

---

## Response Headers

Include tier information in headers:

```python
async def rate_limit_by_tier_with_headers(
    response: Response,
    api_key: str = Header(...),
    tier: str = Depends(get_user_tier),
):
    limiter = await get_or_clone_limiter(f"api_{tier}", api_key)

    if not await limiter.try_acquire(timeout=0):
        raise RateLimitExceededError(...)

    # Add headers showing tier and usage
    state = await limiter.get_state()

    response.headers["X-RateLimit-Limit"] = str(state.current_usage + state.remaining)
    response.headers["X-RateLimit-Remaining"] = str(state.remaining)
    response.headers["X-RateLimit-Reset"] = str(state.reset_at)
    response.headers["X-RateLimit-Tier"] = tier


@app.get("/api/data")
async def get_data(
    response: Response,
    _: None = Depends(rate_limit_by_tier_with_headers),
):
    return {"data": "value"}
```

---

## Testing

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_free_tier_limited():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Make 100 requests (free tier limit)
        for i in range(100):
            response = await client.get(
                "/api/data",
                headers={"X-API-Key": "free_user_key"},
            )
            assert response.status_code == 200

        # 101st request should be rate limited
        response = await client.get(
            "/api/data",
            headers={"X-API-Key": "free_user_key"},
        )
        assert response.status_code == 429


@pytest.mark.asyncio
async def test_pro_tier_higher_limit():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Pro tier should handle 1000 requests
        for i in range(200):  # Test subset
            response = await client.get(
                "/api/data",
                headers={"X-API-Key": "pro_user_key"},
            )
            assert response.status_code == 200
```

---

## See Also

- [Authentication Protection](./authentication-protection.md) - Multi-layer protection
- [Production Deployment](./production-deployment.md) - Deploying tiered limits
- [Monitoring](./monitoring.md) - Tracking tier usage
