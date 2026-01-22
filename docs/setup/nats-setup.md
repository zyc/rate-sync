# NATS Setup Guide

Manual setup instructions for production environments.

## Requirements

- NATS Server 2.9+ with JetStream enabled
- Network access to NATS server
- Authentication credentials (if required)

## JetStream Verification

### 1. Verify JetStream is Enabled

Connect to your NATS server and verify JetStream is enabled:

```bash
# Using nats CLI
nats server check jetstream

# Or check server info
nats server info

# Expected output should show:
# JetStream: enabled
```

If JetStream is **not** enabled, contact your NATS administrator to enable it with the `-js` flag.

### 2. Verify Bucket Creation Permissions

rate-sync automatically creates KV buckets on first use. Verify your credentials have permission to create buckets:

```bash
# Test bucket creation (will be created if doesn't exist)
nats kv add test_bucket

# List buckets
nats kv ls

# Delete test bucket
nats kv rm test_bucket
```

If you **cannot** create buckets, you have two options:

**Option A: Request permission to create buckets** (recommended)

Contact your NATS administrator to grant bucket creation permissions.

**Option B: Pre-create bucket manually**

```bash
# Create the bucket that rate-sync will use
# Default bucket name: "rate_limits"
nats kv add rate_limits --history=1 --ttl=24h
```

Then configure rate-sync with `auto_create = false`:

```toml
[stores.prod]
engine = "nats"
url = "nats://prod.example.com:4222"
bucket_name = "rate_limits"
auto_create = false  # Bucket must already exist
```

## Configuration

### Basic Configuration (No Authentication)

```toml
[stores.prod]
engine = "nats"
url = "nats://nats.example.com:4222"
```

### With Token Authentication

```toml
[stores.prod]
engine = "nats"
url = "nats://nats.example.com:4222"
token = "${NATS_TOKEN}"  # From environment variable
```

### With User/Password Authentication

```toml
[stores.prod]
engine = "nats"
url = "nats://user:password@nats.example.com:4222"
```

### With TLS/SSL

```toml
[stores.prod]
engine = "nats"
url = "tls://nats.example.com:4222"  # Use tls:// scheme
token = "${NATS_TOKEN}"
```

### With Custom Bucket Name

```toml
[stores.prod]
engine = "nats"
url = "nats://nats.example.com:4222"
bucket_name = "myapp_rate_limits"  # Custom bucket name
```

### High Availability (Multiple URLs)

```toml
[stores.prod_ha]
engine = "nats"
# Client will try each URL until successful connection
url = "nats://nats-1.prod:4222,nats://nats-2.prod:4222,nats://nats-3.prod:4222"
token = "${NATS_TOKEN}"
```

## Bucket Configuration

rate-sync creates KV buckets with these settings:

- **History**: 1 (only current value stored)
- **TTL**: 24 hours (keys auto-expire after 24h of inactivity)
- **Replicas**: 1 (inherits from JetStream server config)

These settings are optimized for rate limiting and cannot be customized (yet).

## Monitoring

### List Buckets

```bash
# List all KV buckets
nats kv ls

# Expected output:
# ╭───────────────────────────────────────╮
# │         Key-Value Store Status        │
# ├─────────────────┬────────────────────┤
# │ Bucket          │ rate_limits        │
# │ History         │ 1                  │
# │ TTL             │ 24h0m0s            │
# └─────────────────┴────────────────────┘
```

### Check Bucket Contents

```bash
# Get all keys in bucket
nats kv get rate_limits --all

# Get specific group's state
nats kv get rate_limits <group_id>

# Example:
nats kv get rate_limits api_external
```

### Watch Bucket Activity (Live)

```bash
# Watch all changes to the bucket
nats kv watch rate_limits

# This shows real-time updates as rate limits are acquired
```

## Troubleshooting

### "JetStream not enabled"

**Problem**: NATS server doesn't have JetStream enabled

**Solution**: Contact your NATS administrator to enable JetStream with `-js` flag

### "Insufficient permissions"

**Problem**: Cannot create buckets

**Solution**:
1. Request bucket creation permissions from admin, OR
2. Pre-create bucket manually (see "Verify Bucket Creation Permissions" above)

### "Connection refused"

**Problem**: Cannot connect to NATS server

**Solutions**:
- Verify URL is correct
- Check network connectivity: `telnet nats.example.com 4222`
- Verify firewall rules allow connection
- Check if server is running

### "Authentication violation"

**Problem**: Token or credentials are invalid

**Solutions**:
- Verify token matches server configuration
- Check environment variable is set: `echo $NATS_TOKEN`
- Verify username/password if using user auth

### "Bucket not found"

**Problem**: Bucket doesn't exist and cannot be created

**Solution**:
- Verify JetStream is enabled
- Check bucket creation permissions
- Pre-create bucket manually

## Cleanup

### Remove Old Keys

rate-sync keys auto-expire after 24h of inactivity (TTL). No manual cleanup needed.

To manually delete a specific group's state:

```bash
nats kv del rate_limits <group_id>
```

To delete entire bucket:

```bash
nats kv rm rate_limits
```

**Warning**: Deleting the bucket will remove all rate limiting state.

## Migration from Auto-Create

If rate-sync created buckets automatically and you want to verify settings:

```bash
# Check bucket configuration
nats kv status rate_limits

# Expected output shows:
# - History: 1
# - TTL: 24h
```

If settings are incorrect, you can:

1. Delete bucket: `nats kv rm rate_limits`
2. Recreate with correct settings: `nats kv add rate_limits --history=1 --ttl=24h`
3. Restart application

## See Also

- [NATS Engine Documentation](../engines/nats.md)
- [NATS CLI Documentation](https://docs.nats.io/using-nats/nats-tools/nats_cli)
- [JetStream Guide](https://docs.nats.io/nats-concepts/jetstream)
