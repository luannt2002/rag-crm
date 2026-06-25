"""[T1-Smartness] G4 — ingest data-quality ADVISORY (ADR-0005 advisory, not blocking).

After the stats extractor resolves column roles, the owner needs to learn *why*
coverage is limited WITHOUT being told to reformat: which columns the engine could
not bind to a role (NAME / price / category / aliases) and so fell to a generic
searchable attribute, and — the real coverage risk — whether ANY table produced a
NAME column at all.

``analyze_table_headers`` produces that report. It is purely informational: it never
blocks ingest, never drops data (Tier-3 generic attribute is still searchable), and
is domain-neutral (it reports the owner's own header labels, the engine assumes no
column meanings). An owner-declared column (incl. an explicit ``attribute``) is NOT
flagged — the owner already knows about it.
"""
from __future__ import annotations

from ragbot.shared.document_stats import analyze_table_headers


def _chunk(content: str) -> list[dict]:
    return [{"content": content}]


def test_full_happy_case_no_advisory() -> None:
    # Every column binds a role → nothing to advise, name column present.
    rep = analyze_table_headers(_chunk("Tên, Nhóm, Giá, Aliases\nGói A, Cao cấp, 500000, a;b\n"))
    assert rep["has_name_column"] is True
    assert rep["unassigned_columns"] == []


def test_phone_domain_inference_blind_flags_all_and_missing_name() -> None:
    # "Giá" anchors header detection; the engine recognises NONE of Model/RAM/Pin →
    # NO name column (the real coverage risk) + the three are reported unassigned.
    rep = analyze_table_headers(
        _chunk("Model, RAM, Pin, Giá\niPhone 15, 8GB, 3300mAh, 25000000\n")
    )
    assert rep["has_name_column"] is False  # surfaced → owner declares a NAME role
    assert set(rep["unassigned_columns"]) == {"Model", "RAM", "Pin"}  # 'Giá' = price


def test_owner_declared_columns_are_not_flagged() -> None:
    # Owner declares roles → name present, and declared columns (incl 'attribute')
    # are intentional → never reported as unassigned.
    rep = analyze_table_headers(
        _chunk("Model, RAM, Pin\niPhone 15, 8GB, 3300mAh\n"),
        custom_roles={"Model": "name", "RAM": "attribute", "Pin": "attribute"},
    )
    assert rep["has_name_column"] is True
    assert rep["unassigned_columns"] == []


def test_partial_unassigned_reports_only_the_unbound() -> None:
    # Name + price bind; an extra unrecognised column is reported (FYI), not dropped.
    rep = analyze_table_headers(_chunk("Tên, Giá, Xuất xứ\nGói A, 500000, Việt Nam\n"))
    assert rep["has_name_column"] is True
    assert rep["unassigned_columns"] == ["Xuất xứ"]


def test_advisory_skips_numeric_and_empty_header_cells() -> None:
    # Pure-number / empty header cells are not labelled columns → never flagged.
    rep = analyze_table_headers(_chunk("Tên, Giá, 2024,\nGói A, 500000, x,\n"))
    assert "2024" not in rep["unassigned_columns"]
    assert "" not in rep["unassigned_columns"]


def test_no_table_is_empty_report_not_crash() -> None:
    # Pure prose (no table) → empty, well-formed report, never raises.
    rep = analyze_table_headers(_chunk("Đây là một đoạn văn xuôi không có bảng.\n"))
    assert rep["has_name_column"] is False
    assert rep["unassigned_columns"] == []
    assert rep["tables_seen"] == 0


def test_advisory_dedups_repeated_unassigned_across_chunks() -> None:
    # Dual-index emits the same header in several chunks → report each label ONCE.
    chunks = [
        {"content": "Tên, Xuất xứ\nGói A, Việt Nam\n"},
        {"content": "Tên, Xuất xứ\nGói B, Nhật Bản\n"},
    ]
    rep = analyze_table_headers(chunks)
    assert rep["unassigned_columns"] == ["Xuất xứ"]
