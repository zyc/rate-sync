# Multi-Tenant Fairness

> **Requires:** `pip install rate-sync[redis]` or `pip install rate-sync[postgres]` for distributed backends.

Prevent a single tenant from monopolizing shared platform resources. Without per-tenant limits, one noisy neighbor can starve every other customer — even if they're on the same tier and paying the same price.

## The Problem

In a multi-tenant SaaS platform, all tenants share the same infrastructure. A single tenant running a bulk import, a misconfigured integration, or a traffic spike can consume all available capacity, causing degraded performance for everyone else.

This is different from [API Tiering](./api-tiering.md) (which defines what each tier *is allowed*) and [Abuse Prevention](./abuse-prevention.md) (which blocks *malicious* behavior). Multi-tenant fairness ensures *equitable access* — no tenant gets more than their fair share, regardless of intent.

## Configuration

```toml
# rate-sync.toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL}"

# Base limiter template — cloned per tenant
[limiters.tenant_api]
store = "redis"
rate_per_second = 50.0
max_concurrent = 20

# Stricter limit for expensive operations
[limiters.tenant_export]
store = "redis"
algorithm = "sliding_window"
limit = 50
window_seconds = 3600

# Global platform safety net
[limiters.platform_global]
store = "redis"
rate_per_second = 5000.0
```

## Basic Per-Tenant Limiting

```python
from ratesync import clone_limiter, acquire

async def tenant_request(tenant_id: str):
    # Each tenant gets their own rate limiter instance
    clone_limiter("tenant_api", f"tenant:{tenant_id}")
    await acquire(f"tenant:{tenant_id}")
```

Each tenant's limiter is independent — Tenant A hitting their limit has zero impact on Tenant B.

## FastAPI Integration

```python
import time
from fastapi import Depends, Header, HTTPException, Request
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

async def get_tenant_id(x_tenant_id: str = Header()) -> str:
    tenant = await db.get_tenant(x_tenant_id)
    if not tenant:
        raise HTTPException(401, "Invalid tenant")
    return tenant.id

async def enforce_tenant_limit(
    tenant_id: str = Depends(get_tenant_id),
):
    limiter = await get_or_clone_limiter("tenant_api", f"tenant:{tenant_id}")

    if not await limiter.try_acquire(timeout=0):
        state = await limiter.get_state()
        raise RateLimitExceededError(
            identifier=tenant_id,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=int(state.reset_at - time.time()),
        )

@app.get("/api/resources")
async def list_resources(_: None = Depends(enforce_tenant_limit)):
    return {"resources": [...]}
```

## Per-Operation Tenant Limits

Different operations have different costs. A list query is cheap; a PDF export is expensive.

```toml
[limiters.tenant_read]
store = "redis"
rate_per_second = 100.0

[limiters.tenant_write]
store = "redis"
rate_per_second = 20.0

[limiters.tenant_export]
store = "redis"
algorithm = "sliding_window"
limit = 50
window_seconds = 3600
```

```python
OPERATION_LIMITERS = {
    "read": "tenant_read",
    "write": "tenant_write",
    "export": "tenant_export",
}

async def enforce_tenant_operation(tenant_id: str, operation: str):
    limiter_id = OPERATION_LIMITERS[operation]
    limiter = await get_or_clone_limiter(limiter_id, f"tenant:{tenant_id}")

    if not await limiter.try_acquire(timeout=0):
        raise RateLimitExceededError(...)

@app.post("/api/reports/export")
async def export_report(tenant_id: str = Depends(get_tenant_id)):
    await enforce_tenant_operation(tenant_id, "export")
    return await generate_report()
```

## Combining with Tiered Limits

Tenant fairness and API tiering work together. Tiering defines the ceiling per plan; fairness ensures no tenant within a plan monopolizes resources.

```python
async def enforce_tenant_tiered_limit(
    tenant_id: str = Depends(get_tenant_id),
    tier: str = Depends(get_tenant_tier),
):
    # Tier-based limit (what the plan allows)
    tier_limiter = await get_or_clone_limiter(f"api_{tier}", f"tenant:{tenant_id}")
    if not await tier_limiter.try_acquire(timeout=0):
        raise RateLimitExceededError(...)

    # Global platform safety net (protects infrastructure)
    global_limiter = await get_or_clone_limiter("platform_global", "all")
    if not await global_limiter.try_acquire(timeout=0):
        raise HTTPException(503, "Platform capacity reached")
```

## Global Safety Net

Even with per-tenant limits, a burst of new tenants or a coordinated spike can overwhelm the platform. A global limiter acts as a circuit breaker.

```toml
[limiters.platform_global]
store = "redis"
rate_per_second = 5000.0

[limiters.platform_writes]
store = "redis"
rate_per_second = 1000.0
```

```python
async def check_platform_capacity():
    limiter = await get_or_clone_limiter("platform_global", "all")
    if not await limiter.try_acquire(timeout=0):
        logger.critical("Platform global limit reached — possible incident")
        raise HTTPException(503, "Service temporarily unavailable")
```

## Monitoring Tenant Usage

Track per-tenant consumption to detect noisy neighbors before they cause problems.

```python
async def get_tenant_usage(tenant_id: str) -> dict:
    limiter = await get_or_clone_limiter("tenant_api", f"tenant:{tenant_id}")
    state = await limiter.get_state()

    return {
        "tenant_id": tenant_id,
        "current_usage": state.current_usage,
        "remaining": state.remaining,
        "utilization": state.current_usage / (state.current_usage + state.remaining),
    }

# Alert when a tenant is using >80% of their quota
async def check_noisy_neighbors(tenant_ids: list[str]):
    for tenant_id in tenant_ids:
        usage = await get_tenant_usage(tenant_id)
        if usage["utilization"] > 0.8:
            logger.warning(
                "Noisy neighbor alert: tenant %s at %.0f%% utilization",
                tenant_id,
                usage["utilization"] * 100,
            )
```

## See Also

- [API Tiering](./api-tiering.md) — Different limits per plan
- [Graceful Degradation](./graceful-degradation.md) — What to do when tenant limits are hit
- [Abuse Prevention](./abuse-prevention.md) — Blocking malicious behavior
- [Burst Tuning Guide](./burst-tuning.md) — Sizing per-tenant limits
- [Monitoring](./monitoring.md) — Dashboards and alerting
- [Production Deployment](./production-deployment.md) — Infrastructure considerations
