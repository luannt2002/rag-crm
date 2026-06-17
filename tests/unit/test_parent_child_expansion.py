"""Tests for P15-5 parent-child chunk expansion (small-to-big retrieval)."""

from __future__ import annotations

from ragbot.orchestration.query_graph import expand_parent_chunks


def _parent(content: str, *, text: str | None = None) -> dict:
    return {"content": content, "text": text if text is not None else content}


class TestExpandParentChunks:
    def test_no_parents_pass_through(self):
        chunks = [
            {"chunk_id": "c1", "content": "a"},
            {"chunk_id": "c2", "content": "b"},
        ]
        result = expand_parent_chunks(chunks, parent_map={})
        assert result == chunks

    def test_single_child_swapped_for_parent(self):
        chunks = [{"chunk_id": "c1", "content": "child A", "parent_chunk_id": "p1"}]
        result = expand_parent_chunks(chunks, parent_map={"p1": _parent("PARENT A")})
        assert len(result) == 1
        assert result[0]["content"] == "PARENT A"
        assert result[0]["text"] == "PARENT A"
        assert result[0]["chunk_id"] == "p1"
        assert result[0]["is_parent_expanded"] is True

    def test_dedup_same_parent_twice(self):
        # Two children of the same parent → only one parent emitted
        chunks = [
            {"chunk_id": "c1", "content": "child A", "parent_chunk_id": "p1", "score": 0.9},
            {"chunk_id": "c2", "content": "child B", "parent_chunk_id": "p1", "score": 0.8},
        ]
        result = expand_parent_chunks(chunks, parent_map={"p1": _parent("PARENT")})
        assert len(result) == 1
        assert result[0]["chunk_id"] == "p1"
        # Preserves metadata from the first occurrence (highest-scored child)
        assert result[0]["score"] == 0.9

    def test_missing_parent_falls_back_to_child(self):
        # parent_chunk_id points to an id not in the map → keep original child
        chunks = [{"chunk_id": "c1", "content": "child", "parent_chunk_id": "p-missing"}]
        result = expand_parent_chunks(chunks, parent_map={})
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c1"
        assert result[0]["content"] == "child"
        assert "is_parent_expanded" not in result[0]

    def test_preserves_order(self):
        chunks = [
            {"chunk_id": "c1", "content": "alpha"},
            {"chunk_id": "c2", "content": "bravo child", "parent_chunk_id": "p2"},
            {"chunk_id": "c3", "content": "charlie"},
        ]
        result = expand_parent_chunks(chunks, parent_map={"p2": _parent("BRAVO PARENT")})
        assert [c["content"] for c in result] == ["alpha", "BRAVO PARENT", "charlie"]

    def test_uuid_parent_ids_coerced_to_str(self):
        # Real retrieval rows can have UUID objects; map keyed by string form
        import uuid
        pid = uuid.uuid4()
        chunks = [{"chunk_id": "c1", "content": "child", "parent_chunk_id": pid}]
        result = expand_parent_chunks(chunks, parent_map={str(pid): _parent("PARENT")})
        assert result[0]["chunk_id"] == str(pid)
        assert result[0]["content"] == "PARENT"

    def test_mixed_parents_and_orphans(self):
        chunks = [
            {"chunk_id": "c1", "content": "x1", "parent_chunk_id": "p1"},
            {"chunk_id": "c2", "content": "x2"},  # no parent
            {"chunk_id": "c3", "content": "x3", "parent_chunk_id": "p1"},  # dup parent
            {"chunk_id": "c4", "content": "x4", "parent_chunk_id": "p4"},
        ]
        parent_map = {"p1": _parent("P1"), "p4": _parent("P4")}
        result = expand_parent_chunks(chunks, parent_map)
        contents = [c["content"] for c in result]
        # Expected: P1 (from c1), x2 (orphan), P4 (from c4). c3 is deduped.
        assert contents == ["P1", "x2", "P4"]

    def test_uses_parent_text_when_different(self):
        chunks = [{"chunk_id": "c1", "content": "child", "parent_chunk_id": "p1"}]
        parent_map = {"p1": {"content": "PARENT_CONTENT", "text": "PARENT_TEXT"}}
        result = expand_parent_chunks(chunks, parent_map)
        assert result[0]["content"] == "PARENT_CONTENT"
        assert result[0]["text"] == "PARENT_TEXT"
