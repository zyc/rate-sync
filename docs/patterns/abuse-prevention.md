# Abuse Prevention Pattern

Advanced patterns for detecting and preventing various abuse vectors beyond basic rate limiting.

---

## Problem

Basic rate limiting stops simple abuse, but sophisticated attackers use:

1. **Distributed attacks** - Rotating IPs to bypass IP-based limits
2. **Slow attacks** - Staying under limits but causing cumulative damage
3. **Account enumeration** - Discovering valid usernames/emails
4. **Credential stuffing** - Using leaked credentials from other sites
5. **Resource exhaustion** - Expensive operations that don't need high frequency
6. **API abuse** - Scraping, automated tools, competitor intelligence

Single-dimension rate limiting (just IP or just user) isn't enough.

---

## Solution

Use **layered defense** with multiple detection strategies:

1. **Composite limiting** - Multiple dimensions (IP + credential + behavioral)
2. **Progressive delays** - Exponential backoff for repeated failures
3. **Behavioral analysis** - Detect abnormal patterns
4. **Resource-based limiting** - Cost-aware rate limiting
5. **Temporary blocks** - Escalate from slow to block
6. **CAPTCHA integration** - Human verification after threshold

---

## Pattern 1: Account Enumeration Prevention

### Problem

Attackers probe registration/login to discover valid accounts:

```
POST /register {"email": "user1@example.com"}  → 409 Conflict (exists)
POST /register {"email": "user2@example.com"}  → 409 Conflict (exists)
POST /register {"email": "user3@example.com"}  → 200 OK (doesn't exist)
```

### Solution: Uniform Response + Aggressive Limiting

```toml
# rate-sync.toml

# Very tight limit for registration checks
[limiters.register_check_ip]
store_id = "redis"
algorithm = "sliding_window"
limit = 3
window_seconds = 3600  # 3 per hour per IP

[limiters.register_check_global]
store_id = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 60  # 100 per minute globally
```

```python
from ratesync.composite import CompositeRateLimiter

async def check_registration_limit(request: Request, email: str):
    """Prevent account enumeration via registration endpoint."""
    client_ip = get_client_ip(request)

    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "register_check_ip",
            "global": "register_check_global",
        },
        strategy="most_restrictive",
    )

    result = await composite.check(
        identifiers={
            "by_ip": client_ip,
            "global": "all",  # Global limit
        },
        timeout=0,
    )

    if not result.allowed:
        # Don't reveal which limit was hit
        raise RateLimitExceededError(
            identifier=client_ip,
            limit=result.most_restrictive.limit,
            remaining=0,
            reset_at=int(time.time()) + int(result.most_restrictive.reset_in),
            retry_after=int(result.most_restrictive.reset_in),
        )

@app.post("/register")
async def register(
    email: str,
    password: str,
    _: None = Depends(check_registration_limit),
):
    # IMPORTANT: Return same response whether email exists or not
    user = await db.get_user_by_email(email)

    if user:
        # Email exists - but don't reveal it
        return {"message": "If this email is not registered, you will receive a confirmation"}

    # Email doesn't exist - register
    await db.create_user(email, password)
    return {"message": "If this email is not registered, you will receive a confirmation"}
```

**Key Points**:
- Uniform responses (don't leak existence)
- IP-based limiting (3/hour)
- Global limiting (prevent distributed enumeration)
- Log blocked attempts for investigation

---

## Pattern 2: Credential Stuffing Defense

### Problem

Attackers use leaked credentials from other sites:

```
POST /login {"email": "user1@site.com", "password": "leaked123"}
POST /login {"email": "user2@site.com", "password": "leaked456"}
POST /login {"email": "user3@site.com", "password": "leaked789"}
```

### Solution: Multi-Layer + Progressive Delay

```toml
[limiters.login_ip]
store_id = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 300  # 10 per 5 minutes per IP

[limiters.login_credential]
store_id = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 900  # 5 per 15 minutes per credential

[limiters.login_failed_credential]
store_id = "redis"
algorithm = "sliding_window"
limit = 3
window_seconds = 1800  # 3 failed per 30 minutes per credential
```

```python
import asyncio
from ratesync import get_or_clone_limiter, hash_identifier
from ratesync.composite import CompositeRateLimiter

async def progressive_delay(identifier: str, attempt_count: int):
    """Add exponential delay based on failure count."""
    delays = {
        1: 0,
        2: 1,
        3: 2,
        4: 5,
        5: 10,
    }

    delay = delays.get(attempt_count, 30)  # Max 30 seconds
    if delay > 0:
        logger.info(f"Progressive delay: {delay}s for {identifier}")
        await asyncio.sleep(delay)

async def check_login_with_progressive_delay(
    request: Request,
    email: str,
):
    """Multi-layer protection with progressive delay."""
    client_ip = get_client_ip(request)
    email_hash = hash_identifier(email)

    # First check: Normal rate limits
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "login_ip",
            "by_credential": "login_credential",
        },
        strategy="most_restrictive",
    )

    result = await composite.check(
        identifiers={
            "by_ip": client_ip,
            "by_credential": f"{client_ip}:{email_hash}",
        },
        timeout=0,
    )

    if not result.allowed:
        raise RateLimitExceededError(...)

    # Second check: Failed attempts limiter
    failed_limiter = await get_or_clone_limiter(
        "login_failed_credential",
        email_hash,
    )

    state = await failed_limiter.get_state()
    failed_count = state.current_usage

    # Apply progressive delay
    if failed_count > 0:
        await progressive_delay(email_hash, failed_count)

@app.post("/login")
async def login(
    email: str,
    password: str,
    request: Request,
    _: None = Depends(check_login_with_progressive_delay),
):
    """Login with credential stuffing protection."""
    user = await authenticate(email, password)

    if not user:
        # Failed login - increment failed counter
        email_hash = hash_identifier(email)
        failed_limiter = await get_or_clone_limiter(
            "login_failed_credential",
            email_hash,
        )
        await failed_limiter.acquire()

        # Log for analysis
        logger.warning(
            "Failed login attempt",
            email_hash=email_hash,
            ip=get_client_ip(request),
        )

        return {"error": "Invalid credentials"}, 401

    # Successful login - could reset failed counter here
    # (optional: don't reset to maintain history)

    return {"token": generate_token(user)}
```

---

## Pattern 3: Resource-Based Limiting

### Problem

Some endpoints are expensive regardless of frequency:

- PDF generation
- Video processing
- Large data exports
- Complex analytics queries

### Solution: Cost-Aware Rate Limiting

```toml
# Cheap operations: High limits
[limiters.api_read]
store_id = "redis"
algorithm = "token_bucket"
rate_per_second = 100.0

# Medium operations: Moderate limits
[limiters.api_write]
store_id = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 60

# Expensive operations: Very strict limits
[limiters.api_export]
store_id = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 86400  # 5 per day

[limiters.api_analytics]
store_id = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 3600  # 10 per hour
```

```python
from enum import Enum

class EndpointCost(Enum):
    """Operation cost classification."""
    CHEAP = "cheap"
    MEDIUM = "medium"
    EXPENSIVE = "expensive"

COST_LIMITER_MAP = {
    EndpointCost.CHEAP: "api_read",
    EndpointCost.MEDIUM: "api_write",
    EndpointCost.EXPENSIVE: "api_export",
}

async def rate_limit_by_cost(
    user_id: str,
    cost: EndpointCost,
):
    """Rate limit based on operation cost."""
    limiter_id = COST_LIMITER_MAP[cost]
    limiter = await get_or_clone_limiter(limiter_id, user_id)

    if not await limiter.try_acquire(timeout=0):
        state = await limiter.get_state()
        raise RateLimitExceededError(
            identifier=user_id,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=int(state.reset_at - time.time()),
        )

@app.post("/export/pdf")
async def export_pdf(
    user: User = Depends(get_current_user),
):
    """Export PDF with strict rate limiting."""
    await rate_limit_by_cost(str(user.id), EndpointCost.EXPENSIVE)

    pdf_bytes = await generate_pdf()
    return Response(content=pdf_bytes, media_type="application/pdf")

@app.get("/users")
async def list_users(
    user: User = Depends(get_current_user),
):
    """List users with generous rate limiting."""
    await rate_limit_by_cost(str(user.id), EndpointCost.CHEAP)

    users = await db.get_users()
    return {"users": users}
```

---

## Pattern 4: Behavioral Analysis

### Problem

Attackers may stay under rate limits but exhibit suspicious patterns:

- Accessing many different endpoints rapidly
- Predictable timing (automated)
- Sequential ID enumeration
- Unusual geographic patterns

### Solution: Behavioral Scoring

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import deque

@dataclass
class BehaviorMetrics:
    """Track user behavior metrics."""
    request_timestamps: deque
    endpoints_accessed: set
    user_agents: set
    countries: set

    def __init__(self):
        self.request_timestamps = deque(maxlen=100)
        self.endpoints_accessed = set()
        self.user_agents = set()
        self.countries = set()

class BehaviorAnalyzer:
    """Analyze request patterns for abuse detection."""

    def __init__(self):
        self.metrics = {}  # identifier -> BehaviorMetrics

    def record_request(
        self,
        identifier: str,
        endpoint: str,
        user_agent: str,
        country: str,
    ):
        """Record request for behavior analysis."""
        if identifier not in self.metrics:
            self.metrics[identifier] = BehaviorMetrics()

        metrics = self.metrics[identifier]
        metrics.request_timestamps.append(datetime.now())
        metrics.endpoints_accessed.add(endpoint)
        metrics.user_agents.add(user_agent)
        metrics.countries.add(country)

    def calculate_suspicion_score(self, identifier: str) -> float:
        """Calculate suspicion score (0-1, higher = more suspicious)."""
        if identifier not in self.metrics:
            return 0.0

        metrics = self.metrics[identifier]
        score = 0.0

        # Check 1: High request frequency
        recent = [
            ts for ts in metrics.request_timestamps
            if datetime.now() - ts < timedelta(minutes=1)
        ]
        if len(recent) > 50:
            score += 0.3

        # Check 2: Many endpoints (scraping?)
        if len(metrics.endpoints_accessed) > 20:
            score += 0.2

        # Check 3: Multiple user agents (automation?)
        if len(metrics.user_agents) > 3:
            score += 0.2

        # Check 4: Multiple countries (proxy/VPN?)
        if len(metrics.countries) > 2:
            score += 0.3

        return min(score, 1.0)

# Global analyzer
analyzer = BehaviorAnalyzer()

async def check_behavior(
    request: Request,
    user_id: str,
):
    """Check behavioral patterns."""
    identifier = f"user:{user_id}"

    # Record request
    analyzer.record_request(
        identifier=identifier,
        endpoint=request.url.path,
        user_agent=request.headers.get("user-agent", "unknown"),
        country=request.headers.get("cf-ipcountry", "unknown"),
    )

    # Calculate suspicion
    score = analyzer.calculate_suspicion_score(identifier)

    if score > 0.7:
        logger.warning(
            "High suspicion score",
            user_id=user_id,
            score=score,
        )

        # Apply stricter rate limit or require CAPTCHA
        raise HTTPException(
            status_code=429,
            detail="Unusual activity detected. Please try again later.",
        )
```

---

## Pattern 5: Temporary Escalation

### Problem

Want to give users chances but escalate for repeated violations.

### Solution: Progressive Blocking

```python
from datetime import datetime, timedelta

class EscalationManager:
    """Manage progressive blocking for repeat offenders."""

    def __init__(self):
        self.violations = {}  # identifier -> list of timestamps

    def record_violation(self, identifier: str):
        """Record a rate limit violation."""
        if identifier not in self.violations:
            self.violations[identifier] = []

        # Clean old violations (older than 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        self.violations[identifier] = [
            ts for ts in self.violations[identifier]
            if ts > cutoff
        ]

        # Add new violation
        self.violations[identifier].append(datetime.now())

    def get_block_duration(self, identifier: str) -> int:
        """Get block duration in seconds based on violation count."""
        if identifier not in self.violations:
            return 0

        count = len(self.violations[identifier])

        # Progressive blocking
        if count <= 3:
            return 0  # Just rate limit
        elif count <= 5:
            return 300  # 5 minutes
        elif count <= 10:
            return 3600  # 1 hour
        elif count <= 20:
            return 86400  # 24 hours
        else:
            return 604800  # 7 days

    def is_blocked(self, identifier: str) -> tuple[bool, int]:
        """Check if identifier is currently blocked."""
        if identifier not in self.violations:
            return False, 0

        duration = self.get_block_duration(identifier)
        if duration == 0:
            return False, 0

        # Check if most recent violation is still within block period
        latest_violation = self.violations[identifier][-1]
        elapsed = (datetime.now() - latest_violation).total_seconds()

        if elapsed < duration:
            remaining = int(duration - elapsed)
            return True, remaining

        return False, 0

# Global manager
escalation = EscalationManager()

async def check_with_escalation(
    limiter_id: str,
    identifier: str,
):
    """Rate limit check with progressive blocking."""

    # Check if temporarily blocked
    blocked, remaining = escalation.is_blocked(identifier)
    if blocked:
        logger.warning(
            "Temporary block active",
            identifier=identifier,
            remaining_seconds=remaining,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Temporarily blocked for {remaining}s due to repeated violations",
        )

    # Normal rate limit check
    limiter = await get_or_clone_limiter(limiter_id, identifier)
    allowed = await limiter.try_acquire(timeout=0)

    if not allowed:
        # Record violation
        escalation.record_violation(identifier)

        state = await limiter.get_state()
        raise RateLimitExceededError(
            identifier=identifier,
            limit=state.current_usage + state.remaining,
            remaining=0,
            reset_at=state.reset_at,
            retry_after=int(state.reset_at - time.time()),
        )

    return True
```

---

## Pattern 6: CAPTCHA Integration

### Problem

Need to distinguish humans from bots after suspicious activity.

### Solution: Progressive CAPTCHA

```python
from ratesync import get_or_clone_limiter

CAPTCHA_THRESHOLD = 5  # Require CAPTCHA after N failures

async def check_with_captcha(
    request: Request,
    email: str,
    captcha_token: str | None = None,
):
    """Rate limit with CAPTCHA escalation."""
    client_ip = get_client_ip(request)
    email_hash = hash_identifier(email)

    # Check failed attempts
    failed_limiter = await get_or_clone_limiter(
        "login_failed_credential",
        email_hash,
    )

    state = await failed_limiter.get_state()
    failed_count = state.current_usage

    # Require CAPTCHA after threshold
    if failed_count >= CAPTCHA_THRESHOLD:
        if not captcha_token:
            raise HTTPException(
                status_code=400,
                detail="CAPTCHA required due to multiple failed attempts",
                headers={"X-Requires-Captcha": "true"},
            )

        # Verify CAPTCHA
        if not await verify_captcha(captcha_token, client_ip):
            raise HTTPException(
                status_code=400,
                detail="Invalid CAPTCHA",
            )

    # Normal rate limit check
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "login_ip",
            "by_credential": "login_credential",
        },
        strategy="most_restrictive",
    )

    result = await composite.check(
        identifiers={
            "by_ip": client_ip,
            "by_credential": f"{client_ip}:{email_hash}",
        },
        timeout=0,
    )

    if not result.allowed:
        raise RateLimitExceededError(...)

async def verify_captcha(token: str, ip: str) -> bool:
    """Verify CAPTCHA with external service (e.g., reCAPTCHA, hCaptcha)."""
    # Implementation depends on CAPTCHA provider
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": RECAPTCHA_SECRET,
                "response": token,
                "remoteip": ip,
            },
        )

        result = response.json()
        return result.get("success", False)
```

---

## Pattern 7: Distributed Attack Detection

### Problem

Attackers use botnets (many IPs) to bypass IP-based limits.

### Solution: Global Velocity Limiting

```toml
# Global limit across ALL IPs
[limiters.global_login]
store_id = "redis"
algorithm = "sliding_window"
limit = 1000
window_seconds = 60  # 1000 logins/min globally

# Global limit for failed logins
[limiters.global_failed_login]
store_id = "redis"
algorithm = "sliding_window"
limit = 100
window_seconds = 60  # 100 failed/min globally
```

```python
async def check_global_velocity(
    endpoint: str,
    success: bool,
):
    """Check global velocity limits."""

    if endpoint == "login":
        # Check global login rate
        global_limiter = await get_or_clone_limiter("global_login", "all")

        if not await global_limiter.try_acquire(timeout=0):
            logger.critical(
                "Global login rate exceeded - possible distributed attack"
            )
            # Could activate emergency mode, enable CAPTCHA globally, etc.
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable due to high traffic",
            )

        # Check global failed login rate
        if not success:
            failed_limiter = await get_or_clone_limiter("global_failed_login", "all")

            if not await failed_limiter.try_acquire(timeout=0):
                logger.critical(
                    "Global failed login rate exceeded - credential stuffing attack?"
                )
                # Activate enhanced security measures
                raise HTTPException(
                    status_code=503,
                    detail="Service temporarily unavailable",
                )
```

---

## See Also

- [Authentication Protection](./authentication-protection.md) - Multi-layer auth protection
- [Monitoring](./monitoring.md) - Detecting abuse patterns
- [API Tiering](./api-tiering.md) - Different limits for different users
