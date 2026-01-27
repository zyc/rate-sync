# Rate Limiting Flow Diagrams

## Request Flow

```
Client Request
      │
      ▼
┌─────────────────┐
│ Extract Header  │  X-Tenant-ID: tenant-acme
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Lookup Tier     │  TENANT_TIERS["tenant-acme"] → "free-tier"
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Load Config     │  [limiters.free-tier] → rate: 0.16/s, concurrent: 2
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│ Check Limits    │────►│    Redis     │
└────────┬────────┘     └──────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  Pass      Reject
    │         │
    ▼         ▼
 Handler    HTTP 429
```

## Limit Check Decision

```
                    ┌─────────────────┐
                    │  Rate Limit OK? │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
        ┌─────────────────┐          ┌─────────────┐
        │ Concurrency OK? │          │ Wait/Reject │
        └────────┬────────┘          └─────────────┘
                 │
    ┌────────────┴────────────┐
    │                         │
    ▼                         ▼
  Pass                  Wait/Reject
```

## Multi-Tier Comparison

```
┌──────────────┬─────────────┬──────────────┬────────────────┐
│    Tier      │  Rate/min   │  Concurrent  │  100 requests  │
├──────────────┼─────────────┼──────────────┼────────────────┤
│ Free         │     10      │      2       │ 10 pass, 90 429│
│ Pro          │    100      │     20       │ All pass       │
│ Enterprise   │   1000      │    200       │ All pass       │
└──────────────┴─────────────┴──────────────┴────────────────┘
```

## Redis Data Structure

```
ratelimit:{tier}:tokens        → "2.4"           # Remaining tokens
ratelimit:{tier}:last_update   → "1640000123"    # Last refill time
ratelimit:{tier}:concurrent    → "1"             # Active requests
ratelimit:{tier}:metrics:total → "847"           # Total requests
```

## Code Flow

```python
@app.post("/data")
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id))
async def create_data(tenant_id: str = Depends(get_tenant_id)):
    # 1. get_tenant_id() extracts X-Tenant-ID header → "tenant-acme"
    # 2. Lambda executes: TENANT_TIERS.get("tenant-acme") → "free-tier"
    # 3. rate-sync loads [limiters.free-tier] from TOML
    # 4. Redis checks rate + concurrency limits
    # 5. If OK → handler executes
    # 6. If exceeded → HTTP 429
    return {"status": "ok"}
```
