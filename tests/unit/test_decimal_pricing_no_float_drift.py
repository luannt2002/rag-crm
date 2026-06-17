"""Decimal end-to-end on the pricing path (Hidden Bug Scan Round 2 — Bug 2 / P0).

DB column ``ai_models.input_price_per_1k_usd`` is ``Numeric(10,6)`` — casting
to ``float`` mid-pipeline silently drops precision and accumulates a "penny
leak" at scale (e.g. 1 cent per ~1000 calls). Guard the three layers:

1. Repo ``_to_model`` — must NOT cast Decimal → float.
2. DTO ``ModelRow`` — annotated as ``Decimal``.
3. Cost-compute call sites — must keep ``Decimal`` (no ``float(...)`` wrap).

Plus a precision smoke that compares Decimal-only vs float round-trip
drift over many calls.
"""
from __future__ import annotations

import dataclasses
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragbot.application.ports.ai_config_port import ModelRow
from ragbot.infrastructure.llm.dynamic_litellm_router import compute_cost_usd


_ROOT = Path(__file__).resolve().parents[2]


# --- 1. DTO type annotation -------------------------------------------------

def test_model_row_input_price_is_decimal_type() -> None:
    """``ModelRow`` declares pricing fields as ``Decimal``, not ``float``."""
    fields = {f.name: f.type for f in dataclasses.fields(ModelRow)}
    # ``from __future__ import annotations`` makes ``f.type`` a string.
    assert fields["input_price_per_1k_usd"] == "Decimal", (
        f"input_price_per_1k_usd must be Decimal, got {fields['input_price_per_1k_usd']!r}"
    )
    assert fields["output_price_per_1k_usd"] == "Decimal", (
        f"output_price_per_1k_usd must be Decimal, got {fields['output_price_per_1k_usd']!r}"
    )


# --- 2. Repo conversion preserves Decimal ----------------------------------

def test_ai_config_repository_returns_decimal() -> None:
    """``_to_model`` must propagate the ORM ``Decimal`` value untouched."""
    from ragbot.infrastructure.repositories.ai_config_repository import _to_model

    fake_orm_row = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        record_provider_id="00000000-0000-0000-0000-000000000002",
        name="test-model",
        kind="chat",
        context_window=8192,
        max_output_tokens=4096,
        input_price_per_1k_usd=Decimal("0.000015"),
        output_price_per_1k_usd=Decimal("0.000060"),
        supports_streaming=True,
        supports_tools=True,
        supports_vision=False,
        supports_json_mode=True,
        languages=["vi", "en"],
        enabled=True,
        metadata_json={},
    )
    row = _to_model(fake_orm_row)
    assert isinstance(row.input_price_per_1k_usd, Decimal), (
        f"repo leaked Decimal → {type(row.input_price_per_1k_usd).__name__}"
    )
    assert isinstance(row.output_price_per_1k_usd, Decimal)
    # Exact precision preserved (this is the whole point — float would round).
    assert row.input_price_per_1k_usd == Decimal("0.000015")
    assert row.output_price_per_1k_usd == Decimal("0.000060")


# --- 3. Repo source must not cast float() on price columns -----------------

def test_no_float_cast_in_pricing_path() -> None:
    """grep guard: repo + query_graph must not ``float(...)`` price/cost values."""
    repo_src = (
        _ROOT / "src/ragbot/infrastructure/repositories/ai_config_repository.py"
    ).read_text(encoding="utf-8")
    # No ``float(r.input_price_per_1k_usd)`` or output_price.
    assert not re.search(
        r"float\s*\(\s*r\.input_price_per_1k_usd", repo_src,
    ), "repo must not cast input_price_per_1k_usd to float"
    assert not re.search(
        r"float\s*\(\s*r\.output_price_per_1k_usd", repo_src,
    ), "repo must not cast output_price_per_1k_usd to float"

    qg_src = (
        _ROOT / "src/ragbot/orchestration/query_graph.py"
    ).read_text(encoding="utf-8")
    # No ``float(_router_compute_cost(...))`` — must propagate Decimal.
    assert not re.search(
        r"float\s*\(\s*_router_compute_cost\s*\(", qg_src,
    ), "query_graph must not float-wrap _router_compute_cost (penny leak)"


# --- 4. Cost-compute precision smoke (Decimal vs float drift) --------------

def test_compute_cost_precision_preserved_over_many_calls() -> None:
    """Decimal arithmetic must accumulate exactly; float would drift."""
    pricing = SimpleNamespace(
        input_per_1k_usd=Decimal("0.000015"),
        output_per_1k_usd=Decimal("0.000060"),
        cached_input_per_1k_usd=Decimal("0.000007"),
    )
    n_calls = 1000
    prompt = 137  # primes → guarantees fractional cents
    completion = 251
    cached = 13

    # Decimal accumulation (the path under test).
    total_decimal = Decimal("0")
    for _ in range(n_calls):
        total_decimal += compute_cost_usd(pricing, prompt, completion, cached)

    # Closed-form expected — Decimal exact.
    expected = (
        Decimal(n_calls)
        * (
            Decimal(prompt - cached) / Decimal(1000) * pricing.input_per_1k_usd
            + Decimal(cached) / Decimal(1000) * pricing.cached_input_per_1k_usd
            + Decimal(completion) / Decimal(1000) * pricing.output_per_1k_usd
        )
    )
    drift = total_decimal - expected
    assert drift == Decimal("0"), (
        f"Decimal accumulation drift = {drift} (expected 0). "
        "If non-zero, compute_cost_usd has a precision bug."
    )

    # And confirm the float-cast variant DOES drift (this is why we kept Decimal).
    total_float = 0.0
    for _ in range(n_calls):
        total_float += float(compute_cost_usd(pricing, prompt, completion, cached))
    float_drift = abs(Decimal(repr(total_float)) - expected)
    # We don't assert on float drift magnitude (varies by Python version);
    # we only assert that the Decimal path is bit-exact regardless.
    assert total_decimal == expected


def test_compute_cost_returns_decimal() -> None:
    """``compute_cost_usd`` is the boundary that must always return Decimal."""
    pricing = SimpleNamespace(
        input_per_1k_usd=Decimal("0.000015"),
        output_per_1k_usd=Decimal("0.000060"),
        cached_input_per_1k_usd=None,
    )
    cost = compute_cost_usd(pricing, 100, 50, 10)
    assert isinstance(cost, Decimal), (
        f"compute_cost_usd must return Decimal, got {type(cost).__name__}"
    )


# --- 5. Structured-output usage capture preserves Decimal ------------------

def test_query_graph_so_usage_keeps_decimal_cost() -> None:
    """grep: structured-output _so_usage init + capture must use Decimal."""
    qg_src = (
        _ROOT / "src/ragbot/orchestration/query_graph.py"
    ).read_text(encoding="utf-8")
    # Init line uses Decimal("0") (not 0.0).
    assert '"cost_usd": Decimal("0")' in qg_src, (
        "_so_usage init must seed cost_usd with Decimal('0') not 0.0"
    )
    # Capture path stores raw _router_compute_cost result (no float wrap).
    assert "_so_usage[\"cost_usd\"] = _router_compute_cost(" in qg_src, (
        "_capture_so_usage must store Decimal directly from "
        "_router_compute_cost (no float() wrap)"
    )
