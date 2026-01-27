# Background Jobs

Rate limiting for async workers, queues, and scheduled tasks.

## Third-Party API Limiting

Respect external service quotas.

```toml
# rate-sync.toml
[limiters.github_api]
store_id = "redis"
algorithm = "sliding_window"
limit = 5000
window_seconds = 3600

[limiters.stripe_api]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 100.0

[limiters.openai_api]
store_id = "redis"
algorithm = "sliding_window"
limit = 60
window_seconds = 60
```

```python
async def call_github_api(data: dict):
    limiter = await get_or_clone_limiter("github_api", "worker")

    try:
        await limiter.acquire(timeout=30.0)  # Wait up to 30s
        return await github_client.create_issue(data)
    except TimeoutError:
        await requeue_job(data, delay=60)
```

## Queue Processing

Control throughput without blocking.

```python
async def process_queue(queue_name: str, limiter_id: str):
    limiter = await get_or_clone_limiter(limiter_id, f"queue:{queue_name}")
    semaphore = asyncio.Semaphore(10)  # Max 10 concurrent

    async def process_message(message):
        async with semaphore:
            try:
                async with limiter.acquire_context(timeout=30.0):
                    await handle_message(message)
                    await queue.ack(message)
            except TimeoutError:
                await queue.nack(message, requeue=True, delay=30)

    async for message in queue.consume(queue_name):
        asyncio.create_task(process_message(message))
```

## Scheduled Tasks

Prevent overlap across workers.

```python
async def daily_report():
    limiter = await get_or_clone_limiter("daily_report", "all-workers")

    # Only one worker will succeed (leader election)
    if not await limiter.try_acquire(timeout=0):
        return  # Another worker is handling it

    report = await generate_report()
    await send_report(report)
```

## Batch Processing

```python
async def process_batch(items: list, limiter_id: str):
    limiter = await get_or_clone_limiter(limiter_id, "batch")

    for item in items:
        try:
            async with limiter.acquire_context(timeout=60.0):
                await process_item(item)
        except TimeoutError:
            logger.warning("Rate limit timeout - skipping")
```

## Retry with Backoff

```python
async def retry_with_backoff(operation, limiter_id: str, max_retries: int = 5):
    limiter = await get_or_clone_limiter(limiter_id, "retry")

    for attempt in range(max_retries):
        try:
            async with limiter.acquire_context(timeout=30.0):
                return await operation()
        except (TimeoutError, Exception) as e:
            delay = 2 ** attempt
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            else:
                raise
```

## Multi-Service Coordination

```python
async def process_order(order_id: str):
    # Step 1: Payment (Stripe)
    stripe_limiter = await get_or_clone_limiter("stripe_api", "orders")
    async with stripe_limiter.acquire_context(timeout=10.0):
        payment = await stripe.verify(order_id)

    # Step 2: Email (SendGrid)
    email_limiter = await get_or_clone_limiter("sendgrid_api", "orders")
    async with email_limiter.acquire_context(timeout=30.0):
        await sendgrid.send_confirmation(payment.email)
```

## Celery Integration

```python
from celery import Celery

app = Celery('tasks')

@app.task
def sync_user(user_id: str):
    async def _sync():
        limiter = await get_or_clone_limiter("external_api", "celery")
        try:
            await limiter.acquire(timeout=60.0)
            await external_api.sync(user_id)
        except TimeoutError:
            raise sync_user.retry(countdown=60, max_retries=3)

    asyncio.get_event_loop().run_until_complete(_sync())
```

## Best Practices

```python
# Use appropriate timeouts
await limiter.acquire(timeout=5.0)    # Critical path
await limiter.acquire(timeout=60.0)   # Background job
await limiter.acquire(timeout=300.0)  # Batch processing

# Use context managers
async with limiter.acquire_context(timeout=30.0):
    await process()

# Separate limiters per service
github_limiter = await get_or_clone_limiter("github_api", "worker")
stripe_limiter = await get_or_clone_limiter("stripe_api", "worker")
```

## See Also

- [Production Deployment](./production-deployment.md)
- [Monitoring](./monitoring.md)
