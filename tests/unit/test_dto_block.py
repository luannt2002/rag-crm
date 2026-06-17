"""Unit tests — M11 Block dataclass DTO (Agent A4).

Validates the structured retrieval result wrapper introduced in
``ragbot.application.dto.block``. Focus areas:

* Backward-compat dict access (``block["content"]``, ``block.get("x", default)``)
  so the 300+ legacy chunk dict call-sites in query_graph keep working.
* :func:`from_chunk_dict` lift helper preserves identity, content, type,
  and folds unknown keys into metadata.
* ``as_dict`` serialiser round-trips the Block back to the legacy flat
  chunk shape used by the audit log / cache persistence path.

All assertions are real value/behavior checks per CLAUDE.md test rules.
"""

from __future__ import annotations

import pytest

from ragbot.application.dto.block import Block, from_chunk_dict


def test_block_construct_defaults():
    """Block built with only id+content gets ``type="text"`` and empty
    metadata + references. Identity defaults documented in
    ``DEFAULT_BLOCKS_API_ENABLED`` semantics."""
    b = Block(chunk_id="c1", content="hello")
    assert b.chunk_id == "c1"
    assert b.content == "hello"
    assert b.type == "text"
    assert b.metadata == {}
    assert b.references == []


def test_block_construct_full():
    """All-fields constructor preserves payload exactly. Important: no
    silent metadata merging — bot-owner data round-trips clean."""
    b = Block(
        chunk_id="c2",
        content="abc",
        type="table",
        metadata={"score": 0.42, "document_id": "d1"},
        references=["c3", "c4"],
    )
    assert b.type == "table"
    assert b.metadata["score"] == 0.42
    assert b.references == ["c3", "c4"]


def test_block_getitem_structural_keys():
    """Dict access maps id/chunk_id/content/type/metadata to dataclass
    fields exactly."""
    b = Block(chunk_id="c1", content="hello", type="code", metadata={})
    assert b["chunk_id"] == "c1"
    assert b["id"] == "c1"  # legacy alias
    assert b["content"] == "hello"
    assert b["type"] == "code"
    assert b["metadata"] == {}


def test_block_getitem_metadata_passthrough():
    """Unknown keys fall through to metadata — backward compat for
    legacy code reading ``chunk["score"]`` / ``chunk["document_id"]``."""
    b = Block(
        chunk_id="c1",
        content="x",
        metadata={"score": 0.9, "document_id": "doc-1"},
    )
    assert b["score"] == 0.9
    assert b["document_id"] == "doc-1"


def test_block_getitem_raises_keyerror():
    """Truly missing keys raise KeyError so ``in`` / ``except`` patterns
    still work. Mirrors dict semantics — silent None would mask bugs."""
    b = Block(chunk_id="c1", content="x")
    with pytest.raises(KeyError):
        _ = b["never_exists"]


def test_block_get_with_default():
    """``.get(key, default)`` falls through to metadata then to the
    default — matches ``dict.get`` behaviour exactly."""
    b = Block(chunk_id="c1", content="x", metadata={"score": 0.5})
    assert b.get("score", 0.0) == 0.5
    assert b.get("missing", "fallback") == "fallback"
    assert b.get("content") == "x"


def test_block_contains_operator():
    """``key in block`` works for structural fields + metadata keys."""
    b = Block(chunk_id="c1", content="x", metadata={"score": 0.5})
    assert "chunk_id" in b
    assert "content" in b
    assert "score" in b
    assert "missing" not in b
    assert 42 not in b  # non-string keys safely False


def test_block_as_dict_round_trip():
    """``as_dict`` flattens metadata at top level (legacy shape) and
    keeps both ``id`` + ``chunk_id`` aliases."""
    b = Block(
        chunk_id="c1",
        content="hello",
        type="table",
        metadata={"score": 0.7, "document_id": "d1"},
    )
    out = b.as_dict()
    assert out["chunk_id"] == "c1"
    assert out["id"] == "c1"
    assert out["content"] == "hello"
    assert out["type"] == "table"
    # metadata flattened at top level
    assert out["score"] == 0.7
    assert out["document_id"] == "d1"


def test_block_as_dict_with_references():
    """References emitted only when non-empty (keeps default round-trip
    minimal)."""
    b = Block(chunk_id="c1", content="x", references=["c2", "c3"])
    out = b.as_dict()
    assert out["references"] == ["c2", "c3"]

    b2 = Block(chunk_id="c1", content="x")
    out2 = b2.as_dict()
    assert "references" not in out2


def test_from_chunk_dict_basic():
    """Lift a minimal legacy chunk dict into a Block. The ``id`` key
    (legacy alias) is honored alongside ``chunk_id``."""
    raw = {"id": "c1", "content": "hello"}
    b = from_chunk_dict(raw)
    assert b.chunk_id == "c1"
    assert b.content == "hello"
    assert b.type == "text"  # default


def test_from_chunk_dict_full():
    """Lift a fully-populated legacy chunk dict — score / document_id
    / chunking_strategy land in metadata, unknown ``type`` falls back."""
    raw = {
        "chunk_id": "c1",
        "content": "table content",
        "type": "table",
        "score": 0.85,
        "document_id": "d1",
        "chunking_strategy": "hdt",
    }
    b = from_chunk_dict(raw)
    assert b.chunk_id == "c1"
    assert b.type == "table"
    assert b.metadata["score"] == 0.85
    assert b.metadata["document_id"] == "d1"
    assert b.metadata["chunking_strategy"] == "hdt"


def test_from_chunk_dict_unknown_type_falls_back_to_text():
    """Defensive: bogus ``type`` strings collapse to ``"text"`` rather
    than carry through and fail downstream Literal checks."""
    raw = {"chunk_id": "c1", "content": "x", "type": "BOGUS_LABEL"}
    b = from_chunk_dict(raw)
    assert b.type == "text"


def test_from_chunk_dict_chunk_type_alias():
    """``chunk_type`` field is honored as an alias for ``type`` when
    present (parser uses ``chunk_type``, retrieval uses ``type``)."""
    raw = {"id": "c1", "content": "x", "chunk_type": "code"}
    b = from_chunk_dict(raw)
    assert b.type == "code"


def test_from_chunk_dict_text_alias():
    """Legacy chunks use ``text`` instead of ``content`` — lift still
    populates content correctly."""
    raw = {"id": "c1", "text": "legacy content field"}
    b = from_chunk_dict(raw)
    assert b.content == "legacy content field"
