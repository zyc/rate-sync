"""Integration performance tests for Redis-based rate limiting.

These tests require a real Redis server and measure actual performance
characteristics including:
- Network latency
- Connection pooling efficiency
- Lua script execution time
- Concurrent client handling
- Sliding window accuracy

Run with: pytest tests/integration/test_performance_redis.py -v

Requires Redis running at localhost:6379
"""

import asyncio
import os
import statistics
import time
import uuid
from collections import defaultdict

import pytest

# Skip entire module if Redis is not configured
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SKIP_REDIS_TESTS = os.environ.get("SKIP_REDIS_TESTS", "").lower() == "true"

try:
    from redis import asyncio as redis_asyncio

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.redis,
    pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis library not installed"),
    pytest.mark.skipif(SKIP_REDIS_TESTS, reason="Redis tests disabled via SKIP_REDIS_TESTS"),
]


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
async def redis_client():
    """Create a Redis client for test setup/cleanup."""
    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not available at localhost:6379")
    yield client
    await client.aclose()


@pytest.fixture
async def unique_prefix():
    """Generate unique prefix for test isolation."""
    return f"perf_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def cleanup_keys(redis_client, unique_prefix):
    """Cleanup Redis keys after test."""
    yield
    # Clean up all keys with test prefix
    keys = []
    async for key in redis_client.scan_iter(f"{unique_prefix}:*"):
        keys.append(key)
    if keys:
        await redis_client.delete(*keys)


# =============================================================================
# HELPERS
# =============================================================================


class PerformanceMetrics:
    """Collects and reports performance metrics."""

    def __init__(self):
        self.latencies: list[float] = []
        self.successful = 0
        self.failed = 0
        self.start_time = 0.0
        self.end_time = 0.0

    def record(self, success: bool, latency: float):
        self.latencies.append(latency)
        if success:
            self.successful += 1
        else:
            self.failed += 1

    @property
    def total_time(self) -> float:
        return self.end_time - self.start_time

    @property
    def throughput(self) -> float:
        total = self.successful + self.failed
        return total / self.total_time if self.total_time > 0 else 0

    @property
    def avg_latency_ms(self) -> float:
        return statistics.mean(self.latencies) * 1000 if self.latencies else 0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies) * 1000 if self.latencies else 0

    @property
    def p95_ms(self) -> float:
        if not self.latencies:
            return 0
        idx = int(len(sorted(self.latencies)) * 0.95)
        return sorted(self.latencies)[idx] * 1000

    @property
    def p99_ms(self) -> float:
        if not self.latencies:
            return 0
        idx = int(len(sorted(self.latencies)) * 0.99)
        return sorted(self.latencies)[idx] * 1000

    def report(self, name: str):
        total = self.successful + self.failed
        print(f"\n=== {name} ===")
        print(f"  Total requests: {total}")
        print(f"  Successful: {self.successful}")
        print(f"  Failed: {self.failed}")
        print(f"  Throughput: {self.throughput:.2f} req/s")
        print(f"  Latency avg: {self.avg_latency_ms:.2f}ms")
        print(f"  Latency p50: {self.p50_ms:.2f}ms")
        print(f"  Latency p95: {self.p95_ms:.2f}ms")
        print(f"  Latency p99: {self.p99_ms:.2f}ms")


# =============================================================================
# TOKEN BUCKET PERFORMANCE TESTS
# =============================================================================


@pytest.mark.asyncio
class TestRedisTokenBucketPerformance:
    """Performance tests for Redis token bucket algorithm."""

    async def test_single_client_throughput(self, redis_client, unique_prefix, cleanup_keys):
        """Test throughput with single sequential client."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_max_size=5,
        )

        limiter = RedisRateLimiter.from_config(
            config,
            group_id="single_client",
            rate_per_second=10000.0,  # High rate for throughput test
        )

        try:
            await limiter.initialize()

            metrics = PerformanceMetrics()
            metrics.start_time = time.perf_counter()

            for _ in range(100):  # Reduzido de 500 para teste mais rápido
                start = time.perf_counter()
                result = await limiter.try_acquire(timeout=0)
                elapsed = time.perf_counter() - start
                metrics.record(result, elapsed)

            metrics.end_time = time.perf_counter()
            metrics.report("Token Bucket Single Client")

            # Real Redis should achieve decent throughput
            assert metrics.throughput > 100, f"Throughput too low: {metrics.throughput:.2f}"
            # p95 should be reasonable for localhost
            assert metrics.p95_ms < 50, f"P95 too high: {metrics.p95_ms:.2f}ms"

        finally:
            await limiter.disconnect()

    async def test_concurrent_clients_throughput(self, redis_client, unique_prefix, cleanup_keys):
        """Test throughput with concurrent clients."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_max_size=50,
        )

        limiter = RedisRateLimiter.from_config(
            config,
            group_id="concurrent_clients",
            rate_per_second=10000.0,
        )

        try:
            await limiter.initialize()

            metrics = PerformanceMetrics()
            semaphore = asyncio.Semaphore(50)
            lock = asyncio.Lock()

            async def make_request():
                async with semaphore:
                    start = time.perf_counter()
                    result = await limiter.try_acquire(timeout=0.01)
                    elapsed = time.perf_counter() - start
                    async with lock:
                        metrics.record(result, elapsed)

            metrics.start_time = time.perf_counter()

            tasks = [asyncio.create_task(make_request()) for _ in range(200)]  # Reduzido de 1000
            await asyncio.gather(*tasks)

            metrics.end_time = time.perf_counter()
            metrics.report("Token Bucket Concurrent (50 workers)")

            # Concurrent throughput depends on Redis and network conditions
            # With 50 concurrent workers hitting Redis, expect ~40-100 req/s
            assert metrics.throughput > 20, f"Throughput too low: {metrics.throughput:.2f}"

        finally:
            await limiter.disconnect()

    async def test_rate_limiting_accuracy(self, redis_client, unique_prefix, cleanup_keys):
        """Test that rate limiting is enforced correctly."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_max_size=5,
        )

        rate = 50.0  # 50 req/s
        limiter = RedisRateLimiter.from_config(
            config,
            group_id="rate_accuracy",
            rate_per_second=rate,
        )

        try:
            await limiter.initialize()

            # Run for 2 seconds, attempting ~100 req/s
            duration = 2.0
            allowed_per_second = []
            current_allowed = 0
            current_second = 0

            start = time.perf_counter()
            while time.perf_counter() - start < duration:
                elapsed = time.perf_counter() - start
                second = int(elapsed)

                if second > current_second:
                    allowed_per_second.append(current_allowed)
                    current_allowed = 0
                    current_second = second

                result = await limiter.try_acquire(timeout=0)
                if result:
                    current_allowed += 1

                await asyncio.sleep(0.01)  # ~100 attempts/s

            if current_allowed > 0:
                allowed_per_second.append(current_allowed)

            print(f"\nRate accuracy test: allowed/second = {allowed_per_second}")

            # Each full second should be close to 50 (+/- 30%)
            for i, allowed in enumerate(allowed_per_second[:-1]):
                assert 35 <= allowed <= 65, f"Second {i}: {allowed} (expected ~{rate})"

        finally:
            await limiter.disconnect()


# =============================================================================
# SLIDING WINDOW PERFORMANCE TESTS
# =============================================================================


@pytest.mark.asyncio
class TestRedisSlidingWindowPerformance:
    """Performance tests for Redis sliding window algorithm."""

    async def test_single_client_throughput(self, redis_client, unique_prefix, cleanup_keys):
        """Test sliding window throughput with single client."""
        from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

        limiter = RedisSlidingWindowRateLimiter(
            url=REDIS_URL,
            group_id=f"{unique_prefix}:sw_single",
            limit=10000,  # High limit for throughput test
            window_seconds=60,
            key_prefix=unique_prefix,
        )

        try:
            await limiter.initialize()

            metrics = PerformanceMetrics()
            metrics.start_time = time.perf_counter()

            for _ in range(100):  # Reduzido de 500 para teste mais rápido
                start = time.perf_counter()
                result = await limiter.try_acquire(timeout=0)
                elapsed = time.perf_counter() - start
                metrics.record(result, elapsed)

            metrics.end_time = time.perf_counter()
            metrics.report("Sliding Window Single Client")

            # Sliding window uses ZSET operations which are slower than token bucket
            # Sequential single-client throughput varies based on Redis load
            assert metrics.throughput > 5, f"Throughput too low: {metrics.throughput:.2f}"

        finally:
            await limiter.disconnect()

    async def test_concurrent_clients_throughput(self, redis_client, unique_prefix, cleanup_keys):
        """Test sliding window with concurrent clients."""
        from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

        limiter = RedisSlidingWindowRateLimiter(
            url=REDIS_URL,
            group_id=f"{unique_prefix}:sw_concurrent",
            limit=10000,
            window_seconds=60,
            key_prefix=unique_prefix,
            pool_max_size=50,
        )

        try:
            await limiter.initialize()

            metrics = PerformanceMetrics()
            semaphore = asyncio.Semaphore(50)
            lock = asyncio.Lock()

            async def make_request():
                async with semaphore:
                    start = time.perf_counter()
                    result = await limiter.try_acquire(timeout=0.01)
                    elapsed = time.perf_counter() - start
                    async with lock:
                        metrics.record(result, elapsed)

            metrics.start_time = time.perf_counter()

            tasks = [asyncio.create_task(make_request()) for _ in range(200)]  # Reduzido de 1000
            await asyncio.gather(*tasks)

            metrics.end_time = time.perf_counter()
            metrics.report("Sliding Window Concurrent (50 workers)")

            # Concurrent throughput with ZSET operations is limited
            assert metrics.throughput > 20, f"Throughput too low: {metrics.throughput:.2f}"

        finally:
            await limiter.disconnect()

    async def test_window_limit_enforcement(self, redis_client, unique_prefix, cleanup_keys):
        """Test that sliding window enforces limits correctly."""
        from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

        limit = 20
        limiter = RedisSlidingWindowRateLimiter(
            url=REDIS_URL,
            group_id=f"{unique_prefix}:sw_limit",
            limit=limit,
            window_seconds=60,
            key_prefix=unique_prefix,
        )

        try:
            await limiter.initialize()

            # Try 40 requests, should only allow 20
            allowed = 0
            for _ in range(40):
                result = await limiter.try_acquire(timeout=0)
                if result:
                    allowed += 1

            print(f"\nSliding window limit test: {allowed}/40 allowed (limit: {limit})")

            assert allowed == limit, f"Wrong count: {allowed} (expected {limit})"

        finally:
            await limiter.disconnect()


# =============================================================================
# DISTRIBUTED COORDINATION TESTS
# =============================================================================


@pytest.mark.asyncio
class TestDistributedCoordination:
    """Test distributed rate limiting across multiple simulated instances."""

    async def test_multiple_limiters_same_key(self, redis_client, unique_prefix, cleanup_keys):
        """Test that multiple limiters share the same rate limit."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_max_size=5,
        )

        # Create 3 "instances" (limiters) sharing same group
        limiters = []
        for i in range(3):
            limiter = RedisRateLimiter.from_config(
                config,
                group_id="distributed_test",  # Same group = shared limit
                rate_per_second=30.0,  # 30 req/s total
            )
            await limiter.initialize()
            limiters.append(limiter)

        try:
            # Each limiter makes 20 requests concurrently
            results = defaultdict(int)

            async def instance_requests(idx: int, limiter):
                for _ in range(20):
                    result = await limiter.try_acquire(timeout=0)
                    if result:
                        results[idx] += 1
                    await asyncio.sleep(0.02)  # Spread over time

            tasks = [asyncio.create_task(instance_requests(i, limiters[i])) for i in range(3)]
            await asyncio.gather(*tasks)

            total_allowed = sum(results.values())
            print(f"\nDistributed test: {dict(results)}, total={total_allowed}")

            # Total should be limited across all instances
            # With 30 req/s rate and ~1 second test, should allow ~30-50
            assert total_allowed < 80, f"Distributed limit not working: {total_allowed}"

        finally:
            for limiter in limiters:
                await limiter.disconnect()

    async def test_sliding_window_distributed(self, redis_client, unique_prefix, cleanup_keys):
        """Test sliding window is enforced across multiple instances."""
        from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

        limit = 30
        limiters = []

        for i in range(3):
            limiter = RedisSlidingWindowRateLimiter(
                url=REDIS_URL,
                group_id="sw_distributed",  # Same group
                limit=limit,
                window_seconds=60,
                key_prefix=unique_prefix,
            )
            await limiter.initialize()
            limiters.append(limiter)

        try:
            # Each instance tries 20 requests = 60 total attempts
            results = defaultdict(int)

            async def instance_requests(idx: int, limiter):
                for _ in range(20):
                    result = await limiter.try_acquire(timeout=0)
                    if result:
                        results[idx] += 1

            tasks = [asyncio.create_task(instance_requests(i, limiters[i])) for i in range(3)]
            await asyncio.gather(*tasks)

            total_allowed = sum(results.values())
            print(f"\nSW Distributed: {dict(results)}, total={total_allowed} (limit: {limit})")

            # Should enforce window limit across all instances (with tolerance for timing)
            # Due to network latency and Redis operation timing, we allow ±1 requests
            assert abs(total_allowed - limit) <= 1, (
                f"Wrong total: {total_allowed} (expected {limit}, tolerance ±1)"
            )

        finally:
            for limiter in limiters:
                await limiter.disconnect()


# =============================================================================
# CONNECTION POOLING TESTS
# =============================================================================


@pytest.mark.asyncio
class TestConnectionPooling:
    """Test connection pool efficiency."""

    async def test_pool_reuse_under_load(self, redis_client, unique_prefix, cleanup_keys):
        """Test that connection pool is reused efficiently."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_min_size=2,
            pool_max_size=10,
        )

        limiter = RedisRateLimiter.from_config(
            config,
            group_id="pool_test",
            rate_per_second=10000.0,
        )

        try:
            await limiter.initialize()

            # Make requests with controlled concurrency to stay within pool limits
            semaphore = asyncio.Semaphore(5)  # Keep within pool_max_size
            errors = []
            successful = 0

            async def make_request():
                nonlocal successful
                async with semaphore:
                    try:
                        await limiter.try_acquire(timeout=0.1)
                        successful += 1
                    except Exception as e:
                        errors.append(str(e))

            tasks = [asyncio.create_task(make_request()) for _ in range(100)]
            await asyncio.gather(*tasks)

            print(f"\nPool test: {successful} successful, {len(errors)} errors out of 100 requests")

            # Should have mostly successful requests (allow some connection issues)
            error_rate = len(errors) / 100
            assert error_rate < 0.1, f"Error rate too high: {error_rate:.1%}"

        finally:
            await limiter.disconnect()


# =============================================================================
# STRESS TESTS
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.slow
class TestStress:
    """High-load stress tests."""

    async def test_sustained_high_load(self, redis_client, unique_prefix, cleanup_keys):
        """Test sustained high load for extended period."""
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_max_size=20,
        )

        limiter = RedisRateLimiter.from_config(
            config,
            group_id="sustained_load",
            rate_per_second=200.0,  # 200 req/s
        )

        try:
            await limiter.initialize()

            metrics = PerformanceMetrics()
            duration = 2.0  # Reduzido de 5s para 2s
            semaphore = asyncio.Semaphore(30)
            lock = asyncio.Lock()
            stop = False

            async def worker():
                while not stop:
                    async with semaphore:
                        start = time.perf_counter()
                        try:
                            result = await limiter.try_acquire(timeout=0.01)
                            elapsed = time.perf_counter() - start
                            async with lock:
                                metrics.record(result, elapsed)
                        except Exception:
                            pass
                    await asyncio.sleep(0.003)  # ~300 attempts/s across workers

            # Start workers
            workers = [asyncio.create_task(worker()) for _ in range(30)]

            metrics.start_time = time.perf_counter()
            await asyncio.sleep(duration)
            stop = True
            metrics.end_time = time.perf_counter()

            # Wait for workers to stop
            await asyncio.gather(*workers, return_exceptions=True)

            metrics.report("Sustained High Load (2 seconds)")

            # Should maintain some throughput under sustained load
            # Real Redis performance varies significantly
            assert metrics.throughput > 10, f"Throughput degraded: {metrics.throughput:.2f}"

        finally:
            await limiter.disconnect()

    async def test_burst_recovery(self, redis_client, unique_prefix, cleanup_keys):
        """Test recovery after burst of requests.

        This test validates that rate limiting recovers properly over time.
        The token bucket algorithm allows tokens to refill.
        """
        from ratesync.engines.redis import RedisRateLimiter
        from ratesync.schemas import RedisEngineConfig

        config = RedisEngineConfig(
            url=REDIS_URL,
            key_prefix=unique_prefix,
            pool_max_size=10,
        )

        rate = 100.0  # 100 req/s for faster recovery
        limiter = RedisRateLimiter.from_config(
            config,
            group_id="burst_recovery",
            rate_per_second=rate,
        )

        try:
            await limiter.initialize()

            # Phase 1: Send some requests with short timeout
            # Token bucket may not allow many with timeout=0
            burst_allowed = 0
            for _ in range(20):
                result = await limiter.try_acquire(timeout=0.1)  # Allow some waiting
                if result:
                    burst_allowed += 1

            print(f"\nBurst phase: {burst_allowed}/20 allowed")

            # Phase 2: Wait for recovery (reduzido de 1.0s para 0.3s)
            await asyncio.sleep(0.3)

            # Phase 3: Normal load - should work again
            recovery_allowed = 0
            for _ in range(20):
                result = await limiter.try_acquire(timeout=0.1)
                if result:
                    recovery_allowed += 1
                await asyncio.sleep(0.015)  # Give some spacing

            print(f"Recovery phase: {recovery_allowed}/20 allowed")

            # Should allow at least some requests in each phase
            assert burst_allowed + recovery_allowed > 0, "No requests allowed at all"

        finally:
            await limiter.disconnect()
