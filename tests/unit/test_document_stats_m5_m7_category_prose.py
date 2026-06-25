"""M5 + M7 — extraction noise: category-tag leak + prose-row mis-split.

M5: a single-cell line carrying a leaked "<chunk_context>…" enrichment tag was
used as the entity_CATEGORY for every row of its group (the name was already
shape-filtered, the category never was).

M7: a legal/policy sentence with an incidental comma ("… hạ tầng kỹ thuật, hệ
thống cáp …") passes the chunk delimiter gate, comma-splits into prose cells,
and its first clause becomes a false entity.

Both are rejected by SHAPE (tag-lead / sentence-terminator + no-price) — domain
neutral. CRITICALLY: real short product codes ("A68", "RHP-A68") must SURVIVE —
there is no over-broad numbered-name filter (that false-dropped real SKUs).
"""
from __future__ import annotations

from ragbot.shared.document_stats import parse_table_chunks


def _names_cats(content: str) -> list[tuple[str, str | None]]:
    ents = parse_table_chunks([{"content": content}])
    return [(e.name, e.category) for e in ents]


def test_m5_chunk_context_tag_not_used_as_category() -> None:
    content = (
        "<chunk_context>Bảng giá dịch vụ chăm sóc da</chunk_context>\n"
        "Massage body, 200000\n"
    )
    out = _names_cats(content)
    assert ("Massage body", None) in out, f"got {out}"
    # The tag must never become the category for the row.
    assert all(
        c is None or "<chunk_context>" not in (c or "") for _, c in out
    ), f"tag leaked into category: {out}"


def test_m7_prose_row_skipped() -> None:
    content = (
        "Tên, Giá\n"
        "Trung tâm dữ liệu bao gồm hạ tầng kỹ thuật, hệ thống cáp và phần mềm.\n"
    )
    names = [n for n, _ in _names_cats(content)]
    assert "Trung tâm dữ liệu bao gồm hạ tầng kỹ thuật" not in names
    assert names == [], f"prose row produced false entities: {names}"


def test_m7_keeps_priced_row_even_if_a_cell_ends_with_period() -> None:
    # A real catalog row carries a price → kept even if a description cell ends ".".
    content = "Gói cơ bản, Dịch vụ trọn gói., 500000\n"
    names = [n for n, _ in _names_cats(content)]
    assert "Gói cơ bản" in names, f"priced catalog row wrongly dropped: {names}"


def test_real_short_product_codes_survive() -> None:
    # Role-aware (G1): when a distinct "Tên hàng" NAME column exists, the entity
    # NAME is the product name and the "Mã hàng"/SKU column is preserved as a
    # SEARCHABLE attribute (never silently dropped). The regression-guard intent —
    # short SKUs ending in digits are KEPT — holds: they survive in attributes_json.
    content = (
        "Mã hàng, Tên hàng, Giá\n"
        "A68, Lốp xe NEOTERRA, 1500000\n"
        "RHP-A68, Lốp xe RHINO, 1800000\n"
    )
    ents = parse_table_chunks([{"content": content}])
    names = [e.name for e in ents]
    assert names == ["Lốp xe NEOTERRA", "Lốp xe RHINO"], f"name col mis-bound: {names}"
    skus = {str(v) for e in ents for v in e.attributes.values()}
    assert "A68" in skus, f"A68 SKU dropped from attributes: {skus}"
    assert "RHP-A68" in skus, f"RHP-A68 SKU dropped from attributes: {skus}"
