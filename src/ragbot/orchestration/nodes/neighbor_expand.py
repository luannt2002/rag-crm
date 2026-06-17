"""M2 / M22 — Neighbor window expansion (post-retrieve).

Inspired by LightRAG ``local_query`` (HKUDS, 2025) and LlamaIndex's
``SentenceWindowNodeParser`` window-expansion pattern. After the
retrieve + rerank + MMR stages settle on a top-K of seed chunks, this
helper expands each seed with ±N adjacent siblings inside the same
document (matched by ``document_id`` + ``chunk_index``). The result is a
broader context window for the LLM **without** issuing a second
embedding or LLM call — the recall cost is one batched SQL round-trip.

Distinct from neighbouring retrieval stages
-------------------------------------------
* ``parent_child_enabled`` swaps every child chunk for its parent block
  regardless of sibling concentration. Bloats context when only one
  child matched.
* ``auto_merge_retrieval`` collapses sibling children into a shared
  parent **when ≥N siblings hit**. Different signal — relies on
  parent_chunk_id grouping, not chunk-index adjacency.
* Neighbor expansion APPENDS adjacent chunks regardless of whether
  they share a parent_chunk_id. Useful for flat-chunked corpora
  (legal articles, FAQ rows) where parent_chunk_id is null.

Token budget (M22)
------------------
Budget enforcement is delegated to
``shared.token_budget.truncate_to_token_budget`` — the single
M22 helper that any node growing a bounded context window must
reuse. Seeds always win the budget race because they are the
helper's ``head`` (the helper's "always include first" rule); the
first neighbour to push the cumulative token total above
``DEFAULT_NEIGHBOR_TOKEN_BUDGET`` is dropped, along with every later
candidate.

HALLU=0 sacred
--------------
The helper never fabricates content. Every emitted chunk is fetched
from ``document_chunks`` by ``record_tenant_id`` + ``document_id``
filter, scoped to the requesting tenant via RLS + an explicit JOIN
(defence-in-depth). A chunk that fails the SQL filter is silently
dropped — never substituted, never inferred.

Boundary
--------
This module exposes pure async helpers. The orchestration wiring
(``query_graph.py``) reads ``state["session_factory"]`` and wraps the
call in a step_tracker span; this module just consumes a session
factory + the seed chunks.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.shared.constants import (
    DEFAULT_CHARS_PER_TOKEN_ESTIMATE,
    DEFAULT_NEIGHBOR_MAX_CONCURRENCY,
    DEFAULT_NEIGHBOR_TOKEN_BUDGET,
    DEFAULT_NEIGHBOR_WINDOW_SIZE,
)
from ragbot.shared.token_budget import truncate_to_token_budget


logger = structlog.get_logger(__name__)


# Sentinel for missing chunk_index — keeps the type checker happy and
# makes the "skip this seed" branch obvious in the loop body. We use
# ``-1`` because real ``chunk_index`` values are non-negative integers
# emitted by the parser sequence (0..N-1 per document).
_NO_INDEX: int = -1


def _estimate_tokens(content: str) -> int:
    """Coarse char→token estimate.

    Real tokeniser fan-out would require per-bot tokenizer wiring
    (Anthropic / OpenAI / ZeroEntropy each tokenise differently).
    Char-count divided by :const:`DEFAULT_CHARS_PER_TOKEN_ESTIMATE`
    holds within ±15 % across English + Vietnamese mixed corpora,
    which is fine for a coarse budget guard.
    """
    if not content:
        return 0
    return max(1, len(content) // DEFAULT_CHARS_PER_TOKEN_ESTIMATE)


def _seed_index(chunk: Any) -> int:
    """Extract ``chunk_index`` from a chunk-like object.

    Honors a few key aliases historic chunks carry — ``chunk_index``
    (canonical), ``idx`` (DB row), and the metadata-nested form
    (``metadata.chunk_index``). Returns :const:`_NO_INDEX` when none
    are present; the caller treats this as "skip neighbour fetch for
    this seed" rather than raising.
    """
    if not hasattr(chunk, "get"):
        return _NO_INDEX
    raw = chunk.get("chunk_index")
    if raw is None:
        raw = chunk.get("idx")
    if raw is None:
        meta = chunk.get("metadata")
        if isinstance(meta, dict):
            raw = meta.get("chunk_index")
    if raw is None:
        return _NO_INDEX
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _NO_INDEX


def _seed_document_id(chunk: Any) -> str | None:
    """Extract the parent ``document_id`` of a chunk.

    Several historical aliases exist — ``document_id`` (canonical),
    ``record_document_id`` (DB column), and ``doc_id`` (DTO short
    form). Returns ``None`` when none are present; caller skips
    neighbour fetch for this seed.
    """
    if not hasattr(chunk, "get"):
        return None
    raw = (
        chunk.get("document_id")
        or chunk.get("record_document_id")
        or chunk.get("doc_id")
    )
    if raw is None:
        return None
    return str(raw)


def _seed_chunk_id(chunk: Any) -> str | None:
    """Extract the canonical chunk id (UUID as str)."""
    if not hasattr(chunk, "get"):
        return None
    raw = chunk.get("chunk_id") or chunk.get("id")
    if raw is None:
        return None
    return str(raw)


def plan_neighbor_windows(
    chunks: list[Any],
    *,
    n: int = DEFAULT_NEIGHBOR_WINDOW_SIZE,
) -> dict[str, tuple[int, int]]:
    """Group seed chunks by document and compute per-doc index ranges.

    For each document touched by the seed set, returns the
    (lo, hi) range bounding the union of all (seed_index − n,
    seed_index + n) windows. Doing the per-doc union here means each
    document needs only one SQL query regardless of how many seeds it
    contributed, which keeps the SQL round-trip count bounded by the
    number of distinct documents.

    Pure / synchronous so it can be exercised in isolation by tests
    without any DB fixture. Returns an empty dict when no seeds carry
    a valid (document_id, chunk_index) pair.
    """
    if n < 0 or not chunks:
        return {}
    plan: dict[str, tuple[int, int]] = {}
    for chunk in chunks:
        doc_id = _seed_document_id(chunk)
        idx = _seed_index(chunk)
        if doc_id is None or idx == _NO_INDEX:
            continue
        lo = max(0, idx - n)
        hi = idx + n
        if doc_id in plan:
            prev_lo, prev_hi = plan[doc_id]
            plan[doc_id] = (min(prev_lo, lo), max(prev_hi, hi))
        else:
            plan[doc_id] = (lo, hi)
    return plan


def merge_neighbors_with_seeds(
    seeds: list[Any],
    neighbor_rows: list[dict[str, Any]],
    *,
    token_budget: int = DEFAULT_NEIGHBOR_TOKEN_BUDGET,
) -> list[dict[str, Any]]:
    """Merge fetched neighbour rows with the seed set.

    Algorithm:
        1. Build a dedup set keyed by ``chunk_id`` so a seed isn't
           emitted twice when it also matches its own window.
        2. Emit all seeds first (they win the budget race) — sum
           their estimated tokens into a running ``used`` total.
        3. Then walk neighbour rows ordered by ``(document_id,
           chunk_index)``. Each neighbour is added until ``used``
           crosses ``token_budget``; the *first* over-budget
           neighbour is rejected and the loop stops for safety.

    Returns:
        A new list (not a mutation of ``seeds``). Each entry is a
        plain dict — neighbour rows are normalised to the legacy chunk
        shape (``chunk_id``, ``content``, ``document_id``,
        ``chunk_index``, ``score``, ``metadata``) so downstream nodes
        (grade, generate) keep working unmodified.

    Token budget rule:
        ``token_budget`` is enforced inclusively — a payload whose
        cumulative tokens *equal* the budget is accepted; the next
        candidate that would push it strictly above is dropped.
        ``token_budget <= 0`` disables the cap (seeds + all
        neighbours are emitted).
    """
    if not seeds:
        return []

    seen: set[str] = set()
    seed_dicts: list[dict[str, Any]] = []

    # 1) Normalise seeds → plain dicts, deduped by chunk_id. Seeds
    #    always survive the budget race, so we emit them up-front; the
    #    shared M22 truncator's "always include head" rule (see
    #    ``shared.token_budget.truncate_to_token_budget``) preserves
    #    the first seed even if it alone exceeds ``token_budget``.
    for s in seeds:
        cid = _seed_chunk_id(s)
        if cid is None or cid in seen:
            continue
        if isinstance(s, dict):
            as_dict = dict(s)
        elif hasattr(s, "as_dict"):
            as_dict = s.as_dict()
        else:
            # Last resort: rebuild the minimal shape from getters.
            as_dict = {
                "chunk_id": cid,
                "content": s.get("content") if hasattr(s, "get") else "",
            }
        seed_dicts.append(as_dict)
        seen.add(cid)

    # 2) Normalise neighbours → plain dicts, sorted by (document,
    #    chunk_index) for stable ordering, deduped against the seed
    #    set (seed content wins; duplicate neighbour rows are dropped).
    neighbor_dicts: list[dict[str, Any]] = []
    sorted_neighbors = sorted(
        neighbor_rows,
        key=lambda r: (str(r.get("document_id") or ""), int(r.get("chunk_index") or 0)),
    )
    for row in sorted_neighbors:
        cid = str(row.get("chunk_id") or row.get("id") or "")
        if not cid or cid in seen:
            continue
        neighbor_dicts.append({
            "chunk_id": cid,
            "id": cid,  # legacy alias
            "content": str(row.get("content") or ""),
            "document_id": row.get("document_id"),
            "chunk_index": row.get("chunk_index"),
            "metadata": row.get("metadata") or {},
            "score": 0.0,
            "is_neighbor_expanded": True,
        })
        seen.add(cid)

    combined = seed_dicts + neighbor_dicts

    # 3) Token-budget truncation via shared M22 helper. ``budget <= 0``
    #    legacy semantics: ``0`` disables the cap entirely (seeds +
    #    all neighbours emitted). The helper's "always include head"
    #    rule maps cleanly: when combined is non-empty the first
    #    element is a seed, which existing semantics guarantee.
    if token_budget <= 0 or not combined:
        return combined

    def _content_tokens(item: dict[str, Any]) -> int:
        return _estimate_tokens(str(item.get("content") or ""))

    truncated = truncate_to_token_budget(
        combined, budget=token_budget, token_estimator=_content_tokens,
    )

    # Emit truncation event when the helper dropped tail items, so
    # dashboards can answer "how often does the budget actually bite?".
    if len(truncated) < len(combined):
        used_tokens = sum(_content_tokens(it) for it in truncated)
        dropped = len(combined) - len(truncated)
        logger.info(
            "neighbor_expand_budget_truncated",
            used_tokens=used_tokens,
            budget=token_budget,
            remaining_candidates=dropped,
        )
    return truncated


async def fetch_neighbors_sql(
    session_factory: Any,
    *,
    record_tenant_id: Any,
    plan: dict[str, tuple[int, int]],
    max_concurrency: int = DEFAULT_NEIGHBOR_MAX_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Fetch neighbour rows for every document in the plan.

    Issues one SQL query per document, bounded by ``max_concurrency``
    semaphore so a huge plan (rare — usually 1-3 docs touched) cannot
    exhaust the connection pool. Each query scopes by
    ``record_tenant_id`` + ``record_document_id`` so RLS gets a
    defence-in-depth assist.

    Returns:
        A flat list of dict rows. Empty list on any infrastructure
        failure (logged via structlog, never raised) — the caller
        treats this as "no expansion this turn" which gracefully
        degrades to the seed-only set.

    Why ``return_exceptions=True`` on the gather:
        One slow / failing document must not poison the whole window
        expansion. We log per-task errors via structlog and keep the
        successful rows.
    """
    if not plan or session_factory is None or record_tenant_id is None:
        return []
    if max_concurrency < 1:
        max_concurrency = 1

    sem = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(
        doc_id: str, lo: int, hi: int
    ) -> list[dict[str, Any]]:
        async with sem:
            try:
                async with session_factory() as session:
                    # Tenant scope is defence-in-depth via JOIN documents —
                    # ``document_chunks`` has no ``record_tenant_id`` of
                    # its own (scoped via the parent doc) so the WHERE
                    # filters on ``d.record_tenant_id`` instead. RLS still
                    # enforces row visibility; the explicit JOIN means
                    # we'd refuse to read across-tenant even if RLS were
                    # disabled (defence-in-depth requirement from CLAUDE.md
                    # Quality Gate #4).
                    result = await session.execute(
                        sa_text(
                            "SELECT dc.id, dc.content, dc.record_document_id, "
                            "  dc.chunk_index, dc.metadata_json "
                            "FROM document_chunks dc "
                            "JOIN documents d ON d.id = dc.record_document_id "
                            "WHERE d.record_tenant_id = :tid "
                            "  AND dc.record_document_id = :did "
                            "  AND dc.chunk_index BETWEEN :lo AND :hi "
                            "ORDER BY dc.chunk_index"
                        ),
                        {
                            "tid": record_tenant_id,
                            "did": doc_id,
                            "lo": lo,
                            "hi": hi,
                        },
                    )
                    rows: list[dict[str, Any]] = []
                    for r in result.fetchall():
                        rows.append({
                            "chunk_id": str(r[0]),
                            "content": r[1],
                            "document_id": str(r[2]),
                            "chunk_index": int(r[3]) if r[3] is not None else 0,
                            "metadata": r[4] or {},
                        })
                    return rows
            except (SQLAlchemyError, OSError, RuntimeError) as exc:
                logger.warning(
                    "neighbor_expand_sql_failed",
                    document_id=doc_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                return []

    tasks = [_fetch_one(doc_id, lo, hi) for doc_id, (lo, hi) in plan.items()]
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, BaseException):
            # Shouldn't happen — every coro catches its own — but defensive.
            logger.warning("neighbor_expand_task_exception", error=str(res))
            continue
        out.extend(res)
    return out


async def expand_neighbors(
    chunks: list[Any],
    *,
    session_factory: Any,
    record_tenant_id: Any,
    window_size: int = DEFAULT_NEIGHBOR_WINDOW_SIZE,
    token_budget: int = DEFAULT_NEIGHBOR_TOKEN_BUDGET,
    max_concurrency: int = DEFAULT_NEIGHBOR_MAX_CONCURRENCY,
) -> list[dict[str, Any]]:
    """End-to-end neighbour expansion entry point.

    The orchestrator's ``neighbor_expand`` node calls this and threads
    the returned list back into ``state["retrieved_chunks"]`` (or
    ``reranked_chunks`` depending on where the node sits in the graph).

    Args:
        chunks: Seed chunks from the previous stage (typically MMR
            output). Empty / falsy → empty return.
        session_factory: Async session factory carried on the graph
            state. ``None`` disables expansion (graceful degrade).
        record_tenant_id: Tenant UUID for the RLS WHERE clause.
        window_size: ±N chunk_index radius. Negative / zero disables.
        token_budget: Aggregate token cap (M22).
        max_concurrency: Per-doc SQL fan-out bound.

    Returns:
        The merged seed+neighbour list, deduped by chunk_id, bounded
        by token_budget. Returns the original seed dict snapshots
        unchanged when expansion is disabled / fails / no neighbours
        exist for any seed.
    """
    if not chunks:
        return []
    if window_size <= 0 or session_factory is None or record_tenant_id is None:
        # Fast path — no expansion. Return seeds as plain dicts so the
        # downstream node sees a uniform shape.
        out: list[dict[str, Any]] = []
        for c in chunks:
            if isinstance(c, dict):
                out.append(dict(c))
            elif hasattr(c, "as_dict"):
                out.append(c.as_dict())
            else:
                out.append(dict(getattr(c, "__dict__", {})))
        return out

    plan = plan_neighbor_windows(chunks, n=window_size)
    neighbor_rows = await fetch_neighbors_sql(
        session_factory,
        record_tenant_id=record_tenant_id,
        plan=plan,
        max_concurrency=max_concurrency,
    )
    return merge_neighbors_with_seeds(
        chunks, neighbor_rows, token_budget=token_budget
    )


__all__ = [
    "expand_neighbors",
    "fetch_neighbors_sql",
    "merge_neighbors_with_seeds",
    "plan_neighbor_windows",
]
