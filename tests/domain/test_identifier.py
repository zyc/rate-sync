"""Tests for domain identifier value objects.

This module tests the domain-level identifier utilities:
- hash_identifier: Privacy-safe hashing for sensitive identifiers
- combine_identifiers: Creating compound rate limiter keys
"""

from __future__ import annotations


from ratesync.domain.value_objects.identifier import (
    combine_identifiers,
    hash_identifier,
)


# =============================================================================
# hash_identifier Tests
# =============================================================================


class TestHashIdentifier:
    """Test hash_identifier function from domain layer."""

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
        """Test that identifier is normalized to lowercase."""
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

    def test_hash_length_boundaries(self):
        """Test hash length at boundaries."""
        # Minimum (1 character)
        min_hash = hash_identifier("test", length=1)
        assert len(min_hash) == 1

        # Very large (64 characters - full sha256)
        max_hash = hash_identifier("test", length=64)
        assert len(max_hash) == 64

    def test_different_inputs_different_hashes(self):
        """Test that different inputs produce different hashes."""
        hash1 = hash_identifier("user1@example.com")
        hash2 = hash_identifier("user2@example.com")
        hash3 = hash_identifier("user3@example.com")

        # All should be different
        assert hash1 != hash2
        assert hash2 != hash3
        assert hash1 != hash3

    def test_salt_prevents_correlation(self):
        """Test that salt prevents cross-context correlation."""
        email = "sensitive@example.com"

        # Same email in different contexts should have different hashes
        login_context = hash_identifier(email, salt="login")
        reset_context = hash_identifier(email, salt="password_reset")
        verify_context = hash_identifier(email, salt="email_verification")

        assert login_context != reset_context
        assert reset_context != verify_context
        assert login_context != verify_context

    def test_empty_salt_same_as_no_salt(self):
        """Test that empty salt is same as no salt."""
        hash_no_salt = hash_identifier("test@example.com")
        hash_empty_salt = hash_identifier("test@example.com", salt="")

        assert hash_no_salt == hash_empty_salt

    def test_hash_with_special_characters(self):
        """Test hashing identifiers with special characters."""
        # Email with special characters
        hash1 = hash_identifier("user+tag@example.com")
        hash2 = hash_identifier("user.name@example.com")

        assert isinstance(hash1, str)
        assert isinstance(hash2, str)
        assert len(hash1) == 16
        assert len(hash2) == 16


# =============================================================================
# combine_identifiers Tests
# =============================================================================


class TestCombineIdentifiers:
    """Test combine_identifiers function from domain layer."""

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

    def test_all_empty_values(self):
        """Test with all empty values."""
        result = combine_identifiers("", "", "")

        assert result == ""

    def test_mixed_empty_and_none(self):
        """Test with mix of empty strings and values."""
        result = combine_identifiers("first", "", "third")

        assert result == "first:third"

    def test_numeric_identifiers(self):
        """Test with numeric identifiers (converted to string)."""
        result = combine_identifiers(123, 456, 789)

        assert result == "123:456:789"

    def test_mixed_types(self):
        """Test with mixed types."""
        result = combine_identifiers("api", 42, "endpoint")

        assert result == "api:42:endpoint"

    def test_separator_in_identifier(self):
        """Test behavior when separator appears in identifier.

        Note: This is intentional - we don't escape separators.
        Users should choose separators wisely.
        """
        result = combine_identifiers("a:b", "c:d", separator=":")

        # Will contain multiple separators
        assert result == "a:b:c:d"

    def test_unusual_separators(self):
        """Test with unusual separators."""
        # Pipe separator
        result1 = combine_identifiers("a", "b", "c", separator="|")
        assert result1 == "a|b|c"

        # Slash separator
        result2 = combine_identifiers("a", "b", "c", separator="/")
        assert result2 == "a/b/c"

        # Underscore separator
        result3 = combine_identifiers("a", "b", "c", separator="_")
        assert result3 == "a_b_c"

    def test_combine_with_hashed_identifier(self):
        """Test combining regular and hashed identifiers."""
        ip = "192.168.1.1"
        email_hash = hash_identifier("user@example.com")

        result = combine_identifiers(ip, email_hash)

        assert result.startswith(ip)
        assert email_hash in result
        assert ":" in result


# =============================================================================
# Integration Tests
# =============================================================================


class TestIdentifierIntegration:
    """Test integration between hash_identifier and combine_identifiers."""

    def test_typical_rate_limiting_use_case(self):
        """Test typical rate limiting scenario: IP + hashed email."""
        ip = "192.168.1.1"
        email = "user@example.com"

        # Hash the email for privacy
        email_hash = hash_identifier(email)

        # Combine for compound rate limiter key
        key = combine_identifiers(ip, email_hash)

        assert key == f"{ip}:{email_hash}"
        assert len(key) > len(ip)  # Contains both parts
        assert email not in key  # Email is hashed, not visible

    def test_multi_level_rate_limiting(self):
        """Test multi-level rate limiting: tenant + user + action."""
        tenant_id = "tenant-123"
        user_id = hash_identifier("user@example.com")
        action = "create_payment"

        key = combine_identifiers(tenant_id, user_id, action)

        assert key.startswith(tenant_id)
        assert action in key
        assert key.count(":") == 2  # Two separators for three parts

    def test_contextual_hashing_with_combine(self):
        """Test using salt for context-specific hashing."""
        email = "user@example.com"
        ip = "192.168.1.1"

        # Different contexts for same email
        login_key = combine_identifiers(
            ip,
            hash_identifier(email, salt="login"),
        )
        reset_key = combine_identifiers(
            ip,
            hash_identifier(email, salt="password_reset"),
        )

        # Same IP, same email, but different contexts = different keys
        assert login_key != reset_key
        assert login_key.startswith(ip)
        assert reset_key.startswith(ip)


# =============================================================================
# Edge Cases & Error Handling
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_hash_very_long_identifier(self):
        """Test hashing very long identifier."""
        long_id = "a" * 10000

        result = hash_identifier(long_id)

        assert isinstance(result, str)
        assert len(result) == 16

    def test_hash_unicode_identifier(self):
        """Test hashing unicode characters."""
        unicode_email = "用户@example.com"

        result = hash_identifier(unicode_email)

        assert isinstance(result, str)
        assert len(result) == 16

    def test_combine_very_long_result(self):
        """Test combining many identifiers."""
        identifiers = [f"id{i}" for i in range(100)]

        result = combine_identifiers(*identifiers)

        assert result.count(":") == 99  # 99 separators for 100 items
        assert "id0" in result
        assert "id99" in result

    def test_hash_invalid_algorithm_fallback(self):
        """Test that invalid algorithm falls back to sha256."""
        hash_valid = hash_identifier("test", algorithm="sha256")
        hash_invalid = hash_identifier("test", algorithm="invalid_algo")

        # Should fallback to sha256
        assert hash_valid == hash_invalid
