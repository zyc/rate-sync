# API Reference

Complete API documentation for rate-sync.

## Table of Contents

- [Configuration Functions](#configuration-functions)
- [Usage Functions](#usage-functions)
- [Inspection Functions](#inspection-functions)
- [Initialization Functions](#initialization-functions)
- [Decorators](#decorators)
- [Classes](#classes)
- [Dataclasses](#dataclasses)
- [Exceptions](#exceptions)
- [FastAPI Integration](#fastapi-integration)

---

## Configuration Functions

### configure_store

Configure a coordination store.

```python
def configure_store(store_id: str, strategy: str, **kwargs) -> None
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `store_id` | str | Unique identifier for the store |
| `strategy` | str | Engine type: `"memory"`, `"redis"`, `"nats"`, `"postgres"` |
| `**kwargs` | Any | Engine-specific configuration options |

**Engine-specific kwargs:**

| Engine | Required | Optional |
|--------|----------|----------|
| `memory` | - | - |
| `redis` | `url` | `db`, `password`, `key_prefix`, `pool_min_size`, `pool_max_size`, `timing_margin_ms`, `socket_timeout`, `socket_connect_timeout` |
| `nats` | `url` | `token`, `bucket_name`, `auto_create`, `retry_interval`, `max_retries`, `timing_margin_ms` |
| `postgres` | `url` | `table_name`, `schema_name`, `auto_create`, `pool_min_size`, `pool_max_size`, `timing_margin_ms` |

**Example:**

```python
from ratesync import configure_store

# Memory store
configure_store("dev", strategy="memory")

# Redis store
configure_store(
    "prod",
    strategy="redis",
    url="redis://localhost:6379/0",
    pool_max_size=20,
)

# NATS store
configure_store(
    "nats",
    strategy="nats",
    url="nats://localhost:4222",
    bucket_name="rate_limits",
)

# PostgreSQL store
configure_store(
    "db",
    strategy="postgres",
    url="postgresql://user:pass@localhost/db",
    auto_create=True,
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
) -> None
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | str | Required | Unique identifier for the limiter |
| `store_id` | str | Required | ID of store to use |
| `rate_per_second` | float | None | Max operations per second (token_bucket) |
| `max_concurrent` | int | None | Max simultaneous operations (token_bucket) |
| `timeout` | float | None | Default timeout in seconds |
| `algorithm` | str | `"token_bucket"` | Algorithm: `"token_bucket"` or `"sliding_window"` |
| `limit` | int | None | Max requests in window (sliding_window) |
| `window_seconds` | int | None | Window size in seconds (sliding_window) |

**Raises:**
- `StoreNotFoundError`: If `store_id` doesn't exist
- `ValueError`: If configuration is invalid for the algorithm

**Example:**

```python
from ratesync import configure_limiter

# Token bucket with rate limiting only
configure_limiter("api", store_id="prod", rate_per_second=10.0)

# Token bucket with concurrency limiting only
configure_limiter("db_pool", store_id="prod", max_concurrent=20)

# Token bucket with both (recommended)
configure_limiter(
    "external_api",
    store_id="prod",
    rate_per_second=100.0,
    max_concurrent=50,
    timeout=30.0,
)

# Sliding window for auth protection
configure_limiter(
    "login",
    store_id="redis",
    algorithm="sliding_window",
    limit=5,
    window_seconds=300,
)
```

---

### clone_limiter

Clone configuration from an existing limiter.

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

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source_id` | str | Required | ID of limiter to clone from |
| `new_id` | str | Required | ID for the new limiter |
| `rate_per_second` | float | None | Override rate (uses source's if None) |
| `max_concurrent` | int | None | Override max concurrent |
| `timeout` | float | None | Override timeout |
| `limit` | int | None | Override limit (sliding_window) |
| `window_seconds` | int | None | Override window (sliding_window) |

**Raises:**
- `LimiterNotFoundError`: If `source_id` doesn't exist
- `ValueError`: If `new_id` already exists

**Example:**

```python
from ratesync import clone_limiter

# Clone with same config
clone_limiter("api", "api:user123")

# Clone with modified rate
clone_limiter("api", "api:premium", rate_per_second=100.0)
```

---

### load_config

Load configuration from a TOML file.

```python
def load_config(path: str) -> None
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | str | Path to TOML configuration file |

**Example:**

```python
from ratesync import load_config

load_config("/path/to/custom-config.toml")
```

---

## Usage Functions

### acquire

Acquire a rate limit slot.

```python
def acquire(limiter_id: str, timeout: float | None = None) -> AcquireContext
```

Can be used as both an awaitable and a context manager.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | str | Required | ID of the limiter |
| `timeout` | float | None | Timeout override (uses limiter's default if None) |

**Returns:** `AcquireContext` - Can be awaited or used as async context manager.

**Raises:**
- `LimiterNotFoundError`: If limiter doesn't exist
- `RateLimiterAcquisitionError`: If timeout exceeded

**Example:**

```python
from ratesync import acquire

# As awaitable (simple acquire)
await acquire("api")

# With timeout override
await acquire("api", timeout=5.0)

# As context manager (recommended for concurrency limiting)
async with acquire("api"):
    response = await http_client.get(url)
    # Concurrency slot auto-released on exit
```

---

## Inspection Functions

### get_limiter

Get a rate limiter instance.

```python
def get_limiter(limiter_id: str) -> RateLimiter
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `limiter_id` | str | ID of the limiter |

**Returns:** `RateLimiter` instance (may not be initialized yet).

**Raises:**
- `LimiterNotFoundError`: If limiter doesn't exist

**Example:**

```python
from ratesync import get_limiter

limiter = get_limiter("api")
metrics = limiter.get_metrics()
print(f"Avg wait: {metrics.avg_wait_time_ms}ms")
```

---

### list_stores

List all configured stores.

```python
def list_stores() -> dict[str, dict[str, Any]]
```

**Returns:** Dictionary mapping store IDs to store information.

**Example:**

```python
from ratesync import list_stores

stores = list_stores()
# {'prod': {'engine': 'redis', 'url': 'redis://...', 'initialized': True}}
```

---

### list_limiters

List all configured limiters.

```python
def list_limiters() -> dict[str, dict[str, Any]]
```

**Returns:** Dictionary mapping limiter IDs to limiter information.

**Example:**

```python
from ratesync import list_limiters

limiters = list_limiters()
# {'api': {'backend': 'prod', 'rate_per_second': 10.0, 'initialized': True}}
```

---

## Initialization Functions

### initialize_limiter

Initialize a specific limiter.

```python
async def initialize_limiter(limiter_id: str) -> None
```

**Note:** Initialization is automatic on first use. Call explicitly for fail-fast behavior on startup.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `limiter_id` | str | ID of the limiter to initialize |

**Raises:**
- `LimiterNotFoundError`: If limiter doesn't exist

**Example:**

```python
from ratesync import initialize_limiter

# Initialize on startup for fail-fast
await initialize_limiter("api")
```

---

### initialize_all_limiters

Initialize all configured limiters.

```python
async def initialize_all_limiters() -> None
```

**Example:**

```python
from ratesync import initialize_all_limiters

# Initialize all limiters on startup
await initialize_all_limiters()
```

---

## Decorators

### rate_limited

Decorator for rate limiting async functions.

```python
def rate_limited(
    limiter_id: str | Callable[..., str],
    timeout: float | None = None,
) -> Callable
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | str or Callable | Required | Limiter ID or callable returning ID |
| `timeout` | float | None | Timeout override |

**Example:**

```python
from ratesync import rate_limited

# Static limiter ID
@rate_limited("api")
async def fetch_data():
    return await http_client.get(url)

# With timeout
@rate_limited("api", timeout=5.0)
async def fetch_fast():
    return await http_client.get(url)

# Dynamic limiter ID (for multi-tenant)
@rate_limited(lambda tenant_id: f"tenant-{tenant_id}")
async def process_request(tenant_id: str, data: dict):
    return await process(data)
```

---

## Classes

### RateLimiter

Abstract base class for rate limiter implementations.

```python
class RateLimiter(ABC):
    async def initialize(self) -> None
    async def acquire(self) -> None
    async def try_acquire(self, timeout: float = 0) -> bool
    async def release(self) -> None
    def get_metrics(self) -> RateLimiterMetrics

    @property
    def group_id(self) -> str
    @property
    def rate_per_second(self) -> float | None
    @property
    def max_concurrent(self) -> int | None
    @property
    def is_initialized(self) -> bool
    @property
    def default_timeout(self) -> float | None
    @property
    def fail_closed(self) -> bool

    async def acquire_context(self, timeout: float | None = None) -> AsyncIterator[None]
```

**Methods:**

| Method | Description |
|--------|-------------|
| `initialize()` | Initialize the limiter (idempotent) |
| `acquire()` | Wait for rate limit slot (blocking) |
| `try_acquire(timeout)` | Try to acquire slot within timeout |
| `release()` | Release concurrency slot |
| `get_metrics()` | Get current metrics |
| `acquire_context(timeout)` | Context manager with auto-release |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `group_id` | str | Identifier for distributed coordination |
| `rate_per_second` | float or None | Rate limit (None = unlimited) |
| `max_concurrent` | int or None | Concurrency limit (None = unlimited) |
| `is_initialized` | bool | Whether limiter is initialized |
| `default_timeout` | float or None | Default timeout in seconds |
| `fail_closed` | bool | Behavior on backend failure |

---

### AcquireContext

Helper class for `acquire()` that works as both awaitable and context manager.

```python
class AcquireContext:
    def __init__(self, limiter_id: str, timeout: float | None = None)
    def __await__(self)
    async def __aenter__(self) -> None
    async def __aexit__(self, exc_type, exc_val, exc_tb)
```

---

## Dataclasses

### RateLimiterMetrics

Observability metrics for a rate limiter.

```python
@dataclass
class RateLimiterMetrics:
    total_acquisitions: int = 0
    total_wait_time_ms: float = 0.0
    avg_wait_time_ms: float = 0.0
    max_wait_time_ms: float = 0.0
    cas_failures: int = 0
    timeouts: int = 0
    last_acquisition_at: float | None = None
    current_concurrent: int = 0
    max_concurrent_reached: int = 0
    total_releases: int = 0
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `total_acquisitions` | int | Total slots acquired |
| `total_wait_time_ms` | float | Total accumulated wait time |
| `avg_wait_time_ms` | float | Average wait time per acquisition |
| `max_wait_time_ms` | float | Maximum wait time recorded |
| `cas_failures` | int | CAS failures (NATS only) |
| `timeouts` | int | Number of timeouts |
| `last_acquisition_at` | float | Unix timestamp of last acquisition |
| `current_concurrent` | int | Current in-flight operations |
| `max_concurrent_reached` | int | Times max concurrent was hit |
| `total_releases` | int | Total concurrency slot releases |

---

### LimiterConfig

Configuration for a rate limiter (used internally).

```python
@dataclass
class LimiterConfig:
    store: str
    algorithm: Literal["token_bucket", "sliding_window"] = "token_bucket"
    rate_per_second: float | None = None
    max_concurrent: int | None = None
    limit: int | None = None
    window_seconds: int | None = None
    timeout: float | None = None
    fail_closed: bool = False
```

---

### Engine Config Dataclasses

#### MemoryEngineConfig

```python
@dataclass
class MemoryEngineConfig:
    engine: Literal["memory"] = "memory"
```

#### RedisEngineConfig

```python
@dataclass
class RedisEngineConfig:
    url: str
    engine: Literal["redis"] = "redis"
    db: int = 0
    password: str | None = None
    pool_min_size: int = 2
    pool_max_size: int = 10
    key_prefix: str = "rate_limit"
    timing_margin_ms: float = 10.0
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
```

#### NatsEngineConfig

```python
@dataclass
class NatsEngineConfig:
    url: str
    engine: Literal["nats"] = "nats"
    token: str | None = None
    bucket_name: str = "rate_limits"
    auto_create: bool = False
    retry_interval: float = 0.05
    max_retries: int = 100
    timing_margin_ms: float = 10.0
```

#### PostgresEngineConfig

```python
@dataclass
class PostgresEngineConfig:
    url: str
    engine: Literal["postgres"] = "postgres"
    table_name: str = "rate_limiter_state"
    schema_name: str = "public"
    auto_create: bool = False
    pool_min_size: int = 2
    pool_max_size: int = 10
    timing_margin_ms: float = 10.0
```

---

## Exceptions

### RateLimiterError

Base exception for all rate limiter errors.

```python
class RateLimiterError(Exception):
    pass
```

### RateLimiterAcquisitionError

Raised when unable to acquire a slot within timeout.

```python
class RateLimiterAcquisitionError(RateLimiterError):
    group_id: str | None
    attempts: int | None
```

### LimiterNotFoundError

Raised when a limiter is not configured.

```python
class LimiterNotFoundError(RateLimiterError):
    limiter_id: str
```

### StoreNotFoundError

Raised when a store is not configured.

```python
class StoreNotFoundError(RateLimiterError):
    store_id: str
```

### ConfigValidationError

Raised when configuration validation fails.

```python
class ConfigValidationError(RateLimiterError):
    field: str | None
    expected: str | None
    received: str | None
```

### RateLimiterNotInitializedError

Raised when a limiter is used before initialization.

```python
class RateLimiterNotInitializedError(RateLimiterError):
    name: str | None
```

---

## FastAPI Integration

### RateLimitDependency

FastAPI dependency for rate limiting endpoints.

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
    async def __call__(self, request: Request, response: Response) -> None
```

### RateLimitMiddleware

ASGI middleware for rate limiting.

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

### RateLimitExceededError

Exception raised when rate limit is exceeded.

```python
class RateLimitExceededError(Exception):
    identifier: str
    limiter_id: str
    limit: int
    remaining: int
    reset_at: float | None
    retry_after: float | None
```

### RateLimitResult

Result of a rate limit check.

```python
@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_in: float
```

### Helper Functions

```python
def rate_limit(
    limiter_id: str,
    identifier_extractor: Callable | None = None,
    timeout: float = 0,
    fail_open: bool = True,
) -> RateLimitDependency

def rate_limit_exception_handler(request: Request, exc: RateLimitExceededError) -> Response

def get_client_ip(request: Request, trusted_proxies: list[str] | None = None) -> str

def set_rate_limit_headers(response: Response, result: RateLimitResult) -> None

def get_rate_limit_headers(result: RateLimitResult) -> dict[str, str]
```

---

## See Also

- [Configuration Guide](configuration.md)
- [FastAPI Integration](fastapi-integration.md)
- [Observability](observability.md)
