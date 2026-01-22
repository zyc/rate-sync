# rate-sync

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![PyPI version](https://img.shields.io/pypi/v/rate-sync.svg)](https://pypi.org/project/rate-sync/)

**Zero-configuration distributed rate limiter for Python**

rate-sync provides the simplest possible API for rate limiting in Python applications. Just create a `rate-sync.toml` file and start using it - no initialization code required.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Algorithms](#algorithms)
- [Engines](#engines)
- [FastAPI Integration](#fastapi-integration)
- [Usage Examples](#usage-examples)
- [Observability](#observability)
- [API Reference](#api-reference)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Zero Configuration**: Auto-detects and loads `rate-sync.toml` on import
- **Lazy Initialization**: Limiters auto-initialize on first use
- **Dual Limiting Strategies**: Rate limiting (req/sec) AND concurrency limiting (max simultaneous)
- **Multiple Algorithms**: Token bucket for throughput, sliding window for auth protection
- **Multiple Engines**: Memory, Redis, NATS, PostgreSQL
- **FastAPI Integration**: Dependencies, middleware, and exception handlers
- **Async-First**: Built for modern Python with full asyncio support
- **Type Safe**: Complete type hints throughout
- **Observable**: Built-in metrics for monitoring

## Installation

### Quick Start (Recommended)

```bash
pip install rate-sync[all]
```

Installs all engines: **Memory**, **Redis**, **NATS**, and **PostgreSQL**.

### Minimal (Memory engine only)

```bash
pip install rate-sync
```

Perfect for development, testing, or single-process applications.

### Specific Engines

```bash
# Redis engine (recommended for production)
pip install rate-sync[redis]

# NATS engine
pip install rate-sync[nats]

# PostgreSQL engine
pip install rate-sync[postgres]

# Multiple engines
pip install rate-sync[redis,nats]
```

### With Poetry

```bash
poetry add rate-sync[all]      # All engines
poetry add rate-sync           # Memory only
poetry add rate-sync[redis]    # Redis engine
```

**Engine Dependencies:**

| Installation | Memory | Redis | NATS | PostgreSQL |
|--------------|--------|-------|------|------------|
| `rate-sync` | Yes | - | - | - |
| `rate-sync[redis]` | Yes | Yes | - | - |
| `rate-sync[nats]` | Yes | - | Yes | - |
| `rate-sync[postgres]` | Yes | - | - | Yes |
| `rate-sync[all]` | Yes | Yes | Yes | Yes |

## Quick Start

### Option 1: Zero-Config with TOML (Recommended)

**Step 1: Create `rate-sync.toml`**

```toml
[stores.local]
engine = "memory"

[limiters.api]
store = "local"
rate_per_second = 10.0
timeout = 30.0
```

**Step 2: Use immediately**

```python
from ratesync import acquire, rate_limited

# Config auto-loaded on import, limiters auto-initialize on first use

# Option A: Await acquire
await acquire("api")

# Option B: Decorator
@rate_limited("api")
async def fetch_data():
    return await http_client.get(url)

# Option C: Context manager (recommended for concurrency limiting)
async with acquire("api"):
    response = await http_client.get(url)
```

### Option 2: Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter, acquire

# Configure store (defines HOW rate limiting works)
configure_store("local", strategy="memory")

# Configure limiter (defines WHAT to rate limit)
configure_limiter("api", store_id="local", rate_per_second=10.0, timeout=30.0)

# Use immediately
await acquire("api")
```

## Configuration

rate-sync uses a two-level architecture separating **stores** (coordination mechanism) from **limiters** (rate limit rules).

### Stores

Stores define **how** rate limiting is coordinated:

| Engine | Use Case | Coordination |
|--------|----------|--------------|
| `memory` | Development, single-process | Local only |
| `redis` | Production (recommended) | Distributed |
| `nats` | Production with NATS infrastructure | Distributed |
| `postgres` | Production with existing PostgreSQL | Distributed |

### Limiters

Limiters define **what** to rate limit:

```toml
[limiters.api]
store = "redis"              # Which store to use
algorithm = "token_bucket"   # Algorithm (default)
rate_per_second = 100.0      # Max throughput
max_concurrent = 50          # Max parallelism (optional)
timeout = 30.0               # Wait timeout in seconds
```

See [docs/configuration.md](docs/configuration.md) for complete configuration reference.

## Algorithms

### Token Bucket (Default)

Controls throughput using `rate_per_second` and optionally `max_concurrent`:

```toml
[limiters.external_api]
store = "redis"
rate_per_second = 10.0    # Max 10 requests per second
max_concurrent = 5        # Max 5 simultaneous requests
```

### Sliding Window

Counts requests in a time window. Ideal for auth protection:

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5                 # Max 5 attempts
window_seconds = 300      # Per 5 minutes
```

**Note:** Sliding window requires the Redis engine.

## Engines

### Memory

Zero dependencies, perfect for development:

```toml
[stores.local]
engine = "memory"
```

[Full documentation](docs/engines/memory.md)

### Redis (Recommended for Production)

High-performance distributed coordination:

```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"
```

[Full documentation](docs/engines/redis.md)

### NATS

Distributed coordination via JetStream KV:

```toml
[stores.nats]
engine = "nats"
url = "nats://localhost:4222"
```

[Full documentation](docs/engines/nats.md)

### PostgreSQL

Database-backed coordination:

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost/db"
```

[Full documentation](docs/engines/postgres.md)

## FastAPI Integration

rate-sync provides seamless FastAPI integration via dependencies and middleware.

### Dependency-Based (Recommended)

```python
from fastapi import Depends, FastAPI
from ratesync.contrib.fastapi import (
    RateLimitDependency,
    RateLimitExceededError,
    rate_limit_exception_handler,
)

app = FastAPI()

# Register exception handler
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)

@app.get("/api/data")
async def get_data(_: None = Depends(RateLimitDependency("api"))):
    return {"data": "value"}
```

### Middleware-Based

```python
from ratesync.contrib.fastapi import RateLimitMiddleware

app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
    exclude_paths=[r"^/health$", r"^/metrics$"],
)
```

See [docs/fastapi-integration.md](docs/fastapi-integration.md) for complete guide.

## Usage Examples

### Basic Rate Limiting

```python
from ratesync import acquire

# Wait for rate limit slot
await acquire("api")
response = await http_client.get(url)
```

### Concurrency Limiting

```python
from ratesync import acquire

# Limit concurrent operations (auto-releases on exit)
async with acquire("db_pool"):
    result = await db.execute(query)
```

### Combined Rate + Concurrency

```toml
[limiters.production_api]
store = "redis"
rate_per_second = 100.0   # Max 100 req/sec
max_concurrent = 50       # Max 50 in-flight
timeout = 30.0
```

```python
async with acquire("production_api"):
    # Respects both limits, auto-releases concurrency slot
    result = await external_api.call()
```

### Decorator Pattern

```python
from ratesync import rate_limited

@rate_limited("payments")
async def process_payment(payment_id: str):
    return await payment_service.process(payment_id)
```

### Per-User Rate Limiting

```python
from ratesync import clone_limiter, acquire

# Clone base limiter for specific user
clone_limiter("api", f"api:{user_id}")

async with acquire(f"api:{user_id}"):
    response = await http_client.get(url)
```

### Multi-Tenant Application

```python
from ratesync import rate_limited

@rate_limited(lambda tenant_id: f"tenant-{tenant_id}")
async def process_request(tenant_id: str, data: dict):
    return await process(data)
```

## Observability

### Built-in Metrics

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

### Prometheus Integration

```python
from prometheus_client import Gauge
from ratesync import get_limiter, list_limiters

WAIT_GAUGE = Gauge("ratesync_wait_ms", "Average wait per limiter", ["limiter"])

def export_metrics():
    for limiter_id in list_limiters().keys():
        metrics = get_limiter(limiter_id).get_metrics()
        WAIT_GAUGE.labels(limiter=limiter_id).set(metrics.avg_wait_time_ms)
```

See [docs/observability.md](docs/observability.md) for alerting strategies.

## API Reference

### Configuration

| Function | Description |
|----------|-------------|
| `configure_store(id, strategy, **kwargs)` | Configure a store |
| `configure_limiter(id, store_id, ...)` | Configure a limiter |
| `load_config(path)` | Load custom config file |
| `clone_limiter(source_id, new_id, ...)` | Clone limiter configuration |

### Usage

| Function | Description |
|----------|-------------|
| `acquire(limiter_id, timeout=None)` | Acquire rate limit slot |
| `rate_limited(limiter_id, timeout=None)` | Decorator for rate limiting |

### Inspection

| Function | Description |
|----------|-------------|
| `list_stores()` | List configured stores |
| `list_limiters()` | List configured limiters |
| `get_limiter(limiter_id)` | Get limiter instance |

### Initialization

| Function | Description |
|----------|-------------|
| `initialize_limiter(limiter_id)` | Initialize specific limiter |
| `initialize_all_limiters()` | Initialize all limiters |

See [docs/api-reference.md](docs/api-reference.md) for complete API documentation.

## Development

### Setup

```bash
git clone https://github.com/zyc/rate-sync.git
cd rate-sync

poetry install
poetry run pytest
```

### Running Tests

```bash
# All tests
poetry run pytest

# With coverage
poetry run pytest --cov=ratesync

# Specific engine tests
poetry run pytest -m redis
poetry run pytest -m nats
poetry run pytest -m postgres
```

### Integration Tests

```bash
# Redis integration
export REDIS_URL="redis://localhost:6379/0"
poetry run pytest -m "integration and redis"

# NATS integration
export NATS_INTEGRATION_URL="nats://localhost:4222"
poetry run pytest -m "integration and nats"

# PostgreSQL integration
export POSTGRES_INTEGRATION_URL="postgresql://user:pass@localhost/db"
poetry run pytest -m "integration and postgres"
```

### Code Quality

```bash
poetry run ruff check src/ratesync
poetry run black src/ratesync tests
poetry run pylint src/ratesync
```

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Areas of interest:**
- New engine implementations (etcd, Consul)
- Performance optimizations
- Documentation improvements
- Bug fixes

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Support

- **Issues**: [GitHub Issues](https://github.com/zyc/rate-sync/issues)
- **Discussions**: [GitHub Discussions](https://github.com/zyc/rate-sync/discussions)

---

Built with asyncio for modern Python applications.
