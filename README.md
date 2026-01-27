# rate-sync

[![PyPI version](https://img.shields.io/pypi/v/rate-sync.svg)](https://pypi.org/project/rate-sync/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/github/actions/workflow/status/zyc/rate-sync/test.yml?branch=main&label=tests)](https://github.com/zyc/rate-sync/actions)

Distributed rate limiting for Python with Redis, PostgreSQL, or in-memory backends.

---

## Features

- **Declarative configuration** - Define limits in TOML, use anywhere
- **Multiple backends** - Redis (recommended), PostgreSQL, or memory
- **Dual limiting** - Rate limiting (req/sec) + concurrency limiting (max parallel)
- **Two algorithms** - Token bucket for throughput, sliding window for quotas
- **FastAPI integration** - Dependencies, middleware, exception handlers
- **Async-first** - Built on asyncio with full type hints

## Installation

```bash
pip install rate-sync           # Memory backend only
pip install rate-sync[redis]    # + Redis support
pip install rate-sync[postgres] # + PostgreSQL support
pip install rate-sync[all]      # All backends
```

## Quick Start

**1. Create `rate-sync.toml`:**

```toml
[stores.main]
engine = "memory"  # or "redis", "postgres"

[limiters.api]
store = "main"
rate_per_second = 10.0
```

**2. Use it:**

```python
from ratesync import acquire

await acquire("api")  # Blocks until rate limit allows
```

That's it. Configuration auto-loads on import.

## Usage Patterns

### Context Manager (recommended for concurrency limits)

```python
async with acquire("api"):
    response = await client.get(url)
```

### Decorator

```python
from ratesync import rate_limited

@rate_limited("api")
async def fetch_data():
    return await client.get(url)
```

### Per-User Limits

```python
from ratesync import clone_limiter, acquire

clone_limiter("api", f"api:{user_id}")
await acquire(f"api:{user_id}")
```

## Backends

### Memory (development)

```toml
[stores.local]
engine = "memory"
```

### Redis (production)

```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"
```

### PostgreSQL

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost/mydb"
```

## Algorithms

### Token Bucket (default)

Controls request throughput with optional concurrency limits:

```toml
[limiters.external_api]
store = "redis"
rate_per_second = 100.0  # Max 100 req/sec
max_concurrent = 10      # Max 10 in-flight requests
timeout = 30.0           # Wait up to 30s for a slot
```

### Sliding Window

Counts requests in a time window. Ideal for login protection:

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5              # Max 5 attempts
window_seconds = 300   # Per 5 minutes
```

## FastAPI Integration

```python
from fastapi import Depends, FastAPI
from ratesync.contrib.fastapi import (
    RateLimitDependency,
    RateLimitExceededError,
    rate_limit_exception_handler,
)

app = FastAPI()
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)

@app.get("/api/data")
async def get_data(_: None = Depends(RateLimitDependency("api"))):
    return {"status": "ok"}
```

## Programmatic Configuration

Skip the TOML file if you prefer code:

```python
from ratesync import configure_store, configure_limiter, acquire

configure_store("main", strategy="redis", url="redis://localhost:6379/0")
configure_limiter("api", store_id="main", rate_per_second=100.0)

await acquire("api")
```

## Documentation

| Topic | Link |
|-------|------|
| Configuration Reference | [docs/configuration.md](https://github.com/zyc/rate-sync/blob/main/docs/configuration.md) |
| API Reference | [docs/api-reference.md](https://github.com/zyc/rate-sync/blob/main/docs/api-reference.md) |
| FastAPI Integration | [docs/fastapi-integration.md](https://github.com/zyc/rate-sync/blob/main/docs/fastapi-integration.md) |
| Redis Setup | [docs/setup/redis-setup.md](https://github.com/zyc/rate-sync/blob/main/docs/setup/redis-setup.md) |
| PostgreSQL Setup | [docs/setup/postgres-setup.md](https://github.com/zyc/rate-sync/blob/main/docs/setup/postgres-setup.md) |
| Observability | [docs/observability.md](https://github.com/zyc/rate-sync/blob/main/docs/observability.md) |

### Patterns

- [Authentication Protection](https://github.com/zyc/rate-sync/blob/main/docs/patterns/authentication-protection.md)
- [API Tiering](https://github.com/zyc/rate-sync/blob/main/docs/patterns/api-tiering.md)
- [Background Jobs](https://github.com/zyc/rate-sync/blob/main/docs/patterns/background-jobs.md)
- [Production Deployment](https://github.com/zyc/rate-sync/blob/main/docs/patterns/production-deployment.md)

## Contributing

```bash
git clone https://github.com/zyc/rate-sync.git
cd rate-sync
poetry install
poetry run pytest
```

See [CONTRIBUTING.md](https://github.com/zyc/rate-sync/blob/main/CONTRIBUTING.md) for guidelines.

## License

MIT
