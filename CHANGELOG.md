# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- FastAPI optional extra: `pip install rate-sync[fastapi]` now installs FastAPI and Starlette as optional dependencies, consistent with engine extras (`redis`, `postgres`)
- `all` extra now includes FastAPI/Starlette alongside engine dependencies

### Changed
- Rename GitHub repository from `rate-sync/rate-sync` to `rate-sync/python` to support multi-language implementations
- Update all project URLs, badges, documentation links, and clone instructions to new repo path
- RuntimeError messages in `ratesync.contrib.fastapi` modules now reference `pip install rate-sync[fastapi]` instead of `pip install fastapi`
- Updated installation docs across README, docs, and examples to reflect the new `fastapi` extra

## [0.2.2] - 2025-01-27

### Changed
- Migrate repository from `zyc/rate-sync` to `rate-sync/python` organization
- Update all project URLs, badges, and documentation links to new org
- Add project logo (PNG, SVG) and social preview banner
- Add "What It Solves" section to README with real-world use case scenarios

## [0.2.1] - 2025-01-27

### Changed
- README: add "What It Solves" section with real-world problem scenarios and deep-dive links
- README: add missing pattern doc links (Abuse Prevention, Monitoring, Testing)

## [0.2.0] - 2025-01-27

### Added
- PostgreSQL engine for persistent rate limiting
- Redis engine with Lua scripts for atomic operations
- Sliding window algorithm (Redis and Memory engines)
- FastAPI integration: `RateLimitDependency`, `RateLimitMiddleware`, rate limit headers
- `clone_limiter()` for per-user/per-tenant limiting
- `fail_closed` option for strict mode
- Composite rate limiting with `CompositeRateLimiter` (strategies: `most_restrictive`, `all_must_pass`, `any_must_pass`)
- Public API: `get_or_clone_limiter()`, `hash_identifier()`, `combine_identifiers()`
- Configuration introspection: `get_config()`, `get_state()`, `list_limiters()`
- Testing utilities in `ratesync.testing` module
- Domain layer with `domain/value_objects/` structure
- Concurrency limiting via `max_concurrent` parameter
- `release()` method and automatic slot release in `acquire_context()`
- Standardized compliance test suite for multi-engine testing
- Tag-based automated releases with Trusted Publishing
- Pattern guides for authentication, API tiering, testing, monitoring, and more

### Changed
- Refactored API for improved type safety
- Separated store and limiter configuration
- Project metadata updated for open source release
- `hash_identifier()` and `combine_identifiers()` moved to `domain/value_objects/identifier.py`
- `rate_per_second` now optional (requires at least one of `rate_per_second` or `max_concurrent`)
- Memory engine accepts `algorithm` parameter (`token_bucket` or `sliding_window`)
- All project documentation translated to English

### Removed
- NATS engine (replaced by Redis and PostgreSQL engines)

### Fixed
- Memory engine sliding window implementation for deterministic testing
- Registry configuration parsing for memory + sliding window

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
