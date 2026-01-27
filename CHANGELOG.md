# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Composite rate limiting with `CompositeRateLimiter` (strategies: `most_restrictive`, `all_must_pass`, `any_must_pass`)
- Sliding window algorithm for Memory engine
- Public API: `get_or_clone_limiter()`, `hash_identifier()`, `combine_identifiers()`
- Configuration introspection: `get_config()`, `get_state()`, `list_limiters()`
- Testing utilities in `ratesync.testing` module
- Domain layer with `domain/value_objects/` structure
- Concurrency limiting via `max_concurrent` parameter
- `release()` method and automatic slot release in `acquire_context()`
- Pattern guides for authentication, API tiering, testing, monitoring, and more

### Changed
- Project metadata updated for open source release
- `hash_identifier()` and `combine_identifiers()` moved to `domain/value_objects/identifier.py`
- `rate_per_second` now optional (requires at least one of `rate_per_second` or `max_concurrent`)
- Memory engine accepts `algorithm` parameter (`token_bucket` or `sliding_window`)

### Fixed
- Memory engine sliding window implementation for deterministic testing
- Registry configuration parsing for memory + sliding window

## [0.2.0] - 2025-11-01

### Added
- PostgreSQL engine for persistent rate limiting
- Redis engine with Lua scripts for atomic operations
- Sliding window algorithm (Redis only)
- FastAPI integration: `RateLimitDependency`, `RateLimitMiddleware`, rate limit headers
- `clone_limiter()` for per-user/per-tenant limiting
- `fail_closed` option for strict mode

### Changed
- Refactored API for improved type safety
- Separated store and limiter configuration

## [0.1.0] - 2025-10-15

### Added
- Initial release
- Memory engine for single-process apps
- NATS engine for distributed limiting via JetStream KV
- Token bucket algorithm
- Full async/await support
- Auto-loading from `rate-sync.toml`
- `@rate_limited` decorator
- Built-in metrics (`RateLimiterMetrics`)
- Environment variable expansion in TOML (`${VAR}` syntax)
