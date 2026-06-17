"""EVAL-ONLY helper: point the per-bot ANSWER bindings at a given model.

Used by run_model_matrix.sh to A/B nano/mini/full. NOT a production migration —
the driver always restores gpt-4.1 (the committed production state, alembic 0202)
at the end + on error. Only answer-generating roles are moved; sub-roles
(decompose/multi_query/enrichment/grade/rerank/embedding) stay put.

Usage: PYTHONPATH=. python scripts/set_answer_model.py gpt-4.1-nano
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_ANSWER_PURPOSES = (
    "llm_primary", "generation", "llm_factoid", "llm_aggregation",
    "llm_comparison", "llm_multi_hop",
)


async def main() -> None:
    model = sys.argv[1]
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as c:
        mid = (await c.execute(
            text("SELECT id FROM ai_models WHERE name=:n AND enabled=true"),
            {"n": model})).scalar()
        if mid is None:
            raise SystemExit(f"model not found/enabled: {model}")
        res = await c.execute(
            text("""UPDATE bot_model_bindings SET record_model_id=:mid
                    WHERE purpose = ANY(:purposes) AND active=true"""),
            {"mid": mid, "purposes": list(_ANSWER_PURPOSES)})
        print(f"set answer bindings → {model}: {res.rowcount} rows")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
