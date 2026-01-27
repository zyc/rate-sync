# Tests

## Running Tests

```bash
# Unit tests only (CI default)
poetry run pytest -m "not integration"

# All tests (requires infrastructure)
poetry run pytest

# With coverage
poetry run pytest --cov=ratesync --cov-report=term
```

## Test Markers

| Marker | Description | Requires |
|--------|-------------|----------|
| (none) | Unit tests | Nothing |
| `@pytest.mark.compliance` | Engine compliance tests | Varies |
| `@pytest.mark.integration` | Integration tests | Redis/PostgreSQL |
| `@pytest.mark.redis` | Redis-specific tests | Redis |
| `@pytest.mark.postgres` | PostgreSQL-specific tests | PostgreSQL |
| `@pytest.mark.slow` | Slow tests | Nothing |

## Infrastructure

### Quick Start

```bash
docker compose up -d                 # Start default versions
docker compose --profile all up -d   # Start all versions
```

### Multi-Version Testing

```bash
# Test specific versions
docker compose --profile redis-6 up -d   # Redis 6
docker compose --profile pg-14 up -d     # PostgreSQL 14
docker compose --profile pg-15 up -d     # PostgreSQL 15

# Environment variables
export REDIS_URL="redis://localhost:6379/0"
export POSTGRES_URL="postgresql://postgres:postgres@localhost:5432/ratesync"

# Run integration tests
poetry run pytest -m redis
poetry run pytest -m postgres
```

### Supported Versions

| Backend | Versions Tested | Default |
|---------|-----------------|---------|
| Redis | 6, 7 | 7 |
| PostgreSQL | 14, 15, 16 | 16 |

## Test Structure

| Directory | Purpose |
|-----------|---------|
| `tests/compliance/` | Engine compliance tests (all engines) |
| `tests/engines/` | Engine-specific tests |
| `tests/domain/` | Domain layer tests |
| `tests/integration/` | Integration tests |
| `tests/manual/` | Manual/exploratory tests |

## Adding Tests

### Compliance Tests (engine-agnostic)

```python
# tests/compliance/test_*.py
@pytest.mark.parametrize("engine_name", get_unit_test_engines())
async def test_something(self, engine_name: str, get_factory):
    factory = get_factory(engine_name)
    limiter = await factory(rate_per_second=10.0)
    # Test behavior...
```

### Engine-Specific Tests

```python
# tests/engines/test_*_specific.py
@pytest.mark.redis
@pytest.mark.integration
async def test_redis_specific_feature():
    ...
```
