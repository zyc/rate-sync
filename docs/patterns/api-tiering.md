# API Tiering

> **Requires:** `pip install rate-sync[fastapi]` for FastAPI examples below.

Different rate limits for different user tiers (free, pro, enterprise).

## Configuration

```toml
# rate-sync.toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL}"

[limiters.api_free]
store = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 3600

[limiters.api_pro]
store = "redis"
algorithm = "sliding_window"
limit = 1000
window_seconds = 3600

[limiters.api_enterprise]
store = "redis"
algorithm = "sliding_window"
limit = 10000
window_seconds = 3600
```

## Implementation

```python
import time
from fastapi import Depends, Header, HTTPException
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

async def get_user_tier(api_key: str = Header(alias="X-API-Key")) -> str:
    user = await db.get_user_by_api_key(api_key)
    if not user:
        raise HTTPException(401, "Invalid API key")
    return user.tier  # "free", "pro", "enterprise"

async def rate_limit_by_tier(
    api_key: str = Header(alias="X-API-Key"),
    tier: str = Depends(get_user_tier),
):
    limiter = await get_or_clone_limiter(f"api_{tier}", api_key)

    if not await limiter.try_acquire(timeout=0):
        state = await limiter.get_state()
        raise RateLimitExceededError(
            identifier=api_key,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=int(state.reset_at - time.time()),
        )

@app.get("/api/data")
async def get_data(_: None = Depends(rate_limit_by_tier)):
    return {"data": "value"}
```

## Per-Endpoint Tiering

```toml
# Generous for reads
[limiters.read_free]
store = "redis"
algorithm = "sliding_window"
limit = 1000
window_seconds = 3600

# Strict for writes
[limiters.write_free]
store = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 3600

# Very strict for exports
[limiters.export_free]
store = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 86400
```

```python
async def rate_limit_endpoint(endpoint_type: str, api_key: str, tier: str):
    limiter = await get_or_clone_limiter(f"{endpoint_type}_{tier}", api_key)
    if not await limiter.try_acquire(timeout=0):
        raise RateLimitExceededError(...)

@app.get("/api/users")
async def list_users(api_key: str = Header(), tier: str = Depends(get_user_tier)):
    await rate_limit_endpoint("read", api_key, tier)
    return {"users": [...]}

@app.post("/api/export")
async def export(api_key: str = Header(), tier: str = Depends(get_user_tier)):
    await rate_limit_endpoint("export", api_key, tier)
    return {"export_id": "..."}
```

## Response Headers

```python
async def rate_limit_with_headers(response: Response, api_key: str, tier: str):
    limiter = await get_or_clone_limiter(f"api_{tier}", api_key)
    await limiter.try_acquire(timeout=0)

    state = await limiter.get_state()
    response.headers["X-RateLimit-Limit"] = str(state.current_usage + state.remaining)
    response.headers["X-RateLimit-Remaining"] = str(state.remaining)
    response.headers["X-RateLimit-Tier"] = tier
```

## See Also

- [Multi-Tenant Fairness](./multi-tenant-fairness.md) — Per-tenant resource isolation
- [Burst Tuning Guide](./burst-tuning.md) — Choosing the right limits per tier
- [Authentication Protection](./authentication-protection.md) — Protecting auth endpoints
- [Monitoring](./monitoring.md) — Per-tier observability
