"""extract_all_codes — every distinct spec CODE in a comparison query.

Root cause of comparison 0/4 (G-095/097/098, load-test 2026-07-08): a
"So sánh giá A và B" query names ≥2 product codes, but ``parse_code_query``
returns only the FIRST (``re.search``) → the 2nd entity is never looked up and
the bot answers "no info for B" though the corpus HAS it. ``extract_all_codes``
returns every distinct code so each leg can be looked up.

Domain-neutral: all fixtures are shape-only (digit + letter + separator); no
brand/corpus literal drives the parse.
"""

from __future__ import annotations

from ragbot.shared.query_range_parser import extract_all_codes


def test_comparison_two_codes_extracted():
    q = "So sánh giá Rovelo 175/70R14 A68 và Landspider 215/55R16 G/P, loại nào đắt hơn?"
    assert extract_all_codes(q) == ["175/70R14", "215/55R16"]


def test_zr_codes_extracted():
    q = "So sánh giá Davanti 205/55ZR17 DX640 và Landspider 245/45ZR20 H/P, loại nào đắt hơn?"
    assert extract_all_codes(q) == ["205/55ZR17", "245/45ZR20"]


def test_suffix_fragments_not_mistaken_for_codes():
    # "G/P" / "H/T" carry a letter+separator but NO digit → not a spec code.
    q = "So sánh giá Landspider 195/60R15 G/P và Landspider 265/60R18 H/T?"
    assert extract_all_codes(q) == ["195/60R15", "265/60R18"]


def test_single_code_returns_one():
    # A plain price lookup names ONE code → the multi-code branch must NOT fire.
    assert extract_all_codes("giá 195/65R15 bao nhiêu") == ["195/65R15"]


def test_distinct_dedup_preserves_order():
    q = "195/65R15 vs 195/65R15 vs 205/55ZR17"
    assert extract_all_codes(q) == ["195/65R15", "205/55ZR17"]


def test_no_code_returns_empty():
    assert extract_all_codes("dịch vụ này giá bao nhiêu") == []
    assert extract_all_codes("") == []
