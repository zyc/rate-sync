# Burst Tuning Guide

A practical guide to choosing the right algorithm, parameters, and configuration for your rate limiting needs.

## Token Bucket vs Sliding Window

rate-sync provides two algorithms. Choosing the wrong one leads to either false rejections or ineffective limits.

### Token Bucket

**Best for:** Controlling throughput (requests per second). Allows short bursts above the average rate.

```toml
[limiters.api]
store = "redis"
algorithm = "token_bucket"    # default
rate_per_second = 100.0       # 100 req/s sustained
```

**How it works:** Tokens are added at a steady rate (`rate_per_second`). Each request consumes one token. If tokens are available, the request proceeds immediately. If not, it waits until a token is replenished.

**Key behavior:** A client that was idle for 10 seconds and then sends 20 requests at once — all 20 succeed instantly (tokens accumulated during idle time). This "burst" is a feature, not a bug.

### Sliding Window

**Best for:** Enforcing exact quotas over a time period. No burst allowance.

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5                     # exactly 5 attempts
window_seconds = 300          # per 5-minute window
```

**How it works:** Counts requests in a rolling time window. Request #6 in a 5-minute window is always rejected, regardless of when requests #1–5 happened.

**Key behavior:** Even if a client waited 4 minutes between requests, the 6th request within the window is rejected. This strictness is the point — use it for security limits.

### Decision Table

| Scenario | Algorithm | Why |
|----------|-----------|-----|
| API rate limiting | Token Bucket | Allows natural traffic bursts |
| Login protection | Sliding Window | Exact attempt counting matters |
| Third-party API quota | Token Bucket | Smooth out request flow |
| Daily export limit | Sliding Window | Hard cap on expensive operations |
| Background job throughput | Token Bucket | Steady flow to external services |
| Account creation | Sliding Window | Strict anti-abuse |

## Tuning `rate_per_second`

### Starting Point

Calculate from your actual capacity:

```
rate_per_second = (total_capacity × safety_margin) / number_of_clients
```

**Example:** Your database handles 1,000 queries/sec. You have ~100 active users and want 20% headroom:

```
rate_per_second = (1000 × 0.8) / 100 = 8.0
```

```toml
[limiters.api]
store = "redis"
rate_per_second = 8.0
```

### Common Patterns

```toml
# Public API — generous
[limiters.api_public]
store = "redis"
rate_per_second = 100.0

# Authenticated API — moderate
[limiters.api_auth]
store = "redis"
rate_per_second = 50.0

# Write operations — conservative
[limiters.api_write]
store = "redis"
rate_per_second = 10.0

# External service proxy — match their limit
[limiters.stripe]
store = "redis"
rate_per_second = 90.0    # Stripe allows 100/s — stay 10% under
```

### Signs You Need Adjustment

| Symptom | Cause | Fix |
|---------|-------|-----|
| Legitimate users getting 429s | Rate too low | Increase `rate_per_second` |
| Backend overloaded despite limits | Rate too high | Decrease `rate_per_second` |
| Bursty traffic causes brief overloads | No concurrency limit | Add `max_concurrent` |
| Users wait too long | Rate fine, timeout too long | Reduce `timeout` |

## Tuning `max_concurrent`

Concurrency limits (`max_concurrent`) control how many operations run _at the same time_. This is orthogonal to rate — 100 req/s is about throughput; 10 concurrent is about parallelism.

### When to Use Concurrency Limits

Use `max_concurrent` when the operation holds a resource for its duration:

```toml
# Database connections: limited pool
[limiters.db_query]
store = "redis"
max_concurrent = 20      # match your connection pool size

# File uploads: memory-intensive
[limiters.upload]
store = "redis"
max_concurrent = 5

# PDF generation: CPU-intensive
[limiters.pdf]
store = "redis"
max_concurrent = 3
```

**Don't** use `max_concurrent` for fast, stateless operations (API lookups, cache reads). Rate limiting alone is sufficient.

### Combining Rate + Concurrency

For operations that are both frequent and resource-heavy:

```toml
# External API: 50 req/s but max 10 in-flight
[limiters.external_api]
store = "redis"
rate_per_second = 50.0
max_concurrent = 10
timeout = 30.0
```

This means: send up to 50 requests per second, but never have more than 10 running simultaneously. If 10 are in-flight, new requests wait even if the rate limit allows them.

```python
# IMPORTANT: Use context manager to auto-release concurrency slots
async with acquire("external_api"):
    response = await http_client.get(url)
```

## Tuning `timeout`

The `timeout` parameter controls how long a request waits for a rate limit slot before giving up.

### Guidelines

```toml
# User-facing API: fail fast
[limiters.api]
store = "redis"
rate_per_second = 100.0
timeout = 2.0               # 2s max wait

# Background job: can wait
[limiters.batch]
store = "redis"
rate_per_second = 10.0
timeout = 60.0              # 1 min is fine

# Critical path: moderate wait
[limiters.payment]
store = "redis"
rate_per_second = 20.0
timeout = 10.0              # 10s before failing
```

### `timeout = 0` (Non-Blocking)

For check-and-reject patterns (no waiting):

```python
limiter = await get_or_clone_limiter("api", user_id)
if not await limiter.try_acquire(timeout=0):
    raise HTTPException(429, "Rate limit exceeded")
```

### No timeout (Wait Indefinitely)

For operations that must eventually complete:

```python
# Will wait as long as needed
await acquire("critical_operation")
```

## Tuning `window_seconds` and `limit`

For sliding window limiters, these two parameters define the quota.

### Common Configurations

```toml
# Login protection: 5 per 5 minutes
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 300

# Hourly API quota: 1000 per hour
[limiters.api_hourly]
store = "redis"
algorithm = "sliding_window"
limit = 1000
window_seconds = 3600

# Daily export cap: 10 per day
[limiters.export]
store = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 86400

# Signup rate: 3 per hour per IP
[limiters.signup]
store = "redis"
algorithm = "sliding_window"
limit = 3
window_seconds = 3600
```

### Window Size Trade-offs

| Window | Behavior | Best For |
|--------|----------|----------|
| Short (60s) | Responsive, resets fast | API throttling |
| Medium (300-3600s) | Balanced | Login protection |
| Long (86400s) | Strict quota | Daily caps, expensive ops |

## Capacity Planning Checklist

Before deploying rate limits to production:

1. **Measure current traffic** — What's the actual p50/p95/p99 request rate per user?
2. **Identify bottlenecks** — What breaks first? Database? CPU? External API?
3. **Set limits above normal** — Limits should catch abuse, not normal usage. Start at 2-3x your p95.
4. **Monitor before enforcing** — Use [Gradual Rollout](./gradual-rollout.md) to observe before blocking.
5. **Review regularly** — Traffic patterns change. Review limits quarterly.

```python
# Step 1: Measure before setting limits
from ratesync import get_or_clone_limiter

async def measure_usage(user_id: str):
    limiter = await get_or_clone_limiter("api", user_id)
    state = await limiter.get_state()
    metrics = limiter.get_metrics()

    return {
        "current_usage": state.current_usage,
        "avg_wait_ms": metrics.avg_wait_time_ms,
        "timeouts": metrics.timeouts,
        "max_concurrent_reached": metrics.max_concurrent_reached,
    }
```

## See Also

- [Graceful Degradation](./graceful-degradation.md) — What to do when limits are hit
- [Monitoring](./monitoring.md) — Detecting misconfigured limits
- [Production Deployment](./production-deployment.md) — Infrastructure sizing
- [Gradual Rollout](./gradual-rollout.md) — Safely introducing limits
