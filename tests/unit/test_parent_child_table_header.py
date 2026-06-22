"""M0-7 — parent-child must not row-split a TABLE parent without its header.

Bug: :func:`generate_parent_child_chunks` splits oversized parents into
children with a plain ``RecursiveCharacterTextSplitter``. For a TABLE-type
parent that cuts the row stream and strands the header on the first child
only — every subsequent child is a header-less orphan row (``| 5 | … |``
with no ``| STT | Dịch vụ | Giá |`` context). The table-aware path
(:func:`_chunk_recursive_with_tables`) re-prepends the header per row-group,
so a table parent must flow through it.

These tests pin the post-fix contract: each child of a table parent carries
the column-header row.
"""
from __future__ import annotations

from ragbot.shared.chunking import generate_parent_child_chunks

# A markdown pipe-table whose body, once parented whole, exceeds child_size
# (256) → forces the oversized-parent split path. ASCII-only on purpose so the
# assertion is byte-stable across locales.
_HEADER = "| STT | Dich vu | Gia |"
_SEP = "|-----|---------|-----|"
_ROWS = [
    f"| {i} | Dong so {i} mo ta dai hon mot chut nua | {i * 1000} |"
    for i in range(1, 40)
]
_TABLE = "\n".join([_HEADER, _SEP] + _ROWS)


def _children(result: list[dict]) -> list[dict]:
    return [r for r in result if not r["is_parent"]]


def test_table_parent_children_all_carry_header() -> None:
    # parent_size large enough to keep the whole table in ONE parent so the
    # oversized-parent → child-split branch (len(parent) > child_size) runs.
    result = generate_parent_child_chunks(
        _TABLE, parent_size=4096, child_size=256, child_overlap=50,
    )
    children = _children(result)
    # Sanity: the table really did split into multiple children.
    assert len(children) > 1, "fixture must force a multi-child split"
    for idx, child in enumerate(children):
        first_line = child["content"].splitlines()[0]
        assert first_line.strip() == _HEADER, (
            f"child {idx} lost the header row — first line was {first_line!r}"
        )


def test_table_parent_no_orphan_data_row_as_first_line() -> None:
    # No child may begin with a bare data row (header stranded upstream).
    result = generate_parent_child_chunks(
        _TABLE, parent_size=4096, child_size=256, child_overlap=50,
    )
    for child in _children(result):
        first_line = child["content"].splitlines()[0].strip()
        assert "Dong so" not in first_line, (
            f"orphan data row leaked as first line: {first_line!r}"
        )


def test_non_table_parent_still_row_splits_normally() -> None:
    # Guard: prose parents keep their existing recursive child-split behaviour
    # (the fix must be table-scoped, not a blanket change).
    prose = ". ".join(
        f"Cau van so {i} day la mot doan van xuoi binh thuong" for i in range(1, 60)
    ) + "."
    result = generate_parent_child_chunks(
        prose, parent_size=4096, child_size=256, child_overlap=50,
    )
    children = _children(result)
    assert len(children) > 1
    # Prose children are non-empty text shards — no table header expectation.
    assert all(c["content"].strip() for c in children)
