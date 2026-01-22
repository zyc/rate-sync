# Background Jobs Pattern

Rate limiting for asynchronous background jobs, queues, and scheduled tasks.

---

## Problem

Background jobs have unique rate limiting needs:

1. **Third-party API limits** - Respect external service quotas
2. **Resource constraints** - CPU, memory, database connections
3. **Queue processing** - Control throughput without blocking
4. **Scheduled tasks** - Prevent overlap and resource exhaustion
5. **Retry logic** - Back off on failures without hammering services

Unlike HTTP endpoints, background jobs:
- Can wait longer (no user waiting)
- May need precise pacing (API quotas)
- Should handle back-pressure gracefully
- Operate outside request-response cycle

---

## Solution

Use **blocking rate limiting** with appropriate timeouts:

1. **Acquire with timeout** - Wait for slot to become available
2. **Context managers** - Automatic release on completion
3. **Graceful degradation** - Handle timeout as backpressure signal
4. **Cost-based limiting** - Different limits for different job types
5. **External API coordination** - Respect third-party limits

---

## Pattern 1: Third-Party API Rate Limiting

### Problem

External APIs have rate limits you must respect:

- GitHub API: 5000 req/hour
- Stripe API: 100 req/second
- SendGrid: 600 emails/minute
- OpenAI: 60 req/minute (free tier)

### Solution: Precise Rate Limiting with Waiting

```toml
# rate-sync.toml

# GitHub API
[limiters.github_api]
store_id = "redis"
algorithm = "sliding_window"
limit = 5000
window_seconds = 3600  # 5000/hour
timeout = 30.0  # Wait up to 30s

# Stripe API
[limiters.stripe_api]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 100.0  # 100/sec with burst
timeout = 5.0

# SendGrid Email
[limiters.sendgrid_api]
store_id = "redis"
algorithm = "sliding_window"
limit = 600
window_seconds = 60  # 600/minute
timeout = 60.0

# OpenAI API
[limiters.openai_api]
store_id = "redis"
algorithm = "sliding_window"
limit = 60
window_seconds = 60  # 60/minute
timeout = 120.0
```

### Background Job Implementation

```python
from ratesync import get_or_clone_limiter
import logging

logger = logging.getLogger(__name__)

async def process_github_webhook(webhook_data: dict):
    """Process GitHub webhook with rate limiting."""

    # Acquire slot, wait up to 30s if needed
    limiter = await get_or_clone_limiter("github_api", "worker-1")

    try:
        # This will wait if quota is exhausted
        await limiter.acquire(timeout=30.0)

        # Call GitHub API
        result = await github_client.create_issue(webhook_data)

        logger.info("GitHub API call successful", issue_id=result.id)

    except TimeoutError:
        # Couldn't acquire within timeout - requeue job
        logger.warning("GitHub API rate limit timeout - requeueing")
        await requeue_job(webhook_data, delay=60)

    except Exception as e:
        logger.error(f"GitHub API call failed: {e}")
        raise
```

### With Context Manager

```python
async def send_email_batch(emails: list[str]):
    """Send email batch with rate limiting."""

    limiter = await get_or_clone_limiter("sendgrid_api", "email-worker")

    sent_count = 0
    failed_count = 0

    for email in emails:
        try:
            # Acquire with automatic release
            async with limiter.acquire_context(timeout=60.0):
                await sendgrid.send_email(email)
                sent_count += 1

        except TimeoutError:
            # Rate limit timeout - slow down
            logger.warning(f"SendGrid rate limit - pausing")
            await asyncio.sleep(10)
            failed_count += 1

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            failed_count += 1

    logger.info(
        "Email batch complete",
        sent=sent_count,
        failed=failed_count,
    )
```

---

## Pattern 2: Queue Processing with Backpressure

### Problem

Processing queue too fast can exhaust resources or hit rate limits.

### Solution: Controlled Queue Consumption

```python
from ratesync import get_or_clone_limiter
import asyncio

async def process_queue_with_rate_limit(
    queue_name: str,
    limiter_id: str,
    max_concurrent: int = 10,
):
    """Process queue with rate limiting and concurrency control."""

    limiter = await get_or_clone_limiter(limiter_id, f"queue:{queue_name}")
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_message(message):
        """Process single message with rate limiting."""
        async with semaphore:  # Limit concurrent processing
            try:
                # Acquire rate limit slot (will wait if needed)
                async with limiter.acquire_context(timeout=30.0):
                    await handle_message(message)
                    await queue.ack(message)

            except TimeoutError:
                # Rate limit timeout - requeue
                logger.warning("Rate limit timeout - requeueing message")
                await queue.nack(message, requeue=True, delay=30)

            except Exception as e:
                logger.error(f"Message processing failed: {e}")
                await queue.nack(message)

    # Consume queue
    async for message in queue.consume(queue_name):
        # Spawn task to process message
        asyncio.create_task(process_message(message))
```

### Celery Integration

```python
from celery import Celery
from ratesync import get_or_clone_limiter
import asyncio

app = Celery('tasks', broker='redis://localhost:6379/0')

@app.task
def sync_user_data(user_id: str):
    """Sync user data with external API (synchronous Celery task)."""

    # Wrap async rate limiting for Celery
    async def _sync():
        limiter = await get_or_clone_limiter("external_api", "celery-worker")

        try:
            # Wait up to 60s for rate limit slot
            await limiter.acquire(timeout=60.0)

            # Call external API
            data = await external_api.get_user(user_id)
            await db.save_user_data(user_id, data)

            logger.info(f"Synced user {user_id}")

        except TimeoutError:
            # Retry with exponential backoff
            logger.warning(f"Rate limit timeout for user {user_id}")
            raise sync_user_data.retry(countdown=60, max_retries=3)

    # Run in asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(_sync())
```

---

## Pattern 3: Scheduled Task Coordination

### Problem

Multiple workers running scheduled tasks can cause:
- Overlapping execution
- Resource contention
- Duplicate work
- Rate limit exhaustion

### Solution: Leader Election + Rate Limiting

```python
from ratesync import get_or_clone_limiter
from datetime import datetime

async def scheduled_daily_report():
    """Generate daily report - only one worker should run this."""

    limiter = await get_or_clone_limiter("daily_report", "all-workers")

    try:
        # Try to acquire - only one worker will succeed
        # Use timeout=0 for non-blocking (leader election)
        allowed = await limiter.try_acquire(timeout=0)

        if not allowed:
            logger.info("Another worker is generating report, skipping")
            return

        # This worker won the election - generate report
        logger.info("Generating daily report")
        report = await generate_report()
        await send_report(report)

        logger.info("Daily report sent successfully")

    except Exception as e:
        logger.error(f"Daily report failed: {e}")
        raise
```

### APScheduler Integration

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ratesync import get_or_clone_limiter

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('cron', hour=2, minute=0)
async def cleanup_old_data():
    """Cleanup job - run once daily at 2 AM."""

    # Only one instance should run this
    limiter = await get_or_clone_limiter("cleanup_job", "all-workers")

    allowed = await limiter.try_acquire(timeout=0)
    if not allowed:
        logger.info("Cleanup already running on another worker")
        return

    try:
        deleted = await db.delete_old_records()
        logger.info(f"Cleanup complete: {deleted} records deleted")
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")

scheduler.start()
```

---

## Pattern 4: Batch Processing with Pacing

### Problem

Need to process large dataset while respecting rate limits.

### Solution: Chunked Processing with Rate Limiting

```python
from ratesync import get_or_clone_limiter
import asyncio

async def process_batch_with_pacing(
    items: list,
    limiter_id: str,
    chunk_size: int = 100,
):
    """Process large batch in chunks with rate limiting."""

    limiter = await get_or_clone_limiter(limiter_id, "batch-processor")

    # Split into chunks
    chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

    processed = 0
    failed = 0

    for chunk_idx, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {chunk_idx + 1}/{len(chunks)}")

        for item in chunk:
            try:
                # Acquire slot (will wait if needed)
                async with limiter.acquire_context(timeout=60.0):
                    await process_item(item)
                    processed += 1

            except TimeoutError:
                logger.warning("Rate limit timeout - skipping item")
                failed += 1

            except Exception as e:
                logger.error(f"Item processing failed: {e}")
                failed += 1

        # Small pause between chunks
        if chunk_idx < len(chunks) - 1:
            await asyncio.sleep(1)

    logger.info(
        "Batch processing complete",
        total=len(items),
        processed=processed,
        failed=failed,
    )

    return processed, failed
```

### Example: Bulk User Import

```python
async def import_users_from_csv(csv_path: str):
    """Import users from CSV with rate limiting."""

    # Read CSV
    users = await read_csv(csv_path)

    # Configure limiter for database writes
    limiter = await get_or_clone_limiter("db_writes", "import-worker")

    imported = 0

    for user_data in users:
        try:
            # Rate limit database writes
            async with limiter.acquire_context(timeout=10.0):
                await db.create_user(user_data)
                imported += 1

                # Log progress every 100 users
                if imported % 100 == 0:
                    logger.info(f"Imported {imported}/{len(users)} users")

        except TimeoutError:
            logger.error("Database write timeout - stopping import")
            break

        except Exception as e:
            logger.error(f"Failed to import user: {e}")

    logger.info(f"Import complete: {imported}/{len(users)} users imported")
```

---

## Pattern 5: Retry with Exponential Backoff

### Problem

Failed jobs need retry logic that respects rate limits.

### Solution: Exponential Backoff + Rate Limiting

```python
import asyncio
from ratesync import get_or_clone_limiter

async def retry_with_backoff(
    operation,
    limiter_id: str,
    max_retries: int = 5,
    base_delay: float = 1.0,
):
    """Retry operation with exponential backoff and rate limiting."""

    limiter = await get_or_clone_limiter(limiter_id, "retry-worker")

    for attempt in range(max_retries):
        try:
            # Acquire rate limit slot
            async with limiter.acquire_context(timeout=30.0):
                result = await operation()
                return result

        except TimeoutError:
            # Rate limit timeout - use exponential backoff
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"Rate limit timeout - retry {attempt + 1}/{max_retries} in {delay}s"
            )

            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logger.error("Max retries exceeded")
                raise

        except Exception as e:
            # Operation failed - use exponential backoff
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"Operation failed - retry {attempt + 1}/{max_retries} in {delay}s: {e}"
            )

            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                logger.error("Max retries exceeded")
                raise

# Usage
async def sync_data():
    """Sync data with external API using retry logic."""
    await retry_with_backoff(
        operation=lambda: external_api.sync(),
        limiter_id="external_api",
        max_retries=5,
        base_delay=2.0,
    )
```

---

## Pattern 6: Multi-Service Coordination

### Problem

Job interacts with multiple external services, each with different limits.

### Solution: Multiple Limiters in Sequence

```python
from ratesync import get_or_clone_limiter

async def process_order(order_id: str):
    """Process order - calls multiple external services."""

    # Step 1: Verify payment (Stripe API)
    stripe_limiter = await get_or_clone_limiter("stripe_api", "order-processor")
    async with stripe_limiter.acquire_context(timeout=10.0):
        payment = await stripe.verify_payment(order_id)

    # Step 2: Send confirmation email (SendGrid API)
    sendgrid_limiter = await get_or_clone_limiter("sendgrid_api", "order-processor")
    async with sendgrid_limiter.acquire_context(timeout=30.0):
        await sendgrid.send_confirmation(payment.email)

    # Step 3: Update inventory (internal API)
    inventory_limiter = await get_or_clone_limiter("inventory_api", "order-processor")
    async with inventory_limiter.acquire_context(timeout=5.0):
        await inventory.decrement_stock(payment.items)

    logger.info(f"Order {order_id} processed successfully")
```

---

## Pattern 7: Resource Pool Limiting

### Problem

Limit concurrent resource usage (database connections, file handles, memory).

### Solution: Semaphore-Style Limiting

```python
from ratesync import get_or_clone_limiter

async def process_with_resource_limit(
    job_data: dict,
    max_concurrent: int = 10,
):
    """Process job with max concurrent limit."""

    # Configure limiter with max_concurrent
    limiter = await get_or_clone_limiter("concurrent_jobs", "all-workers")

    try:
        # Acquire slot (blocks if at max)
        async with limiter.acquire_context(timeout=60.0):
            # This code will only run when under max_concurrent limit
            result = await process_expensive_job(job_data)
            return result

    except TimeoutError:
        logger.warning("Too many concurrent jobs - requeueing")
        await requeue_job(job_data, delay=30)
```

---

## Monitoring Background Jobs

### Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

job_rate_limit_waits = Counter(
    "background_job_rate_limit_waits_total",
    "Times job waited for rate limit",
    ["limiter_id", "job_type"],
)

job_rate_limit_wait_duration = Histogram(
    "background_job_rate_limit_wait_seconds",
    "Time spent waiting for rate limit",
    ["limiter_id", "job_type"],
)

job_rate_limit_timeouts = Counter(
    "background_job_rate_limit_timeouts_total",
    "Times job timed out waiting for rate limit",
    ["limiter_id", "job_type"],
)

async def monitored_acquire(limiter_id: str, job_type: str, timeout: float):
    """Acquire with monitoring."""
    import time

    start = time.time()
    limiter = await get_or_clone_limiter(limiter_id, f"job:{job_type}")

    try:
        await limiter.acquire(timeout=timeout)

        # Record wait time
        duration = time.time() - start
        if duration > 0.1:  # Only record if we actually waited
            job_rate_limit_waits.labels(
                limiter_id=limiter_id,
                job_type=job_type,
            ).inc()

            job_rate_limit_wait_duration.labels(
                limiter_id=limiter_id,
                job_type=job_type,
            ).observe(duration)

    except TimeoutError:
        job_rate_limit_timeouts.labels(
            limiter_id=limiter_id,
            job_type=job_type,
        ).inc()
        raise
```

---

## Best Practices

### 1. Use Appropriate Timeouts

```python
# Short timeout for critical path
await limiter.acquire(timeout=5.0)

# Longer timeout for background jobs
await limiter.acquire(timeout=60.0)

# Very long timeout for batch processing
await limiter.acquire(timeout=300.0)
```

### 2. Handle Timeouts Gracefully

```python
try:
    await limiter.acquire(timeout=30.0)
    await process()
except TimeoutError:
    # Requeue with delay
    await requeue_job(delay=60)
```

### 3. Use Context Managers

```python
# ✅ GOOD - Automatic release
async with limiter.acquire_context(timeout=30.0):
    await process()

# ❌ BAD - Manual release (error-prone)
await limiter.acquire(timeout=30.0)
try:
    await process()
finally:
    await limiter.release()
```

### 4. Log Rate Limit Events

```python
try:
    await limiter.acquire(timeout=30.0)
except TimeoutError:
    logger.warning(
        "Rate limit timeout",
        limiter_id=limiter_id,
        timeout=30.0,
        job_id=job_id,
    )
    raise
```

### 5. Separate Limiters Per Service

```python
# ✅ GOOD - Separate limiters
github_limiter = await get_or_clone_limiter("github_api", "worker")
stripe_limiter = await get_or_clone_limiter("stripe_api", "worker")

# ❌ BAD - Shared limiter
api_limiter = await get_or_clone_limiter("external_api", "worker")
```

---

## Testing Background Jobs

### Test with Memory Backend

```python
import pytest
from ratesync import configure_store, configure_limiter

@pytest.fixture
def setup_test_limiter():
    configure_store("test", strategy="memory")

    configure_limiter(
        "test_job_limiter",
        store_id="test",
        algorithm="sliding_window",
        limit=5,
        window_seconds=10,
    )

@pytest.mark.asyncio
async def test_background_job_rate_limiting(setup_test_limiter):
    """Test that background job respects rate limit."""
    from ratesync import get_or_clone_limiter

    limiter = await get_or_clone_limiter("test_job_limiter", "test-worker")

    # Should allow 5 jobs
    for _ in range(5):
        await limiter.acquire(timeout=1.0)

    # 6th should timeout
    with pytest.raises(TimeoutError):
        await limiter.acquire(timeout=0.1)
```

---

## See Also

- [Production Deployment](./production-deployment.md) - Deploying background workers
- [Monitoring](./monitoring.md) - Monitoring job queues
- [Testing](./testing.md) - Testing async code
