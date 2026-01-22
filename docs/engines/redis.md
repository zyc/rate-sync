# Redis Engine

High-performance distributed rate limiting using Redis with atomic Lua scripts.

## Overview

The Redis engine stores rate limit state in Redis and performs atomic updates via Lua scripts. This ensures correct behavior even when multiple processes or containers access the same limiter simultaneously.

**Highlights:**
- Single round-trip per acquisition
- Atomic updates (no race conditions)
- Works across processes, containers, and hosts
- Supports both token bucket and sliding window algorithms
- Built-in metrics via `RateLimiterMetrics`

**Best for:**
- Production deployments
- Multi-container applications
- High-performance rate limiting

## Installation

```bash
# Install with Redis support
pip install rate-sync[redis]

# Or with all engines
pip install rate-sync[all]
```

## Requirements

- Redis 6.0+ (7.x recommended)
- Python package: `redis[asyncio] >= 5.0.0`
- Low network latency between application and Redis

## Configuration

### Basic TOML

```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"

[limiters.api]
store = "redis"
rate_per_second = 100.0
timeout = 30.0
```

### Full Options

```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"
db = 0                              # Database number (0-15)
password = "${REDIS_PASSWORD:-}"    # Optional password
key_prefix = "rate_limit"           # Key prefix for namespacing
pool_min_size = 2                   # Min connections
pool_max_size = 10                  # Max connections
timing_margin_ms = 10.0             # Timing safety margin
socket_timeout = 5.0                # Socket timeout (seconds)
socket_connect_timeout = 5.0        # Connection timeout (seconds)
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `url` | str | Required | Redis connection URL |
| `db` | int | 0 | Database number (0-15) |
| `password` | str | None | Redis password |
| `key_prefix` | str | `"rate_limit"` | Key prefix for namespacing |
| `pool_min_size` | int | 2 | Minimum connection pool size |
| `pool_max_size` | int | 10 | Maximum connection pool size |
| `timing_margin_ms` | float | 10.0 | Timing safety margin in milliseconds |
| `socket_timeout` | float | 5.0 | Socket timeout in seconds |
| `socket_connect_timeout` | float | 5.0 | Connection timeout in seconds |

### Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter

configure_store(
    "redis",
    strategy="redis",
    url="redis://localhost:6379/0",
    key_prefix="myapp_limits",
    pool_max_size=20,
)

configure_limiter(
    "api",
    store_id="redis",
    rate_per_second=100.0,
    max_concurrent=50,
    timeout=30.0,
)
```

## Algorithms

### Token Bucket (Default)

Controls throughput with `rate_per_second`:

```toml
[limiters.api]
store = "redis"
rate_per_second = 100.0    # Max 100 requests per second
max_concurrent = 50        # Max 50 simultaneous (optional)
```

### Sliding Window

Counts requests in a time window (Redis only):

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5                  # Max 5 attempts
window_seconds = 300       # Per 5 minutes
```

Use sliding window for:
- Login attempt limiting
- Password reset protection
- OTP verification
- Abuse prevention

## Running Redis

### Docker

```bash
docker run -d --name redis-ratesync -p 6379:6379 redis:7-alpine
```

### Docker Compose

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command:
      - redis-server
      - --appendonly yes
      - --maxmemory 256mb
      - --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
```

## Usage Examples

### Basic Usage

```python
from ratesync import acquire

# Simple acquire
await acquire("api")

# Context manager (recommended)
async with acquire("api"):
    response = await http_client.get(url)
```

### With Timeout

```python
await acquire("api", timeout=5.0)
```

### Sliding Window

```python
from ratesync import acquire

# Login attempt limiting
try:
    await acquire("login", timeout=0)  # Fail-fast
    # Process login
except RateLimiterAcquisitionError:
    # Too many login attempts
    raise HTTPException(429, "Too many login attempts")
```

## Monitoring

### Redis CLI

```bash
# List rate limiter keys
redis-cli --scan --pattern "rate_limit:*"

# Check specific key
redis-cli GET rate_limit:api
redis-cli TTL rate_limit:api
```

### Application Metrics

```python
from ratesync import get_limiter

metrics = get_limiter("api").get_metrics()
print(f"Avg wait: {metrics.avg_wait_time_ms}ms")
print(f"Timeouts: {metrics.timeouts}")
print(f"Current concurrent: {metrics.current_concurrent}")
```

See [Observability](../observability.md) for alerting strategies.

## Troubleshooting

| Symptom | Solution |
|---------|----------|
| `ConnectionError` | Ensure Redis is running and reachable |
| Too many connections | Increase `pool_max_size` or reuse instances |
| Rate limiting too strict | Increase `rate_per_second` |
| Keys never expire | Check `timing_margin_ms` or clock sync |

## Security Recommendations

1. **Enable authentication** (`requirepass` or ACLs)
2. **Use TLS** (`rediss://`) when crossing networks
3. **Restrict network access** - Redis should not be public
4. **Set `maxmemory`** to contain resource usage

### Production Configuration

```toml
[stores.redis_prod]
engine = "redis"
url = "rediss://:${REDIS_PASSWORD}@redis.internal:6380/0"
password = "${REDIS_PASSWORD}"
pool_max_size = 20
socket_timeout = 10.0
```

## Performance

| Metric | Value |
|--------|-------|
| Latency | 1-5ms per acquisition |
| Throughput | 10,000+ ops/second |
| Scalability | Excellent (add more app instances) |

## How It Works

1. **Lua Scripts**: All operations are atomic via `EVAL`
2. **Key Structure**: `{key_prefix}:{limiter_id}` stores timestamps
3. **TTL**: Keys auto-expire based on rate limit window
4. **Connection Pool**: Reuses connections for efficiency

## See Also

- [Memory Engine](memory.md) - Development alternative
- [NATS Engine](nats.md) - Alternative distributed engine
- [PostgreSQL Engine](postgres.md) - Database-backed alternative
- [Configuration Guide](../configuration.md) - Complete configuration reference
- [Observability](../observability.md) - Monitoring and alerting
