"""Tests for new ratesync features.

Tests for:
- RateLimitResult and MultiLimiterResult schemas
- get_or_clone_limiter, check_limiter, check_multi_limiters
- hash_identifier and combine_identifiers
- FastAPI config for trusted proxies

Note: hash_identifier and combine_identifiers are now imported from the
domain layer, but also still work from contrib.fastapi for backward compatibility.
"""

from __future__ import annotations

import time

import pytest

from ratesync import (
    MultiLimiterResult,
    RateLimitResult,
    check_limiter,
    check_multi_limiters,
    combine_identifiers,
    configure_limiter,
    configure_store,
    get_or_clone_limiter,
    hash_identifier,
)
from ratesync.config import FastAPIConfig, configure_fastapi, get_fastapi_config


# =============================================================================
# RateLimitResult Tests
# =============================================================================


class TestRateLimitResult:
    """Test RateLimitResult dataclass."""

    def test_create_allowed_result(self):
        """Test creating an allowed result."""
        result = RateLimitResult(
            allowed=True,
            limit=100,
            remaining=99,
            reset_in=60.0,
        )

        assert result.allowed is True
        assert result.limit == 100
        assert result.remaining == 99
        assert result.reset_in == 60.0
        assert result.limiter_id is None

    def test_create_blocked_result(self):
        """Test creating a blocked result."""
        result = RateLimitResult(
            allowed=False,
            limit=5,
            remaining=0,
            reset_in=300.0,
            limiter_id="login:192.168.1.1",
        )

        assert result.allowed is False
        assert result.remaining == 0
        assert result.limiter_id == "login:192.168.1.1"

    def test_reset_at_property(self):
        """Test reset_at property calculates correct timestamp."""
        before = int(time.time())
        result = RateLimitResult(
            allowed=True,
            limit=100,
            remaining=50,
            reset_in=120.0,
        )
        after = int(time.time())

        # reset_at should be within expected range
        assert before + 120 <= result.reset_at <= after + 120

    def test_retry_after_property(self):
        """Test retry_after returns at least 1 second."""
        result = RateLimitResult(
            allowed=False,
            limit=10,
            remaining=0,
            reset_in=0.5,  # Less than 1 second
        )

        # Should return at least 1
        assert result.retry_after >= 1

    def test_retry_after_with_longer_reset(self):
        """Test retry_after with longer reset time."""
        result = RateLimitResult(
            allowed=False,
            limit=10,
            remaining=0,
            reset_in=300.0,
        )

        assert result.retry_after == 300

    def test_result_is_frozen(self):
        """Test that result is immutable (frozen dataclass)."""
        result = RateLimitResult(
            allowed=True,
            limit=100,
            remaining=99,
            reset_in=60.0,
        )

        with pytest.raises(AttributeError):
            result.allowed = False  # type: ignore[misc]


# =============================================================================
# MultiLimiterResult Tests
# =============================================================================


class TestMultiLimiterResult:
    """Test MultiLimiterResult dataclass."""

    def test_create_allowed_result(self):
        """Test creating an allowed multi-limiter result."""
        result = MultiLimiterResult(
            allowed=True,
            limit=100,
            remaining=50,
            reset_in=60.0,
        )

        assert result.allowed is True
        assert result.limit == 100
        assert result.remaining == 50
        assert result.blocking_limiter_id is None
        assert not result.results

    def test_from_results_all_allowed(self):
        """Test from_results when all limiters allow."""
        results = [
            RateLimitResult(allowed=True, limit=100, remaining=80, reset_in=60.0, limiter_id="ip"),
            RateLimitResult(
                allowed=True, limit=50, remaining=40, reset_in=120.0, limiter_id="user"
            ),
        ]

        combined = MultiLimiterResult.from_results(results)

        assert combined.allowed is True
        assert combined.remaining == 40  # Minimum
        assert combined.reset_in == 120.0  # Maximum
        assert combined.blocking_limiter_id is None
        assert len(combined.results) == 2

    def test_from_results_one_blocked(self):
        """Test from_results when one limiter blocks."""
        results = [
            RateLimitResult(allowed=True, limit=100, remaining=80, reset_in=60.0, limiter_id="ip"),
            RateLimitResult(
                allowed=False, limit=5, remaining=0, reset_in=300.0, limiter_id="login"
            ),
        ]

        combined = MultiLimiterResult.from_results(results)

        assert combined.allowed is False
        assert combined.remaining == 0  # Minimum
        assert combined.reset_in == 300.0  # Maximum
        assert combined.blocking_limiter_id == "login"

    def test_from_results_empty_list(self):
        """Test from_results with empty list."""
        combined = MultiLimiterResult.from_results([])

        assert combined.allowed is True
        assert combined.limit == 1000
        assert combined.remaining == 999
        assert combined.blocking_limiter_id is None

    def test_from_results_multiple_blocked(self):
        """Test from_results when multiple limiters block."""
        results = [
            RateLimitResult(
                allowed=False, limit=5, remaining=0, reset_in=300.0, limiter_id="login"
            ),
            RateLimitResult(allowed=False, limit=10, remaining=0, reset_in=60.0, limiter_id="ip"),
        ]

        combined = MultiLimiterResult.from_results(results)

        assert combined.allowed is False
        # Should return first blocking limiter
        assert combined.blocking_limiter_id == "login"
        assert combined.reset_in == 300.0  # Maximum

    def test_retry_after_property(self):
        """Test retry_after property."""
        result = MultiLimiterResult(
            allowed=False,
            limit=5,
            remaining=0,
            reset_in=250.0,
        )

        assert result.retry_after == 250


# =============================================================================
# hash_identifier Tests
# =============================================================================


class TestHashIdentifier:
    """Test hash_identifier function."""

    def test_basic_hash(self):
        """Test basic identifier hashing."""
        result = hash_identifier("user@example.com")

        assert isinstance(result, str)
        assert len(result) == 16  # Default length

    def test_hash_is_deterministic(self):
        """Test that same input produces same hash."""
        hash1 = hash_identifier("test@email.com")
        hash2 = hash_identifier("test@email.com")

        assert hash1 == hash2

    def test_hash_normalizes_case(self):
        """Test that email is normalized to lowercase."""
        hash_lower = hash_identifier("user@example.com")
        hash_upper = hash_identifier("USER@EXAMPLE.COM")
        hash_mixed = hash_identifier("User@Example.Com")

        assert hash_lower == hash_upper == hash_mixed

    def test_hash_normalizes_whitespace(self):
        """Test that whitespace is stripped."""
        hash_clean = hash_identifier("user@example.com")
        hash_spaces = hash_identifier("  user@example.com  ")

        assert hash_clean == hash_spaces

    def test_custom_length(self):
        """Test custom hash length."""
        short_hash = hash_identifier("test@email.com", length=8)
        long_hash = hash_identifier("test@email.com", length=32)

        assert len(short_hash) == 8
        assert len(long_hash) == 32
        # Shorter should be prefix of longer
        assert long_hash.startswith(short_hash)

    def test_with_salt(self):
        """Test that salt produces different hash."""
        hash_no_salt = hash_identifier("user@example.com")
        hash_login = hash_identifier("user@example.com", salt="login")
        hash_reset = hash_identifier("user@example.com", salt="password_reset")

        assert hash_no_salt != hash_login
        assert hash_login != hash_reset

    def test_different_algorithms(self):
        """Test different hash algorithms."""
        sha256 = hash_identifier("test", algorithm="sha256")
        sha512 = hash_identifier("test", algorithm="sha512")

        # Different algorithms should produce different hashes
        assert sha256 != sha512
        assert len(sha256) == 16
        assert len(sha512) == 16


# =============================================================================
# combine_identifiers Tests
# =============================================================================


class TestCombineIdentifiers:
    """Test combine_identifiers function."""

    def test_basic_combine(self):
        """Test basic identifier combination."""
        result = combine_identifiers("192.168.1.1", "abc123")

        assert result == "192.168.1.1:abc123"

    def test_multiple_identifiers(self):
        """Test combining multiple identifiers."""
        result = combine_identifiers("api", "192.168.1.1", "user123")

        assert result == "api:192.168.1.1:user123"

    def test_custom_separator(self):
        """Test custom separator."""
        result = combine_identifiers("a", "b", "c", separator="-")

        assert result == "a-b-c"

    def test_skips_empty_values(self):
        """Test that empty values are skipped."""
        result = combine_identifiers("a", "", "b", "", "c")

        assert result == "a:b:c"

    def test_single_identifier(self):
        """Test with single identifier."""
        result = combine_identifiers("single")

        assert result == "single"


# =============================================================================
# FastAPIConfig Tests
# =============================================================================


class TestFastAPIConfig:
    """Test FastAPI configuration."""

    def test_default_config(self):
        """Test default FastAPI config values."""
        config = FastAPIConfig()

        assert config.trusted_proxy_networks is None

    def test_configure_trusted_networks(self):
        """Test configuring trusted proxy networks."""
        config = FastAPIConfig()
        networks = ["10.0.0.0/8", "192.168.0.0/16"]

        config.configure(trusted_proxy_networks=networks)

        assert config.trusted_proxy_networks == networks

    def test_get_fastapi_config_singleton(self):
        """Test that get_fastapi_config returns singleton."""
        config1 = get_fastapi_config()
        config2 = get_fastapi_config()

        assert config1 is config2

    def test_configure_fastapi_function(self):
        """Test configure_fastapi helper function."""
        networks = ["172.16.0.0/12"]
        configure_fastapi(trusted_proxy_networks=networks)

        config = get_fastapi_config()
        assert config.trusted_proxy_networks == networks


# =============================================================================
# Async Functions Tests (get_or_clone_limiter, check_limiter, check_multi_limiters)
# =============================================================================


class TestAsyncCloneAndCheckFunctions:
    """Test async functions for cloning and checking limiters."""

    @pytest.fixture(autouse=True)
    def setup_store(self):
        """Configure a memory store for testing."""
        configure_store("test_memory", strategy="memory")

    @pytest.fixture
    def configure_base_limiter(self):
        """Configure a base limiter for cloning tests."""
        configure_limiter(
            "test_base",
            store_id="test_memory",
            rate_per_second=10.0,
            timeout=0.0,
        )

    @pytest.mark.asyncio
    async def test_get_or_clone_limiter_creates_new(self, configure_base_limiter):
        """Test get_or_clone_limiter creates new limiter for identifier."""
        limiter = await get_or_clone_limiter("test_base", "192.168.1.1")

        assert limiter is not None
        assert limiter.is_initialized
        assert limiter.group_id == "test_base:192.168.1.1"

    @pytest.mark.asyncio
    async def test_get_or_clone_limiter_reuses_existing(self, configure_base_limiter):
        """Test get_or_clone_limiter reuses existing limiter."""
        limiter1 = await get_or_clone_limiter("test_base", "192.168.1.2")
        limiter2 = await get_or_clone_limiter("test_base", "192.168.1.2")

        assert limiter1 is limiter2

    @pytest.mark.asyncio
    async def test_check_limiter_allowed(self, configure_base_limiter):
        """Test check_limiter when request is allowed."""
        # First create the cloned limiter
        await get_or_clone_limiter("test_base", "check_test_1")

        result = await check_limiter("test_base:check_test_1")

        assert result.allowed is True
        assert result.limiter_id == "test_base:check_test_1"
        assert result.remaining >= 0

    @pytest.mark.asyncio
    async def test_check_multi_limiters_empty_list(self):
        """Test check_multi_limiters with empty list."""
        result = await check_multi_limiters([])

        assert result.allowed is True
        assert result.results == []

    @pytest.mark.asyncio
    async def test_check_multi_limiters_all_allowed(self, configure_base_limiter):
        """Test check_multi_limiters when all limiters allow."""
        # Create cloned limiters
        await get_or_clone_limiter("test_base", "multi_test_1")
        await get_or_clone_limiter("test_base", "multi_test_2")

        result = await check_multi_limiters(
            [
                "test_base:multi_test_1",
                "test_base:multi_test_2",
            ]
        )

        assert result.allowed is True
        assert result.blocking_limiter_id is None
        assert len(result.results) == 2
