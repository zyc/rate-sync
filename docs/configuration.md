# Configuration Guide

This guide covers all configuration options for rate-sync, including TOML file configuration, programmatic setup, and environment variable expansion.

## Table of Contents

- [Overview](#overview)
- [TOML Configuration](#toml-configuration)
- [Programmatic Configuration](#programmatic-configuration)
- [Environment Variables](#environment-variables)
- [Stores Configuration](#stores-configuration)
- [Limiters Configuration](#limiters-configuration)
- [Algorithms](#algorithms)
- [Examples](#examples)

## Overview

rate-sync uses a two-level architecture:

1. **Stores**: Define the coordination mechanism (Memory, Redis, NATS, PostgreSQL)
2. **Limiters**: Define the rate limiting rules and reference a store

This separation allows multiple limiters to share the same store connection.

## TOML Configuration

### File Location

rate-sync automatically searches for configuration in this order:

1. `RATE_SYNC_CONFIG` environment variable
2. `./rate-sync.toml` (current directory)
3. `./config/rate-sync.toml` (config subdirectory)

### Basic Structure

```toml
# Stores define HOW rate limiting is coordinated
[stores.local]
engine = "memory"

# Limiters define WHAT to rate limit
[limiters.api]
store = "local"
rate_per_second = 10.0
timeout = 30.0
```

### Complete Example

```toml
# =============================================================================
# STORES - Coordination Mechanisms
# =============================================================================

# Development store (in-memory, single process)
[stores.dev]
engine = "memory"

# Production store (Redis, distributed)
[stores.prod]
engine = "redis"
url = "${REDIS_URL:-redis://localhost:6379/0}"
password = "${REDIS_PASSWORD:-}"
key_prefix = "ratelimit"
pool_min_size = 2
pool_max_size = 10

# =============================================================================
# LIMITERS - Rate Limiting Rules
# =============================================================================

# API rate limiting (token bucket)
[limiters.api]
store = "prod"
algorithm = "token_bucket"
rate_per_second = 100.0
max_concurrent = 50
timeout = 30.0

# Authentication protection (sliding window)
[limiters.login]
store = "prod"
algorithm = "sliding_window"
limit = 5
window_seconds = 300

# External API (strict rate limit)
[limiters.external_api]
store = "prod"
rate_per_second = 1.0
timeout = 60.0
fail_closed = true
```

## Programmatic Configuration

### Basic Setup

```python
from ratesync import configure_store, configure_limiter, acquire

# Configure store
configure_store("local", strategy="memory")

# Configure limiter
configure_limiter(
    "api",
    store_id="local",
    rate_per_second=10.0,
    timeout=30.0
)

# Use
await acquire("api")
```

### Loading Custom Config File

```python
from ratesync import load_config

# Load from custom path
load_config("/path/to/custom-config.toml")
```

### Mixed Configuration

You can combine TOML and programmatic configuration:

```python
from ratesync import load_config, configure_limiter

# Load base config from TOML
load_config("rate-sync.toml")

# Add/override limiters programmatically
configure_limiter("dynamic_api", store_id="prod", rate_per_second=50.0)
```

## Environment Variables

### Expansion Syntax

rate-sync supports environment variable expansion in TOML files:

```toml
# Basic expansion
url = "${REDIS_URL}"

# With default value
url = "${REDIS_URL:-redis://localhost:6379/0}"

# In section names (dynamic limiter IDs)
[limiters."${LIMITER_ID:-default}"]
store = "prod"
rate_per_second = "${RATE_LIMIT:-10.0}"
```

### Built-in Environment Variables

| Variable | Description |
|----------|-------------|
| `RATE_SYNC_CONFIG` | Custom config file path |

## Stores Configuration

### Memory Engine

```toml
[stores.local]
engine = "memory"
```

No additional options. Perfect for development and single-process apps.

### Redis Engine

```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"        # Required
db = 0                                   # Database number (0-15)
password = ""                            # Optional password
key_prefix = "rate_limit"                # Key prefix for namespacing
pool_min_size = 2                        # Min connections
pool_max_size = 10                       # Max connections
timing_margin_ms = 10.0                  # Timing safety margin
socket_timeout = 5.0                     # Socket timeout (seconds)
socket_connect_timeout = 5.0             # Connection timeout (seconds)
```

### NATS Engine

```toml
[stores.nats]
engine = "nats"
url = "nats://localhost:4222"            # Required
token = ""                               # Optional auth token
bucket_name = "rate_limits"              # KV bucket name
auto_create = false                      # Create bucket if missing
retry_interval = 0.05                    # CAS retry interval
max_retries = 100                        # Max CAS retries
timing_margin_ms = 10.0                  # Timing safety margin
```

### PostgreSQL Engine

```toml
[stores.postgres]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/db"  # Required
table_name = "rate_limiter_state"        # Table name
schema_name = "public"                   # Schema name
auto_create = false                      # Create table if missing
pool_min_size = 2                        # Min connections
pool_max_size = 10                       # Max connections
timing_margin_ms = 10.0                  # Timing safety margin
```

## Limiters Configuration

### Common Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `store` | string | Required | Store ID to use |
| `algorithm` | string | `"token_bucket"` | Algorithm type |
| `timeout` | float | None | Default timeout in seconds |
| `fail_closed` | bool | false | Block on backend failure |

### Token Bucket Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `rate_per_second` | float | None | Max operations per second |
| `max_concurrent` | int | None | Max simultaneous operations |

At least one of `rate_per_second` or `max_concurrent` must be specified.

### Sliding Window Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `limit` | int | Required | Max requests in window |
| `window_seconds` | int | Required | Window size in seconds |

**Note:** Sliding window requires the Redis engine.

## Algorithms

### Token Bucket

The token bucket algorithm controls throughput by enforcing a minimum interval between operations.

```toml
[limiters.api]
store = "redis"
algorithm = "token_bucket"    # Default, can be omitted
rate_per_second = 10.0        # 10 requests per second max
```

**Use cases:**
- API rate limiting
- External API integration
- General throughput control

**Combined with concurrency:**

```toml
[limiters.production_api]
store = "redis"
rate_per_second = 100.0       # Max throughput
max_concurrent = 50           # Max parallelism
```

### Sliding Window

The sliding window algorithm counts requests within a time window. Better suited for authentication protection where you want to limit attempts over a longer period.

```toml
[limiters.login]
store = "redis"
algorithm = "sliding_window"
limit = 5                     # 5 attempts
window_seconds = 300          # Per 5 minutes
```

**Use cases:**
- Login attempt limiting
- Password reset protection
- OTP verification limiting
- Abuse prevention

## Examples

### Development Configuration

```toml
# Simple development setup
[stores.dev]
engine = "memory"

[limiters.api]
store = "dev"
rate_per_second = 100.0
```

### Production Configuration

```toml
# Production with Redis
[stores.prod]
engine = "redis"
url = "${REDIS_URL}"
pool_max_size = 20

# Main API limiter
[limiters.api]
store = "prod"
rate_per_second = 1000.0
max_concurrent = 200
timeout = 30.0

# External API with strict limits
[limiters.partner_api]
store = "prod"
rate_per_second = 10.0
timeout = 60.0
fail_closed = true

# Auth protection
[limiters.login]
store = "prod"
algorithm = "sliding_window"
limit = 5
window_seconds = 300
```

### Multi-Tenant Configuration

```toml
[stores.shared]
engine = "redis"
url = "${REDIS_URL}"

# Base limiter for cloning
[limiters.tenant_base]
store = "shared"
rate_per_second = 10.0

# Premium tier
[limiters.tenant_premium]
store = "shared"
rate_per_second = 100.0
```

```python
from ratesync import clone_limiter, acquire

# Clone for specific tenant
clone_limiter("tenant_base", f"tenant:{tenant_id}")

# Use tenant-specific limiter
async with acquire(f"tenant:{tenant_id}"):
    response = await process_request()
```

### Environment-Based Configuration

```toml
# Switch store based on environment
[stores.main]
engine = "${RATE_LIMIT_ENGINE:-memory}"
url = "${RATE_LIMIT_URL:-}"

[limiters.api]
store = "main"
rate_per_second = "${API_RATE_LIMIT:-10.0}"
timeout = "${API_TIMEOUT:-30.0}"
```

```bash
# Development
export RATE_LIMIT_ENGINE=memory
export API_RATE_LIMIT=100.0

# Production
export RATE_LIMIT_ENGINE=redis
export RATE_LIMIT_URL=redis://prod-redis:6379/0
export API_RATE_LIMIT=1000.0
```

## See Also

- [API Reference](api-reference.md) - Complete function signatures
- [Engines](engines/) - Detailed engine documentation
- [FastAPI Integration](fastapi-integration.md) - Web framework integration
