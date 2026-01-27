# Examples

## Quick Start

```bash
pip install rate-sync[redis] fastapi uvicorn
docker run -d -p 6379:6379 redis:7-alpine
python multi_tenant_complete.py
```

## Examples

| File | Description |
|------|-------------|
| `multi_tenant_complete.py` | Full multi-tenant FastAPI app with tier-based limiting |
| `rate-sync.toml` | Example configuration with free/pro/enterprise tiers |
| `FLOW_DIAGRAM.md` | Visual flow diagrams for rate limiting |

## Test Different Tiers

```bash
# Free tier (10 req/min)
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-acme" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Pro tier (100 req/min)
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-globex" \
  -d '{"message": "hello"}'
```

See [docs/](../docs/) for advanced configuration and patterns.
