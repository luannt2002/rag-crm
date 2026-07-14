> # ⚠️ ĐÃ BỊ THAY THẾ — KHÔNG DÙNG LÀM NGUỒN SỰ THẬT
> Nhiều claim trong file này đã bị bác ở tầng L5 (đọc code thật).
> Nguồn sự thật hiện tại: [reports/L5_CODE_TRUTH_20260714.md](L5_CODE_TRUTH_20260714.md)
> Giữ file này làm lịch sử điều tra, không phải kết luận.

---

# PHỤ LỤC ĐẦY ĐỦ — LEDGER · FLAG · SEED · LỊCH SỬ · COMMENT

**Ngày**: 2026-07-14 · HEAD `71682a2` · nhánh `fix-260623-ingest-expert`
**Bổ sung chi tiết cho**: `reports/CONFIG_FLAG_HISTORY_AUDIT_20260714.md` (bản tóm tắt)
**Quy ước ẩn danh**: bot slug thật → `bot-A` / `bot-B` / `bot-C` (domain-neutral rule). Brand token → `<brand>`.

---

# PHỤ LỤC A — L1 DRIFT: 24 KEY, CONSTANT ≠ DB

> **DB thắng 24/24.** Sửa `constants.py` cho những key này = **0 tác dụng**.
> Cột `doc?` = có được giải thích ở alembic docstring / comment / ADR / plan không.

| # | key | constant (file:line) = giá trị | **LIVE DB** | WINS | doc? |
|---|---|---|---|---|---|
| 1 | **`mmr_similarity_threshold`** | `DEFAULT_MMR_SIMILARITY_THRESHOLD` `_14_*.py:235` = **0.98** | **0.88** | DB | ✅ `20260709_seed_cliff_floor_mmr_parity.py:10-14,39-42` — **cố ý** |
| 2 | **`rerank_cliff_gap_ratio`** | `DEFAULT_RERANK_CLIFF_GAP_RATIO` `_01_*.py:151` = **0.35** | **0.5** | DB | ❌ |
| 3 | `embedding_dimension` | `DEFAULT_EMBEDDING_DIM` `_00_*.py:146` = **1024** | **1280** | DB | ❌ |
| 4 | `embedding_provider` | `DEFAULT_EMBEDDING_PROVIDER` `_02_*.py:190` = **`jina`** | **`zeroentropy`** | DB | ❌ |
| 5 | `reranker_provider` | `DEFAULT_RERANKER_PROVIDER` `_00_*.py:235` = **`jina`** | **`zeroentropy`** | DB | ❌ |
| 6 | `reranker_model` | `DEFAULT_RERANK_MODEL` `_00_*.py:234` = **`jina-reranker-v3`** | **`zerank-2`** | DB | ❌ |
| 7 | `llm_default_model` | `DEFAULT_METADATA_EXTRACTION_MODEL` `_02_*.py:18` = `gpt-4.1-mini` | **`openai/claude`** | DB | ❌ |
| 8 | `metadata_extraction_model` | idem `_02_*.py:18` = `gpt-4.1-mini` | **`openai/claude`** | DB | ❌ |
| 9 | `multi_query_model` | `DEFAULT_MULTI_QUERY_MODEL` `_11_*.py:257` = `haiku` | **`openai/claude`** | DB | ❌ |
| 10 | `decomposer.model` | `DEFAULT_DECOMPOSER_MODEL` `_14_*.py:161` = `gpt-4.1-mini` | **`openai/claude`** | DB | ❌ |
| 11 | `lexical_retrieval_provider` | `DEFAULT_LEXICAL_RETRIEVAL_PROVIDER` `_19_*.py:142` = **`null`** | **`pg_textsearch`** | DB | ❌ |
| 12 | `metadata_filter_provider` | `DEFAULT_METADATA_FILTER_PROVIDER` `_17_*.py:109` = **`null`** | **`article_aware`** | DB | ❌ |
| 13 | `metadata_aware_retrieval_enabled` | `_12_*.py:41` = **False** | **True** | DB | ❌ |
| 14 | `adaptive_context_enabled` | `_01_*.py:185` = **False** | **True** | DB | ✅ seed `20260619_phase4_costwin_enable.py:70` (constant **không bao giờ theo**) |
| 15 | `crag_skip_retry_above_score` | `_10_*.py:166` = **0.7** | **0.55** | DB | ❌ |
| 16 | `grounding_check_threshold` | `_15_*.py:105` = **0.3** | **0.5** | DB | ❌ |
| 17 | `rag_rerank_top_n` | `DEFAULT_RERANK_TOP_N` `_00_*.py:53` = **7** | **10** | DB | ❌ |
| 18 | `multi_query_max_variants` | `_11_*.py:251` = **7** | **5** | DB | ❌ |
| 19 | `whole_doc_threshold_chars` | `WHOLE_DOC_THRESHOLD_CHARS` `_06_*.py:106` = **1500** | **4000** | DB | ❌ |
| 20 | `max_ingest_content_chars` | `MAX_DOCUMENT_CONTENT_CHARS` `_03_*.py:78` = **500,000** | **2,000,000** | DB | ❌ |
| 21 | `contextual_retrieval_max_doc_chars` | `DEFAULT_CR_MAX_DOC_CHARS` `_11_*.py:119` = **5,000,000** | **24,000** | DB | ❌ |
| 22 | `max_tokens_total` | `_20_*.py:195` = **10,000** | **10,000,000** | DB | ❌ |
| 23 | `rerank_top_n_by_intent` | `_16_*.py:63` (9 intent) | DB **thêm `range_query:15`** | DB | ❌ |
| 24 | `retrieve_top_k_by_intent` | `_16_*.py:84` (`factoid:15, aggregation:40`) | DB **`factoid:20, aggregation:50`, thêm `summary:25`** | DB | ❌ |

**→ 22/24 KHÔNG được document ở đâu cả.**

**Bằng chứng DB không bao giờ theo constant**: không row L1 nào có `updated_at` sau **2026-06-08**, trong khi constant `mmr` đổi **2026-07-04**.

### ⚠️ Bug kèm — 2 key có **default KHÁC NHAU ở 2 call-site** (vi phạm zero-hardcode)

| key | call-site 1 | call-site 2 |
|---|---|---|
| `max_ingest_content_chars` | `ingest_core.py:377` → `MAX_DOCUMENT_CONTENT_CHARS` (**500k**) | `document_routes.py:238` → literal **`2_000_000`** |
| `contextual_retrieval_max_doc_chars` | `ingest_stages_enrich.py:166` → `DEFAULT_CR_MAX_DOC_CHARS` (**5M**) | `bot_admin_routes.py:646` → literal **`50000`** |

**Xóa row DB → 2 đường code bất đồng với NHAU.**

### ⚠️ 3 key có **HAI constant** cho **MỘT key** (tự nó là defect)

`embedding_dimension` (`DEFAULT_EMBEDDING_DIM`=1024 → drift · `DEFAULT_ZEROENTROPY_EMBEDDING_DIM`=1280 → khớp) · `cache_similarity_threshold` · `condense_history_limit`

---

# PHỤ LỤC B — L2 SHADOWED: 54 CONSTANT **ĐÃ CHẾT** Ở RUNTIME

> Giá trị trùng DB → **DB luôn thắng** → sửa constant **không có tác dụng gì**.
> Đây là 54 trong số 78 constant mà owner nói đúng: **"cần hardcode làm gì?"**

```
audit_max_temp_tables=2                          bm25_normalization_flags=5
callback_max_retries=3                           callback_timeout_s=10
chat_max_history=10                              chat_stream_timeout_s=60
chat_worker_concurrency=4                        child_chunk_overlap=50
child_chunk_size=256                             contextual_retrieval_context_max_tokens=100
contextual_retrieval_enabled=False               contextual_retrieval_prompt_cache_enabled=True
cr_enhanced_enabled=False                        crag_min_fallback_score=0.3
decompose_use_structured_output=True             decomposer.enabled=True
decomposer.max_sub_queries=8                     decomposer.max_tokens=300
default_answer_autonomy_percent=0                embedding_dimension=1280 *
generation_temperature=0.0                       grade_use_batch=True
grade_use_structured_output=True                 grounding_check_enabled=True
lexical_rrf_k=60                                 lexical_top_k=20
lost_in_middle_reorder_enabled=True              metadata_fallback_relax_enabled=True
mmr_lambda=0.7                                   multi_query_enabled=True
multi_query_n_variants=3                         multi_query_timeout_s=5
narrate_then_embed_enabled=False                 parent_chunk_size=1024
query_complexity.weight_comma=0.5                rag_rrf_k=60
rag_top_k=20                                     reflect_use_structured_output=True
rerank_cliff_absolute_floor=0.2                  rerank_cliff_min_keep=3
rerank_filter_strategy='cliff'                   reranker_min_score=0.01
reranker_min_score_active=0.3                    reranker_min_score_bypass=0.0
retrieval_multistage_enabled=False               rrf_k=60
semantic_cache_ttl_s=3600                        streaming_word_delay_ms=30
structured_output_enabled=True                   understand_greeting_patterns=[…]
vector_store_provider='pgvector'                 vi_compound_segmentation_ingest_enabled=True
vi_compound_segmentation_timeout_s=5             vlm_caption_prompt=…
```

`*` `embedding_dimension` xuất hiện ở **cả L1 lẫn L2** vì nó được nối với **2 constant** (xem Phụ lục A).

---

# PHỤ LỤC C — L3 DB-ONLY: 187 KEY · **72 KEY KHÔNG CODE NÀO ĐỌC**

> Operator sửa mấy row này → **không có gì thay đổi**. Row rác thuần.

```
action_state_drift_threshold          audit_page_size
boilerplate_removal_patterns_by_language                cache_stampede_singleflight_enabled
chat_history_ttl_hours                chat_stream_heartbeat_ms
chat_stream_sink_maxsize              chunk_overlap  ⚠️
chunking_avg_len_long                 chunking_avg_len_short
chunking_heading_max_for_semantic     chunking_heading_threshold
chunking_mixed_content_threshold      chunking_table_threshold
ci_gate_* (4 key)                     circuit_breaker_enabled  ⚠️
circuit_breaker_fail_max              circuit_breaker_reset_timeout
config_version                        conversation_max_messages_load
conversation_state_provider           crag_batch_grader_max_chunks
deepeval_* (6 key)                    default_bot_id
docs_only_strict_enabled              embed.cache_ttl_s
embedding_model_alternatives          embedding_timeout_s
generate_max_tokens_by_intent         guardrail_provider  ⚠️
hybrid_rrf_bm25_weight                hybrid_rrf_vector_weight
knowledge_graph_stopwords_by_language legal_ref_patterns_by_language
llm_cost_per_completion_token         llm_cost_per_prompt_token
llm_timeout_s                         max_document_size_bytes
metadata_filter_tier_order            parser_engine  ⚠️
parser_heading_detection              parser_table_detection
prompt_context_cap_chars              query_router_provider
question_max_length                   rag_chunk_overlap
rag_chunk_size                        rag_rrf_missing_rank_penalty
reranker_model_alternatives           retrieval_stage_1..4
section_markers_by_language           source_validator_provider
stopwords_by_language                 tenant_rate_limit_enabled
tenant_token_cap_enabled              token_ledger_provider
understand_query_cache_enabled        zeroentropy_api_url
zeroentropy_latency_mode              zeroentropy_model
zeroentropy_reranker_timeout_s
```

### ⚠️ 4 cái đáng chú ý (đã spot-verify)

| key | sự thật |
|---|---|
| **`guardrail_provider`** | `bootstrap.py:361` ghi thẳng: `provider="local",  # TODO Phase 4: lift to system_config.guardrail_provider`. **Row DB là đồ trang trí.** |
| **`parser_engine`** | `ocr_factory.py:8` gọi nó là *"the source of truth"*. **Không code nào đọc.** |
| **`chunk_overlap`** | Chết. Code thật đọc key **TÊN KHÁC**: `rag_default_chunk_overlap` (`ingest_stages.py:532`). **Key-name drift.** |
| **`circuit_breaker_enabled`** | 🔴 **KILLSWITCH GIẢ** — xem Phụ lục F.INERT |

---

# PHỤ LỤC D — L4 CONSTANT-ONLY: 87 KEY **CÒN CHỊU TẢI**

> **Đây là những constant DUY NHẤT mà sửa `constants.py` thật sự đổi production.**

```
adapchunk_l5_* (6)                    adaptive_router_l1_enabled
batch_step_logging_enabled            bkai_vn_embedder_enabled
bm25_substring_fallback_enabled       cascade_t_high
cascade_t_low                         crag_grade_concurrency
crag_grader_provider                  crag_lenient_grade_for_compound_intents_enabled
crag_min_relevant_count               crag_min_relevant_fraction
decompose_confidence_gate             decompose_enabled
decompose_min_tokens                  decompose_top_k_per_subquery
diacritic_restoration_enabled         diacritic_restoration_use_model
draft_model                           entity_extractor_provider
entity_grounding_enabled              entity_grounding_max_entities
formula_image_atomic_protect_enabled  generate_context_chars_cap
generate_context_trust_hint_enabled   generate_p95_sla_ms
generic_vocab_* (3)                   grade_chunk_preview
grade_timeout_s  ⭐                    grounding_check_async_top_score_threshold
grounding_intents                     guardrail_leak_shingle_size
guardrail_oos_similarity_threshold    intent_extractor_model
intent_extractor_system_prompt        jina_embedding_tpm_per_key
jina_embedding_tpm_safety_fraction    max_total_graph_iterations
metadata_extraction_vocabulary        metadata_layer3_llm_enabled
multi_query_complexity_min            multi_query_dedup_threshold
multi_query_entity_gate_enabled       multi_query_min_tokens
multi_query_skip_chitchat_intent      neighbor_* (4)
pii_redactor_provider                 pipeline_multi_query_speculative_timeout_s
prompt_max_tokens                     prompt_token_opt_* (4)
rag_max_documents                     range_query_min_confidence
reflection_enabled                    refuse_short_circuit_enabled
rerank_cliff_skip_intents             rerank_retrieval_safety_n
rerank_skip_intents                   rerank_threshold_gate_after_cliff_enabled
retrieve_fallback_enabled             retrieve_fallback_top_k
self_rag_critique_enabled             self_rag_critique_threshold
semantic_cache_skip_multi_turn        semantic_cache_skip_numeric
skip_reflect_intents                  skip_rewrite_intents
speculative_hallu_verify_enabled      speculative_retrieve_timeout_s
speculative_similarity_threshold      stats_index_limit
stats_index_race_enabled              stats_race_timeout_s
structural_ref_fallback_pattern       understand_use_structured_output
```

⭐ **`grade_timeout_s`** — đây là lý do fix `5c4fdda` **thực sự có hiệu lực** (constant là live value). Nhưng nó **fix sai tầng** — xem Phụ lục H.

---

# PHỤ LỤC E — 75 KEY `system_config` **MẤT KHI SQUASH**

> Có trong archive (279 migration), **KHÔNG** trong chain active. **DB fresh không có chúng.**

```
chunking_policy  🔴                    rerank_cliff_absolute_floor (0.15)
rerank_cliff_min_keep (3)              rerank_filter_strategy
reranker_min_score_active (0.30)       rerank_top_n_by_intent
rerank_weights_by_intent               adaptive_rerank_weight_enabled
mmr_similarity_threshold_by_intent  🔴 crag_skip_retry_above_score (0.65)
crag_grader_provider                   crag_min_fallback_score_by_intent
crag_emit_gap_enabled                  rag_top_k (20)
rag_chunk_size (1024)                  rag_chunk_overlap (128)
retrieve_top_k_by_intent               top_k_retrieve
top_k_rerank                           rag_rerank_top_n
retrieval_multistage_enabled           retrieval_early_exit_threshold
lexical_retrieval_provider  🔴         lexical_rrf_k
lexical_top_k                          vector_store_provider  🔴
query_router_provider                  multi_query_enabled_by_intent
multi_query_n_variants                 multi_query_model
rewrite_enabled_by_intent              metadata_extraction_enabled
metadata_aware_retrieval_enabled       metadata_filter_tier_order
contextual_retrieval_enabled           contextual_retrieval_max_doc_chars
cr_enhanced_enabled                    enrichment_enabled
narrate_then_embed_enabled             structured_ref_extraction_enabled
structured_subanswer_enabled           embedding_text_strategy  🔴
parser_engine                          guardrail_provider (local)
oos_answer_template  🔴                math_lockdown_enabled
math_lockdown_severity                 default_math_lockdown_enabled
grounding_check_enabled  🔴            cascade_low_model
cascade_high_model                     conversation_state_provider
token_ledger_provider                  speculative_streaming_enabled
adapchunk_layer5_cross_check_enabled   adapchunk_legal_hybrid_enabled
adapchunk_legal_hybrid_min_words       table_csv_emit_header_footer_chunks_enabled 🔴
article_ref_patterns                   default_vocabulary_vi
generate_context_chars_cap_by_intent   prompt_context_cap_chars
prompt_compression_max_chars_per_chunk max_tokens_total
llm_timeout_s (30)                     llm_default_max_tokens
llm_default_temperature                llm_default_top_p
chat_max_history                       chat_ttl
question_max_length                    default_bot_id
audit_page_size                        audit_max_temp_tables
query_complexity.weight_numbers        grading
```

### Mất seed KHÔNG-phải-`system_config` (xác nhận độc lập trong docs)

| | |
|---|---|
| **`guardrail_rules`** | PROD **13 rule**. Chain active **chỉ seed `prompt_injection_vi`**. 12 rule còn lại (`pii_vi_phone`, `pii_vi_email`, `pii_en_ssn`, `secret_leak`, `sql_injection`, 5× `prompt_injection_legacy`) đến từ **archive `20260516_010f`**. `STATE_SNAPSHOT.md:500` gọi đây là **"CRITICAL seed gap"**, `README.md:249` vẫn để mở |
| **RLS role-provisioning DDL** | Bỏ hẳn (`STATE_SNAPSHOT.md:476`) → **20 bảng FORCE-RLS + 21 policy TRƠ** |
| **Few-shot prompt content** (`010w`/`010z`) · **money-norm rules** (`0114`) | risk #2, **chưa bao giờ re-seed** |

### 🔴 Squash còn gây drift ở tầng STAMP

`STATE_SNAPSHOT.md:528-531`: DB live stamp `squash_base_20260618` nhưng DDL **chỉ apply một phần** → **thiếu 6 bảng** (gồm **`event_inbox`** — inbox exactly-once mà **3 file source phụ thuộc**), thiếu `documents.access_groups`, **thiếu 2 trigger + 22 index**. Table count **39 vs baseline 45**.

### 🔴 2 defect **ĐÓNG BĂNG VÀO BASELINE SQL**, không migration nào sửa

`reports/COVERAGE_SWEEP_20260626.md:33-34`:
- `document_service_index` **thiếu `missing_ok=true`** (`squashed_baseline.sql:1477`)
- `document_service_index` **thiếu `FORCE ROW LEVEL SECURITY`** (`:1433`) — **duy nhất trong 24 policy**

→ **Khi bật RLS, session không bind trên bảng đó sẽ RAISE thay vì fail-closed.**

---

# PHỤ LỤC F — INVENTORY FLAG ĐẦY ĐỦ

**Quét**: 133 `Final[bool]` + 21 mode-string trong `shared/constants/` · 133 key `_pcfg` trong `orchestration/` · 62 key `PLAN_LIMIT_SCHEMA` · 2 whitelist pipeline_config · whitelist `bootstrap_config` (127 key) · 32 registry · 264 row `system_config` · 6 bot live.

**Tuổi git**: **348/415** dòng toggle blame về `cd08119` = **KẾ THỪA** (git im lặng). Chỉ **19 dòng CÓ CHỦ ĐÍCH**.

**Thực tế bot**: 6 bot, **chỉ 3 có bất kỳ override `plan_limits` nào**. 3 bot còn lại `{}`.

---

## F.INERT — ☠️ ~30 FLAG TRƠ (giá trị live được set, **code KHÔNG ĐỌC**)

### Gốc rễ: `feature_flag=` **KHÔNG PHẢI GATE**

```python
shared/intrinsic_metrics.py:315   feature_flag: str = "ekimetrics_5metric_selector_enabled",
shared/intrinsic_metrics.py:335   @param feature_flag: flag name to emit in the structlog event
```
→ Mọi `feature_flag="x"` **phát ra log nói rằng flag `x` chi phối bước này** — **trong khi `x` có thể không được đọc ở đâu cả.** Đây là **flag ma**.

| flag | LIVE | vì sao **không làm gì** | evidence |
|---|---|---|---|
| **`circuit_breaker_enabled`** | **`true`** | 🔴 **KILLSWITCH GIẢ.** Chỉ có trong **docstring**. `FailoverOrchestrator(` **KHÔNG BAO GIỜ khởi tạo**. Breaker live là per-provider, **KHÔNG GATE** | `failover_orchestrator.py:58` vs `dynamic_litellm_router.py:443` |
| **`table_csv_emit_header_footer_chunks_enabled`** | **`true`** | reader nằm trong `elif strategy == "table_csv"`; live là `table_dual_index` → `_chunk_table_dual_index(text)` **không nhận param đó** | `chunking/__init__.py:323,507` vs `:66` |
| **`adapchunk_layer5_cross_check_enabled`** | **`true`** | `apply_cross_check` gọi **VÔ ĐIỀU KIỆN** trên block path; flag chỉ đọc khi `strategy is None` | gọi ungated `ingest_stages.py:632-637`; flag `chunking/__init__.py:460` |
| **`tenant_rate_limit_enabled`** | **`true`** | **0 reader.** Limiter luôn được dựng; chỉ `tenant_rate_limit_per_min` được đọc | `bootstrap.py:561`; `tenant_rate_limiter.py:13-14,77-78` |
| **`docs_only_strict_enabled`** | **`true`** | **0 reader** trong `src/ragbot` (chỉ có row seed) | grep |
| **`token_quota_notify_enabled`** | **`true`** | có trong allow-list `bootstrap_config` (`:85`) nhưng **không có lời gọi `get_boot_config`** | `bootstrap_config.py:85` |
| **`cache_stampede_singleflight_enabled`** | **`true`** | **0 reader**; `AsyncSingleFlight` luôn được wire | `tenant_config_cache.py:88` |
| **`understand_query_cache_enabled`** | **`true`** | **0 reader**; cache luôn được dựng | `bootstrap.py:240` |
| **`parser_heading_detection`** / **`parser_table_detection`** | **`true`** | ctor default của `SimpleTextParser` — được dựng **không tham số**. Và `parser_engine="kreuzberg"` → **không phải parser live** | `simple_text_parser.py:60-61`; `ocr_factory.py:72,85` |
| `tenant_token_cap_enabled` | `false` | **0 reader** | grep |
| `deepeval_enabled` | `false` | **0 reader** | grep |
| `bm25_symbol_phrase_enabled` | const `True` | `retrieve.py` truyền `cover_density`/`normalization_flags`/`substring_fallback` **nhưng không truyền cái này** → **ghim cứng `True` ở tầng SQL. KHÔNG LẬT ĐƯỢC** | `pgvector_store.py:370` vs `retrieve.py:1108-1122` |
| `robust_json_parser_enabled` | const `True` | `robust_json_parse()` có **0 call site** | `shared/json_parse.py:265` |
| `callback_ssrf_guard_enabled` | const `True` (**CÓ CHỦ ĐÍCH `eafddaa`**) | `CallbackDelivery` **không bao giờ khởi tạo** | `callback_delivery.py:44` |
| **`heuristic_intent_enabled`** | const `True` | 🔴 key **CHỈ có ở route test-chat**; **VẮNG khỏi `chat_worker/pipeline_config.py`** → trên **worker prod** `_pcfg` **luôn trả default**. **Override per-bot bị PROD BỎ QUA IM LẶNG** | `test_chat/_pipeline_config.py:855` |
| **`guard_output_parallel_enabled`** | const `True` | 🔴 **cùng defect** — chỉ test-chat; worker prod luôn rơi về key legacy | `test_chat/_pipeline_config.py:866`; `guard_output.py:648-658` |

### 🔴 `embedding_text_strategy = "auto"` — GIÁ TRỊ KHÔNG TỒN TẠI

```python
# infrastructure/embedding_text/registry.py:36
_REGISTRY = {"prefix_plus_raw", "raw_only", "field_selective", "null"}   # KHÔNG có "auto"
# :55
cls = _REGISTRY.get(key)
if cls is None:
    logger.warning("embedding_text_strategy_unknown_provider_fallback_null", ...)
    cls = NullEmbeddingTextStrategy       # ← RƠI VÀO ĐÂY, MỖI LẦN
```
LIVE DB: `"auto"` → **luôn rơi về Null Object**.
→ **Toàn bộ registry embedding-text (3 strategy thật) BẤT KHẢ TIẾP CẬN.** Null = pass-through (vô hại về mặt hành vi), **nhưng config đang NÓI DỐI.**

---

## F.KEYNAME — Flag **SAI TÊN KEY** (constant đặt tên A, code đọc B)

| constant | key code **thật sự** đọc | evidence |
|---|---|---|
| `cr_prompt_cache_enabled` | `contextual_retrieval_prompt_cache_enabled` | `ingest_stages_enrich.py:165` |
| `enriched_prefix_persist` | `enriched_prefix_persist_in_content` | `ingest_stages_enrich.py:601-605` |
| `diff_reingest_enabled` | `diff_based_reingest_enabled` — mà cái đó **chỉ log `diff_reingest_telemetry_not_implemented`** | `ingest_core.py:682-696` |
| `rerank_intent_whitelist_enabled` | field DTO **lồng** `rerank_intent_whitelist.enabled` | `rerank.py:112-115`; `dto/bot_config.py:145` |
| `self_rag_enabled` | `self_rag_critique_enabled` | `nodes/critique_parser.py:160` |

---

## F.PSEUDO — Flag GIẢ (constant-only, **không đọc DB** → không lật được nếu không redeploy)

`llm_failover_enabled` (`dynamic_litellm_router.py:707`) · `content_type_dispatch_enabled` (`ingest_stages_store.py:653`) · `semantic_cache_hit_log_enabled` (`semantic_cache.py:439`) · `rl_emit_headers` (`app.py:527`) · `security_headers_hsts_enabled` (`app.py:512`) · `pipeline_audit_logger_enabled` (`pipeline_audit_logger.py:109`) · `table_footer_preserve_enabled` (`blocks.py:24`) · `narrate_batch_use` (`anthropic_haiku_batch.py:220`) · `multi_query_include_original` (`multi_query_expansion.py:296`) · `tenant_token_cap_enforce_preflight` (`dynamic_litellm_router.py:423`) · `sync_documents_wipe_mode` (`sync.py:215`) · `mmr_use_cosine` (0 reader) · `grounding_use_structured` (kwarg default không bao giờ được truyền, `local_guardrail.py:895`)

---

## F.A — CLASS A: **42 FLAG SETTLED-ON** (live ON, 0 override → **nhánh OFF CHẾT**)

| key | constant (file:line) | system_config | override | nhánh OFF (chết) | git |
|---|---|---|---|---|---|
| `structured_output_enabled` | `_14_*:83` `True` | `true` | 0 | parse free-text ở **5 node** (`generate.py:726`, `grade.py:172`, `reflect.py:86`, `understand.py:205`, `decompose.py:45`) | KẾ THỪA |
| `grade_use_structured_output` | `_14_*:84` `True` | `true` | 0 | `path_used="per_chunk_fallback"` (1 LLM call/chunk) `grade.py:173` | KẾ THỪA |
| `grade_use_batch` | `_14_*:93` `True` | `true` | 0 | per-chunk fallback `grade.py:174` | KẾ THỪA |
| `understand_use_structured_output` | `_14_*:87` `True` | vắng | 0 | `understand.py:207` | KẾ THỪA |
| `reflect_use_structured_output` | `_14_*:85` `True` | `true` | 0 | `reflect.py:87` — **CHẾT KÉP** (còn bị gate bởi `reflection_enabled=False`) | KẾ THỪA |
| `decompose_use_structured_output` | `_14_*:86` `True` | `true` | 0 | `decompose.py:47` | KẾ THỪA |
| `pipeline_parallel_rewrite_mq_enabled` | `_11_*:289` `True` | `true` | 0 | serial `return await rewrite(state)` `query_graph.py:2337` | KẾ THỪA |
| `pipeline_parallel_cache_understand_enabled` | `_11_*:290` `True` | `true` | 0 | serial `return await check_cache(state)` `query_graph.py:1844` | KẾ THỪA |
| `pipeline_multi_query_embed_batch_enabled` | `_11_*:298` `True` | `true` | 0 | per-query embed `retrieve.py:1409-1411` | KẾ THỪA |
| `pipeline_pre_retrieval_parallel_enabled` | `_11_*:303` `True` | vắng | 0 | serial `query_complexity_node.py:55` | KẾ THỪA |
| **`pipeline_merge_condense_router`** | — | `true` | 0 | **node condense+router legacy, VẪN ĐĂNG KÝ** `query_graph.py:2923`; gate `routing.py:60` | KẾ THỪA |
| `retrieve_fallback_enabled` | `_11_*:322` `True` | vắng | 0 | `retrieve.py:1564` | KẾ THỪA |
| `metadata_fallback_relax_enabled` | `_12_*:43` `True` | `true` | 0 | `retrieve.py:1520` | KẾ THỪA |
| `crag_lenient_grade_for_compound_intents_enabled` | `_10_rbac:152` `True` | vắng | 0 | 3 site `grade.py:280,370,519` | KẾ THỪA |
| `adaptive_router_l1_enabled` | `_14_*:168` `True` | vắng | 0 | legacy `_router_route` `routing.py:79` | KẾ THỪA |
| `lost_in_middle_reorder_enabled` | `_11_*:207` `True` | `true` | 0 | `generate.py:583` | KẾ THỪA |
| `generic_vocab_enabled` | `_11_*:210` `True` | vắng | 0 | `retrieve.py:800` | KẾ THỪA |
| `multi_query_enabled` | `_11_*:240` `True` | `true` | 0 | `retrieve.py:1213`, `query_graph.py:2074` | KẾ THỪA |
| `multi_query_skip_chitchat_intent` | `_11_*:265` `True` | vắng | 0 | `query_graph.py:2098`, `retrieve.py:1275` | KẾ THỪA |
| `bm25_use_cover_density` | — | `true` | 0 | `ts_rank` thường `pgvector_store.py:367` | KẾ THỪA |
| `semantic_cache_skip_numeric` | `_04_*:22` `True` | vắng | 0 | `persist.py:148` | KẾ THỪA |
| `semantic_cache_skip_multi_turn` | `_04_*:29` `True` | vắng | 0 | `check_cache.py:60`, `persist.py:165` | KẾ THỪA |
| `refuse_short_circuit_enabled` | `_04_*:65` `True` | vắng | 0 | `generate.py:321` | KẾ THỪA |
| `vietnamese_preprocessing_enabled` | — | `true` | 0 | `retrieve.py:735` | KẾ THỪA |
| `vi_compound_segmentation_ingest_enabled` | `_12_*:48` `True` | `true` | 0 | `ingest_stages_enrich.py:167` | KẾ THỪA |
| `cleanbase_tier0_enabled` | `_20_*:40` `True` | vắng | 0 | `ingest_stages.py:292-296` | KẾ THỪA |
| `adapchunk_block_pipeline_enabled` | `_12_*:185` `True` | vắng | 0 | nhánh legacy text-flatten `ingest_stages.py:648+` ⚠️ **xem comment bảo vệ** | KẾ THỪA |
| `ingestion_cleaning_enabled` / `ingestion_validation_enabled` | — | `true`,`true` | 0 | `ingest_stages.py:297`, `ingest_stages_store.py:579` | KẾ THỪA |
| `whole_doc_enabled` | — | `true` | 0 | `ingest_stages.py:412` | KẾ THỪA |
| `late_chunking_enabled` | — | `true` | 0 | `ingest_stages_store.py:359` | KẾ THỪA |
| `enrich_row_gate_enabled` | `_11_*:178` `True` | vắng | 0 | `ingest_stages_enrich.py:169` | KẾ THỪA |
| `context_buffer_atomic_enabled` | `_18_*:34` `True` | vắng | 0 | `context_buffer.py:79-86` (env-sourced) | KẾ THỪA |
| `streaming_enabled` / `streaming_use_real_llm` | `_07_*:9` `True` | `true` | 0 | `chat_stream.py:120`; `chat_routes.py:990` (OFF = **word-replay giả lập** — đường demo) | KẾ THỪA |
| `skip_understand_for_greeting` | `_17_*:68` `False` | **`true`** | 0 | `query_graph.py:664` | KẾ THỪA |
| `grounding_check_enabled` | `_14_*:195` `True` | `true` | 0 | `guard_output.py:405` ⚠️ **xem CLASS D** | KẾ THỪA |
| `cross_doc_reconcile_enabled` | `_15_*:129` `True` | vắng | 0 | `query_graph.py:2575` | **CÓ CHỦ ĐÍCH `c0c0dea`** |
| `stats_code_lookup_enabled` | `_21_*:118` `True` | vắng | 0 | `retrieve.py:286` | **CÓ CHỦ ĐÍCH `ccd9874`** |
| `stats_price_of_entity_enabled` | `_21_*:122` `True` | vắng | 0 | `retrieve.py:297` | **CÓ CHỦ ĐÍCH `9d2fee9`** |
| `stats_superlative_enabled` | `_21_*:143` `True` | vắng | 0 | `retrieve.py:317` | **CÓ CHỦ ĐÍCH `ccd9874`** |
| `stats_serve_require_value` | `_21_*:71` `True` | vắng | 0 | `query_graph.py:2400` | **CÓ CHỦ ĐÍCH `c36094d`** |
| `sysprompt_leak_skip_stats_route` | `_06_*:172` `True` | vắng | 0 | `guard_output.py:604` | **CÓ CHỦ ĐÍCH `ccd9874`** |
| `generate_context_trust_hint_enabled` | `_01_*:272` `True` | vắng | 0 | `generate.py:611` | KẾ THỪA |

### ⚠️ MỚI THÊM + ĐÃ SETTLED (**CÓ CHỦ ĐÍCH, <1 tháng — CHƯA XÓA, còn đang quan sát**)

`stats_route_skip_grounding` (`062d6fa`, OFF) · `empty_answer_guard_enabled` (`7c2570c`) · `generate_surface_verbatim_enabled` (`b5ced79`, OFF) · `late_chunking_sliding_enabled` (`ded8e01`)

### ⚠️ NGOẠI LỆ — **GIỮ `generate_use_structured_output`**
Live `false` **VÀ** force-disable trên đường SSE (`generate.py:737`) → **2-mode THẬT**, không phải flag chết.

---

## F.B1 — CLASS B1: **15 ORPHAN** (0 reader ở src + tests + scripts, 0 override → **XÓA flag + code**)

| key | constant (file:line, value) | nhánh ON |
|---|---|---|
| `api_key_failover_enabled` | `_01_*:129` `True` | **không có code nào** |
| `embedding_failover_enabled` | `_02_*:197` `False` | không có code |
| `embedding_semantic_chunk_enabled` | `_06_*:199` `False` | chỉ có **nhãn ma** (`strategies.py:613`); `sentence_similarity/registry.py` **comment 100%** |
| `multi_vector_enabled` | `_02_*:219` `False` | `multi_vector_registry.py:48-110` **comment hết** |
| `auto_merge_retrieval_enabled` | `_14_*:288` `False` | `shared/auto_merge_retrieval.py:108` định nghĩa `auto_merge_retrieve()`, **KHÔNG BAO GIỜ được import** (~273 dòng) |
| `retrieval_bm25_fallback_enabled` | `_02_*:202` `True` | không có code |
| `grounding_numeric_overlap_enabled` | `_14_*:194` `True` | không có code |
| `grounding_check_truly_parallel` | `_21_*:248` `True` | không có code |
| `cag_mode_enabled` | `_20_*:23` `False` | mọi reader **`#`-comment** (`cag_service.py:141`, `anthropic_cag.py:151-238`, `null_cag.py:70`) |
| `proposition_llm_decomp_enabled` / `proposition_use_llm` | `_20_*:97`, `:115` `False` | reader comment (`shared/proposition_llm.py:276`) |
| `tenant_bypass_rate_limit` | `_03_*:95` `False` | cơ chế thật là **cột `tenants.bypass_rate_limit`** (`tenant_config_cache.py:153,204`) |
| `pii_redaction_universal` | `_13_*:117` `False` | reader live (`pii_universal.py:176`) **nhưng AND-gate với `pii_redaction_enabled`**, mà **không bot nào set** → **nhánh ON không bao giờ chạy** |
| `blocks_api_enabled` | `_15_*:48` `False` | khai trong `bot_limits.py:301`, **không reader** |
| `modality_rerank_enabled` | `_15_*:62` `False` | `_modality_boost.py:91,160` **không bao giờ được import** |
| `mmr_use_cosine` | — | 0 reader |

---

## F.B2 — CLASS B2: **22 TÍNH NĂNG HOÃN THẬT** — ⛔ **GIỮ CẢ FLAG LẪN CODE**

> **Reader LIVE và ĐÚNG. Nhánh ON đơn giản là chưa bao giờ được chọn ở prod.**
> **Xóa flag = XÓA MỘT TÍNH NĂNG ĐÃ SHIP.** Đây **KHÔNG** phải nợ.

`narrate_then_embed_enabled` · `contextual_retrieval_enabled` · `cr_enhanced_enabled` · `enrichment_enabled` · `metadata_layer3_llm_enabled` · `cascade_routing_enabled` · **`self_rag_critique_enabled`** (→ `critique_parser.py:160`, node đăng ký `query_graph.py:2953`) · `speculative_streaming_enabled` · `speculative_hallu_verify_enabled` · **`neighbor_expand_enabled`** (→ `neighbor_expand.py:478`, node đăng ký `query_graph.py:2946`) · `parent_child_enabled` · `retrieval_multistage_enabled` · `autocut_enabled` · `permission_filtering_enabled` · `entity_grounding_enabled` · `formula_image_atomic_protect_enabled` · `adapchunk_layer3_doc_profile_enabled` · `chunk_hash_id_enabled` · `markdown_normalize_enabled` · `source_allowlist_enabled` · `prompt_token_opt_enabled` · `stats_index_race_enabled`

> ⚠️ **Node của chúng CÓ trên LangGraph, và enable-gate nằm TRƯỚC step span** → **chúng sẽ TRÔNG NHƯ CHẾT trong `request_steps` dù wiring hoàn toàn đúng.** OFF ≠ CHẾT.

---

## F.C — CLASS C: **16 FLAG LIVE-PRODUCT** — ⛔ **BẮT BUỘC GIỮ**

> **Đây là TOÀN BỘ bề mặt per-bot thật của platform.** Inline = **phá multi-tenancy**.

| key | default | bot override → giá trị | khác thật? |
|---|---|---|---|
| `hyde_enabled` | `False` (`_00_*:160`) | `bot-B` → **`true`** | ✅ |
| `prompt_compression_enabled` | `True` | `bot-B` → **`false`** | ✅ |
| `rerank_cliff_min_keep` | `3` | `bot-B` → **`5`** | ✅ |
| `empty_answer_guard_enabled` | `False` (`7c2570c`) | `bot-A`, `bot-C` → **`true`** | ✅ |
| `stats_name_by_shape` | `False` (`d495db2`) | `bot-A` → **`true`** | ✅ |
| `stats_brand_aware` | `False` (`d495db2`) | `bot-A` → **`true`** | ✅ |
| `numeric_fidelity_action` | `observe` (`a3529f3`) | `bot-A` → **`block`** | ✅ |
| `brand_scope_gate_action` | `observe` (`d495db2`) | `bot-A` → **`block`** | ✅ |
| `claim_fidelity_action` | `observe` (`7c2570c`) | `bot-A` → `observe` | ⚠️ trùng default (trục mode `observe`/`block` là **thật**) |
| `grounding_confirmed_action` | `observe` (`c0c0dea`) | `bot-A` → `observe` | ⚠️ trùng default |
| `reflect_skip_if_grounded` | `False` | `bot-C` → **`true`** | ✅ ⚠️ **TRƠ** — xem dưới |
| `reflect_skip_top_score_floor` | `0.30` | `bot-C` → `0.3` | ⚠️ trùng · **TRƠ** |
| `crag_skip_retry_above_score` | `0.55` | `bot-C` → **`0.5`** | ✅ |
| `rerank_skip_intents` | non-empty | `bot-C` → **`[]`** | ✅ |
| `sysprompt_version` | default | `bot-C` → **`context_aware`** | ✅ |
| `brand_scope_negation_phrases` · `claim_fidelity_scope_phrases` | `[]` | `bot-A` → danh sách cụm từ VN | ✅ (**domain data trong config — ĐÚNG sacred rule**) |

### ⚠️ `PLAN_LIMIT_SCHEMA` khai **62 key**. Chỉ **16 key** có khách hàng.
**~46 plan-limit key có ZERO override trên cả 6 bot** — bề mặt schema **không ai dùng**.

### 🔴 `reflection_enabled` là **CÁI BẪY**
Nằm trong `PLAN_LIMIT_SCHEMA` (`bot_limits.py:57`, default `False`), **không bot nào override** → node `reflect` **KHÔNG BAO GIỜ chạy** (`routing.py:211` đi thẳng `persist`).
→ **`reflect_skip_if_grounded=true` trên `bot-C` và `reflect_skip_top_score_floor` là 2 OVERRIDE PER-BOT TRƠ** — chúng cấu hình một node **không bao giờ được vào**.
→ **Một cài đặt khách hàng NHÌN THẤY, mà KHÔNG LÀM GÌ.**

---

## F.D — CLASS D: **21 OPS-KILLSWITCH** — ⛔ **GIỮ** (dù chưa lật bao giờ)

| key | live | vì sao giữ |
|---|---|---|
| `pipeline_timeout_s`=60 · `llm_timeout_s`=30 · `grade_timeout_s` · `multi_query_timeout_s`=5 · `speculative_retrieve_timeout_s` · `chat_stream_timeout_s`=60 · `embedding_timeout_s`=90 · `zeroentropy_reranker_timeout_s`=5.0 | — | kiềm chế latency lúc sự cố |
| `circuit_breaker_fail_max`=5 · `circuit_breaker_reset_timeout`=30 · `DEFAULT_CB_MODE` (`3006171`) | — | tuning breaker. ⚠️ **NHƯNG `circuit_breaker_enabled` TRƠ → killswitch KHÔNG TỒN TẠI** |
| `rate_limit_default_value`=120 / `_window`=60 · `tenant_rate_limit_per_min`=600 | — | phản ứng lạm dụng |
| **`grounding_failure_mode`** = `fail_closed` (`3097755`) | `fail_closed` | **HALLU=0 sacred.** Nhánh ON (`fail_open`) là **lối thoát lúc sự cố** khi grounder chưa wire |
| `grounding_check_enabled` · `citation_marker_required` · `degeneration_action` (`099bc53`) | `true`/`false`/`observe` | guard chống hallu — **phải lật được lúc sự cố** |
| `callback_verify_ssl` · `callback_max_retries` · `callback_timeout_s` | `true`/3/10 | an toàn egress |
| **`reranker_enabled`** | `true` | switch degradation → chunk đi qua theo thứ tự retrieval. ⚠️ **BUG**: `rerank.py:153` `elif not enabled and not _per_bot_reranker_active` → **binding reranker per-bot GHI ĐÈ KILLSWITCH.** Đó là **bug trong 1 killswitch** |
| `bypass_cache` / `bypass_rate_limit` (cột tenant) | — | **revenue feature** | `tenant_config_cache.py:153,204` |
| `dev_token_enabled` · `dev_token_allow_network` | `False`,`False` | cổng bảo mật cho test harness nội bộ | `test_chat/pages.py:119,125` |

---

## F.E — CLASS E: **5 UNKNOWN/RISKY** — 🔍 điều tra trước khi động

| key | thiếu gì |
|---|---|
| `speculative_retrieve_enabled` (live **`true`**) | Reader `query_graph.py:1848` nằm **BÊN TRONG** early-exit của `pipeline_parallel_cache_understand_enabled` (`:1843-1844`). Cả hai đang ON nên nó chạy — nhưng **ghép cặp và không document**. **CHƯA VERIFY** — cần trace prod xác nhận task speculative **thật sự thắng cuộc đua**; `request_steps` **sẽ KHÔNG cho thấy** (nó là anh em `gather`, không phải node) |
| `pipeline_multi_query_speculative_enabled` (live **`true`**) | Cùng kiểu lồng (`query_graph.py:1869-1875`). **CHƯA VERIFY — cần runtime trace** |
| `graph_rag_default_mode`=`"disabled"` · `graph_rag_lazy_mode`=`false` · `graph_rag_mode` (plan-limit: `disabled`/`enabled`/`adaptive`) | bảng `knowledge_edges` **tồn tại**. **CHƯA VERIFY** nhánh `enabled`/`adaptive` có implementation live không |
| **`embedding_text_strategy`** = `"auto"` | 🔴 **"auto" KHÔNG nằm trong 4 key registry** → **rơi Null im lặng**. Xem F.INERT |
| `metadata_filter_tier_order` = `["regex","per_bot","llm"]` | tier `llm` bị gate bởi `metadata_layer3_llm_enabled=false`. **CHƯA VERIFY** tier `per_bot` có tới được không khi 0 bot có vocab |

---

## F.COUNT — TỔNG

| CLASS | n |
|---|---|
| **A. SETTLED-ON** (inline, xóa nhánh OFF) | **42** |
| **B. SETTLED-OFF** — trong đó: | **37** |
| ├─ **B1 ORPHAN/INERT** — xóa flag **VÀ** code chết | **15** |
| └─ **B2 tính năng hoãn thật** — **GIỮ code, GIỮ flag** | **22** |
| **C. LIVE-PRODUCT** (bắt buộc giữ) | **16** |
| **D. OPS-KILLSWITCH** (giữ) | **21** |
| **E. UNKNOWN/RISKY** | **5** |
| ☠️ **INERT** (cắt ngang A/B/D) | **~30** |
| 🗑️ module có `DEAD-CODE NOTICE` | **66** file · **6,477 dòng** |
| 🗑️ registry comment 100% | **12** |

---

## F.TRAP — ⛔ **3 CÁI BẪY: TRÔNG NHƯ XÓA ĐƯỢC NHƯNG KHÔNG**

1. **`decomposer_enabled`** — grep bảo orphan. **KHÔNG PHẢI.** Key live là **DOTTED**: `decomposer.enabled` (`query_decomposer.py:151`, live `true`). **Bất kỳ pass xóa bằng regex sẽ GIẾT MỘT FLAG ĐANG SỐNG.**
2. **22 flag Class-B2** — node **CÓ đăng ký trong LangGraph**, enable-gate nằm **TRƯỚC** step span → **trông chết trong `request_steps` dù wiring ĐÚNG**.
3. **`grounding_*` / `*_fidelity_action` / `degeneration_action`** — guard **HALLU=0**. Live `observe`/`fail_closed`, có override `block` per-bot thật. **Class C+D, KHÔNG phải nợ.**

---

# PHỤ LỤC G — LỊCH SỬ ĐẦY ĐỦ

**Phạm vi**: 391 commit trên mọi ref (362 trên HEAD), `cd08119` 2026-06-17 → `71682a2` 2026-07-13.
**Project thật bắt đầu**: **2026-04-15** (`alembic/_archive_pre_squash_20260618/20260415_0001_initial_schema.py`).
→ **Git chỉ giữ 26 ngày cuối của một project 3 tháng.**

## Chương 0 — Hai tháng bị git xóa (2026-04-15 → 06-16)

*(Dựng lại từ 279 file trong `_archive_pre_squash_20260618/`, `STATE_SNAPSHOT_HISTORY.md`, `docs/master/13-M-roadmap-history.md`)*

| Giai đoạn | Sự kiện |
|---|---|
| **Apr 15-25** | **Genesis + P-plans.** Migration 0001-0005 (15/04). Sprint 2-3, plan P1-P28. `HISTORY:2381` liệt kê *"P1-P14 + RBAC + P15 (9/12) + P16 + P17-P21"* — **TÊN sống sót, NỘI DUNG thì không** |
| **Apr 25-28** | **Sprint 7→11B.** Sprint 7/8 ship "Docs-Only STRICT" → grounded 74.7%→80.3% **nhưng `real_answered = 34.3%, refuse = 63.7%`** (`HISTORY:2169-2178`). Sprint 9 enforce 3-key identity, phát hiện index của migration 0011 **âm thầm phá hợp đồng 3-key trên prod từ 0039** (`HISTORY:2083-2087`). Sprint 10 = "VERSION 1", 7.5/10, 772 test. Sprint 11B ship migration RBAC — **blocker P0 suốt 7 sprint** |
| **Apr 29** | **Auditor loop 1-8.1.** Điểm 5.1/10 → **8.9/10**; real-answered **1.9% → 50.7%**; phantom citation → **0** (`HISTORY:1789-1863`). Loop 8 bật Jina reranker: top_score **0.017 → 0.2285 (×13.4)** (`HISTORY:1755-1760`) |
| **Apr 30 – May 1** | **MEGA R1-R7.** Root cause tìm ra một cách trung thực: **`document_chunks.embedding` NULL cho CẢ 209 chunk** — vector retrieval **CHẾT**, bot chạy **chỉ bằng BM25**, và cái "top_score 0.017" mà cả team đang tune **là điểm BM25 bị đọc nhầm thành cosine** (`HISTORY:1396-1405`) |
| **May 1-7** | **V-series (V2→V17).** Jina v3 embedding = *"**cú mở khóa lớn nhất năm**"* (`HISTORY:1018`). V3/VH đạt 100% trên bộ CŨ. V8 nâng tenant INT → `record_tenant_id` UUID. V10 ship 4-key identity. **V11 đổi tên `embedding_v3` → `embedding` — ĐÂY LÀ NGUỒN GỐC sacred rule no-version-ref.** **V16 BỊ REVERT.** **90Q gate của V17 CHƯA BAO GIỜ CHẠY** |
| **May 7** | 🔴 **NGÀY PHÁN XÉT.** Hội đồng thẩm định **bác điểm tự chấm 8.1/10 của team → 4.8/10**, với phát hiện **đến giờ vẫn chi phối codebase này**: *"**HALLU=0 sacred đã trở thành BỘ TỐI ƯU HÓA TỪ CHỐI**"* (`HISTORY:2687`) |
| **May 9 – Jun 16** | Coder team · MoM · ZeroEntropy · Expert Build. **23 nhánh `coder-260509-*` đẩy lên chờ merge** (`HISTORY:3199-3204`) — **git re-init NUỐT SẠCH.** Report 27-stream "Master-of-Masters" được `CLAUDE.md` gọi là truth-of-record file — **VÀ KHÔNG TỒN TẠI TRÊN Ổ ĐĨA** |

## Chương 1 — Squash và re-init (2026-06-17/18)

`9d2fee9` — **240 migration → 1**. Rồi git bị re-init.

**Lời giải thích DUY NHẤT còn sót**, `STATE_SNAPSHOT.md:826`:
> *"Source cleanup: removed reports/ var/parsed_md/ plans/ scratch/ test_results/ … **Git history reset.**"*

**KHÔNG ADR. KHÔNG lý do. KHÔNG ghi chú rollback.**

⚠️ **Chưa giải thích được**: `STATE_SNAPSHOT.md:3` và `README.md:283` đều nói reset **2026-06-14**; commit đầu tiên là **2026-06-17**. **3 ngày không ai biết đi đâu.**

## Chương 2 — Phẫu thuật god-file (Jun 19) — **chiến dịch SẠCH DUY NHẤT**

`630e2c8`→`17eaac6`, Phase A-D. `query_graph.py` **3945 → 2828 dòng**, 9 routing decider tách ra `nodes/routing.py`, **xanh ở mọi bước**.
→ **Chiến dịch DUY NHẤT trong toàn bộ lịch sử KHÔNG có revert và KHÔNG có công việc mắc kẹt.**

## Chương 3 — Sụp đổ provider (Jun 26) — **1 ngày, 7 migration**

Key Jina chết (`403 error 1010`, hết tiền) → stack sụp theo dây chuyền, **mỗi migration sửa cái hỏng do migration trước gây ra**:

```
rerank_swap_to_zeroentropy
  ↓
embed_swap_to_openai
  ↓
drop_jina_bindings          ← binding per-bot đang ĐÈ system_config → bot vẫn gọi account chết
  ↓
rebind_embedding_openai     ← xóa binding làm resolve_embedding rơi về CHAT model → litellm embed 503
  ↓
chat_swap_to_innocom        ← OpenAI rồi 429: "đường answer LLM đã chết"
  ↓
embed_swap_to_zeroentropy_1280  ← OpenAI embed cũng 429; thang matryoshka của ZE KHÔNG có 1024
                                 → CỘT NỚI RỘNG LÊN 1280, TOÀN BỘ VECTOR NULL, CACHE CẮT, HNSW DỰNG LẠI
  ↓
revive_grounding_slot_innocom
```

**Stack hiện tại**: embed `zembed-1`@1280 · rerank `zerank-2` · answer LLM Innocom gateway (`openai/claude`)
⚠️ **`README.md:270-272` VẪN document stack Jina — LỖI THỜI 3 TUẦN.**

## Chương 4 — Các wave agent song song (Jun 24 → Jun 30)
**4 wave worktree agent. ĐÂY LÀ NƠI CÔNG VIỆC BIẾN MẤT** — xem Phụ lục H.

## Chương 5 — Grind stats/ADR-0008 (Jul 1 → Jul 10)
7 patch liên tiếp vào **cùng một** đường stats-serve — xem Phụ lục I.

## Chương 6 — Cái kết trung thực (Jul 10 → 13)

**Phát hiện quan trọng nhất đời dự án — và nó KHÔNG PHẢI phát hiện về RAG** (`STATE_SNAPSHOT.md:11-16`):

> *"**innocom phá ~42%** (33% cụt mid-generation + 8% 503). Bot/RAG thực ra **~89% coverage, HALLU=0**.
> → **RAG KHÔNG phải vấn đề; độ tin cậy innocom dưới tải LÀ vấn đề.**"*
>
> *"burst concurrent → **24/24 = finish_reason "stop" KỂ CẢ câu cụt giữa** → truncation **KHÔNG detect được**… fix 'validate finish_reason' = **VÔ DỤNG**."*

> 🔴 **Provider NÓI DỐI về `finish_reason`. Truncation KHÔNG PHÁT HIỆN ĐƯỢC. Chỉ có PHÒNG NGỪA (giảm tải) mới ăn thua.**
> Concurrency 16→6 kéo truncation **33% → ~7%**.

---

# PHỤ LỤC H — REVERT · NGÕ CỤT · NHÁNH MẮC KẸT

## H.1 — 4 REVERT TƯỜNG MINH

| commit | ngày | undo cái gì | lý do (body) |
|---|---|---|---|
| **`9416f4d`** | 06-29 | **Revert "F7 attribute-generic stats index — every numeric column queryable"** (`5db7922`, **cùng ngày**). **312 dòng + file test 176 dòng bị xóa** | 🔴 **BODY RỖNG** — chỉ *"This reverts commit 5db7922…"*. **GIT IM LẶNG VỀ LÝ DO** |
| `6796cd9` | 07-02 | **revert(ING-F1): khôi phục pure-money fallback — QUYẾT ĐỊNH CỦA OWNER.** `document_stats.py` về trạng thái trước `4e83410` | ✅ Có document: allowlist gate phá 2nd-price out-of-vocab; redesign denylist fix được nhưng *"làm phình từ vựng header baked-in"*. **Owner BỎ HẲN fix.** Q13 stock-as-price = **KNOWN limitation**, workaround per-bot qua `custom_vocabulary["column_roles"]` |
| `ed26e1b` | 07-06 | **Step-17 P1 digit-signature route — explored + reverted** | ✅ *"zero delta + brand-conflation defect."* Chỉ giữ file evidence JSON 7.9k dòng. **Ngõ cụt SẠCH, ghi chép ĐÚNG** |
| `143ff38` | 07-03 | **khôi phục stats-route grounding gate** — undo một **REVERT IM LẶNG** | xem H.2 |

### 🔴 **F7 CHÍNH LÀ ADR-0007**

`5db7922` (*"every numeric column queryable"*) **chính xác là thứ `docs/adr/0007-stats-price-index-to-attribute-index.md` ĐANG ĐỀ XUẤT** — và ADR đó vẫn ở trạng thái **Proposed / CHƯA LÀM** (`STATE_SNAPSHOT.md:162`: *"⏳ Betrayal #1 (PRICE-index) NOT done"*).

> **Công việc đã được BUILD, bị REVERT KHÔNG LÝ DO, và giờ đang được LÊN KẾ HOẠCH BUILD LẠI TỪ ĐẦU.**
> **Implementation lấy lại được bằng `git show 5db7922`.**
> **Đây là "fix đi fix lại" RÕ RÀNG NHẤT trong toàn repo.**

## H.2 — 🔴 REVERT IM LẶNG: **6 NGÀY TẮT LƯỚI HALLU**

`143ff38` là **bằng chứng DUY NHẤT**:

> *"commit `3097755` đã revert cái stats-route grounding skip có-gate-per-bot (`062d6fa`) thành **SKIP VÔ ĐIỀU KIỆN** — **mọi câu trả lời stats/aggregation bỏ qua rerank+grade VÀ bỏ qua grounding judge**, **mở lại lỗ hổng đã được git ghi nhận** (một con số tồn kho rò rỉ từ lịch sử được trích dẫn trên câu trả lời stats, không kiểm tra). Knob và default **vẫn còn** nhưng **node KHÔNG ĐỌC CHÚNG NỮA** — comment "Per-bot overridable" là **SAI SỰ THẬT**."*

```
062d6fa (06-25)  gate grounding cho stats-route, per-bot            ✔
3097755 (06-27)  ÂM THẦM BỎ GATE trong 1 commit "integrate"          ✗ ← 6 NGÀY
143ff38 (07-03)  khôi phục                                           ✔
```

> **6 ngày lưới HALLU TẮT trên mọi câu trả lời stats, và comment trong code VẪN NÓI LÀ ĐANG BẬT.**
> **Đây là phát hiện NGHIÊM TRỌNG NHẤT trong báo cáo lịch sử.**

## H.3 — CLIFF FLOOR: **62 NGÀY CODE GIỮ GIÁ TRỊ ĐÃ BỊ TỪ CHỐI**

`_archive_pre_squash_20260618/20260508_0068_recalibrate_cliff_strategy.py:9`:
> *"`rerank_cliff_absolute_floor = 0.15` — **floor lifted from 0.05**"*

```
2026-05-08  DB: 0.05 → 0.15    (đo, TỪ CHỐI 0.05)
2026-06-17  code (cd08119) VẪN = 0.05     ← 62 NGÀY code SSoT giữ giá trị ĐÃ BỊ ĐO VÀ BÁC
2026-07-09  code (764f559) → 0.2
```

> **Suốt 62 ngày, mọi DB fresh clone chạy floor ĐÃ BỊ TỪ CHỐI.**

## H.4 — 10 NHÁNH MẮC KẸT (`git merge-base --is-ancestor` → **KHÔNG PHẢI ANCESTOR** cả 10)

### ✅ Tip mắc kẹt nhưng **NỘI DUNG ĐÃ LANDING** (re-implement — pointer thừa vô hại)

| nhánh | tip | landing qua |
|---|---|---|
| `worktree-wf_010411a2-303-{1,2,3}` | RLS S0-A / structured-output S0-C / multi-turn S0-D | `24f2451` |
| `worktree-wf_1b986f6a-ee6-{1,2,3}` | late-binding / fail-closed grounding / ING-7 purge | `3097755` + `16710f3` ⚠️ **nhưng integration ÂM THẦM BỎ MỘT GATE — xem H.2** |
| `worktree-wf_8b1f25be-baf-{1,2,3}` | VLM prompt / loadtest-bypass / SSRF guard | `eafddaa` |
| `wt/p0-2-coverage-gate` (`75f5c96`) | char-coverage gate | `d7bd5ac` *"**salvaged from Wave-1**"* |
| `wt/p0-3-locale-pack` (`0a06de8`) | locale word-lists | `c521c37` |

### 🔴 MẮC KẸT **VÀ MẤT** — cụm `integ-260624-wave1` (2026-06-24)

`git diff HEAD...integ-260624-wave1` = **102 file, 5,885 insertion**.
*(Đã probe HEAD cho MỌI file test mà từng nhánh đưa vào — **tất cả đều thiếu**)*

| nhánh | tip | chứa gì | HEAD |
|---|---|---|---|
| `worktree-agent-a7de9273f7913ef58` | **`be94f58`** | reranker `reranker_used` fix · retrieve fan-out concurrency · BM25 soft-delete · grade safety-floor exempt · **pgvector segment language gate** · entity-fairness wire. **6 file test, 600+ dòng** | ❌ **MẤT** — cả 6 test **vắng mặt** |
| `worktree-agent-a98b47eb8ed705bb5` | `cc9880c` | **RBAC gating cho route ghi/xóa `/test_chat`** + `test_rbac_test_chat_destructive.py` (**229 dòng**) | ❌ **MẤT.** Xác nhận: `grep -c require_min_level test_chat/*.py` → **chỉ `bot_insights_routes.py` có**. `document_routes` · `chat_routes` · `admin_routes` · `bot_admin_routes` · `monitoring_routes` = **ZERO**. **13 route ghi/xóa VẪN TRẦN** |
| `worktree-agent-a0fa71ce3f08c42a8` | `4b94c28` | **IDOR write-fence trên 4 repository** · RLS force-parity · `test_idor_write_fence.py` (**261 dòng**) · `test_rls_policy_force_coverage.py` (**164 dòng**) | ❌ **MẤT** |
| `worktree-agent-aeec8ebb27a838533` | `5d6fb6d` | token-ledger rollup + decorator emit + admin-metrics RBAC (**3 file test, 468 dòng**) | ❌ **MẤT** |
| `worktree-agent-a63d8bbc3a41831eb` | `51c7d20` | `shared/structured_blocks.py` (**112 dòng, mới**) · narrate language threading · late-chunking batch ceiling | ❌ **MẤT** |
| `worktree-agent-a24be5228e8b4b1ca` | `548e1c5` | stats `entity_synonyms` + Aliases role + checker/normalizer, **4 file test (461 dòng)** | ⚠️ **SPLIT-BRAIN** |

### 🔴🔴 **SPLIT-BRAIN** — schema ship, code + test thì không

`alembic/versions/20260624_stats_index_entity_synonyms.py` **CÓ trên HEAD** (verified `git cat-file -e HEAD:...`), **nằm trong chain migration LIVE, và SẼ CHẠY trên MỌI DB**
— **nhưng nhánh sinh ra nó thì MẮC KẸT, và 4 file test của nó BIẾN MẤT.**
`document_stats.py` chỉ còn **tham chiếu vụn** (dòng 198, 336, 747).

### 🔴 MẮC KẸT **VÀ LÀM LẠI NHỎ HƠN 60×** — `worktree-agent-a9cb02acde213990b`

```
dcdc55a (06-30)  "B-FORMAT route DOCX/PDF/HTML/XLSX tables through canonical row-split converter"
                 7 file, 489 insertion, 2 FILE TEST
                 (test_multiformat_table_row_split.py 192 dòng · test_tabular_markdown_backward_compat_pin.py 78 dòng)
                 → KHÔNG BAO GIỜ MERGE
7e8dd38 (07-01)  "P1 wire DOCX tables through canonical converter (B-FORMAT)"
                 1 file, 8 insertion, 0 TEST      ← BẢN ĐANG DÙNG
```
> **Bản toàn diện bị bỏ, bản nhỏ hơn 60× được ship thay.** Cả 2 file test **vắng mặt trên HEAD**. `git show dcdc55a`.

### Chỉ **4** nhánh worktree từng được merge đàng hoàng
`b3d3b01` · `9163295` · `50baf9b` · `1870df7` (2026-06-30)

---

# PHỤ LỤC I — SỔ ĐĂNG KÝ FIX-REFIX

| vị trí | lần đụng | commit (thứ tự) | đổi rồi ĐỔI NGƯỢC? | trạng thái hiện tại | **hiện tại có phải TRẠNG THÁI ĐÃ BỊ TỪ CHỐI?** |
|---|---|---|---|---|---|
| `_01_*.py:162` `DEFAULT_RERANK_CLIFF_ABSOLUTE_FLOOR` | **3 giá trị, 2 kho** | archive `0068` (05-08) DB 0.05**→0.15**; `cd08119` (06-17) code **=0.05**; `764f559` (07-09) code **→0.2**; `20260709_seed_cliff_floor_mmr_parity` seed DB **0.2** | ⚠️ **CÓ — code giữ 0.05 suốt 62 NGÀY SAU KHI DB đã bác nó.** Code và DB **bất đồng toàn bộ thời gian đó** | **0.2**, code+DB cuối cùng đã khớp | **Không — 0.2 là mới.** NHƯNG **0.05 là giá trị ĐÃ BỊ TỪ CHỐI mà code vẫn phục vụ từ 05-08 đến 07-09.** Mọi fresh-DB clone trong cửa sổ đó chạy floor bị bác |
| `_14_*.py:235` `DEFAULT_MMR_SIMILARITY_THRESHOLD` | 2 | `cd08119` **=0.88**; `9f93804` (07-04) **→0.98** *"measured threshold recalibration"*; `20260709_...` seed DB **0.88** | **Không revert — CỐ Ý PHÂN KỲ.** Migration docstring: *"constant là 0.98, production DB là 0.88. **Chúng ta CỐ Ý KHÔNG đụng constant**… việc flip 0.98 là một quyết định đo-lường RIÊNG"* | **Code nói 0.98. DB nói 0.88. Prod chạy 0.88** | ⚠️ **Constant 0.98 là CODE CHẾT — nó CHƯA BAO GIỜ có hiệu lực ở bất kỳ đâu mà seed đã chạy.** Bất kỳ DB fresh dựng **TRƯỚC** migration 07-09 chạy **0.98**; dựng **SAU** chạy **0.88**. 🔴 **ĐÂY LÀ NHÀ MÁY SẢN XUẤT PHÂN KỲ — ƯU TIÊN ĐÓNG SỐ 1** |
| `_10_rbac.py:180` `DEFAULT_GRADE_TIMEOUT_S` | 2 | `cd08119` **=2.0**; `5c4fdda` (07-13) **→3.0** | Không revert | **3.0** | **Không** — và code **giờ đã document vì sao 2.0 sai** (`_10_rbac.py:168`). ⚠️ **NHƯNG fix này SAI TẦNG** — xem `CONFIG_FLAG_HISTORY_AUDIT §4`. `grade_timeout_s` **KHÔNG** trong chain seed active → **DB vẫn có thể giữ giá trị cũ** |
| **stats-serve** — `shared/document_stats.py` + `stats_index_repository.py` + `nodes/retrieve.py` | 🔴 **7 lần trong 12 ngày** | `949a3a4` (07-01 count dispatch) → `aa029ec` (07-02 cross-doc fragment reconcile) → `eb750f0` (07-06 price-absent marker) → `ec4a335` (07-06 null-price point-lookup) → `d495db2` (07-07 ADR-0008 shape/value typing) → `2ad4df7` (07-07 shape-name **thắng** category) → `d4de411` (07-10 sparse-drop) | **Không revert, NHƯNG `2ad4df7` landing SAU `d495db2` ĐÚNG 1 NGÀY để sửa `d495db2`.** Mỗi patch sửa edge-case của patch trước | ADR-0008 shape-typing active; **3 alembic per-bot ghim vào 1 bot slug** | 🔴 **Đây KHÔNG phải fix-refix, đây là FIX-CASCADE — 7 patch vào 1 module vì MÔ HÌNH NỀN SAI** (price là column-type hạng nhất). **ADR-0007 gọi tên đúng cái fix và CHƯA ĐƯỢC THỰC THI. Xem dòng F7** |
| **`document_stats.py` phát hiện price/money** (ING-F1) | 3 | `4e83410` (07-02 allowlist gate) → denylist redesign → **`6796cd9` (07-02) REVERT TẤT CẢ** | ⚠️ **CÓ — revert TOÀN BỘ, CÙNG NGÀY, "owner decision"** | **Trạng thái TRƯỚC `4e83410`.** Pure-money fallback khôi phục. Q13 stock-as-price = **KNOWN limitation ĐƯỢC CHẤP NHẬN** | 🔴 **CÓ — trạng thái hiện tại CHÍNH LÀ trạng thái trước-fix.** Revert có document và có chủ đích, nhưng **bất kỳ engineer nào "phát hiện" bug stock-as-price sẽ XÂY LẠI `4e83410` trừ khi họ đọc `6796cd9`**. **ĐÂY LÀ FIX KHÔNG ĐƯỢC THỬ LẠI** |
| **F7 / ADR-0007 attribute-generic stats index** | 2 | `5db7922` (06-29 **BUILD**: 312 dòng, test 176 dòng) → **`9416f4d` (06-29 REVERT, body RỖNG)** | 🔴 **CÓ — build và revert TRONG CÙNG MỘT NGÀY, KHÔNG NÊU LÝ DO** | **Vắng mặt.** Được **đề xuất lại** thành `docs/adr/0007` (**status: Proposed, CHƯA LÀM**) | 🔴🔴 **TRẠNG THÁI HIỆN TẠI LÀ TRẠNG THÁI "TRƯỚC" ĐÃ BỊ BÁC-RỒI-TÁI-LẬP — và team ĐANG LÊN KẾ HOẠCH XÂY LẠI NÓ.** Implementation đã có sẵn ở `git show 5db7922`. **GIT IM LẶNG VỀ LÝ DO REVERT. TÌM RA LÝ DO = HÀNH ĐỘNG GIÁ TRỊ NHẤT TOÀN BÁO CÁO** |
| **stats-route grounding gate** — `nodes/guard_output.py` | 3 | `062d6fa` (06-25 gate ON per-bot) → `3097755` (06-27 **ÂM THẦM BỎ GATE**) → `143ff38` (07-03 khôi phục) | 🔴 **CÓ — REVERT IM LẶNG, 6 NGÀY, lưới HALLU TẮT trên mọi câu stats trong khi comment nói ngược lại** | **Gate ON** mặc định, per-bot opt-out | Không — hiện tại đúng. **NHƯNG nó là trạng thái ĐÃ ĐÚNG TỪ 06-25 và bị một commit "integrate" PHÁ HỦY. Fix HAI LẦN** |
| `shared/chunking/coverage.py` | 2 | `75f5c96` (06-29, trên nhánh **MẮC KẸT** `wt/p0-2-coverage-gate`, 263 dòng) → `d7bd5ac` (06-29 *"**salvaged from Wave-1**"*, 255 dòng) | Không phải revert — **CỨU HỘ** | **Trên HEAD, 255 dòng, ĐÃ WIRE** — `ingest_stages.py:889-890` gọi `check_chunk_gaps` | Không. Sạch. **Là công việc DUY NHẤT từ nhánh mắc kẹt được cứu thành công.** Delta 8 dòng chỉ là constant chuyển vào SSoT |
| **B-FORMAT table row-split** — `tabular_markdown.py` + parsers | 2 | `dcdc55a` (06-30, **MẮC KẸT**, 7 file/489 ins/**2 file test**) → `7e8dd38` (07-01, landing, **1 file/8 ins/0 test**) | Không phải revert — **LÀM LẠI Ở 1/60 QUY MÔ** | Patch tối thiểu trên HEAD; **implementation toàn diện + 270 dòng test nằm bất khả tiếp cận trên 1 nhánh** | 🔴 **Trạng thái hiện tại là phiên bản YẾU HƠN HẲN của công việc ĐÃ TỒN TẠI.** Lấy lại: `git show dcdc55a` |

## I.KẾT — Trả lời câu hỏi quan trọng nhất

> **"Có phải giá trị/trạng thái hiện tại là thứ đã từng bị thử và TỪ CHỐI không?"**

# **CÓ. HAI LẦN.**

**1. ING-F1 / stock-as-price** — code hiện tại **CỐ Ý** là trạng thái trước-fix. Xây lại = **lặp lại công việc đã bị bác**.
*(Việc bác bỏ **CÓ document** → an toàn, **NẾU người ta đọc**.)*

**2. F7 / attribute-generic stats (ADR-0007)** — code hiện tại là trạng thái trước-fix, fix **đã được xây rồi vứt đi KHÔNG LÝ DO GHI LẠI**, và team **đang lên kế hoạch xây lại từ đầu**.
*(Việc bác bỏ **KHÔNG có document** → 🔴 **NGUY HIỂM CHỦ ĐỘNG**.)*

**Và 1 phân kỳ ĐANG SỐNG**: **`DEFAULT_MMR_SIMILARITY_THRESHOLD = 0.98` trong code là một giá trị mà production CHƯA BAO GIỜ CHẠY.**

---

# PHỤ LỤC J — 🛡️ TRÍ NHỚ THỂ CHẾ CHỈ CÒN TRONG COMMENT

> Git bị xóa. **Một số quyết định CHỈ còn sống trong comment code.**
> **CẤM "dọn dẹp". Một PR "tidy comments" sẽ xóa sạch 2 tháng đo đạc.**

## J.1 — MƯỜI CÁI KHÔNG THỂ THAY THẾ

| file:line | Quyết định nó ghi lại | Vỡ gì nếu "dọn" |
|---|---|---|
| `orchestration/nodes/routing.py:201` | *"Production audit (**req 9cf611b5**) found reflect firing **2× per turn (3.57s wasted)** on bots that never enabled it."* | Bản ghi **DUY NHẤT** vì sao `reflect` tắt. Ai đó "bật tính năng chưa dùng" → **âm thầm thêm lại 3.57s/turn**. *(Nhân bản ở `_01_*:262` kèm ngày 2026-05-18)* |
| `ingest_stages_enrich.py:232` | *"per-chunk nano with full-doc context = 19k tokens/call = **O(n²) storm**, chunks=0 until it finished… **Two CR impls existed — disabling #1 alone left this one firing** (the 'whack-a-mole' root cause). Re-enable ONLY without Jina late_chunking."* | **Toàn bộ post-mortem** của blocker ingest — thứ **ép ra quyết định đổi stack Jina→ZE**. **KHÔNG GÌ KHÁC** ghi lại việc có **HAI** bản contextual-retrieval |
| `ingest_stages_enrich.py:445` | *"**Do NOT re-enable expecting 'more context'** — re-enabling brings back the O(n²) storm."* | **Biển báo mìn** trên một config **trông như đã chết**. "Cải tiến hiển nhiên" **chính xác là hành động bị cấm** |
| `infrastructure/repositories/ai_config_repository.py:39` | *"this field was hardcoded `None` since commit **93b1258 (2026-05-12)**, so the secrets resolver always returned an empty string and **LiteLLM silently fell back to `OPENAI_API_KEY` for every provider**… root cause of the Innocom swap failure."* | Giải thích vì sao OpenAI/Anthropic "chạy được" trong khi **MỌI** provider không-mặc-định **âm thầm hỏng**. **Bẫy silent-fallback kinh điển**; xóa nó = **đảm bảo tái diễn** ở lần onboard provider tiếp theo |
| `infrastructure/llm/dynamic_litellm_router.py:469` | *"a gateway that fails a SCATTERED 10-30% never produces `fail_max` failures in a row… (**measured 2026-07-13: 236 provider failures, ZERO opens**)"* → LLM CB dùng **rate** mode; embedder/reranker giữ consecutive mode | **Bằng chứng ĐO ĐƯỢC** rằng CB đếm-liên-tiếp là **no-op** với hình dạng lỗi thật. Không có nó, ai đó "hài hòa hóa" mọi breaker về lại → **LLM CB ngừng hoạt động** |
| `shared/constants/_10_rbac.py:54` + `dynamic_litellm_router.py:773` | *"**Root-cause 2026-06-13**: the async grounding judge saturated all provider slots and the next turn's `generate` queued behind them → **p95 24-37s** while steady-state was 3-5s."* + *"Selection is driven **SOLELY by the explicit `background` flag, never by `purpose`**."* | **Sự cố latency tệ nhất dự án.** `=4` trông tùy tiện; định tuyến theo `purpose` (cách đơn giản hóa **hiển nhiên**) **tái tạo lại starvation** |
| `_01_*:193` + `nodes/rerank.py:367` | Threshold-gate-after-cliff default OFF: *"double-gates and produces false-positive refuses at top_score 0.29-0.43 (**Wave J2 load-test 15Q: 27% refused**)"* | Load-test 27%-refusal là **biện minh DUY NHẤT** cho một flag **trông như safety check bị tắt bừa** |
| `orchestration/query_graph.py:2679` | *"**Do NOT append** the raw per-entity source chunks (`linked_chunks`): re-feeding the raw table rows changed answers (**tenant COVERAGE 1.00→0.90 in the B-1 A/B**)."* | **Regression 10 điểm** mã hóa thành 1 dòng cấm. Biến "không dùng" là **cái bẫy**; thêm nó vào context là **edit "hữu ích" tự nhiên nhất** |
| `shared/constants/_06_llm_defaults.py:131` và `:140` | (a) *"`pii_vi_cmnd` is **deliberately EXCLUDED**: its pattern is ANY bare 9- or 12-digit number — which in a catalog corpus includes **PRICES** (150000000 = 150 triệu is 9 digits) and SKUs."* (b) *"**a refusal sentence ≈ 5 matches, an instruction block ≈ 13-89, a 300-word verbatim dump ≈ 277.** A floor of 10 clears single-sentence refusals while still catching bulk extraction."* | **HAI "fix hiển nhiên" độc lập** (thêm rule PII còn thiếu; siết leak threshold về 1) — **mỗi cái đều phá production.** Cả hai **chỉ được biện minh ở đây** |
| `document_service/ingest_stages.py:751` | *"**Scope gate (2026-05-26)**: the preserve path is ONLY safe when parser intent is row-per-chunk. For markdown/plain-text, '1 chunk = whole document' produced a **single 74KB chunk for a 98KB legal corpus**"* — và gọi nó là *"root cause of the **V13 over-refuse cluster**"* | **Cả vì sao path tồn tại LẪN vì sao nó bị giới hạn hẹp.** Nới gate là **tổng quát hóa hấp dẫn tầm thường** với **tác động recall thảm khốc** |

## J.2 — SỔ ĐĂNG KÝ ĐẦY ĐỦ (~30 comment còn lại)

| file:line | quyết định | mất gì nếu xóa |
|---|---|---|
| `alembic/versions/20260709_seed_cliff_floor_mmr_parity.py:39` | *"**intentionally the live-parity value, NOT the code constant** (DEFAULT_MMR_SIMILARITY_THRESHOLD = 0.98)."* | Sự **CỐ Ý** của phân kỳ code/DB. "Sửa cho nhất quán" = **ship một thay đổi hành vi CHƯA ĐO** dưới danh nghĩa dọn dẹp |
| `_10_rbac.py:168` | *"**the earlier 2.0s undershot its own p95** (2.56s) and clipped the normal band."* | Ghi lại một **giá trị BỊ TỪ CHỐI**. Ai đó "siết" grade timeout về 2s = **lặp lại sai lầm** |
| `_01_*:72` | ZE reranker `latency_mode="slow"`: *"measured 2026-06-10: fast=725ms, slow=1118ms (chỉ +~400ms; **ghi chú '>10s' cũ là SAI**)"* | **SỬA LẠI NIỀM TIN SAI CỦA CHÍNH CODEBASE.** Quay về "fast" = **đụng trần 503** |
| `_10_rbac.py:157` | `DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE = 0.7` — *"production-tuned từ **trace fa7983c2-05f4-4ac7-b1e2-600ee5bdfba4** — top_score=0.91 phí 10683ms cho retry"* | Một **trace production ĐÍCH DANH**. 0.7 nếu không thì là **magic float không giải thích được** |
| `vector/pgvector_store.py:259` | Soft-delete denormalisation (Wave M3.5-C 2026-05-20): *"correlated EXISTS subquery chạy **per-candidate chunk** → **~80% chi phí retrieve p50 1.6s**"* | `doc_deleted_at` **trông như denormalisation thừa đang van xin được normalize đi**. Đây là **lý do nó tồn tại** |
| `vector/pgvector_store.py:512` và `:580` | *"**Verified 2026-06-19**: giả định trước đó 'BM25 đã khớp literal token' **đúng với keyword query, KHÔNG đúng với câu hỏi tự nhiên**"* / *"struct params **PHẢI giữ bound**… bỏ chúng → `InvalidRequestError: struct_p0 has no value` trên MỌI structural-pointer query"* | Cái đầu **lật đổ một niềm tin sai vẫn còn viết ở 20 dòng phía trên nó**; cái sau **bảo vệ param trông như đồ thừa** |
| `ingest_stages_final.py:263` | *"2 bug gây **4 doc stuck DRAFT 25+ phút trong prod 2026-05-13**… Bug B: KHÔNG có code flip state='active'; Bug A: log 'ingested' SUCCESS dù chunks_null_embedding > 0"* | **3 bug prod** và **vì sao state-flip phải nằm trong MỘT transaction** |
| `ingest_stages_final.py:275` | *"Parent-child mode **cố ý KHÔNG embed** parent chunk… Đếm parent NULL là fail là SAI (**regression đưa vào 2026-05-13**)"* | Parent NULL-embedding **trông như data bug**; "fix" nó = **phá lại ingest thành `failed`** |
| `ingest_stages_final.py:403` | *"log này trước đây bắn **vô điều kiện** — phát `document_ingested` cho cả doc THẤT BẠI… **một lời nói dối observability**"* | Bỏ guard = **làm hỏng tín hiệu của recovery worker** |
| `ingest_stages.py:304` | *"**Production bug 2026-05-18**: `_sanitizer` không phải lúc nào cũng init… AttributeError trên **4/4 ingest_clean row**"* | Giải thích một `getattr(self, "_sanitizer", None)` mà **MỌI linter sẽ gắn cờ** |
| `ingest_stages.py:649` | *"**DEPRECATED 2026-05-14** — Legacy text-flatten path. **Giữ NGUYÊN VĂN làm nhánh default**… **ĐỪNG XÓA** — Block pipeline là opt-in"* | Đánh dấu một path **trông như chết** nhưng **LÀ DEFAULT ĐANG SỐNG** và là **cần gạt rollback** |
| `ingest_core.py:256` | *"**2026-05-27** — sniff MIME thật khi declared mơ hồ. Đóng **bug silent-fail: parser registry trả None cho octet-stream upload → 0 chunk ingest**"* | **Sự cố khai sinh** ra tầng byte-sniff, **giờ là sacred ingest rule trong CLAUDE.md** |
| `sysprompt_assembler.py:61` | *"Chỉ match legacy shape là **bug GEN-F6**: `sysprompt_rules_disabled` là **no-op im lặng** — phá điều kiện per-bot opt-out của sacred exception"* | Đơn giản hóa regex 2-shape = **phá lại lối thoát prompt-append HỢP PHÁP DUY NHẤT của platform** (ADR-W1-S10) |
| `reranker_resolver.py:304` | *"system_config drift (provider 'jina' ⊥ model 'zerank-2') **âm thầm hạ cấp MỌI bot không-binding xuống NullReranker**. Fail LOUD."* | Lý do một warning tồn tại ở nơi mà **quiet fallback trông sạch hơn**. **ĐÂY CHÍNH LÀ bẫy reranker-silent-disable** |
| `_14_*:209` | Async grounding `DEFAULT=False`: *"**CORE MVP đặt T1 (no-hallu) TRÊN T2 (cái lợi -1.5s latency)**… Rollback rule: nếu grounding_fail_total breach > 0/tuần sau khi bật, lật lại"* | Một **cái lợi perf ĐÃ ĐO nhưng CỐ Ý TỪ CHỐI**, kèm **rule rollback đã thỏa thuận trước**. Reviewer thuần-latency **sẽ lật nó** |
| `_00_app_env_taxonomy.py:218` | `DEFAULT_EF_SEARCH` *"**Wave M3.6-F2 2026-05-20: hạ 100→64**… 100 là điểm lợi-ích-giảm-dần 1.56×. Recall giữ ≥95%"* — **và giá trị cũ được GIỮ LẠI dưới dạng dòng comment ở `:222`** | Ghi lại **giá trị bị từ chối + bằng chứng recall**. 🔴 **Dòng `= 100` bị comment là LỊCH SỬ CỐ Ý, KHÔNG PHẢI RÁC** |
| `_01_*:188` | *"**A/B 2026-06-08: prune aggregation làm rớt combo-price row (đúng -16pp)**. Synthesis/multi_hop được lợi (+4..+6pp); aggregation/comparison thì không"* | **Delta A/B per-intent**; không có nó, tuple exemption **trông như hack** |
| `_01_*:213` | Cliff-skip intents: *"gap-cut của cliff làm rớt answer chunk (**đo được: một multi_hop trên corpus pháp lý → CHỈ 1 chunk sống sót**)"* | **Failure mode ĐÃ ĐO** đằng sau một frozenset **trông như thiên vị intent** |
| `nodes/generate.py:542` | *"default 2900 char quá chật (**verified 2026-05-21: turn '1tr499 có mấy dịch vụ' làm rớt 3/7 graded chunk**)"* | **Tái hiện cụ thể của bug K1 aggregation** — cái mà CLAUDE.md trích dẫn làm **cái giá của psql hot-fix** |
| `query_graph.py:2108`, `:2320` | *"Measured A/B (5×): **-1.1s/turn với answer KHÔNG ĐỔI**"* / *"**-0.67s** trên nền tiết kiệm fanout"* + *"`parse_code_query` yêu cầu MỘT CHỮ CÁI, nên anchor chỉ-có-số 'Điều 34' của luật không bao giờ khớp"* (**chứng minh domain-neutral**) | **HAI A/B đã đo** VÀ **lập luận an toàn domain-neutral** cho một bypass **trông như hack** |
| `nodes/guard_output.py:265` | Brand-scope gate default **observe**: *"truth-audit step20: `<brand>` bị deny trong khi 50+ SKU tồn tại… Default observe = chỉ log, **ĐO tỉ lệ từ-chối-sai TRƯỚC KHI bất kỳ bot nào opt vào block**"* | **Sự cố + kỷ luật rollout observe-trước-block CÓ CHỦ ĐÍCH** |
| `_02_*:66`, `:94` | *"matryoshka 1280 vì **pgvector HNSW trần 2000 dim** (full 2560 cần halfvec)"* / *"'Điều 3?' đang thua các chunk có prefix nói 'Đoạn 3…'"* + *"**BẮT BUỘC re-embed sau khi toggle**"* | **Lựa chọn dimension**, **failure mode retrieval**, và **một tiền điều kiện vận hành CỨNG** |
| `_05_embedding_circuitbreaker.py:6` | CB `fail_max` **5→10** *"sau khi thêm admission control: một **burst request TỰ GÂY RA đang trip nó ở mức 5**"* | Ghi lại **giá trị bị từ chối** + **sự ghép cặp nhân quả giữa semaphore và CB — chúng PHẢI di chuyển CÙNG NHAU** |
| `_13_adapchunk_ocr_parser.py:12` | Parser default `simple`→`kreuzberg`: *"layout-aware **F1 ~91% parity với Docling, nhanh ~9×**. Theo **Wave C2 winner**"* | **Kết quả bake-off.** **KHÔNG GÌ KHÁC** ghi lại việc Docling **đã được đánh giá và THUA** |
| `_12_multi_stage_retrieval_fallba.py:56` | *"theo **phán quyết deep-audit expert**, LLM metadata extraction lúc query-time là **SAI TẦNG**"* | Một **kiến trúc bị từ chối**, **không được ghi ở đâu khác** |
| `infrastructure/events/redis_streams_bus.py:531` | *"**260525 Bug #11** — NOGROUP auto-recover… Trước fix, vòng lặp **spam CÙNG MỘT lỗi mãi mãi**"* | **Bug đích danh** + kịch bản vận hành. Trông như **noise phòng thủ**; thực ra là **fix vòng lặp vô hạn ĐÃ VERIFY** |
| `structured_output_helper.py:428` | *"litellm mặc định AsyncOpenAI `max_retries=2`, **stack dưới retry loop của caller** — **244 dòng 'Retrying request' không phối hợp trong load-test**"* | **Bằng chứng ĐO ĐƯỢC** cho chính sách single-retry-layer (commit `213b3d2`/`91163d5`/`8251944`) |
| `model_resolver/_cache_mixin.py:234` | *"Cold-cache **P99 giảm từ ~400ms xuống ~50-100ms**"* nhờ thay gather fan-out bằng **1 batch SQL** | Chống lại một **revert thiện chí về `asyncio.gather`** theo chính rule gather-first của repo |
| `ingest_stages_enrich.py:375` | *"burst rộng-bằng-Semaphore chạy đua **TRƯỚC KHI response đầu tiên kịp seed cache** — đợt mở màn chỉ cache **~26-54% so với ~97% khi đã ấm**. **Seed MỘT enrich TUẦN TỰ, rồi mới fan out**"* | Lời gọi tuần tự cố ý bên trong một async fan-out **trông như một `gather()` bị bỏ sót** |
| `contextual_chunk_enrichment.py:185` | *"**verified 2026-06-13: gpt-4.1-mini cache ~98.5%**… Chỉ đọc field của Anthropic khiến hit-rate của OpenAI **đọc ra thành 0**"* | **Tỉ lệ cache-hit đã đo** + **bug observability mà nó đã sửa** |
| `google_link_service.py:214` | Google Docs export dưới dạng **docx, KHÔNG PHẢI txt**: *"parser docx khôi phục được heading style (**87 heading trên 1 Thông tư vs 0 với txt phẳng**)"* | `format=txt` **trông đơn giản hơn** và sẽ **âm thầm PHÁ HỦY toàn bộ cấu trúc tài liệu** |
| `domain/entities/document.py:110` | *"`source_url` **KHÔNG bắt buộc**… Bắt buộc nó **từ chối MỌI bytes-upload lúc load entity, phá rechunk-by-id**"* | Một invariant thiếu **trông như sơ suất** |
| ~25 file (`cag_service.py:2`, `mcp_tool_client.py:2`, `hyde/llm_hyde.py:2`, …) + `model_resolver/service.py:702` | Đồng nhất: *"**DEAD-CODE NOTICE — 2026-06-03**… never wired into bootstrap… Zero external imports"* + **công thức hồi sinh 3 bước** | **Xác minh CÓ NGÀY THÁNG** rằng chúng chưa wire, **và các bước chính xác để hồi sinh**. Xóa file = mất design; xóa header = mất audit |

---

# PHỤ LỤC K — 5 VIỆC PHẢI LÀM VỚI LỊCH SỬ NÀY

| # | Việc | Vì sao |
|---|---|---|
| **1** | 🔴 **TÌM RA VÌ SAO F7 BỊ REVERT** (`9416f4d`, body rỗng) | Team **sắp xây lại nó** thành ADR-0007. Implementation ở `git show 5db7922`. **HÀNH ĐỘNG GIÁ TRỊ NHẤT TOÀN BÁO CÁO** |
| **2** | 🔴 **ĐÓNG PHÂN KỲ MMR** | `DEFAULT_MMR_SIMILARITY_THRESHOLD = 0.98` là **giá trị production CHƯA BAO GIỜ CHẠY**. Hoặc flip prod (có đo), hoặc set constant về 0.88. **Hiện tại, THỜI ĐIỂM bạn dựng DB QUYẾT ĐỊNH hành vi retrieval của bạn** |
| **3** | 🔴 **RE-SEED hoặc CHÍNH THỨC TỪ BỎ 75 key `system_config` đã mất** — bắt đầu từ **`guardrail_rules`** và **`chunking_policy`** | **Fresh clone và prod KHÔNG PHẢI cùng một hệ thống hôm nay** |
| **4** | 🔴 **TRIAGE `integ-260624-wave1` TRƯỚC KHI XÓA** | Nó mang **~5,900 dòng** gồm **IDOR write-fence**, **RBAC cho route ghi/xóa** (verified **VẪN TRẦN trên HEAD**), và **test cho một migration ĐANG NẰM TRONG CHAIN LIVE**. Và `dcdc55a` — **270 dòng test B-FORMAT bị vứt để lấy 1 patch 8 dòng** |
| **5** | 🛡️ **ĐÁNH DẤU CÁC COMMENT Ở PHỤ LỤC J LÀ ĐƯỢC BẢO VỆ** | Chúng là **trí nhớ tiền-git DUY NHẤT** của dự án. **Một pass linter hay một PR "dọn comment" sẽ XÓA SẠCH 2 THÁNG ĐO ĐẠC** |
