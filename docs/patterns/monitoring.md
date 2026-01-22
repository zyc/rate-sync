# Monitoring Pattern

Comprehensive observability for rate limiting with metrics, logging, alerting, and debugging.

---

## Problem

Without proper monitoring, rate limiting becomes a black box:

- ❌ Can't tell why users are being blocked
- ❌ Don't know which limits are being hit
- ❌ Can't detect attacks or abuse patterns
- ❌ No visibility into Redis health
- ❌ Can't optimize limit values

Production rate limiting requires full observability.

---

## Solution

Implement **multi-layer monitoring**:

1. **Metrics** - Quantitative data (counters, histograms, gauges)
2. **Logging** - Contextual events (allowed, blocked, errors)
3. **Tracing** - Request-level debugging
4. **Alerting** - Proactive issue detection
5. **Dashboards** - Visual insights

---

## Metrics

### Key Metrics to Track

```python
from prometheus_client import Counter, Histogram, Gauge, Info

# Decision metrics
rate_limit_checks_total = Counter(
    "rate_limit_checks_total",
    "Total rate limit checks",
    ["limiter_id", "endpoint", "result"],  # result: allowed, blocked, error
)

# Performance metrics
rate_limit_check_duration_seconds = Histogram(
    "rate_limit_check_duration_seconds",
    "Duration of rate limit checks",
    ["limiter_id", "backend"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# Usage metrics
rate_limit_usage_percent = Gauge(
    "rate_limit_usage_percent",
    "Current usage as percentage of limit",
    ["limiter_id", "identifier"],
)

rate_limit_remaining = Gauge(
    "rate_limit_remaining",
    "Remaining quota for identifier",
    ["limiter_id", "identifier"],
)

# Backend health
rate_limit_backend_errors_total = Counter(
    "rate_limit_backend_errors_total",
    "Total backend connection errors",
    ["backend", "error_type"],
)

rate_limit_backend_latency_seconds = Histogram(
    "rate_limit_backend_latency_seconds",
    "Backend operation latency",
    ["backend", "operation"],
)

# Configuration info
rate_limit_config_info = Info(
    "rate_limit_config",
    "Rate limiter configuration",
)
```

### Instrumentation Example

```python
import time
from prometheus_client import Counter, Histogram
from ratesync import get_or_clone_limiter
import logging

logger = logging.getLogger(__name__)

# Metrics
checks_total = Counter(
    "rate_limit_checks_total",
    "Total rate limit checks",
    ["limiter_id", "result"],
)

check_duration = Histogram(
    "rate_limit_check_duration_seconds",
    "Rate limit check duration",
    ["limiter_id"],
)

async def check_rate_limit(
    limiter_id: str,
    identifier: str,
    endpoint: str,
) -> bool:
    """Check rate limit with metrics."""
    start = time.time()

    try:
        # Get limiter
        limiter = await get_or_clone_limiter(limiter_id, identifier)

        # Try acquire
        allowed = await limiter.try_acquire(timeout=0)

        # Record duration
        duration = time.time() - start
        check_duration.labels(limiter_id=limiter_id).observe(duration)

        # Record result
        result = "allowed" if allowed else "blocked"
        checks_total.labels(limiter_id=limiter_id, result=result).inc()

        # Log if blocked
        if not allowed:
            state = await limiter.get_state()
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "limiter_id": limiter_id,
                    "identifier": identifier,
                    "endpoint": endpoint,
                    "remaining": state.remaining,
                    "reset_at": state.reset_at,
                },
            )

        return allowed

    except Exception as e:
        # Record error
        checks_total.labels(limiter_id=limiter_id, result="error").inc()

        logger.error(
            "Rate limit check failed",
            extra={
                "limiter_id": limiter_id,
                "identifier": identifier,
                "endpoint": endpoint,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )

        # Fail-open
        return True
```

---

## Logging

### Structured Logging with Context

```python
import structlog
from ratesync import get_or_clone_limiter

logger = structlog.get_logger()

async def rate_limit_with_logging(
    limiter_id: str,
    identifier: str,
    request_id: str,
    endpoint: str,
):
    """Rate limit check with structured logging."""

    # Bind context
    log = logger.bind(
        request_id=request_id,
        limiter_id=limiter_id,
        identifier=identifier,
        endpoint=endpoint,
    )

    try:
        limiter = await get_or_clone_limiter(limiter_id, identifier)
        allowed = await limiter.try_acquire(timeout=0)

        if allowed:
            state = await limiter.get_state()
            log.info(
                "rate_limit_passed",
                remaining=state.remaining,
                reset_at=state.reset_at,
            )
        else:
            state = await limiter.get_state()
            log.warning(
                "rate_limit_exceeded",
                limit=state.current_usage + state.remaining,
                reset_at=state.reset_at,
                retry_after=state.reset_at - int(time.time()),
            )

        return allowed

    except Exception as e:
        log.error(
            "rate_limit_error",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        return True  # Fail-open
```

### Log Sampling

```python
import random

async def rate_limit_with_sampled_logging(
    limiter_id: str,
    identifier: str,
    sample_rate: float = 0.01,  # 1% of successful checks
):
    """Log only a sample of successful checks to reduce noise."""

    limiter = await get_or_clone_limiter(limiter_id, identifier)
    allowed = await limiter.try_acquire(timeout=0)

    # Always log blocked/errors
    if not allowed:
        logger.warning("rate_limit_exceeded", limiter_id=limiter_id)
        return False

    # Sample successful checks
    if random.random() < sample_rate:
        logger.info("rate_limit_passed", limiter_id=limiter_id)

    return True
```

---

## Tracing

### OpenTelemetry Integration

```python
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from ratesync import get_or_clone_limiter

tracer = trace.get_tracer(__name__)

async def rate_limit_with_tracing(
    limiter_id: str,
    identifier: str,
):
    """Rate limit check with distributed tracing."""

    with tracer.start_as_current_span(
        "rate_limit.check",
        kind=SpanKind.INTERNAL,
    ) as span:
        # Add attributes
        span.set_attribute("rate_limit.limiter_id", limiter_id)
        span.set_attribute("rate_limit.identifier", identifier)

        try:
            limiter = await get_or_clone_limiter(limiter_id, identifier)

            # Trace acquisition
            with tracer.start_span("rate_limit.acquire") as acquire_span:
                allowed = await limiter.try_acquire(timeout=0)
                acquire_span.set_attribute("rate_limit.allowed", allowed)

            # Trace state retrieval
            if not allowed:
                with tracer.start_span("rate_limit.get_state") as state_span:
                    state = await limiter.get_state()
                    state_span.set_attribute("rate_limit.remaining", state.remaining)
                    state_span.set_attribute("rate_limit.reset_at", state.reset_at)

            # Record result
            span.set_attribute("rate_limit.result", "allowed" if allowed else "blocked")
            return allowed

        except Exception as e:
            span.set_attribute("rate_limit.result", "error")
            span.record_exception(e)
            raise
```

---

## Dashboards

### Grafana Dashboard (Prometheus)

```yaml
# Rate Limiting Overview Dashboard

panels:
  - title: "Rate Limit Decisions"
    type: graph
    targets:
      - expr: |
          rate(rate_limit_checks_total[5m])
        legendFormat: "{{result}}"

  - title: "Blocked Requests by Endpoint"
    type: graph
    targets:
      - expr: |
          rate(rate_limit_checks_total{result="blocked"}[5m])
        legendFormat: "{{endpoint}}"

  - title: "Rate Limit Check Latency (P95)"
    type: graph
    targets:
      - expr: |
          histogram_quantile(0.95, rate(rate_limit_check_duration_seconds_bucket[5m]))
        legendFormat: "{{limiter_id}}"

  - title: "Current Usage %"
    type: gauge
    targets:
      - expr: |
          rate_limit_usage_percent
        legendFormat: "{{limiter_id}}"

  - title: "Backend Errors"
    type: stat
    targets:
      - expr: |
          rate(rate_limit_backend_errors_total[5m])
        legendFormat: "{{error_type}}"

  - title: "Top Limited Identifiers"
    type: table
    targets:
      - expr: |
          topk(10, rate(rate_limit_checks_total{result="blocked"}[5m]))
        format: table
```

### Key Visualizations

1. **Rate Limit Decisions Over Time**
   - Line graph: allowed vs blocked vs errors
   - Helps identify attack patterns

2. **Blocked Requests by Limiter**
   - Stacked area chart
   - Shows which limits are most restrictive

3. **Check Latency Heatmap**
   - P50, P95, P99 percentiles
   - Identifies performance issues

4. **Usage Percentage Gauges**
   - Current usage as % of limit
   - Helps tune limit values

5. **Top Limited Users/IPs**
   - Table of most blocked identifiers
   - Identifies abuse sources

---

## Alerting

### Critical Alerts

#### High Rate Limit Error Rate

```yaml
# Prometheus Alert
- alert: RateLimitHighErrorRate
  expr: |
    rate(rate_limit_checks_total{result="error"}[5m]) > 10
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "High rate limit error rate"
    description: "Rate limiter is experiencing {{ $value }} errors/sec"
```

**Action**: Check Redis connectivity and health.

#### Backend Connection Failures

```yaml
- alert: RateLimitBackendDown
  expr: |
    rate(rate_limit_backend_errors_total[1m]) > 1
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Rate limit backend unreachable"
    description: "Backend {{ $labels.backend }} is failing"
```

**Action**: Investigate Redis cluster health.

### Warning Alerts

#### High Block Rate

```yaml
- alert: RateLimitHighBlockRate
  expr: |
    (
      rate(rate_limit_checks_total{result="blocked"}[5m])
      /
      rate(rate_limit_checks_total[5m])
    ) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High rate limit block rate"
    description: "{{ $value }}% of requests are being blocked"
```

**Action**: Investigate if attack is ongoing or limits too strict.

#### Approaching Limit

```yaml
- alert: RateLimitHighUsage
  expr: |
    rate_limit_usage_percent > 80
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "Identifier approaching rate limit"
    description: "{{ $labels.identifier }} at {{ $value }}% usage"
```

**Action**: Monitor for abuse or legitimate high usage.

#### Slow Rate Limit Checks

```yaml
- alert: RateLimitSlowChecks
  expr: |
    histogram_quantile(0.95, rate(rate_limit_check_duration_seconds_bucket[5m])) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Rate limit checks are slow"
    description: "P95 latency is {{ $value }}s"
```

**Action**: Check Redis latency and connection pool.

---

## Usage Analytics

### Track Limit Effectiveness

```python
from collections import defaultdict
from datetime import datetime, timedelta

class RateLimitAnalytics:
    """Track rate limit effectiveness over time."""

    def __init__(self):
        self.stats = defaultdict(lambda: {
            "allowed": 0,
            "blocked": 0,
            "unique_identifiers": set(),
        })

    def record_check(
        self,
        limiter_id: str,
        identifier: str,
        allowed: bool,
    ):
        """Record a rate limit check."""
        stats = self.stats[limiter_id]

        if allowed:
            stats["allowed"] += 1
        else:
            stats["blocked"] += 1

        stats["unique_identifiers"].add(identifier)

    def get_report(self, limiter_id: str) -> dict:
        """Get analytics report for limiter."""
        stats = self.stats[limiter_id]
        total = stats["allowed"] + stats["blocked"]

        if total == 0:
            return {"error": "No data"}

        return {
            "limiter_id": limiter_id,
            "total_checks": total,
            "allowed": stats["allowed"],
            "blocked": stats["blocked"],
            "block_rate": stats["blocked"] / total,
            "unique_identifiers": len(stats["unique_identifiers"]),
        }

# Global analytics instance
analytics = RateLimitAnalytics()

# In your rate limit check
async def check_with_analytics(limiter_id: str, identifier: str):
    allowed = await check_rate_limit(limiter_id, identifier)
    analytics.record_check(limiter_id, identifier, allowed)
    return allowed
```

### Periodic Reporting

```python
import asyncio
from datetime import datetime

async def generate_daily_report():
    """Generate daily rate limit report."""
    while True:
        # Wait for next day
        await asyncio.sleep(86400)

        # Generate report
        report = {
            "date": datetime.now().isoformat(),
            "limiters": {},
        }

        for limiter_id in ["public", "authenticated", "login"]:
            report["limiters"][limiter_id] = analytics.get_report(limiter_id)

        # Log or send report
        logger.info("Daily rate limit report", extra=report)

        # Send to monitoring system
        # send_to_datadog(report)
        # send_to_slack(report)
```

---

## Debugging Tools

### CLI Tool for Limiter State

```python
# debug_rate_limit.py
import asyncio
from ratesync import get_or_clone_limiter, list_limiters

async def debug_limiter(limiter_id: str, identifier: str):
    """Debug rate limiter state."""
    print(f"\n=== Debugging {limiter_id}:{identifier} ===\n")

    try:
        # Get limiter
        limiter = await get_or_clone_limiter(limiter_id, identifier)

        # Get current state
        state = await limiter.get_state()

        print(f"Current Usage: {state.current_usage}")
        print(f"Remaining: {state.remaining}")
        print(f"Reset At: {state.reset_at} ({datetime.fromtimestamp(state.reset_at)})")

        # Get metrics
        metrics = limiter.get_metrics()
        print(f"\nTotal Acquisitions: {metrics.total_acquisitions}")
        print(f"Failed Acquisitions: {metrics.failed_acquisitions}")

        # Get config
        config = limiter.get_config()
        print(f"\nAlgorithm: {config.get('algorithm', 'unknown')}")
        print(f"Limit: {config.get('limit', config.get('rate_per_second'))}")

    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python debug_rate_limit.py <limiter_id> <identifier>")
        sys.exit(1)

    asyncio.run(debug_limiter(sys.argv[1], sys.argv[2]))
```

Usage:
```bash
python debug_rate_limit.py login "192.168.1.1:user@example.com"
```

### HTTP Debug Endpoint

```python
from fastapi import FastAPI
from ratesync import get_or_clone_limiter, list_limiters

@app.get("/debug/rate-limits")
async def debug_rate_limits():
    """List all configured rate limiters."""
    limiters = list_limiters()

    return {
        "count": len(limiters),
        "limiters": limiters,
    }

@app.get("/debug/rate-limit/{limiter_id}/{identifier}")
async def debug_rate_limit_state(limiter_id: str, identifier: str):
    """Get state of specific limiter for identifier."""
    try:
        limiter = await get_or_clone_limiter(limiter_id, identifier)
        state = await limiter.get_state()
        metrics = limiter.get_metrics()
        config = limiter.get_config()

        return {
            "limiter_id": limiter_id,
            "identifier": identifier,
            "state": {
                "current_usage": state.current_usage,
                "remaining": state.remaining,
                "reset_at": state.reset_at,
            },
            "metrics": {
                "total_acquisitions": metrics.total_acquisitions,
                "failed_acquisitions": metrics.failed_acquisitions,
            },
            "config": config,
        }
    except Exception as e:
        return {"error": str(e)}, 500
```

---

## Performance Monitoring

### Track Backend Performance

```python
from prometheus_client import Histogram
import time

backend_latency = Histogram(
    "rate_limit_backend_operation_seconds",
    "Backend operation latency",
    ["backend", "operation"],
)

class InstrumentedRedisBackend:
    """Redis backend with instrumentation."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def incr(self, key: str):
        """Increment with instrumentation."""
        start = time.time()

        try:
            result = await self.redis.incr(key)
            return result
        finally:
            duration = time.time() - start
            backend_latency.labels(
                backend="redis",
                operation="incr",
            ).observe(duration)
```

---

## Best Practices

### 1. Use Correlation IDs

```python
import uuid

async def rate_limit_with_correlation(
    limiter_id: str,
    identifier: str,
    correlation_id: str = None,
):
    """Rate limit check with correlation ID."""
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    logger.info(
        "rate_limit_check_start",
        correlation_id=correlation_id,
        limiter_id=limiter_id,
    )

    # ... check logic ...

    logger.info(
        "rate_limit_check_end",
        correlation_id=correlation_id,
        allowed=allowed,
    )
```

### 2. Log Identifier Patterns

```python
# Don't log raw identifiers (PII)
logger.warning("rate_limit_exceeded", identifier="user@example.com")  # ❌

# Log hashed or anonymized identifiers
from ratesync import hash_identifier
hashed = hash_identifier("user@example.com")
logger.warning("rate_limit_exceeded", identifier_hash=hashed)  # ✅
```

### 3. Monitor Fail-Safe Behavior

```python
fail_safe_triggered = Counter(
    "rate_limit_fail_safe_total",
    "Times fail-safe was triggered",
    ["limiter_id", "mode"],  # mode: open, closed
)

async def check_with_fail_safe_monitoring(limiter_id: str, identifier: str):
    try:
        return await check_rate_limit(limiter_id, identifier)
    except Exception:
        # Determine fail mode from config
        config = get_limiter_config(limiter_id)
        fail_mode = "closed" if config.get("fail_closed") else "open"

        fail_safe_triggered.labels(
            limiter_id=limiter_id,
            mode=fail_mode,
        ).inc()

        return not config.get("fail_closed", False)
```

---

## See Also

- [Production Deployment](./production-deployment.md) - Setting up metrics collection
- [Testing Pattern](./testing.md) - Testing instrumentation
- [Authentication Protection](./authentication-protection.md) - Security monitoring
