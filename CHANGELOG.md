# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Core Features
- **Composite Rate Limiting**: Multi-layer rate limit protection with atomic checking
  - `CompositeRateLimiter` class for coordinating multiple limiters
  - Three strategies: `most_restrictive`, `all_must_pass`, `any_must_pass`
  - Atomic multi-limit checking with detailed error reporting
  - FastAPI integration via `CompositeRateLimitDependency`
  - Reduces code duplication for dual-limit patterns (e.g., IP + IP:Email)

- **Sliding Window Algorithm for Memory Engine**
  - Memory backend now supports both `token_bucket` and `sliding_window` algorithms
  - Enables deterministic testing without Redis dependency
  - Exact quota enforcement for development and testing scenarios
  - Configurable via `algorithm` parameter in TOML config

- **Public API Enhancements**
  - `get_or_clone_limiter()`: Convenience function for dynamic limiter creation
  - `hash_identifier()`: Hash sensitive identifiers (emails, phone numbers) for privacy
  - `combine_identifiers()`: Combine multiple identifiers into compound keys
  - All functions fully type-hinted and documented

- **Configuration Introspection**
  - `get_config()`: Inspect limiter configuration at runtime
  - `get_state()`: Query current limiter state (remaining, reset time, etc.)
  - `list_limiters()`: Enumerate all registered limiters
  - Essential for debugging, monitoring, and dynamic behavior

- **Testing Utilities** (`ratesync.testing`)
  - `consume_limiter_fully()`: Exhaust limiter for negative testing
  - `advance_time_and_refill()`: Time-travel testing for memory backend
  - `create_test_limiter()`: Quick limiter creation for tests
  - Enables reliable, deterministic rate limit testing

- **Domain Layer Architecture**
  - New `domain/value_objects/` structure following Clean Architecture
  - `identifier.py` module with framework-agnostic utilities
  - Proper separation between domain logic and infrastructure
  - 31 comprehensive tests for domain utilities

- **Concurrency Limiting** (`max_concurrent`)
  - New strategy to limit simultaneous operations
  - Complements existing rate limiting (`rate_per_second`)
  - Can be used alone or combined with rate limiting
  - Implemented across all engines: Memory, Redis, NATS, PostgreSQL
  - Redis engine optimized for maximum performance with unified Lua script (single round-trip)
  - Concurrency metrics: `current_concurrent`, `max_concurrent_reached`, `total_releases`
  - `release()` method to release concurrency slots
  - `acquire_context()` now automatically releases concurrency slots on exit

#### Documentation
- **Pattern Guides** (8 comprehensive guides, 2500+ lines)
  - `patterns/README.md` - Pattern catalog overview
  - `patterns/authentication-protection.md` - Multi-layer auth security
  - `patterns/api-tiering.md` - User tier-based limiting
  - `patterns/testing.md` - Testing strategies and best practices
  - `patterns/production-deployment.md` - Production setup and operations
  - `patterns/monitoring.md` - Observability and metrics
  - `patterns/abuse-prevention.md` - Security patterns and threat mitigation
  - `patterns/background-jobs.md` - Async job rate limiting

- **Architecture Documentation**
  - Complete gaps analysis and implementation plans
  - Executive summaries and completion reports
  - Migration guides for existing implementations
  - ADR-style refactoring documentation

### Changed

- **Open Source Preparation**: Cleaned all project metadata for public release
  - Authors updated to generic "Rate-Sync Contributors"
  - Repository URLs updated to `github.com/rate-sync/rate-sync`
  - Contact email changed to `conduct@rate-sync.dev`
  - LICENSE copyright holder updated
  - All documentation links normalized
  - Added clarification note to internal CI/CD workflow

- **Architecture Improvements**
  - `hash_identifier()` and `combine_identifiers()` moved from `contrib/fastapi/ip_utils.py` to `domain/value_objects/identifier.py`
  - Backward compatibility maintained via re-exports
  - Better separation of concerns following Clean Architecture

- **Configuration**
  - `rate_per_second` is now OPTIONAL (default = None = unlimited throughput)
  - `LimiterConfig` requires at least one of `rate_per_second` or `max_concurrent`
  - Memory engine now accepts `algorithm` parameter (`token_bucket` or `sliding_window`)

### Fixed
- Memory engine sliding window implementation for deterministic testing
- Registry configuration parsing for memory + sliding window combination
- Type hints and documentation throughout codebase

## [0.2.0] - 2025-11-01

### Added
- **PostgreSQL Engine** for persistent rate limiting with database coordination
- **Redis Engine** with high-performance Lua scripts for atomic operations
- **Sliding Window Algorithm** for request counting (Redis only)
- **FastAPI Integration**:
  - `RateLimitDependency` for endpoint-level rate limiting
  - `RateLimitMiddleware` for global/path-based rate limiting
  - `RateLimitExceededError` exception with proper 429 responses
  - Rate limit headers (X-RateLimit-Limit, X-RateLimit-Remaining, etc.)
- `clone_limiter()` function for per-user/per-tenant rate limiting
- `fail_closed` option for strict mode when backend fails
- Support for multiple simultaneous limiters

### Changed
- Refactored API for improved type safety
- Store and limiter configuration now separated for better flexibility
- Improved error messages with detailed context

## [0.1.0] - 2025-10-15

### Added
- Initial release
- **Memory Engine**: In-memory rate limiting for development and single-process apps
- **NATS Engine**: Distributed rate limiting via NATS JetStream KV Store
- **Token Bucket Algorithm**: Throughput control with configurable rate
- Full async/await support with asyncio
- Complete type hints throughout the codebase
- Auto-loading configuration from `rate-sync.toml`
- Lazy initialization of limiters on first use
- `@rate_limited` decorator for declarative rate limiting
- Built-in observability metrics (`RateLimiterMetrics`)
- Environment variable expansion in TOML configuration (`${VAR}` syntax)

---

## Change Types

- **Added**: New features
- **Changed**: Changes to existing functionality
- **Deprecated**: Features that will be removed in future versions
- **Removed**: Features that have been removed
- **Fixed**: Bug fixes
- **Security**: Security vulnerability fixes
