# Rate Limiting Patterns

Common patterns and best practices for implementing rate limiting with rate-sync.

---

## Pattern Catalog

### Security Patterns
- [**Authentication Protection**](./authentication-protection.md) - Multi-layer rate limiting for auth endpoints
- [**Abuse Prevention**](./abuse-prevention.md) - Protecting against various attack vectors

### Application Patterns
- [**API Tiering**](./api-tiering.md) - Different limits for different user tiers
- [**Background Jobs**](./background-jobs.md) - Rate limiting for async processing

### Infrastructure Patterns
- [**Testing**](./testing.md) - Testing rate-limited code effectively
- [**Production Deployment**](./production-deployment.md) - Deploying with Redis/NATS
- [**Monitoring**](./monitoring.md) - Observability and alerting

---

## Quick Reference

### When to Use Each Algorithm

**Token Bucket** (`rate_per_second`)
- ✅ API throughput control
- ✅ Burst handling with steady rate
- ✅ Continuous traffic smoothing
- ❌ Exact quotas needed

**Sliding Window** (`limit` + `window_seconds`)
- ✅ Exact quota enforcement
- ✅ Preventing quota gaming
- ✅ Time-based allowances
- ❌ Need burst tolerance

---

## When to Use Composite Rate Limiting

Use `CompositeRateLimiter` when you need:

1. **Multi-dimensional rate limiting**
   - Per-IP AND per-user
   - Per-endpoint AND global
   - Multiple time windows

2. **Defense in depth**
   - Authentication endpoints (IP + credential)
   - Payment processing (IP + account + transaction)
   - Data export (IP + user + daily quota)

3. **Different detection levels**
   - Fast scanning detection (short window)
   - Sustained abuse detection (long window)
   - Global load protection (very short window)

---

## Backend Selection

| Backend | Use Case | Pros | Cons |
|---------|----------|------|------|
| **Memory** | Development, Testing, Single Process | Simple, Fast, No dependencies | No coordination, Lost on restart |
| **Redis** | Production, Distributed | Battle-tested, Fast, Persistent | Requires Redis, Network latency |
| **NATS** | Distributed, Event-driven | Low latency, Built-in HA | Requires NATS cluster |
| **PostgreSQL** | Existing Postgres, Lower QPS | No new infrastructure | Higher latency, DB load |

---

## Pattern Selection Guide

**Choose your primary concern:**

```
Security (auth, abuse)
  ├─ High security requirements
  │  └─ Use: Authentication Protection + Composite Limiting
  └─ Standard security
     └─ Use: Basic Token Bucket with IP limiting

Fair Usage (quotas, tiers)
  ├─ Exact quotas required
  │  └─ Use: Sliding Window + API Tiering Pattern
  └─ Approximate limits OK
     └─ Use: Token Bucket + Simple limits

Performance (throughput, load)
  ├─ Burst tolerance needed
  │  └─ Use: Token Bucket (high rate_per_second)
  └─ Strict rate required
     └─ Use: Sliding Window (tight window)

Integration (existing systems)
  ├─ FastAPI application
  │  └─ Use: RateLimitDependency (built-in)
  ├─ Background jobs
  │  └─ Use: Direct acquire() with context manager
  └─ Other frameworks
     └─ Use: Core RateLimiter directly
```

---

## Next Steps

1. **New to rate limiting?** Start with [Testing Pattern](./testing.md)
2. **Protecting auth endpoints?** See [Authentication Protection](./authentication-protection.md)
3. **Building an API?** Check [API Tiering](./api-tiering.md)
4. **Going to production?** Read [Production Deployment](./production-deployment.md)
