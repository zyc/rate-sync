# v0.1.0 - Initial Public Release

🎉 **First public release of rate-sync!**

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

```bash
pip install rate-sync[all]  # All engines
pip install rate-sync        # Memory only
pip install rate-sync[redis] # Redis engine
```

## Quick Start

**Create `rate-sync.toml`:**

```toml
[stores.local]
engine = "memory"

[limiters.api]
store = "local"
rate_per_second = 10.0
timeout = 30.0
```

**Use immediately:**

```python
from ratesync import acquire

await acquire("api")
```

## Documentation

- [README](https://github.com/rate-sync/python#readme)
- [Configuration Guide](https://github.com/rate-sync/python/blob/main/docs/configuration.md)
- [FastAPI Integration](https://github.com/rate-sync/python/blob/main/docs/fastapi-integration.md)

## PyPI

https://pypi.org/project/rate-sync/

---

**Full Changelog**: https://github.com/rate-sync/python/commits/v0.1.0
