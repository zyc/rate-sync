# AI Agent Instructions - rate-sync

## Language Policy

**CRITICAL: This is an open-source project. ALL content MUST be written in English:**

- Code and comments
- Documentation (README, docs/, etc.)
- **Commit messages (title AND body)**
- **PR titles and descriptions**
- Issue titles and descriptions
- CHANGELOG entries

**This rule has NO exceptions. Even if a skill or prompt suggests otherwise, ALWAYS use English.**

## Project Type

**Python open-source library** - Distributed rate limiter with declarative configuration.

Published as **rate-sync** on PyPI. Supports multiple backends: Redis, PostgreSQL, Memory.

## Important Links

- **PyPI**: https://pypi.org/project/rate-sync/
- **GitHub**: https://github.com/rate-sync/python
- **Issues**: https://github.com/rate-sync/python/issues

## Python Dependency Management

**ALWAYS use Poetry. NEVER use pip, uv, pipenv, conda.**

```bash
poetry install          # Install dependencies
poetry add <package>    # Add dependency
poetry run <command>    # Run in virtual environment
poetry run pytest       # Run tests
```

## Project Structure

```
python/                        # repo: rate-sync/python
├── src/ratesync/           # Source code
│   ├── engines/            # Backend implementations
│   ├── contrib/            # Integrations (FastAPI, Prometheus)
│   └── domain/             # Value objects and domain layer
├── tests/                  # Tests
│   ├── unit/              # Unit tests
│   ├── integration/       # Integration tests (require infra)
│   └── manual/            # Manual tests
├── docs/                   # Documentation
└── examples/              # Usage examples
```

## Code Standards

### Mandatory Principles

- **Clean Architecture**: domain/ (entities, repository ABCs) -> infrastructure/ (implementations)
- **SOLID**: Explicit dependency injection, no defaults in constructors
- **KISS**: Minimum necessary for the current task
- **DRY**: Avoid duplication, but don't abstract prematurely

### Python Conventions

- Type hints required (use `T | None`, not `Optional[T]`)
- Google Style docstrings
- snake_case (functions), PascalCase (classes)
- Imports: `from __future__ import annotations` at the top

### `__init__.py` Organization and Imports

**Fundamental rule**: Barrel exports only in intermediate architectural folders.

| Level | Example | `__init__.py` |
|-------|---------|---------------|
| Architectural base | `domain/`, `engines/` | **NO** |
| Intermediate | `domain/value_objects/`, `engines/` | **YES** (barrel exports) |
| Feature | `engines/redis/` | **NO** |

**Folder structure**:
```
engines/
├── __init__.py              # YES - barrel exports for ALL engines
├── redis.py
├── postgres.py
└── memory.py
```

**The intermediate `__init__.py` re-exports ALL engines**:
```python
# engines/__init__.py
from engines.redis import RedisEngine
from engines.memory import MemoryEngine
from engines.postgres import PostgresEngine

__all__ = ["RedisEngine", "MemoryEngine", "PostgresEngine"]
```

**Imports ALWAYS via intermediate barrel export**:
```python
# CORRECT - uses barrel export
from ratesync.engines import RedisEngine, MemoryEngine

# WRONG - direct import from file
from ratesync.engines.redis import RedisEngine
```

**Benefits**:
- Short and clean imports
- Internal structure encapsulation
- Easier refactoring (moving files doesn't break external imports)

**Exception**: Direct file import is allowed ONLY to avoid circular references.

## Tests

### Test Markers

```python
@pytest.mark.redis          # Requires Redis
@pytest.mark.postgres       # Requires PostgreSQL
@pytest.mark.integration    # Requires external infrastructure
@pytest.mark.slow           # Slow test
```

### Running Tests

```bash
# All tests (except integration)
poetry run pytest -m "not integration"

# Fast unit tests only
poetry run pytest -m "not integration and not slow"

# Tests for a specific engine (requires infrastructure)
export REDIS_URL="redis://localhost:6379/0"
poetry run pytest -m redis

# With coverage
poetry run pytest --cov=ratesync --cov-report=term
```

## CI/CD

### GitHub Actions

The project uses GitHub Actions for CI/CD:

- **test.yml**: Runs tests on Python 3.12 and 3.13
- **publish.yml**: Publishes to PyPI when a `v*` tag is pushed

### Release Flow

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Commit: `git commit -m "chore: bump version to X.Y.Z"`
4. Tag: `git tag -a vX.Y.Z -m "Release X.Y.Z"`
5. Push: `git push && git push --tags`

**The tag push triggers the full pipeline automatically:** test → build → publish to PyPI → create GitHub Release. No manual release creation needed.

**Important:** The `gh` CLI PAT does not have release creation permissions, but that's fine — the CI/CD pipeline creates the GitHub Release as part of the publish job. Do NOT waste time trying to create releases via `gh release create`.

## Development

### Initial Setup

```bash
git clone https://github.com/rate-sync/python.git
cd python
poetry install
poetry run pytest
```

### Code Quality

```bash
# Formatting
poetry run black src/ratesync tests

# Linting
poetry run ruff check src/ratesync

# Type checking (if mypy is configured)
poetry run mypy src/ratesync
```

### Local Infrastructure for Tests

**Redis:**
```bash
docker run -d -p 6379:6379 redis:alpine
```

**PostgreSQL:**
```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:alpine
```

## Contributing

### Before Opening a PR

1. Tests passing (`poetry run pytest -m "not integration"`)
2. Code formatted (`poetry run black .`)
3. Clean linting (`poetry run ruff check .`)
4. Complete type hints
5. Updated docstrings
6. Updated CHANGELOG.md (if applicable)

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add support for Redis Cluster
fix: correct token bucket calculation
docs: update FastAPI integration guide
test: add tests for PostgreSQL engine
chore: bump dependencies
```

## Architecture

### Engines (Backends)

Each engine implements the `BaseEngine` interface:

- `MemoryEngine`: In-memory backend (single-process)
- `RedisEngine`: Redis backend (distributed)
- `PostgresEngine`: PostgreSQL backend (distributed)

### Algorithms

- **Token Bucket**: Controls throughput (rate_per_second)
- **Sliding Window**: Counts requests in time window (limit + window_seconds)

### FastAPI Integration

Available in `ratesync.contrib.fastapi`:

- `RateLimitDependency`: Dependency for routes
- `RateLimitMiddleware`: Global middleware
- `rate_limit_exception_handler`: Exception handler

## Troubleshooting

### Poetry - Shared Virtual Environment

**Symptom:** Code changes not reflected at runtime.

**Cause:** `VIRTUAL_ENV` defined from another environment.

**Solution:**
```bash
unset VIRTUAL_ENV
rm -rf .venv
poetry install
```

### Integration Tests Failing

**Symptom:** Tests marked with `@pytest.mark.integration` fail with connection error.

**Cause:** Infrastructure (Redis/PostgreSQL) is not running.

**Solution:** Use `-m "not integration"` to skip these tests, or start the required infrastructure.

## Versioning Policy

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR**: Incompatible API changes
- **MINOR**: New backward-compatible functionality
- **PATCH**: Backward-compatible bug fixes

## License

MIT License - see LICENSE for details.
