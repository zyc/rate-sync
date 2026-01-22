#!/usr/bin/env python3
"""Test the new async with acquire() syntax."""

import asyncio
import sys
import traceback

from ratesync import acquire, get_limiter


async def test_acquire_as_awaitable():
    """Test 1: Using acquire() as simple awaitable (existing behavior)."""
    print("Test 1: acquire() as awaitable")

    # Should work as before
    await acquire("api_search")
    print("  ✓ await acquire('api_search') works\n")


async def test_acquire_as_context_manager():
    """Test 2: Using acquire() as context manager (new behavior)."""
    print("Test 2: acquire() as context manager")

    # New syntax: async with acquire()
    async with acquire("api_search"):
        print("  ✓ Inside context manager - slot acquired")
        await asyncio.sleep(0.1)
        print("  ✓ Code executed successfully")
    print("  ✓ async with acquire('api_search') works\n")


async def test_acquire_context_with_timeout():
    """Test 3: Context manager with timeout override."""
    print("Test 3: Context manager with timeout override")

    # With timeout override
    async with acquire("api_search", timeout=5.0):
        print("  ✓ Context manager with timeout=5.0 works")
        await asyncio.sleep(0.1)
    print("  ✓ Timeout override successful\n")


async def test_multiple_operations_in_one_slot():
    """Test 4: Multiple operations sharing one slot."""
    print("Test 4: Multiple operations in one slot")

    # Multiple operations in one rate-limited block
    async with acquire("api_search"):
        print("  ✓ Operation 1")
        await asyncio.sleep(0.05)
        print("  ✓ Operation 2")
        await asyncio.sleep(0.05)
        print("  ✓ Operation 3")
    print("  ✓ All operations executed in one slot\n")


async def test_comparison_old_vs_new():
    """Test 5: Compare old syntax vs new syntax."""
    print("Test 5: Old vs New syntax comparison")

    # Old syntax (still works)
    print("  Old syntax:")
    limiter = get_limiter("api_search")
    async with limiter.acquire_context():
        print("    ✓ get_limiter() + acquire_context() works")

    # New syntax (simpler!)
    print("  New syntax:")
    async with acquire("api_search"):
        print("    ✓ async with acquire() works")

    print("  ✓ Both syntaxes work!\n")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("TESTING NEW async with acquire() SYNTAX")
    print("=" * 60)
    print()

    try:
        await test_acquire_as_awaitable()
        await test_acquire_as_context_manager()
        await test_acquire_context_with_timeout()
        await test_multiple_operations_in_one_slot()
        await test_comparison_old_vs_new()

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print()
        print("New feature verified:")
        print("  ✓ await acquire('group') - still works")
        print("  ✓ async with acquire('group'): - NEW! works perfectly")
        print("  ✓ Timeout overrides work in context manager")
        print("  ✓ Multiple operations can share one slot")
        print()
        print("Old syntax still works but new syntax is much simpler:")
        print("  OLD: limiter = get_limiter('api')")
        print("       async with limiter.acquire_context():")
        print()
        print("  NEW: async with acquire('api'):")
        print()

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
