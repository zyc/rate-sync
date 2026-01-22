"""Value objects for rate limiting domain.

This module contains immutable value objects used in rate limiting.
These are domain-level utilities, not tied to any framework.
"""

from __future__ import annotations

from ratesync.domain.value_objects.identifier import (
    combine_identifiers,
    hash_identifier,
)

__all__ = [
    "hash_identifier",
    "combine_identifiers",
]
