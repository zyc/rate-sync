# Webhook Delivery

> **Requires:** `pip install rate-sync[redis]` for distributed backends.

Rate limiting outbound webhook deliveries to protect both your infrastructure and your customers' endpoints.

## The Problem

Your platform sends webhooks to customer-provided URLs. Without rate limiting:

- A burst of events (e.g., bulk import) floods a customer's endpoint with thousands of requests in seconds
- A slow or failing endpoint backs up your delivery queue, affecting all other customers
- Your outbound IP gets blocked by customer firewalls or CDNs that detect the spike as an attack

This is fundamentally different from inbound rate limiting — you're protecting _someone else's_ system, not your own.

## Configuration

```toml
# rate-sync.toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL}"

# Per-endpoint delivery rate
[limiters.webhook_endpoint]
store = "redis"
rate_per_second = 10.0
max_concurrent = 5

# Global outbound limit (protect your own IP reputation)
[limiters.webhook_global]
store = "redis"
rate_per_second = 500.0
max_concurrent = 100
```

## Basic Per-Endpoint Delivery

Each customer endpoint gets its own rate limit, preventing one busy endpoint from affecting others.

```python
from ratesync import get_or_clone_limiter

async def deliver_webhook(endpoint_url: str, payload: dict):
    # Each endpoint gets its own limiter
    limiter = await get_or_clone_limiter("webhook_endpoint", endpoint_url)

    async with limiter.acquire_context(timeout=30.0):
        response = await http_client.post(
            endpoint_url,
            json=payload,
            timeout=10.0,
        )
        return response.status_code
```

## Delivery with Retry and Backoff

Webhooks fail. A robust delivery pipeline retries with exponential backoff while respecting rate limits.

```python
import asyncio
from ratesync import get_or_clone_limiter

RETRY_DELAYS = [0, 5, 30, 120, 600, 3600]  # seconds

async def deliver_with_retry(
    endpoint_url: str,
    payload: dict,
    attempt: int = 0,
) -> bool:
    limiter = await get_or_clone_limiter("webhook_endpoint", endpoint_url)

    try:
        async with limiter.acquire_context(timeout=60.0):
            response = await http_client.post(
                endpoint_url,
                json=payload,
                timeout=10.0,
            )

            if response.status_code < 300:
                return True

            if response.status_code >= 500 and attempt < len(RETRY_DELAYS) - 1:
                delay = RETRY_DELAYS[attempt + 1]
                await schedule_retry(endpoint_url, payload, attempt + 1, delay)
                return False

            # 4xx — don't retry (client error)
            logger.warning(
                "Webhook delivery failed with %d: %s",
                response.status_code,
                endpoint_url,
            )
            return False

    except TimeoutError:
        # Rate limit timeout — requeue
        if attempt < len(RETRY_DELAYS) - 1:
            delay = RETRY_DELAYS[attempt + 1]
            await schedule_retry(endpoint_url, payload, attempt + 1, delay)
        return False

async def schedule_retry(
    endpoint_url: str,
    payload: dict,
    attempt: int,
    delay: int,
):
    """Schedule a retry via your job queue (Celery, RQ, etc.)."""
    await job_queue.enqueue_in(
        delay,
        deliver_with_retry,
        endpoint_url,
        payload,
        attempt,
    )
```

## Endpoint Health Tracking

Disable delivery to consistently failing endpoints to avoid wasting resources.

```python
import time
from ratesync import get_or_clone_limiter

class EndpointHealth:
    def __init__(self, failure_threshold: int = 10, cooldown: int = 3600):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._disabled_until: dict[str, float] = {}

    def is_healthy(self, endpoint_url: str) -> bool:
        disabled_until = self._disabled_until.get(endpoint_url, 0)
        if time.time() < disabled_until:
            return False
        if time.time() >= disabled_until and disabled_until > 0:
            # Cooldown expired — re-enable
            self._failures.pop(endpoint_url, None)
            self._disabled_until.pop(endpoint_url, None)
        return True

    def record_failure(self, endpoint_url: str):
        self._failures[endpoint_url] = self._failures.get(endpoint_url, 0) + 1
        if self._failures[endpoint_url] >= self.failure_threshold:
            self._disabled_until[endpoint_url] = time.time() + self.cooldown
            logger.warning(
                "Endpoint disabled for %ds after %d failures: %s",
                self.cooldown,
                self._failures[endpoint_url],
                endpoint_url,
            )

    def record_success(self, endpoint_url: str):
        self._failures.pop(endpoint_url, None)
        self._disabled_until.pop(endpoint_url, None)

health = EndpointHealth()

async def deliver_checked(endpoint_url: str, payload: dict) -> bool:
    if not health.is_healthy(endpoint_url):
        logger.info("Skipping delivery to disabled endpoint: %s", endpoint_url)
        return False

    limiter = await get_or_clone_limiter("webhook_endpoint", endpoint_url)

    try:
        async with limiter.acquire_context(timeout=30.0):
            response = await http_client.post(endpoint_url, json=payload, timeout=10.0)

            if response.status_code < 300:
                health.record_success(endpoint_url)
                return True

            health.record_failure(endpoint_url)
            return False
    except Exception:
        health.record_failure(endpoint_url)
        return False
```

## Global Outbound Protection

A global limiter prevents your servers from becoming an unintentional DDoS source.

```python
from ratesync import get_or_clone_limiter

async def deliver_with_global_limit(endpoint_url: str, payload: dict):
    # Global outbound limit first
    global_limiter = await get_or_clone_limiter("webhook_global", "all")
    if not await global_limiter.try_acquire(timeout=0):
        logger.warning("Global webhook limit reached — queuing delivery")
        await schedule_retry(endpoint_url, payload, attempt=0, delay=5)
        return False

    # Per-endpoint limit
    endpoint_limiter = await get_or_clone_limiter("webhook_endpoint", endpoint_url)
    async with endpoint_limiter.acquire_context(timeout=30.0):
        return await http_client.post(endpoint_url, json=payload, timeout=10.0)
```

## Batch Event Coalescing

When many events fire for the same endpoint in a short window, batch them into a single delivery.

```python
import asyncio
from collections import defaultdict

class WebhookBatcher:
    def __init__(self, flush_interval: float = 2.0, max_batch_size: int = 50):
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._flush_tasks: dict[str, asyncio.Task] = {}

    async def add(self, endpoint_url: str, event: dict):
        self._buffers[endpoint_url].append(event)

        if len(self._buffers[endpoint_url]) >= self.max_batch_size:
            await self._flush(endpoint_url)
        elif endpoint_url not in self._flush_tasks:
            self._flush_tasks[endpoint_url] = asyncio.create_task(
                self._delayed_flush(endpoint_url)
            )

    async def _delayed_flush(self, endpoint_url: str):
        await asyncio.sleep(self.flush_interval)
        await self._flush(endpoint_url)

    async def _flush(self, endpoint_url: str):
        events = self._buffers.pop(endpoint_url, [])
        self._flush_tasks.pop(endpoint_url, None)

        if not events:
            return

        # Single rate-limited delivery for the batch
        await deliver_with_retry(
            endpoint_url,
            payload={"events": events, "count": len(events)},
        )

batcher = WebhookBatcher()

# Instead of delivering each event individually:
async def on_order_updated(order):
    for subscription in await get_webhook_subscriptions(order.tenant_id):
        await batcher.add(subscription.url, order.to_event())
```

## Customer-Configurable Rates

Let customers set their own preferred delivery rate.

```python
from ratesync import clone_limiter, get_or_clone_limiter

async def configure_customer_webhook(
    customer_id: str,
    endpoint_url: str,
    max_per_second: float = 10.0,
):
    """Called when customer configures their webhook settings."""
    clone_limiter(
        "webhook_endpoint",
        f"webhook_endpoint:{endpoint_url}",
        rate_per_second=min(max_per_second, 100.0),  # Cap at 100/s
    )

async def deliver_customer_webhook(endpoint_url: str, payload: dict):
    limiter = await get_or_clone_limiter("webhook_endpoint", endpoint_url)
    async with limiter.acquire_context(timeout=60.0):
        return await http_client.post(endpoint_url, json=payload, timeout=10.0)
```

## See Also

- [Background Jobs](./background-jobs.md) — Rate limiting async workers
- [Multi-Tenant Fairness](./multi-tenant-fairness.md) — Per-tenant isolation
- [Monitoring](./monitoring.md) — Tracking delivery success rates
