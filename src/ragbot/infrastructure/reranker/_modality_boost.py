"""M17 — Modality-aware rerank boost.

Inspired by RAG-Anything ``raganything/reranker.py`` (HKUDS, 2025): when
the query intent expresses a preference for a particular modality
(``table_lookup``, ``list_lookup``, ``code_lookup``, ``how_to``,
``comparison``), upweight reranker scores for chunks whose ``chunk_type``
matches that preference.

Why a post-rerank helper (and not a new reranker strategy)?
-----------------------------------------------------------
The reranker port + registry pattern is already in place
(``application/ports/reranker_port.py`` + ``infrastructure/reranker/
registry.py``). Replacing the active strategy with a modality-aware
variant would force every existing adapter (Jina, ZeroEntropy, Voyage,
LiteLLM-Cohere, ViRanker-local) to grow modality logic — high blast
radius for a feature that only re-weights the *output* of any strategy.

A post-rerank multiplicative boost is provider-agnostic: it consumes
``(chunk, intent)`` pairs the reranker already emitted and adjusts
their scores. The reranker contract stays intact (single source of
relevance signal). HALLU=0 sacred is preserved because the boost never
fabricates a chunk — it can only re-order existing candidates.

When does it run?
-----------------
Per-bot opt-in via ``bots.plan_limits.modality_rerank_enabled``. Default
OFF. The gate is enforced HERE: both public helpers take an ``enabled``
flag (default ``False``). When ``enabled`` is False the helpers are a
no-op — :func:`apply_modality_boost` returns the chunk's raw score with
no multiplier and :func:`boost_chunks` returns the input list untouched
(byte-identical: no score is re-assigned, so a ``None`` / non-numeric /
missing score is NOT coerced). The caller resolves the flag via
``resolve_bot_limit(bot_cfg, "modality_rerank_enabled", ...)`` and passes
it in, so the boost map below is only ever consulted behind the opt-in.

The boost map shape
-------------------
Keys are ``"{intent}:{chunk_type}"`` strings; values are float
multipliers. Identity (``1.0``) is returned for any pair not in the map.
The map is config-sourced: the caller passes ``boost_map=`` (resolved
from ``system_config`` / per-bot config); when omitted it falls back to
the in-module seed :data:`_DEFAULT_BOOST_MAP` — that English-intent seed
is ONLY reachable when ``enabled`` is True. Bot owners extend or replace
individual entries via ``plan_limits.modality_boost_overrides``
(dict[str, float]).
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    DEFAULT_MODALITY_BOOST_CODE_LOOKUP,
    DEFAULT_MODALITY_BOOST_IDENTITY,
    DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
)


# Seed boost map — ONLY consulted when the per-bot ``enabled`` gate is
# True AND the caller did not pass an explicit ``boost_map``. The intent
# labels here are an English-vocab seed; the caller is expected to source
# the map from config so non-English deployments are not locked to it.
# Conservative — only the strongest intent ↔ type correlations earn a
# non-identity multiplier. Mirrors RAG-Anything's preset table for the
# same intent labels.
#
# Rationale per row:
#   table_lookup     → table       : tables are the canonical answer
#                                     surface for "what's the price of
#                                     X" / "list rates by Y" intents.
#   table_lookup     → table_row   : same as above, finer grain.
#   list_lookup      → table       : "list all X" often pivots on
#                                     tabular data even when phrased as
#                                     prose.
#   comparison       → table       : side-by-side comparisons are
#                                     table-shaped by definition.
#   code_lookup      → code        : code fences answer "how to call X"
#                                     better than narrative prose.
#   how_to           → code        : same as above for tutorial-shaped
#                                     intents.
_DEFAULT_BOOST_MAP: dict[str, float] = {
    "table_lookup:table": DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
    "table_lookup:table_row": DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
    "list_lookup:table": DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
    "comparison:table": DEFAULT_MODALITY_BOOST_TABLE_LOOKUP,
    "code_lookup:code": DEFAULT_MODALITY_BOOST_CODE_LOOKUP,
    "how_to:code": DEFAULT_MODALITY_BOOST_CODE_LOOKUP,
}


def apply_modality_boost(
    chunk: Any,
    query_intent: str,
    *,
    enabled: bool = False,
    boost_map: dict[str, float] | None = None,
    boost_overrides: dict[str, float] | None = None,
) -> float:
    """Return the (optionally boosted) score for a single chunk.

    Args:
        chunk: A chunk-shaped object — either a legacy ``dict`` (with
            ``chunk_type`` / ``type`` keys + ``score``) or a
            :class:`ragbot.application.dto.block.Block`. Both expose
            ``.get`` so a duck-typed read works uniformly.
        query_intent: The router's classified intent label
            (``"factoid"`` / ``"table_lookup"`` / ``"code_lookup"`` /
            ...). When empty or unknown the identity multiplier wins.
        enabled: Per-bot opt-in gate (default ``False``). When False the
            helper is a no-op — the chunk's raw score is returned with no
            multiplier and the boost map is never consulted (byte-identical
            default path). The caller resolves this from
            ``plan_limits.modality_rerank_enabled``.
        boost_map: Config-sourced ``"{intent}:{chunk_type}" -> multiplier``
            map. When ``None`` falls back to the in-module English seed
            :data:`_DEFAULT_BOOST_MAP`. Only consulted when ``enabled``.
        boost_overrides: Optional bot-owner override map. Keys must
            follow the ``"{intent}:{chunk_type}"`` shape. Merged on top
            of the (config or seed) boost map so partial overrides are
            permitted.

    Returns:
        ``base_score * multiplier``. ``base_score`` is read from
        ``chunk["score"]`` (default ``0.0``). The multiplier is
        :const:`DEFAULT_MODALITY_BOOST_IDENTITY` when no intent/type
        pair matches the (merged) boost map.

    Boundary behaviour:
        * Negative scores (uncommon — some rerankers emit logit-style
          floats) are passed through unchanged when the multiplier is
          1.0; a >1× multiplier on a negative score makes it *more*
          negative which is the intended ordering effect.
        * Missing or non-numeric ``score`` collapses to ``0.0``, so a
          boost of N×0 stays 0 — harmless.
    """
    if not enabled:
        # Gate OFF (default) — no re-weighting, byte-identical to the
        # reranker output. The boost map is not consulted.
        return _read_score(chunk)

    if not query_intent:
        # Identity path — no intent signal, no re-weighting.
        return _read_score(chunk) * DEFAULT_MODALITY_BOOST_IDENTITY

    chunk_type = _read_chunk_type(chunk)
    key = f"{query_intent}:{chunk_type}"

    # Config-sourced map wins; fall back to the in-module English seed.
    active_map = boost_map if boost_map is not None else _DEFAULT_BOOST_MAP

    # Merge active map with overrides — overrides win on collision.
    multiplier: float = active_map.get(key, DEFAULT_MODALITY_BOOST_IDENTITY)
    if boost_overrides:
        multiplier = float(boost_overrides.get(key, multiplier))

    base = _read_score(chunk)
    return base * multiplier


def boost_chunks(
    chunks: list[Any],
    query_intent: str,
    *,
    enabled: bool = False,
    boost_map: dict[str, float] | None = None,
    boost_overrides: dict[str, float] | None = None,
) -> list[Any]:
    """Apply :func:`apply_modality_boost` to each chunk's ``score``.

    When ``enabled`` is False (default) this is a no-op: the input list is
    returned UNTOUCHED — no score is re-assigned, so chunk dicts / Blocks
    are byte-identical to the reranker output. Only when ``enabled`` is
    True does it write boosted scores back.


    Mutates a shallow copy — the input list is left intact so callers
    can safely keep a pre-boost reference for audit / telemetry. Each
    chunk dict (or :class:`Block`) is mutated in-place if it is a
    plain dict (legacy path); a Block's ``metadata["score"]`` is
    updated when present.

    Order is preserved — the caller is responsible for re-sorting if
    the new scores demand it. Keeping the sort outside this helper
    avoids surprising the rerank node which sometimes wants the boost
    applied without re-ordering (e.g. for audit logging the delta).

    Returns:
        The same list reference, with ``score`` updated in place where
        possible. Returned for fluent-chaining convenience.
    """
    if not enabled:
        # Gate OFF (default) — leave every chunk byte-identical.
        return chunks
    for chunk in chunks:
        new_score = apply_modality_boost(
            chunk,
            query_intent,
            enabled=True,
            boost_map=boost_map,
            boost_overrides=boost_overrides,
        )
        _write_score(chunk, new_score)
    return chunks


# ─── Internal helpers — duck-typed read/write so both dict + Block work ───


def _read_score(chunk: Any) -> float:
    """Read ``score`` from a chunk-like object, defaulting to ``0.0``.

    Defensive against str-typed scores (some test fixtures use strings)
    and ``None`` values — both collapse to ``0.0``.
    """
    raw = chunk.get("score") if hasattr(chunk, "get") else None
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _read_chunk_type(chunk: Any) -> str:
    """Read the chunk type from any chunk shape.

    Honors the canonical ``chunk_type`` key, falls back to ``type``
    (legacy field name used by the parser), then defaults to
    ``"text"``. Returning ``"text"`` rather than ``""`` keeps the boost
    map keys well-formed (no ``"intent:"`` lookup).
    """
    if not hasattr(chunk, "get"):
        return "text"
    return str(chunk.get("chunk_type") or chunk.get("type") or "text")


def _write_score(chunk: Any, new_score: float) -> None:
    """Write the boosted score back to the chunk.

    For plain dicts we update the top-level ``score`` key (matches the
    pre-existing legacy shape). For Block instances we touch
    ``metadata["score"]`` so the dataclass ``__getitem__`` proxy keeps
    returning the boosted value.
    """
    if isinstance(chunk, dict):
        chunk["score"] = new_score
        return
    # Block path — write through metadata so dict access reflects it.
    if hasattr(chunk, "metadata") and isinstance(chunk.metadata, dict):
        chunk.metadata["score"] = new_score


__all__ = ["apply_modality_boost", "boost_chunks"]
