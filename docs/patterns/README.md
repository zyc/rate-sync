# Rate Limiting Patterns

Common patterns for implementing rate limiting with rate-sync.

## Pattern Catalog

### Security
- [Authentication Protection](./authentication-protection.md) — Multi-layer auth endpoint protection
- [Abuse Prevention](./abuse-prevention.md) — Detecting and blocking attack vectors

### Application
- [API Tiering](./api-tiering.md) — Different limits per user tier
- [Multi-Tenant Fairness](./multi-tenant-fairness.md) — Preventing noisy neighbors in SaaS platforms
- [Webhook Delivery](./webhook-delivery.md) — Rate limiting outbound webhook deliveries
- [File Uploads & Heavy Resources](./file-uploads.md) — Concurrency limits for expensive operations
- [Graceful Degradation](./graceful-degradation.md) — Priority shedding, queuing, and degraded responses
- [Background Jobs](./background-jobs.md) — Rate limiting async workers

### Operations
- [Burst Tuning Guide](./burst-tuning.md) — Choosing algorithms and parameters
- [Gradual Rollout](./gradual-rollout.md) — Safely introducing rate limits to production
- [Testing](./testing.md) — Testing rate-limited code
- [Production Deployment](./production-deployment.md) — Redis setup and HA
- [Monitoring](./monitoring.md) — Metrics, logging, alerting

## Algorithm Selection

| Algorithm | Best For | Configuration |
|-----------|----------|---------------|
| **Token Bucket** | Throughput control, burst tolerance | `rate_per_second` |
| **Sliding Window** | Exact quotas, time-based limits | `limit` + `window_seconds` |

## Backend Selection

| Backend | Use Case |
|---------|----------|
| **Memory** | Development, testing, single process |
| **Redis** | Production, distributed systems |
| **PostgreSQL** | Existing Postgres infrastructure |

## Quick Start

```toml
# rate-sync.toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL}"

[limiters.api]
store = "redis"
algorithm = "token_bucket"
rate_per_second = 100.0
```

```python
from ratesync import get_or_clone_limiter

limiter = await get_or_clone_limiter("api", user_id)
if await limiter.try_acquire(timeout=0):
    # Request allowed
    pass
```
