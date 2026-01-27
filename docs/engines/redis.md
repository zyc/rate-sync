# Redis Engine

High-performance distributed rate limiting with atomic Lua scripts. Use for production deployments requiring coordination across multiple processes or containers.

## Compatible Versions

| Version | Status | Notes |
|---------|--------|-------|
| Redis 7.x | ✅ Recommended | Current stable, all features |
| Redis 6.x | ✅ Supported | LTS version, all features |
| Redis 5.x | ⚠️ Untested | May work, not in CI |

Tested in CI: Redis 6, Redis 7

## Installation

```bash
pip install rate-sync[redis]
```

## Quick Start

```toml
# rate-sync.toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"

[limiters.api]
store = "redis"
rate_per_second = 100.0
```

```python
from ratesync import acquire

async with acquire("api"):
    response = await client.get(url)
```

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | Required | Must be `"redis"` |
| `url` | str | Required | Redis connection URL |
| `db` | int | `0` | Database number (0-15) |
| `password` | str | `None` | Redis password |
| `key_prefix` | str | `"rate_limit"` | Key prefix for namespacing |
| `pool_min_size` | int | `2` | Minimum connection pool size |
| `pool_max_size` | int | `10` | Maximum connection pool size |
| `timing_margin_ms` | float | `10.0` | Timing safety margin (ms) |
| `socket_timeout` | float | `5.0` | Socket timeout (seconds) |
| `socket_connect_timeout` | float | `5.0` | Connection timeout (seconds) |

## Advanced Usage

### Token Bucket (Default)

Controls throughput with configurable rate:

```toml
[limiters.api]
store = "redis"
rate_per_second = 100.0
max_concurrent = 50
```

### Sliding Window

Counts requests in a time window. Ideal for login limiting or abuse prevention:

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 300
```

### Production Configuration

```toml
[stores.redis_prod]
engine = "redis"
url = "rediss://:${REDIS_PASSWORD}@redis.internal:6380/0"
pool_max_size = 20
socket_timeout = 10.0
```

### Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter

configure_store(
    "redis",
    strategy="redis",
    url="redis://localhost:6379/0",
    key_prefix="myapp",
    pool_max_size=20,
)

configure_limiter(
    "api",
    store_id="redis",
    rate_per_second=100.0,
    max_concurrent=50,
)
```

### Monitoring

```python
from ratesync import get_limiter

metrics = get_limiter("api").get_metrics()
print(f"Avg wait: {metrics.avg_wait_time_ms}ms")
print(f"Timeouts: {metrics.timeouts}")
```

```bash
# List rate limiter keys
redis-cli --scan --pattern "rate_limit:*"

# Check specific key
redis-cli GET rate_limit:api
```

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `ConnectionError` | Redis unreachable | Check host/port, network, Redis status |
| Connection pool exhausted | Too many concurrent operations | Increase `pool_max_size` |
| Rate limiting too strict | Configuration issue | Verify `rate_per_second` value |
| Keys never expire | Clock sync or margin issue | Check `timing_margin_ms`, verify server time |
| High latency | Network distance | Place Redis close to application |

## Security

1. **Authentication**: Use `requirepass` or ACLs
2. **TLS**: Use `rediss://` protocol for encrypted connections
3. **Network**: Keep Redis in private subnet, never expose publicly
4. **Memory**: Set `maxmemory` with `allkeys-lru` policy

## See Also

- [Redis Setup Guide](../setup/redis-setup.md) - Installation and configuration
- [Memory Engine](memory.md) - Development alternative
- [PostgreSQL Engine](postgres.md) - Database-backed alternative
