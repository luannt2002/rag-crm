from __future__ import annotations
from typing import Final  # noqa: F401
from ._14_anti_abuse_ip_rate_limit_hon import *  # noqa: F401,F403

# --- M2 Neighbor Window Expansion (RAG-Anything mindset, Wave 3) ------------
# Inspired by LightRAG ``local_query`` (HKUDS, 2025): after the retrieve
# stage produces top-K child chunks, expand each seed chunk with ±N
# adjacent siblings inside the same document to restore paragraph-level
# context for the LLM **without** issuing another LLM/embedding call.
# Distinct from auto-merge (which collapses children into a parent block)
# and from parent-child (which swaps every child for its parent). Neighbor
# expansion preserves the seed chunk and merely *appends* surrounding rows
# by ``chunk_index``. Cost = 1 SQL round-trip per request; HALLU=0 sacred
# is preserved because no new content is fabricated — only existing rows
# from the same tenant/document are surfaced.
#
# Per-bot opt-in via ``bots.plan_limits.neighbor_expand_enabled``. Legacy
# bots keep flat top-K. M22 (``DEFAULT_NEIGHBOR_TOKEN_BUDGET``) caps the
# total expanded payload to keep the LLM context window bounded.
DEFAULT_NEIGHBOR_EXPAND_ENABLED: Final[bool] = False
# Symmetric window radius. ``n=1`` fetches one neighbor on each side of a
# seed chunk (3 chunks total per seed before dedupe). ``n=0`` is a no-op.
# Bound to a small integer to avoid runaway context inflation; tuners can
# bump per-bot but the system default stays conservative.
DEFAULT_NEIGHBOR_WINDOW_SIZE: Final[int] = 1
# M22: hard ceiling on the total post-expansion payload, measured in
# approximate tokens (chars / DEFAULT_CHARS_PER_TOKEN_ESTIMATE). Stops
# the expansion loop once the cumulative budget is exhausted; remaining
# neighbours are dropped in document/chunk-index order so the seeds win
# the budget race.
DEFAULT_NEIGHBOR_TOKEN_BUDGET: Final[int] = 2000
# Per-doc concurrency bound for the neighbor SQL fan-out. Each unique
# document_id maps to one SQL query inside the expand node; the semaphore
# keeps the worker pool from exhausting connections under high QPS.
DEFAULT_NEIGHBOR_MAX_CONCURRENCY: Final[int] = 4
# Char-to-token approximation used by the budget loop. Tokenizer-free
# heuristic that holds within ±15 % across English + Vietnamese mixed
# corpora; precise tokenisation here would require per-bot tokenizer
# wiring, which is overkill for a coarse budget guard.
DEFAULT_CHARS_PER_TOKEN_ESTIMATE: Final[int] = 4

# --- M11 Blocks API (RAG-Anything mindset, Wave 3) --------------------------
# When True, retrieve/rerank/mmr stages may wrap their output dict-chunks
# in the ``application.dto.block.Block`` dataclass. Backward-compat is
# preserved through ``Block.__getitem__`` and ``Block.get`` — legacy node
# code that does ``chunk["content"]`` continues to work without edit.
# Default OFF so production paths stay byte-identical until tests bake.
DEFAULT_BLOCKS_API_ENABLED: Final[bool] = False

# --- M17 Modality-Aware Reranking (RAG-Anything mindset, Wave 3) ------------
# Multiplicative score boost applied **post-rerank** when a chunk's
# ``chunk_type`` matches the query intent's preferred modality. Helper
# lives at ``infrastructure/reranker/_modality_boost.py``; it does NOT
# replace the reranker strategy (Port preserved) — only adjusts scores
# the reranker already emitted. HALLU=0 sacred: boost cannot fabricate
# content, only re-order existing candidates.
#
# Per-bot opt-in via ``bots.plan_limits.modality_rerank_enabled``. When
# True the boost map ``{intent}:{chunk_type} → multiplier`` consults the
# defaults below; bot owners can override individual entries via
# ``plan_limits.modality_boost_overrides`` (dict[str, float]).
DEFAULT_MODALITY_RERANK_ENABLED: Final[bool] = False
# Boost factor when the query is intent ``table_lookup`` / ``list_lookup``
# / ``comparison`` and the chunk's ``chunk_type`` is ``table`` /
# ``table_row``. 1.2× chosen conservatively — large enough to pull a
# table above a near-tie text neighbour, small enough that an irrelevant
# table cannot displace a clear text winner. Mirrors LightRAG's
# "prefer-table-for-numeric-intents" heuristic.
DEFAULT_MODALITY_BOOST_TABLE_LOOKUP: Final[float] = 1.2
# Boost factor when the query is intent ``code_lookup`` / ``how_to`` and
# the chunk's ``chunk_type`` is ``code``. Higher than table (1.3×) because
# code blocks are typically isolated answers and a near-miss text snippet
# rarely substitutes for the actual code.
DEFAULT_MODALITY_BOOST_CODE_LOOKUP: Final[float] = 1.3
# Identity multiplier — returned by the helper when no intent/type pair
# matches the boost map. Kept named (not inline ``1.0``) so it can be
# tuned per-bot if a no-modality-preference posture is desired.
DEFAULT_MODALITY_BOOST_IDENTITY: Final[float] = 1.0

# --- M21 Deterministic Chunk UUID5 (RAG-Anything mindset, Wave 3) -----------
# When True, the ingest path stamps each chunk with a deterministic
# UUID5 derived from ``(record_bot_id, document_id, content.strip())``
# instead of ``uuid.uuid4()``. Re-ingest of the same document with
# byte-identical content yields the same UUIDs → DB UPSERT replaces in
# place, no duplicate row + no dangling FK from caches. Default OFF
# preserves backward compatibility (legacy ``uuid.uuid4()`` path).
#
# Per-bot opt-in via ``bots.plan_limits.chunk_hash_id_enabled``. Toggling
# True for an existing corpus is benign — new ingests use UUID5, old
# rows keep their UUID4. Only when the *same* doc is re-ingested do the
# new UUID5 values supplant prior UUID4 rows (different keys, treated as
# fresh inserts by the persist layer).
DEFAULT_CHUNK_HASH_ID_ENABLED: Final[bool] = False

# --- M23 Content-Type Dispatch Routing (RAG-Anything mindset, Wave 3) -------
# When True, the ingest path emits a per-block-type histogram
# (``blocks_by_type``) as a structlog event + request_steps metadata
# field. Pure observability — no chunking behaviour changes. Lays the
# groundwork for per-type strategy routing (Wave 4) without committing
# to the routing logic yet. Default ON because the cost is one Counter
# pass over already-computed blocks.
DEFAULT_CONTENT_TYPE_DISPATCH_ENABLED: Final[bool] = True

# Permissive — false-positive ungrounded > false-negative ungrounded.
DEFAULT_GROUNDING_CHECK_THRESHOLD: Final[float] = 0.3

# Min score for guard_output rule hits to trigger a block (0–1 scale).
# Below this score the hit is logged but not escalated to a block action.
DEFAULT_GUARD_OUTPUT_MIN_SCORE: Final[float] = 0.5

# guard_output intent gating — only retrieval-bearing intents need grounding.
DEFAULT_GROUNDING_INTENTS: Final[tuple[str, ...]] = (
    "factoid",
    "comparison",
    "aggregation",
    "multi_hop",
)
# Whether the stats/structured-index route SKIPS the grounding judge. Default
# False = grounding ALSO applies to stats answers (HALLU-safe). Historically the
# stats route skipped grounding to avoid false-blocking reformatted numbers, but
# that let an answer cite a value NOT present in the matched entity (e.g. a
# stock number leaked from history) pass unchecked. Owners who hit false-blocks
# on legitimately-reformatted structured answers can re-enable the skip per-bot.
DEFAULT_STATS_ROUTE_SKIP_GROUNDING: Final[bool] = False
# Generate-node SLA — drift surfaces as ops warning before user-visible p95.
DEFAULT_GENERATE_P95_SLA_MS: Final[int] = 8000
DEFAULT_MAX_REFLECT_RETRIES: Final[int] = 1

# --- Ablation reporting thresholds ------------------------------------------
# Used by ``scripts/feature_ablation_report.py`` to classify A/B pilot deltas
# into KEEP / TUNE / DROP buckets. HALLU breach is sacred (delta>0 → DROP).
DEFAULT_ABLATION_KEEP_PASS_LIFT_PP: Final[float] = 5.0
DEFAULT_ABLATION_KEEP_LATENCY_DROP_PCT: Final[float] = 10.0
DEFAULT_ABLATION_KEEP_COST_DROP_PCT: Final[float] = 10.0

# Smart-skip reflect retry knob (T2 perf). When True, the reflect node
# bypasses the rewrite request from the Self-RAG judge if (a) the
# grounding-check guardrail did not fire on pass-1 (answer is grounded in
# retrieved chunks) AND (b) the pass-1 top retrieval score is at or above
# ``DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR``. Default False keeps legacy retry
# behaviour. Bot owner enables per-domain via
# ``plan_limits.reflect_skip_if_grounded``.
DEFAULT_REFLECT_SKIP_IF_GROUNDED: Final[bool] = False

# Top-score floor used by the reflect smart-skip gate. A grounded answer with
# a top retrieval score below this floor is still a candidate for the retry
# pass — the judge requested rewrite because the answer is thin even when
# the citations check out. Mirrors the cliff-strategy floor distribution
# (Jina v3 + cross-encoder).
DEFAULT_REFLECT_SKIP_TOP_SCORE_FLOOR: Final[float] = 0.30

# --- Loadtest refuse-pattern fragments --------------------------------------
# Single source of truth for refuse-detection used by all load-test harnesses.
# Test-side classification only — never feeds the production pipeline.
DEFAULT_LOADTEST_REFUSE_PATTERNS: Final[tuple[str, ...]] = (
    "kiểm tra.*chuyên viên",
    "cần check lại",
    "chưa có thông tin",
    "không có thông tin",
    "chưa hỗ trợ",
    "không hỗ trợ",
    "liên hệ.*tư vấn",
    "để em kiểm tra",
    "em chưa rõ",
    "chỗ này em chưa thấy",
    "em chưa thấy thông tin",
    "trong tài liệu.*chưa có",
    "tài liệu.*không có",
    "em chỉ có thông tin",
    "trong tài liệu hiện có",
    "tài liệu hiện có em chưa",
    "chưa có trong tài liệu",
    "tài liệu không có",
    "check lại với",
    "không có quyền truy cập",
    "không được phép",
    "không thể truy cập",
    "rất tiếc.*không.*quyền",
    "liên hệ trực tiếp",
    "liên hệ hotline",
    "vui lòng liên hệ hotline",
    "xin lỗi.*không",
    "không tìm thấy",
    "ngoài phạm vi",
    "ngoài khả năng",
    "không thuộc",
    "không thể",
    "chuyên về.*dịch vụ",
    # Anchor on apologetic "em chỉ" — avoid false-positive on bot CTAs.
    "em chỉ.*hỗ trợ.*dịch vụ",
    "tôi chỉ.*tư vấn",
    "chỉ có thể tư vấn",
    "không có trong",
    "không nằm trong",
    "chưa được cập nhật",
    "tôi không",
)

# --- Auto-FAQ candidate generator -------------------------------------------
# Min cluster size before a candidate is surfaced for operator review.
DEFAULT_FAQ_MIN_OCCURRENCES: Final[int] = 3
# Cosine threshold for grouping near-paraphrase questions.
DEFAULT_FAQ_CLUSTER_SIMILARITY: Final[float] = 0.85


# --- Prompt-compression / preview-truncation --------------------------------
DEFAULT_PROMPT_COMPRESSION_ENABLED: Final[bool] = True
DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK: Final[int] = 500

# Boot-fallback boilerplate regex patterns (Vietnamese + common web artifacts).
# These are raw strings — caller compiles at resolve time so DB-loaded
# `system_config.boilerplate_removal_patterns_by_language.<lang>` can replace
# them without a redeploy. Per CLAUDE.md domain-neutral rule: this is a SEED
# default only; per-bot override lives in `bots.custom_vocabulary.boilerplate_patterns`,
# tenant-global override in `system_config`.
DEFAULT_BOILERPLATE_PATTERNS_VI: Final[tuple[str, ...]] = (
    r"Xem thêm(?:\s+tại)?[:\s].*",
    r"Nguồn\s*:.*",
    r"Click\s+(?:here|vào đây).*",
    r"Đọc thêm\s*:.*",
    r"Tham khảo\s*:.*",
    r"(?:Bài viết|Tin) liên quan\s*:?.*",
    r"Tags?\s*:.*",
    r"Share\s*(?:this|bài|:).*",
    r"Copyright\s*©.*",
    r"All rights reserved.*",
    r"^\s*#+\s*$",  # empty markdown headers
)

# Boot-fallback Vietnamese stop-word list for sentence-info scoring.
# Negation words intentionally excluded (they flip meaning — see
# `prompt_compression._NEGATION_WORDS`). Per-bot override in
# `bots.custom_vocabulary.stopwords`, tenant-global in
# `system_config.stopwords_by_language.<lang>`.
DEFAULT_VI_STOPWORDS: Final[tuple[str, ...]] = (
    "là", "và", "của", "có", "được", "cho", "với", "trong", "này", "đó",
    "để", "từ", "một", "các", "cũng", "như", "đã", "sẽ", "khi", "tại",
    "đến", "hay", "hoặc", "nếu", "thì", "mà", "về", "bị", "vì", "trên",
    "dưới", "ngoài", "sau", "trước", "giữa", "theo", "bao", "nhiêu",
    "những", "rằng", "lại", "còn", "đang", "ở", "hơn", "nhất", "ra",
    "vào", "nên", "rất", "ai", "gì", "nào", "đây", "kia", "ấy", "thế",
    "mỗi", "vẫn", "chỉ", "do", "bởi",
)

