#!/usr/bin/env python3
"""Test the clone_limiter() function."""

import asyncio

from ratesync import (
    acquire,
    clone_limiter,
    configure_limiter,
    configure_store,
    list_limiters,
)
from ratesync.exceptions import LimiterNotFoundError


async def test_clone_exact():
    """Test 1: Clone limiter with exact same configuration."""
    print("Test 1: Clone limiter with exact configuration")

    # Configure original limiter
    configure_store("local", strategy="memory")
    configure_limiter("original", store_id="local", rate_per_second=1.0, timeout=30.0)

    # Clone it
    clone_limiter("original", "clone1")

    # Verify clone has same config
    limiters = list_limiters()
    assert "clone1" in limiters
    assert limiters["clone1"]["rate_per_second"] == 1.0
    assert limiters["clone1"]["timeout"] == 30.0
    assert limiters["clone1"]["backend"] == "local"

    # Test that clone works
    await acquire("clone1")
    print("  ✓ Cloned limiter works with exact same config\n")


async def test_clone_with_rate_override():
    """Test 2: Clone limiter with rate override."""
    print("Test 2: Clone with rate_per_second override")

    # Clone with different rate
    clone_limiter("original", "clone2", rate_per_second=2.0)

    # Verify override
    limiters = list_limiters()
    assert limiters["clone2"]["rate_per_second"] == 2.0
    assert limiters["clone2"]["timeout"] == 30.0  # Should keep original timeout

    # Test that clone works
    await acquire("clone2")
    print("  ✓ Cloned limiter works with overridden rate\n")


async def test_clone_with_timeout_override():
    """Test 3: Clone limiter with timeout override."""
    print("Test 3: Clone with timeout override")

    # Clone with different timeout
    clone_limiter("original", "clone3", timeout=10.0)

    # Verify override
    limiters = list_limiters()
    assert limiters["clone3"]["rate_per_second"] == 1.0  # Should keep original rate
    assert limiters["clone3"]["timeout"] == 10.0

    # Test that clone works
    await acquire("clone3")
    print("  ✓ Cloned limiter works with overridden timeout\n")


async def test_clone_with_both_overrides():
    """Test 4: Clone limiter with both rate and timeout overrides."""
    print("Test 4: Clone with both overrides")

    # Clone with both overrides
    clone_limiter("original", "clone4", rate_per_second=5.0, timeout=15.0)

    # Verify overrides
    limiters = list_limiters()
    assert limiters["clone4"]["rate_per_second"] == 5.0
    assert limiters["clone4"]["timeout"] == 15.0

    # Test that clone works
    await acquire("clone4")
    print("  ✓ Cloned limiter works with both overrides\n")


async def test_clone_nonexistent_source():
    """Test 5: Cloning from non-existent source raises error."""
    print("Test 5: Clone from non-existent source")

    try:
        clone_limiter("does_not_exist", "clone5")
        assert False, "Should have raised LimiterNotFoundError"
    except LimiterNotFoundError as e:
        assert "does_not_exist" in str(e)
        print("  ✓ Correctly raises LimiterNotFoundError for non-existent source\n")


async def test_clone_to_existing_id():
    """Test 6: Cloning to existing ID raises error."""
    print("Test 6: Clone to existing limiter ID")

    try:
        # Try to clone to an ID that already exists
        clone_limiter("original", "clone1")  # clone1 already exists
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "already exists" in str(e)
        print("  ✓ Correctly raises ValueError when target ID already exists\n")


async def test_clone_usage_example():
    """Test 7: Real-world usage example (payments multi-IP scenario)."""
    print("Test 7: Real-world usage - multiple IP-based limiters")

    # Setup base limiter
    configure_limiter("payments", store_id="local", rate_per_second=1.0, timeout=30.0)

    # Simulate creating limiters for different IPs
    instance_ips = ["192.168.1.10", "192.168.1.11", "192.168.1.12"]

    for ip in instance_ips:
        limiter_id = f"payments-{ip.replace('.', '-')}"
        clone_limiter("payments", limiter_id)
        print(f"  ✓ Created limiter '{limiter_id}'")

    # Verify all were created
    limiters = list_limiters()
    for ip in instance_ips:
        limiter_id = f"payments-{ip.replace('.', '-')}"
        assert limiter_id in limiters
        assert limiters[limiter_id]["rate_per_second"] == 1.0

    # Test usage
    await acquire("payments-192-168-1-10")
    await acquire("payments-192-168-1-11")
    await acquire("payments-192-168-1-12")

    print("  ✓ All IP-based limiters work correctly\n")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing clone_limiter() function")
    print("=" * 60 + "\n")

    await test_clone_exact()
    await test_clone_with_rate_override()
    await test_clone_with_timeout_override()
    await test_clone_with_both_overrides()
    await test_clone_nonexistent_source()
    await test_clone_to_existing_id()
    await test_clone_usage_example()

    print("=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
