"""B2 — GoogleSheetsParser must emit ATOMIC row-as-chunk (one chunk per data
row, each carrying its section + header), NOT one big blob. Multi-row packing
is the root cause of cross-row value mis-binding (bot reads stock/price/date
of a neighbouring row). Stats extraction is decoupled (runs on raw rows), so
chunk granularity only changes retrieval — exactly what we fix.

Domain-neutral fixtures (Item A/B/C, generic columns) — shape only.
"""
from __future__ import annotations

import asyncio

from ragbot.infrastructure.parser.google_sheets_parser import GoogleSheetsParser

_CSV = (
    "STT,Ten,Gia,Ton\n"
    "1,Item A,100000,50\n"
    "2,Item B,200000,30\n"
    "3,Item C,300000,20\n"
).encode("utf-8")


def _parse(csv: bytes) -> list[dict]:
    return asyncio.run(GoogleSheetsParser().parse(csv, file_name="t.csv"))


def test_emits_one_chunk_per_data_row() -> None:
    out = _parse(_CSV)
    assert len(out) >= 3, f"expected row-as-chunk (≥3 data rows), got {len(out)} chunk(s)"


def test_rows_are_atomic_no_cross_row_packing() -> None:
    bodies = [c["content"] for c in _parse(_CSV)]
    a = [b for b in bodies if "Item A" in b]
    assert a, "Item A row chunk missing"
    # The atomic guarantee: Item A's chunk must NOT carry Item B/C values.
    assert "Item B" not in a[0] and "Item C" not in a[0], (
        "multi-row packing — different rows share a chunk (value mis-bind risk)"
    )


def test_each_row_chunk_carries_its_header_labels() -> None:
    bodies = [c["content"] for c in _parse(_CSV)]
    a = next(b for b in bodies if "Item A" in b)
    # Column labels travel WITH the row so the LLM binds value→column correctly.
    assert "Ten" in a and "Gia" in a and "Ton" in a, (
        "header labels not bound into the row chunk"
    )


def test_parser_tag_stamped_for_row_preserve_path() -> None:
    out = _parse(_CSV)
    assert out[0]["metadata"].get("parser") == "google_sheets", (
        "row-preserve path keys off metadata.parser — must stay stamped"
    )


def test_empty_input_returns_empty() -> None:
    assert _parse(b"") == []
    assert _parse(b"   \n  \n") == []
