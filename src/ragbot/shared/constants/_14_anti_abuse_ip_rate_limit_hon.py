from __future__ import annotations
from typing import Final  # noqa: F401
from ._13_adapchunk_ocr_parser import *  # noqa: F401,F403

# --- Anti-abuse + IP rate limit + honeypot ---------------------------
ANTI_ABUSE_SUSPICIOUS_IP_REDIS_KEY: Final[str] = "ragbot:antiabuse:suspicious_ips"
DEFAULT_ANTI_ABUSE_AUTH_FAIL_THRESHOLD: Final[int] = 10
DEFAULT_ANTI_ABUSE_AUTH_FAIL_WINDOW_S: Final[int] = 300
DEFAULT_ANTI_ABUSE_BAN_DURATION_S: Final[int] = 3600
DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_PER_MIN: Final[int] = 30
DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_TTL_PADDING_S: Final[int] = 1
DEFAULT_ANTI_ABUSE_DISTINCT_PATHS_WINDOW_S: Final[int] = 60
DEFAULT_ANTI_ABUSE_HONEYPOT_TTL_S: Final[int] = 86_400
DEFAULT_ANTI_ABUSE_SUSPICIOUS_RL_MULTIPLIER: Final[float] = 0.25
# 4xx-rate ratio gate — flags an IP that exceeds the threshold AFTER at
# least the window-requests count of attempts (small samples are noisy).
DEFAULT_ANTI_ABUSE_4XX_RATIO_THRESHOLD: Final[float] = 0.5
DEFAULT_ANTI_ABUSE_4XX_WINDOW_REQUESTS: Final[int] = 20
# Loadtest bypass — enables localhost-originated load probes to skip the
# 4xx-ratio counter and the IP rate-limit cap WITHOUT disabling auth or
# the UA denylist. Header carries an opaque token; the secret lives in
# the operator's env so production never sets the env var (token compare
# fails-closed when env is empty).
RAGBOT_LOADTEST_BYPASS_HEADER: Final[str] = "X-Ragbot-Loadtest-Bypass"
RAGBOT_LOADTEST_BYPASS_ENV: Final[str] = "RAGBOT_LOADTEST_BYPASS_TOKEN"
DEFAULT_HONEYPOT_PATHS: Final[tuple[str, ...]] = (
    "/wp-admin",
    "/wp-login.php",
    "/.env",
    "/.git/config",
    "/admin/login.php",
    "/phpmyadmin",
    "/.aws/credentials",
)
DEFAULT_IP_RL_BYPASS_PATHS: Final[tuple[str, ...]] = (
    "/health",
    "/health/models",
    "/metrics",
    "/static",
    "/docs",
    "/redoc",
    "/openapi.json",
)
DEFAULT_RL_IP_PER_MIN: Final[int] = 100
DEFAULT_RL_IP_WINDOW_S: Final[int] = 60
# Combined per-IP + per-token gate (SEC-INJ-8 hardening). The per-token
# limiter caps an individual JWT bearer; the per-IP limiter caps every
# bearer that originates from a single source IP. Both must pass.
# 300/min default sized for ~5 concurrent users behind a single NAT at
# the per-token DEFAULT_RL_CHAT_PER_MIN=60 cap (5 × 60 = 300). Bursts
# (browser page-load fan-out) get DEFAULT_RATE_LIMIT_BURST_IP_MULTIPLIER
# headroom inside the first burst window.
DEFAULT_RATE_LIMIT_PER_IP_PER_MIN: Final[int] = 300
DEFAULT_RATE_LIMIT_BURST_IP_MULTIPLIER: Final[float] = 2.0
DEFAULT_UA_DENYLIST_PATTERNS: Final[tuple[str, ...]] = (
    "curl/", "wget/", "python-requests/", "scrapy", "go-http-client",
)
# UA denylist enforcement is scoped to *user-conversation* hot endpoints
# only. Ops/admin/auth/health endpoints rely on RBAC + IP rate-limit +
# auth-fail ban for protection — UA blocking here would punish legit
# operator scripts (curl smoke tests, deploy probes, monitoring) without
# adding security beyond what the harder layers already provide.
DEFAULT_UA_DENYLIST_ENFORCED_PREFIXES: Final[tuple[str, ...]] = (
    "/api/ragbot/test/chat",
    "/api/ragbot/chat",
    "/api/ragbot/sync",
)
# /sync upstream HMAC signing — single canonical algorithm so caller +
# verifier never disagree on a bytes-equality compare.
DEFAULT_SYNC_HMAC_ALGORITHM: Final[str] = "sha256"

# --- Pipeline Auditor Logger ------------------------------------------------
DEFAULT_PIPELINE_AUDIT_LOGGER_ENABLED: Final[bool] = False
DEFAULT_PIPELINE_AUDIT_LOG_DIR: Final[str] = "reports"
# Per-file size cap; once exceeded next event rolls into _part2 etc.
DEFAULT_PIPELINE_AUDIT_MAX_FILE_BYTES: Final[int] = 10_000_000
DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_HEAD: Final[int] = 100
DEFAULT_PIPELINE_AUDIT_CHUNK_PREVIEW_TAIL: Final[int] = 50
DEFAULT_PIPELINE_AUDIT_RETRIEVAL_PREVIEW: Final[int] = 200
DEFAULT_PIPELINE_AUDIT_SERIALISE_ERROR_CAP: Final[int] = 500

# --- Structured Output (provider-enforced JSON schema) ----------------------
DEFAULT_STRUCTURED_OUTPUT_ENABLED: Final[bool] = True
DEFAULT_GRADE_USE_STRUCTURED_OUTPUT: Final[bool] = True
DEFAULT_REFLECT_USE_STRUCTURED_OUTPUT: Final[bool] = True
DEFAULT_DECOMPOSE_USE_STRUCTURED_OUTPUT: Final[bool] = True
DEFAULT_UNDERSTAND_USE_STRUCTURED_OUTPUT: Final[bool] = True
DEFAULT_GENERATE_USE_STRUCTURED_OUTPUT: Final[bool] = True
# Cap on citations LLM may emit per answer.
DEFAULT_GENERATE_CITATIONS_MAX_N: Final[int] = 8
DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS: Final[int] = 200
# Single batched grade call instead of N parallel calls.
DEFAULT_GRADE_USE_BATCH: Final[bool] = True
DEFAULT_UNDERSTAND_CONDENSED_QUERY_MAX_LEN: Final[int] = 1000
DEFAULT_UNDERSTAND_CONDENSED_QUERY_AUDIT_PREVIEW_LEN: Final[int] = 300
DEFAULT_LLM_REASON_MAX_LEN: Final[int] = 500
DEFAULT_DECOMPOSE_MAX_SUB_QUERIES: Final[int] = 5
DEFAULT_DECOMPOSE_TOP_K_PER_SUBQUERY: Final[int] = 12
# Cap on sub-queries PARSED from an LLM decompose payload (defensive truncation
# of a malformed/oversized response). Zero-hardcode lift 2026-06-13 of the
# inline ``max_sub=4`` default param — kept at 4 (behaviour-neutral; this is the
# parse-side guard, distinct from the generate-side DEFAULT_DECOMPOSE_MAX_SUB
# above which bounds how many the LLM is asked to produce).
DEFAULT_PARSE_DECOMPOSED_MAX_SUB: Final[int] = 4
# V6 gate: queries below this token-count rarely benefit from decompose
# (single-fact, no-conjunction). Skip → save 1 LLM call (~1.5s p95 trim).
DEFAULT_DECOMPOSE_MIN_TOKENS: Final[int] = 8
# confidence gate: skip decompose when classifier confidence is low
# (multi_hop intent reported with confidence < gate → fan-out 5 sub-queries
# is wasteful, fall through to single-query retrieve). Default 0.7 mirrors
# the BM25/RRF "high-precision" heuristic threshold from MIRACL 2025.
DEFAULT_DECOMPOSE_CONFIDENCE_GATE: Final[float] = 0.7
# fallback when LLM omits confidence — neither high enough to
# trigger decompose nor low enough to skip rewrite/retrieve gates.
DEFAULT_INTENT_CONFIDENCE_FALLBACK: Final[float] = 0.5

# --- Adaptive Router L1 (query complexity classifier) -----------------------
# Domain-neutral regex/heuristic detector. Score = sum of weighted signals:
#   comma list, conjunction tokens (multi-language list from system_config),
#   numeric tokens (multi-entity hint), multi-question marks, length bonus.
# Score >= threshold → "complex" → Layer 3 decomposer fires; else "simple".
DEFAULT_QUERY_COMPLEXITY_WEIGHT_COMMA: Final[float] = 0.5
DEFAULT_QUERY_COMPLEXITY_WEIGHT_CONJUNCTION: Final[float] = 0.4
DEFAULT_QUERY_COMPLEXITY_WEIGHT_NUMBERS: Final[float] = 0.3
DEFAULT_QUERY_COMPLEXITY_WEIGHT_QUESTION: Final[float] = 0.6
DEFAULT_QUERY_COMPLEXITY_LENGTH_NORMALIZER: Final[float] = 20.0
DEFAULT_QUERY_COMPLEXITY_THRESHOLD: Final[float] = 1.2
# Structural-reference early-exit: a short single-entity structural/legal
# lookup ("Điều 55 ...", "Chương III ...") is a straight retrieve, not a
# multi-entity fan-out. Without this, the article number inflates the
# numeric weight → mis-route to the LLM decomposer, whose paraphrase
# variants drop the structural anchor (UI trace 2026-05-27, a legal-corpus
# Chương 3 lookup → refuse SAI). Keyword group is case-insensitive (`(?i:...)`) and
# multi-language (VN + EN structural words — NOT domain/brand tokens); the
# Roman-numeral branch is uppercase-only so legal forms ("Chương III") match
# while lowercase nouns ("Điều lệ") do NOT false-trigger. Arabic branch
# matches any case. The gate (commas/conjunction/length) below keeps genuine
# compound multi-entity queries on the standard scoring path.
DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_REF_PATTERN: Final[str] = (
    r"\b(?i:điều|khoản|điểm|chương|mục|tiết|phần|article|section|clause|"
    r"chapter|paragraph|part)\s*\.?\s*(?:\d+|[IVXLCDM]+)\b"
)
# Max chars for the structural early-exit: longer queries are compound and
# keep the standard classifier even if they mention a structural anchor.
DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_MAX_CHARS: Final[int] = 80
# JSON-encoded list of conjunction tokens (multi-language). DOMAIN-NEUTRAL:
# do NOT add domain-specific terms ("sản phẩm", "Điều"); only linguistic
# conjunctions across the languages the platform supports.
DEFAULT_QUERY_COMPLEXITY_CONJUNCTIONS_JSON: Final[str] = (
    '["và","hoặc","and","or","&","+","cùng","with"]'
)

# --- Adaptive Router L3 (LLM decomposer) ------------------------------------
# Default-on toggle. When complex queries are detected by L1, the decomposer
# splits them into atomic sub-questions and writes ``sub_queries`` into the
# graph state; the existing fanout (S2 bypass) consumes that contract.
# Admin override 2026-05-12: decomposer.model defaults to gpt-4.1-mini
# (Haiku banned per user direction). Bot owner may override per-tenant in
# system_config.
DEFAULT_DECOMPOSER_ENABLED: Final[bool] = True
DEFAULT_DECOMPOSER_MODEL: Final[str] = "gpt-4.1-mini"
DEFAULT_DECOMPOSER_MAX_TOKENS: Final[int] = 300
DEFAULT_DECOMPOSER_MAX_SUB_QUERIES: Final[int] = 8
# Adaptive Router master toggle. When True (default), the L1 classifier
# fires after understand_query; when False, the legacy
# understand_query → router path is byte-identical to the pre-S6 behaviour.
# Per-bot override via pipeline_config["adaptive_router_l1_enabled"].
DEFAULT_ADAPTIVE_ROUTER_L1_ENABLED: Final[bool] = True
# OpenAI provider codes that support response_format json_schema.
OPENAI_STRUCTURED_OUTPUT_PROVIDER_CODES: Final[tuple[str, ...]] = (
    "openai",
    "azure",
    "azure_ai",
)
# Maximum entries cached by the per-Pydantic-class JSON-schema build memo
# (model_json_schema + harden walk). The structured pipeline reuses a small
# fixed set of schemas (grade / reflect / decompose / understand / generate
# plus a few node-internal subtypes); the LRU just needs headroom above that
# steady-state set so churn is rare.
DEFAULT_STRUCTURED_OUTPUT_SCHEMA_CACHE_SIZE: Final[int] = 64
# Bounded repair retries for structured output: when the first response fails
# schema validation the helper re-issues the call ONCE with an appended repair
# instruction. 0 disables repair; values >1 are intentionally not honoured —
# the helper caps at a single extra round-trip so a flaky model can't loop.
DEFAULT_STRUCTURED_OUTPUT_REPAIR_RETRIES: Final[int] = 1

# NFC preserves diacritics; NFKC over-normalizes (destroys technical Unicode).
DEFAULT_NORMALIZATION_FORM: Final[str] = "NFC"

# --- Grounding pre-check ----------------------------------------------------
# Min substring length for verbatim grounding match (skip NLI on hit).
DEFAULT_GROUNDING_SUBSTRING_MIN: Final[int] = 20
# Numeric overlap pre-check — every digit sequence ≥2 chars must appear in chunks.
DEFAULT_GROUNDING_NUMERIC_OVERLAP_ENABLED: Final[bool] = True
DEFAULT_GROUNDING_CHECK_ENABLED: Final[bool] = True
DEFAULT_GROUNDING_USE_STRUCTURED: Final[bool] = True

# B5 async grounding — opt-in per-bot. When True AND intent is in the
# async-eligible set AND the pass-1 top retrieval score clears the floor,
# guard_output schedules the LLM grounding judge as a background task
# (``asyncio.create_task``) and returns immediately. Breach (judge ratio
# exceeds threshold) is logged via structlog with the request_id +
# tenant_id + bot_id payload and increments ``grounding_fail_total`` so the
# alerting pipeline (Prometheus → Alertmanager) picks it up out-of-band.
# Default False preserves HALLU=0 sacred — bot owner must opt in explicitly.
# Rollback rule: if grounding_fail_total breach > 0 / week post-enable, flip
# back to False and post-mortem before re-enabling.
#
# DEFAULT = False (HALLU=0 sacred). Async grounding ships the answer BEFORE the
# grounding judge completes (breach only logged after), so a not-yet-grounded
# answer can reach the user. CORE MVP priority puts T1 (no-hallu) above T2
# (the -1.5s latency win), so the SAFE default is sync grounding; the async
# perf path is per-bot opt-in (plan_limits.grounding_check_async_enabled) after
# the owner accepts the rollback rule. A prod deployment that already enabled it
# in system_config keeps that DB value (DB override wins); this constant only
# governs the code-level fallback for new bots/deployments.
DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED: Final[bool] = False
# Top retrieval score floor for async eligibility. Below this floor the
# request is considered low-confidence and the sync path runs (block on
# breach). At/above the floor the async path is allowed. 0.7 chosen
# conservatively: gray-zone retrievals (0.18..0.30) and mid-quality
# retrievals (0.30..0.70) still pay the sync grounding cost.
DEFAULT_GROUNDING_CHECK_ASYNC_TOP_SCORE_THRESHOLD: Final[float] = 0.7
# Intents allowed to take the async path. Factoid only by default — narrow
# slice with the highest retrieval-driven HALLU floor; comparison /
# aggregation / multi_hop keep the sync path until factoid bakes clean.
DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS: Final[tuple[str, ...]] = ("factoid",)

# MMR diversity filter: True=cosine semantic, False=trigram Jaccard fallback.
DEFAULT_MMR_USE_COSINE: Final[bool] = True
# 0.88→0.98 (002-D, 2026-07-04): 0.88 was calibrated for the pre-zembed-1
# embedding distribution and wrongly deduped 100% of distinct-section pairs
# on the measured corpus (threshold-drift-post-migration lesson).
# Pairwise similarity above which two MMR-candidates are dropped as dupes.
DEFAULT_MMR_SIMILARITY_THRESHOLD: Final[float] = 0.98
# 002-D survivor floor: mmr_filter never collapses below this many chunks —
# measured (specs/002 evidence): zembed-1 same-doc distinct-section cosine
# p50 0.975/max 0.990 overlaps the near-dup band, no threshold separates them;
# the floor (not the ceiling) is what protects sectioned-document answers.
DEFAULT_MMR_MIN_KEEP: Final[int] = 3
# score = lambda * relevance - (1-lambda) * redundancy.
DEFAULT_MMR_LAMBDA: Final[float] = 0.7

# Per-intent MMR similarity threshold (260525 Bug #10). Default 0.88 is
# too aggressive for ``aggregation`` queries: row-shape CSV chunks with
# the same column structure but different data values (e.g. "Item A,123000"
# vs "Item B,123000") share template-level similarity but represent
# DISTINCT entities. MMR-dedup collapses them into one chunk → the LLM
# only sees one example → "1 dịch vụ" answer where 4 was expected.
#
# Per-intent threshold loosens dedup for aggregation (only drop near-
# identical pairs at 0.98) and comparison / multi_hop (0.95). Factoid /
# greeting keep 0.88 — those intents benefit from aggressive dedup.
#
# Resolution order: ``plan_limits.mmr_similarity_threshold_by_intent`` >
# ``system_config.mmr_similarity_threshold_by_intent`` > this constant.
# Unknown intent falls back to ``DEFAULT_MMR_SIMILARITY_THRESHOLD``.
DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT: Final[dict[str, float]] = {
    "factoid": 0.88,
    "comparison": 0.95,
    "multi_hop": 0.95,
    "aggregation": 0.98,
    "out_of_scope": 0.88,
    "greeting": 0.88,
    "feedback": 0.88,
    "chitchat": 0.88,
    "vu_vo": 0.88,
}

# --- Auto-Merge Retrieval (HiChunk Tencent pattern) -------------------------
# Default OFF (Null Object). When opted in via
# ``system_config.auto_merge_retrieval_enabled`` (tenant-wide) or
# ``bots.pipeline_config.auto_merge_retrieval_enabled`` (per-bot), the
# retrieve stage collapses sibling child chunks into their shared parent
# when ``parent_chunk_id`` is the same for at least
# ``DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD`` retrieved children. Reduces
# context fragmentation on long-form docs (legal/medical/standards) where
# the answer needs the surrounding paragraph, not just isolated sentences.
#
# Citation: Lu, Cao, Wang et al. "HiChunk: Hierarchical Chunking for
# Retrieval-Augmented Generation", arXiv:2509.11552 (2025-09). Reported
# +7pp evidence recall on long-document QA vs flat top-K (81% vs 74%).
# Distinct from ``parent_child_enabled`` (which swaps EVERY child for its
# parent regardless of sibling count) and from ``parent_expand_stage4``
# (which APPENDS parents to the candidate pool). Auto-merge fires only
# when the retriever already concentrated multiple hits on one parent —
# the signal the answer needs the wider block.
DEFAULT_AUTO_MERGE_RETRIEVAL_ENABLED: Final[bool] = False
# Min sibling count from same parent before the group collapses to parent.
# Threshold 2 = "two or more children of same parent retrieved → merge".
# Set to 3 for tighter precision on noisy corpora.
DEFAULT_AUTO_MERGE_SIBLING_THRESHOLD: Final[int] = 2
# Hard ceiling on how many distinct parents may be emitted by a single
# auto-merge pass. Bounds worst-case context inflation when a query maps
# uniformly across many parents (rare; usually one or two parents
# dominate). 0 = unbounded.
DEFAULT_AUTO_MERGE_MAX_PARENTS: Final[int] = 5



# Grounding-net failure mode (AG-A2). When the grounding judge cannot RUN at
# all — the LLM runtime is unwired (model_resolver / llm is None) or no
# "grounding" binding resolves — the answer would otherwise pass through
# UNVERIFIED: the HALLU net is silently OFF. ``fail_closed`` substitutes the
# bot's ``oos_answer_template`` (the existing refuse branch) instead of
# shipping an ungrounded answer, honouring HALLU=0 sacred. ``fail_open``
# restores the legacy pass-through for bots that explicitly accept the risk
# (per-bot opt-out via ``plan_limits.grounding_failure_mode``). Only applies
# when grounding is enabled AND the intent is grounding-eligible AND the sync
# judge was expected (NOT the per-bot async path, which ships-then-checks by
# its own opt-in contract).
GROUNDING_FAILURE_MODE_FAIL_CLOSED: Final[str] = "fail_closed"
GROUNDING_FAILURE_MODE_FAIL_OPEN: Final[str] = "fail_open"
DEFAULT_GROUNDING_FAILURE_MODE: Final[str] = GROUNDING_FAILURE_MODE_FAIL_CLOSED

# A1 — action when the grounding judge RUNS and CONFIRMS the answer is ungrounded
# (fabricated beyond the retrieved context). Distinct from the failure-mode above,
# which handles the judge being DEAD/unavailable. "block" substitutes the bot's
# oos_answer_template — the SAME sacred-#10-compliant path a regex block already
# takes (the refusal text is the bot's own, never app-injected). "observe" keeps
# the legacy behaviour: persist a flag and SHIP the answer. Default is "observe"
# so no bot's refuse-rate changes without an explicit opt-in; owners flip to
# "block" per-bot only AFTER measuring that the grounding threshold's deliberate
# false-positive bias does not over-refuse genuinely-grounded answers.
GROUNDING_CONFIRMED_ACTION_OBSERVE: Final[str] = "observe"
GROUNDING_CONFIRMED_ACTION_BLOCK: Final[str] = "block"
DEFAULT_GROUNDING_CONFIRMED_ACTION: Final[str] = GROUNDING_CONFIRMED_ACTION_OBSERVE



# --- Numeric-fidelity observe gate (truth-audit Phase 4, contract in specs/) --
# structlog event name for the observe-mode verdict (one per answered request
# with >=1 significant number). Observe-only: the verdict NEVER modifies the
# answer (sacred #10); blocking is a separate owner-gated future step.
NUMERIC_FIDELITY_EVENT: Final[str] = "numeric_fidelity_observe"
# Cap the unsupported-token list in events/trace (counts stay exact).
NUMERIC_FIDELITY_UNSUPPORTED_TOKENS_CAP: Final[int] = 8
# 002-H: noise patterns stripped BEFORE numeric-fidelity tokenizing so the gate
# does not false-flag non-value digits (measured observe FP on the trap set:
# URL fragments, contact numbers, and question-echoed numbers were 4 of 6 FPs).
# All structural/domain-neutral — a URL, a contact-number run, and the query's
# own numbers are never a per-row price to misattribute.
#   * URL: any http(s) link (its path/id digits are not corpus values).
#   * Contact number: a leading-0 run of 8+ digits with separators — a phone
#     shape, never a price (prices carry no leading zero); locale-agnostic.
NUMERIC_FIDELITY_URL_PATTERN: Final[str] = r"https?://\S+"
# Contact-number shape: leading 0, then 6–11 digits each separated by at most a
# SINGLE space/dot/dash. The single-separator bound is load-bearing — a greedy
# ``[\d\s.-]+`` spans the " ... " between two prices and eats both (regression
# test_derived_valid). A phone is a contiguous 8–13 digit run; a price never
# carries a leading zero.
NUMERIC_FIDELITY_CONTACT_NUMBER_PATTERN: Final[str] = r"0\d(?:[ .\-]?\d){6,11}"
