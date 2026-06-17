"""CR / per-chunk-enrich ROW GATE (2026-06-13, Phase 2A).

Pins the ingest gate that skips per-chunk LLM enrichment (inline CR, legacy
enrich_chunks, chunk_context storage) for tabular strategies whose row chunks
are self-describing. Evidence: a 225K-char Google Sheet (xe-3) emitted
hundreds of CSV row chunks, each one a per-chunk gpt-4.1-mini call, grinding
ingest for minutes with ~0 retrieval lift (rows already carry their header).

Pure-function test — no DB / LLM / live app.
"""
from __future__ import annotations

from ragbot.application.services.document_service import should_skip_row_enrich
from ragbot.shared.constants import (
    CR_ROW_GATED_STRATEGIES,
    DEFAULT_ENRICH_ROW_GATE_ENABLED,
)


def test_tabular_strategies_skip_when_gate_on() -> None:
    for strat in ("table_csv", "table_dual_index"):
        assert should_skip_row_enrich(strat, gate_enabled=True), strat


def test_prose_strategies_never_skip() -> None:
    for strat in ("recursive", "hdt", "semantic", "proposition", "parser_preserve"):
        assert not should_skip_row_enrich(strat, gate_enabled=True), strat


def test_gate_off_disables_skip_even_for_tables() -> None:
    # Roll-back path: enrich_row_gate_enabled=false → enrich everything again.
    assert not should_skip_row_enrich("table_csv", gate_enabled=False)
    assert not should_skip_row_enrich("table_dual_index", gate_enabled=False)


def test_gated_strategy_set_matches_table_strategies() -> None:
    # The gate must cover exactly the tabular strategies — drift here would
    # silently leave one tabular strategy paying for per-chunk enrichment.
    assert CR_ROW_GATED_STRATEGIES == frozenset({"table_csv", "table_dual_index"})
    assert DEFAULT_ENRICH_ROW_GATE_ENABLED is True
