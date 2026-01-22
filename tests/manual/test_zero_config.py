#!/usr/bin/env python3
"""Test script to verify zero-configuration usage."""

import asyncio
import sys
import time
import traceback

from ratesync import acquire, get_limiter, list_limiters, list_stores, rate_limited


async def test_auto_loading():
    """Test 1: Auto-loading of rate-sync.toml"""
    print("Test 1: Auto-loading of rate-sync.toml")

    # Import should auto-load config
    stores = list_stores()
    limiters = list_limiters()

    print(f"  Stores loaded: {list(stores.keys())}")
    print(f"  Limiters loaded: {list(limiters.keys())}")

    assert "local_memory" in stores, "Store local_memory not loaded"
    assert "api_payments" in limiters, "Limiter api_payments not loaded"

    print("  ✓ Auto-loading works!\n")


async def test_lazy_init_acquire():
    """Test 2: Lazy initialization with acquire()"""
    print("Test 2: Lazy initialization with acquire()")

    limiter = get_limiter("api_payments")
    print(f"  Before acquire: initialized={limiter.is_initialized}")
    assert not limiter.is_initialized, "Limiter should not be initialized yet"

    # First acquire should auto-initialize
    start = time.time()
    await acquire("api_payments")
    elapsed = time.time() - start
    print(f"  After acquire: initialized={limiter.is_initialized}, took {elapsed:.3f}s")
    assert limiter.is_initialized, "Limiter should be initialized after first acquire"

    # Second acquire should be fast (no re-initialization)
    start = time.time()
    await acquire("api_payments")
    elapsed = time.time() - start
    print(f"  Second acquire: took {elapsed:.3f}s")

    print("  ✓ Lazy initialization in acquire() works!\n")


async def test_lazy_init_decorator():
    """Test 3: Lazy initialization with @rate_limited decorator"""
    print("Test 3: Lazy initialization with @rate_limited decorator")

    @rate_limited("api_users")
    async def fetch_data():
        return "data"

    limiter = get_limiter("api_users")
    print(f"  Before decorated call: initialized={limiter.is_initialized}")
    assert not limiter.is_initialized, "Limiter should not be initialized yet"

    # First call should auto-initialize
    result = await fetch_data()
    print(f"  After decorated call: initialized={limiter.is_initialized}, result={result}")
    assert limiter.is_initialized, "Limiter should be initialized after first call"
    assert result == "data"

    print("  ✓ Lazy initialization in decorator works!\n")


async def test_zero_boilerplate():
    """Test 4: Complete zero-boilerplate usage"""
    print("Test 4: Complete zero-boilerplate usage")

    # Direct usage
    await acquire("api_search")
    print("  ✓ Direct acquire() works")

    # Decorator usage
    @rate_limited("api_search")
    async def my_api_call():
        return "success"

    result = await my_api_call()
    assert result == "success"
    print("  ✓ Decorator works")

    print("  ✓ Zero boilerplate code!\n")


async def test_timeout_override():
    """Test 5: Timeout override"""
    print("Test 5: Timeout override")

    # Use default timeout from config (30s)
    await acquire("api_payments")
    print("  ✓ Default timeout works")

    # Override with custom timeout
    await acquire("api_payments", timeout=5.0)
    print("  ✓ Timeout override works")

    # Decorator with timeout override
    @rate_limited("api_payments", timeout=2.0)
    async def fast_call():
        return "fast"

    result = await fast_call()
    assert result == "fast"
    print("  ✓ Decorator timeout override works\n")


async def test_metrics():
    """Test 6: Metrics collection"""
    print("Test 6: Metrics collection")

    # Make some acquisitions
    for _ in range(3):
        await acquire("api_search")

    limiter = get_limiter("api_search")
    metrics = limiter.get_metrics()

    print(f"  Total acquisitions: {metrics.total_acquisitions}")
    print(f"  Average wait time: {metrics.avg_wait_time_ms:.2f}ms")
    print(f"  Max wait time: {metrics.max_wait_time_ms:.2f}ms")
    print(f"  Timeouts: {metrics.timeouts}")

    assert metrics.total_acquisitions >= 3, "Should have at least 3 acquisitions"
    print("  ✓ Metrics work!\n")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("ZERO-CONFIGURATION RATE-SYNC TESTS")
    print("=" * 60)
    print()

    try:
        await test_auto_loading()
        await test_lazy_init_acquire()
        await test_lazy_init_decorator()
        await test_zero_boilerplate()
        await test_timeout_override()
        await test_metrics()

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print()
        print("Zero-configuration features verified:")
        print("  ✓ Auto-loading from rate-sync.toml")
        print("  ✓ Lazy initialization on first acquire()")
        print("  ✓ Lazy initialization in decorators")
        print("  ✓ Timeout overrides")
        print("  ✓ Metrics collection")
        print("  ✓ Zero boilerplate code required")
        print()

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
