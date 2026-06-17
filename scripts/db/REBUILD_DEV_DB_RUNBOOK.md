# Dev DB rebuild runbook (post-V11)

When alembic replay from scratch is broken (migration `0001_initial_schema.py`
calls `Base.metadata.create_all()` with current models, downstream migrations
expect V0 schema and fail on the first ALTER), this is the working rebuild
path. Tested 2026-05-06 against `ragbot_v2_dev` after wipe.

## Prereqs

- Postgres 16 reachable, env `DATABASE_URL` (asyncpg) + `DATABASE_URL_SYNC` (psycopg) set in `.env`.
- `pgcrypto` and `vector` extensions installable (root or postgres role).
- Redis reachable, env `REDIS_URL` set.
- `OPENAI_API_KEY` + `JINA_API_KEY` set.
- `DEFAULT TENANT/BOT/WORKSPACE` constants in `seed_dev_drmedispa_bot.py` match the bot you intend to load-test.

## Steps

```bash
# 1. Wipe schema (DESTRUCTIVE — dev only, confirm first)
python -c "
import os, asyncio, asyncpg
async def main():
    url = os.environ['DATABASE_URL_SYNC'].replace('postgresql+psycopg2://','postgresql://')
    conn = await asyncpg.connect(url)
    await conn.execute('DROP SCHEMA IF EXISTS public CASCADE')
    await conn.execute('CREATE SCHEMA public')
    await conn.execute('GRANT ALL ON SCHEMA public TO postgres, public')
    await conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
    await conn.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
    await conn.close()
asyncio.run(main())
"

# 2. Create the 20 ORM-managed tables from current models
python -c "
import os
from sqlalchemy import create_engine
from ragbot.infrastructure.db.models import Base
Base.metadata.create_all(create_engine(os.environ['DATABASE_URL_SYNC']))
"

# 3. Add the 10 DDL-only tables (post-V11 final form)
psql "$DATABASE_URL_SYNC" -f scripts/db/bootstrap_ddl_only_tables.sql

# 4. Mark alembic at head (we bypassed the migration replay)
alembic stamp head

# 5. Seed system_config (~159 keys)
python scripts/init_system_config.py

# 6. Seed RBAC permissions (s11b first — covers sync:documents_upsert + chat etc.)
python scripts/seed_rbac_permissions_s11b.py
python scripts/seed_rbac_permissions_s12a.py

# 7. Seed Dr. Medispa tenant + bot + ai_models + bindings
python scripts/db/seed_dev_drmedispa_bot.py

# 8. Seed language_packs (vi + en) from migration 0056
python -c "
import asyncio, os, importlib.util
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
spec = importlib.util.spec_from_file_location('lp_seed', 'alembic/versions/20260501_0056_language_packs_seed_vi_en.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
async def main():
    eng = create_async_engine(os.environ['DATABASE_URL'])
    async with eng.begin() as conn:
        for code, key, content in mod._SEED_ROWS:
            await conn.execute(
                text('INSERT INTO language_packs (code, prompt_key, content) VALUES (:c,:k,:v) ON CONFLICT (code, prompt_key) DO NOTHING'),
                {'c': code, 'k': key, 'v': content},
            )
    await eng.dispose()
asyncio.run(main())
"

# 9. Bust Redis caches (RBAC + tenant/bot config)
redis-cli -n 1 FLUSHDB

# 10. Restart workers to flush in-memory perm cache + DI singletons
systemctl restart ragbot-api ragbot-outbox

# 11. Wait for healthcheck
until curl -s -m 3 -o /dev/null http://localhost:3004/healthz; do sleep 2; done

# 12. Ingest corpus (Dr. Medispa 7 sheets) + run 90Q
python -u scripts/loadtest_20260506_runner.py all
```

## Verification at each step

| Step | Expected |
|---|---|
| 2 | 20 tables in `public` (ORM models). `\dt` shows ai_*, bots, conversations, documents, ... |
| 3 | +10 tables → 30 total. Includes `document_chunks`, `semantic_cache`, `language_packs`, `system_config`, `module_permissions`, `role_definitions`, `api_tokens`, `chat_histories`, `knowledge_edges`. |
| 4 | `alembic_version` = `0063` |
| 5 | `system_config` ≥ 159 rows. Key spot-check: `SELECT * FROM system_config WHERE key='llm_default_model'` |
| 6 | `module_permissions` ≥ 45 rows. Spot-check: `SELECT * FROM module_permissions WHERE module='sync'` returns 4 rows. |
| 7 | `bots` 1 row, `bot_model_bindings` ≥ 33 rows (every purpose query_graph + model_resolver looks up). |
| 8 | `language_packs` = 16 rows (8 vi + 8 en). |
| 12 | Ingest: `total_documents=7 total_chunks≈288`. Verify: `chunks_w_embed ≈ 200+` (parents don't need embedding). Test: HTTP 200 per turn, ~15s per turn. |

## Why alembic replay fails

`alembic/versions/20260415_0001_initial_schema.py:24-26`:

```python
def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
```

This uses the CURRENT (V13 final) `Base.metadata`, which has columns
`record_tenant_id`, `workspace_id` (added in V8/V10) baked in. Downstream
migration `0004_external_message_id.py` then fires
`CREATE INDEX ix_reqlog_tenant_message ON request_logs (tenant_id, ...)` —
but the column is already named `record_tenant_id` post-0034, so the index
DDL fails with `column "tenant_id" does not exist`.

Fix would be to rewrite 0001 to use the V0 schema (pinning specific
`Column(...)` definitions instead of model autoload). Out of scope for now;
this runbook is the working escape hatch.

## Cleanup

After successful rebuild, the temp files this runbook produced can be
deleted from `/tmp`. The committed scripts in `scripts/db/` are the
permanent reproduction path.
