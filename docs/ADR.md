# Architecture Decision Records

This document records significant architectural decisions for rate-sync.

---

## ADR-001: Framework-Agnostic Core

**Status:** Accepted | **Date:** 2025-10-15

### Context
Rate-sync could be tightly coupled to FastAPI or designed with a framework-agnostic core.

### Decision
Design a framework-agnostic core with optional integrations via `contrib/` modules.

### Consequences
- Core works with any framework (FastAPI, Flask, Django) or non-web contexts
- Framework integrations are optional dependencies
- Testing possible without any web framework

---

## ADR-002: Multiple Backend Support

**Status:** Accepted | **Date:** 2025-10-15

### Context
Rate limiting can use Memory (single-process), Redis (distributed), or PostgreSQL (persistent).

### Decision
Support multiple backends via a common abstract interface.

### Consequences
- Memory engine for dev/test, distributed engines for production
- All engines implement the same interface
- More code to maintain but greater deployment flexibility

---

## ADR-003: Infrastructure-Level Rate Limiting Only

**Status:** Accepted | **Date:** 2025-12-18

### Context
Rate limiting can occur at infrastructure level (HTTP requests) or domain level (business entities).

### Decision
rate-sync handles only infrastructure-level rate limiting. Domain-level limiting is application responsibility.

### Consequences
- Clear scope boundaries
- Framework-agnostic (no business logic coupling)
- Applications implement domain-level checks in their domain layer

---

## ADR-004: Composite Rate Limiting

**Status:** Accepted | **Date:** 2025-12-19

### Context
Many endpoints need multiple rate limits checked simultaneously (e.g., IP-level AND IP+Email-level).

### Decision
Provide `CompositeRateLimiter` as a first-class feature.

### Consequences
- Common pattern supported out-of-box
- Atomic checking of multiple limits
- Reduces code duplication in consuming applications

---

## ADR-005: Sliding Window for Memory Engine

**Status:** Accepted | **Date:** 2025-12-19

### Context
Memory engine only supported token bucket. Testing needed deterministic behavior without Redis.

### Decision
Implement sliding window algorithm in Memory engine.

### Consequences
- Deterministic testing without Redis
- Exact quota enforcement
- Feature parity with Redis engine

---

## ADR-006: Hash Identifier in Domain Layer

**Status:** Accepted | **Date:** 2025-12-19

### Context
`hash_identifier()` was in `contrib/fastapi/` but is not FastAPI-specific.

### Decision
Move to `domain/value_objects/` following Clean Architecture.

### Consequences
- Domain logic in domain layer
- Reusable across frameworks
- Backward compatibility via re-exports

---

## ADR-007: Pattern Guides Over API Reference

**Status:** Accepted | **Date:** 2025-12-19

### Context
Documentation can prioritize API reference or real-world usage patterns.

### Decision
Prioritize pattern guides with complete, runnable examples.

### Consequences
- Users can copy-paste working solutions
- Covers real-world scenarios
- API reference available in docstrings

---

## ADR-008: Zero Technical Debt Policy

**Status:** Accepted | **Date:** 2025-12-19

### Context
Features could ship with TODOs or be complete before merge.

### Decision
Every feature is complete, tested, and documented before "done".

### Consequences
- Open-source ready from day one
- No accumulating debt backlog
- Professional quality throughout

---

## ADR-009: Barrel Exports

**Status:** Accepted | **Date:** 2025-12-19

### Context
Python modules can use flat structure, deep imports, or barrel exports.

### Decision
Use barrel exports at intermediate architectural layers.

### Consequences
- Clean imports: `from ratesync.engines import RedisEngine`
- Internal structure can change without breaking imports
- Clear public API distinction

---

## ADR-010: Semantic Versioning

**Status:** Accepted | **Date:** 2025-10-15

### Context
Versioning options include SemVer, CalVer, or sequential.

### Decision
Use Semantic Versioning 2.0.0 with strong backward compatibility commitment.

### Consequences
- Standard in Python ecosystem
- Clear communication of change impact
- Deprecation warnings before breaking changes

---

## Summary

| ADR | Decision | Impact |
|-----|----------|--------|
| 001 | Framework-Agnostic Core | Multi-framework support |
| 002 | Multiple Backends | Deployment flexibility |
| 003 | Infrastructure-Level Only | Clear scope |
| 004 | Composite Rate Limiting | Reduced duplication |
| 005 | Sliding Window for Memory | Better testing |
| 006 | Hash Identifier in Domain | Clean architecture |
| 007 | Pattern Guides | Better documentation |
| 008 | Zero Technical Debt | Professional quality |
| 009 | Barrel Exports | Clean imports |
| 010 | Semantic Versioning | Clear versioning |
