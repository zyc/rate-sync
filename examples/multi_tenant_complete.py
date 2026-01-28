"""
Complete Example: Multi-Tenant Rate Limiting with rate-sync

This example shows EXACTLY how to implement rate limiting per tenant tier.
"""

from fastapi import Depends, FastAPI, Header, HTTPException
from ratesync import acquire, clone_limiter, get_limiter, rate_limited

app = FastAPI()

# ==============================================================================
# STEP 1: Store tenant tier (typically in a database)
# ==============================================================================

# Simplified example — in production this would be PostgreSQL/Redis
TENANT_TIERS = {
    "tenant-acme": "free-tier",
    "tenant-globex": "pro-tier",
    "tenant-initech": "enterprise-tier",
}


# Or fetch from database
async def get_tenant_tier_from_db(tenant_id: str) -> str:
    """
    In production, you would do:

    async with db.pool.acquire() as conn:
        result = await conn.fetchrow(
            "SELECT tier FROM tenants WHERE id = $1", tenant_id
        )
        return result['tier']
    """
    # Simplified for this example
    tier = TENANT_TIERS.get(tenant_id, "free-tier")  # Default: free
    return tier


# ==============================================================================
# STEP 2: Extract tenant_id from request (Header, JWT, API Key, etc.)
# ==============================================================================


async def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    """
    Extract tenant_id from X-Tenant-ID header.

    Production alternatives:
    - JWT token: decode token, extract 'tenant_id' claim
    - API Key: look up API key in database, get tenant_id
    - Subdomain: request.url.hostname.split('.')[0]
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return x_tenant_id


# ==============================================================================
# APPROACH 1: Lambda with dynamic lookup
# ==============================================================================


@app.post("/api/v1/data")
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id, "free-tier"))
async def create_data_v1(data: dict, tenant_id: str = Depends(get_tenant_id)):
    """
    How it works:
    1. FastAPI executes get_tenant_id() -> returns "tenant-acme"
    2. Lambda executes: TENANT_TIERS.get("tenant-acme", "free-tier") -> "free-tier"
    3. rate_limited uses the "free-tier" limiter from TOML
    4. Applies rate_per_second=0.16, max_concurrent=2
    """
    return {"tenant": tenant_id, "data": data, "status": "created"}


# ==============================================================================
# APPROACH 2: Lambda with async lookup (database)
# ==============================================================================

# IMPORTANT: Lambda CANNOT be async, so we perform the lookup beforehand


async def get_tenant_tier(tenant_id: str) -> str:
    """Dependency that performs async lookup."""
    return await get_tenant_tier_from_db(tenant_id)


@app.post("/api/v2/data")
@rate_limited(lambda tier: tier)  # Lambda receives tier directly
async def create_data_v2(
    data: dict,
    tenant_id: str = Depends(get_tenant_id),
    tier: str = Depends(get_tenant_tier),  # Async lookup BEFORE the lambda
):
    """
    How it works:
    1. FastAPI executes get_tenant_id() -> "tenant-globex"
    2. FastAPI executes get_tenant_tier("tenant-globex") -> "pro-tier"
    3. Lambda executes: tier -> "pro-tier"
    4. rate_limited uses the "pro-tier" limiter from TOML
    5. Applies rate_per_second=1.66, max_concurrent=20
    """
    return {"tenant": tenant_id, "tier": tier, "data": data}


# ==============================================================================
# APPROACH 3: Build limiter_id manually (more explicit)
# ==============================================================================


@app.post("/api/v3/data")
async def create_data_v3(
    data: dict,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    More explicit approach — no lambda.
    """
    # 1. Determine tier
    tier = await get_tenant_tier_from_db(tenant_id)

    # 2. Build limiter_id manually
    limiter_id = tier  # "free-tier", "pro-tier", or "enterprise-tier"

    # 3. Apply rate limiting
    async with acquire(limiter_id):
        # Rate + concurrency limited by tier
        return {"tenant": tenant_id, "tier": tier, "data": data}


# ==============================================================================
# APPROACH 4: Clone limiter per individual tenant (most granular)
# ==============================================================================


async def ensure_tenant_limiter(tenant_id: str) -> str:
    """
    Create a limiter specific to the tenant based on their tier.

    Advantages:
    - Per-tenant metrics (not just per tier)
    - Can adjust limits individually
    """
    tier = await get_tenant_tier_from_db(tenant_id)
    limiter_id = f"tenant-{tenant_id}"

    # Clone the base tier limiter for this specific tenant
    # (idempotent — does nothing if it already exists)
    clone_limiter(
        source_id=tier,  # "free-tier", "pro-tier", etc.
        new_id=limiter_id,  # "tenant-acme", "tenant-globex", etc.
    )

    return limiter_id


@app.post("/api/v4/data")
async def create_data_v4(
    data: dict,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Limiter PER TENANT (not per tier).

    Advantages:
    - Individual per-tenant metrics (useful for billing)
    - Can adjust limits per tenant
    - Better visibility
    """
    limiter_id = await ensure_tenant_limiter(tenant_id)

    async with acquire(limiter_id):
        return {"tenant": tenant_id, "limiter": limiter_id, "data": data}


# ==============================================================================
# USAGE EXAMPLE: Client making requests
# ==============================================================================

"""
# Free Tier client (tenant-acme)
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-acme" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# How it works:
# 1. Header X-Tenant-ID = "tenant-acme"
# 2. Lookup: TENANT_TIERS["tenant-acme"] = "free-tier"
# 3. rate-sync applies free-tier limits:
#    - rate_per_second = 0.16 (~10/min)
#    - max_concurrent = 2
# 4. If exceeded: HTTP 429 Too Many Requests


# Pro Tier client (tenant-globex)
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-globex" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# How it works:
# 1. Header X-Tenant-ID = "tenant-globex"
# 2. Lookup: TENANT_TIERS["tenant-globex"] = "pro-tier"
# 3. rate-sync applies pro-tier limits:
#    - rate_per_second = 1.66 (~100/min)
#    - max_concurrent = 20
# 4. Much more throughput than the free tier!
"""


# ==============================================================================
# TRACKING PER-TENANT METRICS (for billing)
# ==============================================================================


@app.get("/admin/tenant/{tenant_id}/metrics")
async def get_tenant_metrics(tenant_id: str):
    """
    Admin endpoint to view a tenant's usage.
    Useful for billing, alerts, and analytics.
    """
    limiter_id = f"tenant-{tenant_id}"

    try:
        limiter = get_limiter(limiter_id)
        metrics = limiter.get_metrics()

        return {
            "tenant_id": tenant_id,
            "metrics": {
                "total_requests": metrics.total_acquisitions,
                "avg_wait_ms": metrics.avg_wait_time_ms,
                "max_wait_ms": metrics.max_wait_time_ms,
                "timeouts": metrics.timeouts,
                "current_concurrent": metrics.current_concurrent,
            },
        }
    except KeyError:
        return {"tenant_id": tenant_id, "error": "No data yet"}


# ==============================================================================
# COMPARISON: How it would look with `limits` (competitor)
# ==============================================================================

"""
WITH LIMITS (manual, boilerplate):

from limits import parse, MovingWindowRateLimiter
from limits.storage import RedisStorage

storage = RedisStorage("redis://localhost")
limiter = MovingWindowRateLimiter(storage)

TIER_LIMITS = {
    "free-tier": parse("10/minute"),
    "pro-tier": parse("100/minute"),
    "enterprise-tier": parse("1000/minute"),
}

@app.post("/api/data")
async def create_data(
    data: dict,
    x_tenant_id: str = Header(...)
):
    # Manual tier lookup
    tier = TENANT_TIERS.get(x_tenant_id, "free-tier")

    # Build namespace manually
    namespace = f"tenant:{x_tenant_id}"

    # Parse limit
    limit = TIER_LIMITS[tier]

    # Manual check
    if not limiter.hit(limit, namespace):
        raise HTTPException(429, "Rate limit exceeded")

    # PROBLEM: No concurrency limiting!
    # If 100 simultaneous requests arrive, they all pass
    # Backend can die even with rate limit

    # Metrics? DIY
    # Billing integration? Custom code

    return {"data": data}


WITH RATE-SYNC (declarative, elegant):

@app.post("/api/data")
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id, "free-tier"))
async def create_data(
    data: dict,
    tenant_id: str = Depends(get_tenant_id)
):
    # Automatic rate limiting
    # Automatic concurrency limiting
    # Automatic metrics
    # 3 lines vs 20+
    return {"data": data}
"""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
