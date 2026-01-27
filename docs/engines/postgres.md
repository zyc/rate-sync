# PostgreSQL Engine

Distributed rate limiting using PostgreSQL with optimistic locking. Use when you already have PostgreSQL infrastructure and don't want to add Redis.

## Compatible Versions

| Version | Status | Notes |
|---------|--------|-------|
| PostgreSQL 16 | ✅ Recommended | Current stable, all features |
| PostgreSQL 15 | ✅ Supported | All features |
| PostgreSQL 14 | ✅ Supported | All features |
| PostgreSQL 13 | ⚠️ Untested | May work, not in CI |
| PostgreSQL 12 | ⚠️ Untested | Minimum supported (EOL 2024) |

Tested in CI: PostgreSQL 14, 15, 16

## Installation

```bash
pip install rate-sync[postgres]
```

## Quick Start

```toml
# rate-sync.toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"
auto_create = true

[limiters.api]
store = "db"
rate_per_second = 100.0
```

```python
from ratesync import acquire

async with acquire("api"):
    response = await client.get(url)
```

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | str | Required | Must be `"postgres"` |
| `url` | str | Required | PostgreSQL connection URL |
| `table_name` | str | `"rate_limiter_state"` | Table name for state storage |
| `schema_name` | str | `"public"` | PostgreSQL schema name |
| `auto_create` | bool | `false` | Create table automatically |
| `pool_min_size` | int | `2` | Minimum connection pool size |
| `pool_max_size` | int | `10` | Maximum connection pool size |
| `timing_margin_ms` | float | `10.0` | Timing safety margin (ms) |

## Advanced Usage

### Manual Table Setup (Production)

For production, create the table manually and set `auto_create = false`:

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

-- Grant minimal permissions
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
```

```toml
[stores.db]
engine = "postgres"
url = "postgresql://myapp_user:pass@localhost:5432/mydb"
auto_create = false
```

### Custom Schema

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"
schema_name = "rate_limiting"
auto_create = false
```

### Production Configuration

```toml
[stores.prod_db]
engine = "postgres"
url = "${DATABASE_URL}"
auto_create = false
pool_min_size = 5
pool_max_size = 20

[limiters.api]
store = "prod_db"
rate_per_second = 100.0
max_concurrent = 50
timeout = 30.0
```

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
)
```

### Cleanup Old Records

```sql
-- Delete records older than 7 days
DELETE FROM rate_limiter_state
WHERE last_acquisition_at < NOW() - INTERVAL '7 days';
```

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Table does not exist | `auto_create=false` without manual setup | Create table manually or set `auto_create=true` |
| Permission denied | Missing grants | Grant `SELECT, INSERT, UPDATE` to user |
| Connection pool exhausted | Too many concurrent operations | Increase `pool_max_size` |
| Slow performance | Missing indexes or network latency | Verify indexes exist; use local database |
| Optimistic lock failures | High contention | Expected under load; retries are automatic |

## Permissions

**Development** (`auto_create = true`):
```sql
GRANT CREATE ON SCHEMA public TO myapp_user;
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
```

**Production** (`auto_create = false`):
```sql
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
```

## See Also

- [PostgreSQL Setup Guide](../setup/postgres-setup.md) - Detailed setup instructions
- [Memory Engine](memory.md) - Development alternative
- [Redis Engine](redis.md) - Higher-performance alternative
