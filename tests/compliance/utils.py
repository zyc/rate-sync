"""Utility functions and types for compliance tests.

This module contains non-fixture utilities that can be imported directly.
Fixtures are defined in conftest.py and automatically available.
"""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ratesync.core import RateLimiter


# =============================================================================
# ENGINE FACTORY PROTOCOL
# =============================================================================


class EngineFactory(Protocol):
    """Protocol for engine factory functions.

    Each engine provides a factory that creates configured limiters.
    This allows compliance tests to parametrize over engine types.
    """

    async def __call__(
        self,
        group_id: str | None = None,
        *,
        rate_per_second: float | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        max_concurrent: int | None = None,
        default_timeout: float | None = None,
        fail_closed: bool = False,
    ) -> RateLimiter:
        """Create a configured rate limiter instance."""
        ...


# =============================================================================
# ENGINE CAPABILITIES
# =============================================================================


@dataclass(frozen=True)
class EngineCapabilities:
    """Capabilities of a rate limiter engine."""

    name: str
    supports_token_bucket: bool
    supports_sliding_window: bool
    supports_concurrency: bool
    supports_get_state: bool
    requires_infrastructure: bool
    markers: tuple[str, ...]


ENGINE_CAPABILITIES: dict[str, EngineCapabilities] = {
    "memory": EngineCapabilities(
        name="memory",
        supports_token_bucket=True,
        supports_sliding_window=True,
        supports_concurrency=True,
        supports_get_state=True,
        requires_infrastructure=False,
        markers=(),
    ),
    "redis": EngineCapabilities(
        name="redis",
        supports_token_bucket=True,
        supports_sliding_window=True,
        supports_concurrency=True,
        supports_get_state=True,  # Redis implements get_state() for both algorithms
        requires_infrastructure=True,
        markers=("redis", "integration"),
    ),
    "postgres": EngineCapabilities(
        name="postgres",
        supports_token_bucket=True,
        supports_sliding_window=True,
        supports_concurrency=True,
        supports_get_state=True,  # PostgreSQL implements get_state() for both algorithms
        requires_infrastructure=True,
        markers=("postgres", "integration"),
    ),
}


def get_all_engines() -> list[str]:
    """Get all engine names."""
    return list(ENGINE_CAPABILITIES.keys())


def get_engines_with(capability: str) -> list[str]:
    """Get engine names that support a specific capability.

    Args:
        capability: One of 'token_bucket', 'sliding_window', 'concurrency',
                   'get_state', 'no_infrastructure'

    Returns:
        List of engine names supporting the capability
    """
    engines = []
    for name, caps in ENGINE_CAPABILITIES.items():
        if capability == "token_bucket" and caps.supports_token_bucket:
            engines.append(name)
        elif capability == "sliding_window" and caps.supports_sliding_window:
            engines.append(name)
        elif capability == "concurrency" and caps.supports_concurrency:
            engines.append(name)
        elif capability == "get_state" and caps.supports_get_state:
            engines.append(name)
        elif capability == "no_infrastructure" and not caps.requires_infrastructure:
            engines.append(name)
    return engines


def get_unit_test_engines() -> list[str]:
    """Get engines that can run without infrastructure (for CI)."""
    return get_engines_with("no_infrastructure")
