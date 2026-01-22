"""Testing utilities for rate-sync.

This module provides utilities to help test code that uses rate limiting.
All utilities are backend-agnostic and work with Memory, Redis, NATS, and PostgreSQL.

Example:
    >>> from ratesync import get_limiter
    >>> from ratesync.testing import reset_limiter, reset_all_limiters
    >>>
    >>> # Reset a single limiter
    >>> limiter = get_limiter("api")
    >>> await reset_limiter(limiter)
    >>>
    >>> # Reset all limiters (useful in test fixtures)
    >>> await reset_all_limiters()
    >>>
    >>> # Reset entire backend store (destructive)
    >>> await reset_backend_store("redis")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ratesync.core import RateLimiter

logger = logging.getLogger(__name__)


async def reset_limiter(limiter: RateLimiter) -> None:
    """Reset a single limiter to its initial state.

    Clears all counters and windows, allowing tests to run with fresh state.
    This is backend-agnostic and calls the backend's reset() method.

    Args:
        limiter: The limiter to reset.

    Example:
        >>> from ratesync import get_limiter
        >>> from ratesync.testing import reset_limiter
        >>>
        >>> limiter = get_limiter("api")
        >>> await reset_limiter(limiter)
        >>>
        >>> # Now limiter has fresh state for testing
        >>> allowed = await limiter.try_acquire(timeout=0)
        >>> assert allowed is True
    """
    # Check if backend supports reset
    if not hasattr(limiter, "reset"):
        logger.warning(
            "Limiter '%s' (type: %s) doesn't support reset(). State may persist.",
            limiter.group_id,
            limiter.__class__.__name__,
        )
        return

    await limiter.reset()
    logger.debug("Limiter '%s' reset successfully", limiter.group_id)


async def reset_all_limiters() -> None:
    """Reset all configured limiters to initial state.

    Useful for test fixtures that need clean state between tests.
    Iterates through all registered limiters and calls reset_limiter() on each.

    Example:
        >>> import pytest
        >>> from ratesync.testing import reset_all_limiters
        >>>
        >>> @pytest.fixture(autouse=True)
        >>> async def reset_rate_limits():
        ...     await reset_all_limiters()
        ...     yield
        >>>
        >>> async def test_api_rate_limit():
        ...     # Starts with fresh state every time
        ...     limiter = get_limiter("api")
        ...     allowed = await limiter.try_acquire(timeout=0)
        ...     assert allowed is True
    """
    from ratesync.registry import list_limiters, get_limiter

    limiter_ids = list(list_limiters().keys())

    if not limiter_ids:
        logger.debug("No limiters configured to reset")
        return

    reset_count = 0
    for limiter_id in limiter_ids:
        try:
            limiter = get_limiter(limiter_id)
            await reset_limiter(limiter)
            reset_count += 1
        except Exception as e:
            logger.warning(
                "Failed to reset limiter '%s': %s",
                limiter_id,
                e,
            )

    logger.info("Reset %d/%d limiters", reset_count, len(limiter_ids))


async def reset_backend_store(store_id: str) -> None:
    """Reset an entire backend store.

    More aggressive than reset_all_limiters() - clears all data in the store,
    not just configured limiters. This is useful for cleaning up dynamic
    limiters created via get_or_clone_limiter().

    WARNING: This is destructive and should only be used in tests.
    It will delete ALL rate limiting data in the store, including limiters
    from other test runs or processes.

    Args:
        store_id: ID of the store to reset (from configuration).

    Raises:
        ValueError: If store_id not found in configuration.

    Example:
        >>> from ratesync.testing import reset_backend_store
        >>>
        >>> # Reset Redis store (removes ALL ratelimit:* keys)
        >>> await reset_backend_store("redis")
        >>>
        >>> # Reset memory store
        >>> await reset_backend_store("local")
    """
    from ratesync.registry import _registry

    if store_id not in _registry._backends:
        available = list(_registry._backends.keys())
        raise ValueError(
            f"Store '{store_id}' not found in configuration. Available stores: {available}"
        )

    # Get any limiter using this store to access the backend
    backend = None
    for limiter_config in _registry._limiters.values():
        limiter_store_id = limiter_config[0]
        limiter_instance = limiter_config[4]

        if limiter_store_id == store_id and limiter_instance is not None:
            backend = limiter_instance
            break

    if backend is None:
        logger.warning(
            "No initialized limiter found for store '%s'. "
            "Cannot reset store without an active connection.",
            store_id,
        )
        return

    # Check if backend supports reset_all
    if not hasattr(backend, "reset_all"):
        logger.warning(
            "Backend for store '%s' (type: %s) doesn't support reset_all().",
            store_id,
            backend.__class__.__name__,
        )
        return

    await backend.reset_all()
    logger.info("Backend store '%s' reset successfully", store_id)


__all__ = [
    "reset_limiter",
    "reset_all_limiters",
    "reset_backend_store",
]
