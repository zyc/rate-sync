# Domain Layer Architecture

**Status:** ✅ Implemented
**Version:** 1.0
**Last Updated:** 2025-12-20

---

## Overview

The `ratesync.domain` layer contains pure domain logic that is **completely independent** of any framework or infrastructure concerns. This follows **Clean Architecture** principles where the domain is at the center and all other layers depend on it.

## Design Principles

### 1. Framework Independence

Domain logic does NOT depend on:
- ❌ FastAPI
- ❌ Redis
- ❌ PostgreSQL
- ❌ NATS
- ❌ Any other infrastructure

Domain logic CAN be used:
- ✅ In any Python application
- ✅ With any web framework
- ✅ With any storage backend
- ✅ In tests without mocks

### 2. Layered Architecture

```
┌─────────────────────────────────┐
│   Infrastructure Layer          │  ← Depends on Domain
│   (Redis, FastAPI, etc.)        │
├─────────────────────────────────┤
│   Domain Layer                  │  ← Pure business logic
│   (Value Objects, Rules)        │  ← NO external dependencies
└─────────────────────────────────┘
```

### 3. Dependency Rule

**The Dependency Rule**: Source code dependencies must point **inward only**.

- ✅ Infrastructure → Domain (allowed)
- ❌ Domain → Infrastructure (forbidden)

---

## Structure

```
src/ratesync/
  domain/                    # Base arquitectural (NO exports)
    __init__.py              # Empty - marks as domain boundary
    value_objects/           # Intermediate layer (YES exports - barrel)
      __init__.py            # Exports: hash_identifier, combine_identifiers
      identifier.py          # Implementation
```

### Import Convention

Following the **Barrel Export** pattern:

```python
# ✅ RECOMMENDED - Via public API
from ratesync import hash_identifier, combine_identifiers

# ✅ ALLOWED - Direct from value objects (barrel export)
from ratesync.domain.value_objects import hash_identifier

# ⚠️  ALLOWED - But not recommended (bypasses barrel)
from ratesync.domain.value_objects.identifier import hash_identifier

# ❌ NEVER - domain/ itself doesn't export
from ratesync.domain import hash_identifier  # Won't work!
```

---

## Value Objects

### What is a Value Object?

A **Value Object** is an immutable object defined by its value, not by identity.

**Characteristics:**
- Immutable (cannot change after creation)
- Defined by value (two objects with same value are equal)
- No identity (no ID field)
- Self-validating (invalid state is impossible)

**Examples:**
- Email address
- Phone number
- Hash of an identifier
- Compound key (IP:Email)

### identifier.py

Contains domain utilities for working with rate limiter identifiers.

#### Functions

##### hash_identifier()

Hash sensitive identifiers for privacy-safe rate limiting.

**Purpose:** Transform PII (emails, phones) into deterministic hashes to avoid storing sensitive data in rate limiter keys or logs.

**Signature:**
```python
def hash_identifier(
    identifier: str,
    *,
    length: int = 16,
    salt: str = "",
    algorithm: str = "sha256",
) -> str
```

**Domain Rule:**
> *"Sensitive identifiers must never appear in logs or storage keys."*

**Example:**
```python
from ratesync import hash_identifier

# Basic usage
email_hash = hash_identifier("user@example.com")
# Returns: "a1b2c3d4e5f6g7h8" (16 chars, deterministic)

# With salt for context separation
login_hash = hash_identifier("user@example.com", salt="login")
reset_hash = hash_identifier("user@example.com", salt="password-reset")
# Different hashes prevent cross-context correlation

# Custom length for high-security
secure_hash = hash_identifier("user@example.com", length=32)
```

**Why It's Domain Logic:**
- Enforces privacy rules (business requirement)
- Algorithm choice is a domain decision
- Framework-agnostic (works anywhere)
- No infrastructure dependencies

##### combine_identifiers()

Combine multiple identifiers into a compound key.

**Purpose:** Create composite rate limit keys (e.g., "IP:Email", "UserId:Endpoint").

**Signature:**
```python
def combine_identifiers(
    *identifiers: str | int | None,
    separator: str = ":",
) -> str
```

**Domain Rule:**
> *"Composite keys must be deterministic and skip empty values."*

**Example:**
```python
from ratesync import combine_identifiers

# Basic combination
key = combine_identifiers("192.168.1.1", "login")
# Returns: "192.168.1.1:login"

# With hashed email
key = combine_identifiers(
    "192.168.1.1",
    hash_identifier("user@example.com")
)
# Returns: "192.168.1.1:a1b2c3d4e5f6g7h8"

# Skips None and empty values
key = combine_identifiers("192.168.1.1", None, "", "endpoint")
# Returns: "192.168.1.1:endpoint"

# Custom separator
key = combine_identifiers("user", "endpoint", separator="/")
# Returns: "user/endpoint"
```

**Why It's Domain Logic:**
- Defines how composite keys are structured (domain rule)
- Separator is a domain convention
- Framework-agnostic
- No infrastructure dependencies

---

## Usage Patterns

### Pattern 1: Privacy-Safe User Identification

**Scenario:** Rate limit by email without storing emails in Redis.

```python
from ratesync import get_or_clone_limiter, hash_identifier

email = "user@example.com"
email_hash = hash_identifier(email)

limiter = await get_or_clone_limiter("login", email_hash)
allowed = await limiter.try_acquire(timeout=0)
```

**Why:**
- Email never appears in Redis keys
- Email never appears in logs
- GDPR/LGPD compliant
- Deterministic (same email = same hash)

### Pattern 2: Multi-Factor Rate Limiting

**Scenario:** Rate limit by IP + Email combination.

```python
from ratesync import get_or_clone_limiter, hash_identifier, combine_identifiers

client_ip = "192.168.1.1"
email = "user@example.com"

# Separate limiters with different contexts
identifier = combine_identifiers(
    client_ip,
    hash_identifier(email, salt="verify-code")
)

limiter = await get_or_clone_limiter("verify_code_email", identifier)
allowed = await limiter.try_acquire(timeout=0)
```

**Why:**
- IP + Email provides stronger identity verification
- Salt prevents correlation with other endpoints
- Privacy-safe (email is hashed)

### Pattern 3: Context-Separated Hashing

**Scenario:** Same email, different rate limits for different actions.

```python
from ratesync import hash_identifier, combine_identifiers

email = "user@example.com"

# Different salts = different hashes = different rate limit buckets
login_key = combine_identifiers(
    ip,
    hash_identifier(email, salt="login")
)

reset_key = combine_identifiers(
    ip,
    hash_identifier(email, salt="password-reset")
)

signup_key = combine_identifiers(
    ip,
    hash_identifier(email, salt="signup")
)
```

**Why:**
- User can't exhaust password-reset limit by login attempts
- Each context has independent rate limiting
- Prevents abuse via context switching

---

## Design Decisions

### Why Separate Domain Layer?

**Decision:** Create `domain/` separate from core implementation.

**Rationale:**
1. **Clean Architecture** - Domain logic is framework-agnostic
2. **Reusability** - Can use `hash_identifier()` without FastAPI
3. **Testability** - Domain tests need no mocks or infrastructure
4. **Single Responsibility** - Value objects have one purpose

**Alternatives Considered:**
- ❌ Keep in `contrib/fastapi/` - Ties domain logic to FastAPI
- ❌ Keep in `core.py` - Mixes domain with application logic

**Result:** Domain layer is pure, reusable, and framework-agnostic.

### Why Value Objects?

**Decision:** Use value objects pattern for identifiers.

**Rationale:**
1. **Immutability** - Identifiers can't be modified after creation
2. **Self-validation** - Invalid identifiers can't exist
3. **Equality by value** - Two identical identifiers are equal
4. **Domain concept** - "Identifier" is a business concept

**Alternatives Considered:**
- ❌ Plain strings - No validation or domain rules
- ❌ Dataclasses - Overkill for simple values

**Result:** Simple functions that return validated strings (pragmatic value objects).

### Why hash_identifier() in Domain?

**Decision:** Move from `contrib/fastapi/` to `domain/`.

**Rationale:**
1. **Not FastAPI-specific** - Works anywhere
2. **Domain rule** - "Don't store PII" is a business requirement
3. **Framework-agnostic** - Can use in Django, Flask, etc.
4. **Testable** - No infrastructure dependencies

**Migration:** Maintained backward compatibility via re-exports.

**Result:** Clean separation, no breaking changes.

---

## Testing

### Test Coverage

**Location:** `tests/domain/test_identifier.py`

**Coverage:**
- 31 comprehensive tests
- 100% code coverage
- All edge cases handled

**Categories:**
1. **Normalization Tests** (4 tests)
   - Case insensitivity
   - Whitespace trimming
   - Unicode handling

2. **Hashing Tests** (8 tests)
   - Deterministic behavior
   - Different algorithms (sha256, sha512, md5)
   - Custom lengths
   - Salt variations

3. **Security Tests** (3 tests)
   - Salt prevents correlation
   - Different inputs = different hashes
   - Hash length boundaries

4. **Combination Tests** (8 tests)
   - Multiple identifiers
   - Custom separators
   - Skipping empty/None values
   - Numeric identifiers

5. **Integration Tests** (8 tests)
   - Real-world scenarios
   - Composite keys
   - Privacy-safe patterns

### Example Test

```python
def test_hash_identifier_deterministic():
    """Same input always produces same output."""
    email = "user@example.com"

    hash1 = hash_identifier(email)
    hash2 = hash_identifier(email)

    assert hash1 == hash2

def test_salt_prevents_correlation():
    """Different salts prevent cross-context correlation."""
    email = "user@example.com"

    login_hash = hash_identifier(email, salt="login")
    reset_hash = hash_identifier(email, salt="reset")

    assert login_hash != reset_hash
```

---

## Best Practices

### DO ✅

1. **Use domain functions for all identifier transformations**
   ```python
   from ratesync import hash_identifier
   hash_identifier(email)  # Good
   ```

2. **Salt sensitive operations differently**
   ```python
   hash_identifier(email, salt="login")
   hash_identifier(email, salt="signup")
   ```

3. **Test domain logic without infrastructure**
   ```python
   # No Redis, no FastAPI needed
   assert hash_identifier("test") == hash_identifier("test")
   ```

### DON'T ❌

1. **Don't create hashes manually**
   ```python
   import hashlib
   hashlib.sha256(email.encode()).hexdigest()[:16]  # Bad - use hash_identifier()
   ```

2. **Don't mix domain and infrastructure**
   ```python
   # Bad - domain logic should not import FastAPI
   from fastapi import Request

   def hash_identifier(email: str, request: Request):  # ❌
       ...
   ```

3. **Don't bypass domain validation**
   ```python
   # Bad - use combine_identifiers()
   key = f"{ip}:{email}"  # May have empty values, no validation
   ```

---

## Future Enhancements

### Potential Additions

1. **Value Object Classes**
   ```python
   @dataclass(frozen=True)
   class Identifier:
       value: str
       context: str

       def hashed(self) -> str:
           return hash_identifier(self.value, salt=self.context)
   ```

2. **Domain Events**
   ```python
   @dataclass(frozen=True)
   class IdentifierHashed:
       original_length: int
       hash_length: int
       algorithm: str
   ```

3. **Identifier Repository**
   ```python
   class IdentifierRepository(ABC):
       @abstractmethod
       async def resolve(self, hash: str) -> str | None:
           """Reverse lookup (if needed for audit)."""
   ```

**Note:** These are **future possibilities**, not current requirements. The current implementation is simple and sufficient.

---

## References

### External Resources
- [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [Domain-Driven Design](https://martinfowler.com/bliki/DomainDrivenDesign.html)
- [Value Objects](https://martinfowler.com/bliki/ValueObject.html)

---

**Last Updated:** 2025-12-20
**Status:** Complete and Production-Ready
