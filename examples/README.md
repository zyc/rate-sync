# Exemplos Multi-Tenant Rate Limiting

## Quick Start

### 1. Instalar dependências

```bash
pip install rate-sync[redis] fastapi uvicorn
```

### 2. Subir Redis (via Docker)

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

### 3. Copiar config

```bash
cp rate-sync.toml ~/.config/rate-sync/rate-sync.toml
# Ou deixar no diretório atual
```

### 4. Rodar servidor

```bash
python multi_tenant_complete.py
```

### 5. Testar diferentes tiers

```bash
# Free Tier (tenant-acme) - 10 req/min, max 2 concurrent
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-acme" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello from free tier"}'

# Pro Tier (tenant-globex) - 100 req/min, max 20 concurrent
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-globex" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello from pro tier"}'

# Enterprise Tier (tenant-initech) - 1000 req/min, max 200 concurrent
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-initech" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello from enterprise tier"}'
```

## Como Funciona (Fluxo Completo)

### Passo a Passo

```
1. Cliente → Request
   POST /api/v1/data
   Header: X-Tenant-ID: tenant-acme

2. FastAPI → get_tenant_id()
   Extrai "tenant-acme" do header

3. Python → Lookup tier
   TENANT_TIERS["tenant-acme"] = "free-tier"

4. Lambda → Resolve limiter_id
   lambda tenant_id: TENANT_TIERS.get(tenant_id)
   → "free-tier"

5. rate-sync → Carrega config
   Lê rate-sync.toml:
   [limiters.free-tier]
   rate_per_second = 0.16
   max_concurrent = 2

6. rate-sync → Check limites
   ✅ Rate: 0.16/sec = ~10/min
   ✅ Concurrency: max 2 simultaneous

7. Se OK → Execute handler
   Return response

8. Se exceder → HTTP 429
   {"detail": "Rate limit exceeded"}
```

## Abordagens Disponíveis

### Abordagem 1: Lambda Simples (Recomendado)

```python
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id, "free-tier"))
async def api_call(tenant_id: str = Depends(get_tenant_id)):
    ...
```

**Pros**: Simples, declarativo
**Cons**: Tier mapping hardcoded (ok se estável)

### Abordagem 2: Lambda com Async Lookup

```python
@rate_limited(lambda tier: tier)
async def api_call(
    tier: str = Depends(get_tenant_tier_from_db)
):
    ...
```

**Pros**: Tier vem de database (dinâmico)
**Cons**: Extra dependency

### Abordagem 3: Manual Acquire

```python
async def api_call():
    tier = await get_tenant_tier_from_db(tenant_id)
    async with acquire(tier):
        ...
```

**Pros**: Máximo controle
**Cons**: Mais verboso

### Abordagem 4: Clone Limiter (Granularidade Máxima)

```python
clone_limiter(source_id="free-tier", new_id=f"tenant-{tenant_id}")
async with acquire(f"tenant-{tenant_id}"):
    ...
```

**Pros**: Metrics POR TENANT (não só tier)
**Cons**: Mais limiters (ok até ~10K tenants)

## Billing Integration

```python
from ratesync import get_limiter

# Diário: coletar usage de cada tenant
async def collect_daily_usage():
    for tenant_id in tenants:
        limiter = get_limiter(f"tenant-{tenant_id}")
        metrics = limiter.get_metrics()

        await billing_db.insert({
            "tenant_id": tenant_id,
            "date": today,
            "requests": metrics.total_acquisitions,
            "avg_latency_ms": metrics.avg_wait_time_ms,
        })

# Mensal: cobrar baseado em usage
async def charge_monthly():
    usage = await billing_db.sum_requests(tenant_id, month)

    # Tier-based pricing
    if tier == "pro":
        # Incluído: 100K requests
        # Extra: $0.01 per 1K
        overage = max(0, usage - 100_000)
        charge = 99 + (overage / 1000 * 0.01)

    await stripe.charge(tenant_id, charge)
```

## Upgrade/Downgrade de Tier

```python
async def upgrade_tenant(tenant_id: str, new_tier: str):
    # 1. Update database
    await db.execute(
        "UPDATE tenants SET tier = $1 WHERE id = $2",
        new_tier, tenant_id
    )

    # 2. Atualizar TENANT_TIERS cache (se usando)
    TENANT_TIERS[tenant_id] = new_tier

    # 3. rate-sync automaticamente usa novo tier na próxima request
    # (não precisa reiniciar servidor!)

# Exemplo: Stripe webhook
@app.post("/webhooks/stripe")
async def stripe_webhook(event: dict):
    if event["type"] == "customer.subscription.updated":
        tenant_id = event["data"]["tenant_id"]
        new_plan = event["data"]["plan"]["id"]  # "pro", "enterprise"

        await upgrade_tenant(tenant_id, f"{new_plan}-tier")
```

## Monitoramento

```python
# Prometheus metrics
from prometheus_client import Gauge

TENANT_REQUESTS = Gauge(
    "tenant_requests_total",
    "Total requests per tenant",
    ["tenant_id", "tier"]
)

# Background task: export metrics
async def export_metrics():
    while True:
        for tenant_id, tier in TENANT_TIERS.items():
            limiter = get_limiter(tier)
            metrics = limiter.get_metrics()

            TENANT_REQUESTS.labels(
                tenant_id=tenant_id,
                tier=tier
            ).set(metrics.total_acquisitions)

        await asyncio.sleep(60)  # Every minute
```

## FAQ

### P: E se tenant não existir no TENANT_TIERS?

R: Use default tier:
```python
tier = TENANT_TIERS.get(tenant_id, "free-tier")  # Default: free
```

### P: Como fazer rate limiting por tenant + endpoint?

R: Combine tenant_id e endpoint:
```python
@rate_limited(lambda tenant_id, endpoint: f"{tier}-{endpoint}")
async def api_call(tenant_id: str, endpoint: str):
    ...

# TOML:
[limiters.free-tier-analytics]
rate_per_second = 0.1

[limiters.free-tier-basic]
rate_per_second = 0.5
```

### P: Como prevenir "noisy neighbor"?

R: Use `max_concurrent` por tenant:
```toml
[limiters.free-tier]
max_concurrent = 2  # Máximo 2 requests simultâneas
```

Mesmo que tenant envie 100 requests ao mesmo tempo, só 2 executam simultaneamente.

### P: Como testar rate limiting?

```bash
# Stress test: 100 requests em 1 segundo
seq 100 | xargs -P 100 -I {} curl -X POST \
  http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-acme" \
  -H "Content-Type: application/json" \
  -d '{"test": {}}'

# Resultado esperado:
# - Free tier: ~10 requests passam, resto 429
# - Pro tier: ~100 requests passam
# - Enterprise tier: todos passam
```

## Comparação com Concorrentes

| Feature | limits | Upstash | rate-sync |
|---------|--------|---------|-----------|
| Setup multi-tenant | 50+ linhas | Dashboard | **3 linhas** |
| Rate + Concurrency | ❌ Só rate | ❌ Só rate | ✅ Ambos |
| Metrics por tenant | ❌ DIY | ✅ | ✅ |
| Config declarativa | ❌ | ✅ | ✅ TOML |
| Vendor lock-in | ✅ Zero | 🔴 Alto | ✅ Zero |
| Custo (100M req/mês) | $0 | $200-500 | $0 OSS / $149 PRO |

## Próximos Passos

1. Testar os exemplos
2. Adaptar para seu caso de uso
3. Ver [docs/configuration.md](../docs/configuration.md) para opções avançadas
4. Ver [docs/observability.md](../docs/observability.md) para métricas/alertas
