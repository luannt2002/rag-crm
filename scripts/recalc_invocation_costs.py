#!/usr/bin/env python3
"""Recompute ``model_invocations.cost_usd`` for legacy rows.

Usage::

    # Dry-run — print rowcount that would update + a sample, no writes
    python scripts/recalc_invocation_costs.py

    # Apply
    python scripts/recalc_invocation_costs.py --apply

The audit found that 99.6 % of rows had ``cost_usd = 0`` because:
1. The streaming branch hardcoded zero usage in the orchestration layer.
2. The structured-output helper discarded ``response.usage`` before the
   caller could log it.

After fixing those two paths going forward, this script recomputes cost
for any rows where the token counts are non-zero but cost is still zero
— typically rows that landed AFTER the fix but predate the cost-only
reprice (e.g. an admin tweaks ``ai_models.input_price_per_1k_usd``).

Pricing comes from ``ai_models``: rows where ``model_invocations.model_id``
is the LiteLLM-style ``"<provider.name>/<model.name>"`` (matching what
``ModelResolver`` writes through ``cfg.litellm_name``).

NOTE: rows where ``prompt_tokens=0 AND completion_tokens=0`` are NOT
recomputable from this script — the original LLM call dropped usage
entirely. Re-run those queries against the fixed pipeline if needed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


_RECALC_SQL = text(
    """
    UPDATE model_invocations mi
    SET cost_usd = (
        (mi.prompt_tokens::numeric / 1000.0) * m.input_price_per_1k_usd
      + (mi.completion_tokens::numeric / 1000.0) * m.output_price_per_1k_usd
    )
    FROM ai_models m
    JOIN ai_providers p ON m.record_provider_id = p.id
    WHERE mi.model_id = (p.name || '/' || m.name)
      AND mi.cost_usd = 0
      AND mi.prompt_tokens > 0
    """,
)

_DRYRUN_SQL = text(
    """
    SELECT count(*) AS recomputable,
           sum(
               (mi.prompt_tokens::numeric / 1000.0) * m.input_price_per_1k_usd
             + (mi.completion_tokens::numeric / 1000.0) * m.output_price_per_1k_usd
           ) AS sum_cost_usd
    FROM model_invocations mi
    JOIN ai_models m ON true
    JOIN ai_providers p ON m.record_provider_id = p.id
    WHERE mi.model_id = (p.name || '/' || m.name)
      AND mi.cost_usd = 0
      AND mi.prompt_tokens > 0
    """,
)

_ORPHAN_SQL = text(
    """
    SELECT count(*) AS unrecoverable
    FROM model_invocations mi
    WHERE mi.cost_usd = 0
      AND mi.prompt_tokens = 0
      AND mi.completion_tokens = 0
    """,
)


async def _run(*, apply: bool) -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required\n")
        return 2

    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            recomputable = (await conn.execute(_DRYRUN_SQL)).first()
            orphans = (await conn.execute(_ORPHAN_SQL)).first()
            print(
                "recomputable: rows={r}, sum_cost_usd={c}".format(
                    r=int(recomputable.recomputable or 0),
                    c=float(recomputable.sum_cost_usd or 0),
                ),
            )
            print(
                "unrecoverable (zero tokens, no usage payload): rows={u}".format(
                    u=int(orphans.unrecoverable or 0),
                ),
            )
            if not apply:
                print("DRY RUN — pass --apply to UPDATE.")
                return 0
            result = await conn.execute(_RECALC_SQL)
            print(f"applied: rows updated={result.rowcount}")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Execute the UPDATE. Without this flag the script prints the "
             "would-update count + cost sum and exits.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
