"""The input CONTRACT test: a document conforming to a golden template is parsed by
L1→L7 with 0 errors / 100% coverage. If this breaks, the happy-case guarantee is void.

Templates live in docs/dev/templates/ (the reference the customer copies). This test
runs each through the real pipeline (converter → stats) and asserts exact extraction —
so "conforms to template ⇒ expert control, no row lost" is a locked, verifiable claim.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ragbot.shared.chunking import smart_chunk
from ragbot.shared.document_stats import parse_table_chunks
from ragbot.shared.tabular_markdown import rows_to_structured_markdown

_TPL = Path(__file__).resolve().parents[2] / "docs" / "dev" / "templates"


def _sheet_entities(csv_path: Path):
    rows = list(csv.reader(io.StringIO(csv_path.read_text(encoding="utf-8"))))
    md = rows_to_structured_markdown(rows)
    return md, parse_table_chunks([{"content": md}])


def test_catalog_single_template_100pct() -> None:
    """A single-table price list: every row → a priced entity, nothing dropped."""
    _, ents = _sheet_entities(_TPL / "catalog_single.csv")
    priced = {e.name: e.price_primary for e in ents if e.price_primary}
    assert priced == {
        "Dịch vụ cơ bản": 500000,
        "Dịch vụ nâng cao": 800000,
        "Combo trọn gói": 1200000,
    }
    assert len(ents) == 3, "no extra/noise rows"


def test_catalog_multisection_template_binds_sections() -> None:
    """A multi-sub-table sheet: every row → an entity bound to its ## section (B3)."""
    md, ents = _sheet_entities(_TPL / "catalog_multisection.csv")
    assert "## Nhóm dịch vụ nhóm A" in md
    assert "## Nhóm dịch vụ nhóm B" in md
    by_name = {e.name: (e.price_primary, e.category) for e in ents}
    assert by_name == {
        "Mục A1": (100000, "Nhóm dịch vụ nhóm A"),
        "Mục A2": (200000, "Nhóm dịch vụ nhóm A"),
        "Mục B1": (150000, "Nhóm dịch vụ nhóm B"),
        "Mục B2": (250000, "Nhóm dịch vụ nhóm B"),
    }


def test_document_template_headings_and_table() -> None:
    """A prose doc: headings drive chunking; the embedded table is extracted + atomic."""
    md = (_TPL / "document.md").read_text(encoding="utf-8")
    chunks = [c if isinstance(c, str) else c.get("content", "") for c in smart_chunk(md)]
    assert chunks, "produces chunks"
    # the table survives whole in some chunk (no mid-table cut)
    assert any("Hạng mục A" in c and "Hạng mục B" in c for c in chunks)
    # the table rows are extractable as entities with their values
    ents = parse_table_chunks([{"content": md}])
    names = {e.name for e in ents}
    assert {"Hạng mục A", "Hạng mục B"} <= names


def test_every_template_lints_as_happy_case() -> None:
    """Sanity: each data template is HAPPY-CASE per the checker's own rules."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from check_happy_case import check_one  # type: ignore

    for tpl in ("catalog_single.csv", "catalog_multisection.csv", "document.md"):
        verdict = check_one(str(_TPL / tpl), (_TPL / tpl).read_text(encoding="utf-8"))
        assert "HAPPY-CASE" in verdict, f"{tpl} → {verdict}"


def test_checker_db_mode_does_not_double_transform() -> None:
    """#5 (audit 2026-06-23) — in --db mode the content is ALREADY the parser's
    structured-markdown; check_one(from_db=True) must feed it straight to the
    extractor. Re-running the CSV converter would double-transform pipe-markdown
    into zero entities and report a clean catalog as a false NON-HAPPY."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from check_happy_case import check_one  # type: ignore

    md = rows_to_structured_markdown([
        ["Tên", "Giá"],
        ["Dịch vụ A", "500000"],
        ["Dịch vụ B", "800000"],
    ])
    assert "HAPPY-CASE" in check_one("catalog-from-db", md, from_db=True)
