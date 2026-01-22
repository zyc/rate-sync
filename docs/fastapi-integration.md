# FastAPI Integration

rate-sync provides seamless integration with FastAPI through dependencies, middleware, and exception handlers.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [RateLimitDependency](#ratelimitdependency)
- [RateLimitMiddleware](#ratelimitmiddleware)
- [Custom Identifier Extractors](#custom-identifier-extractors)
- [Exception Handling](#exception-handling)
- [Rate Limit Headers](#rate-limit-headers)
- [Per-User Rate Limiting](#per-user-rate-limiting)
- [Multi-Limiter Patterns](#multi-limiter-patterns)
- [Complete Example](#complete-example)

## Installation

FastAPI integration requires FastAPI/Starlette to be installed:

```bash
pip install fastapi
```

The integration module is available at `ratesync.contrib.fastapi`.

## Quick Start

```python
from fastapi import Depends, FastAPI
from ratesync.contrib.fastapi import (
    RateLimitDependency,
    RateLimitExceededError,
    rate_limit_exception_handler,
)

app = FastAPI()

# Register exception handler for 429 responses
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)

# Apply rate limiting to endpoint
@app.get("/api/data")
async def get_data(_: None = Depends(RateLimitDependency("api"))):
    return {"data": "value"}
```

Configuration in `rate-sync.toml`:

```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"

[limiters.api]
store = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 60
```

## RateLimitDependency

The `RateLimitDependency` class provides endpoint-level rate limiting through FastAPI's dependency injection.

### Basic Usage

```python
from fastapi import Depends
from ratesync.contrib.fastapi import RateLimitDependency

@app.get("/api/resource")
async def get_resource(_: None = Depends(RateLimitDependency("api"))):
    return {"resource": "data"}
```

### Constructor Parameters

```python
RateLimitDependency(
    limiter_id: str,                        # Required: Limiter ID from config
    identifier_extractor: Callable = None,  # Custom identifier extraction
    timeout: float = 0,                     # Timeout for try_acquire (0 = fail-fast)
    fail_open: bool = True,                 # Allow on backend failure
    trusted_proxies: list[str] = None,      # Trusted proxy networks for IP
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | str | Required | ID of limiter in rate-sync.toml |
| `identifier_extractor` | Callable | None | Custom function to extract client ID |
| `timeout` | float | 0 | Timeout in seconds (0 = non-blocking) |
| `fail_open` | bool | True | Allow request if limiter fails |
| `trusted_proxies` | list[str] | None | CIDR networks for X-Forwarded-For |

### Factory Function

For cleaner code, use the `rate_limit` factory function:

```python
from ratesync.contrib.fastapi import rate_limit

@app.get("/api/data")
async def get_data(_: None = Depends(rate_limit("api"))):
    return {"data": "value"}
```

## RateLimitMiddleware

The middleware applies rate limiting at the ASGI level, before requests reach endpoint handlers.

### Basic Usage

```python
from ratesync.contrib.fastapi import RateLimitMiddleware

app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
)
```

### With Path Filtering

```python
app.add_middleware(
    RateLimitMiddleware,
    limiter_id="api",
    include_paths=[r"^/api/.*"],              # Only rate limit /api/* paths
    exclude_paths=[r"^/api/health$", r"^/api/metrics$"],  # Except these
)
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | str | Required | ID of limiter in rate-sync.toml |
| `include_paths` | list[str] | None | Regex patterns to include |
| `exclude_paths` | list[str] | None | Regex patterns to exclude |
| `fail_open` | bool | True | Allow request if limiter fails |
| `trusted_proxies` | list[str] | None | CIDR networks for X-Forwarded-For |

**Note:** Exclusions take precedence over inclusions.

## Custom Identifier Extractors

By default, rate limiting is applied per client IP. You can customize this with identifier extractors.

### Sync Extractor

```python
from starlette.requests import Request
from ratesync.contrib.fastapi import RateLimitDependency, get_client_ip

def get_api_key(request: Request) -> str:
    """Rate limit by API key."""
    return request.headers.get("X-API-Key", get_client_ip(request))

@app.get("/api/data")
async def get_data(
    _: None = Depends(RateLimitDependency("api", identifier_extractor=get_api_key))
):
    return {"data": "value"}
```

### Async Extractor

```python
async def get_user_id(request: Request) -> str:
    """Rate limit by authenticated user ID."""
    # Example: Extract from JWT token
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        user = await verify_jwt(token)
        return str(user.id)
    return get_client_ip(request)

@app.get("/api/user/data")
async def get_user_data(
    _: None = Depends(RateLimitDependency("user_api", identifier_extractor=get_user_id))
):
    return {"data": "value"}
```

### Combined IP + User

```python
def get_ip_and_user(request: Request) -> str:
    """Rate limit by IP and user combination."""
    ip = get_client_ip(request)
    user_id = getattr(request.state, "user_id", "anonymous")
    return f"{ip}:{user_id}"
```

## Exception Handling

### Built-in Exception Handler

The `rate_limit_exception_handler` returns a proper 429 response with rate limit headers:

```python
from ratesync.contrib.fastapi import (
    RateLimitExceededError,
    rate_limit_exception_handler,
)

app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)
```

Response format:

```json
{
    "detail": "Rate limit exceeded",
    "retry_after": 60
}
```

### Custom Exception Handler

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from ratesync.contrib.fastapi import RateLimitExceededError

async def custom_rate_limit_handler(request: Request, exc: RateLimitExceededError):
    return JSONResponse(
        status_code=429,
        content={
            "error": "too_many_requests",
            "message": f"Rate limit exceeded for {exc.limiter_id}",
            "limit": exc.limit,
            "remaining": exc.remaining,
            "retry_after": exc.retry_after,
        },
        headers={
            "Retry-After": str(int(exc.retry_after or 60)),
            "X-RateLimit-Limit": str(exc.limit),
            "X-RateLimit-Remaining": "0",
        },
    )

app.add_exception_handler(RateLimitExceededError, custom_rate_limit_handler)
```

### RateLimitExceededError Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `identifier` | str | Client identifier that was rate limited |
| `limiter_id` | str | Limiter ID that triggered the limit |
| `limit` | int | Maximum requests allowed |
| `remaining` | int | Remaining requests (always 0) |
| `reset_at` | float | Unix timestamp when limit resets |
| `retry_after` | float | Seconds until retry is allowed |

## Rate Limit Headers

rate-sync automatically sets standard rate limit headers on responses:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests allowed |
| `X-RateLimit-Remaining` | Remaining requests in window |
| `X-RateLimit-Reset` | Unix timestamp when limit resets |
| `Retry-After` | Seconds until retry (on 429) |

### Manual Header Setting

```python
from ratesync.contrib.fastapi import set_rate_limit_headers, RateLimitResult

@app.get("/api/data")
async def get_data(response: Response):
    result = RateLimitResult(
        allowed=True,
        limit=100,
        remaining=95,
        reset_in=60.0,
    )
    set_rate_limit_headers(response, result)
    return {"data": "value"}
```

## Per-User Rate Limiting

Use `clone_limiter` to create per-user or per-tenant limiters dynamically.

### With Dependency

```python
from ratesync import clone_limiter, get_limiter
from ratesync.contrib.fastapi import RateLimitDependency, get_client_ip
from ratesync.exceptions import LimiterNotFoundError

async def get_user_limiter(request: Request) -> str:
    """Get or create user-specific limiter ID."""
    user_id = request.state.user_id  # Set by auth middleware
    limiter_id = f"api:{user_id}"

    try:
        get_limiter(limiter_id)
    except LimiterNotFoundError:
        # Clone from base limiter
        clone_limiter("api", limiter_id)

    return user_id  # Return as identifier

@app.get("/api/data")
async def get_data(
    _: None = Depends(RateLimitDependency("api", identifier_extractor=get_user_limiter))
):
    return {"data": "value"}
```

### With Custom Rate Limits per User

```python
async def get_user_with_tier(request: Request) -> str:
    """Rate limit based on user tier."""
    user = request.state.user

    # Map tier to limiter
    tier_limiters = {
        "free": "api_free",
        "pro": "api_pro",
        "enterprise": "api_enterprise",
    }

    return tier_limiters.get(user.tier, "api_free")
```

Configuration:

```toml
[limiters.api_free]
store = "redis"
rate_per_second = 1.0

[limiters.api_pro]
store = "redis"
rate_per_second = 10.0

[limiters.api_enterprise]
store = "redis"
rate_per_second = 100.0
```

## Multi-Limiter Patterns

### Multiple Limiters per Endpoint

```python
@app.post("/api/payment")
async def process_payment(
    _global: None = Depends(RateLimitDependency("global")),
    _payment: None = Depends(RateLimitDependency("payments")),
):
    """Apply both global and payment-specific rate limits."""
    return {"status": "processed"}
```

### Different Limits for Different Endpoints

```python
# Strict limit for sensitive operations
@app.post("/auth/login")
async def login(
    _: None = Depends(RateLimitDependency("login"))
):
    return {"token": "..."}

# Relaxed limit for read operations
@app.get("/api/data")
async def get_data(
    _: None = Depends(RateLimitDependency("api"))
):
    return {"data": "..."}
```

Configuration:

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 300

[limiters.api]
store = "redis"
rate_per_second = 100.0
```

### Global + Path-Specific

```python
# Global middleware for all requests
app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
    exclude_paths=[r"^/health$"],
)

# Additional strict limit for specific endpoint
@app.post("/api/expensive-operation")
async def expensive_operation(
    _: None = Depends(RateLimitDependency("expensive"))
):
    return {"result": "..."}
```

## Complete Example

```python
from fastapi import Depends, FastAPI, Request
from ratesync.contrib.fastapi import (
    RateLimitDependency,
    RateLimitMiddleware,
    RateLimitExceededError,
    rate_limit_exception_handler,
    get_client_ip,
)

app = FastAPI()

# Exception handler
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)

# Global rate limiting (all endpoints except health/metrics)
app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
    exclude_paths=[r"^/health$", r"^/metrics$"],
)

# Custom identifier extractor
async def get_api_key_or_ip(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    return f"ip:{get_client_ip(request)}"

# Public endpoints with IP-based limiting
@app.get("/api/public")
async def public_data(_: None = Depends(RateLimitDependency("public"))):
    return {"data": "public"}

# Authenticated endpoints with API key limiting
@app.get("/api/private")
async def private_data(
    _: None = Depends(RateLimitDependency(
        "private",
        identifier_extractor=get_api_key_or_ip
    ))
):
    return {"data": "private"}

# Auth endpoints with strict limits
@app.post("/auth/login")
async def login(_: None = Depends(RateLimitDependency("login"))):
    return {"token": "..."}

# Health check (excluded from rate limiting)
@app.get("/health")
async def health():
    return {"status": "ok"}
```

Configuration (`rate-sync.toml`):

```toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL:-redis://localhost:6379/0}"

[limiters.global]
store = "redis"
rate_per_second = 1000.0
max_concurrent = 500

[limiters.public]
store = "redis"
rate_per_second = 10.0

[limiters.private]
store = "redis"
rate_per_second = 100.0

[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 300
```

## See Also

- [Configuration Guide](configuration.md) - Complete configuration reference
- [API Reference](api-reference.md) - Full API documentation
- [Observability](observability.md) - Metrics and monitoring
