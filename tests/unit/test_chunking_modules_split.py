"""Unit tests for the chunking package split (strangler refactor 2026-06-15).

The 3192-line ``shared/chunking.py`` god-file was carved into a package:
``vn_structural`` / ``analyze`` / ``blocks`` / ``csv_chunker`` / ``strategies``
+ ``__init__`` (core dispatch). These tests (a) assert real behaviour of the
extracted pure functions so the modules are covered in isolation, and (b) pin
the public API surface so a future carve cannot silently drop a re-exported
name (the bug class that bit us mid-refactor: a swallowed ``import *`` line).
"""
from __future__ import annotations

import importlib

import pytest


# ── (a) behaviour of extracted pure functions ──────────────────────────────
class TestVnStructural:
    def test_roman_to_arabic_valid_and_invalid(self) -> None:
        from ragbot.shared.chunking.vn_structural import roman_to_arabic
        assert roman_to_arabic("III") == 3
        assert roman_to_arabic("IV") == 4
        assert roman_to_arabic("MCMXCIV") == 1994
        assert roman_to_arabic("IIII") is None  # malformed round-trip
        assert roman_to_arabic("3") is None      # not roman
        assert roman_to_arabic("") is None

    def test_normalize_vn_section_numerals(self) -> None:
        from ragbot.shared.chunking.vn_structural import normalize_vn_section_numerals
        assert normalize_vn_section_numerals("Chương III") == "Chương 3"
        assert normalize_vn_section_numerals("chương 3") == "Chương 3"  # case fixup
        assert normalize_vn_section_numerals("Vào lúc 5 giờ") == "Vào lúc 5 giờ"  # untouched

    def test_detect_vn_structural_anchor(self) -> None:
        from ragbot.shared.chunking.vn_structural import detect_vn_structural_anchor
        assert detect_vn_structural_anchor("Điều 55") == ("Điều", "55")
        assert detect_vn_structural_anchor("Chương 3 nói gì") == ("Chương", "3")
        assert detect_vn_structural_anchor("Mục III và Điều 22") is None  # multi-anchor
        assert detect_vn_structural_anchor("giá triệt lông") is None

    def test_promote_vn_hierarchical_headings_gates_on_min_matches(self) -> None:
        from ragbot.shared.chunking.vn_structural import promote_vn_hierarchical_headings
        # A single casual mention must NOT be promoted into a fake heading.
        casual = "Điều 1 nên đọc kỹ hợp đồng trước khi ký."
        assert promote_vn_hierarchical_headings(casual) == casual


class TestAnalyze:
    def test_is_csv_format_true_for_table(self) -> None:
        from ragbot.shared.chunking.analyze import _is_csv_format
        csv = "Dịch vụ,Giá\nTriệt lông,500000\nTrị mụn,700000\nNâng cơ,900000\n"
        assert _is_csv_format(csv) is True

    def test_is_csv_format_false_for_prose(self) -> None:
        from ragbot.shared.chunking.analyze import _is_csv_format
        prose = "Đây là một đoạn văn xuôi bình thường. Không có cấu trúc bảng nào cả."
        assert _is_csv_format(prose) is False

    def test_analyze_document_returns_profile_keys(self) -> None:
        from ragbot.shared.chunking.analyze import analyze_document
        profile = analyze_document("# Heading\n\nNội dung đoạn văn.\n" * 5)
        assert isinstance(profile, dict)
        assert "total_headings" in profile
        assert "is_csv_format" in profile

    def test_select_strategy_returns_known_strategy(self) -> None:
        from ragbot.shared.chunking.analyze import select_strategy, analyze_document
        profile = analyze_document("Dịch vụ,Giá\nA,1\nB,2\nC,3\n")
        strategy, confidence = select_strategy(profile)
        assert strategy in {"table_csv", "recursive", "hdt", "semantic", "proposition", "hybrid"}
        assert 0.0 <= confidence <= 1.0


class TestBlocksAndStrategies:
    def test_split_into_blocks_with_atomic_returns_typed_blocks(self) -> None:
        from ragbot.shared.chunking.blocks import _split_into_blocks_with_atomic
        blocks = _split_into_blocks_with_atomic("# H1\n\npara one\n\npara two\n")
        assert isinstance(blocks, list)
        assert all(isinstance(b, tuple) and len(b) == 2 for b in blocks)

    def test_extract_structural_path_parses_prefix(self) -> None:
        from ragbot.shared.chunking.strategies import extract_structural_path
        out = extract_structural_path("[Chương 3 > Điều 55]\nNội dung điều 55.")
        assert out["structural_path"]["full"] == "Chương 3 > Điều 55"
        assert out["structural_path"]["parts"] == ["Chương 3", "Điều 55"]
        assert out["content"].startswith("Nội dung")

    def test_extract_structural_path_no_prefix(self) -> None:
        from ragbot.shared.chunking.strategies import extract_structural_path
        out = extract_structural_path("plain chunk, no path")
        assert out["structural_path"] is None
        assert out["content"] == "plain chunk, no path"


# ── (b) public-API regression guard for the split ──────────────────────────
# Every name a caller (or test) imports from the package today. If a future
# carve drops one of these from a module's __all__ / re-export, this fails
# loudly instead of at runtime in an importer.
_PUBLIC_API = [
    # core dispatch
    "smart_chunk", "smart_chunk_atomic", "merge_orphan_chunks",
    "generate_parent_child_chunks",
    # analyze
    "analyze_document", "analyze_document_blocks", "select_strategy",
    "apply_cross_check", "_is_table_line", "_is_csv_format",
    # vn_structural
    "roman_to_arabic", "normalize_vn_section_numerals",
    "detect_vn_structural_anchor", "build_vn_structural_like_clauses",
    "promote_vn_hierarchical_headings",
    # blocks
    "_split_into_blocks_with_atomic", "_is_atomic_block_type",
    # csv
    "_chunk_table_csv_with_context", "_CsvRegion",
    # strategies
    "extract_structural_path",
]


@pytest.mark.parametrize("name", _PUBLIC_API)
def test_public_api_still_importable_from_package(name: str) -> None:
    mod = importlib.import_module("ragbot.shared.chunking")
    assert hasattr(mod, name), f"{name} lost from chunking package after split"


def test_external_private_imports_preserved() -> None:
    """Private names other modules import directly must still resolve."""
    # doc_profile imports _is_table_line; narrate imports _split_into_blocks_with_atomic
    from ragbot.shared.chunking import _is_table_line, _split_into_blocks_with_atomic
    assert callable(_is_table_line)
    assert callable(_split_into_blocks_with_atomic)
