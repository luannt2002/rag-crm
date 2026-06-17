"""Retrieval fallback registry tests.

Coverage:
- All 5 stage keys are registered.
- ``build_retrieval_fallback`` falls back to NullRetrievalStage on unknown
  / empty / None inputs.
- ``list_stages()`` is sorted and stable.
- Per-class stage_name matches the registry key (no silent mislabel).
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.retrieval_fallback import (
    BM25OnlyStage2Retriever,
    HybridStage1Retriever,
    KeywordStage3Retriever,
    NullRetrievalStage,
    ParentExpandStage4Retriever,
    build_retrieval_fallback,
    list_stages,
)


def test_registry_lists_all_five_stages() -> None:
    stages = list_stages()
    assert set(stages) == {
        "hybrid_stage1",
        "bm25_only_stage2",
        "keyword_stage3",
        "parent_expand_stage4",
        "null",
    }
    # Sorted output for stable test asserts.
    assert stages == sorted(stages)


@pytest.mark.parametrize(
    "key,expected_cls",
    [
        ("hybrid_stage1", HybridStage1Retriever),
        ("bm25_only_stage2", BM25OnlyStage2Retriever),
        ("keyword_stage3", KeywordStage3Retriever),
        ("parent_expand_stage4", ParentExpandStage4Retriever),
        ("null", NullRetrievalStage),
    ],
)
def test_registry_resolves_each_key(key: str, expected_cls: type) -> None:
    inst = build_retrieval_fallback(key)
    assert isinstance(inst, expected_cls)


@pytest.mark.parametrize(
    "bad_key",
    ["", None, "unknown-strategy", "HYBRID_STAGE_9000"],
)
def test_registry_unknown_falls_back_to_null(bad_key) -> None:
    inst = build_retrieval_fallback(bad_key)
    assert isinstance(inst, NullRetrievalStage)


def test_registry_key_normalization_is_case_insensitive() -> None:
    # Operators may type the key with surrounding whitespace / different case.
    inst_upper = build_retrieval_fallback("  HYBRID_STAGE1  ")
    assert isinstance(inst_upper, HybridStage1Retriever)


def test_each_stage_name_matches_registry_key() -> None:
    # Drift guard: if someone renames stage_name without touching registry,
    # this catches it.
    expected = {
        "hybrid_stage1": "hybrid_stage1",
        "bm25_only_stage2": "bm25_only_stage2",
        "keyword_stage3": "keyword_stage3",
        "parent_expand_stage4": "parent_expand_stage4",
        "null": "null",
    }
    for key, stage_name in expected.items():
        inst = build_retrieval_fallback(key)
        assert inst.stage_name == stage_name
