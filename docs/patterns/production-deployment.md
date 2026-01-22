# Production Deployment Pattern

Best practices for deploying rate-sync in production with Redis, high availability, and operational excellence.

---

## Problem

Production rate limiting requires:

1. **Distributed coordination** across multiple app instances
2. **High availability** with no single point of failure
3. **Low latency** to not impact request performance
4. **Persistence** to survive restarts
5. **Observability** to monitor and debug issues

Development patterns (memory backend) don't scale to production:
- ❌ No coordination between instances
- ❌ State lost on restart
- ❌ No visibility into limits
- ❌ No failover capability

---

## Solution

Use **Redis backend** with proper production configuration:

1. **Redis Cluster/Sentinel** for high availability
2. **Connection pooling** for performance
3. **Monitoring** for visibility
4. **Graceful degradation** for failures
5. **Configuration management** for flexibility

---

## Architecture

### Production Setup

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   App 1     │     │   App 2     │     │   App 3     │
│  (FastAPI)  │     │  (FastAPI)  │     │  (FastAPI)  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                           ▼
                   ┌───────────────┐
                   │  Redis Cluster│
                   │   (Primary +  │
                   │   Replicas)   │
                   └───────────────┘
```

All app instances share the same distributed rate limit state via Redis.

---

## Configuration

### rate-sync.toml (Production)

```toml
# Production Redis configuration
[stores.redis_prod]
strategy = "redis"
url = "${REDIS_URL}"  # From environment
max_connections = 50
socket_timeout = 5
socket_connect_timeout = 5
retry_on_timeout = true
health_check_interval = 30

# Production rate limiters

# Public endpoints: Generous but protective
[limiters.public]
store_id = "redis_prod"
algorithm = "token_bucket"
rate_per_second = 100.0
timeout = 0.1
fail_closed = false  # Allow on Redis failure

# Authenticated endpoints: Higher limits
[limiters.authenticated]
store_id = "redis_prod"
algorithm = "token_bucket"
rate_per_second = 300.0
timeout = 0.1
fail_closed = false

# Auth endpoints: Strict sliding window
[limiters.login_ip]
store_id = "redis_prod"
algorithm = "sliding_window"
limit = 10
window_seconds = 300
timeout = 0.1
fail_closed = true  # Block on Redis failure (security)

[limiters.login_credential]
store_id = "redis_prod"
algorithm = "sliding_window"
limit = 5
window_seconds = 900
timeout = 0.1
fail_closed = true
```

### Environment Variables

```bash
# .env.production
REDIS_URL=redis://redis-cluster:6379/0
REDIS_PASSWORD=your-secure-password
REDIS_SSL=true
REDIS_CERT_REQS=required
```

---

## Redis Setup

### Option 1: Redis Cluster (Recommended)

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  redis-node-1:
    image: redis:7-alpine
    command: >
      redis-server
      --cluster-enabled yes
      --cluster-node-timeout 5000
      --appendonly yes
      --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-1-data:/data
    networks:
      - redis-cluster

  redis-node-2:
    image: redis:7-alpine
    command: >
      redis-server
      --cluster-enabled yes
      --cluster-node-timeout 5000
      --appendonly yes
      --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-2-data:/data
    networks:
      - redis-cluster

  redis-node-3:
    image: redis:7-alpine
    command: >
      redis-server
      --cluster-enabled yes
      --cluster-node-timeout 5000
      --appendonly yes
      --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-3-data:/data
    networks:
      - redis-cluster

volumes:
  redis-1-data:
  redis-2-data:
  redis-3-data:

networks:
  redis-cluster:
```

### Option 2: Redis Sentinel (High Availability)

```yaml
version: '3.8'

services:
  redis-master:
    image: redis:7-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-master-data:/data

  redis-replica-1:
    image: redis:7-alpine
    command: >
      redis-server
      --appendonly yes
      --replicaof redis-master 6379
      --masterauth ${REDIS_PASSWORD}
      --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-replica-1-data:/data
    depends_on:
      - redis-master

  redis-sentinel-1:
    image: redis:7-alpine
    command: >
      redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./sentinel.conf:/etc/redis/sentinel.conf
    depends_on:
      - redis-master
      - redis-replica-1

volumes:
  redis-master-data:
  redis-replica-1-data:
```

### sentinel.conf

```conf
port 26379
sentinel monitor mymaster redis-master 6379 2
sentinel auth-pass mymaster ${REDIS_PASSWORD}
sentinel down-after-milliseconds mymaster 5000
sentinel parallel-syncs mymaster 1
sentinel failover-timeout mymaster 10000
```

### Option 3: Managed Redis (Easiest)

Use managed Redis services:
- **AWS ElastiCache** (Redis Cluster mode)
- **Google Cloud Memorystore**
- **Azure Cache for Redis**
- **Redis Cloud**

Connection string format:
```
redis://username:password@host:port/db
rediss://username:password@host:port/db  # SSL
```

---

## Application Integration

### FastAPI Startup/Shutdown

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage rate limiter lifecycle."""
    # Startup
    logger.info("Initializing rate limiters...")

    # rate-sync auto-initializes from rate-sync.toml
    # Test Redis connection
    try:
        from ratesync import get_limiter
        test_limiter = get_limiter("public")
        # Will fail if Redis is unreachable
        logger.info("Rate limiters initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize rate limiters: {e}")
        # Decide: fail startup or degrade gracefully
        raise

    yield

    # Shutdown
    logger.info("Shutting down rate limiters...")
    # rate-sync auto-cleanup

app = FastAPI(lifespan=lifespan)
```

### Health Check Endpoint

```python
from fastapi import Response
from ratesync import get_limiter

@app.get("/health/rate-limiter")
async def rate_limiter_health():
    """Check if rate limiter backend is healthy."""
    try:
        # Try to get state of a limiter
        limiter = get_limiter("public")
        state = await limiter.get_state()

        return {
            "status": "healthy",
            "backend": "redis",
            "accessible": True,
        }
    except Exception as e:
        return Response(
            content=json.dumps({
                "status": "unhealthy",
                "backend": "redis",
                "accessible": False,
                "error": str(e),
            }),
            status_code=503,
            media_type="application/json",
        )
```

---

## Fail-Safe Strategies

### Fail-Open (Default for Public Endpoints)

```toml
[limiters.public]
store_id = "redis_prod"
algorithm = "token_bucket"
rate_per_second = 100.0
fail_closed = false  # ⬅️ Allow requests if Redis fails
```

**Use when**: Redis failure shouldn't block legitimate traffic.
**Trade-off**: Potential abuse during outage.

### Fail-Closed (Security-Critical Endpoints)

```toml
[limiters.login_credential]
store_id = "redis_prod"
algorithm = "sliding_window"
limit = 5
window_seconds = 900
fail_closed = true  # ⬅️ Block requests if Redis fails
```

**Use when**: Security > availability (auth endpoints).
**Trade-off**: Service unavailable during Redis outage.

### Circuit Breaker Pattern

```python
from ratesync import get_or_clone_limiter
import logging

logger = logging.getLogger(__name__)

class RateLimiterCircuitBreaker:
    """Circuit breaker for rate limiter failures."""

    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time = 0
        self.is_open = False

    async def check_limit(
        self,
        limiter_id: str,
        identifier: str,
    ) -> bool:
        """Check rate limit with circuit breaker."""
        import time

        now = time.time()

        # Reset circuit if timeout elapsed
        if self.is_open and (now - self.last_failure_time) > self.timeout:
            logger.info("Circuit breaker: Attempting to close circuit")
            self.is_open = False
            self.failures = 0

        # Circuit is open - fail fast
        if self.is_open:
            logger.warning("Circuit breaker: Circuit is OPEN, allowing request")
            return True  # Fail-open during circuit break

        try:
            limiter = await get_or_clone_limiter(limiter_id, identifier)
            allowed = await limiter.try_acquire(timeout=0)

            # Success - reset failure count
            self.failures = 0
            return allowed

        except Exception as e:
            self.failures += 1
            self.last_failure_time = now

            logger.error(f"Rate limiter error ({self.failures}/{self.failure_threshold}): {e}")

            # Open circuit if threshold reached
            if self.failures >= self.failure_threshold:
                self.is_open = True
                logger.error("Circuit breaker: Circuit is now OPEN")

            return True  # Fail-open on error
```

---

## Performance Optimization

### Connection Pooling

```toml
[stores.redis_prod]
strategy = "redis"
url = "${REDIS_URL}"
max_connections = 50  # ⬅️ Pool size
socket_timeout = 5
socket_connect_timeout = 5
```

**Tuning**:
- Start with `max_connections = app_instances * concurrent_requests / 10`
- Monitor connection usage
- Increase if seeing connection exhaustion

### Timeout Configuration

```toml
[limiters.public]
timeout = 0.1  # ⬅️ 100ms max wait

[limiters.critical]
timeout = 0.01  # ⬅️ 10ms for critical path
```

**Guideline**:
- Public endpoints: 100-200ms
- Authenticated endpoints: 50-100ms
- Critical path: 10-50ms
- Background jobs: 1000ms+

### Pipeline Commands (Future)

```python
# Not yet implemented, but planned for v2.x
from ratesync import pipeline

async def check_multiple_limits(identifiers: list[str]):
    """Check multiple limits in single Redis round-trip."""
    async with pipeline() as pipe:
        results = []
        for identifier in identifiers:
            results.append(pipe.try_acquire("public", identifier))

        return await pipe.execute()
```

---

## Monitoring

### Metrics to Track

```python
from prometheus_client import Counter, Histogram, Gauge

# Rate limit decisions
rate_limit_allowed = Counter(
    "rate_limit_allowed_total",
    "Total allowed requests",
    ["limiter_id", "endpoint"],
)

rate_limit_blocked = Counter(
    "rate_limit_blocked_total",
    "Total blocked requests",
    ["limiter_id", "endpoint"],
)

# Rate limit latency
rate_limit_latency = Histogram(
    "rate_limit_check_duration_seconds",
    "Time spent checking rate limits",
    ["limiter_id"],
)

# Redis connection health
redis_connection_errors = Counter(
    "rate_limit_redis_errors_total",
    "Total Redis connection errors",
)

# Usage metrics
rate_limit_usage = Gauge(
    "rate_limit_usage_percent",
    "Current rate limit usage percentage",
    ["limiter_id", "identifier"],
)
```

### Instrumentation Example

```python
import time
from ratesync import get_or_clone_limiter

async def rate_limit_with_metrics(
    limiter_id: str,
    identifier: str,
    endpoint: str,
) -> bool:
    """Rate limit check with Prometheus metrics."""
    start = time.time()

    try:
        limiter = await get_or_clone_limiter(limiter_id, identifier)
        allowed = await limiter.try_acquire(timeout=0)

        # Record latency
        duration = time.time() - start
        rate_limit_latency.labels(limiter_id=limiter_id).observe(duration)

        # Record decision
        if allowed:
            rate_limit_allowed.labels(
                limiter_id=limiter_id,
                endpoint=endpoint,
            ).inc()
        else:
            rate_limit_blocked.labels(
                limiter_id=limiter_id,
                endpoint=endpoint,
            ).inc()

        # Get usage percentage
        state = await limiter.get_state()
        total = state.current_usage + state.remaining
        if total > 0:
            usage_pct = (state.current_usage / total) * 100
            rate_limit_usage.labels(
                limiter_id=limiter_id,
                identifier=identifier,
            ).set(usage_pct)

        return allowed

    except Exception as e:
        redis_connection_errors.inc()
        logger.error(f"Rate limit check failed: {e}")
        return True  # Fail-open
```

---

## Logging

### Structured Logging

```python
import structlog

logger = structlog.get_logger()

async def rate_limit_with_logging(
    limiter_id: str,
    identifier: str,
    endpoint: str,
):
    """Rate limit check with structured logging."""
    try:
        limiter = await get_or_clone_limiter(limiter_id, identifier)
        allowed = await limiter.try_acquire(timeout=0)

        if allowed:
            state = await limiter.get_state()
            logger.info(
                "rate_limit_allowed",
                limiter_id=limiter_id,
                identifier=identifier,
                endpoint=endpoint,
                remaining=state.remaining,
                reset_at=state.reset_at,
            )
        else:
            state = await limiter.get_state()
            logger.warning(
                "rate_limit_exceeded",
                limiter_id=limiter_id,
                identifier=identifier,
                endpoint=endpoint,
                limit=state.current_usage + state.remaining,
                reset_at=state.reset_at,
            )

        return allowed

    except Exception as e:
        logger.error(
            "rate_limit_error",
            limiter_id=limiter_id,
            identifier=identifier,
            endpoint=endpoint,
            error=str(e),
            error_type=type(e).__name__,
        )
        return True
```

### Log Levels

- **INFO**: Successful checks (with sampling)
- **WARNING**: Rate limit exceeded
- **ERROR**: Redis connection failures, unexpected errors
- **DEBUG**: Detailed state information

---

## Deployment Checklist

### Before Deploy

- [ ] Redis cluster/sentinel is running and healthy
- [ ] Connection string is correct in environment
- [ ] SSL/TLS is configured if required
- [ ] Password authentication is enabled
- [ ] Firewall rules allow app → Redis connections
- [ ] Health check endpoint is working
- [ ] Metrics are being exported
- [ ] Logs are being collected
- [ ] rate-sync.toml is configured for production
- [ ] Fail-safe strategy is appropriate for each endpoint

### After Deploy

- [ ] Health check returns 200 OK
- [ ] Metrics show successful rate limit checks
- [ ] No error logs about Redis connection
- [ ] Rate limits are being enforced (test with curl)
- [ ] Response includes rate limit headers
- [ ] Distributed limiting works across instances
- [ ] Redis memory usage is reasonable
- [ ] Redis CPU usage is reasonable

### Load Testing

```bash
# Test rate limiting under load
hey -n 10000 -c 100 -H "Authorization: Bearer token" \
  https://api.example.com/endpoint

# Should see 429 responses after quota exhausted
# Check metrics for:
# - P50/P95/P99 latency
# - Error rate
# - Redis connection count
```

---

## Scaling Considerations

### Horizontal Scaling (Multiple App Instances)

✅ **Works out of the box** - Redis backend coordinates all instances.

```bash
# Run multiple app instances
docker-compose up --scale api=5
```

All instances share the same rate limit state via Redis.

### Redis Scaling

**Memory usage per limiter**:
- Token bucket: ~100 bytes per active identifier
- Sliding window: ~200 bytes per active identifier

**Example**:
- 1M active users
- Sliding window: ~200 MB
- Token bucket: ~100 MB

**When to scale Redis**:
- Memory usage > 80% of available
- CPU usage > 70% (under load)
- Network I/O saturated

**Scaling options**:
1. Vertical: Increase Redis instance size
2. Horizontal: Redis Cluster with sharding
3. Read replicas: For get_state() heavy workloads

---

## Disaster Recovery

### Redis Backup

```bash
# Enable AOF (append-only file) persistence
redis-server --appendonly yes --appendfsync everysec

# Manual backup
redis-cli BGSAVE

# Restore from backup
cp dump.rdb /var/lib/redis/
redis-server
```

### State Loss Recovery

**If Redis loses all data**:
- Rate limits reset to zero for all identifiers
- First requests after recovery will succeed
- Limits rebuild as users make requests
- **No permanent data loss** (rate limit state is transient)

**Mitigation**:
- Use Redis persistence (AOF or RDB)
- Use Redis Sentinel/Cluster for HA
- Configure appropriate fail-safe strategy

---

## Security

### Redis Authentication

```toml
[stores.redis_prod]
url = "redis://:password@host:6379/0"
```

### TLS/SSL

```toml
[stores.redis_prod]
url = "rediss://username:password@host:6379/0"  # Note: rediss://
ssl_cert_reqs = "required"
ssl_ca_certs = "/path/to/ca-cert.pem"
```

### Network Isolation

```yaml
# docker-compose.yml
services:
  api:
    networks:
      - app-network

  redis:
    networks:
      - app-network
    # Don't expose Redis port publicly

networks:
  app-network:
    driver: bridge
```

---

## Troubleshooting

### Issue: High Latency

**Symptoms**: Rate limit checks taking > 100ms

**Causes**:
- Redis is far away (network latency)
- Redis is overloaded (CPU/memory)
- Too many concurrent connections

**Solutions**:
1. Move Redis closer to app (same region/AZ)
2. Scale Redis vertically
3. Increase connection pool size
4. Reduce timeout values
5. Use Redis Cluster for sharding

### Issue: Connection Exhausted

**Symptoms**: "Too many connections to Redis"

**Causes**:
- Too many app instances
- Connection pool too large
- Connections not being closed

**Solutions**:
1. Reduce `max_connections` per instance
2. Scale Redis to support more connections
3. Check for connection leaks

### Issue: Rate Limits Not Working

**Symptoms**: Users exceed limits without being blocked

**Causes**:
- Multiple Redis instances (not clustered)
- Clock skew between app instances
- Configuration not loaded

**Solutions**:
1. Use single Redis instance/cluster
2. Sync clocks with NTP
3. Check rate-sync.toml is loaded
4. Verify limiter IDs match configuration

---

## See Also

- [Testing Pattern](./testing.md) - Development vs production configuration
- [Monitoring Pattern](./monitoring.md) - Detailed observability guide
- [Authentication Protection](./authentication-protection.md) - Production security patterns
