"""Compliance tests for configuration introspection (get_config).

All engines must provide read-only configuration access.
"""

from __future__ import annotations

import pytest

from ratesync.schemas import LimiterReadOnlyConfig
from compliance.utils import (
    EngineFactory,
    get_unit_test_engines,
)


pytestmark = [pytest.mark.compliance, pytest.mark.asyncio]

UNIT_ENGINES = get_unit_test_engines()


class TestConfigIntrospection:
    """Test configuration introspection compliance."""

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_get_config_returns_correct_type(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() returns LimiterReadOnlyConfig."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        config = limiter.get_config()
        assert isinstance(config, LimiterReadOnlyConfig)

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_reflects_rate_per_second(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() reflects rate_per_second setting."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=42.5)

        config = limiter.get_config()
        assert config.rate_per_second == 42.5

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_reflects_max_concurrent(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() reflects max_concurrent setting."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(max_concurrent=7)

        config = limiter.get_config()
        assert config.max_concurrent == 7

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_reflects_sliding_window(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() reflects sliding window settings."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=100, window_seconds=300)

        config = limiter.get_config()
        assert config.limit == 100
        assert config.window_seconds == 300

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_algorithm_token_bucket(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() shows token_bucket algorithm."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        config = limiter.get_config()
        assert config.algorithm == "token_bucket"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_algorithm_sliding_window(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() shows sliding_window algorithm."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(limit=10, window_seconds=60)

        config = limiter.get_config()
        assert config.algorithm == "sliding_window"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_includes_group_id(
        self, engine_name: str, get_factory
    ) -> None:
        """get_config() includes the limiter ID/group_id."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(group_id="my_limiter", rate_per_second=10.0)

        config = limiter.get_config()
        assert config.id == "my_limiter"

    @pytest.mark.parametrize("engine_name", UNIT_ENGINES)
    async def test_config_is_frozen(self, engine_name: str, get_factory) -> None:
        """LimiterReadOnlyConfig should be immutable."""
        factory: EngineFactory = get_factory(engine_name)
        limiter = await factory(rate_per_second=10.0)

        config = limiter.get_config()

        # Attempt to modify should raise
        with pytest.raises((AttributeError, TypeError)):
            config.rate_per_second = 99.9  # type: ignore
