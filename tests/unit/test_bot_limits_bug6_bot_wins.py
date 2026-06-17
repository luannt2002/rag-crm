"""Pin tests — 260525 Bug #6 resolver chain fix.

Prior behaviour (commit a3e0... and earlier): ``resolve_bot_limit`` applied
``max(bot_val, fallback)`` to every numeric key. Defence vs ``rerank_top_n=1``
typo, but unintentionally made it IMPOSSIBLE for a bot owner to override
a numeric default DOWNWARD even with a sensible in-range value (e.g.
``crag_skip_retry_above_score=0.5`` vs system_default ``0.55`` → resolver
returned ``0.55``, bot override ignored).

260525 fix: bot value WINS outright when set. Schema-driven ``min`` /
``max`` range guard prevents typo elevation by REJECTING the bot value
(log + fall through to system_default) when it lies outside the
documented bounds — defence in the right place, at validation time
rather than in the resolver.

These pin tests cover the specific regression vector documented in
``plans/260525-4BUG-INGEST-PIPELINE/plan.md`` §4.
"""

from __future__ import annotations

from types import SimpleNamespace

from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA, resolve_bot_limit


def _bot(**kwargs) -> SimpleNamespace:
    """Build a minimal bot_cfg DTO surrogate."""
    ns = SimpleNamespace(plan_limits=None, threshold_overrides=None)
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# -- Numeric: bot wins DOWNWARD when in-range -------------------------------


def test_bot_value_below_system_in_range_bot_wins() -> None:
    """Bot override of ``retrieval_top_k`` to 10 (between min=5 and max=200)
    must beat system_default=20. Pre-fix: ``max(10, 20) = 20`` (bot lost)."""
    bot = _bot(plan_limits={"retrieval_top_k": 10})
    result = resolve_bot_limit(bot, "retrieval_top_k", system_default=20)
    assert result == 10, (
        f"Bot override should win in-range: got {result}, expected 10. "
        "Bug #6 regression — resolver fell back to max() behaviour."
    )


def test_bot_value_below_schema_min_rejected() -> None:
    """Bot value below schema ``min``: rejected → system_default wins.

    Defence-in-place: schema declares ``retrieval_top_k`` min=5. A typo
    bot value of 1 trips the range guard.
    """
    bot = _bot(plan_limits={"retrieval_top_k": 1})
    result = resolve_bot_limit(bot, "retrieval_top_k", system_default=20)
    assert result == 20, (
        f"Out-of-range bot value should fall back: got {result}, expected 20."
    )


def test_bot_value_above_schema_max_rejected() -> None:
    """Bot value above schema ``max``: rejected → system_default wins."""
    bot = _bot(plan_limits={"retrieval_top_k": 999})  # schema max=200
    result = resolve_bot_limit(bot, "retrieval_top_k", system_default=20)
    assert result == 20


def test_bot_value_no_schema_no_range_guard() -> None:
    """Keys without PLAN_LIMIT_SCHEMA entry don't get range-guarded;
    bot value wins unconditionally (no schema to consult)."""
    bot = _bot(plan_limits={"made_up_key_no_schema": 42})
    result = resolve_bot_limit(bot, "made_up_key_no_schema", system_default=100)
    assert result == 42


# -- Float threshold case (the literal Bug #6 reproducer) -------------------


def test_crag_skip_threshold_bot_overrides_downward() -> None:
    """Documented Bug #6 scenario from session 2026-05-25.

    Bot ``test-spa-id`` has ``plan_limits.crag_skip_retry_above_score = 0.5``.
    System default = 0.55 (from ``DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE``).
    Pre-fix: resolver returned 0.55 (max). Post-fix: returns 0.5 (bot wins).

    There is no ``crag_skip_retry_above_score`` schema entry so the
    range guard does not apply — the unrestricted bot-wins path runs.
    """
    bot = _bot(plan_limits={"crag_skip_retry_above_score": 0.5})
    result = resolve_bot_limit(
        bot, "crag_skip_retry_above_score", system_default=0.55,
    )
    assert result == 0.5


# -- Threshold_overrides retains highest priority ---------------------------


def test_threshold_overrides_wins_over_plan_limits() -> None:
    """Stream V Phase 2 contract — threshold_overrides is highest priority
    after dedicated columns. Behaviour unchanged by Bug #6 fix."""
    bot = _bot(
        threshold_overrides={"retrieval_top_k": 30},
        plan_limits={"retrieval_top_k": 50},
    )
    result = resolve_bot_limit(bot, "retrieval_top_k", system_default=20)
    assert result == 30


# -- Bool override no longer silently flipped -------------------------------


def test_bool_bot_false_wins_over_system_true() -> None:
    """A bot opting OUT of a default-ON feature must be honoured.

    Pre-fix: ``max(False, True) = True`` (bool is int subclass). Post-fix:
    bot wins outright. ``reflection_enabled`` schema doesn't declare
    min/max so the range guard skips bool values.
    """
    bot = _bot(plan_limits={"reflection_enabled": False})
    result = resolve_bot_limit(bot, "reflection_enabled", system_default=True)
    assert result is False


# -- Fallback path still works when no bot value ----------------------------


def test_no_bot_value_falls_back_to_system() -> None:
    bot = _bot(plan_limits=None)
    assert resolve_bot_limit(bot, "retrieval_top_k", system_default=42) == 42


def test_no_bot_no_system_falls_back_to_schema() -> None:
    bot = _bot(plan_limits=None)
    result = resolve_bot_limit(bot, "retrieval_top_k", system_default=None)
    assert result == PLAN_LIMIT_SCHEMA["retrieval_top_k"]["default"]
