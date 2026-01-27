# Configuration Guide

rate-sync supports TOML file configuration, programmatic setup, or both.

## Configuration Methods

| Method | TOML | Programmatic |
|--------|------|--------------|
| Auto-loads on import | Yes | No |
| Environment variable expansion | Yes | No |
| Runtime modification | No | Yes |
| Best for | Static config | Dynamic config |

## TOML Configuration

### File Location

rate-sync searches for configuration in this order:

1. `RATE_SYNC_CONFIG` environment variable
2. `./rate-sync.toml`
3. `./config/rate-sync.toml`

### Basic Structure

```toml
# Stores define HOW rate limiting is coordinated
[stores.local]
engine = "memory"

# Limiters define WHAT to rate limit
[limiters.api]
store = "local"
rate_per_second = 10.0
```

### Environment Variable Expansion

```toml
[stores.prod]
engine = "redis"
url = "${REDIS_URL}"                        # Required
url = "${REDIS_URL:-redis://localhost:6379}" # With default
```

---

## Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter, load_config

# Option 1: Programmatic
configure_store("local", strategy="memory")
configure_limiter("api", store_id="local", rate_per_second=10.0)

# Option 2: Load TOML
load_config("/path/to/config.toml")

# Option 3: Combine both
load_config("rate-sync.toml")
configure_limiter("dynamic", store_id="prod", rate_per_second=50.0)  # Override/extend
```

---

## Stores Configuration

### Memory Engine

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `engine` | `str` | Required | `"memory"` |

```toml
[stores.dev]
engine = "memory"
```

```python
configure_store("dev", strategy="memory")
```

### Redis Engine

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `engine` | `str` | Required | `"redis"` |
| `url` | `str` | Required | Connection URL |
| `db` | `int` | `0` | Database number (0-15) |
| `password` | `str` | `None` | Auth password |
| `key_prefix` | `str` | `"rate_limit"` | Key namespace |
| `pool_min_size` | `int` | `2` | Min connections |
| `pool_max_size` | `int` | `10` | Max connections |
| `socket_timeout` | `float` | `5.0` | Socket timeout (sec) |
| `socket_connect_timeout` | `float` | `5.0` | Connect timeout (sec) |
| `timing_margin_ms` | `float` | `10.0` | Timing safety margin |

```toml
[stores.prod]
engine = "redis"
url = "${REDIS_URL:-redis://localhost:6379/0}"
pool_max_size = 20
key_prefix = "myapp:ratelimit"
```

```python
configure_store(
    "prod",
    strategy="redis",
    url="redis://localhost:6379/0",
    pool_max_size=20,
)
```

### PostgreSQL Engine

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `engine` | `str` | Required | `"postgres"` |
| `url` | `str` | Required | Connection URL |
| `table_name` | `str` | `"rate_limiter_state"` | State table name |
| `schema_name` | `str` | `"public"` | Schema name |
| `auto_create` | `bool` | `False` | Create table on init |
| `pool_min_size` | `int` | `2` | Min connections |
| `pool_max_size` | `int` | `10` | Max connections |
| `timing_margin_ms` | `float` | `10.0` | Timing safety margin |

```toml
[stores.db]
engine = "postgres"
url = "${DATABASE_URL}"
auto_create = true
pool_max_size = 10
```

```python
configure_store(
    "db",
    strategy="postgres",
    url="postgresql://user:pass@localhost/db",
    auto_create=True,
)
```

---

## Limiters Configuration

### Common Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `store` | `str` | Required | Store ID to use |
| `algorithm` | `str` | `"token_bucket"` | Algorithm type |
| `timeout` | `float` | `None` | Default timeout (sec) |
| `fail_closed` | `bool` | `False` | Block on backend failure |

### Token Bucket Algorithm

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `rate_per_second` | `float` | `None` | Max operations/sec |
| `max_concurrent` | `int` | `None` | Max simultaneous ops |

At least one of `rate_per_second` or `max_concurrent` required.

```toml
# TOML
[limiters.api]
store = "prod"
rate_per_second = 100.0
max_concurrent = 50
timeout = 30.0
```

```python
# Programmatic
configure_limiter(
    "api",
    store_id="prod",
    rate_per_second=100.0,
    max_concurrent=50,
    timeout=30.0,
)
```

### Sliding Window Algorithm

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `limit` | `int` | Required | Max requests in window |
| `window_seconds` | `int` | Required | Window size (sec) |

Requires Redis engine.

```toml
# TOML
[limiters.login]
store = "prod"
algorithm = "sliding_window"
limit = 5
window_seconds = 300
```

```python
# Programmatic
configure_limiter(
    "login",
    store_id="prod",
    algorithm="sliding_window",
    limit=5,
    window_seconds=300,
)
```

---

## Configuration Recipes

### Development Setup

```toml
[stores.dev]
engine = "memory"

[limiters.api]
store = "dev"
rate_per_second = 100.0
```

### Production Setup

```toml
[stores.prod]
engine = "redis"
url = "${REDIS_URL}"
pool_max_size = 20

[limiters.api]
store = "prod"
rate_per_second = 1000.0
max_concurrent = 200
timeout = 30.0

[limiters.partner_api]
store = "prod"
rate_per_second = 10.0
timeout = 60.0
fail_closed = true

[limiters.login]
store = "prod"
algorithm = "sliding_window"
limit = 5
window_seconds = 300
```

### Multi-Tenant Setup

```toml
[stores.shared]
engine = "redis"
url = "${REDIS_URL}"

[limiters.tenant_base]
store = "shared"
rate_per_second = 10.0

[limiters.tenant_premium]
store = "shared"
rate_per_second = 100.0
```

```python
from ratesync import clone_limiter, acquire

# Clone for specific tenant
clone_limiter("tenant_base", f"tenant:{tenant_id}")

# Use
async with acquire(f"tenant:{tenant_id}"):
    response = await process_request()
```

### Environment-Based Switching

```toml
[stores.main]
engine = "${RATE_LIMIT_ENGINE:-memory}"
url = "${RATE_LIMIT_URL:-}"

[limiters.api]
store = "main"
rate_per_second = "${API_RATE_LIMIT:-10.0}"
```

```bash
# Development
export RATE_LIMIT_ENGINE=memory

# Production
export RATE_LIMIT_ENGINE=redis
export RATE_LIMIT_URL=redis://prod-redis:6379/0
export API_RATE_LIMIT=1000.0
```

---

## Algorithm Comparison

| Aspect | Token Bucket | Sliding Window |
|--------|--------------|----------------|
| Controls | Throughput, parallelism | Request count |
| Parameters | `rate_per_second`, `max_concurrent` | `limit`, `window_seconds` |
| Engine support | All | Redis only |
| Best for | API rate limiting | Auth protection |
| Burst handling | Allows controlled bursts | Strict count enforcement |

### When to Use Token Bucket

- API throughput control (req/sec)
- External API integration
- Connection pooling
- General rate limiting

### When to Use Sliding Window

- Login attempt limiting
- Password reset protection
- OTP verification
- Abuse prevention with strict counts

---

## See Also

- [API Reference](api-reference.md)
- [FastAPI Integration](fastapi-integration.md)
- [Engine Documentation](engines/)
