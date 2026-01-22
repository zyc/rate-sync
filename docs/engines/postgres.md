# PostgreSQL Engine

Distributed rate limiting using PostgreSQL for state coordination.

## Overview

The PostgreSQL engine stores rate limit state in a PostgreSQL database table, using optimistic locking with version numbers for safe concurrent updates. This allows multiple processes/containers to coordinate rate limiting through a shared database.

**Highlights:**
- Distributed coordination via PostgreSQL
- Uses existing database infrastructure
- Persistent state (survives restarts)
- Row-level locking for consistency
- Built-in metrics via `RateLimiterMetrics`

**Best for:**
- Applications already using PostgreSQL
- Environments where Redis/NATS isn't available
- Simple deployments with existing database

## Installation

```bash
# Install with PostgreSQL support
pip install rate-sync[postgres]

# Or with all engines
pip install rate-sync[all]
```

## Requirements

- PostgreSQL 12+
- Python package: `asyncpg >= 0.29.0`
- Database permissions (see [Permissions](#permissions))

## Configuration

### Basic TOML

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"

[limiters.api]
store = "db"
rate_per_second = 100.0
timeout = 30.0
```

### Full Options

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"
table_name = "rate_limiter_state"   # Table name
schema_name = "public"               # Schema name
auto_create = false                  # Create table if missing
pool_min_size = 2                    # Min connections
pool_max_size = 10                   # Max connections
timing_margin_ms = 10.0              # Timing safety margin
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `url` | str | Required | PostgreSQL connection URL |
| `table_name` | str | `"rate_limiter_state"` | Table name for state storage |
| `schema_name` | str | `"public"` | PostgreSQL schema name |
| `auto_create` | bool | false | Create table if missing |
| `pool_min_size` | int | 2 | Minimum connection pool size |
| `pool_max_size` | int | 10 | Maximum connection pool size |
| `timing_margin_ms` | float | 10.0 | Timing safety margin |

### Programmatic Configuration

```python
from ratesync import configure_store, configure_limiter

configure_store(
    "db",
    strategy="postgres",
    url="postgresql://user:pass@localhost/mydb",
    auto_create=True,
)

configure_limiter(
    "api",
    store_id="db",
    rate_per_second=100.0,
    timeout=30.0,
)
```

## Auto-Create vs Manual Setup

### auto_create = true (Development)

Convenient for development - table is created automatically:

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost/db"
auto_create = true
```

**Requires:** `CREATE TABLE` permission

### auto_create = false (Production Recommended)

Table must be pre-created by DBA:

```toml
[stores.db]
engine = "postgres"
url = "postgresql://readonly:pass@localhost/db"
auto_create = false
```

**Requires:** Only `SELECT`, `INSERT`, `UPDATE` permissions

### Manual Table Creation

```sql
CREATE TABLE rate_limiter_state (
    group_id VARCHAR(255) PRIMARY KEY,
    last_acquisition_at TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    current_concurrent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rate_limiter_state_updated
    ON rate_limiter_state(updated_at);
```

## Permissions

### Auto-Create Mode

```sql
GRANT CREATE ON SCHEMA public TO myuser;
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myuser;
```

### Manual Setup Mode (Recommended)

```sql
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myuser;
```

## Usage

```python
from ratesync import acquire

# Simple acquire
await acquire("api")

# Context manager (recommended)
async with acquire("api"):
    response = await http_client.get(url)
```

### With Timeout

```python
await acquire("api", timeout=5.0)
```

### Cleanup

```python
from ratesync import get_limiter

limiter = get_limiter("api")
await limiter.disconnect()  # Close connection pool
```

## How It Works

1. **State Storage**: Each limiter has a row with `group_id`, `last_acquisition_at`, and `version`
2. **Optimistic Locking**: Uses version column to detect concurrent updates
3. **Row Locking**: `SELECT ... FOR UPDATE` prevents race conditions
4. **Connection Pooling**: Maintains pool of connections for performance

## Performance

| Metric | Value |
|--------|-------|
| Latency | 1-5ms per acquisition (local network) |
| Throughput | Thousands of req/s per limiter |
| Scalability | Horizontal (add more app instances) |

**Performance Tips:**
- Use connection pooling (enabled by default)
- Ensure proper indexes (created automatically with auto_create)
- Monitor database load
- Consider Redis for higher throughput needs

## Troubleshooting

### "Table does not exist"

**Problem:** `auto_create=false` but table not created

**Solutions:**
1. Create table manually (see SQL above)
2. Set `auto_create=true` (development only)

### "Permission denied"

**Problem:** User lacks necessary permissions

**Solutions:**
1. Grant permissions (see [Permissions](#permissions))
2. Use auto_create mode with privileged user

### "Connection pool exhausted"

**Problem:** Too many concurrent operations

**Solution:** Increase `pool_max_size`:

```toml
[stores.db]
engine = "postgres"
url = "postgresql://..."
pool_max_size = 50
```

### Slow Performance

**Possible causes:**
- Database on slow network
- Missing indexes
- Connection pool too small
- High database load

**Solutions:**
- Use local/fast database
- Verify indexes exist
- Increase pool size
- Monitor database metrics

## Example: Production Setup

```toml
[stores.prod_db]
engine = "postgres"
url = "${DATABASE_URL}"
auto_create = false       # Pre-created by DBA
pool_min_size = 5
pool_max_size = 20
timing_margin_ms = 10.0

[limiters.api]
store = "prod_db"
rate_per_second = 100.0
max_concurrent = 50
timeout = 30.0
```

## See Also

- [Memory Engine](memory.md) - Development alternative
- [Redis Engine](redis.md) - Higher-performance distributed engine
- [NATS Engine](nats.md) - Alternative distributed engine
- [Configuration Guide](../configuration.md) - Complete configuration reference
- [Observability](../observability.md) - Monitoring and alerting
