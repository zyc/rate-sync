# Memory Engine

In-memory rate limiting for single-process applications. Use for development, testing, or simple single-instance deployments.

## Installation

```bash
pip install rate-sync
```

No additional dependencies required.

## Quick Start

```toml
# rate-sync.toml
[stores.local]
engine = "memory"

[limiters.api]
store = "local"
rate_per_second = 10.0
```

```python
from ratesync import acquire

async with acquire("api"):
    response = await client.get(url)
```

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | Required | Must be `"memory"` |

No additional configuration options. The Memory engine stores state in process memory using `asyncio.Lock` and `collections.deque`.

## Advanced Usage

### Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter

configure_store("local", strategy="memory")
configure_limiter("api", store_id="local", rate_per_second=10.0)
```

### Development Setup with Relaxed Limits

```toml
[stores.dev]
engine = "memory"

[limiters.api]
store = "dev"
rate_per_second = 100.0

[limiters.auth]
store = "dev"
algorithm = "sliding_window"
limit = 100
window_seconds = 60
```

### Migration to Distributed

When ready for production, switch to Redis or PostgreSQL:

```toml
# Just change the store
[stores.prod]
engine = "redis"
url = "redis://localhost:6379/0"

[limiters.api]
store = "prod"  # Same limiter, different backend
rate_per_second = 10.0
```

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Rate limits not shared across workers | Memory engine is per-process | Use Redis or PostgreSQL for multi-process |
| Limits reset on restart | State is not persisted | Expected behavior; use distributed engine if persistence needed |
| High memory usage | Too many unique limiter keys | Review limiter key strategy; consider cleanup |

## Limitations

- Single process only (no coordination across workers/containers)
- State lost on restart
- Not suitable for distributed deployments

## See Also

- [Redis Engine](redis.md) - Recommended for production
- [PostgreSQL Engine](postgres.md) - Database-backed alternative
