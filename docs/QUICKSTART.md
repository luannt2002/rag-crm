# Quick Start

> Local setup → first chat in ~5 minutes. Production deployment follows
> the UAT compose overlay; see [`../docker-compose.uat.yml`](../docker-compose.uat.yml)
> + [`../.env.uat.example`](../.env.uat.example).

---

## 1. Prerequisites

- Python 3.12+
- PostgreSQL 16 with the `pgvector` and `pg_trgm` extensions installed
  cluster-side (see Setup §2a for the `CREATE EXTENSION` snippet — the
  alembic migrations now auto-provision `pg_trgm` but on a brand-new
  cluster `pgvector` must be pre-installed by superuser).
- Redis 7 (Stack image recommended for streams support)
- An OpenAI API key (chat + upload LLM run on `gpt-4.1-mini`)
- A ZeroEntropy API key (embedding `zembed-1` 1280-dim + reranker `zerank-2`)
- An Anthropic API key (chunk enrichment + per-bot Pro tier; ingest path)

---

## 2. Setup

```bash
git clone <repo>
cd ragbot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Required: DATABASE_URL, REDIS_URL, OPENAI_API_KEY, PROVIDER_API_KEYS_JSON
# Optional: ANTHROPIC_API_KEY (only if you swap the upload model to Haiku/Sonnet)
```

### 2a. Database extensions (one-time, superuser)

Both extensions MUST exist on the target database before `alembic upgrade head`
applies the Wave A migrations. `pg_trgm` is auto-provisioned by migration 010l
(`CREATE EXTENSION IF NOT EXISTS pg_trgm`), but `pgvector` is not — install it
manually with a superuser role:

```sql
-- Connect as superuser to the target DB
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector (HNSW + ivfflat)
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- trigram GIN for chunk_context

-- Verify
SELECT extname FROM pg_extension ORDER BY extname;
-- Expect: plpgsql, pg_trgm, vector
```

Run migrations:

```bash
alembic upgrade head
```

Start the API:

```bash
# Dev (hot reload)
uvicorn ragbot.main:app --port 3004 --workers 4

# Or via systemd
systemctl start ragbot-api
```

---

## 3. Smoke verify

```bash
# Liveness
curl http://localhost:3004/health

# Mint a dev test token (level=owner; only when RAGBOT_DEV_TOKEN_ENABLED=true)
TOKEN=$(curl -s http://localhost:3004/api/ragbot/test/tokens/self | jq -r .token)

# Send a chat
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "User-Agent: Mozilla/5.0" \
  -H "Content-Type: application/json" \
  -d '{
        "bot_id": "<your-bot-id>",
        "channel_type": "web",
        "connect_id": "smoke",
        "question": "<a question your corpus can answer>"
      }' \
  http://localhost:3004/api/ragbot/test/chat
```

Expected: a grounded answer (or the refusal template if the corpus has
no match). Per-step timings land in `request_steps`; per-turn
aggregates in `request_logs`.

---

## 4. Run the test suite

```bash
.venv/bin/pytest tests/unit/ -q
```

The unit suite is the green-light signal. Integration tests
(Postgres + Redis required) are gated behind `--run-integration` /
`RAGBOT_RUN_INTEGRATION=1`.

A list of unit tests with golden-string drift is tracked in
[`../tests/_xfail_list.txt`](../tests/_xfail_list.txt) and reported as
`xfailed`; they do not block CI. The per-cluster refactor that
un-xfails them is planned in
[`../plans/260507-V17-test-refactor/plan.md`](../plans/260507-V17-test-refactor/plan.md).

---

## 5. Next reads

- **What does this system do** → [`../README.md`](../README.md)
- **24-step pipeline detail** → [`../RAGBOT_STEP_PIPELINE.md`](../RAGBOT_STEP_PIPELINE.md)
- **Architecture deep dive** → [`master/01-A-foundation-architecture.md`](master/01-A-foundation-architecture.md) onwards
- **API contract** → [`API.md`](API.md) and [`API_REFERENCE.md`](API_REFERENCE.md)
- **Sacred rules for code contributions** → [`../CLAUDE.md`](../CLAUDE.md)
