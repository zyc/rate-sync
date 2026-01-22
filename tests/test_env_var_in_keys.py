"""Test environment variable interpolation in TOML keys (section names)."""

import os
import sys
import tempfile
from pathlib import Path

from ratesync.config import _expand_env_vars, _validate_config_structure

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _load_and_parse_toml(toml_content: str) -> tuple[dict, dict]:
    """Helper to load TOML, expand env vars, and validate structure."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        temp_path = f.name

    try:
        with open(temp_path, "rb") as f:
            config = tomllib.load(f)
        config = _expand_env_vars(config)
        stores, limiters, _fastapi = _validate_config_structure(config)
        return stores, limiters
    finally:
        Path(temp_path).unlink()


def test_expand_env_vars_in_limiter_keys():
    """Test that environment variables are expanded in limiter section names."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters."${TEST_LIMITER_ID:-default_limiter}"]
store = "memory_store"
rate_per_second = 5.0
timeout = 10.0
"""

    try:
        # Test 1: With env var set
        os.environ["TEST_LIMITER_ID"] = "my_custom_limiter"
        _, limiters = _load_and_parse_toml(toml_content)

        assert "my_custom_limiter" in limiters
        assert limiters["my_custom_limiter"]["store"] == "memory_store"
        assert limiters["my_custom_limiter"]["rate_per_second"] == 5.0

        # Test 2: Without env var (uses default)
        del os.environ["TEST_LIMITER_ID"]
        _, limiters = _load_and_parse_toml(toml_content)

        assert "default_limiter" in limiters
        assert limiters["default_limiter"]["store"] == "memory_store"
        assert limiters["default_limiter"]["rate_per_second"] == 5.0

    finally:
        os.environ.pop("TEST_LIMITER_ID", None)


def test_expand_env_vars_in_store_keys():
    """Test that environment variables are expanded in store section names."""
    toml_content = """
[stores."${TEST_STORE_ID:-default_store}"]
engine = "memory"

[limiters.test_limiter]
store = "${TEST_STORE_ID:-default_store}"
rate_per_second = 1.0
"""

    try:
        # Test 1: With env var set
        os.environ["TEST_STORE_ID"] = "my_custom_store"
        stores, limiters = _load_and_parse_toml(toml_content)

        assert "my_custom_store" in stores
        assert stores["my_custom_store"]["engine"] == "memory"
        assert limiters["test_limiter"]["store"] == "my_custom_store"

        # Test 2: Without env var (uses default)
        del os.environ["TEST_STORE_ID"]
        stores, limiters = _load_and_parse_toml(toml_content)

        assert "default_store" in stores
        assert stores["default_store"]["engine"] == "memory"
        assert limiters["test_limiter"]["store"] == "default_store"

    finally:
        os.environ.pop("TEST_STORE_ID", None)


def test_multiple_dynamic_limiters():
    """Test multiple limiters with different environment variables."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters."${LIMITER_A:-limiter_a}"]
store = "memory_store"
rate_per_second = 1.0

[limiters."${LIMITER_B:-limiter_b}"]
store = "memory_store"
rate_per_second = 2.0
"""

    try:
        os.environ["LIMITER_A"] = "custom_a"
        os.environ["LIMITER_B"] = "custom_b"

        _, limiters = _load_and_parse_toml(toml_content)

        assert "custom_a" in limiters
        assert "custom_b" in limiters
        assert limiters["custom_a"]["rate_per_second"] == 1.0
        assert limiters["custom_b"]["rate_per_second"] == 2.0

    finally:
        os.environ.pop("LIMITER_A", None)
        os.environ.pop("LIMITER_B", None)


def test_backward_compatibility_static_keys():
    """Test that static keys (no env vars) still work correctly."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters.static_limiter]
store = "memory_store"
rate_per_second = 3.0
"""

    _, limiters = _load_and_parse_toml(toml_content)

    assert "static_limiter" in limiters
    assert limiters["static_limiter"]["rate_per_second"] == 3.0


def test_payments_api_use_case():
    """Test a real-world payments API use case with Redis store."""
    toml_content = """
[stores.redis_payments]
engine = "redis"
key_prefix = "payments_rate_limit"
url = "${REDIS_URL:-redis://localhost:6379/0}"

[limiters."${PAYMENTS_LIMITER_ID:-payments}"]
store = "redis_payments"
rate_per_second = "${PAYMENTS_RATE_LIMIT_PER_SECOND:-1.0}"
timeout = 30.0
"""

    try:
        # Test 1: Production environment with custom limiter ID
        os.environ["PAYMENTS_LIMITER_ID"] = "payments_prod"
        os.environ["PAYMENTS_RATE_LIMIT_PER_SECOND"] = "2.0"
        os.environ["REDIS_URL"] = "redis://prod-server:6379/0"

        stores, limiters = _load_and_parse_toml(toml_content)

        assert "payments_prod" in limiters
        assert limiters["payments_prod"]["rate_per_second"] == 2.0
        assert stores["redis_payments"]["url"] == "redis://prod-server:6379/0"

        # Test 2: Development environment with defaults
        os.environ.pop("PAYMENTS_LIMITER_ID", None)
        os.environ.pop("PAYMENTS_RATE_LIMIT_PER_SECOND", None)
        os.environ.pop("REDIS_URL", None)

        stores, limiters = _load_and_parse_toml(toml_content)

        assert "payments" in limiters
        assert limiters["payments"]["rate_per_second"] == 1.0
        assert stores["redis_payments"]["url"] == "redis://localhost:6379/0"

    finally:
        os.environ.pop("PAYMENTS_LIMITER_ID", None)
        os.environ.pop("PAYMENTS_RATE_LIMIT_PER_SECOND", None)
        os.environ.pop("REDIS_URL", None)


def test_max_concurrent_in_limiter():
    """Test that max_concurrent can be specified in limiter config."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters.concurrency_limiter]
store = "memory_store"
max_concurrent = 5
"""

    _, limiters = _load_and_parse_toml(toml_content)

    assert "concurrency_limiter" in limiters
    assert limiters["concurrency_limiter"]["max_concurrent"] == 5
    assert "rate_per_second" not in limiters["concurrency_limiter"]


def test_max_concurrent_with_env_var():
    """Test that max_concurrent supports environment variable expansion."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters.api_limiter]
store = "memory_store"
max_concurrent = "${MAX_CONCURRENT:-10}"
"""

    try:
        # Test 1: With env var set
        os.environ["MAX_CONCURRENT"] = "25"
        _, limiters = _load_and_parse_toml(toml_content)

        assert limiters["api_limiter"]["max_concurrent"] == 25

        # Test 2: With default value
        del os.environ["MAX_CONCURRENT"]
        _, limiters = _load_and_parse_toml(toml_content)

        assert limiters["api_limiter"]["max_concurrent"] == 10

    finally:
        os.environ.pop("MAX_CONCURRENT", None)


def test_both_rate_and_max_concurrent():
    """Test that both rate_per_second and max_concurrent can be specified."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters.full_limiter]
store = "memory_store"
rate_per_second = 100.0
max_concurrent = 50
timeout = 30.0
"""

    _, limiters = _load_and_parse_toml(toml_content)

    assert "full_limiter" in limiters
    assert limiters["full_limiter"]["rate_per_second"] == 100.0
    assert limiters["full_limiter"]["max_concurrent"] == 50
    assert limiters["full_limiter"]["timeout"] == 30.0


def test_both_rate_and_max_concurrent_with_env_vars():
    """Test that both strategies support environment variables."""
    toml_content = """
[stores.memory_store]
engine = "memory"

[limiters.api_limiter]
store = "memory_store"
rate_per_second = "${API_RATE:-10.0}"
max_concurrent = "${API_CONCURRENCY:-5}"
"""

    try:
        os.environ["API_RATE"] = "50.0"
        os.environ["API_CONCURRENCY"] = "25"

        _, limiters = _load_and_parse_toml(toml_content)

        assert limiters["api_limiter"]["rate_per_second"] == 50.0
        assert limiters["api_limiter"]["max_concurrent"] == 25

    finally:
        os.environ.pop("API_RATE", None)
        os.environ.pop("API_CONCURRENCY", None)
