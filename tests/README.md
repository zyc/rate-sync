# Testes - rate-limiter

## Política de Testes na Pipeline CI/CD

**IMPORTANTE**: A pipeline CI/CD executa **apenas testes unitários** que não dependem de infraestrutura externa.

### ❌ Não Rodam na Pipeline

Os seguintes tipos de teste **NÃO** rodam na pipeline CI/CD:

- Testes de integração com Redis/cache
- Testes de integração com PostgreSQL
- Testes end-to-end (e2e)
- Testes que dependem de serviços externos
- Testes que requerem Docker/containers

### ✅ Rodam na Pipeline

Apenas testes unitários puros:

- Testes de algoritmos de rate limiting com mocks
- Testes de lógica de janelas/buckets
- Testes de validação de regras
- Testes com stubs de cache

## Como Executar Testes Localmente

### Todos os Testes (incluindo integração)

```bash
poetry run pytest
```

### Apenas Testes Unitários (CI/CD)

```bash
poetry run pytest -m "not integration and not e2e"
```

### Apenas Testes de Integração

```bash
poetry run pytest -m integration
```

### Apenas Testes E2E

```bash
poetry run pytest -m e2e
```

## Marcação de Testes

Use os decoradores pytest para marcar seus testes:

```python
import pytest

# Teste unitário (sem marcação necessária)
def test_calculo_limite():
    assert calculate_rate(window, requests) == expected

# Teste de integração (requer Redis/PostgreSQL)
@pytest.mark.integration
def test_rate_limit_com_cache():
    # ...

# Teste E2E (requer ambiente completo)
@pytest.mark.e2e
def test_fluxo_completo_com_carga():
    # ...
```

## Infraestrutura para Testes

Para rodar testes de integração/e2e localmente:

1. Inicie o ambiente Docker:
   ```bash
   cd ../environment-local
   docker compose up -d
   ```

2. Execute os testes:
   ```bash
   poetry run pytest
   ```

3. Desligue o ambiente:
   ```bash
   cd ../environment-local
   docker compose down
   ```
