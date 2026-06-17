"""Unit tests for auto-merge retrieval (HiChunk Tencent pattern).

Covers:
- Below-threshold sibling group → no merge (preserves precision).
- At-threshold sibling group with parent content → merge into parent.
- Above-threshold sibling group → still single parent emitted.
- Two qualifying parents in one batch → both merged independently.
- ``max_parents`` cap → only first-N parents collapsed.
- Missing ``parent_content_map`` entry → graceful degrade (no fabricated text).
- No ``parent_chunk_id`` anywhere → list passes through verbatim.
- Empty input → empty result.
- Degenerate ``sibling_threshold=1`` clamped to 2.
- Stats fields populated correctly across paths.
- Order preserved: parents emit in slot of their first sibling.
- Score promotion: merged parent inherits max sibling score.
- Original input list not mutated.
"""

from __future__ import annotations

import pytest

from ragbot.shared.auto_merge_retrieval import (
    AutoMergeResult,
    AutoMergeStats,
    auto_merge_retrieve,
)
from ragbot.shared.constants import (
    DEFAULT_AUTO_MERGE_MAX_PARENTS,
    DEFAULT_AUTO_MERGE_RETRIEVAL_ENABLED,
    DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD,
)


def _child(idx: int, parent_id: str | None, score: float = 0.5, content: str | None = None) -> dict:
    return {
        "chunk_id": f"child-{idx}",
        "parent_chunk_id": parent_id,
        "content": content or f"child {idx} text",
        "text": content or f"child {idx} text",
        "score": score,
        "document_id": "doc-1",
    }


class TestConstants:
    def test_default_flag_is_off(self):
        assert DEFAULT_AUTO_MERGE_RETRIEVAL_ENABLED is False

    def test_default_threshold_is_two(self):
        assert DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD == 2

    def test_default_max_parents_positive(self):
        assert DEFAULT_AUTO_MERGE_MAX_PARENTS >= 1


class TestEdgeCases:
    def test_empty_input_returns_empty_result(self):
        result = auto_merge_retrieve([])
        assert result.chunks == []
        assert result.stats.input_count == 0
        assert result.stats.output_count == 0
        assert result.stats.siblings_merged_count == 0
        assert result.stats.parents_emitted == 0

    def test_no_parent_links_passes_through(self):
        chunks = [_child(1, None), _child(2, None), _child(3, None)]
        result = auto_merge_retrieve(chunks, parent_content_map={})
        assert result.chunks == chunks
        assert result.stats.parents_emitted == 0
        assert result.stats.siblings_merged_count == 0
        assert result.stats.output_count == 3

    def test_single_chunk_with_parent_below_threshold(self):
        chunks = [_child(1, "parent-A")]
        pmap = {"parent-A": {"content": "full paragraph A"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap)
        # 1 sibling < threshold 2 → no merge
        assert result.chunks == chunks
        assert result.stats.parents_emitted == 0
        assert result.stats.groups_below_threshold == 1


class TestMergeBehaviour:
    def test_two_siblings_collapse_to_parent(self):
        chunks = [
            _child(1, "parent-A", score=0.9),
            _child(2, "parent-A", score=0.7),
            _child(3, "parent-B", score=0.4),  # no sibling for B
        ]
        pmap = {
            "parent-A": {"content": "parent A full block", "metadata": {"section": "7"}},
        }
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        # Parent A replaces both children; parent B child stays.
        assert len(result.chunks) == 2
        assert result.chunks[0]["chunk_id"] == "parent-A"
        assert result.chunks[0]["content"] == "parent A full block"
        assert result.chunks[0]["is_auto_merged"] is True
        assert result.chunks[0]["auto_merge_sibling_count"] == 2
        # Score promoted to max sibling (0.9) so rerank doesn't demote.
        assert result.chunks[0]["score"] == 0.9
        # Metadata threaded through.
        assert result.chunks[0]["metadata"] == {"section": "7"}
        # Parent_chunk_id stripped from merged chunk (it IS the parent now).
        assert result.chunks[0]["parent_chunk_id"] is None
        # Second slot: parent-B child, untouched.
        assert result.chunks[1]["chunk_id"] == "child-3"
        # Stats.
        assert result.stats.parents_emitted == 1
        assert result.stats.siblings_merged_count == 2
        assert result.stats.input_count == 3
        assert result.stats.output_count == 2

    def test_three_siblings_still_one_parent(self):
        chunks = [_child(i, "parent-X", score=0.5 + 0.1 * i) for i in range(1, 4)]
        pmap = {"parent-X": {"content": "X block"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        assert len(result.chunks) == 1
        assert result.chunks[0]["chunk_id"] == "parent-X"
        assert result.chunks[0]["auto_merge_sibling_count"] == 3
        # Max sibling score = 0.5 + 0.1*3 = 0.8
        assert result.chunks[0]["score"] == pytest.approx(0.8)
        assert result.stats.siblings_merged_count == 3
        assert result.stats.parents_emitted == 1

    def test_two_qualifying_parents_both_merge(self):
        chunks = [
            _child(1, "parent-A", score=0.9),
            _child(2, "parent-B", score=0.85),
            _child(3, "parent-A", score=0.8),
            _child(4, "parent-B", score=0.7),
        ]
        pmap = {
            "parent-A": {"content": "A block"},
            "parent-B": {"content": "B block"},
        }
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        # Two parents, no dangling siblings.
        assert len(result.chunks) == 2
        ids = [c["chunk_id"] for c in result.chunks]
        assert ids == ["parent-A", "parent-B"]  # order: first-sibling slot
        assert result.stats.parents_emitted == 2
        assert result.stats.siblings_merged_count == 4

    def test_threshold_three_rejects_pair(self):
        chunks = [
            _child(1, "parent-A"),
            _child(2, "parent-A"),
        ]
        pmap = {"parent-A": {"content": "A block"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=3)
        # 2 siblings < threshold 3 → no merge
        assert len(result.chunks) == 2
        assert result.stats.parents_emitted == 0
        assert result.stats.groups_below_threshold == 1

    def test_below_threshold_group_counted_in_stats(self):
        chunks = [
            _child(1, "parent-A"),  # only one sibling for A
            _child(2, "parent-B"),
            _child(3, "parent-B"),  # two for B — qualifies
        ]
        pmap = {
            "parent-A": {"content": "A"},
            "parent-B": {"content": "B"},
        }
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        assert result.stats.groups_below_threshold == 1  # parent-A
        assert result.stats.parents_emitted == 1  # parent-B


class TestGracefulDegrade:
    def test_missing_parent_content_keeps_children(self):
        chunks = [
            _child(1, "parent-A"),
            _child(2, "parent-A"),
        ]
        # Empty map — parent-A qualifies but has no content available.
        result = auto_merge_retrieve(chunks, parent_content_map={}, sibling_threshold=2)
        # Graceful degrade: children stay; no fabricated parent text.
        assert len(result.chunks) == 2
        assert result.chunks[0]["chunk_id"] == "child-1"
        assert result.chunks[1]["chunk_id"] == "child-2"
        assert result.stats.parents_emitted == 0
        assert result.stats.parents_skipped_no_content == 1

    def test_none_parent_content_map_does_not_crash(self):
        chunks = [_child(1, "parent-A"), _child(2, "parent-A")]
        # Caller forgot to supply the map — no merge but no exception.
        result = auto_merge_retrieve(chunks, parent_content_map=None, sibling_threshold=2)
        assert len(result.chunks) == 2
        assert result.stats.parents_emitted == 0
        assert result.stats.parents_skipped_no_content == 1

    def test_partial_parent_content_only_merges_available(self):
        chunks = [
            _child(1, "parent-A"),
            _child(2, "parent-A"),
            _child(3, "parent-B"),
            _child(4, "parent-B"),
        ]
        pmap = {"parent-A": {"content": "A block"}}  # parent-B missing
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        # A merges, B stays.
        assert len(result.chunks) == 3  # parent-A + child-3 + child-4
        assert result.chunks[0]["chunk_id"] == "parent-A"
        assert {c["chunk_id"] for c in result.chunks[1:]} == {"child-3", "child-4"}
        assert result.stats.parents_emitted == 1
        assert result.stats.parents_skipped_no_content == 1
        assert result.stats.siblings_merged_count == 2


class TestMaxParentsCap:
    def test_cap_limits_emitted_parents(self):
        chunks = [
            _child(1, "parent-A"),
            _child(2, "parent-A"),
            _child(3, "parent-B"),
            _child(4, "parent-B"),
            _child(5, "parent-C"),
            _child(6, "parent-C"),
        ]
        pmap = {
            "parent-A": {"content": "A"},
            "parent-B": {"content": "B"},
            "parent-C": {"content": "C"},
        }
        result = auto_merge_retrieve(
            chunks, parent_content_map=pmap, sibling_threshold=2, max_parents=2
        )
        # Only first 2 parents (A, B) merge; C's children stay intact.
        assert result.stats.parents_emitted == 2
        c_chunks = [c for c in result.chunks if c.get("chunk_id", "").startswith("child-")]
        # parent-C children survive as plain children.
        assert {c["chunk_id"] for c in c_chunks} == {"child-5", "child-6"}

    def test_max_parents_zero_unbounded(self):
        chunks = [
            _child(1, "parent-A"),
            _child(2, "parent-A"),
            _child(3, "parent-B"),
            _child(4, "parent-B"),
        ]
        pmap = {
            "parent-A": {"content": "A"},
            "parent-B": {"content": "B"},
        }
        result = auto_merge_retrieve(
            chunks, parent_content_map=pmap, sibling_threshold=2, max_parents=0
        )
        assert result.stats.parents_emitted == 2


class TestDegenerateThreshold:
    def test_threshold_one_clamped_to_two(self):
        # threshold=1 would mean "merge any chunk with a parent_id" =
        # the parent_child swap pattern, NOT auto-merge. We refuse.
        chunks = [_child(1, "parent-A")]
        pmap = {"parent-A": {"content": "A"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=1)
        # Threshold clamped to 2 → single child does not qualify.
        assert result.chunks == chunks
        assert result.stats.parents_emitted == 0

    def test_threshold_zero_clamped_to_two(self):
        chunks = [_child(1, "parent-A")]
        pmap = {"parent-A": {"content": "A"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=0)
        assert result.chunks == chunks
        assert result.stats.parents_emitted == 0


class TestPurity:
    def test_input_chunks_not_mutated(self):
        chunks = [
            _child(1, "parent-A", score=0.9),
            _child(2, "parent-A", score=0.7),
        ]
        snapshot = [dict(c) for c in chunks]
        pmap = {"parent-A": {"content": "A block"}}
        auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        # Original list and dicts untouched.
        assert chunks == snapshot

    def test_order_preserved_parent_in_first_sibling_slot(self):
        chunks = [
            _child(1, None, score=0.95),  # standalone, no parent
            _child(2, "parent-A", score=0.9),  # first A sibling
            _child(3, None, score=0.85),
            _child(4, "parent-A", score=0.6),  # second A sibling — collapses
        ]
        pmap = {"parent-A": {"content": "A block"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        ids = [c.get("chunk_id") for c in result.chunks]
        # parent-A occupies slot of child-2 (first sibling); child-4 dropped.
        assert ids == ["child-1", "parent-A", "child-3"]


class TestUuidLikeParentId:
    def test_uuid_objects_normalised_to_string(self):
        import uuid

        pid_uuid = uuid.uuid4()
        chunks = [
            _child(1, pid_uuid),
            _child(2, pid_uuid),
        ]
        pmap = {str(pid_uuid): {"content": "from uuid parent"}}
        result = auto_merge_retrieve(chunks, parent_content_map=pmap, sibling_threshold=2)
        assert result.stats.parents_emitted == 1
        assert result.chunks[0]["chunk_id"] == str(pid_uuid)
        assert result.chunks[0]["content"] == "from uuid parent"


class TestNamedTupleContract:
    def test_result_is_named_tuple(self):
        result = auto_merge_retrieve([])
        assert isinstance(result, AutoMergeResult)
        assert isinstance(result.stats, AutoMergeStats)
        # Stats fields enumerable for telemetry emission.
        assert set(result.stats._fields) == {
            "input_count",
            "output_count",
            "siblings_merged_count",
            "parents_emitted",
            "groups_below_threshold",
            "parents_skipped_no_content",
        }
