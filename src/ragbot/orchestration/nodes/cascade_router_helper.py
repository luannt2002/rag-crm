"""Cascade Routing helper — orchestration-side glue (DOMAIN-NEUTRAL).

Reads the bot's ``cascade_routing_enabled`` opt-in, consults the
``ModelResolverService.resolve_cascade_runtime`` policy on the query's
complexity score, and surfaces the chosen tier model name back to the
caller. The helper never touches LLM call args directly — it only
returns the tier model name (or the unchanged fallback). The
orchestrator that imports this helper is responsible for piping the
returned model name into the existing answer-LLM call site.

Design goals:
- **Strategy + DI**: zero per-brand branches in the orchestrator. Tier
  selection lives in ``ModelResolverService.resolve_cascade_runtime``;
  this helper is a thin call site that wires bot config + score →
  model name.
- **Default OFF**: when the bot has not opted in (or when the bot config
  is missing entirely) the helper returns the supplied ``current_model``
  fallback verbatim. No silent behaviour change.
- **NullObject contract**: when cascade resolves to ``""`` (no tier
  model configured at platform or bot level) the helper degrades to the
  ``current_model`` fallback rather than raising. Aux config gap MUST
  NOT break the primary answer path.
- **Domain-neutral**: bot-name literals are forbidden anywhere in the
  module. Per-bot tuning happens via ``plan_limits`` + ``threshold_overrides``.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from ragbot.shared.bot_limits import resolve_bot_limit

logger = structlog.get_logger(__name__)


class _CascadeResolver(Protocol):
    """Structural typing — anything exposing ``resolve_cascade_runtime``."""

    def resolve_cascade_runtime(
        self,
        complexity_score: float,
        bot_config: dict[str, Any] | None = None,
        *,
        config_getter: Any | None = None,
    ) -> str: ...


def _extract_bot_config(state: dict[str, Any]) -> dict[str, Any]:
    """Lift the ``plan_limits`` / ``threshold_overrides`` view from ``state``.

    The graph state shape varies across nodes. We accept any of:
    - ``state["bot_config"]`` dict (preferred — already merged config).
    - ``state["bot"]`` object with ``plan_limits`` + ``threshold_overrides``
      attributes (raw BotConfig DTO).
    - empty dict otherwise.

    The returned mapping is shallow-merged so callers can ``.get(...)``
    a single namespace without knowing the upstream shape.
    """
    bot_cfg = state.get("bot_config")
    if isinstance(bot_cfg, dict):
        return bot_cfg

    bot = state.get("bot")
    if bot is not None:
        merged: dict[str, Any] = {}
        plan_limits = getattr(bot, "plan_limits", None) or {}
        if isinstance(plan_limits, dict):
            merged.update(plan_limits)
        overrides = getattr(bot, "threshold_overrides", None) or {}
        if isinstance(overrides, dict):
            merged.update(overrides)
        return merged

    return {}


def _coerce_score(raw: Any) -> float:
    """Best-effort float coerce; fall back to 0.0 (cheap tier)."""
    if isinstance(raw, (int, float)):
        score = float(raw)
    elif isinstance(raw, str):
        try:
            score = float(raw)
        except ValueError:
            return 0.0
    else:
        return 0.0
    if score != score:  # NaN guard
        return 0.0
    return score


def apply_cascade_routing(
    state: dict[str, Any],
    model_resolver: _CascadeResolver,
    *,
    current_model: str,
) -> str:
    """Return the tier model name (or ``current_model`` when OFF).

    Reads the bot's ``cascade_routing_enabled`` flag via the same
    ``resolve_bot_limit`` chain the rest of the platform uses
    (``bots`` column > ``plan_limits`` > ``system_config`` > schema
    default). When the flag is OFF (default) the helper short-circuits
    and returns ``current_model`` unchanged — zero behaviour change.

    When the flag is ON the helper pulls ``complexity_score`` from the
    graph state (written by ``orchestration.nodes.query_complexity``)
    and asks the resolver for the matching tier model name. Empty
    return from the resolver (NullObject — no tier configured) falls
    back to ``current_model`` so the answer path stays alive.

    Args:
        state: LangGraph ``GraphState`` dict. Must carry
            ``complexity_score`` (float) when cascade routing is active;
            missing key → 0.0 → cheap tier. ``bot``/``bot_config`` is
            consulted for the opt-in flag and per-bot model overrides.
        model_resolver: Any object satisfying the ``_CascadeResolver``
            protocol — production wiring passes
            ``ModelResolverService`` (DI bootstrap singleton).
        current_model: The model name the orchestrator would use if
            cascade were OFF. Used as the fallback at every short-circuit
            so callers always receive a non-empty string.

    Returns:
        Model-name string suitable for downstream ``ai_models.name``
        lookup. Never ``""``. Never raises — config gaps degrade silent
        to ``current_model`` (graceful degradation contract).
    """
    # Flag-check resolves from EITHER source the graph might carry:
    # 1. ``state["pipeline_config"]["cascade_routing_enabled"]`` — the
    #    flattened per-bot config that chat_worker/test_chat build.
    # 2. ``state["bot"].plan_limits.cascade_routing_enabled`` — the raw
    #    BotConfig DTO (used by some unit tests that don't flatten).
    # Default OFF (return current_model) when neither carries a True.
    # Wave D root cause: helper previously REQUIRED state["bot"], but
    # the production query graph never stores it (plan_limits are
    # flattened to pipeline_config upstream), so cascade was unreachable.
    bot = state.get("bot")
    pipeline_config = state.get("pipeline_config") or {}
    enabled = False
    if isinstance(pipeline_config, dict) and pipeline_config.get(
        "cascade_routing_enabled",
    ) is True:
        enabled = True
    elif bot is not None:
        try:
            enabled = bool(resolve_bot_limit(bot, "cascade_routing_enabled"))
        except Exception:  # noqa: BLE001 — resolver bug must not kill answer path
            logger.warning(
                "cascade_routing_resolve_flag_failed",
                bot_id=getattr(bot, "bot_id", None),
                exc_info=True,
            )
            return current_model

    if not enabled:
        logger.debug(
            "cascade_routing_skipped_disabled",
            bot_id=getattr(bot, "bot_id", None) if bot is not None else None,
        )
        return current_model

    score = _coerce_score(state.get("complexity_score"))
    bot_config = _extract_bot_config(state)

    try:
        tier_model = model_resolver.resolve_cascade_runtime(
            score, bot_config,
        )
    except Exception:  # noqa: BLE001 — resolver outage must not kill answer
        logger.warning(
            "cascade_routing_resolve_runtime_failed",
            bot_id=getattr(bot, "bot_id", None),
            complexity_score=score,
            exc_info=True,
        )
        return current_model

    if not isinstance(tier_model, str) or not tier_model.strip():
        # NullObject contract — no tier configured → keep current model.
        logger.debug(
            "cascade_routing_skipped_null_tier",
            bot_id=getattr(bot, "bot_id", None),
            complexity_score=score,
        )
        return current_model

    chosen = tier_model.strip()
    # Emit at INFO so the wire is observable in production journals when
    # cascade actually flips a model. Per-bot diagnose flow: grep
    # journalctl for ``cascade_routing_applied`` event.
    logger.info(
        "cascade_routing_applied",
        bot_id=getattr(bot, "bot_id", None),
        complexity_score=score,
        chosen_model=chosen,
        previous_model=current_model,
    )
    return chosen


__all__ = ["apply_cascade_routing"]
