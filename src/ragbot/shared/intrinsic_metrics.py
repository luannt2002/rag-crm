"""Ekimetrics 5-Metric Intrinsic Chunking Quality Metrics — pure lexical (no LLM).

Implements the five intrinsic metrics from Ekimetrics LREC 2026 paper for
selecting the optimal chunking strategy without ground-truth labels:

- **RC**  (References Completeness)     — cross-reference markers preserved
- **ICC** (Intrachunk Cohesion)         — token-overlap similarity within blocks
- **DCC** (Document Contextual Coherence) — block-vs-document gist similarity
- **BI**  (Block Integrity)             — structural blocks unfragmented
- **SC**  (Size Compliance)             — chunk sizes within target band

All five are computed via lexical heuristics (Jaccard / regex / size band) so
the selector remains:

* **HALLU=0 sacred** — no LLM invocation, no embedder fabrication
* **Domain-neutral** — no brand / industry literal
* **Zero-hardcode** — thresholds resolved from ``shared.constants`` defaults
  (operator overrides land via ``system_config`` at the call site)
* **Synchronous** — fits the existing ``select_strategy(profile)`` call site
  without any async refactor

The selector ``ekimetrics_select`` returns a ``(strategy, confidence, reason)``
triple where ``strategy`` is one of the chunker dispatch names recognised by
``smart_chunk``. The paper's "late_chunking" suggestion maps to ``semantic``
because ``smart_chunk`` does not dispatch a standalone late-chunking branch
(late chunking is a post-embedding stage owned by ``late_chunking.py``).

# Proof citation
# Ekimetrics — Adaptive Chunking: Optimizing Chunking-Method Selection for RAG
# Paper: https://arxiv.org/abs/2603.25333
# Venue: LREC 2026 (peer-reviewed)
# GitHub: https://github.com/ekimetrics/adaptive-chunking
# Benchmark: 78% Answer Correctness vs 70-73% baselines, p<0.001
# 5 metrics: RC + ICC + DCC + BI + SC
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

import structlog

from ragbot.shared.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_EKIMETRICS_BI_THRESHOLD,
    DEFAULT_EKIMETRICS_DCC_THRESHOLD,
    DEFAULT_EKIMETRICS_DOC_GIST_TOP_TOKENS,
    DEFAULT_EKIMETRICS_FALLBACK_CONFIDENCE,
    DEFAULT_EKIMETRICS_MIN_TOKEN_LEN,
    DEFAULT_EKIMETRICS_RC_THRESHOLD,
    DEFAULT_EKIMETRICS_SC_MAX_BAND_RATIO,
    DEFAULT_EKIMETRICS_SC_MIN_BAND_RATIO,
    DEFAULT_EKIMETRICS_SC_THRESHOLD,
)

logger = structlog.get_logger(__name__)


# ── Cross-reference marker patterns (domain-neutral, multi-language safe) ──
# Match common in-document reference styles that should stay resolvable:
#   * "see section 3.2", "xem mục 4", "cf. section A"
#   * "Figure 5", "Hình 7", "Table 2", "Bảng 3"
#   * "[12]", "[Ref 4]", "(see above)"
# Pure structural — no domain words.
_XREF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:see|cf\.|xem|tham\s+khảo)\s+(?:section|mục|chapter|chương)\s+\d", re.IGNORECASE),
    re.compile(r"\b(?:figure|hình|table|bảng|equation|công\s+thức)\s+\d+", re.IGNORECASE),
    re.compile(r"\[(?:ref|reference|tham\s+chiếu)?\s*\d+\]", re.IGNORECASE),
    re.compile(r"\((?:see|xem)\s+\w+\)", re.IGNORECASE),
)

# Strategy names this selector may return — MUST match smart_chunk dispatch.
_VALID_STRATEGIES: frozenset[str] = frozenset({
    "hdt",
    "semantic",
    "recursive",
    "hybrid",
    "proposition",
    "table_csv",
})

# Paper-name → smart_chunk-name mapping. Paper proposes "late_chunking" when
# DCC is low, but late chunking is a post-embedding stage, not a chunker —
# fall back to "semantic" (closest paradigm: long-context-aware splits).
_PAPER_TO_DISPATCH: dict[str, str] = {
    "late_chunking": "semantic",
    "proposition": "proposition",
    "semantic": "semantic",
    "recursive": "recursive",
    "hybrid": "hybrid",
    "hdt": "hdt",
}


@dataclass(frozen=True)
class IntrinsicMetrics:
    """The five Ekimetrics intrinsic chunking-quality metrics.

    Each value is a fraction in [0, 1]. Higher is better for RC, ICC, DCC, BI,
    and SC; lower means the corresponding aspect of chunk quality is degraded
    so the selector should pick a strategy specialised to repair it.
    """

    RC: float
    ICC: float
    DCC: float
    BI: float
    SC: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class EkimetricsThresholds:
    """Selector thresholds (operator-tunable via system_config at call site)."""

    BI: float = DEFAULT_EKIMETRICS_BI_THRESHOLD
    RC: float = DEFAULT_EKIMETRICS_RC_THRESHOLD
    DCC: float = DEFAULT_EKIMETRICS_DCC_THRESHOLD
    SC: float = DEFAULT_EKIMETRICS_SC_THRESHOLD


# ── Token + similarity helpers (lexical only, no embedder) ────────────────


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokens of length ≥ MIN. Domain-neutral, no stop list."""
    if not text:
        return []
    return [
        t for t in re.findall(r"[\wÀ-ỹ]+", text.lower())
        if len(t) >= DEFAULT_EKIMETRICS_MIN_TOKEN_LEN
    ]


def _jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity of two token bags. 0.0 when either side empty."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def _split_sentences(text: str) -> list[str]:
    """Sentence split on terminal punctuation. Cheap, deterministic."""
    if not text or not text.strip():
        return []
    parts = re.split(r"(?<=[\.\!\?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _doc_gist_tokens(text: str) -> set[str]:
    """Top-N most frequent content tokens — proxy for document gist."""
    counts = Counter(_tokenize(text))
    if not counts:
        return set()
    top = counts.most_common(DEFAULT_EKIMETRICS_DOC_GIST_TOP_TOKENS)
    return {tok for tok, _ in top}


# ── Metric computation (pure functions) ───────────────────────────────────


def _compute_rc(text: str, blocks: list[str]) -> float:
    """RC — fraction of cross-reference markers whose target stays in the
    same block as the source. Higher = references stay resolvable.

    Heuristic: for each x-ref marker, check whether at least one *other*
    occurrence of its surface form appears in the same block (target
    section name, figure label, etc.). When no markers exist the metric
    defaults to ``1.0`` because there is nothing to break.
    """
    markers: list[str] = []
    for pat in _XREF_PATTERNS:
        markers.extend(pat.findall(text))
    if not markers:
        return 1.0

    preserved = 0
    for marker in markers:
        marker_norm = marker.strip().lower()
        for block in blocks:
            block_lower = block.lower()
            if block_lower.count(marker_norm) >= 2:
                preserved += 1
                break
    return preserved / len(markers)


def _compute_icc(blocks: list[str]) -> float:
    """ICC — mean Jaccard sim between adjacent sentences inside each block.

    Higher = sentences inside a block stay topically coherent. Uses
    lexical Jaccard as a deterministic proxy (no embedder dependency).
    Blocks with <2 sentences are skipped; empty corpus → 1.0 (vacuous).
    """
    sims: list[float] = []
    for block in blocks:
        sentences = _split_sentences(block)
        if len(sentences) < 2:
            continue
        tokenized = [_tokenize(s) for s in sentences]
        for i in range(len(tokenized) - 1):
            sims.append(_jaccard(tokenized[i], tokenized[i + 1]))
    if not sims:
        return 1.0
    return sum(sims) / len(sims)


def _compute_dcc(text: str, blocks: list[str]) -> float:
    """DCC — mean overlap between each block's tokens and the document gist.

    Higher = blocks remain anchored to the document's central topic.
    Vacuous case (empty gist) returns 1.0.
    """
    gist = _doc_gist_tokens(text)
    if not gist or not blocks:
        return 1.0
    sims: list[float] = []
    for block in blocks:
        bt = set(_tokenize(block))
        if not bt:
            continue
        sims.append(len(bt & gist) / len(gist))
    if not sims:
        return 1.0
    return sum(sims) / len(sims)


def _compute_bi(blocks: list[str], target_chunk_chars: int) -> float:
    """BI — fraction of structural blocks that fit unfragmented in a chunk
    of size ``target_chunk_chars``. Higher = fewer block-cut events.

    A block "fits" iff ``len(block) <= target_chunk_chars``. Empty corpus
    → 1.0 (no block to break).
    """
    if not blocks:
        return 1.0
    intact = sum(1 for b in blocks if len(b) <= target_chunk_chars)
    return intact / len(blocks)


def _compute_sc(chunks: list[str], target_chunk_chars: int) -> float:
    """SC — fraction of chunks whose size falls inside the target band.

    Band = ``[min_ratio * target, max_ratio * target]``. Empty chunks
    list → 1.0 (vacuous).
    """
    if not chunks:
        return 1.0
    lo = int(target_chunk_chars * DEFAULT_EKIMETRICS_SC_MIN_BAND_RATIO)
    hi = int(target_chunk_chars * DEFAULT_EKIMETRICS_SC_MAX_BAND_RATIO)
    inside = sum(1 for c in chunks if lo <= len(c) <= hi)
    return inside / len(chunks)


def compute_intrinsic_metrics(
    text: str,
    *,
    blocks: list[str] | None = None,
    chunks: list[str] | None = None,
    target_chunk_chars: int = DEFAULT_CHUNK_SIZE,
) -> IntrinsicMetrics:
    """Compute the 5 Ekimetrics intrinsic metrics for ``text``.

    @param text: full document content (required for RC / DCC).
    @param blocks: structural blocks to evaluate. Default = paragraphs
        (split on blank line). Callers that already have semantic blocks
        (from ``_split_into_blocks``) should pass them in.
    @param chunks: candidate chunks to evaluate SC against. ``None`` =
        SC computed by simulating an equal-split into ``target_chunk_chars``
        size, which is the paper's pre-chunk-selection contract.
    @param target_chunk_chars: chunk-size budget (chars). Default from
        ``shared.constants.DEFAULT_CHUNK_SIZE``.

    @return: ``IntrinsicMetrics`` dataclass with 5 floats in [0, 1].
    """
    if not text or not text.strip():
        return IntrinsicMetrics(RC=1.0, ICC=1.0, DCC=1.0, BI=1.0, SC=1.0)

    if blocks is None:
        blocks = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not blocks:
        blocks = [text]

    if chunks is None:
        # Simulate equal-split when caller has no produced chunks yet.
        chunks = [
            text[i : i + target_chunk_chars]
            for i in range(0, len(text), target_chunk_chars)
        ] or [text]

    rc = _compute_rc(text, blocks)
    icc = _compute_icc(blocks)
    dcc = _compute_dcc(text, blocks)
    bi = _compute_bi(blocks, target_chunk_chars)
    sc = _compute_sc(chunks, target_chunk_chars)

    return IntrinsicMetrics(RC=rc, ICC=icc, DCC=dcc, BI=bi, SC=sc)


# ── Selector ──────────────────────────────────────────────────────────────


def ekimetrics_select(
    profile: dict,
    metrics: IntrinsicMetrics,
    thresholds: EkimetricsThresholds | None = None,
    *,
    feature_flag: str = "ekimetrics_5metric_selector_enabled",
) -> tuple[str, float, str]:
    """Pick a chunking strategy from the 5 Ekimetrics metrics.

    Selector logic (per LREC 2026 paper, section "Rule-Based Selector"):

    1. **BI low** → ``semantic``  — structural blocks getting fragmented,
       fall back to coherence-driven splits.
    2. **RC high** → ``proposition`` — references resolve well; preserve
       them by chunking at atomic proposition boundaries.
    3. **DCC low** → paper says "late_chunking"; we map to ``semantic``
       (smart_chunk does not dispatch late_chunking standalone).
    4. **SC low** → ``recursive`` — size compliance broken, use
       recursive-split which respects size budgets best.
    5. **default** → ``hybrid`` — balanced choice.

    @param profile: document profile from ``analyze_document``. Used only
        to log alongside metrics (no decision logic depends on it).
    @param metrics: precomputed ``IntrinsicMetrics``.
    @param thresholds: optional override (operator). Default = constants.
    @param feature_flag: flag name to emit in the structlog event so log
        consumers can join on it.

    @return: ``(strategy, confidence, reason)`` where ``strategy`` is
        guaranteed to be in ``smart_chunk``'s dispatch set and confidence
        is in [0, 1].
    """
    th = thresholds or EkimetricsThresholds()

    if metrics.BI < th.BI:
        strategy, confidence, reason = "semantic", metrics.BI, "BI_below_threshold"
    elif metrics.RC > th.RC:
        strategy, confidence, reason = "proposition", metrics.RC, "RC_above_threshold"
    elif metrics.DCC < th.DCC:
        # Paper proposes late_chunking; map to dispatch-valid name.
        strategy, confidence, reason = "late_chunking", 1.0 - metrics.DCC, "DCC_below_threshold"
    elif metrics.SC < th.SC:
        strategy, confidence, reason = "recursive", metrics.SC, "SC_below_threshold"
    else:
        strategy, confidence, reason = (
            "hybrid",
            DEFAULT_EKIMETRICS_FALLBACK_CONFIDENCE,
            "default_balanced",
        )

    # Map paper strategy → dispatch-valid name.
    dispatch_strategy = _PAPER_TO_DISPATCH.get(strategy, strategy)
    if dispatch_strategy not in _VALID_STRATEGIES:
        # Defensive — must never happen; fall through to recursive baseline.
        logger.warning(
            "ekimetrics_invalid_strategy_fallback",
            invalid_strategy=dispatch_strategy,
            feature_flag=feature_flag,
        )
        dispatch_strategy = "recursive"
        confidence = DEFAULT_EKIMETRICS_FALLBACK_CONFIDENCE

    # Clamp confidence into [0, 1] — defence vs upstream metric drift.
    confidence = max(0.0, min(1.0, float(confidence)))

    logger.info(
        "ekimetrics_selector",
        step_name="ekimetrics_selector",
        feature_flag=feature_flag,
        RC=round(metrics.RC, 4),
        ICC=round(metrics.ICC, 4),
        DCC=round(metrics.DCC, 4),
        BI=round(metrics.BI, 4),
        SC=round(metrics.SC, 4),
        selected_strategy=dispatch_strategy,
        confidence=round(confidence, 4),
        reason=reason,
        profile_total_headings=profile.get("total_headings", 0) if profile else 0,
        profile_table_count=profile.get("table_count", 0) if profile else 0,
    )

    return dispatch_strategy, confidence, reason


__all__ = [
    "EkimetricsThresholds",
    "IntrinsicMetrics",
    "compute_intrinsic_metrics",
    "ekimetrics_select",
]
