"""Seed default AI providers + models for local development.

Usage: `python -m scripts.seed_ai_config`
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from ragbot.bootstrap import Container
from ragbot.config.logging import setup_logging
from ragbot.infrastructure.db.models import AIModelModel, AIProviderModel


SEED_PROVIDERS = [
    {
        "name": "openai",
        "type": "llm",
        "base_url": "https://api.openai.com/v1",
        "auth_type": "api_key",
    },
    {
        "name": "ZeroEntropy",
        "type": "embedding",
        "base_url": "https://api.zeroentropy.dev/v1",
        "auth_type": "api_key",
    },
]


# Catalog LOCKED 2026-06-14: OpenAI gpt-4.1-mini (primary) + gpt-4.1-nano
# (cheap small tasks) + ZeroEntropy zembed-1 (embedding) / zerank-2 (reranker).
# No haiku / gpt-4.1-full / gpt-5 / local models — see alembic 0216.
SEED_MODELS = [
    {
        "provider_name": "openai",
        "name": "gpt-4.1-mini",
        "kind": "chat",
        "context_window": 1_000_000,
        "max_output_tokens": 32_768,
        "input_price_per_1k_usd": "0.000400",
        "output_price_per_1k_usd": "0.001600",
        "input_price_per_1k_cached_usd": "0.000100",
        "supports_streaming": True,
        "supports_tools": True,
        "supports_json_mode": True,
        "supports_caching": True,
        "languages": ["en", "vi"],
    },
    {
        "provider_name": "openai",
        "name": "gpt-4.1-nano",
        "kind": "chat",
        "context_window": 1_000_000,
        "max_output_tokens": 32_768,
        "input_price_per_1k_usd": "0.000160",
        "output_price_per_1k_usd": "0.000640",
        "input_price_per_1k_cached_usd": "0.000040",
        "supports_streaming": True,
        "supports_tools": True,
        "supports_json_mode": True,
        "supports_caching": True,
        "languages": ["en", "vi"],
    },
    {
        "provider_name": "ZeroEntropy",
        "name": "zembed-1",
        "kind": "embedding",
        "context_window": 8192,
        "max_output_tokens": 0,
        "embedding_dimension": 1280,
        "languages": ["en", "vi", "zh"],
    },
    {
        "provider_name": "ZeroEntropy",
        "name": "zerank-2",
        "kind": "reranker",
        "context_window": 8192,
        "max_output_tokens": 0,
        "languages": ["en", "vi", "zh"],
    },
]


async def main() -> None:
    setup_logging(level="INFO", json=False)
    container = Container()
    factory = container.session_factory()

    async with factory() as session:
        # Providers
        prov_by_name: dict[str, AIProviderModel] = {}
        for p in SEED_PROVIDERS:
            existing = await session.scalar(
                select(AIProviderModel).where(AIProviderModel.name == p["name"]),
            )
            if existing is None:
                row = AIProviderModel(
                    id=uuid4(),
                    name=p["name"],
                    type=p["type"],
                    base_url=p["base_url"],
                    auth_type=p["auth_type"],
                )
                session.add(row)
                await session.flush()
                prov_by_name[p["name"]] = row
                print(f"[+] provider: {p['name']}")
            else:
                prov_by_name[p["name"]] = existing
                print(f"[=] provider exists: {p['name']}")

        # Models
        for m in SEED_MODELS:
            provider = prov_by_name[m["provider_name"]]
            existing = await session.scalar(
                select(AIModelModel).where(
                    AIModelModel.provider_id == provider.id,
                    AIModelModel.name == m["name"],
                ),
            )
            if existing is None:
                row = AIModelModel(
                    id=uuid4(),
                    provider_id=provider.id,
                    name=m["name"],
                    kind=m["kind"],
                    context_window=m["context_window"],
                    max_output_tokens=m["max_output_tokens"],
                    input_price_per_1k_usd=Decimal(str(m.get("input_price_per_1k_usd", 0))),
                    output_price_per_1k_usd=Decimal(str(m.get("output_price_per_1k_usd", 0))),
                    supports_streaming=m.get("supports_streaming", True),
                    supports_tools=m.get("supports_tools", False),
                    supports_json_mode=m.get("supports_json_mode", False),
                    languages=list(m.get("languages", ["en"])),
                )
                session.add(row)
                print(f"[+] model: {m['provider_name']}/{m['name']}")
            else:
                print(f"[=] model exists: {m['provider_name']}/{m['name']}")

        await session.commit()

    print("\nSeed complete. Now bind models to bots via POST /ragbot/admin/bots/{id}/bindings")


if __name__ == "__main__":
    asyncio.run(main())
