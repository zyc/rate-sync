# Abuse Prevention

Advanced patterns beyond basic rate limiting for detecting and blocking attacks.

## Account Enumeration Prevention

Attackers probe registration/login to discover valid accounts.

```toml
[limiters.register_ip]
store_id = "redis"
algorithm = "sliding_window"
limit = 3
window_seconds = 3600

[limiters.register_global]
store_id = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 60
```

```python
@app.post("/register")
async def register(request: Request, email: str, password: str):
    await check_composite_limit(request, "register_ip", "register_global")

    user = await db.get_user_by_email(email)
    # IMPORTANT: Same response whether email exists or not
    return {"message": "Check your email for confirmation"}
```

## Credential Stuffing Defense

Multi-layer protection with progressive delay.

```toml
[limiters.login_ip]
limit = 10
window_seconds = 300

[limiters.login_credential]
limit = 5
window_seconds = 900

[limiters.login_failed]
limit = 3
window_seconds = 1800
```

```python
async def progressive_delay(failed_count: int):
    delays = {1: 0, 2: 1, 3: 2, 4: 5, 5: 10}
    await asyncio.sleep(delays.get(failed_count, 30))

@app.post("/login")
async def login(request: Request, email: str, password: str):
    email_hash = hash_identifier(email)

    # Check failed attempts and apply delay
    failed_limiter = await get_or_clone_limiter("login_failed", email_hash)
    state = await failed_limiter.get_state()
    await progressive_delay(state.current_usage)

    # Check rate limits
    await check_composite_limit(request, email, "login_ip", "login_credential")

    user = await authenticate(email, password)
    if not user:
        await failed_limiter.acquire()  # Record failure
        raise HTTPException(401, "Invalid credentials")

    return {"token": generate_token(user)}
```

## Resource-Based Limiting

Different limits based on operation cost.

```toml
[limiters.api_read]
rate_per_second = 100.0

[limiters.api_write]
limit = 100
window_seconds = 60

[limiters.api_export]
limit = 5
window_seconds = 86400
```

```python
COST_MAP = {
    "read": "api_read",
    "write": "api_write",
    "export": "api_export",
}

async def rate_limit_by_cost(user_id: str, cost: str):
    limiter = await get_or_clone_limiter(COST_MAP[cost], user_id)
    if not await limiter.try_acquire(timeout=0):
        raise RateLimitExceededError(...)

@app.post("/export/pdf")
async def export_pdf(user: User = Depends(get_current_user)):
    await rate_limit_by_cost(str(user.id), "export")
    return await generate_pdf()
```

## Progressive Blocking

Escalate penalties for repeat offenders.

```python
class EscalationManager:
    def __init__(self):
        self.violations = {}  # identifier -> [timestamps]

    def record_violation(self, identifier: str):
        if identifier not in self.violations:
            self.violations[identifier] = []
        self.violations[identifier].append(datetime.now())

    def get_block_duration(self, identifier: str) -> int:
        count = len(self.violations.get(identifier, []))
        if count <= 3: return 0
        if count <= 5: return 300      # 5 min
        if count <= 10: return 3600    # 1 hour
        return 86400                    # 24 hours

escalation = EscalationManager()

async def check_with_escalation(limiter_id: str, identifier: str):
    duration = escalation.get_block_duration(identifier)
    if duration > 0:
        raise HTTPException(429, f"Blocked for {duration}s")

    limiter = await get_or_clone_limiter(limiter_id, identifier)
    if not await limiter.try_acquire(timeout=0):
        escalation.record_violation(identifier)
        raise RateLimitExceededError(...)
```

## Distributed Attack Detection

Global velocity limiting catches botnet attacks.

```toml
[limiters.global_login]
limit = 1000
window_seconds = 60

[limiters.global_failed]
limit = 100
window_seconds = 60
```

```python
async def check_global_velocity(success: bool):
    global_limiter = await get_or_clone_limiter("global_login", "all")
    if not await global_limiter.try_acquire(timeout=0):
        logger.critical("Global login limit exceeded - possible attack")
        raise HTTPException(503, "Service temporarily unavailable")

    if not success:
        failed_limiter = await get_or_clone_limiter("global_failed", "all")
        if not await failed_limiter.try_acquire(timeout=0):
            logger.critical("Global failed login spike - credential stuffing?")
```

## See Also

- [Authentication Protection](./authentication-protection.md)
- [Monitoring](./monitoring.md)
