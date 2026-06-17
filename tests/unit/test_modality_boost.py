"""Unit tests — M17 modality-aware rerank boost (Agent A4).

Validates the post-rerank multiplicative boost helper introduced in
``ragbot.infrastructure.reranker._modality_boost``. Key invariants:

* Boost only fires when intent ↔ chunk_type pair is in the boost map;
  every other pair returns identity (1.0×).
* Table intents (table_lookup / list_lookup / comparison) boost tables.
* Code intents (code_lookup / how_to) boost code chunks.
* Override map (bot owner overrides) wins over default map.
* HALLU=0 sacred — the helper never fabricates content, only re-scores.

All assertions are real value/behavior checks per CLAUDE.md test rules.
"""

from __future__ import annotations

from ragbot.application.dto.block import Block
from ragbot.infrastructure.reranker._modality_boost import (
    apply_modality_boost,
    boost_chunks,
)
from ragbot.shared.constants import (
    DEFAULT_MODALITY_BOOST_CODE_LOOKUP,
    DEFAULT_MODALITY_BOOST_IDENTITY,
    DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
)


def test_apply_modality_boost_table_intent_table_chunk():
    """``table_lookup`` × ``table`` → multiplier from constant."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"}
    new = apply_modality_boost(chunk, "table_lookup")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_apply_modality_boost_table_intent_text_chunk_identity():
    """``table_lookup`` × ``text`` is NOT in the boost map → identity
    multiplier so plain prose isn't artificially penalised."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "text"}
    new = apply_modality_boost(chunk, "table_lookup")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_IDENTITY


def test_apply_modality_boost_code_intent_code_chunk():
    """``code_lookup`` × ``code`` → code multiplier (stronger than table)."""
    chunk = {"chunk_id": "c1", "score": 0.4, "chunk_type": "code"}
    new = apply_modality_boost(chunk, "code_lookup")
    assert new == 0.4 * DEFAULT_MODALITY_BOOST_CODE_LOOKUP


def test_apply_modality_boost_unknown_intent_identity():
    """Unknown intent → identity multiplier so the helper degrades
    gracefully when the router emits a label outside the boost map."""
    chunk = {"chunk_id": "c1", "score": 0.6, "chunk_type": "table"}
    new = apply_modality_boost(chunk, "weather_query")
    assert new == 0.6 * DEFAULT_MODALITY_BOOST_IDENTITY


def test_apply_modality_boost_empty_intent_identity():
    """Empty intent (router pass-through) returns identity."""
    chunk = {"chunk_id": "c1", "score": 0.3, "chunk_type": "table"}
    new = apply_modality_boost(chunk, "")
    assert new == 0.3


def test_apply_modality_boost_missing_score_zero():
    """Chunks with missing/None score collapse to 0.0 — boost stays
    harmless (any-multiplier × 0 = 0)."""
    chunk = {"chunk_id": "c1", "chunk_type": "table"}
    new = apply_modality_boost(chunk, "table_lookup")
    assert new == 0.0


def test_apply_modality_boost_with_overrides():
    """Bot-owner override map wins over default — partial overrides
    leave unmatched entries on the default."""
    chunk = {"chunk_id": "c1", "score": 1.0, "chunk_type": "table"}
    overrides = {"table_lookup:table": 1.5}
    new = apply_modality_boost(chunk, "table_lookup", boost_overrides=overrides)
    assert new == 1.5


def test_apply_modality_boost_block_dataclass():
    """Helper works with Block dataclass (M11) — same call pattern."""
    b = Block(chunk_id="c1", content="x", type="table", metadata={"score": 0.5})
    new = apply_modality_boost(b, "table_lookup")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_apply_modality_boost_string_score_collapses():
    """Defensive: non-numeric score → 0.0 (never raises)."""
    chunk = {"chunk_id": "c1", "score": "not_a_number", "chunk_type": "table"}
    new = apply_modality_boost(chunk, "table_lookup")
    assert new == 0.0


def test_apply_modality_boost_list_lookup_table():
    """``list_lookup`` × ``table`` → same multiplier as table_lookup
    (both signals are "table-shaped answer wanted")."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"}
    new = apply_modality_boost(chunk, "list_lookup")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_apply_modality_boost_how_to_code():
    """``how_to`` × ``code`` → code multiplier (tutorial-shaped intent)."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "code"}
    new = apply_modality_boost(chunk, "how_to")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_CODE_LOOKUP


def test_apply_modality_boost_table_row_alias():
    """``table_row`` chunk_type (Excel/CSV granular) gets table boost."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "table_row"}
    new = apply_modality_boost(chunk, "table_lookup")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_apply_modality_boost_chunk_type_fallback_to_type():
    """Legacy chunk dicts use ``type`` instead of ``chunk_type`` — fall
    back so retrieval-stage dicts still get boosted correctly."""
    chunk = {"chunk_id": "c1", "score": 0.5, "type": "table"}
    new = apply_modality_boost(chunk, "table_lookup")
    assert new == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_boost_chunks_mutates_dict_scores():
    """``boost_chunks`` writes the new score back into each dict in
    place — caller sees the mutation."""
    chunks = [
        {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"},
        {"chunk_id": "c2", "score": 0.5, "chunk_type": "text"},
    ]
    out = boost_chunks(chunks, "table_lookup")
    # Same list reference returned for fluent chaining.
    assert out is chunks
    # Table chunk boosted, text chunk unchanged.
    assert chunks[0]["score"] == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP
    assert chunks[1]["score"] == 0.5


def test_boost_chunks_preserves_order():
    """Order is NOT changed by boost — caller decides re-sorting."""
    chunks = [
        {"chunk_id": f"c{i}", "score": 0.5, "chunk_type": "text"}
        for i in range(5)
    ]
    out = boost_chunks(chunks, "table_lookup")
    assert [c["chunk_id"] for c in out] == ["c0", "c1", "c2", "c3", "c4"]


def test_boost_chunks_empty_list():
    """Empty input → empty output (no raises)."""
    out = boost_chunks([], "table_lookup")
    assert out == []
