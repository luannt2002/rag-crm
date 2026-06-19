"""ONE-stop dev/test seed — run after `alembic upgrade head` (squash baseline).

Orchestrates every seed step needed to make the 3 test bots queryable on a
fresh database, in dependency order:

  1. system_config defaults            (init_system_config.py)
  2. RBAC permissions                  (seed_rbac_permissions_s11b/s12a.py)
  3. language_packs vi/en              (migration 0056 _SEED_ROWS)
  4. tenant + providers + models + 3 bots + bindings + workspaces
                                        (seed_3test_bots.py)
  5. embedder/reranker provider = jina (system_config)
  6. per-tenant quota row + rate-limit bypass

Idempotent. Requires DATABASE_URL (asyncpg). Run:
    set -a && source .env && set +a && python scripts/db/seed_dev.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_ROOT = Path(__file__).resolve().parent.parent.parent
_PY = sys.executable
TENANT_ID = "c2f66cb2-9911-5d34-a46e-a4a6da068e23"


def _run(script: str) -> None:
    print(f"\n>>> {script}")
    subprocess.run([_PY, str(_ROOT / script)], check=True, cwd=_ROOT)


def _seed_language_packs() -> None:
    """Seed FINAL accumulated prompt content (few-shot/CLASSIFY-FIRST/money-norm...).

    The squashed baseline is schema-only, so the prompt content accumulated by
    the archived data-migrations (010w/010z/0114/0134/...) is restored here from
    a tracked data dump. Falls back to the migration-0056 base rows if absent.
    """
    seed_sql = _ROOT / "alembic/squashed_seed_language_packs.sql"
    if seed_sql.exists():
        print("\n>>> language_packs (FINAL accumulated content)")

        async def _go_sql() -> None:
            eng = create_async_engine(os.environ["DATABASE_URL"])
            stmts = [s.strip() for s in seed_sql.read_text(encoding="utf-8").split(";\n") if s.strip()]
            async with eng.begin() as conn:
                for ins in stmts:
                    # INSERT ... ; rewrite to upsert so re-seed is idempotent
                    await conn.execute(text(
                        ins.replace(
                            "INSERT INTO public.language_packs",
                            "INSERT INTO public.language_packs",
                        ) + " ON CONFLICT (code, prompt_key) DO UPDATE SET "
                        "content = EXCLUDED.content, version = EXCLUDED.version"
                    ))
            await eng.dispose()

        asyncio.run(_go_sql())
        print("   language_packs seeded (final content)")
        return

    print("\n>>> language_packs (migration 0056 base — fallback)")
    hits = list((_ROOT / "alembic").rglob("*0056_language_packs_seed_vi_en.py"))
    if not hits:
        print("   [skip] 0056 seed module not found")
        return
    spec = importlib.util.spec_from_file_location("_lp", hits[0])
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    async def _go() -> None:
        eng = create_async_engine(os.environ["DATABASE_URL"])
        async with eng.begin() as conn:
            for code, key, content in mod._SEED_ROWS:
                await conn.execute(
                    text(
                        "INSERT INTO language_packs (code, prompt_key, content) "
                        "VALUES (:c,:k,:v) ON CONFLICT (code, prompt_key) DO NOTHING"
                    ),
                    {"c": code, "k": key, "v": content},
                )
        await eng.dispose()

    asyncio.run(_go())
    print("   language_packs seeded (base)")


def _seed_runtime_config_and_quota() -> None:
    print("\n>>> provider=jina + quota + rate-limit bypass")

    async def _go() -> None:
        eng = create_async_engine(os.environ["DATABASE_URL"])
        async with eng.begin() as conn:
            await conn.execute(text(
                "INSERT INTO system_config (key, value, value_type, description) "
                "VALUES ('embedding_provider','\"jina\"','string','Embedder strategy') "
                "ON CONFLICT (key) DO UPDATE SET value='\"jina\"'"
            ))
            await conn.execute(text(
                "UPDATE system_config SET value='\"jina\"' WHERE key='reranker_provider'"
            ))
            await conn.execute(text(
                "INSERT INTO quotas (record_tenant_id, workspace_id, monthly_limit, "
                "used_tokens, used_cost_usd, blocked, documents_per_day_limit, documents_today_count) "
                "VALUES (:t,'spa',1000000000,0,0,false,100000,0) ON CONFLICT DO NOTHING"
            ), {"t": TENANT_ID})
            await conn.execute(text(
                "UPDATE tenants SET bypass_rate_limit=true WHERE id=:t"
            ), {"t": TENANT_ID})
        await eng.dispose()

    asyncio.run(_go())
    print("   runtime config + quota seeded")


def main() -> None:
    _run("scripts/init_system_config.py")
    _run("scripts/seed_rbac_permissions_s11b.py")
    _run("scripts/seed_rbac_permissions_s12a.py")
    _seed_language_packs()
    _run("scripts/db/seed_3test_bots.py")
    _seed_runtime_config_and_quota()
    print("\nDONE — dev seed complete. FLUSHALL redis + restart API + ingest.")


if __name__ == "__main__":
    main()
