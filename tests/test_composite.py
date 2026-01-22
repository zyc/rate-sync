"""Tests for composite rate limiting functionality.

These tests verify the CompositeRateLimiter and CompositeRateLimitDependency,
including:
- Different combining strategies (most_restrictive, all_must_pass, any_must_pass)
- Identifier validation
- FastAPI integration
- Error handling
"""

import pytest

from ratesync import configure_limiter, configure_store
from ratesync.composite import CompositeLimitCheck, CompositeRateLimiter, RateLimitResult


class TestCompositeRateLimiterConfiguration:
    """Test CompositeRateLimiter configuration and validation."""

    def test_create_with_limiters(self):
        """Test creating composite with limiter mappings."""
        composite = CompositeRateLimiter(
            limiters={
                "by_ip": "test_ip",
                "by_email": "test_email",
            },
            strategy="most_restrictive",
        )

        assert composite.limiters == {
            "by_ip": "test_ip",
            "by_email": "test_email",
        }
        assert composite.strategy == "most_restrictive"

    def test_create_without_limiters_raises_error(self):
        """Test that creating without limiters raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CompositeRateLimiter(limiters={})

        assert "At least one limiter must be provided" in str(exc_info.value)

    def test_create_with_different_strategies(self):
        """Test creating composite with different strategies."""
        strategies = ["most_restrictive", "all_must_pass", "any_must_pass"]

        for strategy in strategies:
            composite = CompositeRateLimiter(
                limiters={"test": "test_limiter"},
                strategy=strategy,  # type: ignore[arg-type]
            )
            assert composite.strategy == strategy


class TestCompositeRateLimiterMostRestrictive:
    """Test CompositeRateLimiter with most_restrictive strategy."""

    @pytest.fixture(autouse=True)
    async def setup_limiters(self):
        """Configure test limiters before each test."""
        # Configure store
        configure_store("test_memory", strategy="memory")

        # Configure limiters with sliding window for deterministic testing
        # IP limiter: 1000 requests per minute (very permissive)
        configure_limiter(
            "ip_limiter",
            store_id="test_memory",
            algorithm="sliding_window",
            limit=1000,
            window_seconds=60,
            timeout=1.0,
        )

        # Email limiter: 10 requests per minute (restrictive)
        configure_limiter(
            "email_limiter",
            store_id="test_memory",
            algorithm="sliding_window",
            limit=10,
            window_seconds=60,
            timeout=1.0,
        )

        yield

    @pytest.mark.asyncio
    async def test_all_pass_returns_most_restrictive(self):
        """Test that when all pass, most restrictive is returned."""
        composite = CompositeRateLimiter(
            limiters={
                "by_ip": "ip_limiter",
                "by_email": "email_limiter",
            },
            strategy="most_restrictive",
        )

        result = await composite.check(
            identifiers={
                "by_ip": "192.168.1.1",
                "by_email": "test@example.com",
            },
            timeout=0,
        )

        assert result.allowed is True
        assert result.triggered_by is None
        # Most restrictive should be email limiter (10 req/min)
        assert result.most_restrictive.limit == 10

    @pytest.mark.asyncio
    async def test_one_fails_blocks_request(self):
        """Test that if one limiter fails, request is blocked."""
        composite = CompositeRateLimiter(
            limiters={
                "by_ip": "ip_limiter",
                "by_email": "email_limiter",
            },
            strategy="most_restrictive",
        )

        # Exhaust email limiter (10 requests)
        for _ in range(10):
            await composite.check(
                identifiers={
                    "by_ip": "192.168.1.1",
                    "by_email": "exhaust@example.com",
                },
                timeout=0,
            )

        # Next request should be blocked
        result = await composite.check(
            identifiers={
                "by_ip": "192.168.1.1",
                "by_email": "exhaust@example.com",
            },
            timeout=0,
        )

        assert result.allowed is False
        assert result.triggered_by == "by_email"
        assert result.most_restrictive.remaining == 0

    @pytest.mark.asyncio
    async def test_different_identifiers_independent(self):
        """Test that different identifiers are tracked independently."""
        composite = CompositeRateLimiter(
            limiters={
                "by_ip": "ip_limiter",
                "by_email": "email_limiter",
            },
            strategy="most_restrictive",
        )

        # Exhaust for user1
        for _ in range(10):
            await composite.check(
                identifiers={
                    "by_ip": "192.168.1.1",
                    "by_email": "user1@example.com",
                },
                timeout=0,
            )

        # User2 should still be allowed
        result = await composite.check(
            identifiers={
                "by_ip": "192.168.1.2",
                "by_email": "user2@example.com",
            },
            timeout=0,
        )

        assert result.allowed is True


class TestCompositeRateLimiterAllMustPass:
    """Test CompositeRateLimiter with all_must_pass strategy."""

    @pytest.fixture(autouse=True)
    async def setup_limiters(self):
        """Configure test limiters before each test."""
        configure_store("test_memory_all", strategy="memory")

        configure_limiter(
            "limiter_a",
            store_id="test_memory_all",
            algorithm="sliding_window",
            limit=1000,
            window_seconds=60,
            timeout=1.0,
        )

        configure_limiter(
            "limiter_b",
            store_id="test_memory_all",
            algorithm="sliding_window",
            limit=10,
            window_seconds=60,
            timeout=1.0,
        )

        yield

    @pytest.mark.asyncio
    async def test_all_pass_succeeds(self):
        """Test that when all pass, request succeeds."""
        composite = CompositeRateLimiter(
            limiters={
                "a": "limiter_a",
                "b": "limiter_b",
            },
            strategy="all_must_pass",
        )

        result = await composite.check(
            identifiers={
                "a": "id_a",
                "b": "id_b",
            },
            timeout=0,
        )

        assert result.allowed is True
        assert result.triggered_by is None

    @pytest.mark.asyncio
    async def test_one_fails_triggers_denial(self):
        """Test that first failure triggers denial."""
        composite = CompositeRateLimiter(
            limiters={
                "a": "limiter_a",
                "b": "limiter_b",
            },
            strategy="all_must_pass",
        )

        # Exhaust limiter_b
        for _ in range(10):
            await composite.check(
                identifiers={
                    "a": "id_a",
                    "b": "exhaust",
                },
                timeout=0,
            )

        # Should fail
        result = await composite.check(
            identifiers={
                "a": "id_a",
                "b": "exhaust",
            },
            timeout=0,
        )

        assert result.allowed is False
        assert result.triggered_by == "b"


class TestCompositeRateLimiterAnyMustPass:
    """Test CompositeRateLimiter with any_must_pass strategy."""

    @pytest.fixture(autouse=True)
    async def setup_limiters(self):
        """Configure test limiters before each test."""
        configure_store("test_memory_any", strategy="memory")

        configure_limiter(
            "limiter_x",
            store_id="test_memory_any",
            algorithm="sliding_window",
            limit=5,
            window_seconds=60,
            timeout=1.0,
        )

        configure_limiter(
            "limiter_y",
            store_id="test_memory_any",
            algorithm="sliding_window",
            limit=5,
            window_seconds=60,
            timeout=1.0,
        )

        yield

    @pytest.mark.asyncio
    async def test_one_passes_succeeds(self):
        """Test that if one limiter passes, request succeeds."""
        composite = CompositeRateLimiter(
            limiters={
                "x": "limiter_x",
                "y": "limiter_y",
            },
            strategy="any_must_pass",
        )

        # Exhaust x with one set of identifiers
        for _ in range(5):
            await composite.check(
                identifiers={
                    "x": "exhaust_x",
                    "y": "exhaust_y_old",  # Different Y to not exhaust fresh_y
                },
                timeout=0,
            )

        # Should still pass because y (with fresh_y id) has never been used
        result = await composite.check(
            identifiers={
                "x": "exhaust_x",  # This one is exhausted
                "y": "fresh_y",  # This one is fresh (never used)
            },
            timeout=0,
        )

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_all_fail_blocks_request(self):
        """Test that if all fail, request is blocked."""
        composite = CompositeRateLimiter(
            limiters={
                "x": "limiter_x",
                "y": "limiter_y",
            },
            strategy="any_must_pass",
        )

        # Exhaust both
        for _ in range(5):
            await composite.check(
                identifiers={
                    "x": "exhaust_both",
                    "y": "exhaust_both",
                },
                timeout=0,
            )

        # Should fail
        result = await composite.check(
            identifiers={
                "x": "exhaust_both",
                "y": "exhaust_both",
            },
            timeout=0,
        )

        assert result.allowed is False
        assert result.triggered_by == "all"


class TestCompositeRateLimiterIdentifierValidation:
    """Test identifier validation in CompositeRateLimiter."""

    @pytest.fixture(autouse=True)
    async def setup_limiters(self):
        """Configure test limiers before each test."""
        configure_store("test_memory_val", strategy="memory")

        configure_limiter(
            "test_limiter",
            store_id="test_memory_val",
            rate_per_second=1000.0,
            timeout=1.0,
        )

        yield

    @pytest.mark.asyncio
    async def test_missing_identifier_raises_error(self):
        """Test that missing identifier raises ValueError."""
        composite = CompositeRateLimiter(
            limiters={
                "a": "test_limiter",
                "b": "test_limiter",
            },
        )

        with pytest.raises(ValueError) as exc_info:
            await composite.check(
                identifiers={
                    "a": "id_a",
                    # Missing "b"
                },
                timeout=0,
            )

        assert "must match limiter keys" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extra_identifier_raises_error(self):
        """Test that extra identifier raises ValueError."""
        composite = CompositeRateLimiter(
            limiters={
                "a": "test_limiter",
            },
        )

        with pytest.raises(ValueError) as exc_info:
            await composite.check(
                identifiers={
                    "a": "id_a",
                    "b": "id_b",  # Extra
                },
                timeout=0,
            )

        assert "must match limiter keys" in str(exc_info.value)


class TestCompositeLimitCheckDataclass:
    """Test CompositeLimitCheck dataclass."""

    def test_create_check_result(self):
        """Test creating CompositeLimitCheck instance."""
        result_a = RateLimitResult(
            allowed=True,
            limit=100,
            remaining=50,
            reset_in=60.0,
        )

        result_b = RateLimitResult(
            allowed=True,
            limit=50,
            remaining=25,
            reset_in=30.0,
        )

        check = CompositeLimitCheck(
            allowed=True,
            results={"a": result_a, "b": result_b},
            most_restrictive=result_b,
            triggered_by=None,
        )

        assert check.allowed is True
        assert len(check.results) == 2
        assert check.most_restrictive == result_b
        assert check.triggered_by is None

    def test_frozen_dataclass(self):
        """Test that CompositeLimitCheck is immutable."""
        result = RateLimitResult(
            allowed=True,
            limit=100,
            remaining=50,
            reset_in=60.0,
        )

        check = CompositeLimitCheck(
            allowed=True,
            results={"test": result},
            most_restrictive=result,
            triggered_by=None,
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            check.allowed = False  # type: ignore[misc]
