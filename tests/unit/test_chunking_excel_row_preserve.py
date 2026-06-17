"""Stream A Phase 2 — Excel-row chunks must NOT be flattened+rechunked.

Bug (G2): ``DocumentService._route_through_parser`` joins parser chunks
with ``"\\n\\n".join(...)`` and passes the resulting blob through
``smart_chunk`` again. A 50-row Excel sheet collapses into ~5–10 prose
chunks, destroying the per-row ``Tên: X | Giá: Y`` semantics.

Phase 2 introduces a parser-aware path: when the upstream provider
already emitted row-level chunks (``ExcelOpenpyxlParser``,
``GoogleSheetsParser`` post-Phase-1, etc.), ``ingest`` must preserve
that 1-row → 1-chunk mapping instead of re-chunking.

These tests pin the post-Phase-2 contract using ``smart_chunk`` directly
on a synthesised CSV blob, since ``DocumentService.ingest`` requires DB
plumbing that this unit test deliberately avoids.
"""
from __future__ import annotations

from ragbot.shared.chunking import (
    _chunk_table_csv,
    _is_csv_format,
    select_strategy,
    smart_chunk,
)

# 12 rows + header — typical small bảng-giá sheet.
CSV_FIXTURE = """Topic,Dich vu,Vung,Gia
Bang gia triet long,Triet long Diode Laser,Mep,899000
Bang gia triet long,Triet long Diode Laser,Mat,1499000
Bang gia triet long,Triet long Diode Laser,Nach,1199000
Bang gia triet long,Triet long Diode Laser,Bikini,1799000
Bang gia triet long,Triet long Diode Laser,Toan than,3999000
Bang gia cham soc,Cap am co ban,Mat,700000
Bang gia cham soc,Hydrafacial,Mat,1200000
Bang gia massage,Massage body,Toan than,500000
Bang gia massage,Massage co vai gay,Vai gay,300000
Bang gia goi dau,Goi dau duong sinh,Da dau,250000
Bang gia goi dau,Goi dau thao moc,Da dau,350000
Bang gia khac,VIP combo,Toan than,5999000
"""


def test_csv_fixture_is_detected_as_table() -> None:
    profile = analyze_or_strategy_input(CSV_FIXTURE)
    assert profile.get("is_csv_format"), (
        f"fixture should be recognised as CSV; profile={profile}"
    )


def test_table_csv_path_emits_row_per_chunk() -> None:
    rows_in = [ln for ln in CSV_FIXTURE.strip().split("\n")[1:] if ln.strip()]
    chunks = _chunk_table_csv(CSV_FIXTURE, max_chunk_chars=2048)
    assert len(chunks) == len(rows_in), (
        f"expected one chunk per data row ({len(rows_in)}); got {len(chunks)} — "
        "flattening regression"
    )


def test_smart_chunk_routes_csv_into_row_strategy() -> None:
    """select_strategy must keep CSV → table_csv fast-path; smart_chunk follows."""
    strategy, _conf = select_strategy(_profile(CSV_FIXTURE))
    assert strategy == "table_csv", f"expected table_csv strategy, got {strategy}"

    chunks = smart_chunk(CSV_FIXTURE, chunk_size=2048, chunk_overlap=0)
    # 12 data rows + (optionally) the header preserved upstream
    assert 12 <= len(chunks) <= 13, (
        f"row preservation broken: 12 data rows but smart_chunk returned {len(chunks)}"
    )


def test_each_row_chunk_self_contains_columns() -> None:
    chunks = _chunk_table_csv(CSV_FIXTURE, max_chunk_chars=2048)
    for ch in chunks:
        text = ch["content"] if isinstance(ch, dict) else str(ch)
        # Header + row → both column names and a value should be present.
        assert "Dich vu" in text, f"header context missing on row chunk: {text!r}"
        assert "," in text, f"row delimiter missing: {text!r}"


# ── helpers ────────────────────────────────────────────────────────────


def _profile(text: str) -> dict:
    """Build the profile dict select_strategy() expects, without going through analyze_document()."""
    from ragbot.shared.chunking import analyze_document

    return analyze_document(text)


def analyze_or_strategy_input(text: str) -> dict:
    return _profile(text)
