# Monitoring

Observability for rate limiting with metrics, logging, and alerting.

## Prometheus Metrics

```python
import time
from prometheus_client import Counter, Histogram

checks_total = Counter(
    "rate_limit_checks_total",
    "Total rate limit checks",
    ["limiter_id", "result"],  # result: allowed, blocked, error
)

check_duration = Histogram(
    "rate_limit_check_duration_seconds",
    "Rate limit check duration",
    ["limiter_id"],
)

async def check_with_metrics(limiter_id: str, identifier: str) -> bool:
    start = time.time()

    try:
        limiter = await get_or_clone_limiter(limiter_id, identifier)
        allowed = await limiter.try_acquire(timeout=0)

        check_duration.labels(limiter_id=limiter_id).observe(time.time() - start)
        checks_total.labels(limiter_id=limiter_id, result="allowed" if allowed else "blocked").inc()

        return allowed
    except Exception:
        checks_total.labels(limiter_id=limiter_id, result="error").inc()
        return True  # Fail-open
```

## Structured Logging

```python
import structlog

logger = structlog.get_logger()

async def check_with_logging(limiter_id: str, identifier: str, request_id: str):
    log = logger.bind(request_id=request_id, limiter_id=limiter_id)

    limiter = await get_or_clone_limiter(limiter_id, identifier)
    allowed = await limiter.try_acquire(timeout=0)

    if allowed:
        state = await limiter.get_state()
        log.info("rate_limit_passed", remaining=state.remaining)
    else:
        log.warning("rate_limit_exceeded")

    return allowed
```

## Alerting (Prometheus)

```yaml
# High error rate
- alert: RateLimitHighErrorRate
  expr: rate(rate_limit_checks_total{result="error"}[5m]) > 10
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Rate limiter experiencing errors"

# High block rate
- alert: RateLimitHighBlockRate
  expr: |
    rate(rate_limit_checks_total{result="blocked"}[5m])
    / rate(rate_limit_checks_total[5m]) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "10%+ requests being rate limited"

# Slow checks
- alert: RateLimitSlowChecks
  expr: |
    histogram_quantile(0.95, rate(rate_limit_check_duration_seconds_bucket[5m])) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "P95 latency > 100ms"
```

## Grafana Dashboard Queries

```promql
# Decisions over time
rate(rate_limit_checks_total[5m])

# Block rate by endpoint
rate(rate_limit_checks_total{result="blocked"}[5m])

# P95 latency
histogram_quantile(0.95, rate(rate_limit_check_duration_seconds_bucket[5m]))
```

## Debug Endpoint

```python
@app.get("/debug/rate-limit/{limiter_id}/{identifier}")
async def debug_state(limiter_id: str, identifier: str):
    limiter = await get_or_clone_limiter(limiter_id, identifier)
    state = await limiter.get_state()

    return {
        "limiter_id": limiter_id,
        "identifier": identifier,
        "current_usage": state.current_usage,
        "remaining": state.remaining,
        "reset_at": state.reset_at,
    }
```

## Best Practices

1. **Hash identifiers** - Don't log PII
   ```python
   from ratesync import hash_identifier
   log.warning("blocked", identifier_hash=hash_identifier(email))
   ```

2. **Sample success logs** - Reduce noise
   ```python
   import random
   if random.random() < 0.01:  # 1% sample
       log.info("rate_limit_passed")
   ```

3. **Use correlation IDs** - Trace requests
   ```python
   log.bind(request_id=request_id)
   ```

## See Also

- [Gradual Rollout](./gradual-rollout.md) — Monitoring during rollout
- [Graceful Degradation](./graceful-degradation.md) — Acting on monitoring signals
- [Burst Tuning Guide](./burst-tuning.md) — Adjusting limits based on metrics
- [Production Deployment](./production-deployment.md) — Infrastructure health checks
- [Testing](./testing.md) — Testing rate-limited code
