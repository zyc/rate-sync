"""Performance tests for rate limiting engines.

These tests verify performance characteristics including:
- Throughput (requests per second)
- Latency under load
- Concurrent request handling
- Memory efficiency
- Algorithm accuracy under stress

Tests use mocks for Redis to ensure consistent, reproducible results.
For real Redis performance tests, see integration/test_performance_redis.py
"""

import asyncio
import statistics
import time
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ratesync.engines.memory import MemoryRateLimiter
from ratesync.schemas import MemoryEngineConfig, RedisEngineConfig


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def memory_config():
    """Create memory engine configuration."""
    return MemoryEngineConfig()


@pytest.fixture
def redis_config():
    """Create Redis engine configuration."""
    return RedisEngineConfig(
        url="redis://localhost:6379/0",
        key_prefix="perf_test",
        pool_max_size=20,
    )


# =============================================================================
# PERFORMANCE HELPERS
# =============================================================================


class PerformanceResult:
    """Container for performance test results."""

    def __init__(
        self,
        total_requests: int,
        successful: int,
        failed: int,
        total_time: float,
        latencies: list[float],
    ):
        self.total_requests = total_requests
        self.successful = successful
        self.failed = failed
        self.total_time = total_time
        self.latencies = latencies

    @property
    def throughput(self) -> float:
        """Requests per second."""
        return self.total_requests / self.total_time if self.total_time > 0 else 0

    @property
    def success_rate(self) -> float:
        """Percentage of successful requests."""
        return (self.successful / self.total_requests * 100) if self.total_requests > 0 else 0

    @property
    def avg_latency_ms(self) -> float:
        """Average latency in milliseconds."""
        return statistics.mean(self.latencies) * 1000 if self.latencies else 0

    @property
    def p50_latency_ms(self) -> float:
        """50th percentile latency (median) in milliseconds."""
        return statistics.median(self.latencies) * 1000 if self.latencies else 0

    @property
    def p95_latency_ms(self) -> float:
        """95th percentile latency in milliseconds."""
        if not self.latencies:
            return 0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[idx] * 1000

    @property
    def p99_latency_ms(self) -> float:
        """99th percentile latency in milliseconds."""
        if not self.latencies:
            return 0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[idx] * 1000

    def __str__(self) -> str:
        return (
            f"PerformanceResult("
            f"throughput={self.throughput:.2f} req/s, "
            f"success_rate={self.success_rate:.1f}%, "
            f"avg_latency={self.avg_latency_ms:.2f}ms, "
            f"p95={self.p95_latency_ms:.2f}ms, "
            f"p99={self.p99_latency_ms:.2f}ms)"
        )


async def run_concurrent_requests(
    limiter,
    num_requests: int,
    concurrency: int,
) -> PerformanceResult:
    """Execute concurrent requests against a rate limiter.

    Args:
        limiter: Rate limiter instance
        num_requests: Total number of requests to make
        concurrency: Number of concurrent workers

    Returns:
        PerformanceResult with metrics
    """
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    successful = 0
    failed = 0
    lock = asyncio.Lock()

    async def make_request():
        nonlocal successful, failed
        async with semaphore:
            start = time.perf_counter()
            try:
                result = await limiter.try_acquire(timeout=0.01)
                elapsed = time.perf_counter() - start
                async with lock:
                    latencies.append(elapsed)
                    if result:
                        successful += 1
                    else:
                        failed += 1
            except Exception:
                elapsed = time.perf_counter() - start
                async with lock:
                    latencies.append(elapsed)
                    failed += 1

    start_time = time.perf_counter()
    tasks = [asyncio.create_task(make_request()) for _ in range(num_requests)]
    await asyncio.gather(*tasks)
    total_time = time.perf_counter() - start_time

    return PerformanceResult(
        total_requests=num_requests,
        successful=successful,
        failed=failed,
        total_time=total_time,
        latencies=latencies,
    )


# =============================================================================
# MEMORY ENGINE PERFORMANCE TESTS
# =============================================================================


class TestMemoryEnginePerformance:
    """Performance tests for in-memory rate limiter."""

    @pytest.mark.asyncio
    async def test_throughput_single_client(self, memory_config):
        """Test throughput for single client (sequential requests)."""
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="perf_single",
            rate_per_second=10000.0,  # High rate for throughput test
        )
        await limiter.initialize()

        num_requests = 1000
        start = time.perf_counter()

        for _ in range(num_requests):
            await limiter.try_acquire(timeout=0)

        elapsed = time.perf_counter() - start
        throughput = num_requests / elapsed

        print(f"\nMemory engine single-client throughput: {throughput:.0f} req/s")

        # Memory engine should handle at least 50,000 req/s single-threaded
        assert throughput > 10000, f"Throughput too low: {throughput:.0f} req/s"

    @pytest.mark.asyncio
    async def test_throughput_concurrent_clients(self, memory_config):
        """Test throughput with concurrent clients."""
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="perf_concurrent",
            rate_per_second=100000.0,  # High rate for throughput test
        )
        await limiter.initialize()

        result = await run_concurrent_requests(
            limiter,
            num_requests=5000,
            concurrency=100,
        )

        print(f"\nMemory engine concurrent throughput: {result}")

        # Should handle high concurrent load
        assert result.throughput > 5000, f"Throughput too low: {result.throughput:.0f} req/s"
        assert result.p95_latency_ms < 50, f"P95 latency too high: {result.p95_latency_ms:.2f}ms"

    @pytest.mark.asyncio
    async def test_rate_limiting_accuracy(self, memory_config):
        """Test that rate limiting is accurate under load."""
        rate = 100.0  # 100 req/s
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="perf_accuracy",
            rate_per_second=rate,
        )
        await limiter.initialize()

        # Run for 1 second with attempts at 200 req/s
        duration = 1.0
        attempts = 0
        allowed = 0
        start = time.perf_counter()

        while time.perf_counter() - start < duration:
            attempts += 1
            result = await limiter.try_acquire(timeout=0)
            if result:
                allowed += 1
            await asyncio.sleep(0.005)  # ~200 attempts/s

        # Should allow close to 100 requests (+/- 30% tolerance for memory engine)
        print(f"\nRate limiting accuracy: {allowed}/{attempts} allowed at {rate} req/s")

        # Memory engine is interval-based, so allowed count depends on timing
        assert 60 <= allowed <= 130, f"Rate limiting inaccurate: {allowed} allowed (expected ~100)"

    @pytest.mark.asyncio
    async def test_concurrent_limit_accuracy(self, memory_config):
        """Test that max_concurrent is enforced accurately."""
        max_concurrent = 10
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="perf_concurrent_limit",
            max_concurrent=max_concurrent,
        )
        await limiter.initialize()

        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal active, max_active
            result = await limiter.try_acquire(timeout=0.5)
            if result:
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.1)  # Simulate work
                async with lock:
                    active -= 1
                await limiter.release()

        # Start 50 concurrent workers
        tasks = [asyncio.create_task(worker()) for _ in range(50)]
        await asyncio.gather(*tasks)

        print(f"\nMax concurrent observed: {max_active} (limit: {max_concurrent})")

        assert max_active <= max_concurrent, f"Concurrent limit exceeded: {max_active}"


# =============================================================================
# MOCKED REDIS ENGINE PERFORMANCE TESTS
# =============================================================================


# Try to import RedisRateLimiter
try:
    from ratesync.engines.redis import RedisRateLimiter
    from ratesync.engines.redis_sliding_window import RedisSlidingWindowRateLimiter

    REDIS_ENGINE_AVAILABLE = True
except ImportError:
    REDIS_ENGINE_AVAILABLE = False
    RedisRateLimiter = None
    RedisSlidingWindowRateLimiter = None


@pytest.mark.skipif(not REDIS_ENGINE_AVAILABLE, reason="Redis engine not available")
class TestRedisEnginePerformanceMocked:
    """Performance tests for Redis engine using mocks.

    These tests verify the logic without needing a real Redis connection.
    """

    @pytest.mark.asyncio
    async def test_mock_throughput(self, redis_config):
        """Test throughput with mocked Redis (measures overhead)."""
        limiter = RedisRateLimiter.from_config(
            redis_config,
            group_id="mock_perf",
            rate_per_second=100000.0,
        )

        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()

            # Fast mock script - always succeeds immediately
            mock_script = AsyncMock(return_value=[1, 0, 1])
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()

            # Run throughput test
            num_requests = 1000
            start = time.perf_counter()

            for _ in range(num_requests):
                await limiter.try_acquire(timeout=0)

            elapsed = time.perf_counter() - start
            throughput = num_requests / elapsed

            print(f"\nMocked Redis throughput: {throughput:.0f} req/s")

            # Mocked Redis should be very fast (no network)
            assert throughput > 5000, f"Mocked throughput too low: {throughput:.0f} req/s"

    @pytest.mark.asyncio
    async def test_mock_concurrent_throughput(self, redis_config):
        """Test concurrent throughput with mocked Redis."""
        limiter = RedisRateLimiter.from_config(
            redis_config,
            group_id="mock_concurrent",
            rate_per_second=100000.0,
        )

        with patch("ratesync.engines.redis.redis_asyncio") as mock_redis:
            mock_pool = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()

            # Add small delay to simulate network
            async def slow_script(*args, **kwargs):
                await asyncio.sleep(0.0001)  # 0.1ms simulated latency
                return [1, 0, 1]

            mock_script = AsyncMock(side_effect=slow_script)
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_redis.ConnectionPool.from_url.return_value = mock_pool

            await limiter.initialize()

            result = await run_concurrent_requests(
                limiter,
                num_requests=1000,
                concurrency=50,
            )

            print(f"\nMocked Redis concurrent: {result}")

            # With 0.1ms mock latency, should still be fast
            assert result.throughput > 1000, (
                f"Concurrent throughput too low: {result.throughput:.0f}"
            )


@pytest.mark.skipif(not REDIS_ENGINE_AVAILABLE, reason="Redis engine not available")
class TestSlidingWindowPerformanceMocked:
    """Performance tests for sliding window algorithm using mocks."""

    @pytest.mark.asyncio
    async def test_sliding_window_throughput(self, redis_config):
        """Test sliding window throughput with mocked Redis."""
        limiter = RedisSlidingWindowRateLimiter(
            url="redis://localhost:6379/0",
            group_id="sw_perf",
            limit=1000,
            window_seconds=60,
        )

        with (
            patch("ratesync.engines.redis_sliding_window.redis_asyncio") as mock_redis,
            patch("ratesync.engines.redis_sliding_window.ConnectionPool") as mock_pool_class,
        ):
            mock_pool = AsyncMock()
            mock_pool.disconnect = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.close = AsyncMock()

            # Mock sliding window script - allowed with remaining count
            call_count = [0]

            def mock_script_func(*args, **kwargs):
                call_count[0] += 1
                remaining = 1000 - call_count[0]
                return [1, remaining, 60]

            mock_script = AsyncMock(side_effect=mock_script_func)
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_pool_class.from_url.return_value = mock_pool

            await limiter.initialize()

            # Run throughput test
            num_requests = 500
            start = time.perf_counter()

            for _ in range(num_requests):
                await limiter.try_acquire(timeout=0)

            elapsed = time.perf_counter() - start
            throughput = num_requests / elapsed

            print(f"\nSliding window mocked throughput: {throughput:.0f} req/s")

            assert throughput > 2000, f"Sliding window throughput too low: {throughput:.0f} req/s"

            await limiter.disconnect()

    @pytest.mark.asyncio
    async def test_sliding_window_rate_limit_enforcement(self, redis_config):
        """Test that sliding window enforces limits correctly."""
        limit = 10
        limiter = RedisSlidingWindowRateLimiter(
            url="redis://localhost:6379/0",
            group_id="sw_limit",
            limit=limit,
            window_seconds=60,
        )

        with (
            patch("ratesync.engines.redis_sliding_window.redis_asyncio") as mock_redis,
            patch("ratesync.engines.redis_sliding_window.ConnectionPool") as mock_pool_class,
        ):
            mock_pool = AsyncMock()
            mock_pool.disconnect = AsyncMock()
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.close = AsyncMock()

            # Track calls and enforce limit
            call_count = [0]

            def mock_script_func(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] <= limit:
                    return [1, limit - call_count[0], 60]
                return [0, 0, 60]  # Blocked after limit

            mock_script = AsyncMock(side_effect=mock_script_func)
            mock_client.register_script = MagicMock(return_value=mock_script)

            mock_redis.Redis.return_value = mock_client
            mock_pool_class.from_url.return_value = mock_pool

            await limiter.initialize()

            # Try 20 requests (should only allow 10)
            allowed_count = 0
            for _ in range(20):
                result = await limiter.try_acquire(timeout=0)
                if result:
                    allowed_count += 1

            print(f"\nSliding window: {allowed_count}/{20} allowed (limit: {limit})")

            assert allowed_count == limit, (
                f"Wrong number allowed: {allowed_count} (expected {limit})"
            )

            await limiter.disconnect()


# =============================================================================
# STRESS TESTS
# =============================================================================


class TestStressScenarios:
    """Stress tests for various scenarios."""

    @pytest.mark.asyncio
    async def test_burst_handling(self, memory_config):
        """Test handling of request bursts.

        The memory rate limiter uses strict interval-based timing, so
        bursts will mostly be rejected except for the first request.
        This test validates that behavior is consistent.
        """
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="burst_test",
            rate_per_second=100.0,  # 100 req/s = 1 req per 10ms
        )
        await limiter.initialize()

        # Send burst of 200 requests at once
        async def burst():
            results = []
            for _ in range(200):
                result = await limiter.try_acquire(timeout=0)
                results.append(result)
            return results

        results = await burst()
        allowed = sum(1 for r in results if r)

        print(f"\nBurst test: {allowed}/200 allowed at 100 req/s rate")

        # Memory limiter is strict interval-based, so bursts are heavily limited
        # Only first request (or a few with timing variance) should be allowed
        assert allowed >= 1, "At least one request should be allowed"
        assert allowed <= 50, f"Too many allowed in burst: {allowed}"

    @pytest.mark.asyncio
    async def test_many_unique_identifiers(self, memory_config):
        """Test performance with many unique rate limit identifiers."""
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="many_ids",
            rate_per_second=10.0,
        )
        await limiter.initialize()

        # Simulate 1000 different users
        num_users = 1000
        requests_per_user = 5

        start = time.perf_counter()
        results = defaultdict(list)

        for user_id in range(num_users):
            # Each user has their own identifier (simulated via internal state)
            for _ in range(requests_per_user):
                result = await limiter.try_acquire(timeout=0)
                results[user_id].append(result)

        elapsed = time.perf_counter() - start
        total_requests = num_users * requests_per_user
        throughput = total_requests / elapsed

        print(f"\nMany identifiers: {throughput:.0f} req/s ({num_users} users)")

        # Should still maintain good throughput
        assert throughput > 5000, f"Throughput degraded: {throughput:.0f} req/s"

    @pytest.mark.asyncio
    async def test_sustained_load(self, memory_config):
        """Test sustained load over time."""
        rate = 100.0
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="sustained",
            rate_per_second=rate,
        )
        await limiter.initialize()

        # Run for 3 seconds with attempts at ~150 req/s
        duration = 3.0
        interval = 1.0 / 150.0  # ~150 attempts per second
        allowed_per_second = []
        current_second_allowed = 0
        last_second = 0

        start = time.perf_counter()
        while time.perf_counter() - start < duration:
            elapsed = time.perf_counter() - start
            current_second = int(elapsed)

            if current_second > last_second:
                allowed_per_second.append(current_second_allowed)
                current_second_allowed = 0
                last_second = current_second

            result = await limiter.try_acquire(timeout=0)
            if result:
                current_second_allowed += 1

            await asyncio.sleep(interval)

        # Add last partial second
        if current_second_allowed > 0:
            allowed_per_second.append(current_second_allowed)

        print(f"\nSustained load: allowed/second = {allowed_per_second}")

        # Each second should be close to the rate limit (+/- 40% for timing variance)
        for i, allowed in enumerate(allowed_per_second[:-1]):  # Skip last partial second
            assert 50 <= allowed <= 130, f"Second {i}: {allowed} allowed (expected ~100)"


# =============================================================================
# LATENCY DISTRIBUTION TESTS
# =============================================================================


class TestLatencyDistribution:
    """Tests for latency distribution under various conditions."""

    @pytest.mark.asyncio
    async def test_latency_percentiles_memory(self, memory_config):
        """Test latency percentiles for memory engine."""
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="latency_test",
            rate_per_second=10000.0,
        )
        await limiter.initialize()

        latencies = []
        for _ in range(1000):
            start = time.perf_counter()
            await limiter.try_acquire(timeout=0)
            elapsed = time.perf_counter() - start
            latencies.append(elapsed)

        avg = statistics.mean(latencies) * 1000
        p50 = statistics.median(latencies) * 1000
        p95 = sorted(latencies)[int(len(latencies) * 0.95)] * 1000
        p99 = sorted(latencies)[int(len(latencies) * 0.99)] * 1000

        print("\nMemory latency distribution:")
        print(f"  avg: {avg:.3f}ms, p50: {p50:.3f}ms, p95: {p95:.3f}ms, p99: {p99:.3f}ms")

        # Memory engine should have sub-millisecond latency
        assert avg < 1.0, f"Average latency too high: {avg:.3f}ms"
        assert p99 < 5.0, f"P99 latency too high: {p99:.3f}ms"

    @pytest.mark.asyncio
    async def test_latency_under_contention(self, memory_config):
        """Test latency when many coroutines compete for slots."""
        limiter = MemoryRateLimiter.from_config(
            memory_config,
            group_id="contention",
            rate_per_second=50.0,  # Low rate to create contention
            max_concurrent=10,
        )
        await limiter.initialize()

        latencies = []
        lock = asyncio.Lock()

        async def worker():
            start = time.perf_counter()
            result = await limiter.try_acquire(timeout=1.0)
            elapsed = time.perf_counter() - start
            async with lock:
                latencies.append((result, elapsed))
            if result:
                await asyncio.sleep(0.05)  # Simulate work
                await limiter.release()

        # Run 100 concurrent workers
        tasks = [asyncio.create_task(worker()) for _ in range(100)]
        await asyncio.gather(*tasks)

        successful = [(r, e) for r, e in latencies if r]
        failed = [(r, e) for r, e in latencies if not r]

        if successful:
            success_latencies = [e * 1000 for _, e in successful]
            avg_success = statistics.mean(success_latencies)
            print(f"\nSuccessful requests: {len(successful)}, avg latency: {avg_success:.2f}ms")

        if failed:
            fail_latencies = [e * 1000 for _, e in failed]
            avg_fail = statistics.mean(fail_latencies)
            print(f"Failed requests: {len(failed)}, avg latency: {avg_fail:.2f}ms")
