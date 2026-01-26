# Code Standards

Review the code following the project standards.

## Clean Architecture

- **domain/**: Entities, Value Objects, Repositories (ABC), Services
- **infrastructure/**: Redis, PostgreSQL, Memory backend implementations

Dependencies flow from outside to inside. Domain does NOT depend on infrastructure.

## SOLID

- **S** (Single Responsibility): One class, one responsibility
- **O** (Open/Closed): Open for extension, closed for modification
- **L** (Liskov): Subtypes substitutable for their base types
- **I** (Interface Segregation): Small and specific interfaces
- **D** (Dependency Inversion): Depend on abstractions (ABC), not implementations

## KISS

- Simplest solution that works
- No over-engineering or features "for the future"
- No abstractions for single use

## DRY

- Reusable code goes to shared-*
- Avoid duplication between projects
- Extract when there are 3+ similar uses

## Python Conventions

- Type hints required (`T | None`, not `Optional[T]`)
- Google Style docstrings
- snake_case (functions), PascalCase (classes)
- `from __future__ import annotations` at the top
- No defaults in constructors (explicit DI)

## Logging

- Use `LoggerGateway` from `shared_core.domain.gateways`
- NEVER use `print()` or `logging` directly
- Inject `_logger` via constructor (prefix `_` indicates technical dependency)
- Use structured context: `self._logger.info("msg", context={...})`
- Tests: use `NullLoggerGateway`

## Reference

Complete documentation in `shared-core/docs/architecture/`
