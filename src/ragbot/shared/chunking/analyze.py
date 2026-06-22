"""Document profiling + chunking-strategy selection (rule-based, deterministic).

Extracted from the chunking god-file: analyses a document's structure (headings,
tables, CSV shape, topic signals, block lengths) and picks the best chunking
strategy via a weighted rule scorer + Layer-5 cross-check. No LLM, no I/O — pure
rule logic so strategy choice is reproducible. Re-exported by ``chunking/__init__``
so existing imports (e.g. ``_is_table_line`` used by doc_profile) stay unchanged.
"""
from __future__ import annotations

import re
from typing import Any

import structlog

from ragbot.shared.bootstrap_config import get_boot_config
from ragbot.shared.constants import *  # noqa: F401,F403 — rule thresholds (curated __all__)

logger = structlog.get_logger(__name__)

# Sentence-terminator chars — shared by CSV-shape + sentence-split heuristics.
_SENTENCE_END_CHARS = (".", "!", "?")


def _is_csv_format(text: str) -> bool:
    """Whole-document CSV/table-format detector for ``select_strategy``.

    Two-criteria detection (zero-hardcode):

    1. **Pure-CSV ratio** (criterion 1, preserves prior behaviour) —
       fraction of lines with ≥ ``DEFAULT_CSV_MIN_COMMAS`` commas is
       ≥ ``DEFAULT_CSV_FORMAT_COMMA_RATIO`` AND fraction ending in
       sentence punctuation is ≤ ``DEFAULT_CSV_FORMAT_SENTENCE_END_RATIO``.

       Catches docs that are wall-to-wall CSV with no prose.

    2. **Dominant table run** (criterion 2, NEW — 260525 Bug #5) —
       longest run of consecutive lines sharing the SAME comma count
       (≥ ``DEFAULT_CSV_MIN_COMMAS``) is ≥
       ``DEFAULT_CSV_FORMAT_TABLE_RUN_MIN_LINES``.

       Catches mixed docs like ``[intro paragraph] + [12-row CSV table]
       + [trailing notes]`` where the pure-CSV ratio dips below
       ``DEFAULT_CSV_FORMAT_COMMA_RATIO`` because intro + footer lines
       lack commas. A run of N consecutive same-shape lines is the
       structural signal that a real table is embedded inside — bullet
       lists / prose don't produce that signature.

    Returning True triggers the ``table_csv`` strategy fast path. The
    splitter (``_chunk_table_csv_with_context``) then re-detects the
    table region precisely and emits header / row / footer chunks.

    Returns False for prose with comma-separated lists so generic
    recursive chunking still applies there.
    """
    if not text or not text.strip():
        return False
    lines = [ln for ln in text.split("\n") if ln.strip()][:DEFAULT_CSV_FORMAT_SAMPLE_LINES]
    if len(lines) < 2:
        return False

    # Criterion 1: pure-CSV ratio (preserved from prior behaviour).
    comma_count = sum(1 for ln in lines if ln.count(",") >= DEFAULT_CSV_MIN_COMMAS)
    sentence_end = sum(
        1 for ln in lines if ln.rstrip().endswith(_SENTENCE_END_CHARS)
    )
    n = len(lines)
    pure_csv = (
        comma_count / n >= DEFAULT_CSV_FORMAT_COMMA_RATIO
        and sentence_end / n <= DEFAULT_CSV_FORMAT_SENTENCE_END_RATIO
    )
    if pure_csv:
        return True

    # Criterion 2: dominant table run. Walk lines once; track longest
    # consecutive segment where every line has the same comma count
    # (≥ DEFAULT_CSV_MIN_COMMAS). A real embedded table has ≥ N such
    # consecutive rows; bullet lists with stray commas don't.
    best_run = 0
    i = 0
    while i < n:
        c = lines[i].count(",")
        if c < DEFAULT_CSV_MIN_COMMAS:
            i += 1
            continue
        j = i
        while j + 1 < n and lines[j + 1].count(",") == c:
            j += 1
        run = j - i + 1
        if run > best_run:
            best_run = run
        i = j + 1
    return best_run >= DEFAULT_CSV_FORMAT_TABLE_RUN_MIN_LINES


def _count_topic_signals(text: str) -> int:
    """Structural-only count of distinct topic markers in a document.

    Returns the sum of: markdown headers (h1+h2 weighted strong, h3 weighted
    half), paragraph blocks ≥ DEFAULT_TOPIC_PARAGRAPH_MIN_CHARS separated by
    blank lines, numbered list markers (every 3 markers = 1 signal), and
    UPPERCASE-style section headers. ≥ DEFAULT_WHOLE_DOC_MAX_TOPIC_SIGNALS
    means the document is multi-topic and the whole-doc fast path should be
    rejected so each topic embeds into its own chunk.

    Pure heuristic, no LLM call. Domain-neutral.
    """
    if not text or not text.strip():
        return 0

    lines = text.split("\n")
    signals = 0

    h1 = sum(1 for ln in lines if ln.strip().startswith("# "))
    h2 = sum(1 for ln in lines if ln.strip().startswith("## "))
    h3 = sum(1 for ln in lines if ln.strip().startswith("### "))
    signals += h1 + h2 + (h3 // 2)

    paragraphs = [
        p for p in text.split("\n\n")
        if len(p.strip()) >= DEFAULT_TOPIC_PARAGRAPH_MIN_CHARS
    ]
    signals += max(0, len(paragraphs) - 1)

    numbered = sum(
        1 for ln in lines if re.match(DEFAULT_TOPIC_NUMBERED_MARKER_RE, ln)
    )
    if numbered >= 3:
        signals += numbered // 3

    upper_section = sum(
        1 for ln in lines
        if (s := ln.strip()) and s.isupper() and 5 < len(s) < 80
    )
    signals += upper_section

    return signals


def _is_heading_line(line: str) -> bool:
    """Detect a markdown-style heading line (``#`` … ``####### …``).

    Any leading ``#`` followed by whitespace counts as a heading; the
    depth is intentionally unrestricted because callers (M18 footer
    merge guard, downstream chunkers) only need a YES/NO signal.
    """
    stripped = line.lstrip()
    if not stripped or not stripped.startswith("#"):
        return False
    # Strip the run of ``#`` then require whitespace before content so
    # ``#tag`` (hashtag in prose) is NOT treated as a heading.
    body = stripped.lstrip("#")
    return body.startswith(" ") or body == ""


# VN legal/admin clause markers at line start — điểm "a)", sub-point "(i)",
# numbered "1)". Used to exclude prose enumeration from CSV table detection.
_VN_CLAUSE_MARKER_RE = re.compile(r"^(?:[a-zđ]\)|\([ivxlcdm]+\)|\d+\))\s", re.IGNORECASE)


def _is_table_line(line: str) -> bool:
    """Kiểm tra 1 dòng có phải table row không."""
    stripped = line.strip()
    if not stripped:
        return False
    # Pipe table: | col | col |
    if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
        return True
    # Separator: |---|---|
    if re.match(r"^\|[\s\-:]+\|", stripped):
        return True
    # TSV (tab-separated, 2+ columns)
    if "\t" in stripped and stripped.count("\t") >= 1:
        return True
    # Numbered row with prices: "1 | Gội đầu | 60,000đ" or "1, Gội đầu, 60000"
    if re.match(r"^\d+\s*[|,]\s*\S", stripped):
        return True
    # CSV row — ≥ DEFAULT_CSV_MIN_COMMAS commas + no sentence punctuation.
    # Rationale: CSV rows lack ". " (sentence boundary) and don't end with ".".
    # Sentences like "I love A, B, and C." end in "." → excluded here.
    #
    # Carve-out (P2-B 🐛-B): Vietnamese legal/admin điểm-khoản lines are
    # comma-rich and end ';'/':' (clause continuation), so they slipped
    # through and were mis-narrated as tables. They are prose enumeration,
    # never CSV data rows, so exclude:
    #   - list-marker starts:  "a) ...", "(i) ...", "1) ..."
    #   - clause-continuation endings: ";" or ":"
    # A genuine CSV data row has neither, so this preserves table detection.
    if (stripped.count(",") >= DEFAULT_CSV_MIN_COMMAS
            and ". " not in stripped
            and not stripped.endswith(".")
            and not stripped.endswith((";", ":"))
            and not _VN_CLAUSE_MARKER_RE.match(stripped)):
        return True
    # Header-like: "STT | Dịch vụ | Giá"
    if re.match(r"^[A-ZÀ-Ỹa-zà-ỹ\s]+[|]", stripped):
        return True
    return False
# VN legal-structure helpers extracted to vn_structural (strangler split).
from ragbot.shared.chunking.vn_structural import *  # noqa: E402,F401,F403

def analyze_document(text: str) -> dict:
    """Phân tích cấu trúc tài liệu — rule-based, không LLM.

    @param text: nội dung tài liệu
    @return: document profile dict
    """
    lines = text.split("\n")

    heading_counts = {"h1": 0, "h2": 0, "h3": 0}
    table_count = 0
    text_blocks = 0
    total_text_words = 0
    in_table = False
    vn_hierarchical_markers = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            heading_counts["h3"] += 1
        elif stripped.startswith("## "):
            heading_counts["h2"] += 1
        elif stripped.startswith("# "):
            heading_counts["h1"] += 1
        elif _is_table_line(stripped):
            if not in_table:
                table_count += 1
                in_table = True
        else:
            in_table = False
            if stripped:
                text_blocks += 1
                total_text_words += len(stripped.split())
        # Count plain-text VN admin/legal markers even when no markdown was
        # applied — feeds the HDT cross-check fast-path in select_strategy.
        if _VN_HEADING_DETECT_RE.match(stripped):
            vn_hierarchical_markers += 1

    total_headings = sum(heading_counts.values())
    avg_text_length = total_text_words / max(text_blocks, 1)
    total_blocks = total_headings + table_count + text_blocks
    mixed_score = (
        table_count + sum(1 for l in lines if l.strip().startswith("```"))
    ) / max(total_blocks, 1)

    # AdapChunk Layer 3 (Phần 6.4): four additional profile fields used by
    # downstream chunk-strategy / narrate routing. KEEP existing fields above;
    # only ADD here so legacy callers stay compatible.
    formula_count = len(re.findall(DOCPROFILE_FORMULA_PATTERN, text))
    image_count = len(re.findall(DOCPROFILE_IMAGE_PATTERN, text))
    # Fenced code blocks come in opening/closing pairs; integer division pairs
    # them up. An unterminated fence (odd count) counts as 0 — better to under-
    # report than to inflate the mixed-content signal.
    code_fence_hits = len(
        re.findall(DOCPROFILE_CODE_FENCE_PATTERN, text, flags=re.MULTILINE)
    )
    code_block_count = code_fence_hits // DOCPROFILE_CODE_FENCES_PER_BLOCK
    blocks_for_ratio = (
        total_headings
        + table_count
        + formula_count
        + image_count
        + code_block_count
    )
    heading_ratio = round(total_headings / max(blocks_for_ratio, 1), 3)

    return {
        "heading_counts": heading_counts,
        "total_headings": total_headings,
        "table_count": table_count,
        "avg_text_length": avg_text_length,
        "mixed_content_score": mixed_score,
        "total_words": total_text_words,
        "has_toc": any(
            "mục lục" in l.lower() or "table of contents" in l.lower()
            for l in lines[:30]
        ),
        # CSV/table-format flag. When True and the document has no headings,
        # ``select_strategy`` picks ``table_csv`` so each row stays atomic.
        "is_csv_format": _is_csv_format(text),
        # Count of plain-text VN admin/legal hierarchy markers (Chương / Mục /
        # Điều / Phần) at line start. Drives the HDT cross-check fast-path:
        # when ≥ threshold markers are present, HDT is forced regardless of
        # other heuristics so structural [Chapter > Section > Article] paths
        # are preserved during chunking.
        "vn_hierarchical_markers": vn_hierarchical_markers,
        # AdapChunk Layer 3 additions (Phần 6.4) — rule-based extractors.
        "formula_count": formula_count,
        "image_count": image_count,
        "code_block_count": code_block_count,
        "heading_ratio": heading_ratio,
        "total_blocks_estimated": blocks_for_ratio,
    }


def analyze_document_blocks(blocks: list[Any]) -> dict:
    """AdapChunk Layer 3 — full Document Profile from a parsed Block list.

    Avoids the text-flatten path which loses ``block.type`` information.
    Counts come straight from the structural tags emitted by the OCR /
    parser layer, so the profile is exact (no regex heuristics needed
    once the parser has labelled blocks).

    @param blocks: iterable of ``ragbot.domain.entities.document.Block``
        instances. Typed ``Any`` to avoid an import cycle with the
        domain layer; duck-typed on ``.type`` (str) and ``.content``
        (str).
    @return: profile dict with the same field names as
        ``analyze_document()`` so downstream code can consume either
        shape transparently.
    """
    heading_blocks = [b for b in blocks if b.type == "HEADING"]
    table_blocks = [b for b in blocks if b.type == "TABLE"]
    formula_blocks = [b for b in blocks if b.type == "FORMULA"]
    image_blocks = [b for b in blocks if b.type == "IMAGE"]
    code_blocks = [b for b in blocks if b.type == "CODE"]
    text_blocks = [b for b in blocks if b.type == "TEXT"]

    total_blocks = len(blocks)
    avg_text_block_length = sum(len(b.content) for b in text_blocks) / max(
        len(text_blocks), 1
    )
    table_avg_rows = sum(b.content.count("\n") for b in table_blocks) / max(
        len(table_blocks), 1
    )
    mixed_count = total_blocks - len(text_blocks)
    mixed_content_score = round(mixed_count / max(total_blocks, 1), 3)

    # total_words = TEXT block words + HEADING content words (markdown '#'
    # prefix markers stripped). Mirrors analyze_document() semantics which
    # counts only meaningful content tokens, never the markdown delimiters.
    text_word_count = sum(len(b.content.split()) for b in text_blocks)
    heading_word_count = sum(
        len(b.content.lstrip("#").strip().split()) for b in heading_blocks
    )

    # Plain-text signals select_strategy + apply_cross_check also read. Mirror
    # analyze_document() exactly (same helpers) so the block profile carries the
    # FULL key contract — otherwise the strategy selector KeyErrors / mis-routes
    # on the block path. Lines are reconstructed from every block's content.
    _block_lines = [ln for b in blocks for ln in b.content.split("\n")]
    has_toc = any(
        "mục lục" in ln.lower() or "table of contents" in ln.lower()
        for ln in _block_lines[:30]
    )
    vn_hierarchical_markers = sum(
        1 for ln in _block_lines if _VN_HEADING_DETECT_RE.match(ln.strip())
    )
    is_csv_format = _is_csv_format("\n".join(_block_lines))

    return {
        "heading_counts": {
            "h1": sum(1 for b in heading_blocks if b.content.startswith("# ")),
            "h2": sum(1 for b in heading_blocks if b.content.startswith("## ")),
            "h3": sum(1 for b in heading_blocks if b.content.startswith("### ")),
        },
        "total_headings": len(heading_blocks),
        "table_count": len(table_blocks),
        "table_avg_rows": round(table_avg_rows, 1),
        "formula_count": len(formula_blocks),
        "image_count": len(image_blocks),
        "code_block_count": len(code_blocks),
        "avg_text_block_length": round(avg_text_block_length, 1),
        # ``select_strategy`` + ``apply_cross_check`` read ``avg_text_length``
        # and normalise it against WORD-count constants — so it must be WORDS
        # per text block (mirrors ``analyze_document``'s ``total_text_words /
        # text_blocks``), NOT the character count in ``avg_text_block_length``.
        "avg_text_length": round(text_word_count / max(len(text_blocks), 1), 1),
        "has_toc": has_toc,
        "is_csv_format": is_csv_format,
        "vn_hierarchical_markers": vn_hierarchical_markers,
        "heading_ratio": round(len(heading_blocks) / max(total_blocks, 1), 3),
        "mixed_content_score": mixed_content_score,
        "total_blocks": total_blocks,
        "total_words": text_word_count + heading_word_count,
    }


def select_strategy(
    profile: dict,
    *,
    text: str | None = None,
    ekimetrics_enabled: bool = False,
    ekimetrics_thresholds: EkimetricsThresholds | None = None,
    table_strategy: str = DEFAULT_TABLE_STRATEGY,
) -> tuple[str, float]:
    """Chọn chunking strategy dựa trên document profile — confidence scoring.

    @param profile: document profile từ analyze_document()
    @param text: optional full document text. Required only when the
        Ekimetrics path is enabled (it needs raw text to compute RC / ICC /
        DCC / BI / SC). When ``None`` or empty, the weighted-score
        path runs even if ``ekimetrics_enabled`` is True.
    @param ekimetrics_enabled: feature flag
        ``ekimetrics_5metric_selector_enabled`` (default OFF). When True
        AND ``text`` is supplied, the 5-metric rule-based selector from
        Ekimetrics LREC 2026 (arXiv 2603.25333) is used instead of the
        weighted scorer. Caller is responsible for resolving the
        flag via ``SystemConfigService.get_bool`` (zero-coupling here).
    @param ekimetrics_thresholds: optional operator override for the
        4 selector thresholds. ``None`` → schema defaults from
        ``shared.constants.DEFAULT_EKIMETRICS_*``.
    @param table_strategy: which strategy the CSV/column-table fast-path
        returns — resolved from the chunking policy chain
        (``shared.chunking_policy.resolve_chunking_policy``). Default
        ``DEFAULT_TABLE_STRATEGY`` keeps behaviour byte-identical for
        callers that don't pass a policy.
    @return: (strategy_name, confidence) where confidence in [0, 1]
    """
    # Derive shared features from profile (see DEFAULT_STRATEGY_WEIGHTS).
    total_headings = profile["total_headings"]
    total_words = profile.get("total_words", 0)
    h2_count = profile.get("heading_counts", {}).get("h2", 0)
    table_count = profile["table_count"]
    avg_len = profile["avg_text_length"]
    mixed = profile["mixed_content_score"]
    has_toc = profile["has_toc"]
    is_csv = profile.get("is_csv_format", False)
    vn_markers = profile.get("vn_hierarchical_markers", 0)

    # T1 — table_csv FAST PATH. When the whole document is
    # CSV-format AND has no headings, row-as-chunk is the right
    # strategy: it keeps each (service, price, ...) tuple atomic in a
    # single chunk so retrieval can return the full row context. The
    # previous "recursive" fallback was breaking these rows mid-tuple.
    if is_csv and total_headings == 0 and vn_markers == 0:
        return (table_strategy, 1.0)

    # T2 — HDT FAST PATH for VN admin/legal docs. Any document carrying ≥
    # DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES "Chương / Mục / Điều / Phần"
    # markers (markdown-promoted or plain-text) MUST use HDT — the structural
    # path is the whole point of citing legal text and weight-based scoring
    # was letting recursive win on docs where avg_text_length was small.
    if (total_headings + vn_markers) >= DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES:
        return ("hdt", 1.0)

    # Ekimetrics 5-metric selector (LREC 2026, arXiv 2603.25333) — runs ONLY for
    # AMBIGUOUS PROSE docs, AFTER the structural certainties above (CSV → table,
    # legal/admin → HDT) so it can NEVER override a doc whose shape is already
    # known (a naive placement before the fast-paths would break the spa CSV
    # stats path + thong-tu HDT). For ambiguous prose it replaces the weighted
    # scorer with the paper's intrinsic-metric rules. Flag-gated
    # (``ekimetrics_5metric_selector_enabled``, default OFF); caller resolves the
    # flag + supplies raw ``text``.
    if ekimetrics_enabled and text:
        from ragbot.shared.intrinsic_metrics import (  # noqa: PLC0415
            compute_intrinsic_metrics,
            ekimetrics_select,
        )
        metrics = compute_intrinsic_metrics(text)
        strategy, confidence, _reason = ekimetrics_select(
            profile=profile, metrics=metrics, thresholds=ekimetrics_thresholds,
        )
        return (strategy, round(confidence, 2))

    avg_norm = min(avg_len / DEFAULT_SEMANTIC_AVG_LEN_NORM, 1.0)

    _W = DEFAULT_STRATEGY_WEIGHTS
    scores: dict[str, float] = {}

    # HDT scoring
    w = _W["hdt"]
    scores["hdt"] = (
        min(total_headings / DEFAULT_HDT_HEADINGS_NORM, 1.0) * w["headings_norm"]
        + (1.0 if has_toc else 0.0) * w["has_toc"]
        + (1.0 if h2_count >= DEFAULT_HDT_H2_MIN_COUNT else 0.0) * w["has_h2_group"]
        + (1.0 if total_words > DEFAULT_HDT_LONG_DOC_WORDS else 0.0) * w["is_long_doc"]
    )

    # Semantic scoring
    w = _W["semantic"]
    scores["semantic"] = (
        avg_norm * w["avg_len_norm"]
        + (1.0 if total_headings <= DEFAULT_SEMANTIC_FEW_HEADINGS_MAX else 0.0) * w["few_headings"]
        + (1.0 if table_count == 0 else 0.0) * w["no_tables"]
        + (1.0 if total_words > DEFAULT_SEMANTIC_LONG_DOC_WORDS else 0.0) * w["is_long_doc"]
    )

    # Recursive scoring (always viable baseline)
    w = _W["recursive"]
    scores["recursive"] = (
        w["base"]
        + min(table_count / DEFAULT_RECURSIVE_TABLES_NORM, 1.0) * w["tables_norm"]
        + (1.0 if avg_len < DEFAULT_RECURSIVE_SHORT_AVG_LEN else 0.0) * w["short_avg_len"]
        + (1.0 if mixed > DEFAULT_RECURSIVE_MIXED_THRESHOLD else 0.0) * w["mixed_content"]
    )

    # Hybrid scoring: high for mixed content (headings + long prose)
    w = _W["hybrid"]
    scores["hybrid"] = (
        min(total_headings / DEFAULT_HYBRID_HEADINGS_NORM, 1.0) * w["headings_norm"]
        + (1.0 if mixed > DEFAULT_HYBRID_MIXED_THRESHOLD else 0.0) * w["mixed_content"]
        + (1.0 if total_words > DEFAULT_HYBRID_LONG_DOC_WORDS else 0.0) * w["is_long_doc"]
        + avg_norm * w["avg_len_norm"]
    )

    # Proposition scoring: high for long dense text with few headings
    w = _W["proposition"]
    scores["proposition"] = (
        avg_norm * w["avg_len_norm"]
        + (1.0 if total_headings <= DEFAULT_PROPOSITION_FEW_HEADINGS_MAX else 0.0) * w["few_headings"]
        + (1.0 if total_words > DEFAULT_PROPOSITION_LONG_DOC_WORDS else 0.0) * w["is_long_doc"]
        + (1.0 if table_count == 0 else 0.0) * w["no_tables"]
    )

    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    confidence = scores[best]

    # Fallback: low confidence → recursive (safest)
    if confidence < DEFAULT_STRATEGY_MIN_CONFIDENCE:
        return ("recursive", DEFAULT_STRATEGY_MIN_CONFIDENCE)

    return (best, round(confidence, 2))


# ---------------------------------------------------------------------------
# AdapChunk Layer 5 — Rule Cross-check (S3, T1-Smartness)
# ---------------------------------------------------------------------------
#
# Post-selector safety net: ``select_strategy()`` returns a (strategy, conf)
# pair scored from continuous weights; that pick can still be subtly wrong
# when individual feature counts cross specific tripwires (e.g. an HDT-style
# doc with only 3 headings, a "semantic" pick on bullet-point lists, etc.).
# Five rule-based conditions override the selector in those known-failure
# regions and log an audit event so operators can tune downstream.
#
# Proof citation:
# - AdapChunk Layer 5 — internal blueprint (PhD thesis private, not yet
#   peer-reviewed): concept inspiration only; the 5 conditions below are
#   platform-tuned for the dict-based profile this codebase produces.
# - Databricks AI-Driven Chunking blog (2024): "simple fallback to hybrid
#   when confidence < 0.6" — pattern reused as rule #1.
# - Ekimetrics — Adaptive Chunking, LREC 2026 (peer-reviewed,
#   https://arxiv.org/abs/2603.25333): proves post-selector adjustment
#   based on RC/ICC/DCC/BI/SC lifts Answer Correctness 78% vs 70-73%
#   baselines, p<0.001. AdapChunk L5 here is the platform's rule-based
#   companion to that statistical signal.
#
# Feature flag: ``adapchunk_layer5_cross_check_enabled`` (default OFF).
# Quality Gate compliance:
# - Domain-neutral (no brand / industry literal).
# - Zero-hardcode (every threshold reads ``system_config`` with constant
#   fallback).
# - Application does NOT inject text into the LLM prompt or override an
#   LLM answer here — only the chunking *strategy* is corrected pre-LLM.


def apply_cross_check(
    strategy: str,
    confidence: float,
    profile: dict,
) -> tuple[str, float, str | None]:
    """Apply AdapChunk Layer-5 override rules to a selector result.

    Pure function (no side effects, no I/O) for trivial unit-testability.
    Operator wiring (feature flag, audit event) is handled by the caller
    in ``smart_chunk``.

    Five conditions, evaluated in priority order:

    1. **Low-confidence fallback**: ``confidence < threshold`` → fall back
       to ``hybrid`` (Databricks defensive default).
    2. **HDT without enough headings**: ``strategy == "hdt"`` but the doc
       has fewer than the minimum heading count → downgrade to
       ``semantic`` (HDT on thin structure produces noisy splits).
    3. **Semantic on short blocks**: ``strategy == "semantic"`` but the
       average text-block length is below the prose threshold → upgrade
       to ``proposition`` (short blocks are clauses, not paragraphs).
    4. **Proposition on long structured docs**: ``strategy ==
       "proposition"`` but the doc has long paragraphs AND many headings
       → switch to ``hdt`` (proposition over-fragments well-structured
       content).
    5. **Mixed content not hybrid**: ``mixed_content_score`` exceeds the
       warn threshold but the selector did NOT pick ``hybrid`` — log a
       warning ONLY (no override; the selector may have legitimately
       picked another strategy that handles tables atomically).

    @param strategy: selector's chosen strategy name
    @param confidence: selector's confidence in [0, 1]
    @param profile: ``analyze_document()`` output (dict-shape contract)
    @return: ``(final_strategy, final_confidence, override_reason)``.
        ``override_reason`` is ``None`` when no rule fired (or only the
        warn-only rule fired).
    """
    # Read every threshold from system_config with constant fallback so
    # operators can tune without redeploy. Zero-hardcode requirement.
    conf_threshold = float(
        get_boot_config(
            "adapchunk_l5_confidence_threshold",
            DEFAULT_ADAPCHUNK_L5_CONFIDENCE_THRESHOLD,
        )
    )
    hdt_min_headings = int(
        get_boot_config(
            "adapchunk_l5_hdt_min_headings",
            DEFAULT_ADAPCHUNK_L5_HDT_MIN_HEADINGS,
        )
    )
    semantic_min_avg = int(
        get_boot_config(
            "adapchunk_l5_semantic_min_avg_block_len",
            DEFAULT_ADAPCHUNK_L5_SEMANTIC_MIN_AVG_BLOCK_LEN,
        )
    )
    prop_max_avg = int(
        get_boot_config(
            "adapchunk_l5_proposition_max_avg_block_len",
            DEFAULT_ADAPCHUNK_L5_PROPOSITION_MAX_AVG_BLOCK_LEN,
        )
    )
    prop_max_headings = int(
        get_boot_config(
            "adapchunk_l5_proposition_max_headings",
            DEFAULT_ADAPCHUNK_L5_PROPOSITION_MAX_HEADINGS,
        )
    )
    mixed_warn = float(
        get_boot_config(
            "adapchunk_l5_mixed_content_warn_threshold",
            DEFAULT_ADAPCHUNK_L5_MIXED_CONTENT_WARN_THRESHOLD,
        )
    )

    total_headings = int(profile.get("total_headings", 0) or 0)
    avg_block_len = float(profile.get("avg_text_length", 0.0) or 0.0)
    mixed_score = float(profile.get("mixed_content_score", 0.0) or 0.0)

    # Priority-ordered override list. First match wins; ``mixed_content``
    # is warn-only and intentionally excluded from this list.
    overrides: list[tuple[str, float, str]] = []

    # Rule 1 — Low-confidence fallback (Databricks pattern).
    if confidence < conf_threshold:
        overrides.append((
            "hybrid",
            DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_FALLBACK,
            "low_confidence_fallback",
        ))

    # Rule 2 — HDT pick but too few headings to justify it.
    if strategy == "hdt" and total_headings < hdt_min_headings:
        overrides.append((
            "semantic",
            DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_RULE,
            "hdt_but_few_headings",
        ))

    # Rule 3 — Semantic pick but average block too short to be prose.
    if strategy == "semantic" and avg_block_len < semantic_min_avg:
        overrides.append((
            "proposition",
            DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_RULE,
            "semantic_but_short_blocks",
        ))

    # Rule 4 — Proposition pick but long paragraphs + many headings; HDT
    # preserves structure better than atomic propositions here.
    if (
        strategy == "proposition"
        and avg_block_len > prop_max_avg
        and total_headings > prop_max_headings
    ):
        overrides.append((
            "hdt",
            DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_RULE,
            "proposition_but_long_structured",
        ))

    # Rule 5 — Mixed-content warn (no override). Quality Gate #10: we do
    # NOT silently rewrite the selector's pick here; a low-confidence
    # rule should not undo a deliberate selector choice. Operator inspects
    # the event and tunes weights if needed.
    if mixed_score > mixed_warn and strategy != "hybrid":
        logger.warning(
            "adapchunk_l5_mixed_content_not_hybrid",
            strategy=strategy,
            mixed_score=mixed_score,
            mixed_warn_threshold=mixed_warn,
        )

    if not overrides:
        return strategy, confidence, None

    new_strategy, new_conf, reason = overrides[0]
    return new_strategy, new_conf, reason


# ---------------------------------------------------------------------------
# Block splitting (shared by recursive + hybrid)
# ---------------------------------------------------------------------------


__all__ = [
    "_SENTENCE_END_CHARS",
    "_is_csv_format",
    "_count_topic_signals",
    "_is_heading_line",
    "_is_table_line",
    "_VN_CLAUSE_MARKER_RE",
    "analyze_document",
    "analyze_document_blocks",
    "select_strategy",
    "apply_cross_check",
]
