# File Uploads & Heavy Resources

> **Requires:** `pip install rate-sync[redis]` for distributed backends.

Rate limiting for expensive operations: large file uploads, PDF generation, video processing, bulk imports, and other resource-intensive workloads.

## The Problem

A standard API rate limiter treats all requests equally — a 1KB JSON response and a 500MB file upload both count as "one request." But heavy operations consume disproportionate CPU, memory, bandwidth, and I/O. Without specialized limits:

- Five concurrent 1GB uploads can exhaust server memory
- A bulk CSV import can lock database connections for minutes
- PDF generation can peg CPU at 100% across all cores

These operations need **concurrency-based** limiting (how many at once), not just **rate-based** limiting (how many per second).

## Configuration

```toml
# rate-sync.toml
[stores.redis]
engine = "redis"
url = "${REDIS_URL}"

# File uploads: limit concurrency, not rate
[limiters.upload]
store = "redis"
max_concurrent = 10
timeout = 300.0

# Per-user upload slots
[limiters.upload_user]
store = "redis"
max_concurrent = 2
timeout = 300.0

# PDF generation: limited throughput + concurrency
[limiters.pdf_generate]
store = "redis"
rate_per_second = 5.0
max_concurrent = 3
timeout = 120.0

# Bulk imports: strict concurrency
[limiters.bulk_import]
store = "redis"
max_concurrent = 2
timeout = 600.0

# Daily export quota
[limiters.export_daily]
store = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 86400
```

## Concurrent Upload Slots

Use `max_concurrent` to limit how many uploads run simultaneously. The context manager automatically releases the slot when the upload finishes (or fails).

```python
from ratesync import acquire, get_or_clone_limiter

@app.post("/api/upload")
async def upload_file(file: UploadFile, user_id: str = Depends(get_current_user)):
    # Per-user: max 2 concurrent uploads
    user_limiter = await get_or_clone_limiter("upload_user", user_id)

    try:
        async with user_limiter.acquire_context(timeout=10.0):
            # Global: max 10 concurrent uploads across all users
            async with acquire("upload"):
                saved_path = await save_upload(file)
                return {"path": saved_path}
    except TimeoutError:
        raise HTTPException(
            status_code=429,
            detail="Upload slots full. Please wait and try again.",
            headers={"Retry-After": "30"},
        )
```

## Size-Based Limiting

Different limits based on file size — small files get generous limits, large files get strict ones.

```toml
[limiters.upload_small]
store = "redis"
max_concurrent = 20

[limiters.upload_large]
store = "redis"
max_concurrent = 3
timeout = 600.0
```

```python
from ratesync import get_or_clone_limiter

UPLOAD_TIERS = {
    "small": ("upload_small", 10 * 1024 * 1024),       # < 10MB
    "large": ("upload_large", float("inf")),             # >= 10MB
}

def classify_upload(content_length: int) -> str:
    for tier, (_, max_size) in UPLOAD_TIERS.items():
        if content_length < max_size:
            return tier
    return "large"

@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile,
    user_id: str = Depends(get_current_user),
):
    content_length = int(request.headers.get("content-length", 0))
    tier = classify_upload(content_length)
    limiter_id = UPLOAD_TIERS[tier][0]

    limiter = await get_or_clone_limiter(limiter_id, user_id)

    try:
        async with limiter.acquire_context(timeout=30.0):
            return await process_upload(file, content_length)
    except TimeoutError:
        raise HTTPException(429, f"Too many {tier} uploads in progress")
```

## PDF / Report Generation

CPU-intensive operations need both rate and concurrency limits.

```python
from ratesync import get_or_clone_limiter

@app.post("/api/reports/pdf")
async def generate_pdf(
    report_id: str,
    user_id: str = Depends(get_current_user),
):
    limiter = await get_or_clone_limiter("pdf_generate", user_id)

    try:
        async with limiter.acquire_context(timeout=30.0):
            pdf_bytes = await render_pdf(report_id)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={report_id}.pdf"},
            )
    except TimeoutError:
        raise HTTPException(
            status_code=429,
            detail="PDF generation queue is full. Please try again shortly.",
            headers={"Retry-After": "60"},
        )
```

## Bulk Import Pipeline

Long-running bulk imports need strict concurrency limits and progress tracking.

```python
import asyncio
from ratesync import get_or_clone_limiter

@app.post("/api/import/csv")
async def import_csv(
    file: UploadFile,
    user_id: str = Depends(get_current_user),
):
    limiter = await get_or_clone_limiter("bulk_import", user_id)

    if not await limiter.try_acquire(timeout=0):
        raise HTTPException(
            status_code=429,
            detail="You already have a bulk import running. Please wait for it to complete.",
        )

    # Start background processing — release slot when done
    import_id = generate_import_id()
    asyncio.create_task(run_import(user_id, import_id, file, limiter))

    return {"import_id": import_id, "status": "processing"}

async def run_import(user_id: str, import_id: str, file: UploadFile, limiter):
    try:
        rows = parse_csv(await file.read())
        for i, row in enumerate(rows):
            await process_row(row)
            if i % 100 == 0:
                await update_import_progress(import_id, i, len(rows))
        await mark_import_complete(import_id)
    except Exception as e:
        await mark_import_failed(import_id, str(e))
    finally:
        await limiter.release()
```

## Daily Export Quotas

Expensive exports limited per day, not per second.

```python
import time
from ratesync import get_or_clone_limiter
from ratesync.contrib.fastapi import RateLimitExceededError

@app.post("/api/export/full")
async def full_export(user_id: str = Depends(get_current_user)):
    limiter = await get_or_clone_limiter("export_daily", user_id)

    if not await limiter.try_acquire(timeout=0):
        state = await limiter.get_state()
        raise RateLimitExceededError(
            identifier=user_id,
            limit=10,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=state.reset_at - int(time.time()),
            limiter_id="export_daily",
        )

    return await generate_full_export(user_id)
```

## Combining Upload Limits with API Tiering

Different plans get different upload capabilities.

```toml
[limiters.upload_free]
store = "redis"
max_concurrent = 1
timeout = 60.0

[limiters.upload_pro]
store = "redis"
max_concurrent = 5
timeout = 300.0

[limiters.upload_enterprise]
store = "redis"
max_concurrent = 20
timeout = 600.0
```

```python
async def enforce_upload_limit(
    user_id: str = Depends(get_current_user),
    tier: str = Depends(get_user_tier),
):
    limiter = await get_or_clone_limiter(f"upload_{tier}", user_id)

    try:
        async with limiter.acquire_context(timeout=10.0):
            yield
    except TimeoutError:
        raise HTTPException(
            status_code=429,
            detail=f"Upload limit reached for {tier} plan.",
        )
```

## See Also

- [API Tiering](./api-tiering.md) — Different limits per plan
- [Multi-Tenant Fairness](./multi-tenant-fairness.md) — Per-tenant resource isolation
- [Background Jobs](./background-jobs.md) — Async processing patterns
- [Graceful Degradation](./graceful-degradation.md) — What to do when limits are hit
