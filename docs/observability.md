# Observability

rate-sync exposes metrics for monitoring rate limiter performance and health.

## Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `total_acquisitions` | Counter | Total slots granted since start |
| `total_wait_time_ms` | Counter | Accumulated wait time |
| `avg_wait_time_ms` | Gauge | Average wait time per acquisition |
| `max_wait_time_ms` | Gauge | Longest wait time observed |
| `timeouts` | Counter | Failed `try_acquire()` calls |
| `cas_failures` | Counter | Failed Compare-And-Set attempts |
| `current_concurrent` | Gauge | Current in-flight operations |
| `max_concurrent_reached` | Counter | Times max concurrent was hit |

## Accessing Metrics

```python
from ratesync import get_limiter, list_limiters

# Single limiter
metrics = get_limiter("api").get_metrics()
print(f"Avg wait: {metrics.avg_wait_time_ms}ms, Timeouts: {metrics.timeouts}")

# All limiters
for limiter_id in list_limiters().keys():
    metrics = get_limiter(limiter_id).get_metrics()
    print(f"{limiter_id}: {metrics.avg_wait_time_ms}ms")
```

## Prometheus Integration

```python
from prometheus_client import Gauge, start_http_server
from ratesync import get_limiter, list_limiters

WAIT_GAUGE = Gauge("ratesync_wait_ms", "Avg wait time", ["limiter"])
TIMEOUT_GAUGE = Gauge("ratesync_timeouts", "Timeout count", ["limiter"])
CONCURRENT_GAUGE = Gauge("ratesync_concurrent", "Current concurrent", ["limiter"])

def export_metrics():
    for limiter_id in list_limiters().keys():
        m = get_limiter(limiter_id).get_metrics()
        WAIT_GAUGE.labels(limiter=limiter_id).set(m.avg_wait_time_ms)
        TIMEOUT_GAUGE.labels(limiter=limiter_id).set(m.timeouts)
        CONCURRENT_GAUGE.labels(limiter=limiter_id).set(m.current_concurrent)

# FastAPI endpoint
@app.get("/metrics")
async def metrics():
    export_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

## Logging Configuration

Configure via standard Python logging:

```python
import logging

logging.getLogger("ratesync").setLevel(logging.INFO)
```

Log levels:
- **DEBUG**: Acquisition details, timing
- **INFO**: Startup, configuration loaded
- **WARNING**: Timeouts, backend failures (fail-open)
- **ERROR**: Backend connection errors

## Health Checks

```python
from ratesync import get_limiter

@app.get("/health")
async def health():
    try:
        limiter = get_limiter("api")
        # Simple health check - metrics accessible
        _ = limiter.get_metrics()
        return {"status": "healthy"}
    except Exception as e:
        return JSONResponse({"status": "unhealthy", "error": str(e)}, status_code=503)
```

## Alerting Recommendations

| Condition | Severity | Action |
|-----------|----------|--------|
| `avg_wait > 500ms` | Warning | Monitor closely |
| `avg_wait > 1000ms` | Critical | Increase rate limit |
| `timeouts increasing` | Warning | Review configuration |
| `timeout rate > 1/min` | Critical | Backend may be saturated |

**Prometheus alert example:**

```yaml
- alert: RateLimiterHighWaitTime
  expr: ratesync_wait_ms > 1000
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Rate limiter {{ $labels.limiter }} has high wait time"
```

## See Also

- [FastAPI Integration](fastapi-integration.md)
- [Configuration Guide](configuration.md)
