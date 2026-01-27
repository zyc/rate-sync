# Authentication Protection

Protect auth endpoints against brute force, credential stuffing, and account enumeration using composite rate limiting.

## Configuration

```toml
# rate-sync.toml
[stores.redis]
strategy = "redis"
url = "${REDIS_URL}"

# Layer 1: IP-based (stops scanning)
[limiters.auth_ip]
store_id = "redis"
algorithm = "sliding_window"
limit = 30
window_seconds = 60

# Layer 2: Credential-based (stops brute force)
[limiters.auth_credential]
store_id = "redis"
algorithm = "sliding_window"
limit = 5
window_seconds = 300
```

## Implementation

```python
from fastapi import Depends, FastAPI, Request
from ratesync import hash_identifier
from ratesync.composite import CompositeRateLimiter
from ratesync.contrib.fastapi import RateLimitExceededError, get_client_ip

app = FastAPI()

async def check_auth_limit(request: Request, email: str):
    client_ip = get_client_ip(request)
    email_hash = hash_identifier(email)

    composite = CompositeRateLimiter(
        limiters={"ip": "auth_ip", "credential": "auth_credential"},
        strategy="most_restrictive",
    )

    result = await composite.check(
        identifiers={
            "ip": client_ip,
            "credential": f"{client_ip}:{email_hash}",
        },
        timeout=0,
    )

    if not result.allowed:
        raise RateLimitExceededError(
            identifier=client_ip,
            limit=result.most_restrictive.limit,
            remaining=0,
            reset_at=int(time.time() + result.most_restrictive.reset_in),
            retry_after=int(result.most_restrictive.reset_in),
        )

@app.post("/login")
async def login(request: Request, email: str, password: str):
    await check_auth_limit(request, email)
    # Authentication logic
    return {"token": "..."}
```

## Variations

### Password Reset (Stricter)

```toml
[limiters.reset_ip]
limit = 5
window_seconds = 600

[limiters.reset_email]
limit = 3
window_seconds = 3600
```

### Registration (Anti-Spam)

```toml
[limiters.register_ip]
limit = 3
window_seconds = 3600
```

## How It Works

| Layer | Protects Against | Limit |
|-------|------------------|-------|
| IP | Scanning, enumeration | 30/min |
| Credential | Brute force on specific account | 5/5min |

Attacker using many IPs? Blocked by credential layer.
Attacker using one IP? Blocked by IP layer.

## See Also

- [Abuse Prevention](./abuse-prevention.md)
- [Testing](./testing.md)
