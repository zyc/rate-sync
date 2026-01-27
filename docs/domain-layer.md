# Domain Layer Architecture

Internal architecture documentation for contributors.

## Overview

The `ratesync.domain` layer contains pure domain logic independent of any framework or infrastructure. This follows Clean Architecture principles.

```
+----------------------------------+
|   Infrastructure Layer           |  <- Depends on Domain
|   (Redis, FastAPI, PostgreSQL)   |
+----------------------------------+
|   Domain Layer                   |  <- Pure business logic
|   (Value Objects, Rules)         |  <- NO external dependencies
+----------------------------------+
```

**Dependency Rule:** Source code dependencies point inward only.

## Structure

```
src/ratesync/
  domain/
    __init__.py              # Empty - marks domain boundary
    value_objects/
      __init__.py            # Barrel exports
      identifier.py          # Implementation
```

## Public API

### hash_identifier()

Hash sensitive identifiers for privacy-safe rate limiting.

```python
from ratesync import hash_identifier

email_hash = hash_identifier("user@example.com")
# With salt for context separation
login_hash = hash_identifier("user@example.com", salt="login")
```

### combine_identifiers()

Create composite rate limit keys.

```python
from ratesync import combine_identifiers

key = combine_identifiers("192.168.1.1", hash_identifier(email))
# Returns: "192.168.1.1:a1b2c3d4e5f6g7h8"
```

## Import Convention

```python
# Recommended - via public API
from ratesync import hash_identifier, combine_identifiers

# Allowed - via barrel export
from ratesync.domain.value_objects import hash_identifier

# Avoid - bypasses barrel
from ratesync.domain.value_objects.identifier import hash_identifier
```

## When to Read This

Contributors should understand this layer when:

- Adding new domain concepts (value objects, rules)
- Modifying identifier handling logic
- Understanding the separation between domain and infrastructure
- Writing tests for domain logic (no mocks needed)

## Design Rationale

| Decision | Rationale |
|----------|-----------|
| Separate domain layer | Framework-agnostic, testable without infrastructure |
| Functions over classes | Pragmatic value objects - simple and sufficient |
| Barrel exports | Clean imports, encapsulated internal structure |

## See Also

- [ADR-006: Hash Identifier in Domain](ADR.md#adr-006-hash-identifier-in-domain-layer)
- [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
