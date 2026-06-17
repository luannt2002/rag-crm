"""Sysprompt version resolver — per-bot metadata fallback chain.

The resolver returns a METADATA LABEL only. The platform never uses
this value to substitute prompt text into the LLM call — bot owners
own their ``bots.system_prompt`` column verbatim (CLAUDE.md "Bot owner
owns everything"). The label exists so admin tooling can audit which
reference template each bot row started from.

Tests pin three behaviours:

1. The default fallback (no plan_limits) is the baseline label.
2. A valid ``plan_limits.sysprompt_version`` override wins.
3. An unknown value falls back to the baseline so admin tooling never
   sees a label it cannot interpret (safe-default).
"""

from __future__ import annotations

from types import SimpleNamespace

from ragbot.orchestration.system_prompts import resolve_sysprompt_version
from ragbot.shared.constants import (
    ALLOWED_SYSPROMPT_VERSIONS,
    DEFAULT_SYSPROMPT_VERSION,
    SYSPROMPT_VERSION_BASELINE,
    SYSPROMPT_VERSION_CONTEXT_AWARE,
)


def _bot(plan_limits: dict | None = None) -> SimpleNamespace:
    """Build the lightest possible bot-config stand-in.

    The resolver only reads the ``plan_limits`` attribute; using a real
    ``BotConfig`` DTO here would couple this unit test to unrelated
    pydantic validation surface."""
    return SimpleNamespace(plan_limits=plan_limits)


def test_default_returns_baseline_when_plan_limits_missing() -> None:
    """A bot row with no plan_limits payload resolves to the baseline
    metadata label. The default MUST equal the baseline constant."""
    bot = _bot(plan_limits=None)
    assert resolve_sysprompt_version(bot) == SYSPROMPT_VERSION_BASELINE
    assert DEFAULT_SYSPROMPT_VERSION == SYSPROMPT_VERSION_BASELINE


def test_default_returns_baseline_when_plan_limits_empty_dict() -> None:
    """Empty plan_limits dict is treated the same as missing."""
    bot = _bot(plan_limits={})
    assert resolve_sysprompt_version(bot) == SYSPROMPT_VERSION_BASELINE


def test_context_aware_override_is_honoured() -> None:
    """When the bot owner explicitly opts in, the override label
    wins."""
    bot = _bot(plan_limits={"sysprompt_version": SYSPROMPT_VERSION_CONTEXT_AWARE})
    assert resolve_sysprompt_version(bot) == SYSPROMPT_VERSION_CONTEXT_AWARE


def test_explicit_baseline_override_round_trips() -> None:
    """An explicit baseline opt-in resolves to the same baseline label
    — exercising the in-list branch independent of the fallback."""
    bot = _bot(plan_limits={"sysprompt_version": SYSPROMPT_VERSION_BASELINE})
    assert resolve_sysprompt_version(bot) == SYSPROMPT_VERSION_BASELINE


def test_unknown_value_falls_back_to_baseline_safely() -> None:
    """A stale or out-of-range label MUST NOT propagate to admin
    tooling. The resolver clamps to the baseline so callers never see
    an unrecognised value."""
    bot = _bot(plan_limits={"sysprompt_version": "experimental_unreleased_label"})
    assert resolve_sysprompt_version(bot) == SYSPROMPT_VERSION_BASELINE


def test_non_string_value_falls_back_to_baseline() -> None:
    """Misconfigured types (None / int / dict) clamp to the baseline
    rather than blowing up the orchestration path."""
    for bad_value in (None, 42, ["v7"], {"label": "v7"}):
        bot = _bot(plan_limits={"sysprompt_version": bad_value})
        assert resolve_sysprompt_version(bot) == SYSPROMPT_VERSION_BASELINE


def test_resolved_value_is_always_a_known_label() -> None:
    """Whatever the input, the resolved value must be a known label so
    downstream switch statements never need a default arm."""
    for plan_limits in (
        None,
        {},
        {"sysprompt_version": SYSPROMPT_VERSION_BASELINE},
        {"sysprompt_version": SYSPROMPT_VERSION_CONTEXT_AWARE},
        {"sysprompt_version": "garbage"},
        {"sysprompt_version": None},
    ):
        bot = _bot(plan_limits=plan_limits)
        assert resolve_sysprompt_version(bot) in ALLOWED_SYSPROMPT_VERSIONS


def test_bot_without_plan_limits_attribute_falls_back_to_baseline() -> None:
    """A bot stand-in that omits the attribute entirely (older row shape
    in some legacy fixtures) must still resolve safely."""

    class _BotMissingAttr:
        pass

    assert resolve_sysprompt_version(_BotMissingAttr()) == SYSPROMPT_VERSION_BASELINE
