"""Per-intent RRF weight resolver (Phase-C C5).

Resolves the ``vector`` / ``bm25`` / ``reranker`` blend weights to apply
during hybrid_search fusion, based on the active classifier ``intent`` and
the bot-level configuration table. Pure function — no IO, no DB, no LLM.

Resolution chain (first wins):

1. ``pipeline_config['rerank_weights_by_intent']`` (per-bot owner override)
2. ``DEFAULT_RERANK_WEIGHTS_BY_INTENT`` (shared constants SSoT)

Within the chosen table:

1. Exact ``intent`` key (case-insensitive, whitespace-trimmed)
2. ``"default"`` bucket
3. Flat fallback ``DEFAULT_HYBRID_RRF_VECTOR_WEIGHT`` / ``..._BM25_WEIGHT``
   (preserves current production behaviour when no entry exists)

The ``reranker`` slot is parsed and returned for completeness so callers
can log / audit the intended blend, but the present pipeline applies it
only at the downstream rerank-stage scoring (not inside fusion).

Sum-to-1 normalisation is NOT enforced — the pgvector store clamps each
component to ``[0.0, +inf)`` independently. Callers that need strict
proportions can pass weights through :func:`normalize_weights`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from ragbot.shared.constants import (
    DEFAULT_ADAPTIVE_RERANK_WEIGHT_ENABLED,
    DEFAULT_HYBRID_RRF_BM25_WEIGHT,
    DEFAULT_HYBRID_RRF_VECTOR_WEIGHT,
    DEFAULT_RERANK_WEIGHTS_BY_INTENT,
)

_DEFAULT_BUCKET: Final[str] = "default"
_KEY_VECTOR: Final[str] = "vector"
_KEY_BM25: Final[str] = "bm25"
_KEY_RERANKER: Final[str] = "reranker"


class IntentWeights:
    """Immutable weight triple for one intent bucket.

    Members are exposed as ``vector``, ``bm25``, ``reranker`` floats. The
    class exists (rather than a raw tuple) so callers can pass it around
    without re-ordering bugs and so the rerank-stage logger has stable
    attribute names.
    """

    __slots__ = ("_vector", "_bm25", "_reranker")

    def __init__(self, vector: float, bm25: float, reranker: float) -> None:
        # Negative weights would flip RRF score ordering; clamp at zero.
        self._vector = max(0.0, float(vector))
        self._bm25 = max(0.0, float(bm25))
        self._reranker = max(0.0, float(reranker))

    @property
    def vector(self) -> float:
        return self._vector

    @property
    def bm25(self) -> float:
        return self._bm25

    @property
    def reranker(self) -> float:
        return self._reranker

    def as_dict(self) -> dict[str, float]:
        return {
            _KEY_VECTOR: self._vector,
            _KEY_BM25: self._bm25,
            _KEY_RERANKER: self._reranker,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IntentWeights):
            return NotImplemented
        return (
            self._vector == other._vector
            and self._bm25 == other._bm25
            and self._reranker == other._reranker
        )

    def __repr__(self) -> str:
        return (
            f"IntentWeights(vector={self._vector}, "
            f"bm25={self._bm25}, reranker={self._reranker})"
        )


_FLAT_FALLBACK: Final[IntentWeights] = IntentWeights(
    vector=DEFAULT_HYBRID_RRF_VECTOR_WEIGHT,
    bm25=DEFAULT_HYBRID_RRF_BM25_WEIGHT,
    reranker=0.0,
)


def _coerce_table(raw: Any) -> Mapping[str, Mapping[str, Any]] | None:
    """Accept the per-bot override only when it has the expected shape.

    Tolerates DB-driven JSON values that may be loaded as plain ``dict``
    instances; rejects anything that is not a string-keyed mapping of
    string-keyed mappings, so a misconfigured row cannot silently null
    out fusion weights.
    """
    if not isinstance(raw, Mapping):
        return None
    out: dict[str, Mapping[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, Mapping):
            continue
        out[k] = v
    return out or None


def _extract_one(bucket: Mapping[str, Any]) -> IntentWeights | None:
    """Pull (vector, bm25, reranker) out of a bucket; reject silent zeros."""
    try:
        v = float(bucket.get(_KEY_VECTOR, 0.0) or 0.0)
        b = float(bucket.get(_KEY_BM25, 0.0) or 0.0)
        r = float(bucket.get(_KEY_RERANKER, 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    # All-zero would silently disable fusion — refuse and fall through.
    if v == 0.0 and b == 0.0:
        return None
    return IntentWeights(vector=v, bm25=b, reranker=r)


def resolve_intent_weights(
    intent: str | None,
    *,
    pipeline_config: Mapping[str, Any] | None = None,
) -> IntentWeights:
    """Look up the RRF blend for ``intent`` with per-bot override + default.

    Args:
        intent: Classifier output. ``None`` / empty / unknown → ``default``
            bucket → flat ``DEFAULT_HYBRID_RRF_*`` fallback.
        pipeline_config: Per-bot pipeline_config map. The
            ``rerank_weights_by_intent`` key, if present and well-shaped,
            wins over ``DEFAULT_RERANK_WEIGHTS_BY_INTENT``.

    Returns:
        ``IntentWeights`` with non-negative ``vector`` / ``bm25`` /
        ``reranker``. Never raises.
    """
    intent_lc = str(intent or "").strip().lower()

    override = _coerce_table(
        (pipeline_config or {}).get("rerank_weights_by_intent")
    )
    table: Mapping[str, Mapping[str, Any]] = (
        override if override is not None else DEFAULT_RERANK_WEIGHTS_BY_INTENT
    )

    # Exact match first.
    if intent_lc and intent_lc in table:
        got = _extract_one(table[intent_lc])
        if got is not None:
            return got

    # Default bucket second.
    if _DEFAULT_BUCKET in table:
        got = _extract_one(table[_DEFAULT_BUCKET])
        if got is not None:
            return got

    # Flat constants fallback last — preserves pre-C5 production behaviour.
    return _FLAT_FALLBACK


def adaptive_weight_enabled(
    pipeline_config: Mapping[str, Any] | None = None,
) -> bool:
    """Resolve the per-bot feature flag with constants-level default."""
    cfg = pipeline_config or {}
    raw = cfg.get(
        "adaptive_rerank_weight_enabled",
        DEFAULT_ADAPTIVE_RERANK_WEIGHT_ENABLED,
    )
    return bool(raw)


def normalize_weights(weights: IntentWeights) -> IntentWeights:
    """Rescale to sum-to-1; identity when input sums to 0."""
    total = weights.vector + weights.bm25 + weights.reranker
    if total <= 0.0:
        return weights
    return IntentWeights(
        vector=weights.vector / total,
        bm25=weights.bm25 / total,
        reranker=weights.reranker / total,
    )


__all__ = [
    "IntentWeights",
    "adaptive_weight_enabled",
    "normalize_weights",
    "resolve_intent_weights",
]
