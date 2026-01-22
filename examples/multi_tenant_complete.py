"""
Exemplo Completo: Multi-Tenant Rate Limiting com rate-sync

Este exemplo mostra EXATAMENTE como implementar rate limiting por tenant tier.
"""

from fastapi import Depends, FastAPI, Header, HTTPException
from ratesync import acquire, clone_limiter, get_limiter, rate_limited

app = FastAPI()

# ==============================================================================
# PASSO 1: Armazenar tier do tenant (normalmente em database)
# ==============================================================================

# Exemplo simplificado - em produção seria PostgreSQL/Redis
TENANT_TIERS = {
    "tenant-acme": "free-tier",
    "tenant-globex": "pro-tier",
    "tenant-initech": "enterprise-tier",
}


# Ou buscar de database
async def get_tenant_tier_from_db(tenant_id: str) -> str:
    """
    Em produção, faria:

    async with db.pool.acquire() as conn:
        result = await conn.fetchrow(
            "SELECT tier FROM tenants WHERE id = $1", tenant_id
        )
        return result['tier']
    """
    # Simplificado para exemplo
    tier = TENANT_TIERS.get(tenant_id, "free-tier")  # Default: free
    return tier


# ==============================================================================
# PASSO 2: Extrair tenant_id da request (Header, JWT, API Key, etc)
# ==============================================================================


async def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    """
    Extrai tenant_id do header X-Tenant-ID.

    Alternativas em produção:
    - JWT token: decode token, extrair claim 'tenant_id'
    - API Key: lookup API key em database, pegar tenant_id
    - Subdomain: request.url.hostname.split('.')[0]
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return x_tenant_id


# ==============================================================================
# ABORDAGEM 1: Lambda com lookup dinâmico
# ==============================================================================


@app.post("/api/v1/data")
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id, "free-tier"))
async def create_data_v1(data: dict, tenant_id: str = Depends(get_tenant_id)):
    """
    Como funciona:
    1. FastAPI executa get_tenant_id() → retorna "tenant-acme"
    2. Lambda executa: TENANT_TIERS.get("tenant-acme", "free-tier") → "free-tier"
    3. rate_limited usa limiter "free-tier" do TOML
    4. Aplica rate_per_second=0.16, max_concurrent=2
    """
    return {"tenant": tenant_id, "data": data, "status": "created"}


# ==============================================================================
# ABORDAGEM 2: Lambda com async lookup (database)
# ==============================================================================

# IMPORTANTE: Lambda NÃO pode ser async, então fazemos lookup antes


async def get_tenant_tier(tenant_id: str) -> str:
    """Dependency que faz lookup assíncrono."""
    return await get_tenant_tier_from_db(tenant_id)


@app.post("/api/v2/data")
@rate_limited(lambda tier: tier)  # Lambda recebe tier diretamente
async def create_data_v2(
    data: dict,
    tenant_id: str = Depends(get_tenant_id),
    tier: str = Depends(get_tenant_tier),  # Lookup async ANTES do lambda
):
    """
    Como funciona:
    1. FastAPI executa get_tenant_id() → "tenant-globex"
    2. FastAPI executa get_tenant_tier("tenant-globex") → "pro-tier"
    3. Lambda executa: tier → "pro-tier"
    4. rate_limited usa limiter "pro-tier" do TOML
    5. Aplica rate_per_second=1.66, max_concurrent=20
    """
    return {"tenant": tenant_id, "tier": tier, "data": data}


# ==============================================================================
# ABORDAGEM 3: Construir limiter_id manualmente (mais explícito)
# ==============================================================================


@app.post("/api/v3/data")
async def create_data_v3(
    data: dict,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Abordagem mais explícita - sem lambda.
    """
    # 1. Determinar tier
    tier = await get_tenant_tier_from_db(tenant_id)

    # 2. Construir limiter_id manualmente
    limiter_id = tier  # "free-tier", "pro-tier", ou "enterprise-tier"

    # 3. Aplicar rate limiting
    async with acquire(limiter_id):
        # Rate + Concurrency limitados por tier
        return {"tenant": tenant_id, "tier": tier, "data": data}


# ==============================================================================
# ABORDAGEM 4: Clone limiter por tenant individual (mais granular)
# ==============================================================================


async def ensure_tenant_limiter(tenant_id: str) -> str:
    """
    Cria limiter específico para o tenant baseado no tier dele.

    Vantagens:
    - Metrics por tenant (não só por tier)
    - Pode ajustar limites individualmente
    """
    tier = await get_tenant_tier_from_db(tenant_id)
    limiter_id = f"tenant-{tenant_id}"

    # Clone base tier limiter para este tenant específico
    # (idempotente - se já existe, não faz nada)
    clone_limiter(
        source_id=tier,  # "free-tier", "pro-tier", etc
        new_id=limiter_id,  # "tenant-acme", "tenant-globex", etc
    )

    return limiter_id


@app.post("/api/v4/data")
async def create_data_v4(
    data: dict,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Limiter POR TENANT (não por tier).

    Vantagens:
    - Metrics individuais por tenant (billing!)
    - Pode ajustar limites específicos
    - Melhor visibilidade
    """
    limiter_id = await ensure_tenant_limiter(tenant_id)

    async with acquire(limiter_id):
        return {"tenant": tenant_id, "limiter": limiter_id, "data": data}


# ==============================================================================
# EXEMPLO DE USO: Cliente fazendo requests
# ==============================================================================

"""
# Cliente Free Tier (tenant-acme)
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-acme" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Como funciona:
# 1. Header X-Tenant-ID = "tenant-acme"
# 2. Lookup: TENANT_TIERS["tenant-acme"] = "free-tier"
# 3. rate-sync aplica limites do free-tier:
#    - rate_per_second = 0.16 (~10/min)
#    - max_concurrent = 2
# 4. Se exceder: HTTP 429 Too Many Requests


# Cliente Pro Tier (tenant-globex)
curl -X POST http://localhost:8000/api/v1/data \
  -H "X-Tenant-ID: tenant-globex" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Como funciona:
# 1. Header X-Tenant-ID = "tenant-globex"
# 2. Lookup: TENANT_TIERS["tenant-globex"] = "pro-tier"
# 3. rate-sync aplica limites do pro-tier:
#    - rate_per_second = 1.66 (~100/min)
#    - max_concurrent = 20
# 4. Muito mais throughput que free tier!
"""


# ==============================================================================
# TRACKING METRICS POR TENANT (para billing)
# ==============================================================================


@app.get("/admin/tenant/{tenant_id}/metrics")
async def get_tenant_metrics(tenant_id: str):
    """
    Endpoint admin para ver usage de um tenant.
    Útil para billing, alertas, análises.
    """
    limiter_id = f"tenant-{tenant_id}"

    try:
        limiter = get_limiter(limiter_id)
        metrics = limiter.get_metrics()

        return {
            "tenant_id": tenant_id,
            "metrics": {
                "total_requests": metrics.total_acquisitions,
                "avg_wait_ms": metrics.avg_wait_time_ms,
                "max_wait_ms": metrics.max_wait_time_ms,
                "timeouts": metrics.timeouts,
                "current_concurrent": metrics.current_concurrent,
            },
        }
    except KeyError:
        return {"tenant_id": tenant_id, "error": "No data yet"}


# ==============================================================================
# COMPARAÇÃO: Como seria com `limits` (concorrente)
# ==============================================================================

"""
COM LIMITS (manual, boilerplate):

from limits import parse, MovingWindowRateLimiter
from limits.storage import RedisStorage

storage = RedisStorage("redis://localhost")
limiter = MovingWindowRateLimiter(storage)

TIER_LIMITS = {
    "free-tier": parse("10/minute"),
    "pro-tier": parse("100/minute"),
    "enterprise-tier": parse("1000/minute"),
}

@app.post("/api/data")
async def create_data(
    data: dict,
    x_tenant_id: str = Header(...)
):
    # 🔴 Lookup manual de tier
    tier = TENANT_TIERS.get(x_tenant_id, "free-tier")

    # 🔴 Construir namespace manualmente
    namespace = f"tenant:{x_tenant_id}"

    # 🔴 Parse limit
    limit = TIER_LIMITS[tier]

    # 🔴 Check manual
    if not limiter.hit(limit, namespace):
        raise HTTPException(429, "Rate limit exceeded")

    # 🔴 PROBLEMA: Não tem concurrency limiting!
    # 🔴 Se 100 requests simultâneas chegarem, todas passam
    # 🔴 Backend pode morrer mesmo com rate limit

    # 🔴 Metrics? DIY manual
    # 🔴 Billing integration? Custom code

    return {"data": data}


COM RATE-SYNC (declarativo, elegante):

@app.post("/api/data")
@rate_limited(lambda tenant_id: TENANT_TIERS.get(tenant_id, "free-tier"))
async def create_data(
    data: dict,
    tenant_id: str = Depends(get_tenant_id)
):
    # ✅ Rate limiting automático
    # ✅ Concurrency limiting automático
    # ✅ Metrics automáticos
    # ✅ 3 linhas vs 20+
    return {"data": data}
"""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
