# FastAPI Integration

rate-sync provides seamless FastAPI integration via dependencies, middleware, and exception handlers.

## Complete Working Example

```python
from fastapi import Depends, FastAPI
from ratesync.contrib.fastapi import (
    RateLimitDependency,
    RateLimitMiddleware,
    RateLimitExceededError,
    rate_limit_exception_handler,
    get_client_ip,
)

app = FastAPI()

# 1. Register exception handler
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)

# 2. Global middleware (optional)
app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
    exclude_paths=[r"^/health$", r"^/metrics$"],
)

# 3. Per-endpoint rate limiting
@app.get("/api/data")
async def get_data(_: None = Depends(RateLimitDependency("api"))):
    return {"data": "value"}

@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Configuration (`rate-sync.toml`):**

```toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL:-redis://localhost:6379/0}"

[limiters.global]
store = "redis"
rate_per_second = 1000.0

[limiters.api]
store = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 60
```

## Dependency Injection Pattern

Use `RateLimitDependency` for endpoint-level control.

```python
from ratesync.contrib.fastapi import RateLimitDependency

@app.get("/api/resource")
async def get_resource(_: None = Depends(RateLimitDependency("api"))):
    return {"resource": "data"}
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limiter_id` | str | Required | Limiter ID from config |
| `identifier_extractor` | Callable | None | Custom client ID extraction |
| `timeout` | float | 0 | Seconds to wait (0 = fail-fast) |
| `fail_open` | bool | True | Allow on backend failure |
| `trusted_proxies` | list[str] | None | CIDR networks for X-Forwarded-For |

## Middleware Pattern

Use `RateLimitMiddleware` for global rate limiting.

```python
app.add_middleware(
    RateLimitMiddleware,
    limiter_id="global",
    include_paths=[r"^/api/.*"],
    exclude_paths=[r"^/api/health$"],
)
```

**Note:** Exclusions take precedence over inclusions.

## Exception Handling

**Built-in handler** returns 429 with standard headers:

```python
app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)
```

**Custom handler:**

```python
async def custom_handler(request: Request, exc: RateLimitExceededError):
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited", "retry_after": exc.retry_after},
        headers={"Retry-After": str(int(exc.retry_after or 60))},
    )

app.add_exception_handler(RateLimitExceededError, custom_handler)
```

## Common Recipes

### Per-User Rate Limiting

```python
async def get_user_id(request: Request) -> str:
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

### Per-Endpoint with Different Limits

```python
# Strict for auth
@app.post("/auth/login")
async def login(_: None = Depends(RateLimitDependency("login"))):
    return {"token": "..."}

# Relaxed for reads
@app.get("/api/data")
async def get_data(_: None = Depends(RateLimitDependency("api"))):
    return {"data": "..."}
```

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

### Multiple Limiters per Endpoint

```python
@app.post("/api/payment")
async def process_payment(
    _global: None = Depends(RateLimitDependency("global")),
    _payment: None = Depends(RateLimitDependency("payments")),
):
    return {"status": "processed"}
```

### API Key Rate Limiting

```python
def get_api_key_or_ip(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    return f"key:{api_key}" if api_key else f"ip:{get_client_ip(request)}"

@app.get("/api/private")
async def private_data(
    _: None = Depends(RateLimitDependency("private", identifier_extractor=get_api_key_or_ip))
):
    return {"data": "private"}
```

## Rate Limit Headers

Responses include standard headers:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests allowed |
| `X-RateLimit-Remaining` | Remaining requests |
| `X-RateLimit-Reset` | Unix timestamp when limit resets |
| `Retry-After` | Seconds until retry (on 429) |

## See Also

- [Configuration Guide](configuration.md)
- [Observability](observability.md)
