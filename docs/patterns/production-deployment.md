# Production Deployment

Deploy rate-sync with Redis for distributed rate limiting.

## Configuration

```toml
# rate-sync.toml
[stores.redis]
strategy = "redis"
url = "${REDIS_URL}"
max_connections = 50
socket_timeout = 5

# Public endpoints: fail-open
[limiters.public]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 100.0
fail_closed = false

# Auth endpoints: fail-closed (security)
[limiters.auth]
store_id = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 300
fail_closed = true
```

```bash
# .env
REDIS_URL=redis://:password@redis-cluster:6379/0
```

## FastAPI Integration

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from ratesync import get_limiter

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify Redis on startup
    try:
        get_limiter("public")
    except Exception as e:
        raise RuntimeError(f"Rate limiter init failed: {e}")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/health/rate-limiter")
async def health():
    try:
        limiter = get_limiter("public")
        await limiter.get_state()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}, 503
```

## Fail-Safe Strategies

| Strategy | Use Case | Config |
|----------|----------|--------|
| **Fail-Open** | Public endpoints | `fail_closed = false` |
| **Fail-Closed** | Auth, security | `fail_closed = true` |

## Circuit Breaker

```python
class RateLimiterCircuitBreaker:
    def __init__(self, threshold: int = 5, timeout: int = 60):
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure = 0
        self.is_open = False

    async def check(self, limiter_id: str, identifier: str) -> bool:
        if self.is_open and (time.time() - self.last_failure) > self.timeout:
            self.is_open = False
            self.failures = 0

        if self.is_open:
            return True  # Fail-open

        try:
            limiter = await get_or_clone_limiter(limiter_id, identifier)
            self.failures = 0
            return await limiter.try_acquire(timeout=0)
        except Exception:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.threshold:
                self.is_open = True
            return True
```

## Redis Setup

### Docker Compose

```yaml
services:
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data

volumes:
  redis-data:
```

### Managed Services

- AWS ElastiCache
- Google Cloud Memorystore
- Azure Cache for Redis
- Redis Cloud

## Checklist

**Before Deploy:**
- [ ] Redis running and accessible
- [ ] SSL/TLS configured (if required)
- [ ] rate-sync.toml configured
- [ ] Health check endpoint working

**After Deploy:**
- [ ] Health check returns 200
- [ ] Rate limits enforced across instances
- [ ] Metrics being collected
- [ ] No Redis connection errors in logs

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| High latency | Redis far away | Move Redis closer |
| Connection exhausted | Too many connections | Reduce `max_connections` |
| Limits not working | Multiple Redis instances | Use single cluster |

## See Also

- [Testing](./testing.md)
- [Monitoring](./monitoring.md)
