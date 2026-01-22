"""Tests for rate limiting decorator.

These tests verify the @rate_limited decorator behavior, including:
- Basic acquire/release functionality
- Timeout handling
- Factory function support
- Mode behavior (wait vs try)
- Function signature preservation
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ratesync.core import RateLimiter
from ratesync.decorators import rate_limited
from ratesync.exceptions import LimiterNotFoundError, RateLimiterAcquisitionError


def _create_mock_limiter(
    *,
    is_initialized: bool = True,
    default_timeout: float | None = None,
    acquire_success: bool = True,
) -> MagicMock:
    """Create a mock RateLimiter for testing.

    Args:
        is_initialized: Whether the limiter is initialized
        default_timeout: Default timeout value
        acquire_success: Whether acquire_context should succeed

    Returns:
        Configured mock limiter
    """
    limiter = MagicMock(spec=RateLimiter)
    limiter.is_initialized = is_initialized
    limiter.default_timeout = default_timeout
    limiter.initialize = AsyncMock()

    # Create async context manager for acquire_context
    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=None)
    context_manager.__aexit__ = AsyncMock(return_value=None)

    if not acquire_success:
        context_manager.__aenter__.side_effect = RateLimiterAcquisitionError(
            "Unable to acquire slot"
        )

    limiter.acquire_context = MagicMock(return_value=context_manager)

    return limiter


class TestRateLimitedBasic:
    """Test basic decorator functionality."""

    @pytest.mark.asyncio
    async def test_rate_limited_decorator_acquires_slot(self):
        """Decorator should acquire slot before executing function."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                return "result"

            result = await my_func()

            assert result == "result"
            mock_limiter.acquire_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited_decorator_releases_on_success(self):
        """Decorator should release slot after function completes successfully."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                return "success"

            await my_func()

            # Verify context manager was properly used (exit called)
            context = mock_limiter.acquire_context.return_value
            context.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited_decorator_releases_on_exception(self):
        """Decorator should release slot even when function raises exception."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                raise ValueError("test error")

            with pytest.raises(ValueError, match="test error"):
                await my_func()

            # Verify context manager was properly used (exit called with exception)
            context = mock_limiter.acquire_context.return_value
            context.__aexit__.assert_called_once()


class TestRateLimitedTimeout:
    """Test decorator timeout behavior."""

    @pytest.mark.asyncio
    async def test_rate_limited_with_timeout_raises_on_acquisition_failure(self):
        """Decorator with timeout should raise exception if cannot acquire slot."""
        mock_limiter = _create_mock_limiter(acquire_success=False)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", timeout=1.0)
            async def my_func():
                return "result"

            with pytest.raises(RateLimiterAcquisitionError):
                await my_func()

    @pytest.mark.asyncio
    async def test_rate_limited_with_zero_timeout_fails_immediately(self):
        """Decorator with timeout=0 should fail immediately if occupied."""
        mock_limiter = _create_mock_limiter(acquire_success=False)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", timeout=0)
            async def my_func():
                return "result"

            with pytest.raises(RateLimiterAcquisitionError):
                await my_func()

            # Verify timeout=0 was passed
            mock_limiter.acquire_context.assert_called_once_with(timeout=0)

    @pytest.mark.asyncio
    async def test_rate_limited_uses_limiter_default_timeout(self):
        """Decorator should use limiter's default_timeout when not specified."""
        mock_limiter = _create_mock_limiter(default_timeout=5.0)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                return "result"

            await my_func()

            # Should use limiter's default timeout (5.0)
            mock_limiter.acquire_context.assert_called_once_with(timeout=5.0)

    @pytest.mark.asyncio
    async def test_rate_limited_timeout_overrides_default(self):
        """Explicit timeout should override limiter's default_timeout."""
        mock_limiter = _create_mock_limiter(default_timeout=5.0)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", timeout=2.0)
            async def my_func():
                return "result"

            await my_func()

            # Should use explicit timeout (2.0), not default (5.0)
            mock_limiter.acquire_context.assert_called_once_with(timeout=2.0)


class TestRateLimitedFactory:
    """Test decorator with factory function."""

    @pytest.mark.asyncio
    async def test_rate_limited_with_factory_function(self):
        """Decorator should accept factory callable for dynamic limiter."""
        mock_limiter = _create_mock_limiter()
        factory = MagicMock(return_value=mock_limiter)

        @rate_limited(factory)
        async def my_func(tenant_id: str):
            return f"result for {tenant_id}"

        result = await my_func("tenant-123")

        assert result == "result for tenant-123"
        factory.assert_called_once()
        mock_limiter.acquire_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited_factory_receives_function_args(self):
        """Factory should receive the same arguments as the decorated function."""
        mock_limiter = _create_mock_limiter()
        factory = MagicMock(return_value=mock_limiter)

        @rate_limited(factory)
        async def my_func(tenant_id: str, api_name: str):
            return f"{tenant_id}:{api_name}"

        await my_func("tenant-123", "api-v1")

        # Factory should receive the function arguments
        factory.assert_called_once_with(tenant_id="tenant-123", api_name="api-v1")

    @pytest.mark.asyncio
    async def test_rate_limited_factory_with_kwargs(self):
        """Factory should handle kwargs correctly."""
        mock_limiter = _create_mock_limiter()
        factory = MagicMock(return_value=mock_limiter)

        @rate_limited(factory)
        async def my_func(user_id: str, action: str = "read"):
            return f"{user_id}:{action}"

        await my_func("user-456", action="write")

        factory.assert_called_once_with(user_id="user-456", action="write")

    @pytest.mark.asyncio
    async def test_rate_limited_factory_with_partial_args(self):
        """Factory can accept only some of the function arguments."""
        mock_limiter = _create_mock_limiter()

        # Factory only cares about tenant_id
        def factory(tenant_id: str):
            return mock_limiter

        @rate_limited(factory)
        async def my_func(tenant_id: str, data: dict):
            return data

        result = await my_func("tenant-123", {"key": "value"})

        assert result == {"key": "value"}
        mock_limiter.acquire_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited_factory_error_propagates(self):
        """Factory errors should propagate to caller."""

        def failing_factory(tenant_id: str):
            raise ValueError("Factory error")

        @rate_limited(failing_factory)
        async def my_func(tenant_id: str):
            return "result"

        with pytest.raises(ValueError, match="Factory error"):
            await my_func("tenant-123")


class TestRateLimitedModes:
    """Test decorator mode behavior (wait vs try)."""

    @pytest.mark.asyncio
    async def test_rate_limited_mode_wait_blocks_until_available(self):
        """Mode 'wait' should block until slot is available."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", mode="wait")
            async def my_func():
                return "result"

            result = await my_func()

            assert result == "result"
            mock_limiter.acquire_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited_mode_wait_raises_on_timeout(self):
        """Mode 'wait' should raise exception if timeout is exceeded."""
        mock_limiter = _create_mock_limiter(acquire_success=False)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", timeout=1.0, mode="wait")
            async def my_func():
                return "result"

            with pytest.raises(RateLimiterAcquisitionError):
                await my_func()

    @pytest.mark.asyncio
    async def test_rate_limited_mode_try_returns_none_on_failure(self):
        """Mode 'try' should return None if cannot acquire slot."""
        mock_limiter = _create_mock_limiter(acquire_success=False)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", timeout=0.5, mode="try")
            async def my_func():
                return "result"

            result = await my_func()

            assert result is None

    @pytest.mark.asyncio
    async def test_rate_limited_mode_try_returns_result_on_success(self):
        """Mode 'try' should return function result if slot acquired."""
        mock_limiter = _create_mock_limiter(acquire_success=True)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter", timeout=1.0, mode="try")
            async def my_func():
                return "success"

            result = await my_func()

            assert result == "success"

    def test_rate_limited_mode_try_requires_timeout(self):
        """Mode 'try' should require timeout to be specified."""
        with pytest.raises(ValueError, match="mode='try' requires timeout"):

            @rate_limited("test_limiter", mode="try")
            async def my_func():
                return "result"

    def test_rate_limited_invalid_mode_raises_error(self):
        """Invalid mode should raise ValueError."""
        with pytest.raises(ValueError, match="mode must be 'wait' or 'try'"):

            @rate_limited("test_limiter", mode="invalid")
            async def my_func():
                return "result"


class TestRateLimitedSignaturePreservation:
    """Test that decorator preserves function metadata."""

    def test_rate_limited_preserves_function_signature(self):
        """Decorator should preserve signature of the original function."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func(user_id: str, count: int = 10) -> dict:
                """Process user data."""
                return {"user_id": user_id, "count": count}

            sig = inspect.signature(my_func)
            params = list(sig.parameters.keys())

            assert params == ["user_id", "count"]
            assert sig.parameters["user_id"].annotation is str
            assert sig.parameters["count"].annotation is int
            assert sig.parameters["count"].default == 10

    def test_rate_limited_preserves_docstring(self):
        """Decorator should preserve docstring of the original function."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                """This is my important docstring."""
                return "result"

            assert my_func.__doc__ == "This is my important docstring."

    def test_rate_limited_preserves_function_name(self):
        """Decorator should preserve the function name."""
        mock_limiter = _create_mock_limiter()

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_special_function():
                return "result"

            assert my_special_function.__name__ == "my_special_function"

    def test_rate_limited_only_accepts_async_functions(self):
        """Decorator should raise TypeError for non-async functions."""
        with pytest.raises(TypeError, match="can only be applied to async functions"):

            @rate_limited("test_limiter")
            def sync_func():
                return "result"


class TestRateLimitedLimiterNotFound:
    """Test behavior when limiter is not configured."""

    @pytest.mark.asyncio
    async def test_rate_limited_executes_without_limit_when_not_configured(self):
        """When limiter is not found, function should execute without rate limit."""
        with patch(
            "ratesync.decorators.get_limiter", side_effect=LimiterNotFoundError("test_limiter")
        ):

            @rate_limited("test_limiter")
            async def my_func():
                return "result"

            # Should execute without rate limiting (fallback behavior)
            result = await my_func()

            assert result == "result"


class TestRateLimitedLazyInitialization:
    """Test lazy initialization of limiter."""

    @pytest.mark.asyncio
    async def test_rate_limited_initializes_limiter_if_not_initialized(self):
        """Decorator should initialize limiter if not already initialized."""
        mock_limiter = _create_mock_limiter(is_initialized=False)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                return "result"

            await my_func()

            # Should have called initialize()
            mock_limiter.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limited_skips_initialization_if_already_initialized(self):
        """Decorator should skip initialization if limiter is already initialized."""
        mock_limiter = _create_mock_limiter(is_initialized=True)

        with patch("ratesync.decorators.get_limiter", return_value=mock_limiter):

            @rate_limited("test_limiter")
            async def my_func():
                return "result"

            await my_func()

            # Should NOT have called initialize()
            mock_limiter.initialize.assert_not_called()
