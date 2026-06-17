"""Autonomy resolver — single source of truth for effective answer autonomy
percent (0-100). Pure helper; no I/O, no infrastructure imports.

Phase A (P29-A) uses `docs_only` band globally, but the resolver is written
for the full spectrum so Phase B (per-bot column + admin UI) can plug in
without any call-site rewrite.
"""
from __future__ import annotations

from ragbot.shared.constants import (
    AUTONOMY_BAND_CONSTRAINED_MIN,
    AUTONOMY_BAND_LIBERAL_MIN,
    AUTONOMY_BAND_MODERATE_MIN,
    AUTONOMY_BAND_RESEARCH_MIN,
    AUTONOMY_PERCENT_MAX,
    AUTONOMY_PERCENT_MIN,
    DEFAULT_ANSWER_AUTONOMY_PERCENT,
)


def clamp_autonomy_percent(value: int | None) -> int:
    """Clamp arbitrary input to integer in [AUTONOMY_PERCENT_MIN, AUTONOMY_PERCENT_MAX].

    - None  → AUTONOMY_PERCENT_MIN (0)
    - bool  → int(bool) (True=1, False=0) — Python bool is an int subclass.
    - float → int(float) (truncate toward zero)
    - str   → int(str) if parseable, else MIN
    - bad   → MIN
    """
    if value is None:
        return AUTONOMY_PERCENT_MIN
    try:
        v = int(value)
    except (TypeError, ValueError):
        return AUTONOMY_PERCENT_MIN
    if v < AUTONOMY_PERCENT_MIN:
        return AUTONOMY_PERCENT_MIN
    if v > AUTONOMY_PERCENT_MAX:
        return AUTONOMY_PERCENT_MAX
    return v


def resolve_effective_autonomy_percent(
    bot_percent: int | None,
    system_default_percent: int | None,
) -> int:
    """Effective autonomy = max(clamp(bot), clamp(system_default)).

    Both inputs are clamped first, so None / garbage collapses to 0 before
    the max(). Result is always a pure int in [0, 100].

    Design note: we use MAX (not MIN/override) so a system-default raise
    (e.g. platform loosens to 30) applies to bots that did not pick a value,
    while bots that explicitly opted into a higher value keep theirs.
    """
    return max(
        clamp_autonomy_percent(bot_percent),
        clamp_autonomy_percent(system_default_percent),
    )


def autonomy_band(percent: int) -> str:
    """Map int percent to band name. Clamps first.

    Bands:
        0              → "docs_only"    (hard grounding — Phase A global)
        1..33          → "constrained"
        34..66         → "moderate"
        67..99         → "liberal"
        100            → "research"
    """
    p = clamp_autonomy_percent(percent)
    if p < AUTONOMY_BAND_CONSTRAINED_MIN:
        return "docs_only"
    if p < AUTONOMY_BAND_MODERATE_MIN:
        return "constrained"
    if p < AUTONOMY_BAND_LIBERAL_MIN:
        return "moderate"
    if p < AUTONOMY_BAND_RESEARCH_MIN:
        return "liberal"
    return "research"


__all__ = [
    "clamp_autonomy_percent",
    "resolve_effective_autonomy_percent",
    "autonomy_band",
]
