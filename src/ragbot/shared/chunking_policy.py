"""Chunking policy resolve chain — config-driven, zero-hardcode.

"Gặp dạng nào → phương pháp nào" is resolved from DB config, NOT hardcoded
if/elif in the chunker. Mirrors the resolve precedence of
:mod:`ragbot.shared.bot_limits`:

    per-bot ``plan_limits.chunking_config``   (owner self-service, FE)
      > platform ``system_config.chunking_policy``  (operator default)
        > ``shared/constants`` DEFAULT_*            (SSoT fallback)

The resolved policy is a small, validated dict the chunker consumes:

    {
      "table_strategy": "table_csv" | "table_dual_index",
      "force_strategy": None | "hdt" | "semantic" | "recursive"
                              | "hybrid" | "proposition" | "table_csv"
                              | "table_dual_index",
    }

Platform default (empty chain) is byte-identical to today's behaviour:
``table_strategy = DEFAULT_TABLE_STRATEGY`` and ``force_strategy = None``.
Invalid values are dropped (logged-free, defensive) rather than raising,
so a malformed owner config can never break ingest.
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    ALLOWED_TABLE_STRATEGIES,
    DEFAULT_TABLE_STRATEGY,
)

# Strategies a bot owner / operator may FORCE for a whole document, bypassing
# auto-detection. Superset of the table strategies + the prose strategies the
# selector can pick. Kept here (not constants) because it is the resolver's
# validation contract, not a tunable default.
_ALLOWED_FORCE_STRATEGIES: frozenset[str] = frozenset(
    {"hdt", "semantic", "recursive", "hybrid", "proposition"}
    | set(ALLOWED_TABLE_STRATEGIES)
)


def _as_dict(value: Any) -> dict:
    """Return ``value`` if it is a dict, else an empty dict (defensive)."""
    return value if isinstance(value, dict) else {}


def resolve_chunking_policy(
    *,
    plan_limits: Any = None,
    platform_policy: Any = None,
) -> dict[str, Any]:
    """Resolve the effective chunking policy from the 3-tier chain.

    @param plan_limits: the bot's ``plan_limits`` JSONB (may carry a
        ``chunking_config`` sub-dict). Non-dict → ignored.
    @param platform_policy: the ``system_config.chunking_policy`` value
        (a dict). Non-dict → ignored.
    @return: validated ``{table_strategy, force_strategy}`` dict.
    """
    per_bot = _as_dict(_as_dict(plan_limits).get("chunking_config"))
    platform = _as_dict(platform_policy)

    # table_strategy: per-bot > platform > constant default; invalid → default.
    table_strategy = (
        per_bot.get("table_strategy")
        or platform.get("table_strategy")
        or DEFAULT_TABLE_STRATEGY
    )
    if table_strategy not in ALLOWED_TABLE_STRATEGIES:
        table_strategy = DEFAULT_TABLE_STRATEGY

    # force_strategy: per-bot > platform > None; invalid → None.
    force_strategy = per_bot.get("force_strategy") or platform.get("force_strategy")
    if force_strategy not in _ALLOWED_FORCE_STRATEGIES:
        force_strategy = None

    return {
        "table_strategy": table_strategy,
        "force_strategy": force_strategy,
    }


__all__ = ["resolve_chunking_policy"]
