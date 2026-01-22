"""
Centralized validation for rate limiter configurations.

This module provides validation functions that use dataclass schemas to validate
configuration parameters for both backends and limiters.
"""

from dataclasses import MISSING, fields, is_dataclass
from typing import Any, Literal, get_args, get_origin

from ratesync.exceptions import ConfigValidationError
from ratesync.schemas import ENGINE_SCHEMAS, LimiterConfig


def _get_type_name(type_hint: Any) -> str:
    """Get a human-readable name for a type hint."""
    origin = get_origin(type_hint)

    if origin is None:
        # Simple type like str, int, float
        if hasattr(type_hint, "__name__"):
            return type_hint.__name__
        return str(type_hint)

    # Union types (e.g., str | None)
    args = get_args(type_hint)
    if origin is type(None) or (hasattr(origin, "__name__") and "Union" in origin.__name__):
        type_names = [_get_type_name(arg) for arg in args if arg is not type(None)]
        return " | ".join(type_names) + " | None"

    # Other generic types
    return str(type_hint)


def _validate_type(value: Any, expected_type: Any, field_name: str) -> None:
    """Validate that a value matches the expected type."""
    origin = get_origin(expected_type)
    args = get_args(expected_type)

    # Handle None values
    if value is None:
        # Check if None is allowed (Optional types)
        if origin is type(None) or (args and type(None) in args):
            return
        raise ConfigValidationError(
            f"Field '{field_name}' cannot be None",
            field=field_name,
            expected=_get_type_name(expected_type),
            received="None",
        )

    # Special handling for Literal types (check BEFORE Union handling)
    if origin is Literal:
        literal_values = get_args(expected_type)
        if value not in literal_values:
            raise ConfigValidationError(
                f"Field '{field_name}' must be one of {literal_values}",
                field=field_name,
                expected=f"Literal{literal_values}",
                received=repr(value),
            )
        return

    # Handle Union types (e.g., str | None)
    if args:
        # Filter out Literal types from Union validation
        valid_types = [
            arg for arg in args if arg is not type(None) and get_origin(arg) is not Literal
        ]
        literal_types = [arg for arg in args if get_origin(arg) is Literal]

        # Check Literal types in Union first
        for lit_type in literal_types:
            lit_values = get_args(lit_type)
            if value in lit_values:
                return

        # Check regular types
        for t in valid_types:
            try:
                if not get_origin(t) and isinstance(value, t):
                    return
            except TypeError:
                # Skip types that can't be used with isinstance
                continue

        # Also handle simple type checking for Union
        if origin is type(None) or (hasattr(origin, "__name__") and "Union" in str(origin)):
            for arg in valid_types:
                if arg is type(value):
                    return

    # Simple type checking
    expected_simple_type = origin if origin else expected_type

    # Regular type checking
    if expected_simple_type and expected_simple_type is not Literal:
        try:
            if not isinstance(value, expected_simple_type):
                # Handle cases where expected_simple_type might be a generic
                if hasattr(expected_simple_type, "__name__"):
                    expected_name = expected_simple_type.__name__
                else:
                    expected_name = _get_type_name(expected_type)

                raise ConfigValidationError(
                    f"Field '{field_name}' has incorrect type",
                    field=field_name,
                    expected=expected_name,
                    received=type(value).__name__,
                )
        except TypeError:
            # Skip types that can't be used with isinstance (like Literal)
            pass


def validate_store_config(engine: str, params: dict[str, Any]) -> Any:
    """Validate store configuration parameters and return config dataclass instance.

    Args:
        engine: Engine name (e.g., "nats", "memory")
        params: Configuration parameters as a dictionary

    Returns:
        Config dataclass instance for the engine

    Raises:
        ConfigValidationError: If engine is unknown or parameters are invalid
    """
    if engine not in ENGINE_SCHEMAS:
        available = ", ".join(ENGINE_SCHEMAS.keys())
        raise ConfigValidationError(
            f"Unknown engine '{engine}'. Available engines: {available}",
            field="engine",
            expected=available,
            received=engine,
        )

    config_class = ENGINE_SCHEMAS[engine]

    if not is_dataclass(config_class):
        raise ConfigValidationError(
            f"Engine config class for '{engine}' is not a dataclass",
            field="engine",
            expected="dataclass",
            received=str(type(config_class)),
        )

    # Get all fields
    config_fields = {f.name: f for f in fields(config_class)}

    # Check for required fields (fields without defaults)
    for field_name, field_obj in config_fields.items():
        has_default = field_obj.default is not MISSING or field_obj.default_factory is not MISSING

        if not has_default and field_name not in params:
            raise ConfigValidationError(
                f"Missing required field '{field_name}' for engine '{engine}'",
                field=field_name,
                expected="required",
                received="missing",
            )

    # Validate types for provided fields
    validated_params = {}
    for field_name, value in params.items():
        if field_name not in config_fields:
            # Ignore unknown fields (they might be strategy-specific kwargs)
            validated_params[field_name] = value
            continue

        field_obj = config_fields[field_name]
        _validate_type(value, field_obj.type, field_name)
        validated_params[field_name] = value

    # Create and return config instance
    try:
        return config_class(**validated_params)
    except TypeError as e:
        raise ConfigValidationError(
            f"Failed to create config for engine '{engine}': {e}",
            field="config",
            expected="valid parameters",
            received=str(params),
        ) from e


def validate_limiter_config(config: dict[str, Any]) -> LimiterConfig:
    """Validate limiter configuration and return LimiterConfig instance.

    Args:
        config: Limiter configuration dictionary

    Returns:
        LimiterConfig instance

    Raises:
        ConfigValidationError: If configuration is invalid
    """
    # Check for required fields (fields without defaults)
    for field_obj in fields(LimiterConfig):
        has_default = field_obj.default is not MISSING or field_obj.default_factory is not MISSING

        if not has_default and field_obj.name not in config:
            raise ConfigValidationError(
                f"Missing required field '{field_obj.name}' in limiter config",
                field=field_obj.name,
                expected="required",
                received="missing",
            )

    # Validate types
    for field_obj in fields(LimiterConfig):
        if field_obj.name in config:
            _validate_type(config[field_obj.name], field_obj.type, field_obj.name)

    # Create and return LimiterConfig instance
    try:
        return LimiterConfig(**config)
    except TypeError as e:
        raise ConfigValidationError(
            f"Failed to create limiter config: {e}",
            field="limiter_config",
            expected="valid parameters",
            received=str(config),
        ) from e
