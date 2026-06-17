"""Auto-merge retrieval — collapse sibling child chunks into shared parent.

Pure functional helper. After the retrieve stage emits a top-K list of
child chunks, this module looks for groups whose ``parent_chunk_id`` is
identical and, when a group reaches ``sibling_threshold``, replaces the
siblings with a single chunk carrying the parent block's text.

Why this exists
---------------
Flat top-K retrieval fragments long documents: the answer to "what does
Article 7 say about delivery deadlines?" frequently splits across three
adjacent sentences indexed as three child chunks. Each child shows up in
the top-K, but the reranker scores them in isolation and the LLM sees
three short snippets glued by ``\\n\\n`` instead of the paragraph that
binds them. Merging the siblings into the parent block keeps the same
token budget envelope (one parent ≈ k children of the same paragraph)
while restoring coherence.

Citation
--------
Lu, Cao, Wang et al. "HiChunk: Hierarchical Chunking for Retrieval-
Augmented Generation", arXiv:2509.11552 (Tencent, 2025-09). The paper
reports +7pp evidence recall (81 % vs 74 %) on long-document QA
benchmarks when the retrieval pipeline collapses sibling matches into
their shared parent block at retrieval time. LlamaIndex implements the
same primitive under the name ``AutoMergingRetriever`` (PR #6912, 2023);
this module is the platform-neutral, port-driven equivalent.

Design boundary
---------------
This module is **pure** — it never opens a DB session. Callers that
need the parent block's text supply ``parent_content_map`` (a mapping
from parent_chunk_id to {content, metadata}). The orchestration node
already fetches that map in the existing ``parent_child_enabled`` block
(``query_graph.py`` near line 3017); the same map is reused so we avoid
a second round-trip. When a parent's content is missing from the map,
the group is left untouched (graceful degrade — the children stay in
the result, no fabricated text is injected).

Distinct from neighbouring stages
---------------------------------
- ``parent_child_enabled`` swaps EVERY child for its parent regardless
  of how many siblings hit. Risk: bloats context even when only one
  child matched.
- ``ParentExpandStage4Retriever`` APPENDS parents alongside children
  for the reranker to choose. Doubles the pool size.
- Auto-merge here **conditionally collapses** only when the retriever
  already concentrated several hits on one parent — that concentration
  itself is the signal the wider block matters.

Telemetry contract
------------------
The function returns a ``AutoMergeResult`` named tuple with the merged
chunk list plus a stats payload the caller threads into the structlog
event ``auto_merge_retrieval`` (siblings_merged_count, parents_emitted,
groups_below_threshold). The orchestration layer is the one that
actually emits the event — keeps shared/ side-effect free for tests.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ragbot.shared.constants import (
    DEFAULT_AUTO_MERGE_MAX_PARENTS,
    DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD,
)


class AutoMergeStats(NamedTuple):
    """Telemetry payload returned alongside the merged chunk list.

    All counts are zero when auto-merge is a no-op (empty input, flag
    off, no qualifying group). The orchestration node folds these into
    a single ``structlog`` event so dashboards can answer "how often
    did auto-merge actually fire?" without recomputing client-side.
    """

    input_count: int
    output_count: int
    siblings_merged_count: int  # total child chunks collapsed into parents
    parents_emitted: int  # distinct parent blocks that replaced groups
    groups_below_threshold: int  # parent-id groups that did NOT qualify
    parents_skipped_no_content: int  # qualifying groups left intact (no map entry)


class AutoMergeResult(NamedTuple):
    """Merged chunk list plus per-call stats for telemetry."""

    chunks: list[dict[str, Any]]
    stats: AutoMergeStats


def _parent_id(chunk: dict[str, Any]) -> str | None:
    """Extract the parent_chunk_id from *chunk* as a stable string key.

    Returns ``None`` when the field is absent or empty. The string cast
    insulates the caller from upstream UUID-vs-str heterogeneity (some
    code paths set ``parent_chunk_id`` as ``uuid.UUID``, others as a raw
    string from a SQL row mapping).
    """
    pid = chunk.get("parent_chunk_id")
    if pid is None or pid == "":
        return None
    return str(pid)


def auto_merge_retrieve(
    chunks: list[dict[str, Any]],
    *,
    parent_content_map: dict[str, dict[str, Any]] | None = None,
    sibling_threshold: int = DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD,
    max_parents: int = DEFAULT_AUTO_MERGE_MAX_PARENTS,
) -> AutoMergeResult:
    """Collapse sibling child chunks into shared parent when threshold met.

    @param chunks: ordered top-K children from the retriever. Each item
        is a plain ``dict`` carrying at minimum ``content`` / ``text``
        and optionally ``parent_chunk_id``, ``score``, ``metadata``.
        The original list is **not mutated** — a new list is returned.
    @param parent_content_map: mapping ``str(parent_chunk_id) -> dict``
        where the value supplies ``content`` (required) plus any
        metadata to surface on the emitted parent chunk. When ``None``
        or missing the relevant parent, the group is left intact so the
        caller never sees fabricated text (HALLU=0 guarantee).
    @param sibling_threshold: minimum count of child chunks sharing a
        parent_chunk_id before that group collapses into the parent.
        Threshold ``2`` is the HiChunk paper default; tighter values
        (3+) reduce false-positive merges on noisy corpora.
    @param max_parents: maximum number of parent blocks the function may
        emit in one call. ``0`` (or any value ≤ 0) means unbounded.
        Defensive bound — without it a uniformly-distributed query
        could in theory replace all K children with K parents and bloat
        downstream context.
    @return: ``AutoMergeResult`` with the merged list (order: parents
        appear in the position of their first sibling, non-merged
        children keep their original position; result preserves
        retrieval rank as closely as possible) plus stats for
        telemetry.

    Edge cases:
    - Empty ``chunks`` → empty result, all stats zero.
    - ``sibling_threshold`` ≤ 1 → falls back to threshold ``2`` (we
      refuse to collapse single chunks; that's the ``parent_child``
      pattern's job, not auto-merge's).
    - No chunk carries ``parent_chunk_id`` → result is the original
      list, untouched.
    - One parent qualifies but no ``parent_content_map`` entry → kids
      stay in result, ``parents_skipped_no_content`` increments by 1.
    """
    if not chunks:
        return AutoMergeResult(
            chunks=[],
            stats=AutoMergeStats(0, 0, 0, 0, 0, 0),
        )

    # Refuse degenerate threshold (1 = always merge = parent_child semantics).
    # Floor at ``DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD`` so the contract
    # "auto-merge requires SIBLINGS" is enforced regardless of caller input.
    effective_threshold = max(DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD, int(sibling_threshold))

    # Group child indices by parent_chunk_id, preserving first-seen order.
    groups: dict[str, list[int]] = {}
    group_order: list[str] = []
    for idx, chunk in enumerate(chunks):
        pid = _parent_id(chunk)
        if pid is None:
            continue
        if pid not in groups:
            groups[pid] = []
            group_order.append(pid)
        groups[pid].append(idx)

    # Identify qualifying parent groups; track non-qualifying for stats.
    qualifying: list[str] = []
    groups_below = 0
    for pid in group_order:
        if len(groups[pid]) >= effective_threshold:
            qualifying.append(pid)
        else:
            groups_below += 1

    # Apply max_parents cap. ``max_parents <= 0`` → unbounded.
    if max_parents and max_parents > 0:
        capped_qualifying = qualifying[: int(max_parents)]
    else:
        capped_qualifying = qualifying

    # Resolve which qualifying parents actually have content available.
    qualifying_with_content: set[str] = set()
    parents_skipped_no_content = 0
    pmap = parent_content_map or {}
    for pid in capped_qualifying:
        parent_row = pmap.get(pid)
        if parent_row and parent_row.get("content"):
            qualifying_with_content.add(pid)
        else:
            parents_skipped_no_content += 1

    # Fast path: nothing actually merges → return original list verbatim.
    if not qualifying_with_content:
        return AutoMergeResult(
            chunks=list(chunks),
            stats=AutoMergeStats(
                input_count=len(chunks),
                output_count=len(chunks),
                siblings_merged_count=0,
                parents_emitted=0,
                groups_below_threshold=groups_below,
                parents_skipped_no_content=parents_skipped_no_content,
            ),
        )

    # Build the merged output. Each qualifying parent emits at the
    # position of its **first** retrieved child; subsequent siblings
    # are dropped. Non-qualifying children pass through unchanged.
    merged: list[dict[str, Any]] = []
    siblings_merged_count = 0
    parents_emitted = 0
    emitted_parents: set[str] = set()

    for idx, chunk in enumerate(chunks):
        pid = _parent_id(chunk)
        if pid is not None and pid in qualifying_with_content:
            if pid in emitted_parents:
                # Sibling that already contributed to its parent block; drop.
                siblings_merged_count += 1
                continue
            # First-seen sibling for this parent: emit parent in its slot.
            parent_row = pmap[pid]
            sibling_indices = groups[pid]
            sibling_scores = [
                float(chunks[i].get("score", 0) or 0) for i in sibling_indices
            ]
            # Promote the strongest sibling's score so the merged parent
            # doesn't lose retrieval rank against non-merged children.
            promoted_score = max(sibling_scores) if sibling_scores else 0.0
            merged.append({
                "chunk_id": pid,
                "parent_chunk_id": None,  # the merged chunk IS the parent now
                "content": parent_row["content"],
                "text": parent_row.get("text", parent_row["content"]),
                "score": promoted_score,
                "metadata": dict(parent_row.get("metadata") or parent_row.get("metadata_json") or {}),
                "document_id": parent_row.get("document_id")
                    or chunk.get("document_id")
                    or chunk.get("record_document_id"),
                "is_auto_merged": True,
                "auto_merge_sibling_count": len(sibling_indices),
            })
            emitted_parents.add(pid)
            parents_emitted += 1
            siblings_merged_count += 1  # this sibling itself collapsed too
        else:
            merged.append(chunk)

    return AutoMergeResult(
        chunks=merged,
        stats=AutoMergeStats(
            input_count=len(chunks),
            output_count=len(merged),
            siblings_merged_count=siblings_merged_count,
            parents_emitted=parents_emitted,
            groups_below_threshold=groups_below,
            parents_skipped_no_content=parents_skipped_no_content,
        ),
    )


__all__ = [
    "AutoMergeResult",
    "AutoMergeStats",
    "auto_merge_retrieve",
]
