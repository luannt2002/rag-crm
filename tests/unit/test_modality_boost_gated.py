"""Unit tests — F9 config gate for the modality rerank boost.

Validates that ``ragbot.infrastructure.reranker._modality_boost`` honors
the per-bot DEFAULT-OFF gate (``plan_limits.modality_rerank_enabled``).

Invariants under test:

* Flag OFF (the default) -> byte-identical: scores are NOT mutated, the
  English-vocab boost map is never consulted, and a missing / None /
  non-numeric score is NOT coerced (no write-back at all).
* Flag ON -> the boost is applied exactly as before the gate existed.
* The boost map is config-sourced: a caller-supplied ``boost_map`` wins
  over the in-module English seed, and ``boost_overrides`` still merges
  on top.
* The default of the public functions is OFF (no caller can accidentally
  enable the boost by omitting the flag).

All assertions are real value/behavior checks per CLAUDE.md test rules.
"""

from __future__ import annotations

import copy
import inspect

from ragbot.application.dto.block import Block
from ragbot.infrastructure.reranker._modality_boost import (
    apply_modality_boost,
    boost_chunks,
)
from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA
from ragbot.shared.constants import (
    DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
    DEFAULT_MODALITY_RERANK_ENABLED,
)


# ───────────────────────── gate default is OFF ─────────────────────────


def test_schema_default_is_off():
    """The plan-limit schema entry defaults to the OFF constant."""
    assert DEFAULT_MODALITY_RERANK_ENABLED is False
    assert (
        PLAN_LIMIT_SCHEMA["modality_rerank_enabled"]["default"]
        is DEFAULT_MODALITY_RERANK_ENABLED
    )


def test_apply_modality_boost_enabled_param_defaults_false():
    """`enabled` MUST default to False so the boost is opt-in."""
    sig = inspect.signature(apply_modality_boost)
    assert sig.parameters["enabled"].default is False


def test_boost_chunks_enabled_param_defaults_false():
    sig = inspect.signature(boost_chunks)
    assert sig.parameters["enabled"].default is False


# ───────────────────── flag OFF -> byte-identical ──────────────────────


def test_apply_off_returns_raw_score_no_multiplier():
    """Gate OFF (explicit) returns the raw score unchanged — even for a
    table chunk + table intent that WOULD otherwise be boosted."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"}
    out = apply_modality_boost(chunk, "table_lookup", enabled=False)
    assert out == 0.5
    # And it is genuinely the un-boosted value (sanity vs the ON path).
    assert out != 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_apply_off_is_default():
    """Omitting the flag entirely == OFF (no accidental boost)."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"}
    assert apply_modality_boost(chunk, "table_lookup") == 0.5


def test_boost_chunks_off_does_not_mutate_dicts():
    """Gate OFF -> dict scores are byte-identical (no write-back)."""
    chunks = [
        {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"},
        {"chunk_id": "c2", "score": 0.42, "chunk_type": "text"},
    ]
    before = copy.deepcopy(chunks)
    out = boost_chunks(chunks, "table_lookup", enabled=False)
    assert out is chunks
    assert chunks == before


def test_boost_chunks_off_does_not_coerce_missing_score():
    """Gate OFF must NOT touch a chunk with no ``score`` key — the ON
    path would coerce it to 0.0 via write-back; OFF leaves it absent."""
    chunks = [{"chunk_id": "c1", "chunk_type": "table"}]
    boost_chunks(chunks, "table_lookup", enabled=False)
    assert "score" not in chunks[0]


def test_boost_chunks_off_does_not_coerce_none_score():
    """Gate OFF leaves a None score as None (ON path would zero it)."""
    chunks = [{"chunk_id": "c1", "score": None, "chunk_type": "table"}]
    boost_chunks(chunks, "table_lookup", enabled=False)
    assert chunks[0]["score"] is None


def test_boost_chunks_off_does_not_coerce_string_score():
    """Gate OFF leaves a non-numeric score untouched."""
    chunks = [{"chunk_id": "c1", "score": "n/a", "chunk_type": "table"}]
    boost_chunks(chunks, "table_lookup", enabled=False)
    assert chunks[0]["score"] == "n/a"


def test_boost_chunks_off_block_metadata_untouched():
    """Gate OFF must not write into a Block's metadata score."""
    b = Block(
        chunk_id="c1", content="x", type="table", metadata={"score": 0.5}
    )
    boost_chunks([b], "table_lookup", enabled=False)
    assert b.metadata["score"] == 0.5


# ───────────────────────── flag ON -> boost ────────────────────────────


def test_apply_on_applies_seed_boost():
    """Gate ON with no config map uses the English seed multiplier."""
    chunk = {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"}
    out = apply_modality_boost(chunk, "table_lookup", enabled=True)
    assert out == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_boost_chunks_on_mutates_scores():
    """Gate ON writes boosted scores back; non-matching chunk unchanged."""
    chunks = [
        {"chunk_id": "c1", "score": 0.5, "chunk_type": "table"},
        {"chunk_id": "c2", "score": 0.5, "chunk_type": "text"},
    ]
    out = boost_chunks(chunks, "table_lookup", enabled=True)
    assert out is chunks
    assert chunks[0]["score"] == 0.5 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP
    assert chunks[1]["score"] == 0.5


# ───────────────────── config-sourced boost map ────────────────────────


def test_apply_on_config_boost_map_wins_over_seed():
    """A caller-supplied (config-sourced) boost_map overrides the
    in-module English seed for the same intent:chunk_type key."""
    chunk = {"chunk_id": "c1", "score": 1.0, "chunk_type": "table"}
    cfg_map = {"table_lookup:table": 2.0}
    out = apply_modality_boost(
        chunk, "table_lookup", enabled=True, boost_map=cfg_map
    )
    assert out == 2.0
    # Seed value is NOT used when a config map is supplied.
    assert out != 1.0 * DEFAULT_MODALITY_BOOST_TABLE_LOOKUP


def test_apply_on_config_boost_map_intent_outside_map_is_identity():
    """A config map that omits the active key -> identity (no boost),
    proving the English seed is not silently consulted as a fallback
    for keys the config map simply does not define."""
    chunk = {"chunk_id": "c1", "score": 1.0, "chunk_type": "table"}
    cfg_map = {"some_other_intent:table": 9.0}
    out = apply_modality_boost(
        chunk, "table_lookup", enabled=True, boost_map=cfg_map
    )
    assert out == 1.0


def test_apply_on_overrides_win_over_config_map():
    """boost_overrides merge on top of the config map."""
    chunk = {"chunk_id": "c1", "score": 1.0, "chunk_type": "table"}
    cfg_map = {"table_lookup:table": 2.0}
    overrides = {"table_lookup:table": 3.0}
    out = apply_modality_boost(
        chunk,
        "table_lookup",
        enabled=True,
        boost_map=cfg_map,
        boost_overrides=overrides,
    )
    assert out == 3.0


def test_boost_chunks_on_threads_config_map():
    """boost_chunks forwards the config map to per-chunk boosting."""
    chunks = [{"chunk_id": "c1", "score": 1.0, "chunk_type": "table"}]
    cfg_map = {"table_lookup:table": 2.0}
    boost_chunks(chunks, "table_lookup", enabled=True, boost_map=cfg_map)
    assert chunks[0]["score"] == 2.0
