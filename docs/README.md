# rate-sync Documentation

Distributed rate limiting for Python with declarative configuration.

## Quick Links

| Category | Documentation |
|----------|---------------|
| **Getting Started** | [Main README](../README.md) |
| **Configuration** | [Configuration Guide](configuration.md) |
| **API** | [API Reference](api-reference.md) |
| **FastAPI** | [FastAPI Integration](fastapi-integration.md) |
| **Observability** | [Metrics & Monitoring](observability.md) |

## Engine Selection

| Engine | Use Case | Coordination | Documentation |
|--------|----------|--------------|---------------|
| Memory | Development, single-process | Local | [Memory Engine](engines/memory.md) |
| Redis | Production (recommended) | Distributed | [Redis Engine](engines/redis.md) |
| PostgreSQL | Existing database infrastructure | Distributed | [PostgreSQL Engine](engines/postgres.md) |

## Algorithm Selection

| Algorithm | Parameters | Best For |
|-----------|------------|----------|
| Token Bucket | `rate_per_second`, `max_concurrent` | API throughput control |
| Sliding Window | `limit`, `window_seconds` | Auth protection, abuse prevention |

## 5-Minute Quick Start

### 1. Install

```bash
pip install rate-sync[redis]  # Production
pip install rate-sync          # Development (memory only)
```

### 2. Configure

Create `rate-sync.toml`:

```toml
[stores.main]
engine = "redis"
url = "${REDIS_URL:-redis://localhost:6379/0}"

[limiters.api]
store = "main"
rate_per_second = 100.0
max_concurrent = 50
timeout = 30.0
```

### 3. Use

```python
from ratesync import acquire

# Config auto-loads on import
async with acquire("api"):
    response = await client.get(url)
```

## Architecture

```
Application Instances          Coordination Store
+------------------+          +------------------+
|   rate-sync      |--------->|                  |
+------------------+          |  Redis or        |
+------------------+          |  PostgreSQL      |
|   rate-sync      |--------->|                  |
+------------------+          +------------------+
+------------------+
|   rate-sync      |---------------^
+------------------+
```

**Key concepts:**

- **Stores** define the coordination mechanism (Memory, Redis, PostgreSQL)
- **Limiters** define rate limiting rules and reference a store
- Multiple limiters can share one store connection

## Common Patterns

### Rate Limiting (Throughput)

```toml
[limiters.external_api]
store = "redis"
rate_per_second = 10.0  # Max 10 req/sec
```

### Concurrency Limiting (Parallelism)

```toml
[limiters.db_pool]
store = "redis"
max_concurrent = 20  # Max 20 simultaneous
```

### Combined (Production)

```toml
[limiters.production]
store = "redis"
rate_per_second = 100.0  # Throughput
max_concurrent = 50       # Parallelism
timeout = 30.0
```

### Auth Protection (Sliding Window)

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 300  # 5 attempts per 5 min
```

## Support

- [GitHub Issues](https://github.com/rate-sync/rate-sync/issues)
- [GitHub Discussions](https://github.com/rate-sync/rate-sync/discussions)
