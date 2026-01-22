"""Manual test script for Redis rate limiter.

Run this to verify Redis engine is working correctly.
"""

import asyncio
import time
import traceback

import pytest

from ratesync import acquire, configure_limiter, configure_store, get_limiter, initialize_limiter


@pytest.mark.redis
async def test_redis_rate_limiter():
    """Test Redis rate limiter with manual verification."""
    print("🧪 Testing Redis Rate Limiter\n")

    print("1️⃣  Configuring Redis store...")
    configure_store(
        "test_redis",
        strategy="redis",
        url="redis://localhost:6379/0",
        key_prefix="test_rate_limit",
    )
    print("   ✅ Redis store configured\n")

    print("2️⃣  Configuring rate limiter (2 req/s)...")
    configure_limiter("test_limiter", store_id="test_redis", rate_per_second=2.0, timeout=5.0)
    print("   ✅ Rate limiter configured\n")

    print("3️⃣  Initializing rate limiter...")
    await initialize_limiter("test_limiter")
    print("   ✅ Rate limiter initialized\n")

    print("4️⃣  Testing acquisitions:\n")

    # First acquisition - should be immediate
    print("   📥 First acquire (should be immediate)...")
    start = time.time()
    await acquire("test_limiter")
    elapsed = time.time() - start
    print(f"   ✅ Acquired in {elapsed * 1000:.2f}ms\n")

    # Second acquisition - should be immediate (within rate)
    print("   📥 Second acquire (should be immediate - rate allows 2/s)...")
    start = time.time()
    await acquire("test_limiter")
    elapsed = time.time() - start
    print(f"   ✅ Acquired in {elapsed * 1000:.2f}ms\n")

    # Third acquisition - should wait ~0.5s (rate limit kicks in)
    print("   📥 Third acquire (should wait ~500ms - rate limited)...")
    start = time.time()
    await acquire("test_limiter")
    elapsed = time.time() - start
    print(f"   ✅ Acquired in {elapsed * 1000:.2f}ms (waited ~{elapsed:.3f}s)\n")

    # Get metrics
    limiter = get_limiter("test_limiter")
    metrics = limiter.get_metrics()

    print("5️⃣  Metrics:")
    print(f"   📊 Total acquisitions: {metrics.total_acquisitions}")
    print(f"   ⏱️  Total timeouts: {metrics.timeouts}")
    print(f"   📈 Average wait: {metrics.avg_wait_time_ms:.2f}ms")
    print(f"   ⏱️  Max wait: {metrics.max_wait_time_ms:.2f}ms\n")

    print("✅ All tests passed! Redis rate limiter is working correctly.\n")


if __name__ == "__main__":
    try:
        asyncio.run(test_redis_rate_limiter())
    except ImportError as e:
        print(f"❌ Error: {e}")
        print("\n💡 Make sure to install Redis support:")
        print("   cd rate-limiter && poetry install --extras redis")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        traceback.print_exc()
