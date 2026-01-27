# PostgreSQL Setup

## Docker (Quickstart)

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres --name postgres postgres:16-alpine
```

## Local Installation

**macOS:**
```bash
brew install postgresql@16
brew services start postgresql@16
```

**Ubuntu/Debian:**
```bash
sudo apt update && sudo apt install postgresql
sudo systemctl start postgresql
```

## Verify Connection

```bash
psql -h localhost -U postgres -c "SELECT 1"
```

Or with connection string:
```bash
psql "postgresql://postgres:postgres@localhost:5432/postgres" -c "SELECT 1"
```

## Docker Compose (Production-like)

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: myapp
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: myapp
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U myapp"]
      interval: 10s
      timeout: 3s
      retries: 3

volumes:
  postgres_data:
```

## Table Setup

### Option 1: Auto-create (Development)

```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"
auto_create = true
```

### Option 2: Manual Setup (Production)

```sql
-- Create table
CREATE TABLE rate_limiter_state (
    group_id VARCHAR(255) PRIMARY KEY,
    last_acquisition_at TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    current_concurrent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create index
CREATE INDEX idx_rate_limiter_state_updated
    ON rate_limiter_state(updated_at);

-- Grant permissions (minimal)
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
```

```toml
[stores.db]
engine = "postgres"
url = "postgresql://myapp_user:pass@localhost:5432/mydb"
auto_create = false
```

## Configuration Examples

**Basic:**
```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"
auto_create = true
```

**Production:**
```toml
[stores.db]
engine = "postgres"
url = "${DATABASE_URL}"
auto_create = false
pool_min_size = 5
pool_max_size = 20
```

**Custom schema:**
```toml
[stores.db]
engine = "postgres"
url = "postgresql://user:pass@localhost:5432/mydb"
schema_name = "rate_limiting"
auto_create = false
```

## Common Issues

| Issue | Solution |
|-------|----------|
| `Connection refused` | Start PostgreSQL: `brew services start postgresql` or `docker start postgres` |
| `FATAL: password authentication failed` | Check username/password in connection URL |
| `relation "rate_limiter_state" does not exist` | Create table manually or set `auto_create = true` |
| `permission denied for table` | Grant permissions: `GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO user` |
| `too many connections` | Increase `max_connections` in PostgreSQL or reduce `pool_max_size` |

## Maintenance

**Cleanup old records:**
```sql
DELETE FROM rate_limiter_state
WHERE last_acquisition_at < NOW() - INTERVAL '7 days';
```

**Check table size:**
```sql
SELECT pg_size_pretty(pg_total_relation_size('rate_limiter_state'));
```

**Verify permissions:**
```sql
SELECT grantee, privilege_type
FROM information_schema.table_privileges
WHERE table_name = 'rate_limiter_state';
```

## See Also

- [PostgreSQL Engine](../engines/postgres.md) - Configuration reference
