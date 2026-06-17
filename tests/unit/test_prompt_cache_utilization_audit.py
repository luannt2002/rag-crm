"""T1.5.S28 — audit_prompt_cache_utilization aggregation tests.

Drives ``aggregate_rows`` with handcrafted ``model_invocations``-style
dicts (no postgres) to verify per-purpose grouping, hit-ratio
calculation, and potential-savings estimate.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest


# Load the script as a module so we can call its pure helpers without
# spawning a subprocess. The script lives outside the package tree.
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "audit_prompt_cache_utilization.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "audit_prompt_cache_utilization", _SCRIPT_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

aggregate_rows = _MODULE.aggregate_rows
render_table = _MODULE.render_table
PurposeStats = _MODULE.PurposeStats


ANTHROPIC_NOMINAL = Decimal("3.0000")  # USD per 1k tokens (Claude Sonnet ~2024)
ANTHROPIC_CACHED = Decimal("0.3000")   # 10% read rate


def _row(
    *,
    purpose: str,
    provider: str = "anthropic",
    prompt_tokens: int = 1000,
    completion_tokens: int = 200,
    cost_usd: Decimal,
    nominal: Decimal = ANTHROPIC_NOMINAL,
    cached: Decimal = ANTHROPIC_CACHED,
) -> dict:
    return {
        "purpose": purpose,
        "provider": provider,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "model_id": "anthropic/claude-sonnet-4-5",
        "input_price_per_1k_usd": nominal,
        "input_price_per_1k_cached_usd": cached,
    }


def test_aggregate_groups_by_purpose_and_provider():
    """Two rows for grading + one for generation → 2 PurposeStats."""
    rows = [
        _row(purpose="grading", cost_usd=Decimal("0.01")),
        _row(purpose="grading", cost_usd=Decimal("0.01")),
        _row(purpose="generation", cost_usd=Decimal("0.02")),
    ]
    stats = aggregate_rows(rows)
    purposes = {s.purpose: s for s in stats}
    assert set(purposes.keys()) == {"grading", "generation"}
    assert purposes["grading"].total_calls == 2
    assert purposes["generation"].total_calls == 1
    assert purposes["grading"].total_prompt_tokens == 2000


def test_zero_prompt_tokens_rows_dropped():
    """Failed / preflight calls (prompt_tokens=0) skew rates → drop them."""
    rows = [
        _row(purpose="rewriting", prompt_tokens=0, cost_usd=Decimal("0")),
        _row(purpose="rewriting", prompt_tokens=500, cost_usd=Decimal("0.0015")),
    ]
    stats = aggregate_rows(rows)
    assert len(stats) == 1
    assert stats[0].total_calls == 1
    assert stats[0].total_prompt_tokens == 500


def test_hit_ratio_zero_when_full_nominal_rate():
    """If every call is billed at the nominal rate → discount=0% → hit≈0%."""
    # 1000 prompt tokens at $3/1k input, 200 completion at $15/1k (proxied
    # via a cost split that lands input at the nominal rate). Hand-craft
    # cost_usd so input portion = 1000/1k * 3 = 3.0 USD (no discount).
    # Model assumes input/output share by token count: input share = 1000/1200.
    # Total cost so input slice = 3.0 → total = 3.0 * (1200/1000) = 3.6 USD.
    rows = [_row(purpose="grading", cost_usd=Decimal("3.6"))]
    stats = aggregate_rows(rows)
    s = stats[0]
    assert s.discount_pct == Decimal("0") or s.discount_pct < Decimal("0.01")
    assert s.hit_ratio_estimate < Decimal("0.05")


def test_hit_ratio_high_when_input_costs_near_cached_rate():
    """If cost matches the cache-read rate → discount≈90% → hit≈100%."""
    # Input at cached rate $0.3/1k → 1000 tokens = 0.3 USD input slice.
    # Total cost = 0.3 * (1200/1000) = 0.36 USD assuming output split.
    rows = [_row(purpose="grading", cost_usd=Decimal("0.36"))]
    stats = aggregate_rows(rows)
    s = stats[0]
    # Discount ≈ 1 - 0.3/3.0 = 0.90 → hit_ratio ≈ 1.0
    assert s.discount_pct > Decimal("0.85")
    assert s.hit_ratio_estimate > Decimal("0.90")


def test_potential_savings_uses_cached_rate_when_present():
    """potential_savings_usd = (nominal - cached) * (prompt_tokens / 1k)."""
    rows = [
        _row(
            purpose="generation",
            prompt_tokens=10_000,
            completion_tokens=0,
            cost_usd=Decimal("0.30"),  # at full nominal rate
        ),
    ]
    stats = aggregate_rows(rows)
    s = stats[0]
    # nominal=3.0, cached=0.3 → diff=2.7 per 1k → 10k tokens → 27 USD
    expected = (ANTHROPIC_NOMINAL - ANTHROPIC_CACHED) * Decimal(10)
    assert s.potential_savings_usd == expected


def test_render_table_marks_below_target_purposes():
    """Purposes with hit_ratio < 30% get an asterisk in the table."""
    rows = [
        # 100% miss: cost matches nominal → discount=0
        _row(purpose="grading", cost_usd=Decimal("3.6")),
        # 100% hit: cost matches cached → discount≈90%
        _row(purpose="generation", cost_usd=Decimal("0.36")),
    ]
    stats = aggregate_rows(rows)
    out = render_table(stats)
    grading_line = next(line for line in out.splitlines() if line.startswith("grading"))
    generation_line = next(line for line in out.splitlines() if line.startswith("generation"))
    # Grading is below the 30% target → asterisk; generation is well above → no marker.
    assert grading_line.rstrip().endswith("*") or " *" in grading_line
    # Generation line shouldn't have the trailing-asterisk marker.
    # (a literal "*" still appears in the legend below the separator,
    # so we look at the rendered data line specifically.)
    assert " *" not in generation_line.split("USD")[0]


def test_per_provider_separation():
    """Anthropic + OpenAI rows for the same purpose stay in separate buckets."""
    rows = [
        _row(purpose="generation", provider="anthropic", cost_usd=Decimal("0.36")),
        _row(
            purpose="generation",
            provider="openai",
            nominal=Decimal("0.6"),  # GPT-4.1-mini ~ $0.6/1k
            cached=Decimal("0.15"),
            cost_usd=Decimal("0.072"),
        ),
    ]
    stats = aggregate_rows(rows)
    keys = {(s.purpose, s.provider) for s in stats}
    assert ("generation", "anthropic") in keys
    assert ("generation", "openai") in keys


def test_nominal_rate_averaged_over_rows():
    """Mixed-rate rows in same bucket → nominal_input_rate is the mean."""
    rows = [
        _row(purpose="rewriting", nominal=Decimal("3.0"), cost_usd=Decimal("0.001")),
        _row(purpose="rewriting", nominal=Decimal("5.0"), cost_usd=Decimal("0.001")),
    ]
    stats = aggregate_rows(rows)
    assert len(stats) == 1
    assert stats[0].nominal_input_rate_per_1k == Decimal("4.0")


def test_aggregate_rows_handles_string_cost_usd():
    """Some DB drivers return Decimal as str — accept both shapes."""
    rows = [
        {
            "purpose": "grading",
            "provider": "anthropic",
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "cost_usd": "0.36",
            "model_id": "anthropic/claude-sonnet-4-5",
            "input_price_per_1k_usd": "3.0",
            "input_price_per_1k_cached_usd": "0.3",
        },
    ]
    stats = aggregate_rows(rows)
    assert stats[0].total_cost_usd == Decimal("0.36")
    assert stats[0].nominal_input_rate_per_1k == Decimal("3.0")
