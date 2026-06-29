"""F5 dual-read close — surface the ingest-time VERBATIM original of a chunk
(exact table grid / formula source, stored read-only in chunk metadata) into
the context fence at answer time.

Tests the pure module-level resolver ``_resolve_verbatim_fence`` directly
(mirrors the ``_extract_locked_prices`` test pattern — no heavy graph DI).
The resolver returns ``""`` whenever the fence must stay byte-identical
(flag off, no verbatim present, or verbatim equals the already-fenced text),
and a read-only ``<verbatim>…</verbatim>`` data segment otherwise.

Sacred-rule 10: the surfaced text is ingest DATA placed read-only inside the
data envelope — no instruction text, no answer override. Domain-neutral
fixtures (generic ``Item A`` / a generic formula) — shape-only.
"""
from __future__ import annotations

from ragbot.orchestration.nodes.generate import _resolve_verbatim_fence
from ragbot.shared.constants import (
    DEFAULT_GENERATE_VERBATIM_TAG,
    NARRATE_METADATA_KEY_RAW_CHUNK,
)

_TAG = DEFAULT_GENERATE_VERBATIM_TAG
_TABLE = "| Tên | Giá |\n| --- | --- |\n| Item A | 700000 |"
_NARRATED = "Bảng có 2 cột (Tên, Giá). Dòng 1: Tên=Item A, Giá=700000."


def test_metadata_raw_chunk_surfaces_verbatim_when_enabled():
    """A chunk whose verbatim grid is stored under the narrate ``raw_chunk``
    metadata key surfaces it as a fenced data segment when the flag is on."""
    chunk = {"text": _NARRATED, "metadata": {NARRATE_METADATA_KEY_RAW_CHUNK: _TABLE}}
    out = _resolve_verbatim_fence(
        chunk, chunk["metadata"], _NARRATED, enabled=True, tag=_TAG,
    )
    assert out == f"\n<{_TAG}>{_TABLE}</{_TAG}>"
    # The exact numbers from the grid are now present in the fence.
    assert "700000" in out
    # Read-only data envelope only — no instruction / answer-override text.
    assert "instruction" not in out.lower()
    assert "must" not in out.lower()


def test_top_level_original_content_surfaces():
    """Top-level ``original_content`` (entity field / compression-preserved)
    takes precedence and is surfaced."""
    chunk = {"text": _NARRATED, "original_content": _TABLE, "metadata": {}}
    out = _resolve_verbatim_fence(
        chunk, chunk["metadata"], _NARRATED, enabled=True, tag=_TAG,
    )
    assert out == f"\n<{_TAG}>{_TABLE}</{_TAG}>"


def test_metadata_original_content_key_surfaces():
    """``metadata['original_content']`` is resolved when present."""
    meta = {"original_content": _TABLE}
    chunk = {"text": _NARRATED, "metadata": meta}
    out = _resolve_verbatim_fence(chunk, meta, _NARRATED, enabled=True, tag=_TAG)
    assert out == f"\n<{_TAG}>{_TABLE}</{_TAG}>"


def test_precedence_top_level_over_metadata():
    """Top-level field wins over both metadata keys (precedence order)."""
    top = "| Item A | 700000 |"
    meta = {"original_content": "META_OC", NARRATE_METADATA_KEY_RAW_CHUNK: "META_RAW"}
    chunk = {"text": _NARRATED, "original_content": top, "metadata": meta}
    out = _resolve_verbatim_fence(chunk, meta, _NARRATED, enabled=True, tag=_TAG)
    assert out == f"\n<{_TAG}>{top}</{_TAG}>"


def test_no_verbatim_returns_empty_byte_identical():
    """Without any verbatim original, the resolver returns '' so the fence is
    byte-identical to its current form (VN/legacy happy-path)."""
    chunk = {"text": "some prose answer text", "metadata": {"document_title": "d"}}
    out = _resolve_verbatim_fence(
        chunk, chunk["metadata"], chunk["text"], enabled=True, tag=_TAG,
    )
    assert out == ""


def test_flag_off_returns_empty_even_when_present():
    """Default OFF: even when a verbatim is present, the resolver is a no-op
    so the default fence stays byte-identical (A/B before flip, rule #0)."""
    chunk = {"text": _NARRATED, "metadata": {NARRATE_METADATA_KEY_RAW_CHUNK: _TABLE}}
    out = _resolve_verbatim_fence(
        chunk, chunk["metadata"], _NARRATED, enabled=False, tag=_TAG,
    )
    assert out == ""


def test_verbatim_equal_to_text_is_not_duplicated():
    """When the verbatim equals the already-fenced text, no duplicate segment
    is emitted (fence stays byte-identical)."""
    chunk = {"text": _TABLE, "metadata": {NARRATE_METADATA_KEY_RAW_CHUNK: _TABLE}}
    out = _resolve_verbatim_fence(
        chunk, chunk["metadata"], _TABLE, enabled=True, tag=_TAG,
    )
    assert out == ""


def test_non_string_verbatim_is_ignored():
    """A non-string metadata value degrades to '' (no crash, byte-identical)."""
    meta = {NARRATE_METADATA_KEY_RAW_CHUNK: {"unexpected": "dict"}}
    chunk = {"text": _NARRATED, "metadata": meta}
    out = _resolve_verbatim_fence(chunk, meta, _NARRATED, enabled=True, tag=_TAG)
    assert out == ""


def test_formula_source_surfaces():
    """Formula source (generic) surfaces the same way as a table grid."""
    formula = "E = m * c^2"
    narrated = "Energy equals mass times the speed of light squared."
    chunk = {"text": narrated, "metadata": {NARRATE_METADATA_KEY_RAW_CHUNK: formula}}
    out = _resolve_verbatim_fence(
        chunk, chunk["metadata"], narrated, enabled=True, tag=_TAG,
    )
    assert out == f"\n<{_TAG}>{formula}</{_TAG}>"
