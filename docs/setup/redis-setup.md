# Redis Setup

## Docker (Quickstart)

```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

## Local Installation

**macOS:**
```bash
brew install redis
brew services start redis
```

**Ubuntu/Debian:**
```bash
sudo apt update && sudo apt install redis-server
sudo systemctl start redis
```

**Windows:**
Use Docker or WSL2.

## Verify Connection

```bash
redis-cli ping
# Expected: PONG
```

With password:
```bash
redis-cli -a YOUR_PASSWORD ping
```

With TLS:
```bash
redis-cli --tls -u rediss://localhost:6380 ping
```

## Docker Compose (Production-like)

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
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

## Configuration Examples

**Basic:**
```toml
[stores.redis]
engine = "redis"
url = "redis://localhost:6379/0"
```

**With password:**
```toml
[stores.redis]
engine = "redis"
url = "redis://:${REDIS_PASSWORD}@localhost:6379/0"
```

**Production (TLS + auth):**
```toml
[stores.redis]
engine = "redis"
url = "rediss://:${REDIS_PASSWORD}@redis.internal:6380/0"
pool_max_size = 20
socket_timeout = 10.0
```

## Common Issues

| Issue | Solution |
|-------|----------|
| `Connection refused` | Start Redis: `redis-server` or `docker start redis` |
| `NOAUTH Authentication required` | Add password to URL: `redis://:password@host:port` |
| `Connection timed out` | Check firewall, security groups, or VPC settings |
| `Too many connections` | Increase `maxclients` in Redis or `pool_max_size` in rate-sync |

## Security Checklist

- [ ] Enable authentication (`requirepass` or ACLs)
- [ ] Use TLS (`rediss://`) for remote connections
- [ ] Restrict network access (private subnet, security groups)
- [ ] Set `maxmemory` with eviction policy
- [ ] Enable persistence (`appendonly yes`) if needed

## See Also

- [Redis Engine](../engines/redis.md) - Configuration reference
