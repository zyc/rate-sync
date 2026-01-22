"""Configuration file loader for rate limiter.

This module provides functionality to load rate limiter configuration from TOML files,
with support for environment variable expansion.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

# Python 3.11+ has tomllib in stdlib, older versions need tomli
import tomllib

from ratesync.exceptions import ConfigValidationError
from ratesync.registry import _registry
from ratesync.validation import validate_limiter_config

logger = logging.getLogger(__name__)


# =============================================================================
# Global FastAPI Configuration (loaded from [fastapi] section)
# =============================================================================


class FastAPIConfig:
    """Global configuration for FastAPI integration.

    Loaded from the [fastapi] section in rate-sync.toml.
    Provides access to configuration values for FastAPI utilities.

    Attributes:
        trusted_proxy_networks: List of trusted proxy networks in CIDR notation.
            Used by get_client_ip() to determine if proxy headers should be trusted.

    Example TOML:
        [fastapi]
        trusted_proxy_networks = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "127.0.0.0/8",
        ]
    """

    def __init__(self) -> None:
        """Initialize with default values."""
        self._trusted_proxy_networks: list[str] | None = None

    @property
    def trusted_proxy_networks(self) -> list[str] | None:
        """Get trusted proxy networks from config.

        Returns:
            List of CIDR networks, or None to use defaults.
        """
        return self._trusted_proxy_networks

    @trusted_proxy_networks.setter
    def trusted_proxy_networks(self, value: list[str] | None) -> None:
        """Set trusted proxy networks."""
        self._trusted_proxy_networks = value

    def configure(
        self,
        trusted_proxy_networks: list[str] | None = None,
    ) -> None:
        """Configure FastAPI integration settings.

        Args:
            trusted_proxy_networks: List of trusted proxy networks in CIDR notation.
        """
        if trusted_proxy_networks is not None:
            self._trusted_proxy_networks = trusted_proxy_networks


# Global singleton for FastAPI config
_fastapi_config = FastAPIConfig()


def get_fastapi_config() -> FastAPIConfig:
    """Get the global FastAPI configuration.

    Returns:
        The global FastAPIConfig instance.
    """
    return _fastapi_config


def configure_fastapi(
    trusted_proxy_networks: list[str] | None = None,
) -> None:
    """Configure FastAPI integration settings programmatically.

    This is an alternative to using the [fastapi] section in rate-sync.toml.

    Args:
        trusted_proxy_networks: List of trusted proxy networks in CIDR notation.

    Example:
        >>> from ratesync.config import configure_fastapi
        >>> configure_fastapi(
        ...     trusted_proxy_networks=["10.0.0.0/8", "192.168.0.0/16"]
        ... )
    """
    _fastapi_config.configure(
        trusted_proxy_networks=trusted_proxy_networks,
    )


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand environment variables in configuration keys and values.

    Supports ${VAR_NAME} and ${VAR_NAME:-default} syntax (bash-like default values).
    Automatically converts numeric strings to appropriate types (int/float).

    Args:
        obj: Configuration object (dict, list, str, or other)

    Returns:
        Object with environment variables expanded and types converted

    Example:
        >>> _expand_env_vars("nats://${NATS_HOST}:4222")
        "nats://localhost:4222"  # If NATS_HOST=localhost
        >>> _expand_env_vars("${RATE:-1.0}")
        1.0  # Converted to float
        >>> _expand_env_vars("${PORT:-8000}")
        8000  # Converted to int
        >>> _expand_env_vars({"limiters": {"${LIMITER_ID:-default}": {...}}})
        {"limiters": {"my_limiter": {...}}}  # If LIMITER_ID=my_limiter
    """
    if isinstance(obj, dict):
        expanded_dict = {}
        for key, value in obj.items():
            # Expand key if it's a string (enables dynamic section names in TOML)
            expanded_key = _expand_env_vars(key) if isinstance(key, str) else key
            # Expand value recursively
            expanded_value = _expand_env_vars(value)
            expanded_dict[expanded_key] = expanded_value
        return expanded_dict

    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]

    if isinstance(obj, str):
        # Handle ${VAR:-default} syntax (bash-like default values)
        def replace_with_default(match):
            var_name = match.group(1)
            default_value = match.group(2)
            return os.environ.get(var_name, default_value)

        # Pattern: ${VAR_NAME:-default_value}
        result = re.sub(r"\$\{([^}:]+):-([^}]+)\}", replace_with_default, obj)

        # Then expand remaining ${VAR} and $VAR using standard expandvars
        result = os.path.expandvars(result)

        # Try to convert to numeric types if applicable
        # This ensures that "1.0" becomes float 1.0, "42" becomes int 42
        try:
            # Try float first (handles both "1.0" and "1")
            if "." in result or "e" in result.lower():
                return float(result)

            # Try int for whole numbers
            return int(result)
        except (ValueError, AttributeError):
            # Not a number, return as string
            return result

    return obj


def _validate_config_structure(
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate configuration structure and extract stores, limiters, and fastapi sections.

    Args:
        config: Configuration dictionary loaded from TOML

    Returns:
        Tuple of (stores, limiters, fastapi) dictionaries

    Raises:
        ConfigValidationError: If configuration structure is invalid
    """
    if not isinstance(config, dict):
        raise ConfigValidationError(
            "Configuration must be a dictionary",
            field="config",
            expected="dict",
            received=type(config).__name__,
        )

    # Extract stores and limiters sections
    stores = config.get("stores", {})
    limiters = config.get("limiters", {})
    fastapi = config.get("fastapi", {})

    if not isinstance(stores, dict):
        raise ConfigValidationError(
            "stores section must be a dictionary",
            field="stores",
            expected="dict",
            received=type(stores).__name__,
        )

    if not isinstance(limiters, dict):
        raise ConfigValidationError(
            "limiters section must be a dictionary",
            field="limiters",
            expected="dict",
            received=type(limiters).__name__,
        )

    if not isinstance(fastapi, dict):
        raise ConfigValidationError(
            "fastapi section must be a dictionary",
            field="fastapi",
            expected="dict",
            received=type(fastapi).__name__,
        )

    return stores, limiters, fastapi


def _load_stores(stores: dict[str, Any], config_path: Path) -> None:
    """Load and configure all stores from configuration.

    Args:
        stores: Dictionary of store configurations
        config_path: Path to configuration file (for logging)

    Raises:
        ConfigValidationError: If store configuration is invalid
    """
    logger.info("Loading %d stores from config file %s", len(stores), config_path)

    for store_id, store_config in stores.items():
        if not isinstance(store_config, dict):
            raise ConfigValidationError(
                f"Store '{store_id}' configuration must be a dictionary",
                field=f"stores.{store_id}",
                expected="dict",
                received=type(store_config).__name__,
            )

        engine = store_config.get("engine")
        if not engine:
            raise ConfigValidationError(
                f"Store '{store_id}' missing required field 'engine'",
                field=f"stores.{store_id}.engine",
                expected="engine name",
                received="missing",
            )

        # Validate and configure store
        try:
            # Extract engine from config and pass as strategy parameter
            # Remove 'engine' from kwargs since it will be passed as 'strategy'
            store_kwargs = {k: v for k, v in store_config.items() if k != "engine"}
            _registry.configure_store(store_id, strategy=engine, **store_kwargs)
            logger.info("Store '%s' configured from file (engine=%s)", store_id, engine)
        except Exception as e:
            raise ConfigValidationError(
                f"Failed to configure store '{store_id}': {e}",
                field=f"stores.{store_id}",
                expected="valid store config",
                received=str(store_config),
            ) from e


# Threshold below which we warn about suspiciously low rate limits
# This helps catch cases where environment variables weren't loaded
_LOW_RATE_THRESHOLD = 10.0


def _load_limiters(limiters: dict[str, Any], stores: dict[str, Any], config_path: Path) -> None:
    """Load and configure all limiters from configuration.

    Args:
        limiters: Dictionary of limiter configurations
        stores: Dictionary of store configurations (for validation)
        config_path: Path to configuration file (for logging)

    Raises:
        ConfigValidationError: If limiter configuration is invalid
    """
    logger.info("Loading %d limiters from config file %s", len(limiters), config_path)

    for limiter_id, limiter_config in limiters.items():
        if not isinstance(limiter_config, dict):
            raise ConfigValidationError(
                f"Limiter '{limiter_id}' configuration must be a dictionary",
                field=f"limiters.{limiter_id}",
                expected="dict",
                received=type(limiter_config).__name__,
            )

        # Validate limiter config schema
        try:
            validated_limiter = validate_limiter_config(limiter_config)
        except Exception as e:
            raise ConfigValidationError(
                f"Failed to validate limiter '{limiter_id}': {e}",
                field=f"limiters.{limiter_id}",
                expected="valid limiter config",
                received=str(limiter_config),
            ) from e

        # Check that referenced store exists
        store_id = validated_limiter.store
        if store_id not in stores:
            raise ConfigValidationError(
                f"Limiter '{limiter_id}' references unknown store '{store_id}'. "
                f"Store must be defined in the same TOML file.",
                field=f"limiters.{limiter_id}.store",
                expected=f"one of {list(stores.keys())}",
                received=store_id,
            )

        # Configure limiter
        try:
            _registry.configure_limiter(
                limiter_id=limiter_id,
                store_id=validated_limiter.store,
                rate_per_second=validated_limiter.rate_per_second,
                max_concurrent=validated_limiter.max_concurrent,
                timeout=validated_limiter.timeout,
                algorithm=validated_limiter.algorithm,
                limit=validated_limiter.limit,
                window_seconds=validated_limiter.window_seconds,
            )
            # Build log message based on algorithm
            if validated_limiter.algorithm == "sliding_window":
                logger.info(
                    "Limiter '%s' configured from file (store=%s, algorithm=sliding_window, "
                    "limit=%d, window=%ds)",
                    limiter_id,
                    validated_limiter.store,
                    validated_limiter.limit,
                    validated_limiter.window_seconds,
                )
            else:
                # Token bucket
                if validated_limiter.rate_per_second:
                    rate_str = f"{validated_limiter.rate_per_second:.2f} req/s"
                else:
                    rate_str = "unlimited"
                if validated_limiter.max_concurrent:
                    conc_str = f"{validated_limiter.max_concurrent} concurrent"
                else:
                    conc_str = "unlimited"
                logger.info(
                    "Limiter '%s' configured from file (store=%s, rate=%s, max_concurrent=%s)",
                    limiter_id,
                    validated_limiter.store,
                    rate_str,
                    conc_str,
                )

                # Warn if rate is suspiciously low (likely missing env var)
                if (
                    validated_limiter.rate_per_second is not None
                    and validated_limiter.rate_per_second < _LOW_RATE_THRESHOLD
                ):
                    logger.warning(
                        "Limiter '%s' has very low rate (%.2f req/s). "
                        "This may cause timeouts with concurrent workers. "
                        "Check if SICONFI_RATE_LIMIT_PER_SECOND env var is set correctly.",
                        limiter_id,
                        validated_limiter.rate_per_second,
                    )
        except Exception as e:
            raise ConfigValidationError(
                f"Failed to configure limiter '{limiter_id}': {e}",
                field=f"limiters.{limiter_id}",
                expected="valid limiter config",
                received=str(limiter_config),
            ) from e


def _load_fastapi(fastapi_config: dict[str, Any], config_path: Path) -> None:
    """Load FastAPI configuration from the [fastapi] section.

    Args:
        fastapi_config: FastAPI configuration dictionary
        config_path: Path to configuration file (for logging)
    """
    if not fastapi_config:
        logger.debug("No [fastapi] section in config file %s", config_path)
        return

    logger.info("Loading FastAPI config from %s", config_path)

    # Load trusted_proxy_networks
    trusted_networks = fastapi_config.get("trusted_proxy_networks")
    if trusted_networks is not None:
        if not isinstance(trusted_networks, list):
            raise ConfigValidationError(
                "fastapi.trusted_proxy_networks must be a list of CIDR networks",
                field="fastapi.trusted_proxy_networks",
                expected="list[str]",
                received=type(trusted_networks).__name__,
            )
        _fastapi_config.trusted_proxy_networks = trusted_networks
        logger.info(
            "FastAPI trusted proxy networks configured: %d networks",
            len(trusted_networks),
        )


def load_config(config_path: str | Path) -> None:
    """Load rate limiter configuration from TOML file.

    The configuration file should have three main sections:
    - [stores.*]: Store configurations
    - [limiters.*]: Limiter configurations
    - [fastapi]: FastAPI integration settings (optional)

    Example TOML:
        [stores.prod_nats]
        engine = "nats"
        url = "${NATS_URL}"

        [stores.local]
        engine = "memory"

        [limiters.payments]
        store = "prod_nats"
        rate_per_second = 1.0
        timeout = 30.0

        [fastapi]
        trusted_proxy_networks = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ]

    Args:
        config_path: Path to TOML configuration file

    Raises:
        FileNotFoundError: If config file doesn't exist
        ConfigValidationError: If configuration is invalid
        ValueError: If config format is invalid

    Example:
        >>> load_config("rate_limiter.toml")
        >>> await acquire("payments")
    """
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    # Load TOML file
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        raise ConfigValidationError(
            f"Failed to parse TOML file: {e}",
            field="config_file",
            expected="valid TOML",
            received=str(config_path),
        ) from e

    # Expand environment variables in all values
    config = _expand_env_vars(config)

    # Validate structure and extract stores, limiters, and fastapi
    stores, limiters, fastapi = _validate_config_structure(config)

    # Configure all stores first
    _load_stores(stores, config_path)

    # Configure all limiters
    _load_limiters(limiters, stores, config_path)

    # Configure FastAPI integration (optional)
    _load_fastapi(fastapi, config_path)

    logger.info(
        "Configuration loaded successfully: %d stores, %d limiters",
        len(stores),
        len(limiters),
    )


def _auto_load_config() -> None:
    """Automatically load configuration from standard locations.

    Searches for rate-sync.toml in the following order:
    1. Environment variable RATE_SYNC_CONFIG
    2. ./rate-sync.toml (current directory)
    3. ./config/rate-sync.toml (config subdirectory)

    If found, loads the configuration silently. If not found or error occurs,
    continues silently without failing.

    This function is called automatically when ratesync is imported.
    """
    # Check environment variable first
    env_config = os.getenv("RATE_SYNC_CONFIG")
    if env_config:
        config_path = Path(env_config)
        if config_path.exists():
            try:
                load_config(config_path)
                logger.debug("Configuration auto-loaded from RATE_SYNC_CONFIG: %s", config_path)
                return
            except (ValueError, OSError, ImportError, KeyError) as e:
                logger.warning(
                    "Failed to load config from RATE_SYNC_CONFIG (%s): %s",
                    config_path,
                    e,
                )
                # Continue to try other locations
        else:
            logger.warning("RATE_SYNC_CONFIG points to non-existent file: %s", config_path)

    # Search for rate-sync.toml in standard locations
    search_paths = [
        Path.cwd() / "rate-sync.toml",  # Current directory
        Path.cwd() / "config" / "rate-sync.toml",  # Config subdirectory
    ]

    for config_path in search_paths:
        if config_path.exists():
            try:
                load_config(config_path)
                logger.debug("Configuration auto-loaded from: %s", config_path)
                return
            except (ValueError, OSError, ImportError, KeyError) as e:
                logger.warning(
                    "Failed to auto-load config from %s: %s",
                    config_path,
                    e,
                )
                # Don't try other paths if we found a file but it failed to load
                return

    # No config file found - this is OK, user will configure programmatically
    logger.debug("No rate-sync.toml found in standard locations. Using programmatic configuration.")
