# Contributing to Rate-Sync

Thank you for your interest in contributing to Rate-Sync! This document provides guidelines and instructions for contributing to the project.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Documentation](#documentation)
- [Pull Request Process](#pull-request-process)
- [Architecture Principles](#architecture-principles)

---

## Code of Conduct

This project adheres to a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to conduct@rate-sync.dev.

---

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Poetry (for dependency management)
- Git
- (Optional) Redis, NATS, or PostgreSQL for integration testing

### Quick Start

```bash
# Clone the repository
git clone https://github.com/rate-sync/rate-sync.git
cd rate-sync

# Install dependencies
poetry install

# Run tests
poetry run pytest

# Run linting
poetry run ruff check .
poetry run ruff format --check .
```

---

## Development Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then:
git clone https://github.com/YOUR-USERNAME/rate-sync.git
cd rate-sync
git remote add upstream https://github.com/rate-sync/rate-sync.git
```

### 2. Install Dependencies

```bash
poetry install
```

This installs:
- Core dependencies
- Development dependencies (pytest, ruff, mypy)
- Optional dependencies (redis, nats, psycopg)

### 3. Set Up Pre-commit Hooks (Optional but Recommended)

```bash
poetry run pre-commit install
```

---

## How to Contribute

### Reporting Bugs

**Before submitting a bug report:**
1. Check existing [issues](https://github.com/rate-sync/rate-sync/issues)
2. Ensure you're using the latest version
3. Try to reproduce with minimal code

**Bug Report Template:**

```markdown
**Describe the bug**
A clear description of what the bug is.

**To Reproduce**
```python
# Minimal code to reproduce
```

**Expected behavior**
What you expected to happen.

**Environment:**
- rate-sync version: X.Y.Z
- Python version: 3.X
- Engine: (memory/redis/nats/postgres)
- OS: (macOS/Linux/Windows)

**Additional context**
Any other relevant information.
```

### Suggesting Features

**Before suggesting a feature:**
1. Check existing issues and discussions
2. Consider if it fits the project scope
3. Think about backward compatibility

**Feature Request Template:**

```markdown
**Problem Statement**
What problem does this solve?

**Proposed Solution**
How should it work?

**Alternatives Considered**
What other approaches did you consider?

**Additional Context**
Any relevant examples or use cases.
```

### Contributing Code

**Good First Issues:**
- Look for issues labeled `good-first-issue`
- Documentation improvements
- Test coverage improvements
- Bug fixes

**Contribution Workflow:**
1. Create an issue (or comment on existing one)
2. Fork and create a feature branch
3. Make your changes
4. Add tests
5. Update documentation
6. Submit a pull request

---

## Coding Standards

### Python Style

We follow **PEP 8** with some modifications:

```python
# ✅ GOOD
from __future__ import annotations

def hash_identifier(
    identifier: str,
    *,
    length: int = 16,
    salt: str = "",
) -> str:
    """Hash a sensitive identifier.

    Args:
        identifier: The identifier to hash
        length: Hash length (default: 16)
        salt: Optional salt for uniqueness

    Returns:
        Truncated hex hash

    Example:
        >>> hash_identifier("user@example.com")
        'a1b2c3d4e5f6g7h8'
    """
    # Implementation
    pass
```

### Key Conventions

1. **Type Hints**: Required for all public APIs
   ```python
   # ✅ GOOD
   def get_limiter(limiter_id: str) -> RateLimiter:
       ...

   # ❌ BAD
   def get_limiter(limiter_id):
       ...
   ```

2. **Modern Union Syntax**: Use `|` instead of `Optional`/`Union`
   ```python
   # ✅ GOOD
   def foo(x: str | None = None) -> int | str:
       ...

   # ❌ BAD
   from typing import Optional, Union
   def foo(x: Optional[str] = None) -> Union[int, str]:
       ...
   ```

3. **Naming Conventions**:
   - Functions/methods: `snake_case`
   - Classes: `PascalCase`
   - Constants: `UPPER_SNAKE_CASE`
   - Private: `_leading_underscore`

4. **Docstrings**: Google Style
   ```python
   def function(arg1: str, arg2: int) -> bool:
       """Short one-line summary.

       Longer description if needed.

       Args:
           arg1: Description of arg1
           arg2: Description of arg2

       Returns:
           Description of return value

       Raises:
           ValueError: When something bad happens

       Example:
           >>> function("test", 42)
           True
       """
   ```

### Code Quality Tools

We use the following tools (all run via `poetry run`):

```bash
# Linting and formatting (Ruff)
poetry run ruff check .          # Check for issues
poetry run ruff check --fix .    # Auto-fix issues
poetry run ruff format .         # Format code

# Type checking (MyPy)
poetry run mypy src/

# Tests
poetry run pytest
poetry run pytest --cov=ratesync  # With coverage
```

---

## Testing Guidelines

### Test Requirements

**All code contributions must include tests.**

- ✅ Unit tests for new features
- ✅ Integration tests for engine implementations
- ✅ Docstring examples should be testable
- ✅ Test coverage should not decrease

### Running Tests

```bash
# All tests
poetry run pytest

# Specific test file
poetry run pytest tests/test_composite.py

# Specific test
poetry run pytest tests/test_composite.py::TestCompositeRateLimiter::test_most_restrictive_strategy

# With coverage
poetry run pytest --cov=ratesync --cov-report=html

# Fast (skip integration tests)
poetry run pytest -m "not integration"
```

### Writing Tests

**Structure:**

```python
import pytest
from ratesync import get_limiter
from ratesync.testing import create_test_limiter, consume_limiter_fully

class TestFeatureName:
    """Test suite for FeatureName."""

    async def test_basic_functionality(self):
        """Test basic usage works as expected."""
        # Arrange
        limiter = create_test_limiter("test", limit=10)

        # Act
        result = await limiter.try_acquire()

        # Assert
        assert result.allowed is True
        assert result.remaining == 9

    async def test_edge_case(self):
        """Test edge case behavior."""
        limiter = create_test_limiter("test", limit=1)
        await consume_limiter_fully(limiter)

        # Should be rejected
        result = await limiter.try_acquire()
        assert result.allowed is False

    @pytest.mark.integration
    async def test_with_redis(self):
        """Test with actual Redis backend."""
        # Integration tests marked separately
        ...
```

**Use Testing Utilities:**

```python
from ratesync.testing import (
    create_test_limiter,       # Quick limiter creation
    consume_limiter_fully,     # Exhaust limiter
    advance_time_and_refill,   # Time-travel testing
)
```

---

## Documentation

### What Needs Documentation

1. **Public APIs**: All public functions/classes must have docstrings
2. **Architecture Changes**: Update `docs/ADR.md`
3. **New Features**: Add pattern guide in `docs/patterns/`
4. **Breaking Changes**: Update `CHANGELOG.md` and migration guide

### Documentation Structure

```
docs/
  README.md                # Main documentation index
  architecture/            # Architecture decisions and analysis
    ADR.md                 # Architecture Decision Records
    domain-layer.md        # Domain layer design
  patterns/                # Usage patterns and guides
    authentication-protection.md
    api-tiering.md
    ...
  engines/                 # Engine-specific documentation
    redis.md
    memory.md
    ...
```

### Writing Documentation

**Pattern Guides Should Include:**
1. Problem statement
2. Complete, runnable code examples
3. Explanation of the approach
4. Production considerations
5. Common pitfalls and solutions

**Example:**

````markdown
# Pattern: Multi-Layer Authentication Protection

## Problem

Login endpoints need protection against:
1. Credential stuffing (too many attempts globally)
2. Account enumeration (too many attempts per email)

## Solution

Use composite rate limiting with IP and IP+Email layers.

```python
from ratesync import CompositeRateLimiter, hash_identifier, combine_identifiers

# Configuration in rate-sync.toml
# [limiters.login_ip]
# engine = "redis"
# limit = 100
# window_seconds = 60

async def login(email: str, password: str, client_ip: str):
    composite = CompositeRateLimiter(
        limiters=["login_ip", "login_email"],
        strategy="most_restrictive",
    )

    email_hash = hash_identifier(email, salt="login")
    identifier = combine_identifiers(client_ip, email_hash)

    result = await composite.check({
        "login_ip": client_ip,
        "login_email": identifier,
    })

    if not result.allowed:
        raise RateLimitExceeded(retry_after=result.retry_after)

    # Proceed with authentication
```

## Production Considerations

- Use Redis in production for distributed rate limiting
- Set appropriate limits based on your threat model
- Monitor `triggered_by` field to identify attack patterns
- Consider different salts for different endpoints

## Common Pitfalls

**Don't hash the entire identifier:**
```python
# ❌ BAD
identifier = hash_identifier(f"{client_ip}:{email}")

# ✅ GOOD
identifier = combine_identifiers(client_ip, hash_identifier(email))
```
````

---

## Pull Request Process

### Before Submitting

- [ ] Tests pass (`poetry run pytest`)
- [ ] Linting passes (`poetry run ruff check .`)
- [ ] Type checking passes (`poetry run mypy src/`)
- [ ] Documentation updated
- [ ] CHANGELOG.md updated (for features/fixes)
- [ ] Commit messages follow convention

### PR Title Format

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add sliding window algorithm for memory engine
fix: correct race condition in Redis limiter
docs: add pattern guide for background jobs
test: improve coverage for composite limiter
refactor: move hash_identifier to domain layer
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `test`: Adding/improving tests
- `refactor`: Code refactoring
- `perf`: Performance improvement
- `chore`: Maintenance tasks

### PR Description Template

```markdown
## Summary
Brief description of what this PR does.

## Motivation
Why is this change needed?

## Changes
- List of key changes
- Another change

## Testing
How was this tested?

## Breaking Changes
Any breaking changes? Migration guide?

## Checklist
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] Backward compatible (or breaking change documented)
```

### Review Process

1. **Automated Checks**: CI must pass
2. **Code Review**: At least one maintainer approval
3. **Documentation Review**: Ensure docs are clear
4. **Testing**: All tests must pass

**Review Criteria:**
- ✅ Follows coding standards
- ✅ Has adequate tests
- ✅ Documentation is clear
- ✅ No breaking changes (or well documented)
- ✅ Follows architecture principles

---

## Architecture Principles

### Core Principles

Read [docs/ADR.md](docs/ADR.md) for full details.

**Key Points:**

1. **Framework-Agnostic Core**
   - Core logic doesn't depend on FastAPI/Flask/Django
   - Framework integrations via `contrib/` modules

2. **Clean Architecture**
   - Domain layer is independent
   - Infrastructure depends on domain (not vice versa)
   - See [docs/domain-layer.md](docs/domain-layer.md)

3. **Backward Compatibility**
   - Avoid breaking changes
   - Use deprecation warnings
   - Provide migration guides

4. **Zero Technical Debt**
   - No TODOs in production code
   - All features fully tested and documented
   - Professional quality throughout

### Directory Structure

```
rate-sync/
  src/ratesync/
    __init__.py          # Public API exports
    core.py              # Core rate limiter logic
    domain/              # Domain layer (framework-agnostic)
      value_objects/     # Value objects (identifiers, etc.)
    engines/             # Backend implementations
      memory.py
      redis.py
      nats.py
      postgres.py
    contrib/             # Framework integrations
      fastapi/           # FastAPI-specific
      prometheus/        # Prometheus metrics
    testing.py           # Testing utilities
```

### Import Conventions

**Barrel Exports:**

```python
# ✅ RECOMMENDED
from ratesync import get_limiter, hash_identifier
from ratesync.engines import RedisRateLimiter
from ratesync.contrib.fastapi import RateLimitDependency

# ⚠️  ALLOWED (but not recommended)
from ratesync.engines.redis import RedisRateLimiter

# ❌ AVOID (bypasses public API)
from ratesync.core import RateLimiter
```

---

## Questions?

- **General Questions**: Open a [Discussion](https://github.com/rate-sync/rate-sync/discussions)
- **Bug Reports**: Open an [Issue](https://github.com/rate-sync/rate-sync/issues)
- **Security Issues**: Email security@rate-sync.dev (do not open public issue)

---

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](LICENSE)).

---

**Thank you for contributing to Rate-Sync!** 🎉
