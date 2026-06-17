from __future__ import annotations
from typing import Final  # noqa: F401
from ._11_table_csv_chunking_strategy import *  # noqa: F401,F403

# --- Multi-stage retrieval fallback (Stream S8) -----------------------------
# Default OFF for backward compatibility — flip
# ``system_config.retrieval_multistage_enabled`` to ``true`` per environment
# once the operator wants the 4-stage chain instead of the single-shot retrieve.
# Each stage in ``DEFAULT_RETRIEVAL_STAGES`` runs sequentially. Whichever stage
# first returns at least one chunk with score >= ``DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD``
# wins; subsequent stages are skipped. Domain-neutral: any bot can use any
# subset by overriding ``retrieval_stage_{1,2,3,4}`` in system_config or
# per-bot pipeline_config.
DEFAULT_RETRIEVAL_MULTISTAGE_ENABLED: Final[bool] = False
DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD: Final[float] = 0.35
DEFAULT_RETRIEVAL_STAGES: Final[tuple[str, ...]] = (
    "hybrid_stage1",
    "bm25_only_stage2",
    "keyword_stage3",
    "parent_expand_stage4",
)
# BM25-only stage uses a separate weight knob so the operator can dial back
# the recall when the corpus is noisy. 0.0 = use the same top_k as stage 1.
DEFAULT_RETRIEVAL_BM25_STAGE_TOP_K_MULTIPLIER: Final[float] = 1.0
# Stage-3 keyword filter — pre-compiled regex pattern over `content_segmented`
# captures Vietnamese legal structural anchors (Điều / Khoản / Chương / Mục /
# Điểm). Domain-neutral: any bot whose corpus uses these markers benefits;
# others will simply see the stage return [] and fall through.
DEFAULT_RETRIEVAL_KEYWORD_STAGE_PATTERN: Final[str] = (
    r"(?i)\b(?:Điều|Khoản|Chương|Mục|Điểm)\s*\d+"
)
# Synthetic recall score the keyword stage assigns to matched chunks. Held
# DELIBERATELY BELOW ``DEFAULT_RETRIEVAL_EARLY_EXIT_THRESHOLD`` (0.35) so a
# keyword-only hit never short-circuits the chain — downstream rerank /
# grounding nodes get to vet the candidate before answer time.
DEFAULT_RETRIEVAL_KEYWORD_STAGE_SCORE: Final[float] = 0.25

# --- Metadata-aware retrieval -----------------------------------------------
# Default OFF — write-side metadata extraction also off; flipping read-only
# without re-ingest costs LLM call + 2x hybrid_search per query for no benefit.
DEFAULT_METADATA_AWARE_RETRIEVAL_ENABLED: Final[bool] = False

DEFAULT_METADATA_FALLBACK_RELAX_ENABLED: Final[bool] = True
DEFAULT_INTENT_EXTRACTOR_QUERY_PREVIEW_CHARS: Final[int] = 500
DEFAULT_INTENT_EXTRACTOR_MAX_TOKENS: Final[int] = 200

# --- VN compound segmentation at INGEST -------------------------------------
DEFAULT_VI_COMPOUND_SEGMENTATION_INGEST_ENABLED: Final[bool] = True
DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S: Final[int] = 5
# Length-based budget for sync underthesea — bounds without hard wall-clock.
DEFAULT_VI_COMPOUND_SEGMENTATION_THROUGHPUT_CHARS_PER_S: Final[int] = 200_000

# Opt-in BM25 ILIKE substring branch — cannot use GIN index, seq-scan at scale.
DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED: Final[bool] = False

# Query-time Layer-3 LLM metadata extractor (Plan 260604-metadata-aware-v4).
# OFF by default: per the deep-audit expert verdict, query-time LLM metadata
# extraction is the wrong tier (adds an LLM call + latency per query and can
# over-restrict retrieval). Ingest-side Contextual Retrieval
# (DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED) is the correct, already-on mechanism.
# Operators may opt in via system_config 'metadata_layer3_llm_enabled' after a
# per-bot A/B; until then the block is a clean no-op (no swallowed NameError).
DEFAULT_METADATA_LAYER3_LLM_ENABLED: Final[bool] = False

# Symbol/code-token phrase branch for BM25. `websearch_to_tsquery('simple', ...)`
# splits a compound like `range(5)` into the AND-term `range & 5`, and the
# surrounding natural-language words AND-restrict the predicate so the
# code-bearing chunk never matches. When the query carries a function-call /
# bracketed token, add an independent `phraseto_tsquery` OR-branch on just
# that token (`range <-> 5`) so the chunk is retrievable on the symbol alone.
DEFAULT_BM25_SYMBOL_PHRASE_ENABLED: Final[bool] = True
# Rank BOOST added to a chunk's sparse score when it matches the symbol/code
# phrase. The main rank expression scores on the full AND-query, so a chunk
# holding the exact code but NOT the surrounding words ("195/65R15 về hàng" →
# the FAQ row has the code but not "về hàng") scored 0 and was drowned by
# near-duplicate noise (e.g. a multilingual manifest whose boilerplate makes
# every chunk embed alike). A boost well above typical ts_rank values (~0.01–
# 0.3) surfaces the exact-code match to the top of the sparse arm. Technical
# score offset, domain-neutral.
DEFAULT_BM25_SYMBOL_PHRASE_RANK_BOOST: Final[float] = 1.0


# Owner-facing placeholder token the platform substitutes with captured
# conversational-action slot data (slot-filling / lead-capture). Sacred-rule
# 10: the platform ONLY substitutes structured DATA into this owner-declared
# placeholder — it never injects behavioural text or rules. A bot owner opts
# in by placing the token in ``bots.system_prompt`` (e.g. "Slot đã có:
# {captured_slots} — chỉ hỏi slot còn thiếu."). Absent token → no substitution.
ACTION_CAPTURED_SLOTS_PLACEHOLDER: Final[str] = "{captured_slots}"

# Default VN filler tokens stripped from BM25 sparse query. Override via
# system_config key 'vn_filler_tokens' (JSON array of strings). Filler
# words ("nói gì", "ra sao", "thế nào", ...) turn websearch_to_tsquery
# AND-of-N into an over-strict predicate, collapsing recall to ~0 for
# natural-language queries. Dense/embedding branch keeps original query.
DEFAULT_VN_FILLER_TOKENS_JSON: Final[str] = (
    '["nói gì","nói về gì","có gì","có những gì","là sao","ra sao",'
    '"thế nào","như thế nào","là gì","có không","không","ạ","nhé",'
    '"đi","cho","với","ơi","à","ư"]'
)

# --- Hierarchical text promotion (VN admin/legal docs) ----------------------
# Convert plain-text section markers ("Chương I", "Mục 2", "Điều 5") into
# markdown ATX headings (#/##/###) so the HDT detector can pick them up.
# Trigger condition: at least N matching lines so a casual document mentioning
# "Điều 1" in prose does not get falsely promoted.
DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES: Final[int] = 3

# --- Strategy selection weight constants ------------------------------------
DEFAULT_HDT_HEADINGS_NORM: Final[int] = 8
DEFAULT_HDT_H2_MIN_COUNT: Final[int] = 3
DEFAULT_HDT_LONG_DOC_WORDS: Final[int] = 500
DEFAULT_SEMANTIC_AVG_LEN_NORM: Final[int] = 300
DEFAULT_SEMANTIC_FEW_HEADINGS_MAX: Final[int] = 3
DEFAULT_SEMANTIC_LONG_DOC_WORDS: Final[int] = 1000
DEFAULT_RECURSIVE_TABLES_NORM: Final[int] = 3
DEFAULT_RECURSIVE_SHORT_AVG_LEN: Final[int] = 50
DEFAULT_RECURSIVE_MIXED_THRESHOLD: Final[float] = 0.2
DEFAULT_HYBRID_HEADINGS_NORM: Final[int] = 5
DEFAULT_HYBRID_MIXED_THRESHOLD: Final[float] = 0.3
DEFAULT_HYBRID_LONG_DOC_WORDS: Final[int] = 2000
DEFAULT_PROPOSITION_FEW_HEADINGS_MAX: Final[int] = 2
DEFAULT_PROPOSITION_LONG_DOC_WORDS: Final[int] = 1500

# --- AdapChunk Layer 5 — Rule Cross-check (S3, T1-Smartness) ---------------
# Post-selector safety net. After ``select_strategy()`` returns its best
# (strategy, confidence), the cross-check applies 5 override rules backed
# by the AdapChunk Layer-5 blueprint (rule-based corrections that catch
# known confidence-scoring blindspots) plus the Databricks AI-Driven
# "simple fallback" pattern (when confidence is too low, fall back to a
# defensive strategy rather than commit to a brittle one).
#
# Inspired by:
# - AdapChunk Layer 5 internal blueprint (PhD private, not yet published) —
#   concept inspiration only; the conditions below are platform-tuned for
#   the dict-based profile shape this codebase produces.
# - Databricks AI-Driven Chunking blog (2024): simple fallback to hybrid.
# - Ekimetrics — Adaptive Chunking, LREC 2026 (peer-reviewed): proven
#   benefit of post-selector adjustment via RC/ICC/DCC/BI/SC signals.
#
# Default OFF (Quality-Gate "no behaviour change without opt-in"). Operator
# flips ``adapchunk_layer5_cross_check_enabled`` in ``system_config`` to
# activate; each threshold below is independently overridable so ops can
# tune without redeploy.
# DEPRECATED 2026-05-14 AdapChunk-reorg: Layer 5 cross-check active by default.
# Code merged sprint3-l5-crosscheck a6ff98a. Default OFF for safe rollout;
# reorg flips ON because 5 rule conditions are production-ready + audit-logged.
# DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED: Final[bool] = False
DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED: Final[bool] = True
# Confidence floor — below this the selector's pick is considered too weak
# to trust; fall back to the defensive "hybrid" strategy at 0.6 confidence.
DEFAULT_ADAPCHUNK_L5_CONFIDENCE_THRESHOLD: Final[float] = 0.6
# Min headings required to honour an "hdt" pick. Below = downgrade to
# "semantic" (HDT without enough structure degrades to noise).
DEFAULT_ADAPCHUNK_L5_HDT_MIN_HEADINGS: Final[int] = 5
# Min avg block length to honour a "semantic" pick. Below = upgrade to
# "proposition" (short blocks are clauses, not paragraphs).
DEFAULT_ADAPCHUNK_L5_SEMANTIC_MIN_AVG_BLOCK_LEN: Final[int] = 50
# Long-structured doc indicator: paragraphs > X words AND headings > Y
# means "proposition" is wrong (over-fragmentation); use "hdt" instead.
DEFAULT_ADAPCHUNK_L5_PROPOSITION_MAX_AVG_BLOCK_LEN: Final[int] = 300
DEFAULT_ADAPCHUNK_L5_PROPOSITION_MAX_HEADINGS: Final[int] = 20
# Mixed-content warn threshold (warn-only, no override): when the doc has
# heavy table/code mixing but the selector did NOT pick "hybrid", log a
# warning so operators can audit and tune weights. NEVER overrides — Quality
# Gate #10 forbids application overriding deliberate selector output here.
DEFAULT_ADAPCHUNK_L5_MIXED_CONTENT_WARN_THRESHOLD: Final[float] = 0.4
# Fallback confidence assigned to every override. Mid-band 0.6-0.7 reflects
# "we corrected on rule basis, not strong statistical match".
DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_FALLBACK: Final[float] = 0.6
DEFAULT_ADAPCHUNK_L5_OVERRIDE_CONFIDENCE_RULE: Final[float] = 0.7

# AdapChunk reorg Wave B2 — Block pipeline (Layer 2 → 3 → 4 → 5 → 6) opt-in.
# When ``True``, ``document_service.ingest()`` routes the table-aware chunking
# branch through a Block-aware pipeline (``attach_context_buffer`` →
# ``analyze_document`` → ``select_strategy`` → ``apply_cross_check`` →
# ``smart_chunk``) that preserves atomic-block semantics end-to-end. When
# ``False`` (default), the legacy text-flatten path runs unchanged — same
# ``promote_vn_hierarchical_headings`` → ``smart_chunk`` ordering as before.
#
# Default ON: the deps (atomic-chunking signature + ``analyze_document_blocks``)
# have landed (default==happy). Degrades gracefully to the text-flatten
# primitives, so the smart path carries no breakage risk; the flag stays the
# per-bot kill-switch.
DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED: Final[bool] = True


# Per-strategy term weights (must sum to 1.0 per strategy).
DEFAULT_STRATEGY_WEIGHTS: Final[dict[str, dict[str, float]]] = {
    "hdt": {
        "headings_norm": 0.40,
        "has_toc": 0.35,
        "has_h2_group": 0.15,
        "is_long_doc": 0.10,
    },
    "semantic": {
        "avg_len_norm": 0.50,
        "few_headings": 0.25,
        "no_tables": 0.15,
        "is_long_doc": 0.10,
    },
    "recursive": {
        "base": 0.30,
        "tables_norm": 0.35,
        "short_avg_len": 0.20,
        "mixed_content": 0.15,
    },
    "hybrid": {
        "headings_norm": 0.25,
        "mixed_content": 0.25,
        "is_long_doc": 0.25,
        "avg_len_norm": 0.25,
    },
    "proposition": {
        "avg_len_norm": 0.40,
        "few_headings": 0.20,
        "is_long_doc": 0.20,
        "no_tables": 0.20,
    },
}

# --- DeepEval thresholds ----------------------------------------------------
DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD: Final[float] = 0.85
DEFAULT_DEEPEVAL_RELEVANCY_THRESHOLD: Final[float] = 0.80
DEFAULT_DEEPEVAL_PRECISION_THRESHOLD: Final[float] = 0.75
DEFAULT_DEEPEVAL_RECALL_THRESHOLD: Final[float] = 0.75
DEFAULT_DEEPEVAL_JUDGE_MODEL: Final[str] = "gpt-4.1-mini"
DEFAULT_DEEPEVAL_SMOKE_N: Final[int] = 5
# Sanity floor — non-zero judge response means chain wired correctly.
DEFAULT_DEEPEVAL_SMOKE_FAITHFULNESS_FLOOR: Final[float] = 0.5

# --- Document Parser Strategy registry --------------------------------------
DEFAULT_DOCUMENT_PARSER_PROVIDER: Final[str] = "null"
DEFAULT_EXCEL_HEADER_ROW_INDEX: Final[int] = 0

