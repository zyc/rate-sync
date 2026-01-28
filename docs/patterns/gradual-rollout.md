# Gradual Rollout

Safely introducing rate limiting to an existing system without disrupting production traffic.

## The Problem

Deploying rate limits to a running system is risky. Set them too low and you block legitimate users. Set them too high and they're useless. Deploy to everyone at once and a miscalculation causes an outage.

Gradual rollout solves this with three stages:

1. **Observe** — Log what _would_ be blocked, block nothing
2. **Enforce selectively** — Block for a percentage of traffic
3. **Full enforcement** — Block for everyone

## Stage 1: Shadow Mode (Observe Only)

Log rate limit decisions without enforcing them. This shows you what traffic patterns look like before you start blocking.

```python
import structlog
from ratesync import get_or_clone_limiter

logger = structlog.get_logger()

async def shadow_rate_limit(
    limiter_id: str,
    identifier: str,
    request_path: str,
):
    """Check rate limit but don't enforce — log only."""
    limiter = await get_or_clone_limiter(limiter_id, identifier)
    allowed = await limiter.try_acquire(timeout=0)

    if not allowed:
        state = await limiter.get_state()
        logger.warning(
            "shadow_rate_limit_would_block",
            limiter_id=limiter_id,
            identifier=identifier,
            path=request_path,
            current_usage=state.current_usage,
            remaining=state.remaining,
            reset_at=state.reset_at,
        )
    else:
        logger.debug(
            "shadow_rate_limit_passed",
            limiter_id=limiter_id,
            identifier=identifier,
        )

    # Always allow — this is observation only
    return True
```

### FastAPI Shadow Middleware

```python
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class ShadowRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter_id: str):
        super().__init__(app)
        self.limiter_id = limiter_id

    async def dispatch(self, request: Request, call_next):
        identifier = request.client.host
        await shadow_rate_limit(self.limiter_id, identifier, request.url.path)

        # Always proceed — shadow mode
        return await call_next(request)

# Deploy this first, monitor logs for a few days
app.add_middleware(ShadowRateLimitMiddleware, limiter_id="api")
```

### What to Look For

After deploying shadow mode, analyze the logs:

```python
# Pseudo-query: find users who would be blocked most often
#
# SELECT identifier, COUNT(*) as would_block_count
# FROM logs
# WHERE message = 'shadow_rate_limit_would_block'
# GROUP BY identifier
# ORDER BY would_block_count DESC
# LIMIT 20
```

Questions to answer before moving to Stage 2:

- **What percentage of requests would be blocked?** If > 5%, your limits are too strict.
- **Who gets blocked?** If it's your biggest customers, adjust the limits.
- **When does blocking happen?** Spikes at specific times may indicate legitimate batch operations.

## Stage 2: Percentage-Based Enforcement

Enforce rate limits for a percentage of traffic. Increase the percentage gradually as you gain confidence.

```python
import hashlib
import time
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

# Start at 5%, increase to 10%, 25%, 50%, 100%
ENFORCEMENT_PERCENTAGE = 10  # Change via config or env var

def is_in_enforcement_group(identifier: str, percentage: int) -> bool:
    """Deterministic: same identifier always gets same result."""
    hash_value = int(hashlib.sha256(identifier.encode()).hexdigest(), 16)
    return (hash_value % 100) < percentage

async def gradual_rate_limit(
    limiter_id: str,
    identifier: str,
):
    limiter = await get_or_clone_limiter(limiter_id, identifier)
    allowed = await limiter.try_acquire(timeout=0)

    if allowed:
        return

    if is_in_enforcement_group(identifier, ENFORCEMENT_PERCENTAGE):
        # Enforce: actually block this request
        state = await limiter.get_state()
        logger.info(
            "rate_limit_enforced",
            limiter_id=limiter_id,
            identifier=identifier,
            enforcement_pct=ENFORCEMENT_PERCENTAGE,
        )
        raise RateLimitExceededError(
            identifier=identifier,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=state.reset_at - int(time.time()),
        )
    else:
        # Shadow: log but allow
        logger.warning(
            "rate_limit_shadow_block",
            limiter_id=limiter_id,
            identifier=identifier,
            enforcement_pct=ENFORCEMENT_PERCENTAGE,
        )
```

### Ramp-Up Schedule

A typical rollout over two weeks:

| Day | Enforcement % | Action |
|-----|--------------|--------|
| 1-3 | 0% (shadow) | Deploy shadow mode, collect baseline |
| 4 | 5% | Enable for 5%, monitor error rates |
| 6 | 10% | Increase, check support tickets |
| 8 | 25% | Quarter of traffic |
| 10 | 50% | Half of traffic |
| 12 | 100% | Full enforcement |

At any point, if you see unexpected impact, roll back to the previous percentage.

## Stage 3: Full Enforcement with Safety Valves

Once at 100%, keep a kill switch for emergencies.

```python
import os
import time
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

# Emergency kill switch via environment variable
RATE_LIMITING_ENABLED = os.getenv("RATE_LIMITING_ENABLED", "true") == "true"

async def enforced_rate_limit(limiter_id: str, identifier: str):
    if not RATE_LIMITING_ENABLED:
        return  # Kill switch — allow everything

    limiter = await get_or_clone_limiter(limiter_id, identifier)
    if not await limiter.try_acquire(timeout=0):
        state = await limiter.get_state()
        raise RateLimitExceededError(
            identifier=identifier,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=state.reset_at - int(time.time()),
        )
```

## Per-Limiter Rollout

Different endpoints may need different rollout speeds. Critical endpoints (login, payments) should roll out slower than low-risk ones (public reads).

```python
ROLLOUT_CONFIG = {
    "api_public": 100,     # Fully enforced
    "api_auth": 50,        # 50% enforcement
    "api_payments": 10,    # 10% enforcement — extra caution
    "api_admin": 0,        # Shadow only
}

async def configurable_rate_limit(limiter_id: str, identifier: str):
    percentage = ROLLOUT_CONFIG.get(limiter_id, 0)

    if percentage == 0:
        await shadow_rate_limit(limiter_id, identifier, "")
        return

    await gradual_rate_limit(limiter_id, identifier)
```

## Allowlisting During Rollout

Exempt known-good clients during rollout to reduce risk.

```python
ALLOWLISTED_IDENTIFIERS = {
    "internal-service-a",
    "trusted-partner-key",
    "load-test-runner",
}

async def rate_limit_with_allowlist(limiter_id: str, identifier: str):
    if identifier in ALLOWLISTED_IDENTIFIERS:
        logger.debug("rate_limit_allowlisted", identifier=identifier)
        return

    await enforced_rate_limit(limiter_id, identifier)
```

## Monitoring the Rollout

Track these metrics during each stage:

```python
from prometheus_client import Counter, Gauge

rollout_decisions = Counter(
    "rate_limit_rollout_total",
    "Rollout decisions",
    ["limiter_id", "decision"],  # decision: allowed, shadow_blocked, enforced_blocked
)

rollout_percentage = Gauge(
    "rate_limit_rollout_percentage",
    "Current enforcement percentage",
    ["limiter_id"],
)
```

```yaml
# Alert: enforcement causing elevated error rates
- alert: RateLimitRolloutImpact
  expr: |
    rate(http_responses_total{status="429"}[5m])
    / rate(http_responses_total[5m]) > 0.05
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Rate limit rollout blocking >5% of traffic"
```

## See Also

- [Burst Tuning Guide](./burst-tuning.md) — Choosing the right limits
- [Monitoring](./monitoring.md) — Observability during rollout
- [Production Deployment](./production-deployment.md) — Infrastructure setup
