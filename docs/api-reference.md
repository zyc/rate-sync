# API Reference

Complete API documentation for rate-sync.

## Configuration

### configure_store

Configure a coordination store.

```python
def configure_store(store_id: str, strategy: str, **kwargs) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `store_id` | `str` | Unique identifier |
| `strategy` | `str` | `"memory"`, `"redis"`, or `"postgres"` |
| `**kwargs` | | Engine-specific options (see below) |

**Redis options:**

| Option | Type | Default |
|--------|------|---------|
| `url` | `str` | Required |
| `db` | `int` | `0` |
| `password` | `str \| None` | `None` |
| `key_prefix` | `str` | `"rate_limit"` |
| `pool_min_size` | `int` | `2` |
| `pool_max_size` | `int` | `10` |
| `socket_timeout` | `float` | `5.0` |
| `socket_connect_timeout` | `float` | `5.0` |

**PostgreSQL options:**

| Option | Type | Default |
|--------|------|---------|
| `url` | `str` | Required |
| `table_name` | `str` | `"rate_limiter_state"` |
| `schema_name` | `str` | `"public"` |
| `auto_create` | `bool` | `False` |
| `pool_min_size` | `int` | `2` |
| `pool_max_size` | `int` | `10` |

```python
from ratesync import configure_store

configure_store("dev", strategy="memory")

configure_store(
    "prod",
    strategy="redis",
    url="redis://localhost:6379/0",
    pool_max_size=20,
)
```

---

### configure_limiter

Configure a rate limiter.

```python
def configure_limiter(
    limiter_id: str,
    store_id: str,
    rate_per_second: float | None = None,
    max_concurrent: int | None = None,
    timeout: float | None = None,
    algorithm: str = "token_bucket",
    limit: int | None = None,
    window_seconds: int | None = None,
    fail_closed: bool = False,
) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | `str` | Required | Unique identifier |
| `store_id` | `str` | Required | Store to use |
| `rate_per_second` | `float \| None` | `None` | Max ops/sec (token_bucket) |
| `max_concurrent` | `int \| None` | `None` | Max simultaneous ops (token_bucket) |
| `timeout` | `float \| None` | `None` | Default timeout (seconds) |
| `algorithm` | `str` | `"token_bucket"` | `"token_bucket"` or `"sliding_window"` |
| `limit` | `int \| None` | `None` | Max requests (sliding_window) |
| `window_seconds` | `int \| None` | `None` | Window size (sliding_window) |
| `fail_closed` | `bool` | `False` | Block on backend failure |

**Raises:** `StoreNotFoundError`, `ValueError`

```python
from ratesync import configure_limiter

# Token bucket
configure_limiter("api", store_id="prod", rate_per_second=100.0, max_concurrent=50)

# Sliding window
configure_limiter(
    "login",
    store_id="prod",
    algorithm="sliding_window",
    limit=5,
    window_seconds=300,
)
```

---

### clone_limiter

Clone an existing limiter with optional overrides.

```python
def clone_limiter(
    source_id: str,
    new_id: str,
    *,
    rate_per_second: float | None = None,
    max_concurrent: int | None = None,
    timeout: float | None = None,
    limit: int | None = None,
    window_seconds: int | None = None,
) -> None
```

**Raises:** `LimiterNotFoundError`, `ValueError`

```python
from ratesync import clone_limiter

clone_limiter("api", "api:user123")
clone_limiter("api", "api:premium", rate_per_second=500.0)
```

---

### load_config

Load configuration from TOML file.

```python
def load_config(path: str) -> None
```

```python
from ratesync import load_config

load_config("/path/to/config.toml")
```

---

## Usage

### acquire

Acquire a rate limit slot. Works as both awaitable and context manager.

```python
def acquire(limiter_id: str, timeout: float | None = None) -> AcquireContext
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | `str` | Required | Limiter to use |
| `timeout` | `float \| None` | `None` | Timeout override |

**Raises:** `LimiterNotFoundError`, `RateLimiterAcquisitionError`

```python
from ratesync import acquire

# As awaitable
await acquire("api")

# As context manager (recommended for concurrency limiting)
async with acquire("api"):
    response = await client.get(url)
    # Slot auto-released on exit

# With timeout
await acquire("api", timeout=5.0)
```

---

### rate_limited

Decorator for rate limiting async functions.

```python
def rate_limited(
    limiter_id: str | Callable[..., str],
    timeout: float | None = None,
) -> Callable
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `limiter_id` | `str` or `Callable` | Static ID or callable returning ID |
| `timeout` | `float \| None` | Timeout override |

```python
from ratesync import rate_limited

@rate_limited("api")
async def fetch_data() -> dict:
    return await client.get(url)

@rate_limited("api", timeout=5.0)
async def fetch_fast() -> dict:
    return await client.get(url)

# Dynamic limiter ID
@rate_limited(lambda tenant_id: f"tenant:{tenant_id}")
async def process(tenant_id: str, data: dict) -> dict:
    return await handle(data)
```

---

## Inspection

### get_limiter

Get a limiter instance for metrics or state inspection.

```python
def get_limiter(limiter_id: str) -> RateLimiter
```

**Raises:** `LimiterNotFoundError`

```python
from ratesync import get_limiter

limiter = get_limiter("api")
metrics = limiter.get_metrics()
config = limiter.get_config()
```

---

### list_stores / list_limiters

List all configured stores or limiters.

```python
def list_stores() -> dict[str, dict[str, Any]]
def list_limiters() -> dict[str, dict[str, Any]]
```

```python
from ratesync import list_stores, list_limiters

stores = list_stores()
# {"prod": {"engine": "redis", "initialized": True}}

limiters = list_limiters()
# {"api": {"store": "prod", "rate_per_second": 100.0}}
```

---

## Initialization

### initialize_limiter / initialize_all_limiters

Initialize limiters explicitly. Useful for fail-fast on startup.

```python
async def initialize_limiter(limiter_id: str) -> None
async def initialize_all_limiters() -> None
```

```python
from ratesync import initialize_limiter, initialize_all_limiters

# Single limiter
await initialize_limiter("api")

# All configured limiters
await initialize_all_limiters()
```

Note: Initialization is automatic on first use. Explicit initialization catches connection errors early.

---

## Classes

### RateLimiter

Abstract base class for rate limiter implementations.

**Methods:**

| Method | Description |
|--------|-------------|
| `initialize()` | Initialize (idempotent) |
| `acquire()` | Wait for slot (blocking) |
| `try_acquire(timeout)` | Try to acquire within timeout |
| `release()` | Release concurrency slot |
| `get_metrics()` | Get metrics snapshot |
| `get_config()` | Get configuration |
| `get_state()` | Get current state (if supported) |
| `acquire_context(timeout)` | Context manager with auto-release |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `group_id` | `str` | Coordination identifier |
| `rate_per_second` | `float \| None` | Rate limit |
| `max_concurrent` | `int \| None` | Concurrency limit |
| `is_initialized` | `bool` | Initialization status |
| `default_timeout` | `float \| None` | Default timeout |
| `fail_closed` | `bool` | Backend failure behavior |

---

### RateLimiterMetrics

Observability metrics dataclass.

| Field | Type | Description |
|-------|------|-------------|
| `total_acquisitions` | `int` | Total slots acquired |
| `total_wait_time_ms` | `float` | Accumulated wait time |
| `avg_wait_time_ms` | `float` | Average wait time |
| `max_wait_time_ms` | `float` | Maximum wait time |
| `timeouts` | `int` | Timeout count |
| `current_concurrent` | `int` | Current in-flight |
| `max_concurrent_reached` | `int` | Times limit was hit |
| `cas_failures` | `int` | CAS failures |

```python
metrics = get_limiter("api").get_metrics()
print(f"Avg wait: {metrics.avg_wait_time_ms:.2f}ms")
print(f"Concurrent: {metrics.current_concurrent}")
```

---

### RateLimitResult

Result of a rate limit check.

| Field | Type | Description |
|-------|------|-------------|
| `allowed` | `bool` | Request allowed |
| `limit` | `int` | Max requests |
| `remaining` | `int` | Remaining in window |
| `reset_in` | `float` | Seconds until reset |
| `limiter_id` | `str \| None` | Limiter ID |

**Properties:** `reset_at` (Unix timestamp), `retry_after` (seconds for 429)

---

### LimiterState

Current state snapshot (read-only, no slot consumption).

| Field | Type | Description |
|-------|------|-------------|
| `allowed` | `bool` | Next acquire would succeed |
| `remaining` | `int` | Slots remaining |
| `reset_at` | `int` | Unix timestamp of reset |
| `current_usage` | `int` | Current usage count |

```python
limiter = get_limiter("login")
state = await limiter.get_state()
if state.remaining < 3:
    logger.warning(f"Only {state.remaining} attempts left")
```

---

## Exceptions

| Exception | Description | Attributes |
|-----------|-------------|------------|
| `RateLimiterError` | Base exception | - |
| `RateLimiterAcquisitionError` | Timeout acquiring slot | `group_id`, `attempts` |
| `LimiterNotFoundError` | Limiter not configured | `limiter_id` |
| `StoreNotFoundError` | Store not configured | `store_id` |
| `ConfigValidationError` | Invalid configuration | `field`, `expected`, `received` |
| `RateLimiterNotInitializedError` | Used before init | `name` |

---

## FastAPI Integration

Import from `ratesync.contrib.fastapi`:

### RateLimitDependency

```python
class RateLimitDependency:
    def __init__(
        self,
        limiter_id: str,
        identifier_extractor: Callable | None = None,
        timeout: float = 0,
        fail_open: bool = True,
        trusted_proxies: list[str] | None = None,
    )
```

```python
from fastapi import Depends, FastAPI
from ratesync.contrib.fastapi import RateLimitDependency

app = FastAPI()

@app.get("/api/data")
async def get_data(_: None = Depends(RateLimitDependency("api"))):
    return {"data": "value"}
```

---

### RateLimitMiddleware

```python
class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        limiter_id: str,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        fail_open: bool = True,
        trusted_proxies: list[str] | None = None,
    )
```

```python
from ratesync.contrib.fastapi import RateLimitMiddleware

app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
    exclude_paths=[r"^/health$", r"^/metrics$"],
)
```

---

### RateLimitExceededError

```python
class RateLimitExceededError(Exception):
    identifier: str
    limiter_id: str
    limit: int
    remaining: int
    reset_at: float | None
    retry_after: float | None
```

---

### Helper Functions

```python
# Exception handler
def rate_limit_exception_handler(
    request: Request,
    exc: RateLimitExceededError,
) -> Response

# IP extraction
def get_client_ip(
    request: Request,
    trusted_proxies: list[str] | None = None,
) -> str

# Response headers
def set_rate_limit_headers(response: Response, result: RateLimitResult) -> None
def get_rate_limit_headers(result: RateLimitResult) -> dict[str, str]
```

```python
from ratesync.contrib.fastapi import (
    RateLimitExceededError,
    rate_limit_exception_handler,
)

app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)
```

---

## See Also

- [Configuration Guide](configuration.md)
- [FastAPI Integration](fastapi-integration.md)
- [Observability](observability.md)
