"""Domain value objects for rate limiter identifiers.

This module provides domain-level utilities for working with rate limiter
identifiers. These functions are framework-agnostic and can be used in any
context, not just with FastAPI.

Key functions:
- hash_identifier: Hash sensitive identifiers (emails, phone numbers) for privacy
- combine_identifiers: Combine multiple identifiers into compound keys
"""

from __future__ import annotations

import hashlib


def hash_identifier(
    identifier: str,
    *,
    length: int = 16,
    salt: str = "",
    algorithm: str = "sha256",
) -> str:
    """Hash a sensitive identifier for privacy-safe rate limiting.

    This is a DOMAIN utility - not tied to any framework or infrastructure layer.
    Use for hashing emails, phone numbers, or other PII to avoid storing
    sensitive data in rate limiter keys/logs.

    Generates a truncated hash suitable for use as a rate limiter key.
    The hash is deterministic (same input = same output) and normalized
    (case-insensitive, whitespace-trimmed).

    Args:
        identifier: The sensitive identifier to hash (email, phone, etc.)
        length: Length of the returned hash (default: 16 characters).
            Shorter hashes are more collision-prone but more readable in logs.
            Recommended: 16 for most cases, 32 for high-security scenarios.
        salt: Optional salt to add for uniqueness across different uses.
            For example, use different salts for login vs. password-reset
            to prevent cross-context correlation.
        algorithm: Hash algorithm to use (default: sha256).
            Supported: sha256, sha384, sha512, md5 (not recommended for security).

    Returns:
        Truncated hex hash of the identifier.

    Example:
        >>> # Basic usage - hash email for rate limiting
        >>> email_hash = hash_identifier("user@example.com")
        >>> limiter_id = f"login:{ip}:{email_hash}"
        >>>
        >>> # Hash with salt for different contexts
        >>> login_hash = hash_identifier(email, salt="login")
        >>> reset_hash = hash_identifier(email, salt="password_reset")
        >>> # login_hash != reset_hash (prevents correlation)
        >>>
        >>> # Longer hash for less collisions
        >>> secure_hash = hash_identifier(email, length=32)
        >>>
        >>> # Case and whitespace normalization
        >>> hash_identifier("User@Example.com") == hash_identifier("  user@example.com  ")
        >>> # True

    Notes:
        - Input is normalized: lowercased and stripped of whitespace
        - Same input always produces same output (deterministic)
        - Different salts produce different hashes for same input
        - Truncation may increase collision probability (use longer length if needed)
    """
    # Normalize the identifier (lowercase + strip whitespace)
    normalized = identifier.lower().strip()

    # Add salt if provided (prevents correlation across contexts)
    if salt:
        normalized = f"{salt}:{normalized}"

    # Get the hash function
    hash_funcs = {
        "sha256": hashlib.sha256,
        "sha384": hashlib.sha384,
        "sha512": hashlib.sha512,
        "md5": hashlib.md5,  # Not recommended for security, but available
    }

    hash_func = hash_funcs.get(algorithm, hashlib.sha256)

    # Generate hash
    full_hash = hash_func(normalized.encode()).hexdigest()

    # Truncate to requested length
    return full_hash[:length]


def combine_identifiers(*identifiers: str, separator: str = ":") -> str:
    """Combine multiple identifiers into a single rate limiter key.

    This is a DOMAIN utility for creating compound rate limiter keys.
    Useful for multi-dimensional rate limiting (e.g., "IP:email", "user:action").

    Args:
        *identifiers: Variable number of identifiers to combine.
            Empty or None values are skipped.
        separator: Separator between identifiers (default: ":").
            Choose a separator that won't appear in your identifiers.

    Returns:
        Combined identifier string with non-empty values joined by separator.

    Example:
        >>> # Combine IP and hashed email
        >>> key = combine_identifiers(
        ...     ip,
        ...     hash_identifier(email),
        ... )
        >>> # Result: "192.168.1.1:a1b2c3d4e5f6g7h8"
        >>>
        >>> # Multi-level key
        >>> key = combine_identifiers(
        ...     "api",
        ...     user_id,
        ...     endpoint,
        ... )
        >>> # Result: "api:user123:/payments"
        >>>
        >>> # Empty values are skipped
        >>> combine_identifiers("a", "", "b", None, "c")
        >>> # Result: "a:b:c"
        >>>
        >>> # Custom separator
        >>> combine_identifiers("a", "b", "c", separator="-")
        >>> # Result: "a-b-c"

    Notes:
        - Empty strings and None are filtered out
        - All values are converted to string
        - No validation of separator uniqueness (choose wisely)
    """
    return separator.join(str(i) for i in identifiers if i)


__all__ = [
    "hash_identifier",
    "combine_identifiers",
]
