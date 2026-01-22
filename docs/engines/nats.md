# NATS Engine

Distributed rate limiting using NATS JetStream Key-Value Store.

## Overview

The NATS engine uses NATS JetStream's Key-Value store for distributed coordination across multiple processes and containers. It provides atomic Compare-And-Set (CAS) operations for safe concurrent updates.

**Highlights:**
- Distributed coordination via NATS JetStream KV
- Atomic CAS operations
- Works across processes, containers, and hosts
- Integrates with existing NATS infrastructure
- Built-in metrics via `RateLimiterMetrics`

**Best for:**
- Applications already using NATS
- Microservices architectures with NATS
- Real-time systems requiring low latency

## Installation

```bash
# Install with NATS support
pip install rate-sync[nats]

# Or with all engines
pip install rate-sync[all]
```

## Requirements

- NATS Server 2.9+ with JetStream enabled
- Python package: `nats-py >= 2.9.0`

## Configuration

### Basic TOML

```toml
[stores.nats]
engine = "nats"
url = "nats://localhost:4222"

[limiters.api]
store = "nats"
rate_per_second = 100.0
timeout = 30.0
```

### Full Options

```toml
[stores.nats]
engine = "nats"
url = "nats://localhost:4222"
token = "${NATS_TOKEN:-}"        # Optional auth token
bucket_name = "rate_limits"       # KV bucket name
auto_create = false               # Create bucket if missing
retry_interval = 0.05             # CAS retry interval (seconds)
max_retries = 100                 # Max CAS retries
timing_margin_ms = 10.0           # Timing safety margin
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `url` | str | Required | NATS server URL |
| `token` | str | None | Authentication token |
| `bucket_name` | str | `"rate_limits"` | JetStream KV bucket name |
| `auto_create` | bool | false | Create bucket if missing |
| `retry_interval` | float | 0.05 | Seconds between CAS retries |
| `max_retries` | int | 100 | Maximum CAS retry attempts |
| `timing_margin_ms` | float | 10.0 | Timing safety margin |

### Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter

configure_store(
    "nats",
    strategy="nats",
    url="nats://localhost:4222",
    bucket_name="rate_limits",
)

configure_limiter(
    "api",
    store_id="nats",
    rate_per_second=100.0,
    timeout=30.0,
)
```

### Advanced: Pre-Connected

```python
from nats import connect
from ratesync import configure_store

# Use existing NATS connection
nc = await connect("nats://localhost:4222")
js = nc.jetstream()

configure_store("nats", strategy="nats", jetstream=js)
```

## Auto-Create vs Manual Setup

### auto_create = true (Development)

Convenient for development - bucket is created automatically:

```toml
[stores.nats]
engine = "nats"
url = "nats://localhost:4222"
auto_create = true
```

### auto_create = false (Production)

Recommended for production - bucket must exist:

```toml
[stores.nats]
engine = "nats"
url = "nats://localhost:4222"
auto_create = false  # Requires pre-created bucket
```

Create bucket manually:
```bash
nats kv add rate_limits --replicas 3
```

## Running NATS

### Docker

```bash
docker run -d --name nats -p 4222:4222 nats:latest -js
```

### Docker Compose

```yaml
services:
  nats:
    image: nats:latest
    command: ["-js"]
    ports:
      - "4222:4222"
```

### Kubernetes

See [NATS Helm Chart](https://github.com/nats-io/k8s/tree/main/helm/charts/nats)

## Usage

```python
from ratesync import acquire

# Simple acquire
await acquire("api")

# Context manager (recommended)
async with acquire("api"):
    response = await http_client.get(url)
```

## How It Works

1. **KV Bucket**: Stores rate limit state per limiter
2. **State Storage**: Each limiter stores last acquisition timestamp
3. **CAS Operations**: Atomic compare-and-set for concurrent updates
4. **TTL**: Automatic cleanup of old keys
5. **Retry Logic**: Automatic retry on CAS conflicts

## Performance

| Metric | Value |
|--------|-------|
| Latency | 5-10ms per acquisition |
| Throughput | High (NATS is fast) |
| Scalability | Excellent (add more instances) |

## Troubleshooting

### "JetStream not enabled"

Enable JetStream on your NATS server:
```bash
nats-server -js
```

### "Connection refused"

Check NATS server is running:
```bash
nats server check connection
```

### "Bucket not found"

Either set `auto_create = true` or create bucket:
```bash
nats kv add rate_limits
```

### High CAS failures

If `metrics.cas_failures` is high, workers are racing for slots:
- Review deployment sizing
- Lower `retry_interval`
- Consider using Redis for higher throughput

## Metrics

The NATS engine tracks CAS-specific metrics:

```python
metrics = get_limiter("api").get_metrics()
print(f"CAS failures: {metrics.cas_failures}")
```

High CAS failures indicate contention - consider adjusting rate limits or using Redis.

## See Also

- [NATS Documentation](https://docs.nats.io/)
- [JetStream Guide](https://docs.nats.io/nats-concepts/jetstream)
- [Memory Engine](memory.md) - Development alternative
- [Redis Engine](redis.md) - Alternative distributed engine
- [PostgreSQL Engine](postgres.md) - Database-backed alternative
- [Configuration Guide](../configuration.md) - Complete configuration reference
