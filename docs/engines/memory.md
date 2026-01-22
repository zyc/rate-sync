# Memory Engine

In-memory rate limiting for single-process applications.

## Overview

The Memory engine uses local Python data structures and asyncio primitives for rate limiting. It's the simplest engine with zero external dependencies.

**Best for:**
- Development and testing
- Single-process applications
- Local rate limiting without coordination needs

**Not suitable for:**
- Multi-process applications
- Distributed systems
- Containerized deployments with multiple replicas

## Installation

The Memory engine is included by default with rate-sync:

```bash
pip install rate-sync
```

No additional dependencies required.

## Configuration

### TOML Configuration

```toml
[stores.local]
engine = "memory"

[limiters.api]
store = "local"
rate_per_second = 10.0
timeout = 30.0
```

### Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter

configure_store("local", strategy="memory")
configure_limiter("api", store_id="local", rate_per_second=10.0)
```

## Configuration Options

The Memory engine has no additional configuration options.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `engine` | str | Required | Must be `"memory"` |

## Usage

```python
from ratesync import acquire

# Simple acquire
await acquire("api")

# Context manager (recommended for concurrency limiting)
async with acquire("api"):
    response = await http_client.get(url)
```

## How It Works

The Memory engine implements rate limiting using:

1. **Token Bucket Algorithm**: Controls throughput using timestamps
2. **asyncio.Lock**: Prevents race conditions within the process
3. **asyncio.Semaphore**: Manages concurrency limiting
4. **collections.deque**: Tracks timestamps for sliding window

All coordination happens in-process memory, meaning:
- No network calls required
- Sub-millisecond latency
- No synchronization across processes

## Performance

| Metric | Value |
|--------|-------|
| Latency | <1ms (local memory access) |
| Throughput | Very high (no I/O) |
| Scalability | Single process only |

## Limitations

The Memory engine does **not** coordinate across:
- Multiple processes
- Multiple containers
- Multiple hosts

For distributed rate limiting, use:
- [Redis Engine](redis.md) (recommended)
- [NATS Engine](nats.md)
- [PostgreSQL Engine](postgres.md)

## Migration to Distributed

### To Redis

```toml
# Change engine
[stores.prod]
engine = "redis"
url = "redis://localhost:6379/0"

[limiters.api]
store = "prod"
rate_per_second = 10.0
```

### To NATS

```toml
[stores.prod]
engine = "nats"
url = "nats://localhost:4222"

[limiters.api]
store = "prod"
rate_per_second = 10.0
```

### To PostgreSQL

```toml
[stores.db]
engine = "postgres"
url = "postgresql://localhost/mydb"

[limiters.api]
store = "db"
rate_per_second = 10.0
```

## Example: Development Setup

```toml
# rate-sync.toml
[stores.dev]
engine = "memory"

# Relaxed limits for development
[limiters.api]
store = "dev"
rate_per_second = 100.0

[limiters.auth]
store = "dev"
algorithm = "sliding_window"
limit = 100
window_seconds = 60
```

```python
from ratesync import acquire, rate_limited

# Use in your code
@rate_limited("api")
async def fetch_data():
    return await http_client.get(url)

async with acquire("api"):
    response = await http_client.get(url)
```

## See Also

- [Redis Engine](redis.md) - Distributed alternative (recommended)
- [NATS Engine](nats.md) - Distributed via NATS
- [PostgreSQL Engine](postgres.md) - Database-backed alternative
- [Configuration Guide](../configuration.md) - Complete configuration reference
