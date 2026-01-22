# Redis Setup Guide

Manual setup instructions for production-like environments that rely on Redis
for distributed rate limiting.

## Requirements

- Redis 6.0+ (7.x recommended)
- Persistence enabled (AOF or RDB) if you need durability across restarts
- Authentication (password or ACL) for production deployments
- Network access from every application host to the Redis endpoint

## Quick Verification

### 1. Check Service Health

```bash
redis-cli -u redis://redis.example.com:6379 ping
# Expected: PONG
```

### 2. Verify Authentication (if enabled)

```bash
redis-cli -u redis://:PASSWORD@redis.example.com:6379 ping
```

### 3. Verify TLS (if enabled)

```bash
redis-cli --tls \
  --cert /path/client.crt --key /path/client.key --cacert /path/ca.crt \
  -u rediss://redis.example.com:6380 ping
```

If any of these checks fail, contact the team that manages Redis to validate
credentials or network policies before wiring rate-sync to it.

## Docker / Local Development

```bash
docker run -d --name redis-rate-sync \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server \
    --appendonly yes \
    --appendfsync everysec \
    --maxmemory 256mb \
    --maxmemory-policy allkeys-lru
```

### docker-compose snippet

```yaml
services:
  redis:
    image: redis:7-alpine
    command:
      - redis-server
      - --appendonly yes
      - --maxmemory 256mb
      - --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
```

## Managed Redis (AWS ElastiCache, Azure Cache, etc.)

1. Provision a Redis cluster with at least one replica.
2. Enable TLS and password/ACL authentication.
3. Restrict inbound traffic to the CIDRs where your services live.
4. Note the connection string (e.g., `rediss://:PASSWORD@cluster-id.cache.amazonaws.com:6379/0`).

## Configuration Examples

### Minimal

```toml
[stores.redis_shared]
engine = "redis"
url = "redis://redis.internal:6379/0"
```

### With password and custom prefix

```toml
[stores.redis_shared]
engine = "redis"
url = "redis://:${REDIS_PASSWORD}@redis.internal:6379/0"
key_prefix = "company_rate_limit"
pool_max_size = 20
```

### TLS and failover

```toml
[stores.redis_shared]
engine = "redis"
url = "rediss://:${REDIS_PASSWORD}@redis-primary.internal:6380/0"
socket_timeout = 10.0
socket_connect_timeout = 10.0

[limiters.api_partner]
store = "redis_shared"
rate_per_second = 1.0
timeout = 30.0
```

### Environment variable override

```toml
[stores.redis_shared]
engine = "redis"
url = "${REDIS_URL}"
```

Set `REDIS_URL=rediss://:${REDIS_PASSWORD}@redis.internal:6380/0`.

## Monitoring Checklist

- `redis-cli INFO memory` – track memory usage and eviction stats.
- `redis-cli INFO stats` – watch command throughput and latency.
- `redis-cli --scan --pattern "<key_prefix>:*"` – inspect rate limiter keys.
- Export metrics (latency, timeouts) via `RateLimiterMetrics` and ship to your
  observability platform (see `docs/observability.md`).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `redis.exceptions.ConnectionError` | Verify host/port, security groups, and that Redis process is healthy |
| `BUSY Redis is busy running a script` | Ensure scripts finish quickly; rate-sync’s Lua script is constant time |
| High latency | Place Redis close to the application or enable cluster/replicas |
| Too many connections | Increase `pool_max_size` or reuse limiter instances instead of recreating them |

## Security Best Practices

1. **Authentication**: Require passwords or ACL users.
2. **TLS**: Use `rediss://` endpoints in transit-sensitive environments.
3. **Network**: Keep Redis in a private subnet and avoid public exposure.
4. **Backups**: Enable snapshots/AOF if you need to recover limiter state quickly.
5. **Monitoring**: Alert on memory exhaustion, failovers, and connection spikes.

## Related Docs

- [Redis Engine](../engines/redis.md)
- [Observability](../observability.md)
