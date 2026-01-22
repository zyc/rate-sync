# rate-sync Documentation

Welcome to the rate-sync documentation. This guide covers everything you need to know about using rate-sync for distributed rate limiting in Python applications.

## Table of Contents

### Getting Started

- [Main README](../README.md) - Installation, quick start, and overview
- [Configuration Guide](configuration.md) - TOML and programmatic configuration

### Engines

Choose the right engine for your use case:

- [Memory Engine](engines/memory.md) - In-memory rate limiting (development, single-process)
- [Redis Engine](engines/redis.md) - High-performance distributed (recommended for production)
- [NATS Engine](engines/nats.md) - Distributed via JetStream KV
- [PostgreSQL Engine](engines/postgres.md) - Database-backed coordination

### Integrations

- [FastAPI Integration](fastapi-integration.md) - Dependencies, middleware, and exception handling

### Reference

- [API Reference](api-reference.md) - Complete API documentation
- [Observability](observability.md) - Metrics, monitoring, and alerting

### Additional Resources

- [Changelog](../CHANGELOG.md) - Version history
- [Contributing](../CONTRIBUTING.md) - How to contribute

---

## Quick Navigation

### By Use Case

| Use Case | Recommended Engine | Documentation |
|----------|-------------------|---------------|
| Development/Testing | Memory | [Memory Engine](engines/memory.md) |
| Production (recommended) | Redis | [Redis Engine](engines/redis.md) |
| NATS infrastructure | NATS | [NATS Engine](engines/nats.md) |
| Existing PostgreSQL | PostgreSQL | [PostgreSQL Engine](engines/postgres.md) |

### By Algorithm

| Algorithm | Use Case | Configuration |
|-----------|----------|---------------|
| Token Bucket | Throughput control (req/sec) | [Configuration](configuration.md#token-bucket) |
| Sliding Window | Auth protection (attempts/window) | [Configuration](configuration.md#sliding-window) |

### By Integration

| Framework | Integration | Documentation |
|-----------|-------------|---------------|
| FastAPI | Dependencies, Middleware | [FastAPI Integration](fastapi-integration.md) |
| Generic | Decorator, Context Manager | [API Reference](api-reference.md) |

---

## Overview

rate-sync is a distributed rate limiter for Python with the following key features:

### Zero Configuration

Just create a `rate-sync.toml` file and import the package:

```python
from ratesync import acquire

await acquire("api")
```

### Dual Limiting Strategies

- **Rate Limiting** (`rate_per_second`): Controls throughput
- **Concurrency Limiting** (`max_concurrent`): Controls parallelism

### Multiple Algorithms

- **Token Bucket**: For API rate limiting
- **Sliding Window**: For authentication protection

### Multiple Engines

- **Memory**: Development and single-process
- **Redis**: Production with Redis
- **NATS**: Production with NATS infrastructure
- **PostgreSQL**: Production with existing database

### Built-in Observability

```python
metrics = get_limiter("api").get_metrics()
print(f"Avg wait: {metrics.avg_wait_time_ms}ms")
```

---

## Architecture

```
+------------------+     +------------------+     +------------------+
|   Application    |     |   Application    |     |   Application    |
|   (Container 1)  |     |   (Container 2)  |     |   (Container 3)  |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
         v                        v                        v
+--------+---------+     +--------+---------+     +--------+---------+
|    rate-sync     |     |    rate-sync     |     |    rate-sync     |
|    (limiter)     |     |    (limiter)     |     |    (limiter)     |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
         +------------------------+------------------------+
                                  |
                                  v
                    +-------------+-------------+
                    |      Coordination Store   |
                    |   (Redis / NATS / Postgres)  |
                    +---------------------------+
```

The architecture separates **stores** (coordination mechanism) from **limiters** (rate limit rules), allowing:

- Multiple limiters to share one store
- Easy switching between development (memory) and production (Redis/NATS)
- Clear separation of concerns

---

## Getting Help

- **Issues**: [GitHub Issues](https://github.com/rate-sync/rate-sync/issues)
- **Discussions**: [GitHub Discussions](https://github.com/rate-sync/rate-sync/discussions)
