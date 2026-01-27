# Contributing to rate-sync

Thanks for your interest in contributing! This guide will help you get started.

## Quick Start

```bash
git clone https://github.com/rate-sync/rate-sync.git
cd rate-sync
poetry install
poetry run pytest -m "not integration"
```

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feat/your-feature
```

### 2. Make Changes

- Write code with type hints
- Add tests for new functionality
- Update docstrings (Google style)

### 3. Verify

```bash
poetry run pytest                    # Run tests
poetry run ruff check .              # Lint
poetry run ruff format .             # Format
```

### 4. Commit

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add sliding window support for memory engine
fix: correct token calculation on edge case
docs: clarify Redis configuration options
test: add coverage for composite limiter
refactor: extract validation logic
```

### 5. Submit PR

- Push your branch
- Open a PR against `main`
- Fill in the PR template
- Ensure CI passes

## Code Style

We use **Ruff** for linting and formatting. Key conventions:

| Convention | Example |
|------------|---------|
| Type hints | `def foo(x: str) -> int:` |
| Union syntax | `str \| None` (not `Optional[str]`) |
| Docstrings | Google style |
| Naming | `snake_case` functions, `PascalCase` classes |

All public functions need docstrings:

```python
def acquire(self, timeout: float | None = None) -> bool:
    """Acquire a rate limit slot.

    Args:
        timeout: Max seconds to wait. None waits indefinitely.

    Returns:
        True if acquired, False if timed out.
    """
```

## Testing

### Run Tests

```bash
# Unit tests only (fast)
poetry run pytest -m "not integration"

# All tests with coverage
poetry run pytest --cov=ratesync

# Specific file
poetry run pytest tests/unit/test_composite.py
```

### Integration Tests

Require external services (Redis, PostgreSQL):

```bash
docker compose up -d
export REDIS_URL="redis://localhost:6379/0"
export POSTGRES_URL="postgresql://postgres:postgres@localhost:5432/ratesync"
poetry run pytest -m integration
```

### Write Tests

```python
import pytest

class TestFeature:
    async def test_basic_case(self):
        # Arrange
        limiter = create_test_limiter(limit=10)

        # Act
        result = await limiter.try_acquire()

        # Assert
        assert result.allowed is True
```

## PR Checklist

Before submitting:

- [ ] Tests pass locally
- [ ] Code formatted (`poetry run ruff format .`)
- [ ] Linting passes (`poetry run ruff check .`)
- [ ] New code has tests
- [ ] Public APIs have docstrings

## What to Contribute

- **Good first issues**: Check [issues labeled `good-first-issue`](https://github.com/rate-sync/rate-sync/issues?q=label%3Agood-first-issue)
- **New engines**: See [Contributing a New Engine](https://github.com/rate-sync/rate-sync/blob/main/docs/CONTRIBUTING_NEW_ENGINE.md)
- **Bug fixes**: Always welcome
- **Documentation**: Improvements and examples

## Getting Help

- **Questions**: [GitHub Discussions](https://github.com/rate-sync/rate-sync/discussions)
- **Bugs**: [GitHub Issues](https://github.com/rate-sync/rate-sync/issues)
- **Security**: Email security@rate-sync.dev (do not open public issue)

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be respectful and constructive.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
