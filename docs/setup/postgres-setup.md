# PostgreSQL Setup Guide

Manual setup instructions for production environments.

## SQL Setup Script

### 1. Create Table

```sql
-- Create the rate limiter state table
CREATE TABLE IF NOT EXISTS rate_limiter_state (
    group_id VARCHAR(255) PRIMARY KEY,
    last_acquisition_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL DEFAULT 1
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_rate_limiter_group
ON rate_limiter_state(group_id);

-- Add comment for documentation
COMMENT ON TABLE rate_limiter_state IS 'Rate limiter state for distributed coordination';
COMMENT ON COLUMN rate_limiter_state.group_id IS 'Unique identifier for rate limit group';
COMMENT ON COLUMN rate_limiter_state.last_acquisition_at IS 'Timestamp of last successful acquisition';
COMMENT ON COLUMN rate_limiter_state.version IS 'Version number for optimistic locking';
```

### 2. Grant Permissions

**Option A: Full permissions (auto-create enabled)**

```sql
-- For development or when auto_create = true
GRANT CREATE ON SCHEMA public TO myapp_user;
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
GRANT USAGE ON SCHEMA public TO myapp_user;
```

**Option B: Read-write only (production recommended)**

```sql
-- For production with auto_create = false
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
GRANT USAGE ON SCHEMA public TO myapp_user;
```

### 3. Verify Setup

```sql
-- Check table exists
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'rate_limiter_state';

-- Check permissions
SELECT grantee, privilege_type
FROM information_schema.table_privileges
WHERE table_schema = 'public'
  AND table_name = 'rate_limiter_state';

-- Check indexes
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'rate_limiter_state';
```

## Configuration

After setup, configure rate-sync with `auto_create = false`:

```toml
[stores.db]
engine = "postgres"
url = "postgresql://myapp_user:password@localhost:5432/mydb"
auto_create = false  # Table already created
```

## Custom Schema

To use a custom schema (not `public`):

```sql
-- Create schema
CREATE SCHEMA IF NOT EXISTS rate_limiting;

-- Create table in custom schema
CREATE TABLE IF NOT EXISTS rate_limiting.rate_limiter_state (
    group_id VARCHAR(255) PRIMARY KEY,
    last_acquisition_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL DEFAULT 1
);

-- Grant permissions
GRANT USAGE ON SCHEMA rate_limiting TO myapp_user;
GRANT SELECT, INSERT, UPDATE ON rate_limiting.rate_limiter_state TO myapp_user;
```

Configuration:

```toml
[stores.db]
engine = "postgres"
url = "postgresql://myapp_user:password@localhost:5432/mydb"
schema_name = "rate_limiting"
auto_create = false
```

## Maintenance

### Cleanup Old Records

Rate limiter state rows persist indefinitely. For cleanup:

```sql
-- Delete records older than 7 days (adjust as needed)
DELETE FROM rate_limiter_state
WHERE last_acquisition_at < NOW() - INTERVAL '7 days';
```

Or create a cron job:

```sql
-- Create cleanup function
CREATE OR REPLACE FUNCTION cleanup_rate_limiter_state()
RETURNS void AS $$
BEGIN
    DELETE FROM rate_limiter_state
    WHERE last_acquisition_at < NOW() - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;

-- Schedule with pg_cron (if available)
SELECT cron.schedule('cleanup-rate-limiter', '0 0 * * *',
    'SELECT cleanup_rate_limiter_state()');
```

### Monitoring

```sql
-- Check number of groups
SELECT COUNT(*) as total_groups FROM rate_limiter_state;

-- Check recent activity
SELECT group_id, last_acquisition_at
FROM rate_limiter_state
WHERE last_acquisition_at > NOW() - INTERVAL '1 hour'
ORDER BY last_acquisition_at DESC;

-- Check table size
SELECT pg_size_pretty(pg_total_relation_size('rate_limiter_state')) as size;
```

## Troubleshooting

### Table Not Found

```sql
-- Check if table exists
\dt rate_limiter_state

-- Check current schema
SHOW search_path;

-- Set search path if needed
SET search_path TO public, rate_limiting;
```

### Permission Denied

```sql
-- Check current user
SELECT current_user;

-- Check permissions
\dp rate_limiter_state

-- Grant missing permissions
GRANT SELECT, INSERT, UPDATE ON rate_limiter_state TO myapp_user;
```

### Performance Issues

```sql
-- Check for missing indexes
SELECT schemaname, tablename, indexname
FROM pg_indexes
WHERE tablename = 'rate_limiter_state';

-- Analyze table
ANALYZE rate_limiter_state;

-- Check query performance
EXPLAIN ANALYZE
SELECT last_acquisition_at, version
FROM rate_limiter_state
WHERE group_id = 'test_group'
FOR UPDATE;
```

## Migration from Auto-Create

If you started with `auto_create=true` and want to switch to manual:

1. Verify table was created:
```sql
\d rate_limiter_state
```

2. Update configuration:
```toml
auto_create = false
```

3. Revoke CREATE permission (optional):
```sql
REVOKE CREATE ON SCHEMA public FROM myapp_user;
```

4. Restart application

## See Also

- [PostgreSQL Engine Documentation](../strategies/postgres.md)
- [PostgreSQL Official Docs](https://www.postgresql.org/docs/)
