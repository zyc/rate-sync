"""Engine-specific tests.

This package contains tests for implementation-specific behavior
that differs between engines. These tests complement the compliance
tests by covering engine-specific features like:

- Connection pooling (Redis, PostgreSQL)
- Lua script behavior (Redis)
- Table management (PostgreSQL)
- Memory management (Memory)

See tests/compliance/ for the standardized test suite that all
engines must pass.
"""
