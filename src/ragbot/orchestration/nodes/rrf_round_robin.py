"""Entity-quota-aware Reciprocal Rank Fusion (round-robin fairness layer).

Problem this solves
-------------------
Plain RRF (Cormack et al. 2009, ``score(d) = Σ_q 1/(k + rank_q(d))``) ranks
purely by fused score. When one *entity* dominates the candidate pool — e.g. a
comparison question where one of the two compared things has far more matching
chunks — the minority entity's chunks can be pushed below the ``top_k`` cut
even though the user explicitly asked to compare the two. The downstream
context window then contains only the majority entity, and the answer silently
drops half the comparison.

This module adds a fairness layer on top of plain RRF: before the global RRF
ranking fills the result, each distinct entity is guaranteed at least
``per_entity_quota`` slots (its own top-scored chunks). The remaining capacity
is filled by global RRF order over everything not already taken.

Design properties
-----------------
* **Pure function** — no I/O, no global state, no DB. Trivially unit-testable
  and safe to wire into the retrieve node later (S2 owns ``query_graph.py``).
* **Zero-hardcode** — ``k`` and ``per_entity_quota`` are required keyword
  params; no magic numbers live here.
* **Domain-neutral** — the notion of "entity" is supplied by the caller as a
  callable ``entity_of(chunk) -> Hashable``. This module never inspects chunk
  content for any brand / domain term. A chunk whose entity key is ``None`` is
  treated as belonging to no entity (eligible only for the global fill phase).
* **Degrades to plain RRF** — when entities are balanced (or there is a single
  entity, or quota is 0), the output ordering is exactly plain-RRF order, so
  enabling this layer is a no-op for the balanced/common case.

The fused RRF score itself is identical to
``application.services.multi_query_expansion.rrf_merge_chunks`` so the two stay
interchangeable; only the *final ordering* changes when a minority entity would
otherwise be starved.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence

# Chunk payloads are plain dicts throughout the retrieve pipeline; the id may
# live under either key (mirrors rrf_merge_chunks' fallback).
Chunk = dict
_PRIMARY_ID_KEY = "chunk_id"
_FALLBACK_ID_KEY = "id"


def _chunk_id(chunk: Chunk, chunk_id_key: str) -> str:
    """Stable string identity for a chunk across ranked lists."""
    return str(chunk.get(chunk_id_key) or chunk.get(_FALLBACK_ID_KEY) or "")


def _fuse_rrf_scores(
    ranked_lists: Sequence[Sequence[Chunk]],
    *,
    k: int,
    chunk_id_key: str,
) -> tuple[dict[str, float], dict[str, Chunk], list[str]]:
    """Compute RRF scores and return (scores, chunks_by_id, global_order).

    ``global_order`` is the list of chunk ids sorted by descending RRF score —
    i.e. exactly what plain RRF would emit. Ties break on first-seen order so
    the function is deterministic.
    """
    scores: dict[str, float] = {}
    chunks_by_id: dict[str, Chunk] = {}
    first_seen: dict[str, int] = {}
    seq = 0
    for results in ranked_lists:
        for rank, chunk in enumerate(results):
            cid = _chunk_id(chunk, chunk_id_key)
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in chunks_by_id:
                chunks_by_id[cid] = dict(chunk)
                first_seen[cid] = seq
                seq += 1

    global_order = sorted(
        chunks_by_id,
        key=lambda cid: (-scores[cid], first_seen[cid]),
    )
    return scores, chunks_by_id, global_order


def rrf_round_robin(
    ranked_lists: Sequence[Sequence[Chunk]],
    *,
    k: int,
    per_entity_quota: int,
    entity_of: Callable[[Chunk], Hashable],
    top_k: int | None = None,
    chunk_id_key: str = _PRIMARY_ID_KEY,
) -> list[Chunk]:
    """Fuse ranked lists with RRF, guaranteeing minority entities survive.

    Each distinct entity (per ``entity_of``) is first granted up to
    ``per_entity_quota`` of *its own* highest-RRF chunks. Remaining capacity up
    to ``top_k`` is then filled in global RRF order over whatever is left.

    Args:
        ranked_lists: One ranked list of chunk dicts per query/source. Rank 0 =
            best within each list. Empty inner lists contribute nothing.
        k: RRF penalty constant (Cormack canonical 60). Caller-supplied; no
            default here to keep the algorithm zero-hardcode.
        per_entity_quota: Minimum slots guaranteed to each entity before the
            global fill phase. ``0`` disables the fairness layer → plain RRF.
        entity_of: Callable mapping a chunk to its entity key. Return ``None``
            for "no entity" (excluded from quota grants, still eligible for
            the global fill). Caller owns all domain knowledge here.
        top_k: Optional cap on the number of chunks returned. ``None`` returns
            every fused chunk (quota grants never exceed the pool anyway).
        chunk_id_key: Field identifying identical chunks across lists.

    Returns:
        Chunks ordered for the context window. Each returned chunk's ``score``
        is overwritten with its fused RRF score (matching ``rrf_merge_chunks``).

    Notes:
        * Single surviving list → returned unchanged (bit-exact single-query
          flow), mirroring ``rrf_merge_chunks``.
        * Balanced entities / single entity / ``per_entity_quota == 0`` →
          output equals plain RRF order (degradation guarantee).
    """
    non_empty = [list(lst) for lst in ranked_lists if lst]
    if not non_empty:
        return []
    if len(non_empty) == 1:
        # Identity-preserving single-list fast path (no score rewrite).
        return list(non_empty[0])

    scores, chunks_by_id, global_order = _fuse_rrf_scores(
        non_empty, k=k, chunk_id_key=chunk_id_key
    )

    def _finalize(ordered_ids: list[str]) -> list[Chunk]:
        out: list[Chunk] = []
        for cid in ordered_ids:
            chunk = chunks_by_id[cid]
            chunk["score"] = scores.get(cid, 0.0)
            out.append(chunk)
        return out if top_k is None else out[:top_k]

    # Fairness disabled → behave exactly like plain RRF.
    if per_entity_quota <= 0:
        return _finalize(global_order)

    # Group chunk ids by entity, preserving global RRF order within each group
    # so a grant always hands out the entity's strongest chunks first.
    entity_to_ids: dict[Hashable, list[str]] = {}
    for cid in global_order:
        ent = entity_of(chunks_by_id[cid])
        if ent is None:
            continue
        entity_to_ids.setdefault(ent, []).append(cid)

    # Single entity (or none) → no minority to protect → plain RRF.
    if len(entity_to_ids) <= 1:
        return _finalize(global_order)

    # Phase 1 — quota grants. Round-robin across entities so that when top_k is
    # tight the guarantee is shared fairly rather than front-loaded onto the
    # first entity. Each entity contributes at most ``per_entity_quota`` ids.
    granted: set[str] = set()
    grant_order: list[str] = []
    entities = list(entity_to_ids)
    for slot in range(per_entity_quota):
        for ent in entities:
            ids = entity_to_ids[ent]
            if slot < len(ids):
                cid = ids[slot]
                granted.add(cid)
                grant_order.append(cid)

    # Phase 2 — fill remaining capacity in global RRF order, skipping granted.
    fill_order = [cid for cid in global_order if cid not in granted]

    return _finalize(grant_order + fill_order)
