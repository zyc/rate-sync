# Contributing a New Engine

This guide walks you through adding a new backend engine to rate-sync.

## Checklist

- [ ] Implement the `RateLimiter` interface
- [ ] Add test fixtures in `tests/compliance/conftest.py`
- [ ] Register capabilities in `tests/compliance/utils.py`
- [ ] Pass the compliance test suite
- [ ] Add engine-specific tests
- [ ] **Test multiple backend versions**
- [ ] **Document compatible versions**
- [ ] Update documentation and CHANGELOG

## Step 1: Implement the Interface

Create `src/ratesync/engines/your_engine.py`:

```python
from __future__ import annotations

from ratesync.core import RateLimiter, RateLimiterMetrics
from ratesync.schemas import LimiterReadOnlyConfig, LimiterState


class YourRateLimiter(RateLimiter):
    """Rate limiter using YourBackend."""

    def __init__(
        self,
        group_id: str,
        *,
        rate_per_second: float | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        max_concurrent: int | None = None,
        default_timeout: float | None = None,
        fail_closed: bool = False,
        # Your engine-specific params:
        url: str = "your://localhost",
    ) -> None:
        # Validation: must have at least one limiting strategy
        has_rate = rate_per_second is not None
        has_window = limit is not None and window_seconds is not None

        if not has_rate and not has_window and max_concurrent is None:
            raise ValueError("Must specify rate_per_second, limit+window_seconds, or max_concurrent")

        if has_rate and has_window:
            raise ValueError("Cannot use both token_bucket and sliding_window")

        self._group_id = group_id
        self._rate_per_second = rate_per_second
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_concurrent = max_concurrent
        self._default_timeout = default_timeout
        self._fail_closed = fail_closed
        self._url = url
        self._initialized = False
        self._metrics = RateLimiterMetrics()

    # === Required Properties ===

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def rate_per_second(self) -> float | None:
        return self._rate_per_second

    @property
    def limit(self) -> int | None:
        return self._limit

    @property
    def window_seconds(self) -> int | None:
        return self._window_seconds

    @property
    def max_concurrent(self) -> int | None:
        return self._max_concurrent

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def default_timeout(self) -> float | None:
        return self._default_timeout

    @property
    def fail_closed(self) -> bool:
        return self._fail_closed

    @property
    def algorithm(self) -> str:
        if self._rate_per_second is not None:
            return "token_bucket"
        return "sliding_window"

    # === Required Async Methods ===

    async def initialize(self) -> None:
        """Connect to backend. Must be idempotent."""
        if self._initialized:
            return
        # Create connection pool, register scripts, etc.
        self._initialized = True

    async def acquire(self) -> None:
        """Block until slot available."""
        if not self._initialized:
            raise RuntimeError("Not initialized")
        # Your implementation

    async def try_acquire(self, timeout: float = 0) -> bool:
        """Try to acquire with timeout. Returns True if acquired."""
        if not self._initialized:
            raise RuntimeError("Not initialized")
        # Your implementation
        return True

    async def release(self) -> None:
        """Release concurrency slot (no-op if max_concurrent not set)."""
        if not self._initialized:
            raise RuntimeError("Not initialized")
        # Your implementation

    async def reset(self) -> None:
        """Reset this limiter's state."""
        # Clear state for self._group_id

    async def reset_all(self) -> None:
        """Reset ALL limiter data in backend (testing only)."""
        # Clear everything

    async def disconnect(self) -> None:
        """Close connections."""
        self._initialized = False

    # === Required Sync Methods ===

    def get_metrics(self) -> RateLimiterMetrics:
        return self._metrics

    def get_config(self) -> LimiterReadOnlyConfig:
        return LimiterReadOnlyConfig(
            id=self._group_id,
            algorithm=self.algorithm,
            store_id="your_engine",
            rate_per_second=self._rate_per_second,
            max_concurrent=self._max_concurrent,
            timeout=self._default_timeout,
            limit=self._limit,
            window_seconds=self._window_seconds,
            fail_closed=self._fail_closed,
        )
```

## Step 2: Add Test Fixtures

In `tests/compliance/conftest.py`:

```python
@pytest.fixture
async def your_engine_factory(your_url: str) -> AsyncIterator[EngineFactory]:
    """Factory for YourRateLimiter instances."""
    try:
        from ratesync.engines.your_engine import YourRateLimiter
    except ImportError:
        pytest.skip("your-engine library not installed")

    created: list[YourRateLimiter] = []

    async def factory(
        group_id: str | None = None,
        **kwargs,
    ) -> YourRateLimiter:
        if group_id is None:
            group_id = f"test_{uuid.uuid4().hex[:8]}"

        limiter = YourRateLimiter(url=your_url, group_id=group_id, **kwargs)

        try:
            await limiter.initialize()
        except Exception as e:
            pytest.skip(f"Backend not available: {e}")

        created.append(limiter)
        return limiter

    yield factory

    for limiter in created:
        try:
            await limiter.reset()
            await limiter.disconnect()
        except Exception:
            pass
```

## Step 3: Register Capabilities

In `tests/compliance/utils.py`:

```python
ENGINE_CAPABILITIES["your_engine"] = EngineCapabilities(
    name="your_engine",
    supports_token_bucket=True,
    supports_sliding_window=True,
    supports_concurrency=True,
    supports_get_state=False,  # Set True if you implement get_state()
    requires_infrastructure=True,
    markers=("your_engine", "integration"),
)
```

Update `get_factory` fixture in `conftest.py`:

```python
factories = {
    # ...existing...
    "your_engine": your_engine_factory,
}
```

## Step 4: Run Compliance Tests

```bash
export YOUR_ENGINE_URL="your://localhost"
poetry run pytest tests/compliance/ -k "your_engine" -v
```

All these test files must pass:

| Test File | What It Tests |
|-----------|---------------|
| `test_initialization.py` | Idempotent init, state tracking |
| `test_token_bucket.py` | Rate limiting (if supported) |
| `test_sliding_window.py` | Window limiting (if supported) |
| `test_concurrency.py` | Concurrency control (if supported) |
| `test_metrics.py` | Metrics collection |
| `test_config.py` | Configuration introspection |
| `test_cleanup.py` | Reset and disconnect |

## Step 5: Add Engine-Specific Tests

Create `tests/engines/test_your_engine_specific.py`:

```python
import pytest

pytestmark = [
    pytest.mark.your_engine,
    pytest.mark.integration,
    pytest.mark.asyncio,
]


class TestYourEngineSpecific:
    """Tests for engine-specific behavior."""

    async def test_connection_pooling(self, your_engine_factory):
        """Test pool size is respected."""
        # ...

    async def test_reconnection(self, your_engine_factory):
        """Test recovery after connection loss."""
        # ...
```

## Step 6: Test Multiple Versions

New engines **must** be tested against multiple backend versions. This ensures users can rely on the documented compatibility.

### Add to docker-compose.yml

```yaml
# Your engine versions
your-engine-v2:
  image: your-engine:2-alpine
  container_name: rate-sync-your-engine-2
  profiles: ["your-engine-2", "all"]
  ports:
    - "YOUR_PORT:YOUR_PORT"

your-engine-v3:
  image: your-engine:3-alpine
  container_name: rate-sync-your-engine-3
  profiles: ["your-engine-3", "all"]
  ports:
    - "YOUR_PORT_ALT:YOUR_PORT"
```

### Add CI Matrix

In `.github/workflows/test.yml`:

```yaml
integration-your-engine:
  runs-on: ubuntu-latest
  strategy:
    fail-fast: false
    matrix:
      version: ["2", "3"]  # Test at least 2 versions

  services:
    your-engine:
      image: your-engine:${{ matrix.version }}-alpine
      ports:
        - YOUR_PORT:YOUR_PORT

  steps:
    # ... standard setup ...
    - name: Run integration tests
      env:
        YOUR_ENGINE_URL: your://localhost:YOUR_PORT
      run: poetry run pytest -m your_engine -v
```

### Document Compatible Versions

In `docs/engines/your_engine.md`, add a "Compatible Versions" section:

```markdown
## Compatible Versions

| Version | Status | Notes |
|---------|--------|-------|
| v3.x | ✅ Recommended | Current stable |
| v2.x | ✅ Supported | LTS version |
| v1.x | ⚠️ Untested | May work |

Tested in CI: v2, v3
```

## Step 7: Update Documentation

1. Export from `src/ratesync/engines/__init__.py`
2. Add to README.md supported backends list
3. Create `docs/engines/your_engine.md` with configuration guide
4. **Include "Compatible Versions" section** (required)
5. Update CHANGELOG.md

## Example PRs

Reference these PRs for implementation patterns:

- Redis engine: Initial implementation
- PostgreSQL engine: Database-backed implementation

## Questions?

Open a [Discussion](https://github.com/zyc/rate-sync/discussions) or [Issue](https://github.com/zyc/rate-sync/issues).
