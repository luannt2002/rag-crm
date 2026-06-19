"""Dev-DB seed: 3 test bots (spa / xe / legal) on a shared tenant + ai_providers/models + bindings.

Idempotent. Run AFTER schema is in place (REBUILD_DEV_DB_RUNBOOK.md) +
`init_system_config.py` + RBAC seeds. Then seed language_packs (migration 0056),
FLUSHDB redis, start server + document worker, then `init_bots_from_urls.py --apply`.

System prompts are NOT duplicated here — they are imported from the canonical
alembic migration modules (alembic = single source of truth for bot-owner content):
  - spa  (test-spa-id)              -> migration 0239 SPA_PROMPT  (latest full rewrite)
  - xe   (chinh-sach-xe)            -> migration 0239 XE_PROMPT   (latest full rewrite)
  - legal(thong-tu-09-2020-tt-nhnn) -> migration 0236 LEGAL_PROMPT(not touched after 0236)

record_bot_id is a deterministic uuid5 of the 4-key so re-runs are stable.
Provider/model names match the LiteLLM wire convention used by seed_dev_drmedispa_bot.py.
"""
from __future__ import annotations

import asyncio
import glob
import importlib.util
import os
import uuid
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.db.models import (
    AIModelModel,
    AIProviderModel,
    BotModel,
    BotModelBindingModel,
    TenantModel,
    WorkspaceModel,
)

# Shared dev tenant (same UUID as seed_dev_drmedispa_bot.py — one tenant, many bots).
TENANT_ID = UUID("c2f66cb2-9911-5d34-a46e-a4a6da068e23")
CHANNEL_TYPE = "web"
# Stable namespace for deterministic record_bot_id (idempotent re-seed).
_NS = UUID("00000000-0000-0000-0000-00000000ba70")


def _load_prompt(rev: str, const: str) -> str:
    """Import a *_PROMPT constant from a canonical alembic migration module."""
    matches = glob.glob(f"alembic/**/*_{rev}_*.py", recursive=True)
    if not matches:
        raise RuntimeError(f"migration {rev} not found under alembic/")
    spec = importlib.util.spec_from_file_location(f"_mig_{rev}", matches[0])
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    val = getattr(mod, const)
    if not isinstance(val, str) or not val.strip():
        raise RuntimeError(f"{const} in migration {rev} is empty/not a str")
    return val


# bot_id -> (workspace_id, language, prompt_rev, prompt_const)
BOTS = [
    ("test-spa-id", "spa", "vi", "0239", "SPA_PROMPT"),
    ("chinh-sach-xe", "xe", "vi", "0239", "XE_PROMPT"),
    ("thong-tu-09-2020-tt-nhnn", "legal", "vi", "0236", "LEGAL_PROMPT"),
]


def _record_bot_id(workspace_id: str, bot_id: str) -> UUID:
    return uuid.uuid5(_NS, f"{TENANT_ID}:{workspace_id}:{bot_id}:{CHANNEL_TYPE}")


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    Sess = async_sessionmaker(eng, expire_on_commit=False)

    async with Sess() as s:
        # ---- tenant ----
        if not (await s.execute(select(TenantModel).where(TenantModel.id == TENANT_ID))).scalar_one_or_none():
            s.add(TenantModel(id=TENANT_ID, name="Dev Test Tenant", config={}))
            await s.commit()
            print(f"+ tenant {TENANT_ID}")
        else:
            print(f"= tenant {TENANT_ID} exists")

        # ---- providers (litellm wire convention) ----
        provs = {p.name: p for p in (await s.execute(select(AIProviderModel))).scalars().all()}
        # code = registry key (reranker/embedder resolver reads provider_code);
        # api_key_ref = env var holding the key (resolver does os.getenv(ref)).
        for pname, ptype, base, auth, key_ref in [
            ("openai", "llm", "https://api.openai.com/v1", "api_key", "OPENAI_API_KEY"),
            ("jina", "embedding", "https://api.jina.ai/v1", "api_key", "JINA_API_KEY"),
        ]:
            if pname not in provs:
                p = AIProviderModel(
                    name=pname, type=ptype, base_url=base, auth_type=auth,
                    enabled=True, code=pname, api_key_ref=key_ref,
                )
                s.add(p)
                print(f"+ provider {pname}")
        await s.commit()
        provs = {p.name: p for p in (await s.execute(select(AIProviderModel))).scalars().all()}

        # ---- ai_models (model.name = wire model_id) ----
        models_seed = [
            {"name": "gpt-4.1-mini", "kind": "llm", "model_id": "gpt-4.1-mini",
             "provider": "openai", "context_window": 128000, "max_output_tokens": 4096,
             "input_price_per_1k_usd": Decimal("0.00040"), "output_price_per_1k_usd": Decimal("0.00160"),
             "supports_caching": True, "supports_streaming": True, "supports_json_mode": True,
             "languages": ["vi", "en"]},
            {"name": "gpt-4.1-nano", "kind": "llm", "model_id": "gpt-4.1-nano",
             "provider": "openai", "context_window": 128000, "max_output_tokens": 4096,
             "input_price_per_1k_usd": Decimal("0.00010"), "output_price_per_1k_usd": Decimal("0.00040"),
             "supports_caching": True, "supports_streaming": True, "supports_json_mode": True,
             "languages": ["vi", "en"]},
            {"name": "jina-embeddings-v3", "kind": "embedding", "model_id": "jina-embeddings-v3",
             "provider": "jina", "context_window": 8192, "max_output_tokens": 0,
             "input_price_per_1k_usd": Decimal("0.00002"), "output_price_per_1k_usd": Decimal("0"),
             "embedding_dimension": 1024, "languages": ["vi", "en"]},
            {"name": "jina-reranker-v3", "kind": "reranker", "model_id": "jina-reranker-v3",
             "provider": "jina", "context_window": 8192, "max_output_tokens": 0,
             "input_price_per_1k_usd": Decimal("0.00002"), "output_price_per_1k_usd": Decimal("0"),
             "languages": ["vi", "en"]},
        ]
        for ms in models_seed:
            ex = (await s.execute(select(AIModelModel).where(
                AIModelModel.record_provider_id == provs[ms["provider"]].id,
                AIModelModel.name == ms["name"],
            ))).scalar_one_or_none()
            if not ex:
                ms_clean = {k: v for k, v in ms.items() if k != "provider"}
                s.add(AIModelModel(record_provider_id=provs[ms["provider"]].id, **ms_clean))
                print(f"+ model {ms['model_id']}")
        await s.commit()
        models = {m.model_id: m for m in (await s.execute(select(AIModelModel))).scalars().all()}

        # nano = light/cheap purposes · mini = quality-sensitive (generation/grade/ground)
        # embedding/rerank routed to Jina (provider selected via system_config too)
        _BINDINGS = [
            ("understand_query", "gpt-4.1-nano"), ("condensing", "gpt-4.1-nano"),
            ("routing", "gpt-4.1-nano"), ("rewriting", "gpt-4.1-nano"),
            ("multi_query", "gpt-4.1-nano"), ("decompose", "gpt-4.1-nano"),
            ("grading", "gpt-4.1-mini"), ("generation", "gpt-4.1-mini"),
            ("grounding", "gpt-4.1-mini"), ("reflection", "gpt-4.1-mini"),
            ("embedding", "jina-embeddings-v3"), ("rerank", "jina-reranker-v3"),
            ("llm_factoid", "gpt-4.1-mini"), ("llm_chitchat", "gpt-4.1-nano"),
            ("llm_oos", "gpt-4.1-nano"), ("llm_primary", "gpt-4.1-mini"),
            ("llm_comparison", "gpt-4.1-mini"), ("llm_multi_hop", "gpt-4.1-mini"),
            ("llm_aggregation", "gpt-4.1-mini"), ("llm_greeting", "gpt-4.1-nano"),
            ("llm_feedback", "gpt-4.1-nano"), ("llm_out_of_scope", "gpt-4.1-nano"),
            ("llm_vu_vo", "gpt-4.1-nano"), ("chat", "gpt-4.1-mini"),
            ("intent", "gpt-4.1-nano"), ("condense", "gpt-4.1-nano"),
            ("rewrite", "gpt-4.1-nano"), ("grade", "gpt-4.1-mini"),
            ("reflect", "gpt-4.1-mini"), ("guard", "gpt-4.1-nano"),
            ("enrichment", "gpt-4.1-nano"),
        ]

        for bot_id, workspace_id, lang, rev, const in BOTS:
            rbid = _record_bot_id(workspace_id, bot_id)
            prompt = _load_prompt(rev, const)

            # workspace entity (additive; RBAC/lifecycle) — uq(tenant, slug)
            if not (await s.execute(select(WorkspaceModel).where(
                WorkspaceModel.record_tenant_id == TENANT_ID,
                WorkspaceModel.slug == workspace_id,
            ))).scalar_one_or_none():
                s.add(WorkspaceModel(record_tenant_id=TENANT_ID, slug=workspace_id, name=workspace_id))
                print(f"+ workspace {workspace_id}")

            if not (await s.execute(select(BotModel).where(BotModel.id == rbid))).scalar_one_or_none():
                s.add(BotModel(
                    id=rbid,
                    record_tenant_id=TENANT_ID,
                    workspace_id=workspace_id,
                    bot_id=bot_id,
                    channel_type=CHANNEL_TYPE,
                    bot_name=bot_id,
                    record_model_id=models["gpt-4.1-mini"].id,
                    record_embedding_model_id=models["jina-embeddings-v3"].id,
                    system_prompt=prompt,
                    language=lang,
                ))
                print(f"+ bot {bot_id} (ws={workspace_id}, prompt={const}@{rev}, len={len(prompt)})")
            else:
                print(f"= bot {bot_id} exists")
            await s.commit()

            for purpose, model_id in _BINDINGS:
                ex = (await s.execute(select(BotModelBindingModel).where(
                    BotModelBindingModel.record_bot_id == rbid,
                    BotModelBindingModel.purpose == purpose,
                ))).scalar_one_or_none()
                if not ex:
                    s.add(BotModelBindingModel(
                        record_tenant_id=TENANT_ID,
                        workspace_id=workspace_id,
                        record_bot_id=rbid,
                        purpose=purpose,
                        record_model_id=models[model_id].id,
                        rank=0,
                        weight=100,
                        active=True,
                    ))
            await s.commit()
            print(f"  bindings ready for {bot_id}")

    await eng.dispose()
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
