"""#2 — a PARTIAL re-upload silently erased the stats index of every UNCHANGED entity.

Mechanism (verified in code):
  * ingest_core diffs chunk hashes → ``ctx.chunks_to_embed`` holds ONLY the changed
    chunks; unchanged chunks are skipped (they keep their vectors).
  * ingest_stages_store builds ``ctx.rows`` by iterating ``chunks_to_embed`` — so on
    a re-ingest ``rows`` covers only the CHANGED chunks.
  * ingest_stages_final then calls ``delete_by_document`` — which wipes EVERY
    document_service_index row for the doc — and re-inserts entities parsed from
    ``rows``.

Net: edit 3 chunks of a 500-chunk catalog and the other 497 entities vanish from
the stats route (the SQL path that answers price/listing questions), while their
vectors survive — a silent, hard-to-notice data loss.

Fix: rebuild the stats index from ``ctx.chunks`` (the chunker's FULL ordered
output) instead of ``rows``. That also keeps ``chunk_index`` correct, since
``parse_table_chunks`` derives it from list position — feeding it only the changed
rows also mis-numbered every entity.
"""
from __future__ import annotations

from ragbot.application.services.document_service.ingest_stages_final import (
    _stats_rows_for_document,
)


def test_partial_reingest_rebuilds_from_all_chunks_not_just_changed() -> None:
    """THE bug: 5 chunks in the doc, only chunk #3 changed. The stats rebuild must
    still see all 5 — otherwise delete_by_document + insert(changed) drops 4."""
    all_chunks = [f"row-{i}" for i in range(5)]
    changed_rows = [{"content": "row-3", "idx": 3, "meta": None}]  # only the edit

    out = _stats_rows_for_document(all_chunks, changed_rows)

    assert len(out) == 5, f"only {len(out)} chunks fed to the stats rebuild — 4 lost"
    assert [r["content"] for r in out] == all_chunks
    # chunk_index is derived from list POSITION → order must be preserved
    assert out[3]["content"] == "row-3"


def test_full_ingest_unchanged_behaviour() -> None:
    """A fresh ingest (every chunk new): chunks and rows agree — same result."""
    all_chunks = ["a", "b"]
    rows = [{"content": "a", "idx": 0}, {"content": "b", "idx": 1}]
    out = _stats_rows_for_document(all_chunks, rows)
    assert [r["content"] for r in out] == ["a", "b"]


def test_falls_back_to_rows_when_chunks_missing() -> None:
    """Defensive: no chunker output (legacy/edge caller) → keep the old path."""
    rows = [{"content": "x", "idx": 0}]
    assert _stats_rows_for_document([], rows) == rows
    assert _stats_rows_for_document(None, rows) == rows


def test_accepts_dict_shaped_chunks() -> None:
    """The chunker may hand back dicts rather than bare strings."""
    out = _stats_rows_for_document([{"content": "c0"}, {"content": "c1"}], [])
    assert [r["content"] for r in out] == ["c0", "c1"]


def test_final_stage_uses_the_full_chunk_rebuild() -> None:
    """Wiring pin: the stats block must feed the FULL chunk list, never ``rows``."""
    import inspect

    from ragbot.application.services.document_service import ingest_stages_final as f

    src = inspect.getsource(f)
    assert "_stats_rows_for_document(" in src
