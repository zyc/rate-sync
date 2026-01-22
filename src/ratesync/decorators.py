"""Decorator for automatic rate limiting on functions.

This module provides the @rate_limited decorator that applies rate limiting
declaratively to async functions.
"""

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

from ratesync.core import RateLimiter
from ratesync.exceptions import (
    LimiterNotFoundError,
    RateLimiterAcquisitionError,
)
from ratesync.registry import get_limiter

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def rate_limited(
    limiter_or_factory: str | Callable[..., RateLimiter],
    *,
    timeout: float | None = None,
    mode: str = "wait",
) -> Callable[[F], F]:
    """Decorator to apply rate limiting to async functions.

    Supports two usage modes:

    1. **Fixed limiter** (string): Uses registry to look up limiter by name
    2. **Dynamic factory** (callable): Calls function to obtain limiter dynamically

    Args:
        limiter_or_factory: Name of limiter in registry (str) or callable that
            returns the limiter. The callable receives the same arguments as the
            decorated function and must return a RateLimiter instance.
        timeout: Optional timeout in seconds. If specified, uses try_acquire().
            If None, uses acquire() (waits indefinitely).
        mode: Behavior mode when unable to acquire slot:
            - "wait": Wait until acquired (default)
            - "try": Try once and return None if failed (requires timeout)

    Returns:
        Decorated function that applies rate limiting before execution

    Raises:
        ValueError: If mode="try" but timeout not specified
        RateLimiterNotConfiguredError: If limiter not found in registry
        RateLimiterAcquisitionError: If timeout and unable to acquire

    Examples:
        >>> # Usage with fixed limiter (registry)
        >>> @rate_limited("payments")
        >>> async def fetch_payment_data():
        >>>     return await http_client.get(url)
        >>>
        >>> # Usage with dynamic factory (multi-tenant)
        >>> @rate_limited(lambda tenant_id: get_tenant_limiter(tenant_id))
        >>> async def fetch_tenant_data(tenant_id: str):
        >>>     return await http_client.get(url)
        >>>
        >>> # Usage with timeout (non-blocking)
        >>> @rate_limited("payments", timeout=1.0)
        >>> async def fetch_with_timeout():
        >>>     return await http_client.get(url)
        >>>
        >>> # "try" mode - returns None if can't get slot
        >>> @rate_limited("payments", timeout=0.5, mode="try")
        >>> async def try_fetch():
        >>>     return await http_client.get(url)
        >>> result = await try_fetch()  # None if can't get slot
        >>>
        >>> # Factory with multiple arguments
        >>> @rate_limited(lambda user_id, api_name: get_user_api_limiter(user_id, api_name))
        >>> async def call_external_api(user_id: str, api_name: str, data: dict):
        >>>     return await http_client.post(url, json=data)
    """
    if mode not in ("wait", "try"):
        raise ValueError(f"mode must be 'wait' or 'try', received: {mode!r}")

    if mode == "try" and timeout is None:
        raise ValueError("mode='try' requires timeout to be specified")

    def decorator(func: F) -> F:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"@rate_limited can only be applied to async functions, "
                f"but {func.__name__} is not async"
            )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Resolve limiter
            limiter: RateLimiter
            if isinstance(limiter_or_factory, str):
                # Mode 1: Fixed limiter_id in registry
                try:
                    limiter = get_limiter(limiter_or_factory)
                except LimiterNotFoundError:
                    logger.warning(
                        "Rate limiter %r not configured, executing without rate limit",
                        limiter_or_factory,
                    )
                    # Fallback: execute without rate limit
                    return await func(*args, **kwargs)
            else:
                # Mode 2: Dynamic factory
                # Call factory with decorated function arguments
                try:
                    limiter = _call_factory(limiter_or_factory, func, args, kwargs)
                except Exception as e:
                    logger.error(
                        "Error obtaining rate limiter via factory: %s",
                        e,
                        exc_info=True,
                    )
                    # Can choose to re-raise or fallback
                    raise

            # Lazy initialization: ensure limiter is initialized before use
            if not limiter.is_initialized:
                await limiter.initialize()

            # Acquire slot
            # Use provided timeout, or fall back to limiter's default_timeout
            effective_timeout = timeout if timeout is not None else limiter.default_timeout

            try:
                async with limiter.acquire_context(timeout=effective_timeout):
                    return await func(*args, **kwargs)
            except RateLimiterAcquisitionError:
                if mode == "try":
                    # "try" mode: return None if can't get slot
                    logger.debug(
                        "Rate limiter: couldn't get slot in %ss, returning None",
                        effective_timeout,
                    )
                    return None
                # "wait" mode: re-raise exception
                raise

        # Preserve original signature for frameworks like FastAPI that inspect it
        # Use eval_str=True to resolve string annotations (from __future__ import annotations)
        # Pass func's globals for proper type resolution
        try:
            wrapper.__signature__ = inspect.signature(
                func, eval_str=True, globals=getattr(func, "__globals__", None)
            )
        except (TypeError, NameError):
            # Fallback: if eval_str fails, use regular signature
            wrapper.__signature__ = inspect.signature(func)

        return cast(F, wrapper)

    return decorator


def _call_factory(
    factory: Callable[..., RateLimiter],
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> RateLimiter:
    """Call factory with appropriate arguments from decorated function.

    Tries to pass arguments intelligently:
    1. If factory accepts **kwargs, pass complete args and kwargs
    2. Otherwise, inspect signature and pass only necessary arguments
    """
    sig = inspect.signature(factory)

    # If factory accepts **kwargs, pass everything
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if has_var_keyword:
        # Convert positional args to kwargs using original function signature
        func_sig = inspect.signature(func)
        bound = func_sig.bind(*args, **kwargs)
        bound.apply_defaults()
        all_kwargs = dict(bound.arguments)
        return factory(**all_kwargs)

    # Otherwise, pass only arguments that factory accepts
    factory_params = list(sig.parameters.keys())
    func_sig = inspect.signature(func)
    bound = func_sig.bind(*args, **kwargs)
    bound.apply_defaults()
    all_kwargs = dict(bound.arguments)

    # Filter only arguments that factory expects
    filtered_kwargs = {k: v for k, v in all_kwargs.items() if k in factory_params}

    return factory(**filtered_kwargs)
