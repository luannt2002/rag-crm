"""Metadata filter strategy registry + ArticleAwareFilter — unit tests.

Pins (C2 Phase-C — Article-aware metadata pre-filter):

- Registry default = ``null`` for missing / unknown / empty / None provider.
- Port Protocol satisfied by NullFilter and ArticleAwareFilter.
- NullFilter is a true no-op for any input (multi-lingual, content-agnostic).
- ArticleAwareFilter with the platform-default VN pattern list extracts
  ``article_no`` / ``clause_no`` / ``section_no`` / ``appendix_no`` /
  ``chapter_no`` from a query.
- ArticleAwareFilter is **operator-configurable**: a tenant supplying a
  non-VN keyword set gets matching anchors with no code change.
- Malformed pattern entries degrade gracefully (skip + log; do not crash).
- Empty / whitespace input → ``{}`` (no metadata pollution).
- Vertical-agnostic — no industry / brand literal in fixtures.
- SQL splitter routes chunk-level keys (``article_no`` ...) onto the
  chunk JSONB containment clause; document-level keys keep the existing
  documents-subquery containment clause.
"""

from __future__ import annotations

import json
from uuid import uuid4

from ragbot.application.ports.metadata_filter_port import MetadataFilterPort
from ragbot.infrastructure.metadata_filter.article_aware_filter import (
    ArticleAwareFilter,
)
from ragbot.infrastructure.metadata_filter.null_filter import NullFilter
from ragbot.infrastructure.metadata_filter.registry import (
    build_metadata_filter,
    list_providers,
)
from ragbot.shared.constants import (
    CHUNK_LEVEL_METADATA_FILTER_KEYS,
    DEFAULT_ARTICLE_REF_PATTERNS,
)


# --------------------------------------------------------------------------- #
# Registry resolution                                                         #
# --------------------------------------------------------------------------- #


def test_registry_default_is_null_for_falsy_or_unknown() -> None:
    """Falsy / typo / None all collapse to NullFilter — no boot crash."""
    for prov in (None, "", "   ", "does_not_exist_xyz", "ARTICLE_TYPO"):
        instance = build_metadata_filter(prov)
        assert isinstance(instance, NullFilter), f"prov={prov!r}"


def test_registry_resolves_known_providers() -> None:
    """Each registered key returns the matching class."""
    assert isinstance(build_metadata_filter("null"), NullFilter)
    assert isinstance(
        build_metadata_filter("article_aware", patterns=[]),
        ArticleAwareFilter,
    )
    # Case-insensitive resolution mirrors entity_extractor registry.
    assert isinstance(build_metadata_filter("NULL"), NullFilter)
    assert isinstance(
        build_metadata_filter("Article_Aware", patterns=[]),
        ArticleAwareFilter,
    )


def test_list_providers_sorted_and_complete() -> None:
    providers = list_providers()
    assert "null" in providers
    assert "article_aware" in providers
    assert providers == sorted(providers), "list_providers must return sorted"
    assert len(providers) >= 2


def test_registry_kwargs_filtered_safely() -> None:
    """Unknown kwargs from DI container must not blow up construction."""
    inst = build_metadata_filter("null", api_key="ignored", patterns=[])
    assert isinstance(inst, NullFilter)
    inst2 = build_metadata_filter(
        "article_aware", patterns=[], random_kw="x",
    )
    assert isinstance(inst2, ArticleAwareFilter)


# --------------------------------------------------------------------------- #
# Port Protocol + provider name                                               #
# --------------------------------------------------------------------------- #


def test_all_strategies_implement_port_protocol() -> None:
    assert isinstance(NullFilter(), MetadataFilterPort)
    assert isinstance(ArticleAwareFilter(patterns=[]), MetadataFilterPort)


def test_provider_names_match_registry_keys() -> None:
    """get_provider_name must equal the registry key — pin against drift."""
    assert NullFilter.get_provider_name() == "null"
    assert ArticleAwareFilter.get_provider_name() == "article_aware"


# --------------------------------------------------------------------------- #
# Null strategy — true no-op                                                  #
# --------------------------------------------------------------------------- #


def test_null_filter_returns_empty_for_any_input() -> None:
    n = NullFilter()
    assert n.extract("Điều 3") == {}
    assert n.extract("Article 11") == {}
    assert n.extract("") == {}
    assert n.extract("   ") == {}
    assert n.extract("?!?") == {}


# --------------------------------------------------------------------------- #
# ArticleAwareFilter with platform-default patterns                           #
# --------------------------------------------------------------------------- #


def _build_default_filter() -> ArticleAwareFilter:
    return ArticleAwareFilter(patterns=list(DEFAULT_ARTICLE_REF_PATTERNS))


def test_filter_extracts_article_number_from_query() -> None:
    out = _build_default_filter().extract("Điều 3 quy định gì?")
    assert out == {"article_no": "3"}


def test_filter_extracts_clause_number_from_query() -> None:
    out = _build_default_filter().extract("Khoản 5 nói về điều gì?")
    assert out == {"clause_no": "5"}


def test_filter_extracts_multiple_keys_from_combined_query() -> None:
    """Query mentioning chapter + article + clause yields all three keys."""
    out = _build_default_filter().extract(
        "Chương II Điều 7 Khoản 1 quy định gì?",
    )
    assert out["chapter_no"] == "II"
    assert out["article_no"] == "7"
    assert out["clause_no"] == "1"


def test_filter_extracts_chapter_arabic_and_roman() -> None:
    f = _build_default_filter()
    assert f.extract("Chương 3 nói gì?") == {"chapter_no": "3"}
    assert f.extract("Chương IX nói gì?") == {"chapter_no": "IX"}


def test_filter_extracts_appendix_letter_and_digit() -> None:
    f = _build_default_filter()
    assert f.extract("Phụ lục A là gì?") == {"appendix_no": "A"}
    assert f.extract("Phụ lục 1 là gì?") == {"appendix_no": "1"}


def test_filter_extracts_three_digit_article_number() -> None:
    """Civil-code corpora reference Điều 100..999."""
    out = _build_default_filter().extract("Điều 117 có hiệu lực không?")
    assert out == {"article_no": "117"}


def test_filter_case_insensitive_match() -> None:
    f = _build_default_filter()
    assert f.extract("ĐIỀU 8") == {"article_no": "8"}
    assert f.extract("điều 11") == {"article_no": "11"}


def test_filter_returns_empty_when_no_anchor_in_query() -> None:
    """Generic question with no structural anchor → no filter."""
    out = _build_default_filter().extract("Nguyên tắc chung là gì?")
    assert out == {}


def test_filter_returns_empty_for_empty_or_whitespace_input() -> None:
    f = _build_default_filter()
    assert f.extract("") == {}
    assert f.extract("   ") == {}
    assert f.extract("\n\t") == {}


def test_filter_does_not_match_partial_word() -> None:
    """'Điều khiển' (without trailing digit) must NOT match."""
    out = _build_default_filter().extract("Điều khiển từ xa hoạt động thế nào?")
    assert "article_no" not in out


def test_filter_first_occurrence_wins_per_key() -> None:
    """Comparison-style query: two articles mentioned → first one wins.

    Caller's job to detect comparison intent and run a different
    retrieval strategy; the filter just records the leading anchor.
    """
    out = _build_default_filter().extract("So sánh Điều 5 và Điều 7")
    assert out["article_no"] == "5"


# --------------------------------------------------------------------------- #
# ArticleAwareFilter — operator-supplied custom patterns                      #
# --------------------------------------------------------------------------- #


def test_filter_with_empty_pattern_list_returns_empty_dict() -> None:
    """No patterns → filter degrades to no-op silently."""
    f = ArticleAwareFilter(patterns=[])
    assert f.extract("Điều 3 quy định gì?") == {}


def test_filter_with_custom_english_patterns() -> None:
    """Non-VN bot owner replaces the pattern list wholesale — same flow."""
    custom = [
        {"name": "article", "regex": r"\bArticle\s+(\d+)\b", "flags": "IGNORECASE"},
        {"name": "section", "regex": r"\bSection\s+(\d+)\b", "flags": "IGNORECASE"},
    ]
    f = ArticleAwareFilter(patterns=custom)
    assert f.extract("What does Article 12 say?") == {"article_no": "12"}
    assert f.extract("section 4 covers what?") == {"section_no": "4"}
    # Generic English text with no anchor → empty.
    assert f.extract("hello, how does this work?") == {}


def test_filter_skips_malformed_pattern_entries() -> None:
    """Operator typo (missing name/regex) is logged but does not crash."""
    mixed = [
        {"name": "article", "regex": r"\bĐiều\s+(\d+)\b"},
        {"name": "broken"},  # missing regex — skip
        "not_a_dict",  # wrong type — skip
        {"regex": r"\bX\s+(\d+)"},  # missing name — skip
    ]
    f = ArticleAwareFilter(patterns=mixed)
    # The valid entry still works.
    assert f.extract("Điều 5") == {"article_no": "5"}


def test_filter_skips_pattern_with_compile_error() -> None:
    """Invalid regex (unclosed bracket) skipped; valid entries still apply."""
    patterns = [
        {"name": "broken", "regex": r"["},  # invalid regex
        {"name": "article", "regex": r"\bĐiều\s+(\d+)"},
    ]
    f = ArticleAwareFilter(patterns=patterns)
    assert f.extract("Điều 9") == {"article_no": "9"}


def test_filter_pattern_without_capture_group_uses_full_match() -> None:
    """Operator misconfig (no capture group) → emit full match, log warn."""
    patterns = [
        # No (...) → match.group(1) raises IndexError; fallback to group(0).
        {"name": "section", "regex": r"\bMục\s+\d+\b"},
    ]
    f = ArticleAwareFilter(patterns=patterns)
    out = f.extract("Mục 2 quy định")
    # Value is the full match upper-cased (non-digit value path).
    assert out.get("section_no") in ("MỤC 2", "Mục 2".upper())


def test_filter_returns_only_strings() -> None:
    """JSONB persistence requires string values — pin contract."""
    out = _build_default_filter().extract(
        "Chương III Điều 42 Khoản 5 Mục 2 Phụ lục B",
    )
    for value in out.values():
        assert isinstance(value, str), f"non-str value {value!r}"


# --------------------------------------------------------------------------- #
# Pattern flags — only IGNORECASE accepted                                    #
# --------------------------------------------------------------------------- #


def test_filter_unknown_flag_is_silently_dropped() -> None:
    """Operator typo in flags field doesn't disable the pattern."""
    patterns = [
        {"name": "article", "regex": r"\bĐiều\s+(\d+)", "flags": "BOGUS_FLAG"},
    ]
    f = ArticleAwareFilter(patterns=patterns)
    # Case-sensitive match still works on title case.
    assert f.extract("Điều 7") == {"article_no": "7"}


# --------------------------------------------------------------------------- #
# Domain-neutral guard                                                        #
# --------------------------------------------------------------------------- #


def test_default_patterns_are_string_dicts_with_required_fields() -> None:
    """Sanity-check that the operator default pattern list is well-formed."""
    for entry in DEFAULT_ARTICLE_REF_PATTERNS:
        assert isinstance(entry, dict)
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["regex"], str) and entry["regex"]


def test_test_fixtures_contain_no_vertical_literals() -> None:
    """Self-test that fixture strings have no industry / brand literal."""
    fixtures = (
        "Điều 3 quy định gì?"
        " Khoản 5 nói về điều gì?"
        " Chương II Điều 7 Khoản 1 quy định gì?"
        " So sánh Điều 5 và Điều 7"
        " What does Article 12 say?"
        " hello, how does this work?"
    ).lower()
    banned = ("spa", "massage", "chăm sóc da", "triệt lông", "gội đầu")
    for term in banned:
        assert term not in fixtures, (
            f"vertical literal '{term}' leaked into fixtures"
        )


# --------------------------------------------------------------------------- #
# SQL splitter — chunk-level vs document-level metadata keys                  #
# --------------------------------------------------------------------------- #


def test_chunk_level_keys_constant_includes_all_structural_anchors() -> None:
    expected = {
        "article_no",
        "clause_no",
        "section_no",
        "appendix_no",
        "chapter_no",
    }
    assert expected.issubset(CHUNK_LEVEL_METADATA_FILTER_KEYS)


def test_pgvector_doc_filter_splits_chunk_and_doc_keys() -> None:
    """Mixed-dict filter: chunk-keys go to outer JSONB containment,
    document-keys stay on the documents subquery."""
    from ragbot.infrastructure.vector.pgvector_store import PgVectorStore

    store = PgVectorStore.__new__(PgVectorStore)
    bot_id = uuid4()
    clause, params = store._doc_filter_sql(
        bot_id,
        metadata_filter={
            "article_no": "11",  # chunk-level
            "document_type": "policy",  # document-level
        },
    )
    # Both clauses must appear in the SQL fragment.
    assert "metadata_json @> CAST(:metadata_filter AS jsonb)" in clause
    assert "metadata_json @> CAST(:chunk_metadata_filter AS jsonb)" in clause
    # Both bind params must be present with correct JSON shapes.
    doc_bind = json.loads(params["metadata_filter"])
    chunk_bind = json.loads(params["chunk_metadata_filter"])
    assert doc_bind == {"document_type": "policy"}
    assert chunk_bind == {"article_no": "11"}


def test_pgvector_doc_filter_only_chunk_keys() -> None:
    """Filter dict with ONLY chunk-level keys still emits the outer
    containment clause; the documents subquery remains unfiltered by
    metadata (only the bot-scope predicate applies)."""
    from ragbot.infrastructure.vector.pgvector_store import PgVectorStore

    store = PgVectorStore.__new__(PgVectorStore)
    clause, params = store._doc_filter_sql(
        uuid4(),
        metadata_filter={"article_no": "3"},
    )
    assert "metadata_json @> CAST(:chunk_metadata_filter AS jsonb)" in clause
    # No documents-level metadata bind when no doc-keys present.
    assert "metadata_filter" not in params
    assert "chunk_metadata_filter" in params
    chunk_bind = json.loads(params["chunk_metadata_filter"])
    assert chunk_bind == {"article_no": "3"}


def test_pgvector_doc_filter_only_doc_keys_preserves_existing_shape() -> None:
    """Filter dict with no chunk-level keys must reproduce the pre-C2 wire
    shape exactly so existing callers stay untouched."""
    from ragbot.infrastructure.vector.pgvector_store import PgVectorStore

    store = PgVectorStore.__new__(PgVectorStore)
    clause, params = store._doc_filter_sql(
        uuid4(),
        metadata_filter={"document_type": "pricing"},
    )
    assert "metadata_json @> CAST(:metadata_filter AS jsonb)" in clause
    # No outer chunk containment when no chunk-keys present.
    assert "chunk_metadata_filter" not in params
    assert "metadata_filter" in params


def test_pgvector_doc_filter_empty_filter_skips_both_clauses() -> None:
    """Backward-compat: empty / None filter → only the bot-scope predicate."""
    from ragbot.infrastructure.vector.pgvector_store import PgVectorStore

    store = PgVectorStore.__new__(PgVectorStore)
    clause_none, params_none = store._doc_filter_sql(uuid4(), metadata_filter=None)
    clause_empty, params_empty = store._doc_filter_sql(uuid4(), metadata_filter={})
    # Neither containment clause should appear.
    assert "metadata_json @>" not in clause_none
    assert "metadata_json @>" not in clause_empty
    assert "metadata_filter" not in params_none
    assert "metadata_filter" not in params_empty
    assert "chunk_metadata_filter" not in params_none
    assert "chunk_metadata_filter" not in params_empty
