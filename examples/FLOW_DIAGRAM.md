# Fluxo Completo: Multi-Tenant Rate Limiting

## Diagrama do Fluxo

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENTE (Tenant ACME)                            │
│                                                                           │
│  curl -X POST http://api.example.com/data                                │
│       -H "X-Tenant-ID: tenant-acme"                                      │
│       -H "Content-Type: application/json"                                │
│       -d '{"message": "hello"}'                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          FASTAPI SERVER                                  │
│                                                                           │
│  @app.post("/data")                                                      │
│  @rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id))           │
│  async def create_data(                                                  │
│      tenant_id: str = Depends(get_tenant_id)  ← Extrai header           │
│  ):                                                                      │
│      ...                                                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
                    ▼                               ▼
        ┌───────────────────────┐      ┌──────────────────────┐
        │  get_tenant_id()      │      │  Lambda Function     │
        │                       │      │                      │
        │  Header: X-Tenant-ID  │      │  TENANT_TIERS.get()  │
        │  → "tenant-acme"      │      │                      │
        └───────────────────────┘      └──────────────────────┘
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  TENANT_TIERS Lookup          │
                    │                               │
                    │  {                            │
                    │    "tenant-acme": "free-tier",│
                    │    "tenant-globex": "pro-tier"│
                    │  }                            │
                    │                               │
                    │  tenant-acme → "free-tier"    │
                    └───────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         RATE-SYNC ENGINE                                 │
│                                                                           │
│  1. Carrega config do TOML:                                              │
│     [limiters.free-tier]                                                 │
│     rate_per_second = 0.16    # ~10/min                                 │
│     max_concurrent = 2                                                   │
│                                                                           │
│  2. Conecta ao Redis:                                                    │
│     redis://localhost:6379/0                                             │
│                                                                           │
│  3. Verifica limites:                                                    │
│     ┌──────────────────────────────────────┐                            │
│     │ Rate Limiting (Token Bucket)         │                            │
│     │ ✓ Tenant usou 8 req nos últimos 60s  │                            │
│     │ ✓ Rate: 0.16/s = ~10/min             │                            │
│     │ ✓ OK para passar                     │                            │
│     └──────────────────────────────────────┘                            │
│                                                                           │
│     ┌──────────────────────────────────────┐                            │
│     │ Concurrency Limiting                 │                            │
│     │ ✓ Tenant tem 1 request em execução   │                            │
│     │ ✓ Max: 2 concurrent                  │                            │
│     │ ✓ OK para passar                     │                            │
│     └──────────────────────────────────────┘                            │
│                                                                           │
│  4. Incrementa contadores no Redis                                       │
│  5. Libera execução                                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        HANDLER EXECUTION                                 │
│                                                                           │
│  async def create_data(...):                                             │
│      # Código do endpoint executa AQUI                                   │
│      result = await process_data(data)                                   │
│      return {"status": "ok", "result": result}                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      RESPONSE AO CLIENTE                                 │
│                                                                           │
│  HTTP 200 OK                                                             │
│  {"status": "ok", "result": {...}}                                       │
│                                                                           │
│  Headers:                                                                │
│    X-RateLimit-Limit: 10                                                │
│    X-RateLimit-Remaining: 2                                             │
│    X-RateLimit-Reset: 1640000000                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

## Caso: Request EXCEDE Limite

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENTE (Tenant ACME)                            │
│                                                                           │
│  # 11ª request em 60 segundos (limite: 10/min)                          │
│  curl -X POST http://api.example.com/data                                │
│       -H "X-Tenant-ID: tenant-acme"                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         RATE-SYNC ENGINE                                 │
│                                                                           │
│  Verifica limites:                                                       │
│     ┌──────────────────────────────────────┐                            │
│     │ Rate Limiting (Token Bucket)         │                            │
│     │ ✗ Tenant usou 10 req nos últimos 60s │                            │
│     │ ✗ Rate: 0.16/s = 10/min              │                            │
│     │ ✗ LIMITE EXCEDIDO                    │                            │
│     └──────────────────────────────────────┘                            │
│                                                                           │
│  Opções:                                                                 │
│  1. Timeout configurado (10s) → Espera slot disponível                  │
│  2. Se timeout expirar → Retorna 429                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      RESPONSE AO CLIENTE                                 │
│                                                                           │
│  HTTP 429 Too Many Requests                                              │
│  {                                                                        │
│    "detail": "Rate limit exceeded",                                      │
│    "retry_after": 45  # segundos                                        │
│  }                                                                        │
│                                                                           │
│  Headers:                                                                │
│    X-RateLimit-Limit: 10                                                │
│    X-RateLimit-Remaining: 0                                             │
│    X-RateLimit-Reset: 1640000045                                        │
│    Retry-After: 45                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

## Comparação: 3 Tenants Diferentes

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        TENANT ACME (Free Tier)                           │
├─────────────────────────────────────────────────────────────────────────┤
│  Request → Lookup → "free-tier"                                          │
│                                                                           │
│  Limites aplicados:                                                      │
│    Rate: 10/min                                                          │
│    Concurrency: 2 simultaneous                                           │
│                                                                           │
│  Cenário: 100 requests simultâneas chegam                                │
│    ✓ 2 executam imediatamente                                           │
│    ⏳ 8 esperam (até completar 10/min)                                   │
│    ✗ 90 retornam 429 (excede rate + timeout)                            │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                      TENANT GLOBEX (Pro Tier)                            │
├─────────────────────────────────────────────────────────────────────────┤
│  Request → Lookup → "pro-tier"                                           │
│                                                                           │
│  Limites aplicados:                                                      │
│    Rate: 100/min                                                         │
│    Concurrency: 20 simultaneous                                          │
│                                                                           │
│  Cenário: 100 requests simultâneas chegam                                │
│    ✓ 20 executam imediatamente                                          │
│    ⏳ 80 esperam em fila                                                 │
│    ✓ Todos completam em ~60s (rate de 100/min)                          │
│    ✗ 0 retornam 429 (dentro dos limites)                                │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                   TENANT INITECH (Enterprise Tier)                       │
├─────────────────────────────────────────────────────────────────────────┤
│  Request → Lookup → "enterprise-tier"                                    │
│                                                                           │
│  Limites aplicados:                                                      │
│    Rate: 1000/min                                                        │
│    Concurrency: 200 simultaneous                                         │
│                                                                           │
│  Cenário: 100 requests simultâneas chegam                                │
│    ✓ Todos 100 executam imediatamente                                   │
│    ✓ Latência mínima (sem espera)                                       │
│    ✓ Pode escalar até 1000/min                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Dados no Redis (Visão Interna)

```
Redis Keys após algumas requests:

# Rate limiting counters (Token Bucket)
ratelimit:free-tier:tokens = "2.4"        # Tokens restantes
ratelimit:free-tier:last_update = "1640000123.456"

# Concurrency tracking
ratelimit:free-tier:concurrent = "1"      # 1 request executando agora
ratelimit:free-tier:concurrent:locks = [
    "request-abc123",  # Request ID em execução
]

# Metrics (para billing)
ratelimit:free-tier:metrics:total = "847"         # Total requests
ratelimit:free-tier:metrics:timeouts = "23"       # Timeouts
ratelimit:free-tier:metrics:avg_wait = "234.5"    # Avg wait ms
```

## Code Flow: Linha por Linha

```python
# 1. Cliente faz request
# curl -H "X-Tenant-ID: tenant-acme" ...

# 2. FastAPI processa headers
@app.post("/data")
async def create_data(tenant_id: str = Depends(get_tenant_id)):
    #                                          ^^^^^^^^^^^^
    #                                          Executa AQUI
    #                                          └─> "tenant-acme"
    ...

# 3. get_tenant_id() extrai header
async def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    return x_tenant_id  # → "tenant-acme"

# 4. rate_limited decorator processa
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id))
#             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#             Lambda executa com tenant_id="tenant-acme"
#             TENANT_TIERS.get("tenant-acme") → "free-tier"

# 5. rate-sync carrega config do TOML
# Procura [limiters.free-tier] no rate-sync.toml
# Encontra:
#   rate_per_second = 0.16
#   max_concurrent = 2

# 6. rate-sync conecta ao Redis
# redis://localhost:6379/0

# 7. rate-sync verifica limites
async def acquire(limiter_id: str):
    limiter = get_limiter(limiter_id)  # "free-tier"

    # 7a. Check rate limit
    await limiter._check_rate_limit()
    # Redis: GET ratelimit:free-tier:tokens
    # Se < 1 token → espera ou 429

    # 7b. Acquire concurrency slot
    await limiter._acquire_concurrency()
    # Redis: INCR ratelimit:free-tier:concurrent
    # Se > max_concurrent → espera ou 429

    # 7c. Se tudo OK, incrementa metrics
    # Redis: INCR ratelimit:free-tier:metrics:total

    yield  # Executa handler

    # 7d. Cleanup: libera concurrency slot
    # Redis: DECR ratelimit:free-tier:concurrent

# 8. Handler executa
async def create_data(...):
    result = await process_data(data)
    return result

# 9. Response ao cliente
# HTTP 200 OK com headers X-RateLimit-*
```

## Resumo Visual

```
Request → Header → Lookup Tier → Config TOML → Redis Check → Handler
          └─┬─┘    └────┬────┘   └────┬────┘   └────┬────┘
            │           │             │             │
      X-Tenant-ID  TENANT_TIERS  [limiters.*]  Rate+Concur
```

**Ponto chave**: O lambda `lambda tenant_id: TENANT_TIERS.get(tenant_id)` é executado DEPOIS que FastAPI já extraiu `tenant_id` do header. Então o lambda recebe `"tenant-acme"` como argumento, faz lookup, e retorna `"free-tier"`.
