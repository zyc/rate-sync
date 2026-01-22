# Observability and Alerting

rate-sync provides built-in metrics for monitoring rate limiter performance and health. This guide covers how to access, export, and alert on these metrics.

## Table of Contents

- [Metrics Overview](#metrics-overview)
- [Accessing Metrics](#accessing-metrics)
- [Built-in Alert Loop](#built-in-alert-loop)
- [Prometheus Integration](#prometheus-integration)
- [Grafana Dashboards](#grafana-dashboards)
- [Alert Recommendations](#alert-recommendations)
- [Integration Testing](#integration-testing)

## Metrics Overview

Each limiter exposes the following metrics via `RateLimiterMetrics`:

| Field | Type | Description |
|-------|------|-------------|
| `total_acquisitions` | int | Total slots granted since process start |
| `total_wait_time_ms` | float | Total accumulated wait time |
| `avg_wait_time_ms` | float | Average wait time per acquisition |
| `max_wait_time_ms` | float | Longest wait time observed |
| `timeouts` | int | Number of failed `try_acquire()` calls |
| `cas_failures` | int | Failed Compare-And-Set attempts (NATS only) |
| `last_acquisition_at` | float | Unix timestamp of last successful slot |
| `current_concurrent` | int | Current in-flight operations |
| `max_concurrent_reached` | int | Times max concurrent limit was hit |
| `total_releases` | int | Total concurrency slot releases |

## Accessing Metrics

### Basic Usage

```python
from ratesync import get_limiter

limiter = get_limiter("api")
metrics = limiter.get_metrics()

print(f"Total acquisitions: {metrics.total_acquisitions}")
print(f"Avg wait time: {metrics.avg_wait_time_ms}ms")
print(f"Max wait time: {metrics.max_wait_time_ms}ms")
print(f"Timeouts: {metrics.timeouts}")
print(f"Current concurrent: {metrics.current_concurrent}")
```

### Listing All Limiters

```python
from ratesync import list_limiters, get_limiter

for limiter_id in list_limiters().keys():
    metrics = get_limiter(limiter_id).get_metrics()
    print(f"{limiter_id}: avg_wait={metrics.avg_wait_time_ms}ms, timeouts={metrics.timeouts}")
```

## Built-in Alert Loop

The repository includes `examples/metrics_alert.py`, a minimal monitoring loop that polls limiters and emits warnings when thresholds are exceeded.

### Running the Alert Loop

```bash
RATE_SYNC_ALERT_INTERVAL=5 \
RATE_SYNC_MAX_AVG_WAIT_MS=1500 \
RATE_SYNC_MAX_TIMEOUTS=3 \
python examples/metrics_alert.py
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_SYNC_ALERT_INTERVAL` | 5 | Polling interval in seconds |
| `RATE_SYNC_MAX_AVG_WAIT_MS` | 1500 | Alert threshold for avg wait time |
| `RATE_SYNC_MAX_TIMEOUTS` | 3 | Alert threshold for timeout count |

### Integration with Log Forwarders

Use a log forwarder (Grafana Loki, CloudWatch, Datadog) to trigger alerts when warning logs appear. The script serves as a template - replace `emit_alert` with your exporter.

## Prometheus Integration

### Basic Exporter

```python
import time
from prometheus_client import Gauge, start_http_server
from ratesync import get_limiter, list_limiters

# Define gauges
WAIT_GAUGE = Gauge("ratesync_wait_ms", "Average wait per limiter", ["limiter"])
TIMEOUT_GAUGE = Gauge("ratesync_timeouts", "Timeout count per limiter", ["limiter"])
CONCURRENT_GAUGE = Gauge("ratesync_concurrent", "Current concurrent per limiter", ["limiter"])

def export_metrics():
    """Export metrics to Prometheus."""
    for limiter_id in list_limiters().keys():
        try:
            metrics = get_limiter(limiter_id).get_metrics()
            WAIT_GAUGE.labels(limiter=limiter_id).set(metrics.avg_wait_time_ms)
            TIMEOUT_GAUGE.labels(limiter=limiter_id).set(metrics.timeouts)
            CONCURRENT_GAUGE.labels(limiter=limiter_id).set(metrics.current_concurrent)
        except Exception as e:
            print(f"Failed to export metrics for {limiter_id}: {e}")

if __name__ == "__main__":
    start_http_server(9000)
    print("Metrics server running on :9000")
    while True:
        export_metrics()
        time.sleep(5)
```

### Available Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `ratesync_wait_ms` | Gauge | limiter | Average wait time in milliseconds |
| `ratesync_timeouts` | Gauge | limiter | Total timeout count |
| `ratesync_concurrent` | Gauge | limiter | Current concurrent operations |
| `ratesync_acquisitions` | Counter | limiter | Total acquisitions |
| `ratesync_max_wait_ms` | Gauge | limiter | Maximum wait time observed |

### FastAPI Integration

```python
from fastapi import FastAPI
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from ratesync import get_limiter, list_limiters

app = FastAPI()

@app.get("/metrics")
async def metrics():
    # Export rate-sync metrics
    for limiter_id in list_limiters().keys():
        metrics = get_limiter(limiter_id).get_metrics()
        WAIT_GAUGE.labels(limiter=limiter_id).set(metrics.avg_wait_time_ms)

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

## Grafana Dashboards

### Example Dashboard JSON

```json
{
  "title": "Rate Limiter Metrics",
  "panels": [
    {
      "title": "Average Wait Time",
      "type": "graph",
      "targets": [
        {
          "expr": "ratesync_wait_ms",
          "legendFormat": "{{limiter}}"
        }
      ]
    },
    {
      "title": "Timeouts",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(ratesync_timeouts[5m])",
          "legendFormat": "{{limiter}}"
        }
      ]
    },
    {
      "title": "Concurrent Operations",
      "type": "graph",
      "targets": [
        {
          "expr": "ratesync_concurrent",
          "legendFormat": "{{limiter}}"
        }
      ]
    }
  ]
}
```

### Recommended Panels

1. **Average Wait Time**: Line graph showing `ratesync_wait_ms` per limiter
2. **Timeout Rate**: Rate of change of `ratesync_timeouts`
3. **Concurrent Operations**: Current `ratesync_concurrent` vs configured max
4. **CAS Failures** (NATS): Rate of `ratesync_cas_failures`

## Alert Recommendations

### Wait Time Alerts

| Condition | Severity | Action |
|-----------|----------|--------|
| avg_wait > 500ms | Warning | Monitor closely |
| avg_wait > 1000ms | Critical | Consider increasing rate limit |
| avg_wait > 2000ms | Emergency | Rate limit likely saturated |

```yaml
# Prometheus alerting rule
- alert: RateLimiterHighWaitTime
  expr: ratesync_wait_ms > 1000
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Rate limiter {{ $labels.limiter }} has high wait time"
    description: "Average wait time is {{ $value }}ms"
```

### Timeout Alerts

| Condition | Severity | Action |
|-----------|----------|--------|
| timeouts increasing | Warning | Callers may be failing |
| timeout rate > 1/min | Critical | Review rate limit configuration |

```yaml
- alert: RateLimiterTimeouts
  expr: rate(ratesync_timeouts[5m]) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Rate limiter {{ $labels.limiter }} is timing out"
```

### CAS Failures (NATS)

| Condition | Severity | Action |
|-----------|----------|--------|
| cas_failures spiking | Warning | Workers racing for slots |
| sustained high CAS | Critical | Review deployment sizing |

```yaml
- alert: RateLimiterCASFailures
  expr: rate(ratesync_cas_failures[5m]) > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High CAS failures on {{ $labels.limiter }}"
    description: "Consider using Redis or adjusting retry_interval"
```

### Concurrency Alerts

```yaml
- alert: RateLimiterAtCapacity
  expr: ratesync_concurrent >= ratesync_max_concurrent * 0.9
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Rate limiter {{ $labels.limiter }} near capacity"
```

## Integration Testing

Integration tests under `tests/integration/` validate cross-host guarantees for distributed engines.

### Running Integration Tests

```bash
# Redis
export REDIS_URL="redis://localhost:6379/0"
poetry run pytest -m "integration and redis"

# NATS
export NATS_INTEGRATION_URL="nats://localhost:4222"
poetry run pytest -m "integration and nats"

# PostgreSQL
export POSTGRES_INTEGRATION_URL="postgresql://user:pass@localhost/db"
poetry run pytest -m "integration and postgres"
```

### What Integration Tests Verify

1. **Cross-Process Coordination**: Multiple processes respect the same rate limit
2. **Atomic Operations**: No race conditions under load
3. **Timeout Behavior**: Timeouts work correctly under contention
4. **Metrics Accuracy**: Metrics reflect actual behavior

Run these tests regularly to catch regressions that could allow multiple hosts to exceed rate limits.

## Debugging Tips

### High Wait Times

```python
metrics = get_limiter("api").get_metrics()

if metrics.avg_wait_time_ms > 1000:
    print(f"High wait time: {metrics.avg_wait_time_ms}ms")
    print(f"Total acquisitions: {metrics.total_acquisitions}")
    print(f"Consider increasing rate_per_second")
```

### Tracking Concurrency

```python
metrics = get_limiter("api").get_metrics()

print(f"Current concurrent: {metrics.current_concurrent}")
print(f"Max concurrent reached: {metrics.max_concurrent_reached}")
print(f"Total releases: {metrics.total_releases}")

# Check for leaks
if metrics.current_concurrent > 0 and metrics.total_releases == metrics.total_acquisitions:
    print("Warning: Possible concurrency slot leak")
```

## See Also

- [Configuration Guide](configuration.md) - Configure limiters
- [API Reference](api-reference.md) - Full API documentation
- [Redis Engine](engines/redis.md) - Redis-specific monitoring
- [NATS Engine](engines/nats.md) - NATS-specific CAS metrics
