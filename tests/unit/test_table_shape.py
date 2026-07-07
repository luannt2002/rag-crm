"""ADR-0008 A1: shape/value column typing — pick the descriptive NAME by the SHAPE
of the cell values, NOT by a header word list (B1) or column position (B2).

Grounded in the REAL chinh-sach-xe data (DB-verified 2026-07-07): the catalog stores
the entity identity in two columns — an internal CODE ("2-R16 195/55 LPD", brand as a
2-3 letter suffix RVL/LPD) and the human product name ("Lốp Rovelo 195/55R16 RHP-A68").
The old code chose the CODE (vocab/positional guess) → brand lost → 97% false-deny.
Shape typing must pick the DESCRIPTIVE name deterministically, language/domain-neutral,
with ZERO vocabulary and ZERO model.
"""
from __future__ import annotations

from ragbot.shared.table_shape import (
    classify_cell_shape,
    discriminating_token_filter,
    pick_descriptive_name,
)

# Real 195/55R16 candidate set (what query_by_name_keyword returns for the size code) —
# mixed brands; the query names Rovelo, so only the Rovelo rows should survive.
_CANDS = [
    "Lốp Rovelo 195/55R16 RHP-A68",                     # 0 Rovelo
    "Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P",    # 1 Landspider (wrong brand)
    "Lốp Rovelo 195/55R16 RCMX+",                       # 2 Rovelo variant
]


# ── cell shape classification (shape only, no vocab, no language) ────────────
def test_descriptive_name_shape() -> None:
    assert classify_cell_shape("Lốp Rovelo 195/55R16 RHP-A68") == "name"
    assert classify_cell_shape("Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P") == "name"


def test_code_shape_not_name() -> None:
    # size + short brand-suffix code — must NOT be typed as a name
    assert classify_cell_shape("2-R16 195/55 LPD") == "code"
    assert classify_cell_shape("2-R14 175/65 RVL") == "code"


def test_money_and_number_shape() -> None:
    # money vs number boundary is fuzzy (currency floor) and irrelevant to name-picking;
    # what matters is a numeric cell is NEVER a name.
    assert classify_cell_shape("1.044.000") in ("money", "number")
    assert classify_cell_shape("26") in ("money", "number")


def test_url_and_list_shape() -> None:
    assert classify_cell_shape("https://drive.google.com/drive/folders/1vehsK") == "url"
    # the alias/search blob (comma-separated variants) is a LIST, never a name
    assert classify_cell_shape(
        "195/55R16, 195 55 16, 195 55R16, Landspider 195/55R16, Land 195/55R16"
    ) == "list"


# ── pick the descriptive name among an entity's own fields ──────────────────
def test_pick_name_over_code_and_aliasblob() -> None:
    """The Rovelo root: among {code, productname, alias-blob} the descriptive
    productname must win — NOT the code (old bug) and NOT the alias list."""
    got = pick_descriptive_name([
        "2-R16 195/55 LPD",                                    # code (old entity_name)
        "Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P",       # productname (real name)
        "195/55R16, 195 55 16, Landspider 195/55R16, Land 195/55R16, 195/55R16 G/P",  # aliases
    ])
    assert got == "Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P"


def test_pick_prefers_more_descriptive() -> None:
    # a group/warehouse stub ("Kho lốp ...") is name-shaped but SHORTER than the
    # full product name → the fuller descriptive string wins.
    got = pick_descriptive_name([
        "Kho lốp ROVELO",
        "Lốp Rovelo 195/55R16 RHP-A68",
    ])
    assert got == "Lốp Rovelo 195/55R16 RHP-A68"


def test_pick_falls_back_to_code_when_no_name() -> None:
    # nothing descriptive available → return the best available (the code), never None
    assert pick_descriptive_name(["2-R16 195/55 LPD"]) == "2-R16 195/55 LPD"


def test_pick_ignores_empty_and_none() -> None:
    assert pick_descriptive_name(["", None, "  ", "Lốp Rovelo 175/65R14 RHP-A68"]) == "Lốp Rovelo 175/65R14 RHP-A68"
    assert pick_descriptive_name([]) is None
    assert pick_descriptive_name(["", None]) is None


# ── B3: brand-aware narrowing by discriminating query tokens ────────────────
def test_brand_token_narrows_to_matching_brand() -> None:
    """The Rovelo residual: for 'Rovelo 195/55R16' the size-code match returns
    every brand of that size; the brand token must drop the Landspider row."""
    keep = discriminating_token_filter("Lốp Rovelo 195/55R16 giá bao nhiêu?", _CANDS)
    assert keep == [0, 2]  # both Rovelo rows kept, Landspider dropped


def test_shared_category_word_not_discriminating() -> None:
    # "Lốp" is in EVERY candidate → not discriminating → must not filter anything;
    # with no brand named, all same-size candidates stay.
    assert discriminating_token_filter("giá lốp 195/55R16", _CANDS) == [0, 1, 2]


def test_grammar_words_ignored() -> None:
    # question/grammar words appear in NO candidate → never filter (no stopword list).
    assert discriminating_token_filter("giá bao nhiêu vậy shop ơi?", _CANDS) == [0, 1, 2]


def test_single_or_empty_candidate_passthrough() -> None:
    assert discriminating_token_filter("Rovelo", ["Lốp Rovelo 195/55R16"]) == [0]
    assert discriminating_token_filter("Rovelo", []) == []


# ── A4: shape-based name selection at INGEST (headerless row) ────────────────
def test_ingest_headerless_name_by_shape() -> None:
    """xe-1 style headerless row: positional picks the warehouse/code stub;
    shape picks the descriptive product name (with brand) — fixes the DSI at the
    SOURCE so entity_name is the real name, not an internal code."""
    from ragbot.shared.document_stats import _extract_entity_from_row

    cols = [
        "Kho lốp LANDSPIDER",
        "2-R16 195/55 LPD",
        "Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P",
        "26",
        "",
    ]
    legacy = _extract_entity_from_row(cols, [], 0, None, None, name_by_shape=False)
    shaped = _extract_entity_from_row(cols, [], 0, None, None, name_by_shape=True)
    assert shaped is not None
    assert shaped.name == "Lốp xe LANDSPIDER 195/55R16 87V CITYTRAXX G/P"
    # legacy positional picks the FIRST cell (the warehouse stub), not the name
    assert legacy is not None and legacy.name != shaped.name


def test_shape_name_wins_over_category_on_same_column() -> None:
    """Domain-neutral: a spa 'Vùng | Giá' table gives the body-part column a CATEGORY
    role, but that cell ("Cả chân", "Nách") IS the service name. Shape-typing must
    keep the row — the shape-picked name wins over the category role on the same
    column — instead of dropping it nameless. (Regression exposed by the spa bot;
    the xe bot has no category column so it hid this.)"""
    from ragbot.shared.document_stats import _column_roles, _extract_entity_from_row

    header = ["Vùng", "Giá buổi lẻ", "Giá Combo 10 buổi"]
    roles = _column_roles(header)  # "Vùng" → category, "Giá …" → price, name → None
    cols = ["Cả chân", "699.000", "6.291.000"]
    e = _extract_entity_from_row(cols, header, 0, None, roles, name_by_shape=True)
    assert e is not None
    assert e.name == "Cả chân"
