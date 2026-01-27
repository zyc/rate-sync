"""Global test configuration and fixtures for rate-sync.

This module provides common fixtures and pytest configuration used across
all test modules. It handles environment detection, URL configuration,
and availability checks for different backends.
"""

from __future__ import annotations

import os

import pytest


# =============================================================================
# ENVIRONMENT DETECTION
# =============================================================================


def is_redis_available() -> bool:
    """Check if Redis library is installed."""
    try:
        import redis.asyncio  # noqa: F401

        return True
    except ImportError:
        return False


def is_postgres_available() -> bool:
    """Check if asyncpg library is installed."""
    try:
        import asyncpg  # noqa: F401

        return True
    except ImportError:
        return False


REDIS_AVAILABLE = is_redis_available()
POSTGRES_AVAILABLE = is_postgres_available()


# =============================================================================
# URL FIXTURES
# =============================================================================


@pytest.fixture
def redis_url() -> str:
    """Redis URL for testing.

    Reads from REDIS_URL environment variable, with fallback to localhost.
    Supports REDIS_PASSWORD for authenticated connections.
    """
    url = os.environ.get("REDIS_URL", "")
    if url:
        return url

    # Build URL from components
    password = os.getenv("REDIS_PASSWORD", "").strip()
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    db = os.getenv("REDIS_DB", "0")

    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


@pytest.fixture
def postgres_url() -> str:
    """PostgreSQL URL for testing.

    Reads from POSTGRES_URL environment variable, with fallback to localhost.
    """
    return os.environ.get(
        "POSTGRES_URL",
        os.environ.get(
            "POSTGRES_INTEGRATION_URL",
            "postgresql://postgres:postgres@localhost:5432/ratesync",
        ),
    )


# =============================================================================
# SKIP HELPERS
# =============================================================================


def skip_if_no_redis():
    """Skip test if Redis library is not available."""
    if not REDIS_AVAILABLE:
        pytest.skip("Redis library not installed")


def skip_if_no_postgres():
    """Skip test if asyncpg library is not available."""
    if not POSTGRES_AVAILABLE:
        pytest.skip("asyncpg library not installed")
