"""[T1-Smartness] G1 — column-role recognition cascade (exact → vocab → word-substring → fuzzy).

The stats extractor bound a header to a role ONLY by EXACT membership in a
Vietnamese frozenset, so a real-world header phrased differently ("Tên hàng",
"Mặt hàng", "Đơn giá bán", EN "Item"/"Price") fell out of scope → the column was
silently dropped to attributes_json → unsearchable. G1 makes the matcher robust
WITHOUT changing what a role means:

  1. exact (unchanged — happy-case never regresses),
  2. expanded vocab (sanctioned synonyms kept in the frozensets so the checker
     stays in sync),
  3. word-substring fallback (a role token appears as a whole word in the
     header — catches unseen combos),
  4. fuzzy fallback (accent/typo variant).

Disambiguation pinned: "Tên kho" (warehouse) must NOT steal the name role from
"Tên hàng" (the product) — the xe-1 bug.
"""
from __future__ import annotations

from ragbot.shared.document_stats import _column_roles


def _roles(header: list[str]) -> dict:
    return _column_roles(header)


# ── regression: exact happy-case unchanged ──────────────────────────────────
def test_exact_happy_case_unchanged() -> None:
    r = _roles(["Tên", "Nhóm", "Giá", "Aliases"])
    assert r["name"] == 0
    assert r["category"] == 1
    assert r["price"] == [2]
    assert r["aliases"] == 3


def test_exact_english_unchanged() -> None:
    r = _roles(["Item", "Category", "Price"])
    assert r["name"] == 0 and r["category"] == 1 and r["price"] == [2]


# ── G1: header variants that previously dropped now bind ─────────────────────
def test_name_variants_bind() -> None:
    for h in (["Tên hàng", "Giá"], ["Mặt hàng", "Giá"], ["Tên sản phẩm", "Giá"]):
        r = _roles(h)
        assert r["name"] == 0, h


def test_price_variants_bind() -> None:
    for h in (["Tên", "Đơn giá bán"], ["Tên", "Giá bán lẻ"], ["Tên", "Giá niêm yết"]):
        r = _roles(h)
        assert r["price"] == [1], h


def test_category_variants_bind() -> None:
    for h in (["Phân loại", "Tên", "Giá"], ["Thương hiệu", "Tên", "Giá"]):
        r = _roles(h)
        assert r["category"] == 0, h


def test_fuzzy_typo_diacritic_binds() -> None:
    # accent-dropped / spacing typo still binds price
    r = _roles(["Tên", "Gia  ban"])
    assert r["price"] == [1]


# ── xe-1 disambiguation: "Tên kho" must NOT win name over "Tên hàng" ─────────
def test_ten_kho_does_not_steal_name() -> None:
    r = _roles(["Tên kho", "Mã", "Tên hàng", "Giá"])
    assert r["name"] == 2, f"name must be 'Tên hàng' (idx 2), got {r['name']}"
    # 'Tên kho' is a warehouse/stub column → category (or at least NOT name)
    assert r["name"] != 0


# ── unmatched header still has no role (no false positive) ───────────────────
def test_unrelated_header_no_role() -> None:
    r = _roles(["Ghi chú lung tung", "Xyzzy"])
    assert r["name"] is None and r["category"] is None
    assert r["price"] == [] and r["aliases"] is None
