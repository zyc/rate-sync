# Architecture Decision Records (ADR)

This document records significant architectural decisions made during the development of rate-sync.

---

## ADR-001: Framework-Agnostic Core Design

**Date:** 2025-10-15
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

Rate-sync aims to be a reusable library for rate limiting across multiple projects. The library could be designed in two ways:
1. Tightly coupled to FastAPI
2. Framework-agnostic core with optional integrations

### Decision

We will **design a framework-agnostic core** with optional framework integrations via `contrib/` modules.

**Structure:**
```
ratesync/
  core.py              # Framework-agnostic
  engines/             # Framework-agnostic
  domain/              # Framework-agnostic
  contrib/
    fastapi/           # FastAPI-specific
    flask/             # Future: Flask-specific
    django/            # Future: Django-specific
```

### Rationale

**Pros:**
- ✅ Works with any web framework (FastAPI, Flask, Django, etc.)
- ✅ Works in non-web contexts (background jobs, CLI tools)
- ✅ Easier to test (no framework mocks needed)
- ✅ Better separation of concerns
- ✅ More attractive for open source (wider audience)

**Cons:**
- ❌ Slightly more complex structure
- ❌ FastAPI users need to import from `contrib.fastapi`

### Consequences

- Core API is pure Python + asyncio
- Framework integrations are optional dependencies
- Testing can be done without any web framework
- Library can be used in non-HTTP contexts

**Example:**
```python
# Core - works anywhere
from ratesync import get_limiter
limiter = await get_limiter("api")

# FastAPI - optional
from ratesync.contrib.fastapi import RateLimitDependency
```

---

## ADR-002: Multiple Backend Support (Memory, Redis, NATS, PostgreSQL)

**Date:** 2025-10-15
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

Rate limiting can be implemented using different storage backends:
- **Memory**: Fast, simple, single-process only
- **Redis**: Fast, distributed, requires Redis server
- **NATS**: Distributed, requires NATS JetStream
- **PostgreSQL**: Persistent, works with existing DB

We could:
1. Support only one backend (e.g., Redis)
2. Support multiple backends with a common interface

### Decision

We will **support multiple backends** via a common `RateLimiter` abstract base class.

### Rationale

**Pros:**
- ✅ Flexibility for different deployment scenarios
- ✅ No Redis dependency for development/testing
- ✅ Can leverage existing infrastructure (PostgreSQL, NATS)
- ✅ Users can choose based on performance/complexity trade-offs

**Cons:**
- ❌ More code to maintain
- ❌ Feature parity challenges across backends
- ❌ More complex testing matrix

### Consequences

- All engines implement the same interface
- Configuration is backend-specific but follows common patterns
- Memory engine for dev/test, distributed engines for production
- Some features may not be available in all backends

**Example:**
```python
# Memory (development)
from ratesync.engines.memory import MemoryRateLimiter

# Redis (production)
from ratesync.engines.redis import RedisRateLimiter
```

---

## ADR-003: Domain-Level Rate Limiting is Out of Scope

**Date:** 2025-12-18
**Status:** ✅ Accepted
**Deciders:** Architecture Team, Product Team

### Context

Rate limiting can occur at two levels:

**Infrastructure Level (HTTP):**
- "Max 100 requests/minute per IP"
- Based on request metadata (IP, user agent, etc.)
- Prevents abuse at the edge

**Domain Level (Business Logic):**
- "Max 10 leads/hour per IP" (based on created Lead entities)
- Based on persisted business entities
- Enforces business rules

We need to decide if rate-sync should handle both or focus on one.

### Decision

Rate-sync will **only handle infrastructure-level rate limiting**. Domain-level rate limiting is the responsibility of consuming applications.

### Rationale

**Why NOT include domain-level rate limiting:**
- ❌ Tightly couples to business entities (leads, orders, etc.)
- ❌ Requires knowledge of domain models
- ❌ Different semantics (entity count vs request count)
- ❌ Different storage (domain repository vs rate limiter)
- ❌ Compliance implications (LGPD audit trails)
- ❌ Not reusable across projects (domain-specific)

**Why focus on infrastructure-level:**
- ✅ Framework-agnostic (doesn't care about business logic)
- ✅ Reusable across all projects
- ✅ Clear separation of concerns
- ✅ Better performance (no database queries)

### Consequences

- rate-sync handles HTTP request-level rate limiting
- Applications implement domain-level rate limiting in their domain layer
- Documentation includes examples of both patterns
- No confusion about responsibility boundaries

**Example:**
```python
# ✅ rate-sync (infrastructure level)
@app.post("/leads")
async def create_lead(
    _: None = Depends(RateLimitDependency("create_lead")),  # 100 req/min
):
    # Business logic here
    pass

# ✅ Application (domain level)
class CreateLeadUseCase:
    async def execute(self, ip: str, data: dict):
        # Check domain-level rate limit
        count = await self.lead_repo.count_by_ip_last_hour(ip)
        if count >= 10:
            raise LeadRateLimitExceeded()  # Business rule

        # Create lead
        await self.lead_repo.save(lead)
```

---

## ADR-004: Composite Rate Limiting as First-Class Feature

**Date:** 2025-12-19
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

Many endpoints need multiple rate limits checked simultaneously:
- Example: Login endpoint needs both IP-level AND IP+Email-level limits
- Pattern: "Dual-limit" or "Multi-layer" rate limiting

This could be:
1. Left to application code (manual implementation)
2. Provided as a first-class library feature

### Decision

We will **provide CompositeRateLimiter as a first-class feature**.

### Rationale

**Pros:**
- ✅ Common pattern across multiple projects
- ✅ Reduces code duplication (eliminates ~120 lines in mobile-api)
- ✅ Atomic checking (all limits checked in one operation)
- ✅ Better error reporting (which limit was triggered)
- ✅ Easier to add/remove layers

**Cons:**
- ❌ More complexity in core library
- ❌ Requires careful strategy design

### Consequences

- `CompositeRateLimiter` added to core
- Three strategies: `most_restrictive`, `all_must_pass`, `any_must_pass`
- FastAPI integration via `CompositeRateLimitDependency`
- Significant code reduction in consuming applications

**Example:**
```python
from ratesync import CompositeRateLimiter

composite = CompositeRateLimiter(
    limiters=["verify_code_ip", "verify_code_email"],
    strategy="most_restrictive",
)

result = await composite.check({
    "verify_code_ip": client_ip,
    "verify_code_email": f"{client_ip}:{hash_identifier(email)}",
})
```

---

## ADR-005: Sliding Window for Memory Engine

**Date:** 2025-12-19
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

The Memory engine only supported **token bucket** algorithm. For testing, we needed:
- Deterministic behavior (no time-based refills)
- Exact quota enforcement
- Predictable test results

Redis has **sliding window** but requires running Redis for tests.

### Decision

We will **implement sliding window algorithm in Memory engine**.

### Rationale

**Pros:**
- ✅ Deterministic testing without Redis
- ✅ Exact quota enforcement (10 requests = 10 requests, not ~10)
- ✅ Better for development/testing scenarios
- ✅ Parity with Redis features

**Cons:**
- ❌ More complexity in Memory engine
- ❌ Memory usage increases (tracking individual requests)

### Consequences

- Memory engine supports both `token_bucket` and `sliding_window`
- Tests can use sliding window for deterministic behavior
- Configuration via `algorithm` parameter in TOML
- Better testing experience overall

**Example:**
```toml
[limiters.test_limiter]
engine = "memory"
algorithm = "sliding_window"  # NEW
limit = 10
window_seconds = 60
```

---

## ADR-006: Hash Identifier in Domain Layer

**Date:** 2025-12-19
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

The `hash_identifier()` function was originally in `contrib/fastapi/ip_utils.py` but:
- It's not FastAPI-specific
- It's a domain concept (identifier privacy)
- It's reusable across frameworks

### Decision

We will **move hash_identifier() to domain/value_objects/identifier.py**.

### Rationale

**Following Clean Architecture:**
- ✅ Domain logic belongs in domain layer
- ✅ Framework-agnostic utilities shouldn't depend on framework
- ✅ Better separation of concerns
- ✅ Can be used without FastAPI

**Backward Compatibility:**
- ✅ Re-export from `contrib.fastapi` for BC
- ✅ Export from root `ratesync` module
- ✅ No breaking changes

### Consequences

- Domain layer created (`domain/value_objects/`)
- `hash_identifier()` and `combine_identifiers()` moved
- Backward compatibility maintained via re-exports
- Better architecture, no breaking changes

**Migration:**
```python
# OLD (still works - backward compatible)
from ratesync.contrib.fastapi import hash_identifier

# NEW (recommended)
from ratesync import hash_identifier
```

---

## ADR-007: Pattern Guides Over API Reference

**Date:** 2025-12-19
**Status:** ✅ Accepted
**Deciders:** Architecture Team, Documentation Team

### Context

Documentation can focus on:
1. **API Reference**: Detailed function signatures and parameters
2. **Pattern Guides**: Real-world usage scenarios with complete examples

Most libraries prioritize API reference, but users often struggle to apply it to their use cases.

### Decision

We will **prioritize pattern guides over API reference**, with 8 comprehensive guides covering all major use cases.

### Rationale

**Pros:**
- ✅ Users can copy-paste working solutions
- ✅ Covers real-world scenarios (not just theory)
- ✅ Reduces support questions
- ✅ Demonstrates best practices
- ✅ More valuable for beginners

**Cons:**
- ❌ More work to maintain
- ❌ Examples can become outdated
- ❌ Still need API reference (in docstrings)

### Consequences

- 8 pattern guides created (2500+ lines)
- Each guide includes complete, runnable code
- API reference is in docstrings (accessible via `help()`)
- Documentation is production-ready for open source

**Guides:**
1. Authentication Protection
2. API Tiering
3. Testing Patterns
4. Production Deployment
5. Monitoring
6. Abuse Prevention
7. Background Jobs
8. Migration from Custom Code

---

## ADR-008: Zero Technical Debt Policy

**Date:** 2025-12-19
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

During implementation, we could:
1. Ship with TODOs and "good enough" code
2. Maintain zero technical debt from the start

### Decision

We will **maintain zero technical debt**. Every feature is complete, tested, and documented before being considered "done".

### Rationale

**Why zero debt:**
- ✅ Library is open-source ready from day one
- ✅ No "we'll fix it later" accumulation
- ✅ Professional quality throughout
- ✅ Easier to maintain long-term

**How we achieve it:**
- ✅ 100% test coverage for new features
- ✅ Complete documentation for every feature
- ✅ Architecture documents explain all decisions
- ✅ No TODOs in production code
- ✅ All deprecations have migration paths

### Consequences

- Slower initial development (but 4x faster than planned anyway!)
- No technical debt backlog
- Library is open-source ready
- High confidence in code quality

**Result:**
- 277 tests passing
- 2500+ lines of documentation
- Zero known bugs
- Zero TODOs in code

---

## ADR-009: Barrel Exports for Organized Imports

**Date:** 2025-12-19
**Status:** ✅ Accepted
**Deciders:** Architecture Team

### Context

Python modules can organize exports in different ways:
1. Flat structure (everything in `__init__.py`)
2. Deep imports (import from specific files)
3. Barrel exports (intermediate `__init__.py` files re-export)

### Decision

We will use **barrel exports at intermediate architectural layers**.

**Convention:**
- ❌ Base architectural layers (`domain/`, `infrastructure/`) - NO exports
- ✅ Intermediate layers (`domain/value_objects/`, `engines/`) - YES exports
- ❌ Feature files (`identifier.py`, `redis.py`) - NO `__init__.py`

### Rationale

**Pros:**
- ✅ Clean imports: `from ratesync.engines import RedisRateLimiter`
- ✅ Encapsulation: Internal structure can change without breaking imports
- ✅ Discoverability: Barrel shows what's public API
- ✅ Refactoring-friendly: Move files without breaking consumers

**Cons:**
- ❌ One extra level of indirection
- ❌ Need to maintain `__all__` lists

### Consequences

- Imports are cleaner and shorter
- Internal refactoring doesn't break external code
- Clear distinction between public and internal APIs
- Easier for users to discover available features

**Example:**
```python
# ✅ GOOD - Barrel export
from ratesync.engines import RedisRateLimiter, MemoryRateLimiter

# ❌ AVOID - Deep import
from ratesync.engines.redis import RedisRateLimiter
from ratesync.engines.memory import MemoryRateLimiter
```

---

## ADR-010: Semantic Versioning with Backward Compatibility

**Date:** 2025-10-15
**Status:** ✅ Accepted
**Deciders:** Product Team, Architecture Team

### Context

Versioning strategies:
1. **Semantic Versioning** (MAJOR.MINOR.PATCH)
2. **Calendar Versioning** (YYYY.MM.DD)
3. **Sequential** (v1, v2, v3)

### Decision

We will use **Semantic Versioning 2.0.0** with a strong commitment to backward compatibility.

**Rules:**
- MAJOR: Breaking changes (increment rarely)
- MINOR: New features, backward compatible
- PATCH: Bug fixes, backward compatible

### Rationale

**Pros:**
- ✅ Standard in Python ecosystem
- ✅ Clear communication of change impact
- ✅ Dependency managers understand it
- ✅ Users know when to expect breaking changes

**Backward Compatibility Commitment:**
- ✅ We avoid breaking changes whenever possible
- ✅ Deprecation warnings before removal (minimum 1 MINOR version)
- ✅ Migration guides for breaking changes
- ✅ Re-exports maintain old import paths

### Consequences

- Version bumps follow semver strictly
- Breaking changes are rare and well-documented
- Users can upgrade MINOR/PATCH versions confidently
- Deprecation process gives users time to migrate

**Example:**
```
0.1.0 → 0.2.0  (new features, BC maintained)
0.2.0 → 0.2.1  (bug fixes)
0.2.1 → 1.0.0  (breaking changes - v1 stable)
1.0.0 → 1.1.0  (new features, BC maintained)
```

---

## Summary

| ADR | Decision | Status | Impact |
|-----|----------|--------|--------|
| ADR-001 | Framework-Agnostic Core | ✅ Accepted | High - Enables multi-framework support |
| ADR-002 | Multiple Backends | ✅ Accepted | High - Flexibility in deployment |
| ADR-003 | No Domain-Level Rate Limiting | ✅ Accepted | High - Clear scope boundaries |
| ADR-004 | Composite Rate Limiting | ✅ Accepted | High - Eliminates code duplication |
| ADR-005 | Sliding Window for Memory | ✅ Accepted | Medium - Better testing experience |
| ADR-006 | Hash Identifier in Domain | ✅ Accepted | Medium - Better architecture |
| ADR-007 | Pattern Guides Over API Ref | ✅ Accepted | High - Better documentation |
| ADR-008 | Zero Technical Debt | ✅ Accepted | High - Professional quality |
| ADR-009 | Barrel Exports | ✅ Accepted | Medium - Cleaner imports |
| ADR-010 | Semantic Versioning | ✅ Accepted | High - Clear version communication |

---

**Last Updated:** 2025-12-20
**Status:** All decisions accepted and implemented
