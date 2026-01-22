# Authentication Protection Pattern

Multi-layer rate limiting for authentication endpoints to prevent brute force attacks, credential stuffing, and account enumeration.

---

## Problem

Authentication endpoints are prime targets for attacks:

1. **Brute Force** - Trying many passwords for one account
2. **Credential Stuffing** - Using leaked credentials from other sites
3. **Account Enumeration** - Discovering valid usernames/emails
4. **Distributed Attacks** - Using botnets to bypass simple IP limiting

A single rate limit is insufficient because:
- Too strict → legitimate users get blocked
- Too loose → attackers can succeed
- IP-only limiting → botnets bypass easily
- Account-only limiting → doesn't protect during enumeration

---

## Solution

Use **Composite Rate Limiting** with multiple strategies:

1. **Layer 1**: Broad IP-based limit (prevent scanning)
2. **Layer 2**: Tight credential-based limit (prevent brute force)
3. **Strategy**: `most_restrictive` (both must pass, report tightest)

---

## Implementation

### Configuration (`rate-sync.toml`)

```toml
# Store configuration
[stores.redis]
strategy = "redis"
url = "${REDIS_URL}"

# Layer 1: Prevent IP-based scanning
# 30 requests per minute per IP
[limiters.verify_code_ip]
store_id = "redis"
algorithm = "sliding_window"
limit = 30
window_seconds = 60

# Layer 2: Prevent credential-based brute force
# 10 requests per 5 minutes per IP+Email combination
[limiters.verify_code_email]
store_id = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 300
```

### Code (FastAPI)

```python
from fastapi import Depends, FastAPI, Request
from ratesync import hash_identifier
from ratesync.composite import CompositeRateLimiter
from ratesync.contrib.fastapi import (
    RateLimitExceededError,
    get_client_ip,
    rate_limit_exception_handler,
)

app = FastAPI()

# Register exception handler
app.add_exception_handler(
    RateLimitExceededError,
    rate_limit_exception_handler,
)


async def check_verify_code_limit(
    request: Request,
    email: str,
) -> None:
    """Rate limit verify code endpoint with composite limiting."""
    client_ip = get_client_ip(request)
    email_hash = hash_identifier(email)

    # Composite rate limiter with most_restrictive strategy
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "verify_code_ip",
            "by_ip_email": "verify_code_email",
        },
        strategy="most_restrictive",
    )

    # Check both limits atomically
    result = await composite.check(
        identifiers={
            "by_ip": client_ip,
            "by_ip_email": f"{client_ip}:{email_hash}",
        },
        timeout=0,  # Non-blocking
    )

    # If blocked, raise error with information about which limit was hit
    if not result.allowed:
        raise RateLimitExceededError(
            identifier=client_ip,
            limit=result.most_restrictive.limit,
            remaining=0,
            reset_at=int(time.time()) + int(result.most_restrictive.reset_in),
            retry_after=int(result.most_restrictive.reset_in),
        )


@app.post("/auth/verify-code")
async def verify_code(
    request: Request,
    email: str,
    code: str,
    _: None = Depends(lambda r, e: check_verify_code_limit(r, e)),
):
    # Verify code logic here
    return {"success": True}
```

---

## Why This Works

### Layer 1: IP-Based Limit (30/min)
- **Stops**: Automated scanners trying many emails
- **Allows**: Legitimate users with occasional typos
- **Granularity**: Coarse, catches broad attacks

### Layer 2: Credential-Based Limit (10/5min)
- **Stops**: Brute force against specific accounts
- **Allows**: 2 attempts per minute on average
- **Granularity**: Fine, protects individual accounts

### Combined Effect
- **Attacker using many IPs**: Blocked by Layer 2 (credential limit)
- **Attacker using one IP**: Blocked by Layer 1 (IP limit)
- **Legitimate user**: Passes both limits easily

---

## Real-World Examples

### Login Endpoint

```python
# rate-sync.toml
[limiters.login_ip]
store_id = "redis"
algorithm = "sliding_window"
limit = 10
window_seconds = 300  # 10 attempts per 5 minutes per IP

[limiters.login_credential]
store_id = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 900  # 5 attempts per 15 minutes per credential

# code.py
async def check_login_limit(request: Request, email: str):
    client_ip = get_client_ip(request)
    email_hash = hash_identifier(email)

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
        # Log which layer triggered
        logger.warning(
            "Login rate limit: ip=%s, triggered_by=%s",
            client_ip,
            result.triggered_by,
        )
        raise RateLimitExceededError(...)
```

### Password Reset

```python
# Protect against account enumeration and abuse
[limiters.reset_ip]
limit = 5
window_seconds = 600  # 5 per 10 minutes per IP

[limiters.reset_email]
limit = 3
window_seconds = 3600  # 3 per hour per email
```

### Registration

```python
# Prevent mass account creation
[limiters.register_ip]
limit = 3
window_seconds = 3600  # 3 per hour per IP

[limiters.register_email_domain]
limit = 10
window_seconds = 86400  # 10 per day per domain
```

---

## Tuning Guidelines

### Too Many False Positives?

**Symptom**: Legitimate users getting blocked

**Solutions**:
1. Increase Layer 1 limit (IP-based)
2. Shorten Layer 2 window (credential-based)
3. Add Layer 3 with longer window for severe cases only

### Still Getting Attacked?

**Symptom**: Attackers succeeding despite limits

**Solutions**:
1. Tighten Layer 2 (credential-based)
2. Add Layer 3 with IP geolocation
3. Implement CAPTCHA after N failures
4. Add progressive delays

### Window Size Selection

| Window | Use Case | Example |
|--------|----------|---------|
| 1 minute | Spam prevention | API endpoints |
| 5 minutes | Brute force protection | Login attempts |
| 1 hour | Account enumeration | Password reset |
| 1 day | Mass abuse prevention | Registration |

### Limit Value Selection

| Limit | Use Case | Notes |
|-------|----------|-------|
| 3-5 | Critical auth operations | Login, password reset |
| 10-20 | Standard auth operations | Verification codes |
| 50-100 | Read-only endpoints | Profile viewing |

---

## Monitoring

### Metrics to Track

```python
# Log when limits are hit
logger.warning(
    "Rate limit exceeded: endpoint=%s, ip=%s, triggered_by=%s, limit=%d",
    "verify_code",
    client_ip,
    result.triggered_by,  # Which layer triggered
    result.most_restrictive.limit,
)

# Track which layer blocks most often
if result.triggered_by == "by_ip":
    metrics.increment("rate_limit.layer1.blocks")
elif result.triggered_by == "by_ip_email":
    metrics.increment("rate_limit.layer2.blocks")
```

### Alerting

**Alert if**:
- Layer 1 blocks spike (potential DDoS/scanning)
- Layer 2 blocks for same credential (targeted attack)
- High ratio of blocks to successful requests (too strict)

---

## Testing

### Unit Tests

```python
import pytest
from ratesync.composite import CompositeRateLimiter
from ratesync import configure_store, configure_limiter

@pytest.fixture(autouse=True)
async def setup():
    configure_store("test", strategy="memory")

    configure_limiter(
        "test_ip",
        store_id="test",
        algorithm="sliding_window",
        limit=3,
        window_seconds=60,
    )

    configure_limiter(
        "test_credential",
        store_id="test",
        algorithm="sliding_window",
        limit=2,
        window_seconds=60,
    )

async def test_ip_layer_triggers_first():
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "test_ip",
            "by_credential": "test_credential",
        },
        strategy="most_restrictive",
    )

    # Exhaust IP limit (3 requests)
    for _ in range(3):
        result = await composite.check(
            identifiers={
                "by_ip": "192.168.1.1",
                "by_credential": "user1",
            }
        )
        assert result.allowed

    # Next request blocked by IP layer
    result = await composite.check(
        identifiers={
            "by_ip": "192.168.1.1",
            "by_credential": "user1",
        }
    )
    assert not result.allowed
    assert result.triggered_by == "by_ip"

async def test_credential_layer_protects_account():
    composite = CompositeRateLimiter(
        limiters={
            "by_ip": "test_ip",
            "by_credential": "test_credential",
        },
        strategy="most_restrictive",
    )

    # Different IPs, same credential
    # Exhaust credential limit (2 requests)
    result1 = await composite.check(
        identifiers={
            "by_ip": "192.168.1.1",
            "by_credential": "target_user",
        }
    )
    assert result1.allowed

    result2 = await composite.check(
        identifiers={
            "by_ip": "192.168.1.2",  # Different IP
            "by_credential": "target_user",  # Same credential
        }
    )
    assert result2.allowed

    # Next attempt blocked despite different IP
    result3 = await composite.check(
        identifiers={
            "by_ip": "192.168.1.3",  # Yet another IP
            "by_credential": "target_user",
        }
    )
    assert not result3.allowed
    assert result3.triggered_by == "by_credential"
```

---

## Advanced: Triple-Layer Protection

For highly sensitive endpoints:

```toml
# Layer 1: Prevent IP scanning (very broad)
[limiters.sensitive_ip]
limit = 50
window_seconds = 60

# Layer 2: Prevent credential brute force (medium)
[limiters.sensitive_credential]
limit = 5
window_seconds = 300

# Layer 3: Prevent sustained attacks (very tight)
[limiters.sensitive_sustained]
limit = 10
window_seconds = 3600
```

```python
composite = CompositeRateLimiter(
    limiters={
        "ip": "sensitive_ip",
        "credential": "sensitive_credential",
        "sustained": "sensitive_sustained",
    },
    strategy="most_restrictive",
)

result = await composite.check(
    identifiers={
        "ip": client_ip,
        "credential": f"{client_ip}:{email_hash}",
        "sustained": email_hash,  # Track long-term abuse
    }
)
```

---

## See Also

- [Abuse Prevention Pattern](./abuse-prevention.md) - Additional protection strategies
- [Testing Pattern](./testing.md) - Testing rate-limited code
- [Monitoring Pattern](./monitoring.md) - Observability best practices
