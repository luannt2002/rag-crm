from __future__ import annotations
from typing import Final  # noqa: F401
from ._18_admin_all_tenants_analytics_ import *  # noqa: F401,F403

# --- sprint3-ekimetrics-selector (9 constants) ---
# --- Ekimetrics 5-metric rule-based chunker-strategy selector --------------
# Thresholds drive the Ekimetrics LREC 2026 rule-based selector
# (paper https://arxiv.org/abs/2603.25333 §"Rule-Based Selector"). Each value
# is a fraction in [0, 1]. Operators override at runtime via system_config
# keys ``ekimetrics_*_threshold``; these constants are the schema defaults.
DEFAULT_EKIMETRICS_BI_THRESHOLD: Final[float] = 0.6
# DCC < this → blocks drift from document gist; use coherence-aware chunker.
DEFAULT_EKIMETRICS_DCC_THRESHOLD: Final[float] = 0.5
# Top-N most frequent content tokens forming the document "gist" against
# which each block is scored for DCC. Matches the paper's vocabulary cap.
DEFAULT_EKIMETRICS_DOC_GIST_TOP_TOKENS: Final[int] = 50
# Confidence assigned to the default "balanced" branch (hybrid). Paper does
# not specify a number — match the legacy ``DEFAULT_STRATEGY_*`` band so the
# Ekimetrics path does not appear artificially overconfident.
DEFAULT_EKIMETRICS_FALLBACK_CONFIDENCE: Final[float] = 0.8
# Minimum token length when building the document-gist token set. 2 chars
# keeps Vietnamese mono-syllabic content tokens while filtering punctuation
# fragments.
DEFAULT_EKIMETRICS_MIN_TOKEN_LEN: Final[int] = 2
# RC ≥ this → references resolve well; switch to proposition splits.
DEFAULT_EKIMETRICS_RC_THRESHOLD: Final[float] = 0.8
DEFAULT_EKIMETRICS_SC_MAX_BAND_RATIO: Final[float] = 2.0
# Size-Compliance band as ratios of the target chunk size. Chunks whose
# length falls outside ``[min_ratio * target, max_ratio * target]`` count
# as size-non-compliant. The asymmetric band (0.5 .. 2.0) mirrors the
# paper's "half to double target" tolerance for tail blocks.
DEFAULT_EKIMETRICS_SC_MIN_BAND_RATIO: Final[float] = 0.5
# SC < this → too many chunks outside the size band; force recursive splits.
DEFAULT_EKIMETRICS_SC_THRESHOLD: Final[float] = 0.7

# --- sprint5-proposition-llm (2 constants) ---
# Per-call source-text ceiling (characters). Anything longer is split
# upstream by the chunker; this guards a single LLM call from blowing
# context window on a giant paragraph.
DEFAULT_PROPOSITION_LLM_MAX_INPUT_CHARS: Final[int] = 4_000
# Minimum proposition length (characters) — anything shorter is dropped
# as noise (single-word lines, residual punctuation from a malformed
# completion). Mirrors ``DEFAULT_CHUNK_MIN_CLAUSE_LEN`` semantics for
# the rule-based path.
DEFAULT_PROPOSITION_LLM_MIN_LEN: Final[int] = 12

# --- sprint6-chunk-quality-scoring (11 constants) ---
# Non-alphanumeric char ratio above which a chunk is judged corrupted
# (typical prose is 0.15-0.25 punctuation+whitespace; 0.5+ signals
# table-cell leak or OCR replacement-char run).
DEFAULT_CHUNK_QUALITY_CORRUPTION_RATIO_MAX: Final[float] = 0.5
# Information-density (type/token ratio) ramp boundaries.
DEFAULT_CHUNK_QUALITY_INFO_DENSITY_FLOOR: Final[float] = 0.2
DEFAULT_CHUNK_QUALITY_INFO_DENSITY_TARGET: Final[float] = 0.5
DEFAULT_CHUNK_QUALITY_MAX_CHARS: Final[int] = 4000
# Text-length triangular score knee points (characters, not words — char
# count is encoding-stable across languages including CJK / VN diacritics).
DEFAULT_CHUNK_QUALITY_MIN_CHARS: Final[int] = 50
DEFAULT_CHUNK_QUALITY_MIN_SCORE: Final[float] = 0.5
DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS: Final[int] = 800
QUALITY_WEIGHT_INFO_DENSITY: Final[float] = 0.2
QUALITY_WEIGHT_LANGUAGE: Final[float] = 0.2
QUALITY_WEIGHT_NO_CORRUPTION: Final[float] = 0.3
# Per-component weights (must sum to 1.0).
QUALITY_WEIGHT_TEXT_LENGTH: Final[float] = 0.3

# --- sprint6-cleanbase-tier0 (3 constants) ---
# Replacement token used by Tier-0 when a prompt-injection pattern matches.
# Visible in the persisted chunk so downstream retrieval + grounding checks
# can tell the bot owner exactly which corpus segments were scrubbed.
DEFAULT_INJECTION_REDACTION_TOKEN: Final[str] = "[REDACTED]"
# HTML / XML tag pattern — pre-compilable. Requires the opening ``"<"`` to
# be IMMEDIATELY followed by a letter (opening tag), ``"/"`` (closing tag)
# or ``"!"`` (comment / DOCTYPE), which leaves legitimate ``"a < b"`` math
# and chat-ML tokens like ``"<|im_start|>"`` intact (those are surfaced by
# the prompt-injection regex below, not by the HTML strip). Body is
# non-greedy, then anchored against the next ``">"`` on the same span.
HTML_TAG_REGEX: Final[str] = r"</?[A-Za-z!][^<>]*>"
# Zero-width + BOM family — the Trojan-Source paper enumerates these as the
# invisible injection vectors. NOT a complete Unicode invisible-format
# enumeration (we keep U+00AD soft-hyphen which is legitimately used by
# typesetters); just the high-signal attack surface.
#   U+200B ZERO WIDTH SPACE
#   U+200C ZERO WIDTH NON-JOINER
#   U+200D ZERO WIDTH JOINER
#   U+200E LEFT-TO-RIGHT MARK
#   U+200F RIGHT-TO-LEFT MARK
#   U+202A..U+202E directional formatting (LRE/RLE/PDF/LRO/RLO)
#   U+2060 WORD JOINER
#   U+2066..U+2069 LRI/RLI/FSI/PDI
#   U+FEFF BYTE ORDER MARK / ZWNBSP
ZERO_WIDTH_CHAR_REGEX: Final[str] = (
    "[​-‏‪-‮⁠⁦-⁩﻿]"
)

# --- sprint6-recap-pii-vn (1 constants) ---
# --- RECAP PII (Vietnamese custom) feature flag -----------------------------
# System-level kill-switch for the RECAP PII detect hook. When False (default),
# the hook is bypassed at the ingest boundary regardless of any per-bot opt-in
# in ``plan_limits.pii_redaction_enabled``. The composite gate is:
#   recap_pii_enabled (system_config, default False)
#     AND plan_limits.pii_redaction_enabled (per-bot, default False)
#       → redact + emit step_name="recap_pii_detect"
# Proof: Paper "RECAP-PII" + Microsoft Presidio recognizer pattern adapted
# to Vietnamese national-ID / phone / address shapes. See
# ``plans/260514-master-of-master/SPRINT-GAP-CLOSURE.md`` for the
# end-to-end RECAP spec.
DEFAULT_RECAP_PII_ENABLED: Final[bool] = False

# --- e1-table-narrator (1 constants) ---
# AdapChunk Layer 7 — rule-based TABLE narrator ($0 cost path).
# Companion to the LLM narrator above: deterministic markdown-table
# linearisation used when an operator wants cost-free narration for
# highly structured blocks. ``max_rows`` caps the row-by-row expansion
# so a 1000-row table does not blow the embedding context; remaining
# rows are summarised as a tail count. Ten rows covers >95% of tables
# in typical KB corpora (per debug doc Phan 3.8) while keeping the
# narrated string short enough to embed comfortably.
DEFAULT_TABLE_NARRATE_MAX_ROWS: Final[int] = 10

# --- e2-formula-narrator (2 constants) ---
# AdapChunk Layer 7 — FORMULA narrator (LaTeX → natural-language description).
# Tighter token budget than the generic narrate path: a 1-2 sentence formula
# description rarely needs more than ~100 tokens, and the lower cap is the
# main cost-guard for Haiku Batch ingest (50% discount × small max_tokens
# → ~$0.00005 per formula).
DEFAULT_FORMULA_NARRATE_MAX_TOKENS: Final[int] = 100
# Prompt template kept generic / domain-neutral. Bot owner's system_prompt
# is NEVER touched at query time — this template is INGEST-side only
# (Quality Gate #10: application MUST NOT inject text into the answer LLM).
DEFAULT_FORMULA_NARRATE_PROMPT_TEMPLATE: Final[str] = (
    "Describe this mathematical formula in 1-2 plain sentences (no LaTeX in "
    "output, no greeting). Be precise about what variables represent.\n\n"
    "Formula:\n{latex}\n\nDescription:"
)

# --- Lexical retrieval port (Strategy + DI) ---------------------------------
# Separate keyword/sparse path running in parallel with the vector branch,
# fused via RRF in the retrieve node. Default OFF (``"null"``) for backward
# compatibility — operators flip via ``system_config.lexical_retrieval_provider``.
# Registered providers live in ``infrastructure/retrieval/lexical_registry.py``.
DEFAULT_LEXICAL_RETRIEVAL_PROVIDER: Final[str] = "null"


# === Wave J continuation — 21 more constants from src + tests ===

# Corpus-size ceiling above which CAG MUST decline and fall back to RAG.
# 80K tokens chosen because:
#   - Claude 3.5/4.x prompt cache breakpoint cost amortises well under 100K.
#   - Beyond that, RAG retrieval is cheaper than re-priming the cache on a
#     miss (paper Figure 5 — cross-over around 80-120K depending on model).
#   - Conservative floor: ops can raise via system_config without code change.
DEFAULT_CAG_MAX_CORPUS_TOKENS: Final[int] = 80000
