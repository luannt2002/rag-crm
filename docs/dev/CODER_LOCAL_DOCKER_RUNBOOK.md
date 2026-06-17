# Coder Local Docker Runbook

> **For**: CODER team — setup local DB + Redis để integration test + load test app TRƯỚC commit
> **Replaces**: mock-only constraint trong CODER_FULL_SMARTNESS_PLAN.md §❷ (CODER giờ có DB local)
> **DB seed**: `tests/fixtures/db_seed/test_db_dump.sql` (2.8MB, dev DB snapshot, NO credential leak verified)

---

## ❶ Prerequisites

- Docker + docker-compose (Compose V2)
- Python 3.12+ với `.venv` đã `pip install -e .[dev]`
- Free port 5432 (Postgres) + 6379 (Redis)

## ❷ One-shot setup

```bash
# 1. Clone repo + enter
git clone git@github.com:luannt2002/ragbot-py.git
cd ragbot-py

# 2. Spin up local DB + Redis (auto-load dump on first start)
docker-compose -f docker/coder/docker-compose.yml up -d

# 3. Wait health check
docker ps | grep ragbot-coder
# Expect both ragbot-coder-postgres + ragbot-coder-redis status "healthy"

# 4. Verify DB seed loaded
docker exec ragbot-coder-postgres psql -U postgres -d ragbot_v2_dev -c "SELECT count(*) FROM bots; SELECT count(*) FROM document_chunks;"
# Expect: bots=3, document_chunks=240

# 5. Copy env template
cp docker/coder/.env.coder.example .env.coder
# Edit .env.coder — fill OPENAI_API_KEY + RERANKER_JINA_API_KEY (BYO)

# 6. Run app (optional — coder verify integration before commit)
set -a && source .env.coder && set +a
.venv/bin/python -m ragbot.main
# → API at http://localhost:3004
```

## ❸ Run integration tests

Integration test marked với `pytest.mark.integration` → cần `--run-integration` flag.

```bash
set -a && source .env.coder && set +a
.venv/bin/python -m pytest tests/integration/ --run-integration -q
```

Or specific:

```bash
.venv/bin/python -m pytest tests/integration/test_rls_cross_tenant.py --run-integration -v
.venv/bin/python -m pytest tests/integration/test_openai_fallback_to_anthropic.py --run-integration -v
```

## ❹ Run load test (when TASK-3 anti-abuse bypass shipped)

```bash
# Set bypass token
export RAGBOT_LOADTEST_BYPASS_TOKEN=$(openssl rand -hex 16)

# Run script (consumes prepared question fixture)
.venv/bin/python scripts/loadtest_3persona_consume.py \
  --questions reports/loadtest_3persona_questions.json \
  --output reports/LOADTEST_LOCAL_$(date +%Y%m%d_%H%M%S).json \
  --pace 4.0 \
  --label "coder-local"
```

Output JSON file → admin pulls + analyzes.

## ❺ Reset DB (re-load fresh dump)

```bash
docker-compose -f docker/coder/docker-compose.yml down -v   # -v deletes volume
docker-compose -f docker/coder/docker-compose.yml up -d
# DB seed reloads from dump on first start
```

## ❻ Notes

- **Credentials**: `.env.coder` chứa local-only password (`coder_dev_local`). Production credentials NEVER committed (CLAUDE.md sacred rule).
- **DB seed brand**: dump chứa Medispa test bot (`bot_id=1774946011723:web`, sysprompt v8 alembic 0072). Brand exposure acceptable per user explicit override 2026-05-09 — test DB only, public sites already published.
- **Schema version**: dump = alembic revision `0072` (RLS + Anthropic fallback + sysprompt v8 9-intent + multi-agent framework T0 fix).
- **No alembic apply needed** trên this seed — full schema + data already in dump.
- **RLS warning**: dump enables RLS policies but `postgres` superuser BYPASSES. Coder runtime uses `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` escape until TASK-1 (T1.S1b non-superuser DSN) ships.

## ❼ Sacred contracts (CLAUDE.md) trong coder local env

- ❌ KHÔNG commit `.env.coder` (gitignored as `.env*`)
- ❌ KHÔNG hardcode brand từ DB seed vào src/ragbot/ code
- ❌ KHÔNG copy DSN literal vào tracked file (use env var)
- ✅ DB seed `tests/fixtures/db_seed/test_db_dump.sql` = test fixture, OK tracked
- ✅ Mock test vẫn ưu tiên cho unit suite — integration only when needed
