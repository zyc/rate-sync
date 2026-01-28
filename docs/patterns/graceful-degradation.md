# Graceful Degradation

> **Requires:** `pip install rate-sync[redis]` for distributed backends.

What to do when rate limits are consistently hit — queue, shed, prioritize, or reject with clear communication.

## The Problem

Rate limiting tells you _when_ to stop. Graceful degradation tells you _what to do next_. Without it, users get a raw `429` and no guidance. With it, your system stays useful under pressure instead of becoming a wall of errors.

## Priority-Based Request Shedding

When the system is under pressure, serve high-priority requests first and shed the rest.

```toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL}"

# Generous limit for critical paths
[limiters.api_critical]
store = "redis"
rate_per_second = 500.0

# Tighter limit for normal traffic
[limiters.api_normal]
store = "redis"
rate_per_second = 200.0

# Strict limit for best-effort traffic
[limiters.api_low]
store = "redis"
rate_per_second = 50.0
```

```python
from enum import StrEnum
from ratesync import get_or_clone_limiter

class Priority(StrEnum):
    CRITICAL = "critical"   # Payments, auth, health checks
    NORMAL = "normal"       # Standard API calls
    LOW = "low"             # Reports, exports, analytics

PRIORITY_LIMITERS = {
    Priority.CRITICAL: "api_critical",
    Priority.NORMAL: "api_normal",
    Priority.LOW: "api_low",
}

async def enforce_priority_limit(identifier: str, priority: Priority) -> bool:
    limiter_id = PRIORITY_LIMITERS[priority]
    limiter = await get_or_clone_limiter(limiter_id, identifier)
    return await limiter.try_acquire(timeout=0)
```

## FastAPI Integration with Priority Routing

```python
import time
from fastapi import Depends, Request, HTTPException
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

def classify_request(request: Request) -> Priority:
    """Classify request priority based on path and method."""
    path = request.url.path

    if path.startswith("/api/payments") or path.startswith("/api/auth"):
        return Priority.CRITICAL
    if request.method in ("GET", "HEAD"):
        return Priority.NORMAL
    if path.startswith("/api/reports") or path.startswith("/api/export"):
        return Priority.LOW

    return Priority.NORMAL

async def priority_rate_limit(request: Request):
    priority = classify_request(request)
    identifier = request.client.host

    allowed = await enforce_priority_limit(identifier, priority)
    if not allowed:
        limiter_id = PRIORITY_LIMITERS[priority]
        limiter = await get_or_clone_limiter(limiter_id, identifier)
        state = await limiter.get_state()

        if priority == Priority.LOW:
            raise HTTPException(
                status_code=503,
                detail="Service busy. Low-priority requests are temporarily delayed.",
                headers={"Retry-After": "30"},
            )
        raise RateLimitExceededError(
            identifier=identifier,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=max(1, state.reset_at - int(time.time())),
        )

@app.get("/api/reports/monthly")
async def monthly_report(_: None = Depends(priority_rate_limit)):
    return await generate_report()
```

## Queuing Instead of Rejecting

For background-compatible requests, queue them instead of returning `429`.

```python
import asyncio
from collections import deque
from ratesync import get_or_clone_limiter

class RequestQueue:
    def __init__(self, max_queue_size: int = 100):
        self._queue: deque = deque(maxlen=max_queue_size)
        self._processing = False

    async def enqueue(self, operation, *args, **kwargs):
        if len(self._queue) >= self._queue.maxlen:
            raise HTTPException(503, "Queue full — try again later")

        future = asyncio.get_event_loop().create_future()
        self._queue.append((future, operation, args, kwargs))

        if not self._processing:
            asyncio.create_task(self._drain())

        return await future

    async def _drain(self):
        self._processing = True
        while self._queue:
            future, operation, args, kwargs = self._queue.popleft()
            try:
                result = await operation(*args, **kwargs)
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)
        self._processing = False

export_queue = RequestQueue(max_queue_size=50)

@app.post("/api/export")
async def export_data(user_id: str):
    limiter = await get_or_clone_limiter("api_low", user_id)

    if await limiter.try_acquire(timeout=0):
        return await generate_export(user_id)

    # Can't process now — queue it
    return await export_queue.enqueue(generate_export, user_id)
```

## Degraded Responses

When the full response is too expensive, return a lighter version instead of failing.

```python
from ratesync import get_or_clone_limiter

async def get_dashboard(user_id: str):
    limiter = await get_or_clone_limiter("api_normal", user_id)

    if await limiter.try_acquire(timeout=0):
        # Full response: real-time data, charts, recommendations
        return {
            "stats": await fetch_realtime_stats(user_id),
            "charts": await generate_charts(user_id),
            "recommendations": await compute_recommendations(user_id),
        }

    # Degraded response: cached summary only
    return {
        "stats": await get_cached_stats(user_id),
        "charts": None,
        "recommendations": None,
        "_degraded": True,
        "_reason": "High load — showing cached data",
    }
```

## Circuit Breaker with Automatic Recovery

Combine rate limiting with a circuit breaker to protect downstream services.

```python
import time
from ratesync import get_or_clone_limiter

class ServiceCircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_at = 0.0
        self.state = "closed"  # closed | open | half-open

    def record_failure(self):
        self.failures += 1
        self.last_failure_at = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "open"

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    @property
    def should_allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_at > self.recovery_timeout:
                self.state = "half-open"
                return True
            return False
        return True  # half-open: allow one probe

payment_breaker = ServiceCircuitBreaker(failure_threshold=3, recovery_timeout=30)

async def process_payment(user_id: str, amount: float):
    if not payment_breaker.should_allow:
        raise HTTPException(503, "Payment service temporarily unavailable")

    limiter = await get_or_clone_limiter("api_critical", user_id)
    await limiter.acquire(timeout=10.0)

    try:
        result = await payment_gateway.charge(user_id, amount)
        payment_breaker.record_success()
        return result
    except Exception as e:
        payment_breaker.record_failure()
        raise
```

## Communicating Degradation to Clients

Always tell clients what's happening and when to retry.

```python
import time
from fastapi.responses import JSONResponse
from ratesync import get_or_clone_limiter

async def rate_limit_with_guidance(
    limiter_id: str,
    identifier: str,
    priority: Priority,
) -> JSONResponse | None:
    limiter = await get_or_clone_limiter(limiter_id, identifier)

    if await limiter.try_acquire(timeout=0):
        return None  # Allowed — proceed normally

    state = await limiter.get_state()

    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "priority": priority,
            "remaining": state.remaining,
            "reset_at": state.reset_at,
            "message": "Request rate exceeded. See Retry-After header.",
        },
        headers={
            "Retry-After": str(state.reset_at - int(time.time())),
            "X-RateLimit-Limit": str(state.current_usage + state.remaining),
            "X-RateLimit-Remaining": str(state.remaining),
            "X-RateLimit-Reset": str(state.reset_at),
        },
    )
```

## See Also

- [Multi-Tenant Fairness](./multi-tenant-fairness.md) — Per-tenant resource isolation
- [Production Deployment](./production-deployment.md) — Fail-open vs fail-closed strategies
- [Monitoring](./monitoring.md) — Detecting when degradation kicks in
