# RAGBOT — TOÀN BỘ LUỒNG DỰ ÁN (Debug Handoff Reference)
> Sinh ngày 2026-06-18. Mục đích: 1 file duy nhất mô tả CHI TIẾT mọi luồng để đem đi debug chuyên sâu ở môi trường khác.
> Mọi claim đều có `file:line` (verify read-only). Stack: Python 3.12 / FastAPI / LangGraph / pgvector / Redis Streams / structlog.

---

## CÁCH DÙNG FILE NÀY
- 8 subsystem, mỗi cái là 1 luồng độc lập có thể đọc riêng. Mục lục bên dưới.
- Phần **§0 — KNOWN BUGS (đã verify load-test)** ở đầu là nơi bắt đầu debug: các lỗi THẬT đo được session 2026-06-18.
- Ký hiệu: ✅ verified-working · ❌ verified-broken · ⚠️ risk/gap chưa đo · 🚨 HALLU/safety.

## MỤC LỤC
- §0 — KNOWN BUGS (verified 2026-06-18) — bắt đầu debug ở đây
- §1 — HTTP ENTRY · AUTH · 4-KEY IDENTITY · REQUEST LIFECYCLE
- §2 — QUERY UNDERSTANDING · CONDENSE · REWRITE · INTENT · ROUTING (stats vs vector)
- §3 — RETRIEVAL DUAL-PATH (vector/BM25 hybrid + stats structured) · RRF · RERANK · GRADE
- §4 — GENERATION · SYSPROMPT ASSEMBLY · MODEL RESOLUTION · TOKEN BUDGET · CITATIONS
- §5 — GUARDRAILS · GROUNDING · HALLU · REFUSAL
- §6 — INGESTION · PARSING · CHUNKING · ENRICHMENT · EMBEDDING · STATS-INDEX EXTRACTION
- §7 — CACHING · MULTI-TENANCY · RLS · CONVERSATION-STATE · ACTION/BOOKING
- §8 — DI/BOOTSTRAP · CONFIG · WORKERS/STREAMS · OBSERVABILITY · GRAPH ASSEMBLY
- §9 — CROSS-CUTTING ROOT CAUSES + DEBUG ENTRY POINTS

---

# §0 — KNOWN BUGS (verified bằng load-test thật 2026-06-18)

Đây là các lỗi THẬT, đo bằng cách bắn câu hỏi vào live API (`/api/ragbot/test/chat`, bypass_cache=True) + đối chiếu ground-truth DB. KHÔNG phải giả thuyết.

## 🚨 BUG-1 — CONFLATE HALLU trên path factoid-giá (NGHIÊM TRỌNG NHẤT)
- **Triệu chứng**: hỏi "Tẩy da chết body giá bao nhiêu?" → bot trả **2.499.000đ**. Ground-truth DB (`document_service_index.price_primary`) = **450.000đ**. Số 2.499.000 là giá của dịch vụ **"Toàn thân"** (entity khác).
- **Đo brittleness**: cùng câu hỏi viết 6 cách → 6 đáp án khác nhau (1 đúng 450k, 3 refuse sai "chưa có giá", 2 conflate 550k/299k của "ủ trắng body").
- **Gốc rễ**: `shared/query_range_parser.py:374-377` — `parse_list_query` CỐ TÌNH loại "gia bao nhieu"/"bao nhieu tien" khỏi route stats → câu rơi xuống **vector path**. Vector kéo chunk có nhiều dịch vụ co-occur → LLM gán nhầm giá. Route stats (deterministic, 1 row=1 giá) cho 450k đúng nhưng KHÔNG được gọi.
- **Vì sao lốp không bị**: lốp có **code** (205/55R16) → `parse_code_query` bắt → stats. Spa service không có code → vector → conflate.
- **Tầng fix**: routing/retrieval (CODE), KHÔNG phải sysprompt (bài học spa-07: vá retrieval bug bằng sysprompt = sai tầng).
- **Liên quan**: §2 (routing), §3 (retrieval), §5 (grounding judge = warn-only → KHÔNG chặn được conflate).

## ❌ BUG-2 — Routing giòn (brittle), hardcode cụm-từ-VN
- 11/12 cách hỏi giá rơi vào vector (đo bằng parser thật). 1 từ đổi ("dịch vụ"/"giá" có/không) → route nhảy.
- Gốc: routing = pile of hardcoded VN regex (`_LIST_SIGNALS`, `_LIST_STRIP_PHRASES`, superlative tokens) thay vì intent+entity extractor thống nhất.
- Chi tiết: §2 "HARDCODED VN HEURISTICS MASTER LIST".

## ⚠️ BUG-3 — Latency p95 ~15s (tiêu chí "Nhanh" FAIL)
- Đo 22 case parallel: p50=8s, p95=14.7s, max=15s. Target T2 <8s.
- Nghi: pipeline nặng (multi-query + rerank + grade + sysprompt ~2400 tok) cho corpus nhỏ. Cần ablate + MQ auto-gate (đã dựng cơ chế, default OFF) + nén sysprompt.
- Chi tiết: §4 (token bloat), §3 (pipeline stages).

## ⚠️ BUG-4 — RLS chết ở runtime (isolation chỉ app-level)
- `.env`: DSN superuser `postgres` + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, `DATABASE_URL_APP` trống → `engine.py:67-81` fallback superuser → mọi FORCE-RLS policy bị bypass.
- Policy + role `ragbot_app` (NOBYPASSRLS) đã provisioned (alembic 0069/0141/0186/0187). Code-level WHERE `record_tenant_id` còn sống (1st line). RLS là 2nd line đang tắt.
- Fix: ops set `DATABASE_URL_APP` + gỡ escape flag (KHÔNG sửa code). Chi tiết: §7 "RLS VERDICT".

## ⚠️ BUG-5 — Data quality: stats index duplicate 3-8 row/service + entity_category mostly NULL
- Mỗi dịch vụ lặp 3-8 row trong `document_service_index` (re-ingest/chunking không dedup). entity_category phần lớn NULL → self-query theo category chưa chạy được.
- Gốc: §6 "OBSERVED ISSUES — ROOT CAUSES OF CONFLATE" (table_dual_index emit cả per-row + group-chunk).

## ✅ ĐÃ FIX session 2026-06-18 (có test)
- C2 SSE booking slot persist (`chat_stream.py` wire `resolve_action_conversation_id`) — 4 test.
- MQ auto-gate complexity (Adaptive-RAG, default OFF) — 4 test. H2 synonym OR-expand stats route — 5 test.
- NullGuardrail.check_output signature drift + pcfg parity (`multi_query_complexity_min`) — full suite 5963 pass / 0 fail.

---
# §1 — HTTP ENTRY · AUTH · 4-KEY IDENTITY · REQUEST LIFECYCLE

## I. FASTAPI APP BOOTSTRAP
- `src/ragbot/main.py:14-33` — `main()` uvloop + uvicorn.run → `ragbot.interfaces.http.app:app`.
- `interfaces/http/app.py:169-384` lifespan; `:387-595` create_app factory.
- Lifespan: get_settings (175) · `_check_required_provider_keys` fail-loud UAT/prod (123-166) · Container() → app.state.container (188-189) · OTel+Sentry (193-195) · Bus eager-init (198-206) · LiteLLM refresh (208-213) · parallel bootstrap warm `asyncio.gather` (274-279: model_resolver, bot_registry, JwtTokenService, GuardrailRuleLoader) · embedded workers if APP_EMBED_WORKERS_ENABLED (343-353) · shutdown drains bus→cache→engine (360-383).

## II. MIDDLEWARE STACK (insertion=reverse exec; inbound order)
1. IpRateLimitMiddleware (app.py:540-547, outermost) — per-IP/min cap (DEFAULT_RATE_LIMIT_PER_IP_PER_MIN=300), fail-CLOSED on Redis outage.
2. SourceRateLimitMiddleware (457-460) — `(record_tenant_id, source_tag)` on /documents/ingest+/sync, degrade-OPEN.
3. BotRateLimitMiddleware (444-447) — 4-key `(tenant,workspace,bot,channel)`, DEFAULT_RL_BOT_PER_MIN=60 (ingest 10), X-RateLimit-Bot-* headers, bypass when bypass_rate_limit flag.
4. SlidingRateLimitMiddleware (429-434) — per-token L2.
5. SecurityHeadersMiddleware (416-419) — HSTS opt-in.
6. GZipMiddleware (410).
7. TraceContextMiddleware (409, `middlewares/trace_context.py:20-31`) — X-Trace-Id read/gen, sanitize `[A-Za-z0-9_-]{1,128}`, bind request.state.trace_id, echo header.
8. TenantContextMiddleware (408, `middlewares/tenant_context.py:86-431`) — PUBLIC_PATHS bypass (47-62). **Service JWT HS256** (111-206): verify_token → claims record_tenant_id/tenant_id/sub/role/rl_val/rl_win → resolve tenant UUID → bot bypass cache `ragbot:bot:{tenant}:{workspace}:{bot}:{channel}` (182-206) → L1 tenant RL (212-282) → L1.5 per-service (309-324) → L2 per-user (328-373) → bind request.state. **User JWT RS256 fallback** (394-431). `enforce_tenant_match` (481-500) defence-in-depth.
9. SchemaVersionMiddleware (407, `middlewares/schema_version.py:52-96`) — X-Schema-Version vs SUPPORTED_SCHEMA_VERSIONS, 400 on invalid.
10. CORSPerTenantMiddleware (470-474) — per-tenant allowed_origins via TenantConfigCache, wildcard subdomain.
11. BodySizeLimitMiddleware (482) — per-path limits (chat DEFAULT_MAX_BODY_CHAT_BYTES, ingest 16MB), reject chunked w/o Content-Length (411).
12. AntiAbuseMiddleware (510-517, optional) — UA denylist, API-key allowlist.
13. LoggingMiddleware (406) — duration + Prometheus http_requests_total.

## III. DI CONTAINER (bootstrap.py:161-788) — see §8 for full provider list
db_engine (168, RLS wrapper SET LOCAL app.tenant_id) · session_factory create_rls_session_factory (174-176) · redis_client (179-183) · redis_streams_client (189-192, 5s socket_timeout) · api_key_pool_factory (199-208) · adapters cache/semantic_cache/vector_store/embedder/reranker/guardrail/... (211-412) · repos (422-478) · services (479-631) · chat hooks (712-729).

## IV. CHAT ENTRY POINTS (ALL)
### 2.1 POST /chat (queued 202) — `routes/chat.py:40-93`
JWT record_tenant_id from request.state (53) → resolve_workspace_id (58-60) → 4-key BotRegistryService.lookup (64-66, 404 if None) → AnswerQuestionCommand w/ trace_id (70-86) → enqueue Redis Stream (87) → 202 {job_id,status,status_url,trace_id}. **conversation_id=None ở command OK** vì use-case `answer_question.py:83` get_or_create + đẩy real id vào worker payload (110).
### 2.2 POST /chat/stream (SSE) — `routes/chat_stream.py:87-470`
tenant check (96-98) → streaming flag (108-126) → 4-key lookup (129-144) → request log create (189-207) → StepTracker (209-214) → graph DI assembly (238-258) → oos_template + sysprompt_assembler (260-281) → **resolve_action_conversation_id** + sink (283-300) → build_chat_initial_state (301-323) → state["_stream_sink"]=sink (323) → graph.ainvoke timeout-guard (345-376) → StreamingResponse (449-470) → post-stream finalize log + save_history (379-406). **FIX 2026-06-18**: line 287-300 nay resolve conversation_id (trước hardcode None → SSE booking slot loss).
### 2.3 POST /test/chat (demo inline) — `routes/test_chat/chat_routes.py:68-220`
No auth (fallback _PLATFORM_TENANT_FALLBACK_UUID) · bypass_cache flag (268) · workspace explicit find_by_4key OR find_by_3key_unique (98-109) · inline graph · debug payload optional.
### 2.4 POST /test/chat-async — `routes/chat_async.py:144-250` — enqueue CHAT_REQUEST_STREAM, poll GET.

## V. 4-KEY IDENTITY
- record_tenant_id (UUID): JWT claim only, request.state, NEVER body (tenant_context.py:86-431).
- workspace_id (slug): body optional, fallback str(record_tenant_id), validate `[a-z0-9-]{1,36}` (workspace_id_validator.py:75-107).
- bot_id (slug): body REQUIRED, len 1-128.
- channel_type: body REQUIRED (default "web" in test).
- **Resolve boundary** BotRegistryService.lookup (`services/bot_registry_service.py:103-168`): cache key `ragbot:bot:{tenant}:{workspace}:{bot}:{channel}` (133), single-flight on miss (141-168), bot_repo.find_by_4key (149), TTL DEFAULT_BOT_CONFIG_TTL_S ~3600s.

## VI. REQUEST LIFECYCLE / LOGGING
- request_logs create (started, status=running) → finalize (answer_hash, model, tokens, cost, status, retrieved_chunks refs, citations, duration_ms).
- request_steps via StepTracker — per-node (step_name, step_order, model_used, tokens, cost, duration_ms, status). 33 step_name instrumented (see §8).
- trace_id propagation: header → request.state → structlog context → response header + request_logs.trace_id.

## §1 OBSERVED ISSUES
- 7.4 idempotency chỉ /chat (queued), KHÔNG /chat/stream → 2 SSE client trùng → chạy 2 lần.
- 7.6 history load best-effort (chat_stream.py:154-176) — DB hiccup → empty history, no metric.
- 7.7 message_id = `int(time.time()*1000)` (chat_stream.py:147) — 2 request cùng ms collide.
- 7.8 bypass_cache chỉ test routes, không production.
- Workspace resolution divergence: production enforce 4-key/str(tenant) fallback; demo cho 3-key ambiguous.

---
# §2 — QUERY UNDERSTANDING · CONDENSE · REWRITE · INTENT · ROUTING

## 1. understand_query NODE (`orchestration/nodes/understand.py`)
- 1.1 Idempotency+cache (55-107): guard `_understand_skipped_by_parallel` (76); Redis cache gate TTL DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S=600 (82-107); cache keys `{intent,intent_confidence,query,original_query}` (106); cache only when `has_meaningful_history=false`.
- 1.2 Heuristic intent fast-path (109-142) → `services/heuristic_intent_classifier.py:109-147`. Gate `heuristic_intent_enabled` (default TRUE). Threshold skip LLM if conf>=0.85. Patterns (62-106): greeting `^(xin chào|hi|hello|chào em|...)` 0.90; chitchat `^(cảm ơn|thanks|ok|...)` 0.90; aggregation `(có mấy|bao nhiêu|liệt kê|tất cả|toàn bộ|kể tên|các loại|...)` 0.85; multi_hop `(tại sao|vì sao|giải thích|nguyên nhân|...)` 0.85; comparison `(so sánh|khác nhau|vs|...)` 0.85. factoid=default fallback (no pattern). **HALLU=0: heuristic NEVER fires domain-specific → LLM fallback.**
- 1.3 History condense (157-176): gate len(history)>DEFAULT_CONDENSE_MIN_HISTORY_TURNS(3) AND chars>=150; limit condense_history_limit(5); fallback `<question>{query}</question>`.
- 1.4 Bot context inject (178-185): `<bot_context>` preview DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS=500.
- 1.5 LLM structured understand (186-299): schema UnderstandOutput (`dto/llm_schemas.py:51-93` — condensed_query 1-100ch, intent Literal[factoid|comparison|multi_hop|aggregation|out_of_scope|greeting|feedback|chitchat|vu_vo], confidence 0.5). State keys (231-238): intent, intent_source="llm", intent_confidence, query(condensed), **original_query (preserve raw for routing)**.
- 1.7 Fallback (301-311): catch InvariantViolation/Timeout/OSError/RuntimeError/ValueError/KeyError → intent=factoid, conf=0.5.

## 2. query_complexity (`orchestration/nodes/query_complexity.py`)
- classify_query_complexity (96-213) → (label, score). Structural early-exit (136-142): 1 struct-ref + ≤1 comma + no conj → ("simple",0.0). Pattern `(Chương|Mục|Phần|Điều)\s*\.?\s*\d+`, max 80 chars.
- Scoring signals (additive, config): comma×0.5, conjunction×0.4, numbers×0.3, question×0.6, length/20. Conjunctions JSON `["và","hoặc","cũng như","or","and"]`. Threshold DEFAULT_QUERY_COMPLEXITY_THRESHOLD=1.2 → "complex".
- has_aggregation_keyword (216-237): per-lang DEFAULT_AGGREGATION_KEYWORDS_BY_LANG (`constants/_24`): VI "tất cả/liệt kê/bao nhiêu/toàn bộ/so sánh/đắt nhất/rẻ nhất/..."; EN "all/list/how many/compare/most expensive/cheapest/..."; non-VI → empty dict.

## 3. decompose (`orchestration/nodes/query_decomposer.py`)
- Gate decomposer.enabled. Model DEFAULT_DECOMPOSER_MODEL (gpt-4.1-mini; Haiku banned). Domain-neutral prompt (54-78) → JSON `{sub_queries:[...]}`. Fallback [query] on any failure (172-191).

## 4. REWRITE + MULTI-QUERY (`query_graph.py`)
- rewrite_and_mq_parallel (2682-2723): gate pipeline_parallel_rewrite_mq_enabled (default OFF). Decompose precedence: sub_queries≥2 → skip MQ, fanout_bypassed=true.
- _run_multi_query_expansion gates: per-intent skip (multi_query_enabled_by_intent), **NEW complexity gate `multi_query_complexity_min` (default 0.0=inert, Adaptive-RAG)**, mq_enabled, n_variants>1. **⚠️ MQ node runs BEFORE query_complexity node → complexity_score not in state → gate classifies inline.**

## 5. ROUTE DECISION STATS vs VECTOR (`orchestration/nodes/retrieve.py:176-273`)
Evaluated IN ORDER (each can veto):
1. parse_range_query(_raw_query) (205). _raw_query = original_query or query.
2. parse_code_query (216-220) if range None + stats_code_lookup_enabled.
3. parse_list_query (226-227) if range None.
4. Superlative kill-switch (231-239): op max/min + stats_superlative_enabled=false → None.
5. Structural-ref guard (240-269): article anchor (Điều/Khoản) → skip stats. Pattern `(?i)\b(điều|khoản|điểm|chương|mục|article|section|...)\s*\.?\s*\d+`.
6. Confidence floor (270-272): <RANGE_QUERY_MIN_CONFIDENCE(0.7) → vector.
7. Race mode (284-521): stats+vector concurrent, stats preferred, timeout stats_race_timeout_s(3s).
8. Sequential default (524-573): stats first, fallback vector if empty.
- Stats ops (`query_graph.py:2804-2832`): keyword→query_by_name_keyword(keyword,synonyms,limit); max/min→top_by_price; range→query_by_price_range.

## 6. PARSER HARDCODE (`shared/query_range_parser.py`) — 🚨 GỐC BUG-1/BUG-2
- RangeFilter dataclass (84-102): price_min/max, price_column, operation(count|list|filter|max|min|keyword), confidence, keyword.
- parse_range_query (245-334): range "từ X đến Y" conf 0.9 (260-275); fuzzy "khoảng X" ±10% conf 0.75 (277-290); below tokens `duoi|it hon|nho hon|thap hon|khong qua|toi da|max` conf 0.85 (292-303); above tokens `tren|hon|lon hon|cao hon|tu|min` conf 0.85 (305-316); superlative MAX `dat nhat|mac nhat|cao nhat|dat tien nhat` / MIN `re nhat|thap nhat|re tien nhat` conf 0.8 (318-332).
- _COUNT_SIGNALS (109-117) `có bao nhiêu|bao nhieu|dem|so luong|count`. _LIST_SIGNALS (119-135) `liet ke|liệt kê|danh sach|toan bo|tat ca|co nhung|có những|list`.
- **🚨 parse_list_query (359-400) line 374-377: `if "gia bao nhieu" in folded or "bao nhieu tien" in folded: return None`** ← loại price-factoid khỏi stats → vector → CONFLATE (BUG-1).
- _LIST_STRIP_PHRASES (339-356) ~40 phrase strip trước ILIKE; connective filler "về/vào/không/có" LEFT IN (349-354) → pollute keyword.
- parse_code_query (410-445): `_CODE_QUERY_RE = [A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)+` (overridable `code_query_pattern`), must have ≥1 letter (exclude date 09/2020). conf 0.8.
- Bare-number guard (481-482): no unit + <1000 → reject (doc/article number). Date-tail guard (471-472): `\s*/\s*\d` → reject.

## §2 HARDCODED VN HEURISTICS MASTER (configurable?)
| Phrase/pattern | file:line | Config-override? |
|---|---|---|
| greeting/chitchat/aggregation/multi_hop/comparison regex | heuristic_intent_classifier.py:62-106 | NO (gate on/off only) |
| structural markers Chương/Phần/Mục/Điều | query_complexity.py:55 | NO |
| conjunctions và/hoặc/or/and | constants (JSON) | YES query_complexity.conjunctions |
| below/above/superlative tokens | query_range_parser.py:158-210 | NO (constant) |
| range/fuzzy regex | query_range_parser.py:213-225 | NO |
| count/list signals | query_range_parser.py:109-135 | NO |
| **"gia bao nhieu" exclusion** | query_range_parser.py:376 | **NO** |
| _LIST_STRIP_PHRASES (40+) | query_range_parser.py:339-356 | NO |
| summary patterns | constants/_21:156-165 | NO |
| aggregation kw per-lang | constants/_24:53-90 | YES (lang dict) |
| structural-ref fallback | constants/_21:118-121 | YES structural_ref_fallback_pattern |
| code regex | constants/_21:88-90 | YES code_query_pattern |

## §2 FAILURE CASES (debug)
- A: "dịch vụ X bao nhiêu" → "bao nhiêu" count-signal + no "gia bao nhieu" exclusion → list route returns ALL X (sai) — phrasing-dependent.
- B: "Điều 12 giá" — "thong tu" folds "tu"→từ extract "12" → guard <1000 rejects ✓ (2026-06-05 fix).
- C: "có dịch vụ VỀ da chết" — "về" left-in keyword → ILIKE fails → vector fallback.
- F: "Điều 38" simple → MQ fanout fires anyway (multi_query_complexity_min=0.0) → wasteful.

## §2 CRITICAL — "giá \<tên dịch vụ không code\>" → KHÔNG route stats nào → VECTOR → conflate (BUG-1). Fix = route price-of-named-entity → stats query_by_name_keyword (labeled price deterministic).

---
# §3 — RETRIEVAL DUAL-PATH · RRF · RERANK · GRADE · NEIGHBOR

## 1. retrieve() NODE (`orchestration/nodes/retrieve.py:147-173`)
DI: vector_store, lexical_retrieval, embedder, llm, model_resolver, entity_extractor, metadata_filter_strategy, stats_index_repo, doc_repo, _do_stats_lookup, _embed_query, _prewarm_embedding_cache...
Branch order (176-573): (1) STATS-INDEX (198-572) gate stats_index_repo!=None → parse_range/code/list → structural guard (240-269) → confidence (270-272) → race(284-521)/sequential(524-573); (2) DOC-SUMMARY (574-620) `_matches_summary_pattern`; (3) SPECULATIVE hit (622-668); (4) HYBRID (670-1873).

## 2. PATH A — VECTOR/BM25 HYBRID
- Preprocessing (670-800): expand_abbreviations (692-697), diacritic restore opt-in (1612-1671), generic vocab expansion (734-761 via vocabulary_expander custom_vocabulary["synonyms"]), metadata extract L1 LLM-intent (777-782) / L2 regex article (816-841) / L3 LLM fallback (853-895).
- topK (700-732): _topk_by_intent DEFAULT_RETRIEVE_TOP_K_BY_INTENT (705); keyword promotion to aggregation top_k (712-719); fallback DEFAULT_TOP_K=20.
- Multi-query/decompose (1126-1315): decompose sub_queries precedence (1127-1131); MQ gates (1146-1205); entity-grounded (1251-1254); cost track (1211-1238).
- Pre-batch embed (1323-1339): _embed_batch_queries if >1 query.
- _run_hybrid_for_query (977-1124): old port hybrid_search(query_embedding) (985-1024) OR new adapter hybrid_search(query_text+embedding)+search fallback (1025-1124). Threads record_bot_id/channel_type/corpus_version (998-1000), record_tenant_id RLS (1007-1010), metadata_filter JSONB (1074-1075), embedding_column whitelist (1077-1080), adaptive RRF bm25_weight/vector_weight per-intent (1063-1073), VN structural pre-filter (1093-1098).
- RRF fusion (1341-1391): asyncio.gather per-query (1343-1354), mq_rrf_merge_chunks rrf_k=DEFAULT_RRF_K=60 (1371), dedup by chunk_id first-wins, cap [:_retrieve_top_k].
- Lexical BM25 (1673-1735): gate lexical_retrieval!=null, RRF merge (1716-1717).

## 3. PATH B — STATS STRUCTURED (`query_graph.py:2786-3000` _do_stats_lookup)
- Repo (`stats_index_repository.py`) schema document_service_index (alembic 0118): id, record_tenant_id, workspace_id, record_bot_id, record_document_id, record_chunk_id(nullable), entity_name, entity_category(nullable), price_primary/secondary(nullable), attributes_json(JSONB).
- 3 SQL: query_by_price_range (165-250) WHERE price_min≤price≤price_max ORDER price ASC; top_by_price (252-318) ORDER price DESC/ASC LIMIT min(limit,DEFAULT_STATS_SUPERLATIVE_LIMIT=5); query_by_name_keyword (418-494) WHERE `unaccent(entity_name) ILIKE :kw OR unaccent(entity_category) ILIKE :kw` + **NEW synonyms OR-expand bound-param :kw{i} (454-467, dedup lowercase)**.
- Synthetic-chunk builder (2867-2965): per-row name(2891) + price_primary→secondary(2894-2896) + category(2933). _is_field_like (2884-2886) max DEFAULT_STATS_ATTR_MAX_CHARS=120/WORDS=12 (skip mega-cells). Line "{name}: {price}" (2919) or "price: {price}" if not field-like (2923-2926). Dedup by (entity_name,price) (2898-2900). chunk_id=DEFAULT_STATS_SYNTHETIC_CHUNK_ID="stats_index_synthetic" (2955-2965), score=1.0, source="stats_index".
- Linked-chunks (2834-2991): attempt1 record_chunk_id FK (pre-2026-05-26 NULL → empty); attempt2 whole-doc fallback (reintroduces variant-blob noise).

## 4. RERANK (`orchestration/nodes/rerank.py:55-488`)
- Resolver (`services/reranker_resolver.py:80-323`): per-bot binding purpose='rerank' (56-77) → platform system_config reranker_* (199-268) → NullReranker (272-273). Cache `ragbot:rerank:{bot_id}` TTL.
- Mode (146-161): empty_input/intent_skip_set/intent_skip/disabled/no_reranker/null_reranker/**rerank**. Mode=rerank → cross-encoder rerank(query,chunks,top_n) (171-176), fail-soft→RRF order (177-195).
- Filter (238-387): CLIFF (default) `_cliff_detect_filter(absolute_floor=0.0, gap_ratio=0.4, min_keep=3)` (255-304); THRESHOLD legacy (305-331). Gate-after-cliff (354-387) only threshold strategy.
- Max-to-LLM cap (389-404). **Retrieval safety-net (449-482)**: union top-N retrieval back if buried by weak reranker, stamp lowest rerank score (468).

## 5. GRADE / CRAG (`orchestration/nodes/grade.py:60-567`)
- Skip: stats-route bypass (92-111) `retrieve_mode.startswith("stats")` → pass-through; high-score skip (113-162) top_score>=crag_skip_retry_above_score.
- Structured batch grade (186-311) GradeBatchOutput, timeout→AMBIGUOUS (248-269). Per-chunk fallback (313-398). No-SO fallback all AMBIGUOUS (402-436).
- Verdict map yes→RELEVANT/no→IRRELEVANT/partial→AMBIGUOUS (180-184), _remap_grade_for_intent lenient (288-293).
- Adequacy (439-533): all-relevant→adequate; all-irrelevant→fallback gate (rerank abs crag_min_fallback_score 487-488 / bypass relative top_score×ratio 494-499); ambiguous→retry gate. retrieval_adequate=False → OOS.

## 6. MMR (`query_graph.py:3049-3094`): per-intent mmr_similarity_threshold_by_intent (aggregation loose), DEFAULT_MMR_LAMBDA=0.5.
## 7. NEIGHBOR (`orchestration/nodes/neighbor_expand.py:397-452`): plan windows DEFAULT_NEIGHBOR_WINDOW_SIZE (148-181), SQL fetch per-doc RLS-joined (299-394), merge+truncate token budget seeds-first (184-296).

## §3 DATA SHAPES
- chunk dict: {chunk_id, document_id, content, text, score, document_name, chunk_index, metadata, relevance(yes/no/partial/fallback), is_neighbor_expanded, _safety_injected, access_groups}.
- Constants: DEFAULT_TOP_K=20, DEFAULT_RRF_K=60, DEFAULT_RERANK_TOP_N=7, RANGE_QUERY_MIN_CONFIDENCE=0.7, DEFAULT_STATS_SYNTHETIC_CHUNK_ID="stats_index_synthetic".

## §3 OBSERVED ISSUES (debug)
- 🚨 #1 VECTOR MULTI-SERVICE CONFLATION: chunk chứa nhiều dịch vụ co-occur (table_dual_index group chunk OR CSV row đa-cell) → embedding centroid lẫn → price gán nhầm (GỐC BUG-1 ở retrieval). Stats route tránh được (1 row atomic).
- #2 stats empty fallback (pre-backfill record_chunk_id NULL) → synthetic chunk surfaces filtered rows trước doc-fallback.
- #3 rerank silent drop → safety-net (449-482).
- #5 metadata over-restriction "Điều N giá" → stats wins before structural guard.
- #6 synthetic chunk_id sentinel (else generate drops falsy id).

---
# §4 — GENERATION · SYSPROMPT ASSEMBLY · MODEL RESOLUTION · TOKEN BUDGET · CITATIONS

## 1. generate NODE (`orchestration/nodes/generate.py`)
- Entry 90-108 (DI closures _audit/_invoke_llm_node/_invoke_structured_llm_node/_pcfg/_lang/_oos_text/_resolve_xml_wrap_enabled/_resolve_generate_schema/_render_captured_slots/_CITATION_RE). Clock _generate_t0 (110-125).
- Refuse short-circuit (242-287): flag refuse_short_circuit_enabled; graded empty + not chitchat + not action → oos_text → return answer_type="no_context".
- Context optimization (353-440): prompt_compression (353-394), adaptive_context (401-417), lost-in-middle reorder (420-439), token_opt (472-479).
- **Context cap (481-528)**: generate_context_chars_cap_by_intent[intent] or DEFAULT_GENERATE_CONTEXT_CHARS_CAP (488-509); drop tail chunks when running+len>cap (514-523); whitelist chunk_ids_allowed (524-528).
- **Context block build (540-580)**: xml_wrap resolve (540). Loop: extract chunk_id/text/doc_name (543-549). **🔑 line 551-552 `if not cid: continue` — drop chunk có id rỗng** (stats synthetic dùng sentinel để không bị drop). xml `<chunk id type section><content>` (553-571) or legacy `<context>` (572-579).
- **Prompt build msg order (602-616)**: [1] system (603); [2] history capped + citation stripped + truncated (604-609); [3] user `<documents>{context}</documents>\n<question>{q}</question>` (610-616). Cache-friendly (static system prefix first).
- **System prompt (582-593)**: state["bot_system_prompt"] (pre-assembled) or _lang.prompt_generator (584). **{captured_slots} substitution (589-593)** — DATA only (action bots), sacred-#10 OK.
- Output cap (645-656): compute_output_cap (`token_budget.py:63-78`).
- LLM call (694-802): purpose resolve (676-692); structured path (694-751) _resolve_generate_schema → validate citations vs chunk_ids_allowed (720-751); free-form fallback (753-802) _CITATION_RE.findall (771) validate (775-793).
- Post-hoc citation (809-821): empty citations + graded → top chunk, source="posthoc_top_chunk".
- Action drift (873-952): detect_drift severity warn→flag / block→raise GuardrailBlocked (928-932), save state best-effort (941-952).
- SLA (841-861): generate_p95_sla_ms warn; TTFT _stream_first_token_ms.

## 2. SysPromptAssembler (`services/sysprompt_assembler.py`)
- 3-tier (18-21): `bot.system_prompt + language_packs[locale].sysprompt_default_rules − plan_limits.sysprompt_rules_disabled`. ADR-W1-S10 governed APPEND-only.
- assemble (83-126): base+locale, fetch platform rules (106-114), strip disabled (120-125), return base+rules (concat, no override).
- Seed via alembic (`20260611_0204_sysprompt_aprime.py` UPDATE language_packs prompt_key='sysprompt_default_rules'). A-prime cut 22-rule 8KB→concise.
- opt-out plan_limits["sysprompt_rules_disabled"] (161-191). Pin test_sysprompt_assembler_pin.py (5 pass). Called chat_stream:278 / chat_worker pipeline:580 / test_chat:391.

## 3. SACRED-#10 VERDICT: ✅ NO INJECT, NO OVERRIDE
- Prompt = XML structural framing only (603-616), {captured_slots}=DATA (589-593). Answer read verbatim (755-802 free, 701-712 structured). Refusal REPLACES answer (269-287), không append. Refusal origin = _resolved_oos_template 7-tier (`query_graph.py:685-716`: bot column→plan_limits→workspace→tenant→system_config→language_pack→DEFAULT_OOS_ANSWER_TEMPLATE=""). NO hardcoded i18n. Module docstring pins (generate.py:10-12).

## 4. MODEL RESOLUTION (`services/model_resolver/`)
- resolve_purpose_for_intent (_helpers.py:128-155): factoid→llm_factoid, chitchat→llm_chitchat, OOS→llm_oos, else llm_primary. Fallback llm_primary.
- resolve_llm (__init__.py:117-153): intent→purpose→cached bindings (tenant,bot,purpose)→sort rank→fallback llm_primary→primary/variant.
- Cascade (query_graph.py:302-351): cascade_routing_enabled → complexity_score → tier model (336), graceful degrade (346).

## 5. TOKEN BUDGET — sysprompt ~2400 tok (pre-Aprime) = cost/latency lever (BUG-3). History cap min(condense_history_limit, DEFAULT_GENERATE_HISTORY_MAX_MSGS); factoid skip-history opt (598-599).

## 6. CITATIONS: _CITATION_RE=`\[chunk:([0-9a-f\-]+)\]` (query_graph.py:407). Validate vs chunk_ids_allowed. Metric citation_validation_fail_total.

## §4 OBSERVED ISSUES
- 7.1 sysprompt bloat 2400 tok (A-prime đã giảm; mọi rule mới cần audit token).
- 7.4 chunk-id drop (551-552) no audit event.
- 7.5 OOS empty-string all-tier → bot trả rỗng (language_packs tier nên luôn non-empty).
- 7.6 cascade silent degrade (346-351) no metric.
- 7.3 multilingual: sysprompt_default_rules chỉ seed vi/en; locale khác → fallback vi.

---

# §5 — GUARDRAILS · GROUNDING · HALLU · REFUSAL

## 1. INPUT GUARDRAIL (`query_graph.py:1703-1761` guard_input → `local_guardrail.py:796-847` check_input)
- too_short (188-218): min_alpha config DEFAULT_GUARDRAIL_MIN_ALPHA_CHARS=2 (0=skip). severity=block.
- length_limit (105-117): DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH=4096. block.
- DB regex rules (723-761) from guardrail_rules table (alembic 010f) OR static fallback: prompt_injection (120-142, block), pii_vi phone/email/cmnd (145-170, warn/redact), pii_en ssn (warn), sql_injection (221-233, block).
- Blocked (1738-1761): _resolved_oos_template, per-rule response_message override (1752).

## 2. OUTPUT GUARDRAIL (`orchestration/nodes/guard_output.py:49-516` + `local_guardrail.py`)
- **system_prompt_leak (298-350)**: skip if OOS refusal Jaccard≥DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD=0.90 (257-279); shingle hash size DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE=8 (244-251); **doc-shingle subtraction (260-270)** (shingle in chunks too = legit relay); block if matches≥**DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT=10** (105-112). Intent-skip greeting/chitchat (221-227). **Stats-route skip (234-241)** retrieve_mode.startswith("stats") + sysprompt_leak_skip_stats_route=True.
- secret_scanner (353-365): block.
- **grounding_check (368-414) numeric/substring**: citation marker `\[...\]`, substring verbatim (239-254), numeric overlap _extract_numbers (282-288). **severity="warn" (410-411) — NEVER blocks** (observability only).
- **llm_grounding_check (417-553)**: intent-gated (grounding_intents=factoid/comparison/aggregation/multi_hop), stats-route skip, async option. Structured (556-587) GroundingVerdictsOutput OR text-parse (590-628). **max_sentences=5 HARDCODED (413,451) — tail-claims >5 unchecked**. Verdict ratio>threshold(0.30) → warn (529-552). **NEVER blocks.** Silent degrade on timeout/error → None (514-520).

## 3. 🚨 HALLU=0 ENFORCEMENT VERDICT
**Grounding judge = observability (warn), KHÔNG enforce.** HALLU=0 giữ bởi: (1) sysprompt anti-fabricate (bot owner), (2) retrieval quality (no chunks → OOS template), (3) CRAG retry, (4) citation_marker_required opt-in. **4 trap coverage:**
| Trap | Caught? |
|---|---|
| Fabricate | ✅ citation/substring |
| Misinterpret | ⚠️ judge warn-only |
| Extrapolate (sum) | ❌ numeric overlap thấy addends, không verify tổng |
| **Conflate (entity-map)** | ❌ cả 2 entity present → grounding pass → **BUG-1 KHÔNG bị chặn** |

## 4. REFUSAL: _resolved_oos_template 7-tier (xem §4), per-rule response_message. NO hardcoded i18n (sacred-#3).

## §5 PER-CHECK FP/FN
| Check | FP | FN |
|---|---|---|
| numeric_overlap | HIGH (addends present→pass dù tổng sai) | LOW |
| system_prompt_leak | MED (mitig min_match=10+doc-subtract) | MED |
| llm_grounding sentence-cap | LOW | **HIGH (tail >5 unchecked)** |
| llm_grounding silent-degrade | — | **HIGH (timeout→pass)** |

## §5 OBSERVED ISSUES
- max_sentences=5 hardcoded (no config). Grounding prompt English-only (479-483) → VN text-parse risk (structured mitigates). _SENTENCE_SPLIT_RE=`[.!?]\s+` splits VN decimals.
- 🚨 Conflate (BUG-1) + Extrapolate KHÔNG bị grounding chặn → cần arithmetic/structured route, không phải tăng threshold.

---
# §6 — INGESTION · PARSING · CHUNKING · ENRICHMENT · EMBEDDING · STATS-INDEX

## ENTRY (`interfaces/workers/document_worker.py`)
- handle_document_uploaded (83-107) → _inner (110-370). `_is_refetchable_url` (74-80) guard Google `edit?gid=`→HTML. U0 raw_content reuse from DB (224-255), fallback registry parser (276+).

## PIPELINE STAGES (`application/services/document_service/`)
- **U1 validate** (ingest_core.py:275-286): tenant guard, workspace RLS GUC (document_worker.py:127-139), Phase D request_steps emit.
- **U2 parse** (ingest_core.py:314-344): registry detect_parser(mime,ext) (`parser/registry.py:81-104`). Row-shaped parsers (excel/google_sheets) → parser_row_chunks bypass smart_chunk (672-677). Adapters: google_sheets_parser (row-per-chunk), excel_openpyxl, pdf, docx, markdown, null.
- **U3 clean** (ingest_stages.py:221-337): CleanBase Tier-0 (258-275, HTML strip+NFC+zero-width+injection blacklist, cleanbase_tier0_enabled), legacy _clean_document_text, LLM metadata opt (321-336, metadata_extraction_enabled default OFF).
- **U4 chunk** (ingest_stages.py:339-800): **AdapChunk Layers**: L1 whole-doc (356-382, <whole_doc_threshold); L2 parent-child (400-445, parent_child_enabled OFF, generate_parent_child_chunks); L3 doc profile (446-651, adapchunk_layer3 OFF, analyze_document→DocumentProfile 10 features); L4 select_strategy (`chunking/analyze.py` + strategies.py) → hdt|semantic|recursive|table_csv|table_dual_index|parser_preserve|hybrid|proposition; L5 cross-check (`chunking/__init__.py:~520` adapchunk_l5 ON). VN heading promote (vn_structural.py). Orphan merge (NOT tabular). M25 block histogram (730-748).
- **U5 enrich** (ingest_stages_enrich.py:120-500): CR legacy (201-219, contextual_retrieval_enabled OFF), WA-3 enhanced CR (222-298, cr_enhanced_enabled OFF, stored chunk_context col NEVER in prompt QG#10), VN compound segment (300-354, segment_vi_compounds to_thread timeout). Concurrent CR+seg per chunk (310-400, prompt-cache warm).
- **U6 vn_segment** (vi_tokenizer.py segment_vi_compounds) → content_segmented.
- **U7 embed_store** (ingest_stages_store.py:120-999): embedding spec (154-158, jina/zeroentropy 1024/matryoshka); embed-text strategy auto/raw_only/prefix_plus_raw (180-195); Narrate-then-Embed (227-276, OFF); passage prefix (278-286); late_chunking sliding/single (288-388, late_chunking_enabled ON); standard embed batches (409-443, embed_doc_batch_size=50, raise on provider fail); chunk identity M21 (537-560, deterministic UUID5 OFF→UUIDv7); structured-ref extraction (597-628, OFF, article_no/chapter_no JSONB); insert parent-child (652-848) OR flat (851-950); **semantic cache invalidate (954-975)**.

## FINALIZE + STATS EXTRACTION (ingest_stages_final.py:120-377)
- State flip atomic (145-227): total=0→failed, null_non_parent>0→failed (parent legit NULL), else active.
- **Stats extraction (305-342)**: delete_by_document stale (repo 132-163); **parse_table_chunks (`shared/document_stats.py:259-320`)** → ParsedEntity: skip prose (283-289), detect header (305-307), category from single-col heading (309-314), extract (name,category,price_primary,price_secondary,attrs). parse_money_vn (82-103) "1.499.000"/"1tr499"/"499k", filter <DEFAULT_PRICE_MIN_VND(50k). Header tokens HARDCODE (58-65) `stt/ten/gia/vung/loai/dich vu/service/price/name/category`. aggregate_summary (323-383) price buckets. bulk_insert (repo 57-130) RLS session_with_tenant. upsert documents.summary_json.

## §6 STATS-INDEX VERDICT
- **entity_category mostly NULL**: only from explicit category column OR multi-group heading. NOT LLM (HALLU=0). → self-query category CHƯA reliable (BUG-5).
- **NO dedup in ingest**: 3-8 dupes/service (BUG-5) vì table_dual_index emit per-row + group chunk, OR CSV repeat header per group section. parse_table_chunks deterministic; dupes vào từ upstream chunking.

## §6 ROOT CAUSE CONFLATE (BUG-1 ingest side)
- table_dual_index emit GROUP chunk "[Service A 100k][Service B 200k]" → embedding centroid lẫn → vector kéo về matches single-service query ở mid-confidence với giá diluted. **Fix: table_csv per-row exclusive, không emit group chunk.**

## §6 HARDCODE/MULTILINGUAL
- document_stats.py:58-65 header tokens VN-biased (no config). constants DEFAULT_CHUNK_SIZE/OVERLAP/ORPHAN/MAX, DEFAULT_PRICE_MIN_VND=50k (VND-centric). VN segment vi-only; HDT Chương/Mục/Điều assume VN. Chunking strategies (hdt/semantic/recursive) language-agnostic.

---

# §7 — CACHING · MULTI-TENANCY · RLS · CONVERSATION-STATE · ACTION/BOOKING

## 1. SEMANTIC CACHE (`infrastructure/cache/semantic_cache.py`)
- Hash fast-path (410-459): `WHERE record_bot_id AND record_tenant_id AND query_hash AND bot_version AND corpus_version AND expires`. Cosine slow-path (461-527): pgvector `<=>` HNSW, same scope + threshold (default 0.97).
- Write store() (529-597): **NULL-tenant gate (545-552) skip+warn** (no cross-tenant leak rows).
- Stampede 2-tier (191-368): Redis SETNX `ragbot:cache:lock:{bot}:{qhash}` (230-301) + asyncio.Lock (303-368).
- bot_version `_compute_bot_cache_version` (query_graph.py:870-873) = sha256(system_prompt+oos_template)[:12]. bypass_cache (1777-1780). **Multi-turn skip (1787-1792)**: conversation_history present → cache skipped (correctness).

## 2. EMBED CACHE (embed_cache.py): key `ragbot:embed:{model}:{sha256(query)[:16]}` — model-scoped, NOT tenant (same text=same vector, intentional reuse).
## 3. UNDERSTAND-QUERY CACHE (understand_query_cache.py:64): key `ragbot:uq:v{ver}:{record_bot_id}:{hash}` — bot-scoped.

## 4. 🚨 MULTI-TENANCY / RLS VERDICT
- **RLS INSTALLED but BYPASSED at runtime** (BUG-4). `.env`: DSN superuser `postgres`, `DATABASE_URL_APP` unset, `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`. `engine.py:67-81`: url_app None + escape=1 → superuser fallback + WARN. Superuser ignores FORCE ROW LEVEL SECURITY.
- Layer1 policies alembic 0069 (14 direct + 2 child tables, `record_tenant_id = current_setting('app.tenant_id')::uuid`, NULL→excluded fail-closed), 0141 workspace-aware, 0187 re-assert.
- Layer2 role `ragbot_app` alembic 0186 (NOSUPERUSER NOBYPASSRLS NOLOGIN, DML-only grants).
- Layer3 SET LOCAL (engine.py session_with_tenant 103-164, `SET LOCAL app.tenant_id` 142-143, fail-secure raise if unbound 129-135). RLS hook session.py:188-203 OPT-IN.
- **Cross-tenant leak: POSSIBLE if bare SQL forgets WHERE** (RLS 2nd line off). Code-level filters (semantic_cache 420/480, pgvector_store session_with_tenant) = 1st line LIVE. **Fix = ops set DATABASE_URL_APP (no code).**

## 5. CONVERSATION STATE (`infrastructure/conversation_state/jsonb_conversation_state.py`)
- **load_state (80-117): conversation_id None → {} (85-86)**; TTL guard last_message_at>now-ttl (96-98, DEFAULT_CONVERSATION_STATE_TTL_HOURS=24); errors→{}.
- **save_state (119-147): conversation_id None → return (125-126 no-op)**; sanitize (127); UPDATE conversations.action_state JSONB (131-137).
- _sanitize (149-168): drop keys not in ACTION_STATE_ALLOWED_TOP_KEYS{intent,slots_filled,service_locked}, cap DEFAULT_MAX_ACTION_SLOTS=5.
- detect_drift (170-240): service drift (196-218 locked name vs answer), price drift (220-238 locked price vs answer), __drift_severity runtime key (warn default).

## 6. ACTION/BOOKING
- slot_extractor.py:57-268: extract (69-163) dynamic Pydantic from owner slot_schema (no hardcode field names), LLM call_with_schema (137-146), model DEFAULT_SLOT_EXTRACTOR_MODEL_WIRE (Haiku).
- resolve_action_conversation_id (`routes/_action_conversation.py:24-56`): action_config.enabled (40) + conv_repo (41) → get_or_create (45-50), else None. **SSE wired chat_stream.py:287-300 (FIX, was None bug)**.
- _render_captured_slots (query_graph.py:814-839): slots_filled → `key="val"; missing: x,y` DATA only (sacred-#10).

## §7 CACHE-CORRECTNESS VERDICT: ✅ tenant-scoped (semantic explicit WHERE + NULL gate), embed model-scoped (OK), uq bot-scoped, multi-turn skip, corpus/bot version invalidation. No leak at cache layer.

## §7 HARDCODE: conversation_state.py:276-277 price range 10K-50M VND spa-hardcoded; TTL 24h magic (per-tenant only). slot_extractor "bắt buộc"/"tùy chọn" VN.

---
# §8 — DI/BOOTSTRAP · CONFIG · WORKERS/STREAMS · OBSERVABILITY · GRAPH ASSEMBLY

## 1. DI CONTAINER (`bootstrap.py:161-788`) — Port→Registry→Null-Object 5-layer
- Infra (168-192): db_engine RLS, session_factory, uow, redis, redis_streams (5s), api_key_pool (199-208).
- Adapters (config-driven via get_boot_config): vector_store (238-247, "pgvector"/null), lexical_retrieval Factory (255-263, "null"/pg_textsearch), embedder Singleton (275-283, env EMBEDDING_PROVIDER/"litellm", registry litellm/jina/zeroentropy/bkai_vn), reranker (345-356, "jina"/jina-reranker-v3), entity_extractor (361-369, "null"), metadata_filter (377-389, "null"), **guardrail (324-332, HARDCODE provider="local" ⚠️ DI-001 không toggle Null được)**, crag_grader (396-401), ocr (287), pii (408-411, "null").
- Services (479-631): llm DynamicLiteLLMRouter (523-529), token_ledger (517-521), hyde_generator (538-541, wired after llm), hallu_verifier (552-555), conversation_state (594-612, "null"/jsonb per action_config), system_config_service (288-292, Redis 5min), guardrail_rule_loader (306-315).
- Observability (473-492): invocation_logger (model_invocations), pipeline_audit_logger (OFF), metrics_port Prometheus.
- DI compliance: ✅ 47 ports, ~12 registries, ubiquitous Null objects. ⚠️ guardrail hardcode (DI-001).

## 2. CONFIG 7-TIER LADDER
| Tier | Source | TTL | Scope |
|---|---|---|---|
| 7 constants | shared/constants/_NN_*.py | compile | global |
| 6 bootstrap_config | get_boot_config→system_config | 30s | global |
| 5 system_config | DB JSONB | 5min Redis | tenant |
| 4 plan_limits | bots.plan_limits JSONB | — | bot |
| 3 threshold_overrides | bots.threshold_overrides | — | bot |
| 2 pipeline_config | built per-request | none | request |
| 1 _pcfg() | query_graph node | per-node | turn |
- bootstrap_config._ALLOWED_KEYS whitelist (46-251); get_boot_config (291-354) psycopg2 sync. **Bug #7c: 78 keys missing whitelist → silent no-op (FIXED 173-251).**
- pipeline_config builders MUST parity: `test_chat/_pipeline_config.py` + `chat_worker/pipeline_config.py`. **Bug #7: 38 per-intent keys missing worker (FIXED).** Test test_pipeline_cfg_keys_parity.py. **NEW key `multi_query_complexity_min` đăng ký 4 site (2026-06-18).**
- resolve_bot_limit (bot_limits.py): threshold_overrides > plan_limits > system_default (max() numeric except semantic_cache_threshold).

## 3. GRAPH ASSEMBLY (`orchestration/graph_assembly.py`)
- build_graph_di_kwargs (92-122): REQUIRED {llm,model_resolver,invocation_logger,guardrail,vector_store,embedder} fail-loud GraphAssemblyError; optional _resolve_optional→None.
- build_chat_initial_state (136-200): 25 state keys. `raw_user_message` never overwritten (173-176, slot extraction reads this).
- build_graph (query_graph.py:1037+): node sequence guard_input→understand→router→retrieve→rerank→grade→reflect→generate→guard_output→persist. Conditional edges (router→retrieve/oos, grade→reflect/generate).

## 4. WORKERS / REDIS STREAMS
- chat_worker/pipeline.py: handle_chat_received (91-106) resolve tenant + bind context + body + clear (finally). _build_pipeline_config (81), build_chat_initial_state, graph.invoke. Callbacks TokenUsageDbHook(db)/Redis(post_commit)/QuotaThresholdNotify.
- document_worker.py: handle_document_uploaded (83-107) → DocumentService.ingest 7 steps.
- redis_streams_bus.py: XREADGROUP at-least-once + **transactional inbox** event_inbox (_INBOX_MARK/SEEN_SQL 51-58, _inbox_seen 169-191 fail-OPEN, _mark_processed 193-199). inbox_tx handler param (atomic process-then-mark). Fairness semaphores bot:channel cap5 / workspace cap10 (106-107). NOGROUP auto-create, DLQ after max redeliveries.
- outbox_publisher.py: transactional outbox (write+outbox same tx → publish → mark).

## 5. OBSERVABILITY
- request_logs (models_monitoring.py:75-155): 29 fields (identity, hashes question/answer, routing, timing, tokens, cost, status, citations JSONB, feedback). Indexes (tenant,started_at)...
- request_steps (160-194): per-node (step_name, step_order, model_used, binding_id, tokens, cost, duration_ms, status). **33 step_name instrumented**: guard_input/understand_query/condense_question/router/router_select_model/query_complexity/adaptive_decompose/decompose/rewrite/rewrite_retry/multi_query_fanout/cache_check/semantic_cache_check/retrieve/graph_retrieve/multistage_retrieval/retrieve_fallback/rrf_fuse/rerank/filter_min_score/mmr_dedup/neighbor_expand/litm_order/grade/grounding_check/reflect/prompt_build/prompt_compression/generate/critique_parse/guard_output/citations_extract/persist.
- model_invocations (models_invocation.py): every LLM/embed/rerank (purpose, provider, model_id, tokens, cost, cached, feature_name "query.generation"/"ingest.enrich").
- invocation_logger (102-150): async ctx insert-running→update. audit_log hash chain (models.py:674+ alembic 010g). StepTracker batch opt (batch_step_logging_enabled).
- Metrics: citation_validation_fail_total, grounding_fail_total, cliff_drop_total, cost_usd_total, tokens_used_total, model_invocation_total, chat_worker_queue_depth, document_ingest_duration_seconds.

## §8 OBSERVED ISSUES
- DI-001 guardrail hardcode "local" (bootstrap.py:326) — cannot Null without redeploy.
- CONFIG-003 pii frozen DI (410).
- Broad-except: 3 unjustified + 248 noqa BLE001 (well-controlled). version-ref ~0 (4 dead comments).

---

# §9 — CROSS-CUTTING ROOT CAUSES + DEBUG ENTRY POINTS

## A. CHUỖI GỐC RỄ BUG-1 (CONFLATE giá) — across subsystems
`bot trả 2.499.000 (sai)` ← `LLM thấy chunk có nhiều dịch vụ` ← `vector path retrieve chunk co-occur` ← `query "giá X" KHÔNG route stats` ← `parse_list_query:376 loại "gia bao nhieu"` (§2) **+** `chunk co-occur do table_dual_index group chunk` (§6) **+** `grounding judge warn-only không chặn conflate` (§5).
→ **Immutable cause**: catalog Q&A đi fuzzy-vector thay vì structured-first. **Fix đúng tầng** = route price-of-named-entity → `query_by_name_keyword` (stats deterministic, labeled price) + ingest dùng table_csv per-row (không group chunk) + dedup stats rows.

## B. WHACK-A-MOLE ROOT (BUG-2): routing = 3 hardcoded VN regex parser (`query_range_parser.py`). Mỗi câu mới = nhánh mới. Expert fix = **unified intent+entity extractor** (Self-Query) thay pile regex, multilingual, config/LLM-driven.

## C. DEBUG ENTRY POINTS (file:line bắt đầu)
| Triệu chứng | Bắt đầu đọc |
|---|---|
| Giá sai/conflate | query_range_parser.py:374-377 → retrieve.py:198-273 → query_graph.py _do_stats_lookup 2786 |
| List thiếu | stats_index_repository.py:418 query_by_name_keyword + retrieve.py:226 |
| Refuse oan có docs | grade.py:439-533 adequacy + retrieve.py confidence 270 |
| Bot bịa số | local_guardrail.py:410 (warn-only!) + sysprompt (bot.system_prompt) |
| Booking quên slot | jsonb_conversation_state.py:85/125 + chat_stream.py:287 |
| Cross-tenant nghi ngờ | engine.py:67-81 + .env DATABASE_URL_APP |
| Chậm | generate.py:481 context cap + retrieve.py multi_query + sysprompt token |
| Config flip no effect | bootstrap_config._ALLOWED_KEYS + pipeline_config parity 2 builders |
| Cache stale | semantic_cache.py:870 bot_version + corpus_version |

## D. TEST/REPRO HARNESS
- `scripts/verify_fixes_loadtest.py` — 22-case parallel load-test (list/price/factoid/trap) đo latency+coverage+HALLU, bypass_cache.
- `scripts/debug_query_loadtest.py` — per-question request_steps dump.
- Parser route check: `python -c "from ragbot.shared.query_range_parser import *; print(parse_list_query('...'))"`.
- Ground-truth: `SELECT entity_name,price_primary FROM document_service_index WHERE unaccent(entity_name) ILIKE unaccent('%...%')`.

## E. SACRED INVARIANTS (đừng phá khi fix)
1. App KHÔNG inject text/override answer (sacred-#10) — sysprompt = single source.
2. HALLU=0 — refusal trap honored, ground-truth only.
3. zero-hardcode (số → constants/system_config), domain-neutral (no brand in src), no-version-ref.
4. 4-key identity ở resolve boundary; record_bot_id internal.
5. Config via alembic/admin-UI, KHÔNG psql hotfix.
6. EVOLVE-not-REWRITE (strangler fig).

## F. STATE 5 TIÊU CHÍ (đo 2026-06-18)
| Tiêu chí | State |
|---|---|
| Đúng/Faithfulness | ❌ CONFLATE giá (BUG-1) — không 100% |
| Nhanh | ❌ p95 ~15s |
| UX | ⚠️ booking fixed; list ok; price phrasing loạn |
| Performance | ⚠️ RLS off (BUG-4); rerank alive |
| Cost | ⚠️ sysprompt 2400 tok; cache 96% prefix ok |

---
*PART I (§0-§9) = bản đồ điều hướng đặc. PART II bên dưới = full detail prose từng subsystem (để debug sâu).*

---
---

# PART II — FULL DETAILED FLOW MAPS (prose chi tiết, file:line đầy đủ)

> Phần này là bản chi tiết-đầy-đủ của từng subsystem, viết dạng prose để đọc tuần tự khi debug sâu. Trùng lặp có chủ đích với PART I (PART I tra cứu nhanh, PART II đọc hiểu sâu).

## ════════ PART II-A — HTTP ENTRY · AUTH · 4-KEY · REQUEST LIFECYCLE (full) ════════

### I. FASTAPI APP BOOTSTRAP
**1.1 Entry Point** — `src/ragbot/main.py:14-33`: `main()` installs uvloop + uvicorn.run(), points to `ragbot.interfaces.http.app:app` (factory).
**1.2 App Factory & Lifespan** — `interfaces/http/app.py:169-384` (lifespan async ctx), `:387-595` (create_app). Lifespan bootstrap: get_settings (175); `_check_required_provider_keys` fail-loud UAT/staging/prod khi OPENAI_API_KEY/RERANKER keys thiếu (123-166); Container() → app.state.container (188-189); app.state.settings (190); OTel+Sentry (193-195); Bus Redis Streams eager-init best-effort (198-206); LiteLLM routing refresh (208-213); parallel cache-warm asyncio.gather (274-279): model_resolver + bot_registry_service + JwtTokenService owner-token ensure + GuardrailRuleLoader; embedded worker tasks (343-353) nếu APP_EMBED_WORKERS_ENABLED; shutdown drains bus.close→cache.close→db_engine.dispose (360-383).

**1.3 Middleware (insertion = reverse exec; REQUEST inbound order):**
1. IpRateLimitMiddleware (540-547, added LAST=outermost): per-IP/min cap DEFAULT_RATE_LIMIT_PER_IP_PER_MIN=300, trusted-proxy+IP-allowlist, fail-CLOSED on Redis outage.
2. SourceRateLimitMiddleware (457-460): scope (record_tenant_id, source_tag) trên /documents/ingest+/sync, per-source burst, degrade-OPEN.
3. BotRateLimitMiddleware (444-447): scope 4-key (tenant,workspace,bot,channel), DEFAULT_RL_BOT_PER_MIN=60 (ingest 10), X-RateLimit-Bot-* headers, bypass khi bypass_rate_limit trong bot config.
4. SlidingRateLimitMiddleware (429-434): per-token L2, fail-mode tunable, limiter per-request từ container.
5. SecurityHeadersMiddleware (416-419): HSTS opt-in DEFAULT_SECURITY_HEADERS_HSTS_ENABLED, wires after GZip.
6. GZipMiddleware (410): minimum_size GZIP_MINIMUM_SIZE.
7. TraceContextMiddleware (409, `middlewares/trace_context.py:20-31`): X-Trace-Id read/gen UUID, sanitize `[A-Za-z0-9_-]{1,128}`, bind request.state.trace_id + structured logs, echo X-Trace-Id response.
8. TenantContextMiddleware (408, `middlewares/tenant_context.py:86-431`): PUBLIC_PATHS bypass frozenset (47-62) /health,/health/models,/metrics,/favicon,honeypot. **Service JWT HS256 (111-206)**: JwtTokenService.verify_token via app.state.container; claims record_tenant_id/tenant_id(legacy int)/sub/role/rl_val/rl_win; resolve record_tenant_id UUID (explicit claim → fallback upstream INT); bot bypass-RL lookup `ragbot:bot:{tenant}:{workspace}:{bot}:{channel}` (182-206, workspace chain request.state→body→str(tenant)); Layer1 per-tenant TenantRateLimiter.check tenant_bypass (212-282); Layer1.5 per-service-token _check_rate_limit (309-324); Layer2 per-user connect_id async gather (328-373); bind request.state {record_tenant_id,user_id,bot_id=None,role,rl_bypass}. **User JWT RS256 fallback (394-431)**: jwt_verifier.verify; claims record_tenant_id/bot_id/user_id/role; fails 410-415 nếu thiếu record_tenant_id. enforce_tenant_match (481-500) JWT==body except owner/super_admin.
9. SchemaVersionMiddleware (407, `schema_version.py:52-96`): X-Schema-Version (default DEFAULT_SCHEMA_VERSION=1) vs SUPPORTED_SCHEMA_VERSIONS, 400 invalid.
10. CORSPerTenantMiddleware (470-474, `cors_per_tenant.py:100+`): per-tenant allowed_origins via TenantConfigCache, pre-auth paths env CORS, wildcard `https://*.example.com`.
11. BodySizeLimitMiddleware (482, `body_size.py:28-83`): reject before auth; per-path /test/chat+/chat DEFAULT_MAX_BODY_CHAT_BYTES, /documents+/sync DEFAULT_MAX_BODY_INGEST_BYTES(16MB), default 10MB; reject chunked w/o Content-Length (411).
12. AntiAbuseMiddleware (510-517, optional): UA denylist DEFAULT_UA_DENYLIST_PATTERNS, API-key hash allowlist, settings.app.anti_abuse_enabled.
13. LoggingMiddleware (406, `logging_mw.py:17-38`): monotonic duration, Prometheus http_requests_total{method,route,status}, log http.request.

### II. CHAT ENTRY POINTS
**2.1 POST /chat (queued 202)** `routes/chat.py:40-93`: identity (47-68) JWT record_tenant_id from request.state (53), resolve_workspace_id fallback str(tenant) (58-60), 4-key BotRegistryService.lookup (64-66, 404 None); AnswerQuestionCommand (70-86); enqueue Redis Stream → chat_worker (87); 202 {job_id,status="queued",status_url,trace_id} (88-93).
**2.2 POST /chat/stream (SSE 200)** `routes/chat_stream.py:87-470`: tenant 403 (96-98); streaming flag streaming_response_enabled OR streaming_enabled (108-126); workspace+4-key lookup→bot_cfg (129-144); request_log_repo.create_request_log (189-207); StepTracker (209-214); build_graph_di_kwargs+get_graph (238-258); oos_template 7-tier + sysprompt_assembler (260-281); asyncio.Queue sink + resolve_action_conversation_id (283-300); build_chat_initial_state (301-323); state["_stream_sink"]=sink (323); graph.ainvoke timeout-guard (345-376); StreamingResponse stream_real_llm (449-470); finalize_request_log + _save_history (379-406).
**2.3 POST /test/chat (demo inline)** `routes/test_chat/chat_routes.py:68-220`: no-auth fallback _PLATFORM_TENANT_FALLBACK_UUID; bypass_cache flag (268-269); workspace explicit find_by_4key OR find_by_3key_unique omitted (98-109); inline graph; debug payload optional.
**2.4 POST /test/chat-async** `routes/chat_async.py:144-250`: enqueue CHAT_REQUEST_STREAM, chat_async_worker consumes, result Redis hash, poll GET.

### III. 4-KEY IDENTITY
(1) record_tenant_id UUID: JWT claim preferred OR legacy tenant_id int; bind tenant_context.py:131-144(svc)/404-409(user); request.state NEVER body. (2) workspace_id slug: body optional, fallback str(record_tenant_id) (workspace_id_validator.py:99-106), validate `[a-z0-9-]{1,36}` (54-72). (3) bot_id slug: body REQUIRED len 1-128. (4) channel_type: body REQUIRED default "web" test.
**Resolve boundary** BotRegistryService.lookup (`services/bot_registry_service.py:103-168`): cache key `ragbot:bot:{tenant}:{workspace}:{bot}:{channel}` (ví dụ ragbot:bot:550e...:spa-tenant:support:web); Redis cache (133), tenant-match validate (184) evict poisoned; single-flight AsyncSingleFlight miss (141-168); bot_repo.find_by_4key (149); back-fill TTL DEFAULT_BOT_CONFIG_TTL_S~3600; metric ragbot_cache_stampede_avoided_total.

### IV. RATE LIMIT & BYPASS
Service JWT (168-306): extract bot_id/channel/workspace from body (175-176), cache `ragbot:bot:...` → _bot_data bypass_rate_limit (203); Layer1 tenant_cfg.bypass_rate_limit → limiter.check (decision propagate L1.5+L2 skip 286); Layer1.5 skip nếu _bypass_rl (309); Layer2 skip nếu _bypass_rl OR no connect_id (328), per-user-overrides per_user_rl_val/win.
Idempotency POST /chat only: idempotency_key → idempotency_service.check_and_record. Loadtest bypass token (test only): JWT claim loadtest_bypass_token.

### V. REQUEST LIFECYCLE LOGGING
RequestLog create (request_id, record_tenant_id, workspace_id, connect_id, question_hash SHA256, message_id BIGINT, record_bot_id, record_conversation_id, channel_type, trace_id, started_at, status="running"); finalize (answer_hash, record_model_id, model_name, refusal_reason, prompt/completion_tokens, cost_usd, status success/failed, error_code/message, retrieved_chunks refs, citations, finished_at, duration_ms, payload_sha256).
RequestStep via StepTracker: step(name, model_used, binding_id, metadata) async ctx; persist RequestStepModel (request_id, step_name, step_order, model_used, input/output_tokens, cost_usd, duration_ms, status, error, metadata); PII redaction Phase D2 nếu plan_limits.pii_redaction_universal; Prometheus step_duration_seconds.
Trace propagation: X-Trace-Id sanitize → request.state.trace_id → bind_request_context structlog → response header + request_logs.trace_id.

### §II-A OBSERVED ISSUES
7.1 4-key cache key fixed mega-sprint G7 (tenant_context.py:195-199). 7.2 workspace divergence production 4-key/str(tenant) vs demo 3-key ambiguous. 7.3 service-JWT vs user-JWT bind different fields (user JWT no rl_val/win/tenant_bypass). 7.4 idempotency chỉ /chat queued, KHÔNG SSE → 2 client trùng chạy 2 lần. 7.5 OOS empty all-tier → empty answer. 7.6 history load best-effort empty (chat_stream.py:154-176) no metric. 7.7 message_id=int(time.time()*1000) (147) collide trong cùng ms — dùng time_ns/UUID. 7.8 bypass_cache chỉ test routes.

---
## ════════ PART II-B — QUERY UNDERSTANDING · ROUTING (full) ════════

### 1. understand_query NODE (`orchestration/nodes/understand.py`)
1.1 Idempotency/cache (55-107): guard `_understand_skipped_by_parallel` (76) — cache_check_and_understand_parallel may run first; Redis cache gate (82-107) TTL DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S=600; cache keys whitelist {intent,intent_confidence,query,original_query} (106); seed only khi has_meaningful_history=false (history thay đổi prompt body).
1.2 Heuristic intent fast-path (109-142, `heuristic_intent_classifier.py:109-147`): gate heuristic_intent_enabled default TRUE (113-125); _classify_heuristic (117) → HeuristicResult(intent,confidence,pattern); threshold skip LLM nếu conf≥0.85 (118-124); return early (125-142). Pattern registry (62-106): INTENT_GREETING `^(xin chào|hi|hello|chào em|chào bạn|chào shop|hey|xin chao)\b`=0.90; INTENT_CHITCHAT `^(cảm ơn|cám ơn|thanks|thank you|ok\b|...)\b`=0.90; INTENT_AGGREGATION `(có mấy|bao nhiêu|liệt kê|tất cả|toàn bộ|kể tên|các loại|mấy loại|bao gồm những gì|gồm những gì)`=0.85; INTENT_MULTI_HOP `(tại sao|vì sao|giải thích|nguyên nhân|lý do|how come|why)`=0.85; INTENT_COMPARISON `(so sánh|khác nhau|khác gì|vs\b|versus|difference between|hơn hay kém|tốt hơn|nên chọn)`=0.85. factoid=default fallback (no pattern). HALLU=0 sacred: heuristic NEVER fires domain-specific → LLM fallback.
1.3 History condense (157-176): meaningful nếu len(history)>DEFAULT_CONDENSE_MIN_HISTORY_TURNS(3) AND chars≥DEFAULT_CONDENSE_MIN_HISTORY_CHARS(150); condense prompt limit condense_history_limit(5); fallback `<question>{query}</question>`.
1.4 Bot context (178-185): inject bot_system_prompt vào `<bot_context>` preview DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS=500.
1.5 LLM structured (186-299): gate structured_output_enabled ∧ understand_use_structured_output (191-196); _invoke_structured_llm_node(UnderstandOutput) (200-206); parse condensed_query (217), intent (218), confidence clamp [0,1] default 0.5 (224-230); state keys (231-238) intent, intent_source="llm", intent_confidence, query(condensed nếu ≠orig), original_query(raw preserve). UnderstandOutput schema (`dto/llm_schemas.py:51-93`): condensed_query 1-100ch, intent Literal[factoid,comparison,multi_hop,aggregation,out_of_scope,greeting,feedback,chitchat,vu_vo], confidence=0.5.
1.6 Cache (258-281) hit khi _uq_cache+_uq_bot_id+_uq_query+not meaningful_history; TTL get_boot_config understand_query.cache_ttl_s.
1.7 Fallback (301-311): catch InvariantViolation/Timeout/OSError/RuntimeError/ValueError/KeyError → {intent:DEFAULT_INTENT_FALLBACK="factoid", intent_confidence:0.5}.

### 2. query_complexity (`orchestration/nodes/query_complexity.py:96-213`)
Structural early-exit (136-142): 1 struct-ref + ≤1 comma + no conjunction + ≤80ch → ("simple",0.0). Pattern `(Chương|Mục|Phần|Điều)\s*\.?\s*\d+`. Signals additive config: comma max(0,n-1)×0.5, conjunction ×0.4 (word-boundary `" {tok} "`), numbers ×0.3, question max(0,n-1)×0.6, length word/20. Conjunctions JSON `["và","hoặc","cũng như","or","and"]`. Threshold DEFAULT_QUERY_COMPLEXITY_THRESHOLD=1.2 → complex. has_aggregation_keyword (216-237) per-lang DEFAULT_AGGREGATION_KEYWORDS_BY_LANG (constants/_24:54-90): VI 16 kw, EN 14 kw, JA empty; non-VI → empty dict.

### 3. decompose (`query_decomposer.py:132-191`)
Gate decomposer.enabled. Model DEFAULT_DECOMPOSER_MODEL gpt-4.1-mini (Haiku banned 2026-05-12). Domain-neutral prompt (54-78). Parse sub_queries cap max_sub_queries (111-129). Fallback [query] (172-191) log decomposer_llm_call_failed.

### 4. rewrite+MQ (`query_graph.py:2682-2723`)
Gate pipeline_parallel_rewrite_mq_enabled OFF. Decompose precedence sub_queries≥2 → fanout_bypassed (2705-2710). MQ gate (retrieve.py:1189-1205): skip nếu decompose fired OR _mq_queries preset OR chitchat OR <min_tokens(5). Complexity gate _mq_cx_min (query_graph.py:2443) default 0.0. N variants 3.

### 5. ROUTE STATS vs VECTOR (`retrieve.py:176-273`) — order
1. _parse_range_query(_raw_query) (205), _raw_query=original_query or query. 2. _parse_code_query (216-220) nếu range None + stats_code_lookup_enabled. 3. _parse_list_query (226-227) nếu range None. 4. Superlative kill-switch (231-239) op max/min + stats_superlative_enabled=false → None. 5. Structural guard (240-269) article anchor → skip stats, pattern `(?i)\b(điều|khoản|điểm|chương|mục|tiết|article|section|clause|chapter|paragraph)\s*\.?\s*\d+`. 6. Confidence floor (270-272) <0.7 → vector. 7. Race (284-521) stats+vector concurrent timeout 3s stats-preferred. 8. Sequential (524-573) stats-first fallback vector.

### 6. PARSER (`query_range_parser.py`) — 🚨 BUG-1/2 root
RangeFilter (84-102). parse_range_query (245-334): range "từ X đến Y" `_RANGE_FROM_TO_RE` conf 0.9 (260-275); fuzzy "khoảng X" ±10% conf 0.75 (277-290); below tokens (158-168) `duoi|it hon|nho hon|thap hon|khong qua|toi da|max|<|<=` conf 0.85; above tokens (171-180) `tren|hon|lon hon|cao hon|tu|min|>|>=` conf 0.85; superlative MAX (187-198) `dat nhat|mac nhat|cao nhat|cao cap nhat|dat tien nhat|dat gia nhat|most expensive|highest|priciest` / MIN (199-210) `re nhat|thap nhat|re tien nhat|re gia nhat|phai chang nhat|binh dan nhat|cheapest|lowest|least expensive` conf 0.8.
_COUNT_SIGNALS (109-117). _LIST_SIGNALS (119-135). **parse_list_query (359-400) line 374-377: `if "gia bao nhieu" in folded or "bao nhieu tien" in folded: return None`** ← BUG-1. has_list/has_count/has_cat (378-384). Strip _LIST_STRIP_PHRASES (385-394). min len 2 (395). _LIST_STRIP_PHRASES (339-356) connective "về/vào/không/có" left-in (349-354). parse_code_query (410-445) `[A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)+` overridable, ≥1 letter. Bare<1000 guard (481-482). Date-tail `\s*/\s*\d` (471-472).

### §II-B FAILURE CASES (debug-actionable)
A "dịch vụ X bao nhiêu": "bao nhiêu" count-signal + no "gia bao nhieu" exclusion → list ALL X (wrong). B "Điều 12 giá": fold "tu"→từ extract 12 → guard <1000 reject ✓. C "có dịch vụ VỀ da chết": "về" left-in → ILIKE fail → vector. D "gần như rẻ nhất": "re nhat" substring → op=min (misclassify, no negation guard). E 3+ struct-refs → complex → decompose (inefficient). F "Điều 38" simple → MQ fanout anyway (cx_min=0.0).
**🚨 CRITICAL**: "giá \<tên dịch vụ không code\>" → KHÔNG route stats nào → VECTOR → conflate (BUG-1).

## ════════ PART II-C — RETRIEVAL DUAL-PATH (full) ════════

### 1. retrieve() (`retrieve.py:147-173`) — branch decision (176-573)
Route triggers in order: STATS-INDEX (176-572 gate stats_index_repo!=None), DOC-SUMMARY (574-620 _matches_summary_pattern), SPECULATIVE (622-668), HYBRID (670-1873).
Stats sub-steps: range(205)/code(220)/list(227)/superlative(816-823); structural guard skip (240-269); confidence ≥0.7 (270-272); race mode asyncio.wait vector+stats winner stats-preferred (284-521); sequential _do_stats_lookup (525) return early if linked_chunks (537-565) else fallthrough (566-572).

### 2. PATH A vector/BM25 hybrid
Preprocessing (670-800): expand_abbreviations (692-697), restore_diacritics opt-in supplementary BM25 (1612-1671), generic vocab _bot_custom_vocab=custom_vocab["synonyms"] enrich_state (734-761), metadata L1 LLM (777-782)/L2 regex (816-841)/L3 LLM (853-895). topK (700-732) per-intent + keyword promotion aggregation. MQ/decompose (1126-1315). Pre-batch embed (1323-1339). _run_hybrid_for_query (977-1124) old/new port detection, threads bot/channel/corpus/tenant/metadata_filter/embedding_column/adaptive-weights/structural-prefilter. RRF (1341-1391) gather → mq_rrf_merge_chunks rrf_k=60 (1371) dedup chunk_id → cap. Lexical BM25 (1673-1735) RRF merge. Permission pre-filter (1737-1755). Parent-child (1757-1804). Autocut (1806-1817 gap 0.3). Superlative enrich (1857-1873).

### 3. PATH B stats (`query_graph.py:2786-3000`)
3 SQL (`stats_index_repository.py`): query_by_price_range (165-250), top_by_price (252-318 limit 5), query_by_name_keyword (418-494 unaccent ILIKE + synonyms OR-expand 454-467). Synthetic-chunk (2867-2965): _is_field_like max 120ch/12w, "{name}: {price}" or "price: {price}", dedup (entity_name,price), chunk_id sentinel "stats_index_synthetic" score 1.0. Linked-chunks (2834-2991): FK attempt + doc fallback.

### 4. RERANK (`rerank.py:55-488`)
Resolver (reranker_resolver.py:80-323): binding purpose='rerank' → system_config → NullReranker, cache TTL. Mode (146-161). Cross-encoder (163-197) fail-soft RRF. Filter CLIFF (255-304 floor 0.0 gap 0.4 min_keep 3)/THRESHOLD (305-331). Gate-after-cliff (354-387). Max-to-LLM (389-404). Safety-net (449-482).

### 5. GRADE (`grade.py:60-567`)
Stats-route bypass (92-111). High-score skip (113-162). Structured batch (186-311)/per-chunk (313-398)/no-SO (402-436). Verdict map (180-184) + remap intent (288-293). Adequacy (439-533): all-relevant→adequate; all-irrelevant→fallback gate (487-499); ambiguous→retry. False→OOS.

### 6. MMR (query_graph.py:3049-3094) per-intent threshold. NEIGHBOR (neighbor_expand.py:397-452) plan/fetch/merge token-budget.

### §II-C OBSERVED ISSUES (debug)
🚨 #1 vector multi-service conflation: table_dual_index group chunk "[A 100k][B 200k]" → embedding centroid → diluted match → wrong price (BUG-1 retrieval-side). #2 stats empty fallback. #3 rerank silent drop→safety-net. #4 dedup gaps across RRF+MMR+neighbor. #5 metadata over-restriction. #6 synthetic chunk_id sentinel.

---
## ════════ PART II-D — GENERATION · SYSPROMPT · MODEL · CITATIONS (full) ════════

### 1. generate (`generate.py`)
Entry 90-108 closures. Clock 110-125 audit generate_started. Refuse short-circuit 242-287 (flag refuse_short_circuit_enabled, graded empty + not chitchat + not action → _oos_text → answer_type="no_context"). Cascade 302-351 (complexity→tier model 336, degrade 346). Context opt: prompt_compression 353-394, adaptive_context 401-417, lost-in-middle reorder 420-439, token_opt 472-479. **Context cap 481-528** (generate_context_chars_cap_by_intent 488-509, drop tail 514-523, chunk_ids_allowed 524-528). **Context block 540-580** (xml_wrap 540; loop extract 543-549; **🔑 551-552 `if not cid: continue`**; xml `<chunk id type section><content>{text}</content>` 553-571 OR legacy `<context source chunk id>` 572-579; join \n\n 580). **System prompt 582-593** (state["bot_system_prompt"] 582 OR _lang.prompt_generator 584; **{captured_slots} _render_captured_slots 589-592 DATA only**). Output cap 645-656 compute_output_cap. **Msg order 602-616** ([1] system 603; [2] history capped+citation-stripped+truncated 604-609; [3] user `<documents>{ctx}</documents>\n\n<question>{q}</question>` 610-616). LLM 694-802: purpose resolve 676-692; structured 694-751 _resolve_generate_schema→validate citations vs chunk_ids_allowed 720-751 metric citation_validation_fail_total; free-form 753-802 _CITATION_RE.findall 771 validate 775-793. Post-hoc 809-821 (empty+graded→top chunk source posthoc_top_chunk). Action drift 873-952 (detect_drift warn→flag/block→GuardrailBlocked 928-932, save 941-952). SLA 841-861 generate_p95_sla_ms + TTFT.

### 2. SysPromptAssembler (`sysprompt_assembler.py`)
3-tier (18-21) base + language_packs[locale].sysprompt_default_rules − plan_limits.sysprompt_rules_disabled. assemble (83-126) fetch (106-114) strip (120-125) return concat (126 no override). Seed alembic 20260611_0204_sysprompt_aprime UPDATE language_packs prompt_key='sysprompt_default_rules' (A-prime 22-rule 8KB→concise). opt-out _extract_disabled_rules (161-191) forms ["rule_17"]/[17]/["17"]; _strip_rules regex _RULE_BLOCK_RE (66-69, 194-213). Graceful degrade → bot.system_prompt unchanged. Called bootstrap.py:584; chat_stream:278; admin_bots:230 (effective-prompt endpoint); test_chat:391; chat_worker pipeline:580. Pin test_sysprompt_assembler_pin.py 5 pass.

### 3. SACRED-#10 VERDICT ✅ NO INJECT/OVERRIDE
Prompt XML framing only (603-616); {captured_slots}=DATA (589-593); answer verbatim free (763) / structured (712); refusal REPLACES not appends (269-287); refusal origin _resolved_oos_template 7-tier (query_graph.py:685-716 bot column→plan_limits→workspace→tenant→system_config→language_pack→DEFAULT_OOS_ANSWER_TEMPLATE=""); _lang from DB language_packs not hardcoded. Docstring pins (generate.py:10-12, 135, 296).

### 4. MODEL resolution
resolve_purpose_for_intent (_helpers.py:128-155) factoid→llm_factoid/chitchat→llm_chitchat/OOS→llm_oos/else llm_primary. resolve_llm (__init__.py:117-153). Cascade (cascade_router_helper.py / query_graph.py:302-351).

### 5. TOKEN BUDGET (token_budget.py:63-78) compute_output_cap zero-default sacred. Context cap per-intent (488-509). History cap min(condense_history_limit, DEFAULT_GENERATE_HISTORY_MAX_MSGS); factoid skip-history (598-599). Sysprompt ~2400 tok = BUG-3 lever.

### 6. CITATIONS _CITATION_RE=`\[chunk:([0-9a-f\-]+)\]` (query_graph.py:407). Validate chunk_ids_allowed. Post-hoc top chunk. Metric citation_validation_fail_total.

### §II-D OBSERVED: sysprompt bloat (A-prime mitig); chunk-id drop no audit; OOS empty-string all-tier; cascade silent degrade no metric; sysprompt_rules_disabled operator-only no UI; locale ≠vi/en fallback vi.

## ════════ PART II-E — GUARDRAILS · GROUNDING · HALLU (full) ════════

### 1. INPUT (`query_graph.py:1703-1761` → `local_guardrail.py:796-847`)
too_short (188-218 min_alpha DEFAULT_GUARDRAIL_MIN_ALPHA_CHARS=2, 0=skip, block); length_limit (105-117 DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH=4096 block); DB regex rules (723-761 guardrail_rules alembic 010f, classic reserved) OR static prompt_injection (120-142 block)/pii_vi phone`(0\d{9,10}|\+84\d{9,10})`/email/cmnd`\b(\d{9}|\d{12})\b` (145-170 warn/redact)/pii_en ssn/sql_injection (221-233 block). Default patterns `_default_patterns.py:46-200` + seed `alembic 20260516_010f`. Blocked (1738-1761) _resolved_oos_template + per-rule response_message (1752).

### 2. OUTPUT (`guard_output.py:49-516`)
system_prompt_leak (local_guardrail.py:298-350): skip OOS-refusal Jaccard≥0.90 (_is_oos_refusal 257-279); shingle hash size 8 (244-251); doc-shingle subtraction (260-270); block matches≥**DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT=10** (105-112). Intent-skip greeting/chitchat (221-227). **Stats-route skip (234-241)**. secret_scanner (353-365 block). grounding_check numeric/substring (368-414, citation marker `\[...\]`, substring 239-254, numeric overlap 282-288) **severity warn 410-411 NEVER block**. llm_grounding_check (417-553): intent-gated grounding_intents factoid/comparison/aggregation/multi_hop, stats skip, async option (109-152), structured (556-587)/text-parse (590-628), **max_sentences=5 HARDCODED (413,451)**, ratio>0.30 → warn, silent degrade (514-520) → None. Parallel/serial (272-302). Blocked (500-516) _oos_template.

### 3. 🚨 HALLU=0 VERDICT: grounding judge observability(warn) NOT enforcement. Enforced by sysprompt anti-fabricate + retrieval quality + CRAG retry + citation opt-in. Trap coverage: Fabricate ✅, Misinterpret ⚠️, Extrapolate(sum) ❌, **Conflate(entity-map) ❌ → BUG-1 không bị chặn**.

### §II-E FP/FN: numeric_overlap FP HIGH (addends present→pass dù sum sai). llm_grounding sentence-cap FN HIGH (tail>5). silent-degrade FN HIGH. English grounding prompt (479-483) VN risk. _SENTENCE_SPLIT_RE `[.!?]\s+` splits VN decimals.

## ════════ PART II-F — INGESTION · CHUNKING · STATS (full) ════════

### ENTRY worker (document_worker.py:83-370)
handle_document_uploaded (83-107) → _inner (110-370). _is_refetchable_url (74-80) guard Google edit?gid=→HTML. U0 raw_content DB reuse (224-255) "worker_reused_raw_content", fallback parser (276+) _is_refetchable check + raise _LocalSourceNotRefetchable (288).

### STAGES
U1 validate (ingest_core.py:275-286) tenant guard + workspace RLS GUC (worker 127-139) + Phase D. U2 parse (ingest_core.py:314-344) registry detect_parser (registry.py:81-104) row-shaped bypass smart_chunk (672-677); adapters google_sheets/excel/pdf/docx/markdown/null. U3 clean (ingest_stages.py:221-337) CleanBase Tier-0 (258-275 HTML+NFC+zero-width+injection) + legacy + LLM metadata opt (321-336 OFF). U4 chunk (339-800): L1 whole-doc (356-382), L2 parent-child (400-445 OFF), L3 doc-profile (446-651 OFF DocumentProfile 10 feats), L4 select_strategy (analyze.py/strategies.py) hdt|semantic|recursive|table_csv|table_dual_index|parser_preserve|hybrid|proposition, L5 cross-check (~520 ON), VN heading promote (vn_structural.py), orphan merge (not tabular), M25 histogram (730-748). U5 enrich (ingest_stages_enrich.py:120-500) CR legacy (201-219 OFF) + WA-3 (222-298 OFF chunk_context col never-in-prompt QG#10) + VN segment (300-354 to_thread timeout) concurrent (310-400 cache-warm). U6 vn_segment (vi_tokenizer.segment_vi_compounds)→content_segmented. U7 embed_store (ingest_stages_store.py:120-999): spec (154-158), embed-text strategy auto/raw_only/prefix_plus_raw (180-195), Narrate-then-Embed (227-276 OFF), passage prefix (278-286), late_chunking sliding/single (288-388 ON), standard batches (409-443 batch 50 raise-on-fail), chunk identity M21 (537-560 UUID5 OFF/UUIDv7), structured-ref (597-628 OFF article_no JSONB), insert parent-child (652-848)/flat (851-950), semantic cache invalidate (954-975).

### FINALIZE (ingest_stages_final.py:120-377)
State flip atomic (145-227): total=0→failed, null_non_parent>0→failed (parent legit NULL 149-153), else active. **Stats extraction (305-342)**: delete_by_document (repo 132-163); **parse_table_chunks (document_stats.py:259-320)** skip prose (283-289), detect header (305-307), category single-col heading (309-314), extract ParsedEntity (316-318); parse_money_vn (82-103) "1.499.000"/"1tr499"/"499k" filter<50k; header tokens HARDCODE (58-65) stt/ten/gia/vung/loai/dich vu/service/price/name/category; aggregate_summary (323-383) buckets; bulk_insert (repo 57-130 RLS session_with_tenant); upsert documents.summary_json.

### §II-F STATS VERDICT: entity_category mostly NULL (explicit col OR multi-group heading, NOT LLM) → self-query CHƯA reliable (BUG-5). NO ingest dedup → 3-8 dupes (table_dual_index per-row+group OR CSV repeat-header) (BUG-5). 🚨 CONFLATE root: table_dual_index GROUP chunk → embedding centroid lẫn → vector diluted match → fix table_csv per-row exclusive.

### §II-F HARDCODE: document_stats.py:58-65 header tokens VN-biased no-config. DEFAULT_PRICE_MIN_VND=50k VND-centric. VN segment vi-only. HDT Chương/Mục/Điều assume VN.

---
## ════════ PART II-G — CACHE · TENANCY · RLS · STATE · ACTION (full) ════════

### 1. SEMANTIC CACHE (`semantic_cache.py`)
Hash fast-path (410-459): `SELECT answer,citations,model_name,cached_at_ts,metadata_json FROM semantic_cache WHERE record_bot_id=:bot AND record_tenant_id=:tenant AND query_hash=:h AND bot_version=:bv AND corpus_version=:cv AND (expires_at IS NULL OR expires_at>now()) ORDER BY created_at DESC LIMIT 1`. Cosine slow-path (461-527): pgvector `<=>` HNSW same scope + `1-(emb<=>:emb)>=:threshold` (default 0.97). store() (529-597): **NULL-tenant gate 545-552 skip+warn** (test_semantic_cache_no_null_tenant_write.py). Stampede 2-tier (191-368): Redis SETNX `ragbot:cache:lock:{bot}:{qhash}` (230-301) + asyncio.Lock weakref (303-368), stampede-avoided counter. bot_version _compute_bot_cache_version (query_graph.py:870-873) sha256(system_prompt+oos_template)[:12]. bypass (1777-1780). Multi-turn skip (1787-1792) conversation_history present → skip.

### 2. EMBED CACHE (embed_cache.py): `ragbot:embed:{model}:{sha256(query)[:16]}` model-scoped (intentional cross-tenant reuse), TTL 3600, Redis errors silent.
### 3. UQ CACHE (understand_query_cache.py:49,64): `ragbot:uq:v{ver}:{record_bot_id}:{sha256(query[:300])[:16]}` bot-scoped.

### 4. 🚨 RLS VERDICT
**.env**: DATABASE_URL superuser postgres@<db-host>, DATABASE_URL_APP UNSET, RAGBOT_ALLOW_SUPERUSER_RUNTIME=1. engine.py:67-81 url_app None + escape=1 → admin DSN + WARN. Superuser ignores FORCE RLS → **policies cosmetic**. Layers: alembic 0069 (14 direct + 2 child FORCE RLS, `record_tenant_id=current_setting('app.tenant_id',true)::uuid`, NULL→excluded fail-closed) + 0141 workspace-aware + 0187 re-assert; role ragbot_app 0186 (NOSUPERUSER NOBYPASSRLS NOLOGIN DML-only); SET LOCAL session_with_tenant (engine.py:103-164, 142-143, fail-secure 129-135), RLS hook session.py:188-203 OPT-IN. **Leak POSSIBLE if bare SQL forgets WHERE** (RLS 2nd line off); code-level filters (semantic_cache 420/480, pgvector_store session_with_tenant) 1st line LIVE. Fix = ops DATABASE_URL_APP (no code).

### 5. CONVERSATION STATE (jsonb_conversation_state.py)
load_state (80-117) **conversation_id None→{} (85-86)**, TTL guard (96-98 24h), errors→{}. save_state (119-147) **None→return (125-126 no-op)**, sanitize (127), UPDATE action_state JSONB (131-137). _sanitize (149-168) ACTION_STATE_ALLOWED_TOP_KEYS {intent,slots_filled,service_locked}, cap 5. detect_drift (170-240) service (196-218)/price (220-238) lock vs answer, __drift_severity warn default.

### 6. ACTION/BOOKING
slot_extractor.py:57-268 extract (69-163) dynamic Pydantic owner slot_schema, LLM call_with_schema (137-146), Haiku. resolve_action_conversation_id (_action_conversation.py:24-56) action_config.enabled+conv_repo→get_or_create else None. **SSE wired chat_stream.py:287-300 (FIX)**. _render_captured_slots (query_graph.py:814-839) `key="val"; missing:x,y` DATA only.

### §II-G CACHE VERDICT ✅ tenant-scoped + NULL gate + multi-turn skip + corpus/bot version invalidation; no leak at cache layer.
### §II-G HARDCODE: conversation_state.py:276-277 price 10K-50M VND spa; TTL 24h magic; slot_extractor "bắt buộc"/"tùy chọn" VN.

## ════════ PART II-H — DI · CONFIG · WORKERS · OBSERVABILITY (full) ════════

### 1. DI (`bootstrap.py:161-788`) Port→Registry→Null 5-layer
Infra (168-192). api_key_pool DBBackedApiKeyPoolFactory (199-208). Adapters config-driven get_boot_config: vector_store (238-247 pgvector/null), lexical_retrieval Factory (255-263 null/pg_textsearch per-call), embedder Singleton (275-283 env/litellm registry litellm/jina/zeroentropy/bkai_vn), reranker (345-356 jina/jina-reranker-v3), entity_extractor (361-369 null), metadata_filter (377-389 null), **guardrail (324-332 HARDCODE provider="local" ⚠️DI-001)**, crag_grader (396-401), ocr (287), pii (408-411 null). Services: llm DynamicLiteLLMRouter (523-529), token_ledger (517-521), hyde_generator (538-541 after llm), hallu_verifier (552-555), conversation_state (594-612 null/jsonb), system_config_service (288-292 Redis 5min), guardrail_rule_loader (306-315). Observability: invocation_logger (473-475), pipeline_audit (477 OFF), metrics_port (492). DI compliance ✅ 47 ports ~12 registries Null ubiquitous; ⚠️ guardrail hardcode.

### 2. CONFIG 7-tier (xem §8 table). bootstrap_config._ALLOWED_KEYS (46-251) get_boot_config (291-354) psycopg2 30s cache. **Bug#7c 78 keys missing whitelist FIXED (173-251)**. pipeline_config parity test_chat/_pipeline_config.py + chat_worker/pipeline_config.py **Bug#7 38 keys FIXED**; **NEW multi_query_complexity_min 4-site (2026-06-18)**. resolve_bot_limit threshold_overrides>plan_limits>system_default.

### 3. GRAPH ASSEMBLY (graph_assembly.py): build_graph_di_kwargs (92-122 REQUIRED 6 fail-loud, optional→None), build_chat_initial_state (136-200 25 keys, raw_user_message preserve 173-176). build_graph (query_graph.py:1037+) nodes guard_input→understand→router→retrieve→rerank→grade→reflect→generate→guard_output→persist conditional edges.

### 4. WORKERS/STREAMS: chat_worker/pipeline.py handle_chat_received (91-106) bind+body+clear, callbacks TokenUsageDb(db)/Redis(post_commit)/QuotaNotify. document_worker (83-107). redis_streams_bus.py XREADGROUP at-least-once + transactional inbox event_inbox (_INBOX_MARK/SEEN 51-58, _inbox_seen 169-191 fail-OPEN, _mark_processed 193-199), inbox_tx atomic, fairness sems bot:channel cap5/workspace cap10 (106-107), NOGROUP auto-create, DLQ. outbox_publisher transactional outbox.

### 5. OBSERVABILITY: request_logs (models_monitoring.py:75-155 29 fields), request_steps (160-194) **33 step_name** (guard_input/understand_query/condense_question/router/router_select_model/query_complexity/adaptive_decompose/decompose/rewrite/rewrite_retry/multi_query_fanout/cache_check/semantic_cache_check/retrieve/graph_retrieve/multistage_retrieval/retrieve_fallback/rrf_fuse/rerank/filter_min_score/mmr_dedup/neighbor_expand/litm_order/grade/grounding_check/reflect/prompt_build/prompt_compression/generate/critique_parse/guard_output/citations_extract/persist), model_invocations (every LLM/embed/rerank, feature_name), invocation_logger (102-150 async ctx), audit_log hash chain (alembic 010g), StepTracker batch opt. Metrics citation_validation_fail_total/grounding_fail_total/cliff_drop_total/cost_usd_total/tokens_used_total/chat_worker_queue_depth/document_ingest_duration_seconds.

### §II-H ISSUES: DI-001 guardrail hardcode; CONFIG-003 pii frozen; broad-except 3 unjustified+248 noqa; version-ref ~0 (4 dead comments). Config-flip debug: bootstrap_config 30s / system_config 5min Redis / per-bot column immediate.

---

*PART I (§0-§9) = navigable. PART II (II-A→II-H) = detail nén. PART III bên dưới = báo cáo ĐẦY ĐỦ verbatim (exhaustive prose) cho debug sâu nhất.*

---
---

# PART III — EXHAUSTIVE VERBATIM REPORTS (đầy đủ nhất, từng subsystem)

## ███████ III-1. HTTP ENTRY · AUTH · 4-KEY · REQUEST LIFECYCLE ███████

### I. FASTAPI APP BOOTSTRAP
**1.1 Entry Point**: `src/ragbot/main.py:14-33` — `main()` installs uvloop + uvicorn.run(), points to `ragbot.interfaces.http.app:app` (factory pattern).
**1.2 App Factory & Lifespan**: `interfaces/http/app.py:169-384` (lifespan async context manager), `:387-595` (create_app factory). Lifespan bootstrap (175-279): get_settings() (175); `_check_required_provider_keys(settings)` fail-loud on UAT/staging/prod when OPENAI_API_KEY or RERANKER keys missing (123-166); Container() instantiated + app.state.container (188-189); app.state.settings (190); OTel+Sentry init (193-195); Bus Redis Streams eager-init best-effort (198-206); LiteLLM routing refresh best-effort (208-213); parallel bootstrap cache-warming asyncio.gather (274-279): model_resolver bootstrap, bot_registry_service bootstrap, JwtTokenService bootstrap + owner-token ensure, GuardrailRuleLoader bootstrap; embedded worker tasks spawned (343-353) if APP_EMBED_WORKERS_ENABLED=true; shutdown drains (360-383): bus.close() → cache.close() → db_engine.dispose().

**1.3 Middleware Stack (insertion = reverse execution order). REQUEST FLOW first→last inbound:**
1. **IpRateLimitMiddleware** (540-547, added LAST = outermost): source-IP per-minute cap DEFAULT_RATE_LIMIT_PER_IP_PER_MIN=300; trusted proxy + IP allowlist; fail-CLOSED on Redis outage.
2. **SourceRateLimitMiddleware** (457-460): scope (record_tenant_id, source_tag) on /documents/ingest + /sync only; per-source-tag burst; degrade-OPEN on Redis error.
3. **BotRateLimitMiddleware** (444-447): scope 4-key (record_tenant_id, workspace_id, bot_id, channel_type); DEFAULT_RL_BOT_PER_MIN=60 (ingest 10); X-RateLimit-Bot-* headers; bypass when bypass_rate_limit on bot config (Redis cache).
4. **SlidingRateLimitMiddleware** (429-434): per-token L2; fail-mode tunable; limiter per-request from container.
5. **SecurityHeadersMiddleware** (416-419): HSTS when DEFAULT_SECURITY_HEADERS_HSTS_ENABLED=True; wires after GZip.
6. **GZipMiddleware** (410): minimum_size GZIP_MINIMUM_SIZE.
7. **TraceContextMiddleware** (409, `middlewares/trace_context.py:20-31`): reads X-Trace-Id or generates UUID; sanitizes `[A-Za-z0-9_-]{1,128}`; binds request.state.trace_id + structured logs; echoes X-Trace-Id response header.
8. **TenantContextMiddleware** (408, `middlewares/tenant_context.py:86-431`): PUBLIC_PATHS bypass frozenset (47-62): /health, /health/models, /metrics, /favicon.ico, honeypot. **Service JWT HS256 path (111-206)**: JwtTokenService.verify_token(token, redis) via app.state.container; extract claims record_tenant_id, tenant_id (legacy int), sub, role, rl_val, rl_win; resolve record_tenant_id UUID (explicit claim → fallback upstream INT lookup); bot bypass-RL lookup `ragbot:bot:{tenant}:{workspace}:{bot}:{channel}` (182-206, workspace source chain request.state → JSON body → str(record_tenant_id)); Layer 1 per-tenant TenantRateLimiter.check tenant_bypass (212-282); Layer 1.5 per-service-token _check_rate_limit (309-324); Layer 2 per-user connect_id async gather system_config + _check_rate_limit (328-373); bind request.state {record_tenant_id, user_id, bot_id=None, role, rl_bypass}. **User JWT RS256 path (394-431)**: container.jwt_verifier().verify(token); extract record_tenant_id, bot_id, user_id, role; bind request.state. `enforce_tenant_match(request, body_tenant_id)` (481-500): JWT record_tenant_id must equal body except owner/super_admin.
9. **SchemaVersionMiddleware** (407, `middlewares/schema_version.py:52-96`): read X-Schema-Version (or DEFAULT_SCHEMA_VERSION=1); validate int ∈ SUPPORTED_SCHEMA_VERSIONS; 400 on invalid (echoes trace_id).
10. **CORSPerTenantMiddleware** (470-474, `middlewares/cors_per_tenant.py:100+`): per-tenant allowed_origins via TenantConfigCache; pre-auth paths (/health, /metrics, /static, /demo-ragbot) → env CORS; wildcard `https://*.example.com` matches subdomains.
11. **BodySizeLimitMiddleware** (482, `middlewares/body_size.py:28-83`): reject before auth/logging; per-path /api/ragbot/test/chat + /api/ragbot/chat DEFAULT_MAX_BODY_CHAT_BYTES, /api/ragbot/documents + /api/ragbot/sync DEFAULT_MAX_BODY_INGEST_BYTES (16MB), default DEFAULT_MAX_BODY_DEFAULT_BYTES (10MB); reject chunked transfer w/o Content-Length (411).
12. **AntiAbuseMiddleware** (510-517, optional): UA denylist DEFAULT_UA_DENYLIST_PATTERNS; programmatic API-key hash allowlist; trusted proxy + IP allowlist; settings.app.anti_abuse_enabled.
13. **LoggingMiddleware** (406, `middlewares/logging_mw.py:17-38`): monotonic duration; Prometheus http_requests_total{method,route,status}; log http.request event.

**1.4 DI Container Assembly** (`bootstrap.py:161-788`) — full provider list in PART III-8. Key infra Singletons: db_engine (168-170, RLS wrapper SET LOCAL app.tenant_id GUC, no-op under superuser DSN); session_factory create_rls_session_factory (174-176); uow_factory (177); redis_client (179-183); redis_streams_client (189-192, 5s socket_timeout for XREADGROUP); api_key_pool_factory DBBackedApiKeyPoolFactory (199-208, provider-agnostic via _PROVIDER_CODE).

### II. CHAT ENTRY POINTS
**2.1 POST /chat (queued 202)** — `routes/chat.py:40-93`: Step1 identity (47-68) JWT record_tenant_id from request.state (53), resolve_workspace_id fallback str(tenant) (58-60), 4-key BotRegistryService.lookup (64-66) 404 if None; Step2 AnswerQuestionCommand 4-key + trace_id (70-86); Step3 enqueue Redis Stream → chat_worker (87); Step4 return 202 ChatAcceptedResponse {job_id, status="queued", status_url, trace_id} (88-93). NOTE conversation_id=None in command (77) is OK: use-case `answer_question.py:83` does get_or_create + payload conversation_id=str(updated.id) (110).
**2.2 POST /chat/stream (SSE 200)** — `routes/chat_stream.py:87-470`: Step1 tenant check 403 (96-98); Step2 streaming flag streaming_response_enabled OR streaming_enabled (108-126); Step3 workspace+4-key resolution → bot_cfg (129-144); Step4 request_log_repo.create_request_log (189-207); Step5 StepTracker init (209-214); Step6 build_graph_di_kwargs(container) + get_graph (238-258); Step7 oos_template 7-tier + sysprompt_assembler (260-281); Step8 asyncio.Queue sink DEFAULT_SSE_SINK_MAXSIZE + resolve_action_conversation_id (283-300); Step9 build_chat_initial_state (301-323); Step10 state["_stream_sink"]=sink (323); Step11 graph.ainvoke timeout-guard (345-376); Step12 StreamingResponse(stream_real_llm) (449-470); Step13 finalize_request_log + _save_history (379-406).
**2.3 POST /test/chat (demo inline)** — `routes/test_chat/chat_routes.py:68-220`: no-auth fallback _PLATFORM_TENANT_FALLBACK_UUID; bypass_cache flag (268-269); workspace explicit find_by_4key OR find_by_3key_unique omitted (98-109); inline graph.ainvoke; optional debug payload.
**2.4 POST /test/chat-async** — `routes/chat_async.py:144-250`: enqueue CHAT_REQUEST_STREAM; chat_async_worker consumes; result Redis hash CHAT_RESULT_HASH_PREFIX:{job_id}; caller polls GET until status≠queued.

### III. 4-KEY IDENTITY RESOLUTION
**Where each key enters**: (1) record_tenant_id UUID — JWT claim "record_tenant_id" preferred OR legacy "tenant_id" int; bind tenant_context.py:131-144 (svc) / 404-409 (user); request.state, NEVER body; validate 146-165 (401 if missing non-owner). (2) workspace_id slug — request body optional; fallback str(record_tenant_id) (workspace_id_validator.py:99-106); validate strict ASCII `[a-z0-9-]{1,36}` (54-72). (3) bot_id slug — request body REQUIRED; length 1-128 (MAX_BOT_ID_LENGTH). (4) channel_type — request body REQUIRED, defaults "web" in test_chat; length 1-128.
**Resolve boundary BotRegistryService.lookup** (`services/bot_registry_service.py:103-168`): cache key shape `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}` (e.g. ragbot:bot:550e8400-...:spa-tenant:support:web); check Redis cache (133) via _read_cache; validate tenant match at hit (184) evict poisoned; single-flight AsyncSingleFlight on miss (141-168, first caller locks+queries, waiters re-check after back-fill, timeout fallback independent fetch); fetch bot_repo.find_by_4key (149); back-fill cache TTL DEFAULT_BOT_CONFIG_TTL_S~3600; return BotConfig DTO. Stampede metric ragbot_cache_stampede_avoided_total{cache=bot_registry}.
**Divergence**: /api/ragbot/chat = lookup only + enqueue; /chat/stream = lookup + inline ainvoke + SSE; /test/chat = Path A find_by_4key (explicit ws) OR Path B find_by_3key_unique (omitted ws) + inline; /test/chat-async = enqueue CHAT_REQUEST_STREAM.

### IV. RATE LIMITING & BYPASS
Service JWT path (168-306): extract _req_bot_id/_req_channel/_req_workspace_id from body (175-176, ws chain request.state→body→str(tenant)); cache lookup `ragbot:bot:...` → _bot_data → bypass_rate_limit → _bot_bypass (203); Layer1 tenant_cfg.bypass_rate_limit → limiter.check(record_tenant_id, tenant_bypass, bot_bypass, tenant_limit, system_limit), bypass propagate L1.5+L2 skip (286); Layer1.5 skip if _bypass_rl (309); Layer2 skip if _bypass_rl OR no connect_id (328), per_user_rl_val/win from JWT.
Idempotency POST /chat only: idempotency_key → idempotency_service.check_and_record → replay cached. Loadtest bypass token (test only): JWT loadtest_bypass_token.

### V. REQUEST LIFECYCLE LOGGING
RequestLog create (request_log_repo.create_request_log): request_id UUID, record_tenant_id (JWT), workspace_id slug, connect_id, question_hash SHA256, message_id BIGINT ms, record_bot_id UUID, record_conversation_id UUID|None (action only), channel_type, trace_id, started_at UTC, status="running". Finalize (finalize_request_log): answer_hash SHA256, record_model_id, model_name, refusal_reason, prompt_tokens, completion_tokens, cost_usd Decimal, status success/failed, error_code/message, retrieved_chunks [{chunk_id, rank, score}] refs-only no-PII, citations JSON, finished_at, duration_ms, payload_sha256.
RequestStep StepTracker: __init__(request_id, record_tenant_id, repo, kind="query", metrics, pii_redactor); step(name, model_used, binding_id, metadata) async ctx (monotonic duration, tokens, cost, metadata); on exit persist RequestStepModel (request_id, step_name, step_order, model_used, input/output_tokens, cost_usd, duration_ms, status, error, metadata); optional PII redaction Phase D2 if plan_limits.pii_redaction_universal; Prometheus step_duration_seconds{step_name}.
Trace propagation: X-Trace-Id header (or gen) → sanitize `[A-Za-z0-9_-]{1,128}` → request.state.trace_id → bind_request_context structlog → X-Trace-Id response + request_logs.trace_id + error responses.

### §III-1 OBSERVED ISSUES (detailed)
- 7.1 4-key cache bug fixed mega-sprint G7 (tenant_context.py:195-199) — was 3-key shape, bypass_rate_limit always missed; now `ragbot:bot:{tenant}:{workspace}:{bot}:{channel}`.
- 7.2 workspace divergence: production enforces explicit ws or str(tenant) fallback; demo allows ambiguous 3-key (test_chat comment 88-92).
- 7.3 service-JWT (HS256, /admin/tokens, claims sub/role/record_tenant_id/rl_val/rl_win) vs user-JWT (RS256, external, claims sub/bot_id/record_tenant_id/role); user JWT lacks rl_val/win (L1.5 skip) + tenant_bypass; missing record_tenant_id fails 410-415.
- 7.4 idempotency /chat only (chat.py), NOT /chat/stream (comment 17 "out of scope"); 2 identical SSE parallel → both execute.
- 7.5 token usage hooks (bootstrap.py:712-729): stage 'db' TokenUsageDbHook UPDATE bots.tokens_used atomic with graph (hook db fail → graph rollback, no per-hook isolation); stage 'post_commit' TokenUsageRedisHook INCR + QuotaThresholdNotifyHook webhook.
- 7.6 history load best-effort (chat_stream.py:154-176): SQLAlchemyError → empty list, logger.exception, no metric/alert.
- 7.7 message_id = int(time.time()*1000) (147): 2 concurrent same-ms collide, no unique constraint → silent dup insert; fix time_ns()/UUID.
- 7.8 bypass_cache only test routes (test_chat:268), absent production; adding needs RBAC gate.

---
## ███████ III-2. QUERY UNDERSTANDING · CONDENSE · REWRITE · INTENT · ROUTING ███████

### 1. understand_query NODE (merge condense+router) — `orchestration/nodes/understand.py`
**1.1 Idempotency & Cache (55-107)**: Line 76 guard `_understand_skipped_by_parallel` — cache_check_and_understand_parallel may run first, skip LLM if hit. Line 82-107 Redis cache gate memoize repeat queries within understand_query.cache_ttl_s (DEFAULT_UNDERSTAND_QUERY_CACHE_TTL_S=600s). Line 106 cache keys {intent, intent_confidence, query, original_query} filtered whitelist. Cache only seeds when has_meaningful_history=false (history changes prompt body).
**1.2 Layer 1 Heuristic Intent Classify FAST PATH (109-142)** → `services/heuristic_intent_classifier.py:109-147`. Line 113-125 gate heuristic_intent_enabled default TRUE. Line 117 _classify_heuristic(state["query"]) → HeuristicResult(intent, confidence, pattern). Line 118-124 threshold skip LLM if confidence ≥ heuristic_intent_confidence_threshold (0.85). Line 125-142 return early. Pattern registry (62-106): INTENT_GREETING `^(xin chào|hi|hello|chào em|chào bạn|chào shop|hey|xin chao)\b` conf 0.90; INTENT_CHITCHAT `^(cảm ơn|cám ơn|thanks|thank you|ok\b|...)\b` 0.90; INTENT_AGGREGATION `(có mấy|bao nhiêu|liệt kê|tất cả|toàn bộ|kể tên|các loại|mấy loại|bao gồm những gì|gồm những gì)` 0.85; INTENT_MULTI_HOP `(tại sao|vì sao|giải thích|nguyên nhân|lý do|how come|why)` 0.85; INTENT_COMPARISON `(so sánh|khác nhau|khác gì|vs\b|versus|difference between|hơn hay kém|tốt hơn|nên chọn)` 0.85. Fallback no match → intent=None conf 0.0 → LLM. HALLU=0 sacred: heuristic NEVER fires domain-specific.
**1.3 Layer 2 History Condense (157-176)**: Line 158-163 gate meaningful if len(history)>DEFAULT_CONDENSE_MIN_HISTORY_TURNS(3) AND sum chars ≥ DEFAULT_CONDENSE_MIN_HISTORY_CHARS(150). Line 165-174 build condensed prompt limit condense_history_limit(5). Line 176 fallback `<question>{query}</question>`.
**1.4 Layer 2.5 Bot Context (178-185)**: inject bot_system_prompt into `<bot_context>` preview DEFAULT_UNDERSTAND_BOT_CONTEXT_PREVIEW_CHARS=500.
**1.5 Layer 3 LLM Understand STRUCTURED (186-299)**: Line 191-196 gate structured_output_enabled ∧ understand_use_structured_output. Line 200-206 _invoke_structured_llm_node(UnderstandOutput) → parsed or None. Parse (217-238): condensed_query strip check ≠ orig (217); intent literal Pydantic enum (218); confidence clamp [0,1] default 0.5 (224-230). State keys (231-238): intent ← parsed.intent; intent_source ← "llm"; intent_confidence; query ← condensed (if ≠ orig); original_query ← state["query"] (preserve raw for routing). UnderstandOutput schema (llm_schemas.py:51-93): condensed_query str 1-100ch required; intent Literal[factoid, comparison, multi_hop, aggregation, out_of_scope, greeting, feedback, chitchat, vu_vo]; confidence float 0.5 [0,1].
**1.6 Caching (258-281)**: hit when _uq_cache + _uq_bot_id + _uq_query + not meaningful_history; TTL _get_boot_config understand_query.cache_ttl_s.
**1.7 Fallback (301-311)**: catch InvariantViolation, asyncio.TimeoutError, OSError, RuntimeError, ValueError, KeyError → {intent: DEFAULT_INTENT_FALLBACK="factoid", intent_confidence: 0.5}.

### 2. QUERY COMPLEXITY (`orchestration/nodes/query_complexity.py:96-213`)
Called from query_complexity_node (query_graph.py:3505-3518). Input query, optional config_getter; Output (label, score). Structural-ref early exit (136-142): gate exactly 1 struct-ref AND ≤1 comma AND no conjunction AND ≤ cap chars; pattern DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_REF_PATTERN `(Chương|Mục|Phần|Điều)\s*\.?\s*\d+`, max DEFAULT_QUERY_COMPLEXITY_STRUCTURAL_MAX_CHARS=80; return ("simple", 0.0). Scoring signals additive all-configurable: comma `query_complexity.weight_comma` 0.5 max(0,count-1)×w; conjunction `weight_conjunction` 0.4 (word-boundary padded match)×w; numbers `weight_numbers` 0.3 (digit-token count)×w; question `weight_question` 0.6 max(0,count-1)×w; length `length_normalizer` 20.0 word_count/normalizer. Conjunction list (118-129) `query_complexity.conjunctions` default JSON `["và","hoặc","cũng như","or","and"]` word-boundary `" {token} "`. Threshold `query_complexity.complexity_threshold` DEFAULT_QUERY_COMPLEXITY_THRESHOLD=1.2 → "complex". has_aggregation_keyword(query, lang) (216-237): per-lang DEFAULT_AGGREGATION_KEYWORDS_BY_LANG (constants/_24:54-90) VI "tất cả/liệt kê/bao nhiêu/có bao nhiêu/toàn bộ/so sánh/tổng cộng/tổng quan/đắt nhất/rẻ nhất/mắc nhất/cao nhất/thấp nhất/nhiều nhất/ít nhất"; EN "all/list/list all/how many/compare/every/overview/total/most expensive/cheapest/highest/lowest/maximum/minimum"; JA empty; non-VI → empty built-in. Used retrieve.py:712-719 promote top_k.

### 3. QUERY DECOMPOSITION (`orchestration/nodes/query_decomposer.py:132-191`)
Gate decomposer.enabled TRUE; input query + llm_invoker; output list[str]. Config: decomposer.model DEFAULT_DECOMPOSER_MODEL (gpt-4.1-mini, Haiku banned 2026-05-12); decomposer.max_tokens; decomposer.max_sub_queries. Domain-neutral system prompt (54-78): KHÔNG mention domain/industry/jurisdiction/brand; split multi-entity, preserve language, aggressive split; output JSON `{"sub_queries":["q1","q2"]}`. Parse (111-129) extract sub_queries, cap, fallback [query]. Contract never-raise. Failure (172-191) log decomposer_llm_call_failed.

### 4. REWRITE + MULTI-QUERY EXPANSION (`query_graph.py:2682-2723`)
Parallel vs sequential (2691-2696): gate pipeline_parallel_rewrite_mq_enabled default OFF; OFF → pure rewrite legacy; ON → rewrite + MQ concurrent. Decompose precedence (2705-2710): sub_queries ≥2 → skip MQ, fanout_bypassed=true. MQ gate (retrieve.py:1189-1205): skip when decompose fired (sub_queries≥2) OR _mq_queries preset OR intent chitchat (multi_query_skip_chitchat_intent) OR query<min tokens (multi_query_min_tokens 5). Config multi_query_enabled TRUE, multi_query_enabled_by_intent dict. Complexity gate _mq_cx_min (query_graph.py:2443) only expand if complexity_score ≥ threshold (default 0.0). N variants multi_query_n_variants 3. Fanout parallel embed + RRF.

### 5. ROUTING STATS vs VECTOR (`orchestration/nodes/retrieve.py:176-273`)
Sequence of VETO checks: (1) Range parse (205) `_parse_range_query(_raw_query)` _raw_query=original_query or query → RangeFilter|None. (2) Code/Spec (216-220) gate stats_code_lookup_enabled TRUE, only if range None, `_parse_code_query` → keyword. (3) List/Category (226-227) only if range None, `_parse_list_query` → keyword. (4) Superlative kill-switch (231-239) op∈(max,min) AND stats_superlative_enabled=false → None → vector. (5) Structural-ref guard (240-269) article/clause anchor (Điều/Khoản) → skip stats; metadata_filter_strategy.extract() OR fallback `(?i)\b(điều|khoản|điểm|chương|mục|tiết|article|section|clause|chapter|paragraph|art\.?|sec\.?)\s*\.?\s*\d+`. (6) Confidence floor (270-272) <range_query_min_confidence(0.7) → vector. (7) Race mode (284-521) stats_index_race_enabled → fire stats+vector concurrent, stats preferred, timeout stats_race_timeout_s 3s. (8) Sequential (524-573) stats first fallback vector. Stats ops (query_graph.py:2804-2832): keyword→query_by_name_keyword(keyword, synonyms, limit) ALL rows; max/min→top_by_price; count/list/filter→query_by_price_range.

### 6. query_range_parser HARDCODE & ROUTING (`shared/query_range_parser.py`) — 🚨 BUG-1/BUG-2 ROOT
RangeFilter dataclass (84-102): price_min/max int|None VND; price_column "primary"/"secondary"/"any"; operation count|list|filter|max|min|keyword; confidence (ignore<RANGE_QUERY_MIN_CONFIDENCE=0.7); keyword str|None.
parse_range_query (245-334) tried in order: (1) range "từ X đến Y"/"X-Y" `_RANGE_FROM_TO_RE` (260-275) conf 0.9; (2) fuzzy "khoảng X" `_FUZZY_RE` (277-290) conf 0.75 centre±10%; (3) below/max tokens (158-168) `duoi|it hon|nho hon|thap hon|khong qua|toi da|max|<|<=` (292-303) conf 0.85; (4) above/min tokens (171-180) `tren|hon|lon hon|cao hon|tu|min|>|>=` (305-316) conf 0.85; (5) superlative MAX (187-198) `dat nhat|mac nhat|cao nhat|cao cap nhat|dat tien nhat|dat gia nhat|most expensive|highest price|priciest|dearest` / MIN (199-210) `re nhat|thap nhat|re tien nhat|re gia nhat|phai chang nhat|binh dan nhat|cheapest|lowest price|least expensive|most affordable` (318-332) conf SUPERLATIVE_QUERY_CONFIDENCE 0.8.
Operation signals (138-146): _COUNT_SIGNALS (109-117) `có bao nhiêu|bao nhieu|dem|đếm|so luong|số lượng|count`; _LIST_SIGNALS (119-135) `liet ke|liệt kê|danh sach|danh sách|toan bo|toàn bộ|tat ca|tất cả|nhung gi|những gì|nhung cai|những cái|co nhung|có những|list`.
Money parse (63-76) delegates `number_format.parse_money_vn(text, min_value=0)`: "2tr"→2M, "500k"→500k, "1.5 triệu", "700,000". Diacritic fold (38-60) `_ascii_fold` đ→d/ư→u/ơ→o pattern on folded, extract money from original.
**🚨 parse_list_query (359-400)**: preconditions (373-384) check _LIST_SIGNALS / count "bao nhieu|may loai|may cai|dem|so luong" / category "tu van ve|dich vu ve|co dich vu", fallback None if none. **Line 374-377 `folded=_ascii_fold(query); if "gia bao nhieu" in folded or "bao nhieu tien" in folded: return None`** ← BUG-1 price-factoid excluded → vector. Keyword extraction (388-400): strip _LIST_STRIP_PHRASES (longest-first) from ORIGINAL preserve diacritics, word-boundary `\b{phrase}\b`. _LIST_STRIP_PHRASES (339-356) ~40: count/list/existence/service/domain-filler/generic patterns + connective fillers (349-354) "về/ve/vào/vao/không/khong/có/co/ạ/à/ra/mình/minh" LEFT IN → pollute ILIKE. Min len 2 (395). Return RangeFilter(operation="keyword", keyword, confidence 0.8).
parse_code_query (410-445): `_CODE_QUERY_RE = DEFAULT_CODE_QUERY_PATTERN = [A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)+` overridable system_config code_query_pattern; min 2 chars; must have ≥1 letter (exclude 09/2020 date, phone); conf 0.8. Bare-number guard (481-482) _MIN_BARE_PRICE_VND=1000 no-unit + <1000 → reject doc/article number (Thông tư fold collision 2026-06-05). Date-tail guard (471-472) `_DATE_OR_DOCNUM_TAIL_RE = \s*/\s*\d` → reject. Summary patterns (491-503) SUMMARY_QUERY_PATTERNS_VI (constants/_21:156-165) `tóm tắt|tổng quan|tổng cộng|tất cả|toàn bộ|overview|summarize|summarise` → doc_repo.fetch_summaries_by_bot synthetic chunks (retrieve.py:579-616).

### 7. INTENT TAXONOMY & STATE KEYS
Intent values (constants): factoid (default), comparison, aggregation, multi_hop, out_of_scope, greeting, chitchat, synthesis. UnderstandOutput allowed (llm_schemas.py:73-83): factoid, comparison, multi_hop, aggregation, out_of_scope, greeting, feedback, chitchat, vu_vo. State keys (understand.py:231-238): intent, intent_confidence, intent_source, query (condensed), original_query (raw preserve), _uq_cache_hit.

### 8. ROUTING DECISION TREE
_complexity_route (query_graph.py:3344-3348): complexity_label=="complex" → adaptive_decompose else _router_route. _router_route (3350-3391): intent==MULTI_HOP + decompose_enabled + query≥min_tokens + conf≥gate → decompose; intent in skip_rewrite_intents (greeting/chitchat/out_of_scope) → retrieve; else rewrite.

### §III-2 HARDCODED VN HEURISTICS MASTER TABLE
| Phrase/Pattern | file:line | Configurable? | Router impact |
|---|---|---|---|
| greeting/chitchat/aggregation/multi_hop/comparison regex | heuristic_intent_classifier.py:66-104 | NO (gate only) | heuristic intent skip LLM |
| structural markers Chương/Phần/Mục/Điều | query_complexity.py:55 | NO | simple early-exit |
| conjunctions và/hoặc/or/and | constants JSON | YES query_complexity.conjunctions | ×0.4 score |
| below `duoi|it hon|...` | query_range_parser.py:158-168 | NO constant | price_max |
| above `tren|hon|...` | 171-180 | NO | price_min |
| range/fuzzy regex | 213-225 | NO | min/max |
| superlative MAX/MIN | 187-210 | NO | op max/min |
| count signals | 109-117 | NO | op count |
| list signals | 119-135 | NO | op list |
| **"gia bao nhieu" exclusion** | **376** | **NO** | **list→None→vector (BUG-1)** |
| _LIST_STRIP_PHRASES 40+ | 339-356 | NO | strip before ILIKE |
| summary patterns | constants/_21:156-165 | NO | doc summaries |
| aggregation kw per-lang | constants/_24:53-90 | YES (lang dict) | top_k promote |
| structural-ref fallback | constants/_21:118-121 | YES structural_ref_fallback_pattern | skip stats |
| code regex | constants/_21:88-90 | YES code_query_pattern | keyword lookup |

### §III-2 FAILURE MODES (root-caused)
- **Case A** "dịch vụ X bao nhiêu": "bao nhiêu" count-signal + no "gia bao nhieu" exclusion → list route → ALL X (wrong, wanted one). Fix regex `(?:dịch vụ|sản phẩm) [^ ]+ bao nhiêu` → factoid.
- **Case B** "Điều 12 giá bao nhiêu": fold "thong tu"→"tu"(từ) extract "12" → guard <1000 rejects ✓ (2026-06-05).
- **Case C** "có dịch vụ VỀ da chết": strip removes "có dịch vụ", "về" left in → keyword "về da chết" ILIKE fails → vector fallback.
- **Case D** "gần như rẻ nhất": "re nhat" substring found → op=min (misclassify, no negation guard).
- **Case E** "Điều 34, Khoản 2, và Điều 40": 2 commas+conj → complex → decompose (correct but inefficient).
- **Case F** "Điều 38" simple → MQ fanout fires anyway (multi_query_complexity_min=0.0 default).
**🚨 CRITICAL** "giá \<tên dịch vụ KHÔNG code\>" → no stats route → VECTOR → conflate (BUG-1). Spa service escapes because no code; tire works because parse_code_query catches 205/55R16.

### §III-2 CRITICAL FINDINGS
1. VN-LOCKED routing (non-VN → LLM/empty). 2. PRICE-FACTOID AMBIGUITY ("bao nhiêu" dual count/factoid, exclusion only "gia bao nhieu"). 3. CONFIG OVERRIDABILITY: heuristic patterns + range/superlative tokens CONSTANTS not system_config (only enable/disable + structural fallback configurable). 4. CACHE validity: history breaks reuse. 5. CONNECTOR filler in keywords (về/vào/không left-in → pollute). 6. STATS race no circuit-breaker. 7. SUPERLATIVE null bounds → ORDER BY. 8. STRUCTURAL guard best-effort.

## ███████ III-3. RETRIEVAL DUAL-PATH · RRF · RERANK · GRADE · NEIGHBOR ███████

### 1. retrieve() (`retrieve.py:147-173`) + branch (176-573)
Route triggers in order: (1) STATS-INDEX (176-572) gate stats_index_repo!=None; signals range(205)/code(220)/list(227)/superlative(816-823); structural guard skip (240-269); confidence ≥0.7 (270-272); race mode asyncio.wait _race_vector+_do_stats_lookup winner stats-preferred (406-493), both empty/timeout fallthrough (515-521); sequential _do_stats_lookup (525) return early if linked_chunks (537-565) else fallthrough (566-572). (2) DOC-SUMMARY (574-620) gate doc_repo + _matches_summary_pattern → synthetic chunks from summary_json. (3) SPECULATIVE (622-668) pre-computed embeddings + rewritten ≤ threshold. (4) HYBRID (670-1873) fallthrough.

### 2. PATH A VECTOR/BM25 HYBRID
**Preprocessing (670-800)**: expand_abbreviations (692-697) from vietnamese_abbreviations or bot custom vocab; restore_diacritics opt-in supplementary BM25 (1612-1671); generic vocab expansion _bot_custom_vocab=custom_vocab["synonyms"] enrich_state (734-761); metadata L1 LLM intent _extract_query_intent (777-782) / L2 regex metadata_filter_strategy.extract (816-841) / L3 LLM fallback _L3Extractor (853-895).
**TopK (700-732)**: _topk_by_intent retrieve_top_k_by_intent (705); keyword promotion to aggregation if superlative/list keywords (712-719); _intent_override_topk (720); fallback DEFAULT_TOP_K (728).
**MQ/decompose (1126-1315)**: decompose sub_queries precedence (1127-1131); MQ fanout gate (1146-1205) per-intent _retrieve_intent_mq_enabled (1136-1145), preset _has_preset_mq (1168-1171), mq_model (1240-1242), entity path (1251-1254), mq_expand_query/with_entities (1258-1283), cost track (1211-1238, 1308-1314); single-query fallback (1393-1402).
**Pre-batch embed (1323-1339)**: _batch_embed_enabled if >1 query → _embed_batch_queries; fallback individual (969-975).
**_run_hybrid_for_query (977-1124)**: old port hybrid_search(query_embedding) (985-1024) → vector_store.hybrid_search(HybridQuery(dense_vector, query_text)); new adapter hybrid_search(query_text+query_embedding)+search fallback (1025-1124). Threads record_bot_id/channel_type/corpus_version (998-1000), record_tenant_id RLS (1007-1010), metadata_filter JSONB (1074-1075), embedding_column whitelist (1077-1080), adaptive RRF bm25_weight/vector_weight per-intent _resolve_intent_weights (1063-1073), VN structural pre-filter structural_filter_patterns fallback unfiltered (1093-1098).
**RRF fusion (1341-1391)**: asyncio.gather per-query (1343-1354); per_query_chunks (1355-1367); mq_rrf_merge_chunks rrf_k=DEFAULT_RRF_K=60 (1371) formula Σ 1/(k+rank), dedup chunk_id first-wins; cap [:_retrieve_top_k] (1372).
**Lexical BM25 (1673-1735)**: gate lexical_retrieval!=null (1679-1681); DEFAULT_LEXICAL_TOP_K (1684); CR-enhanced widens tsvector content+chunk_context (1698-1700); RRF merge (1716-1717) cap (1719).
**Finalize**: permission pre-filter (1737-1755 user_groups∩access_groups); parent-child expand (1757-1804); autocut (1806-1817 gap 0.3); superlative enrich (1857-1873); audit hybrid_search_executed + chunks_retrieved (1819-1855).

### 3. PATH B STATS (`query_graph.py:2786-3000` _do_stats_lookup)
Schema document_service_index (alembic 0118): id, record_tenant_id, workspace_id, record_bot_id, record_document_id, record_chunk_id(nullable), entity_name(NOT NULL), entity_category(nullable), price_primary/secondary(nullable), attributes_json(JSONB chunk_index). 3 SQL (stats_index_repository.py): query_by_price_range (165-250) WHERE price_min≤price≤price_max price_column any/primary/secondary ORDER price ASC NULLS LAST; top_by_price (252-318) WHERE price NOT NULL ORDER DESC(max)/ASC(min) LIMIT min(limit, DEFAULT_STATS_SUPERLATIVE_LIMIT=5); query_by_name_keyword (418-494) WHERE `unaccent(entity_name) ILIKE ? OR unaccent(entity_category) ILIKE ?` + **synonyms OR-expand for [keyword,*synonyms] dedup lowercase bound :kw{i} (454-467)**. Synthetic-chunk builder (2867-2965): extract name(2891) checked _is_field_like (2884-2886 max DEFAULT_STATS_ATTR_MAX_CHARS=120/WORDS=12 skip mega-cells), price_primary→secondary (2894-2896), category (2933); line "{name}: {price}" (2919-2921) OR "price: {price}" if not field-like (2923-2926), category "category: {cat}" (2934-2935), attributes skip internal chunk_index/question/variants/col_* (2939-2940); dedup (entity_name,price) (2898-2900); synthetic chunk (2955-2965) content=\n.join rows, chunk_id=DEFAULT_STATS_SYNTHETIC_CHUNK_ID="stats_index_synthetic", score 1.0, source "stats_index". Linked-chunks (2834-2991): attempt1 record_chunk_id FK doc_repo.find_chunks_by_ids (pre-2026-05-26 NULL→empty); attempt2 doc fallback find_chunks_by_document_ids (variant-blob noise). Return {entities, linked_chunks: synthetic+linked, range_filter}.

### 4. LEXICAL BM25 AUX (retrieve.py:1673-1735) — covered above.

### 5. RERANK (`orchestration/nodes/rerank.py:55-488`)
Resolver (reranker_resolver.py:80-323): per-bot binding purpose='rerank' active (56-77) → platform system_config reranker_enabled/model/provider (199-268) → NullReranker (272-273); cache `ragbot:rerank:{bot_id}` TTL DEFAULT_RERANK_CONFIG_TTL_S (110-159). Mode (146-161): empty_input/intent_skip_set/intent_skip/disabled/no_reranker/null_reranker/rerank. Cross-encoder (163-197): mode==rerank → _active_reranker.rerank(query,chunks,top_n,model_override) (171-176), fail-soft → RRF order + webhook (177-195); else inp[:top_n] (197). Per-intent rerank_top_n_by_intent (72). Filter (238-387): CLIFF default _cliff_detect_filter(absolute_floor 0.0, gap_ratio 0.4, min_keep 3) (255-304), compound intents min_keep=len(out) (271-272), metric cliff_drop_total (297-304); THRESHOLD legacy (305-331) min_score per-mode active/bypass. Gate-after-cliff (354-387) only threshold OR rerank_threshold_gate_after_cliff_enabled. Max-to-LLM cap (389-404). Retrieval safety-net (449-482): mode==rerank + _safety_n>0 → union top-N retrieval back, stamp lowest rerank score (468). Return {reranked_chunks, rerank_score_mode}.

### 6. GRADE CRAG (`orchestration/nodes/grade.py:60-567`)
Skip: stats-route bypass (92-111) retrieve_mode.startswith("stats"); high-score skip (113-162) top_score≥crag_skip_retry_above_score. Intent self-correction (166-171). Structured batch grade (186-311) GradeBatchOutput XML chunks, timeout→AMBIGUOUS (211-269). Per-chunk SO (313-398) semaphore gather. No-SO fallback all AMBIGUOUS (402-436). Verdict map yes→RELEVANT/no→IRRELEVANT/partial→AMBIGUOUS (180-184), _remap_grade_for_intent (288-293, 364-373). Adequacy (439-533): all-relevant (456-461) has_relevant min_count+min_fraction → adequate; all-irrelevant (462-507) fallback gate rerank abs crag_min_fallback_score (487-488) / bypass relative top_score×DEFAULT_CRAG_FALLBACK_RELATIVE_RATIO (494-499) → "fallback" adequate (502-503) else False (505); ambiguous (508-533) retry gate exhausted/compound → partial else refuse. Return {graded_chunks (RELEVANT+AMBIGUOUS, IRRELEVANT filtered), retrieval_adequate, _total_graph_iterations}.

### 7. MMR (query_graph.py:3049-3094): per-intent mmr_similarity_threshold_by_intent (aggregation loose preserve rows), DEFAULT_MMR_LAMBDA 0.5, mmr_filter cosine-diversity. NEIGHBOR (neighbor_expand.py:397-452): plan_neighbor_windows DEFAULT_NEIGHBOR_WINDOW_SIZE (148-181) per-doc union; fetch_neighbors_sql per-doc BETWEEN lo,hi RLS-joined semaphore (299-394) fallback [] on error; merge_neighbors_with_seeds dedup chunk_id seeds-first truncate_to_token_budget (184-296).

### §III-3 DATA SHAPES
chunk dict: {chunk_id, document_id, content, text, score (vector cosine/RRF/rerank/CRAG), document_name, chunk_index, metadata, is_neighbor_expanded, _safety_injected, relevance(yes/no/partial/fallback), is_parent_expanded, access_groups}. RangeFilter: {operation, price_min, price_max, price_column, keyword, confidence}. Stats entity: {id, record_document_id, record_chunk_id, entity_name, entity_category, price_primary, price_secondary, attributes_json}. Constants: DEFAULT_TOP_K=20, DEFAULT_RRF_K=60, DEFAULT_RERANK_TOP_N=7, DEFAULT_STATS_INDEX_LIMIT=100, DEFAULT_STATS_SYNTHETIC_CHUNK_ID="stats_index_synthetic", DEFAULT_STATS_ATTR_MAX_CHARS=120/WORDS=12, RANGE_QUERY_MIN_CONFIDENCE=0.7.

### §III-3 OBSERVED ISSUES (detailed)
- **🚨 #1 VECTOR MULTI-SERVICE CONFLATION**: chunking splits doc by line-breaks/windows; CSV/table multi-service row → single chunk has both → embedding sees whole text → on noisy CSV LLM attributes one price to wrong service. Stats avoids (atomic row). Mitigation: stats route for price, synthetic dedup (name,price), grounding HALLU=0 (but grounding warn-only → NOT enforced — BUG-1).
- #2 STATS EMPTY FALLBACK: pre-2026-05-26 record_chunk_id NULL → find_chunks_by_ids([None])=0 → false OOS; synthetic chunk surfaces filtered rows before doc fallback.
- #3 RERANK SILENT DROP: cross-encoder ≠ lexical; safety-net unions top-N back stamped lowest score.
- #4 CHUNK DEDUP across RRF (first-wins) + MMR (per-intent threshold) + neighbor (chunk_id), no global dedup.
- #5 METADATA OVER-RESTRICTION: "Điều N giá" → stats wins (price signal) before structural guard; mitigated by structural-ref guard 240-269.
- #6 STATS SYNTHETIC CHUNK_ID sentinel (else generate drops falsy id).
- #7 MQ PARALLEL COST: fanout ~3-5s even when single-query works; per-intent skip + min token + preset reuse.
- #8 LANGUAGE NORMALIZATION: NFC vs NFD VN diacritics → normalize_vn (pgvector_store.py:355), symmetric content_segmented, fallback diacritic-stripped.
- #9 RACE stats vs vector both-complete → stats preferred (431-444), loser cancelled.
- #10 INTENT CLASSIFIER DRIFT: skip gates depend on intent label, LLM version change risk.

---
## ███████ III-4. GENERATION · SYSPROMPT ASSEMBLY · MODEL · TOKEN BUDGET · CITATIONS ███████

### 1. GENERATE NODE (`orchestration/nodes/generate.py`)
**1.1 Entry (90-108)**: DI contract llm/model_resolver required; _audit/_invoke_llm_node/_invoke_structured_llm_node/_so_usage/_pcfg/_lang/_oos_text/_resolve_xml_wrap_enabled/_resolve_generate_schema/_render_captured_slots/_CITATION_RE closures. **(110-125)** SLA clock _generate_t0; audit generate_started (chunk count + char sum).
**1.2 Refuse short-circuit (242-287)**: flag refuse_short_circuit_enabled (243-246); when graded empty AND not chitchat AND not action-booking → oos_text=_oos_text(state) (269), audit refuse_short_circuit_fired (274-280), return answer_type="no_context" (282-287).
**1.3 Context framing (540-580)**: _resolve_xml_wrap_enabled (540, M14 atomic-chunk vs legacy); loop (542-579) extract chunk_id/text/document_name/chunk_index/metadata (543-549); **🔑 line 551-552 `if not cid: continue` silently skips chunks w/o ID** (no citation, no audit); xml_wrap=True emit `<chunk id="{cid}" type="{ctype}" section="{section}"><content>{text}</content></chunk>` (553-571); xml_wrap=False legacy `<context source="{label}" chunk="{idx}" id="{cid}">{text}</context>` (572-579); join \n\n (580).
**1.4 Prompt msg order (602-616)**: [1] system first `{"role":"system","content":system_prompt}` (603); [2] history appended capped (601), citations stripped `_cite_marker_re.sub("",content)` (606), truncated >MAX_HISTORY_MESSAGE_CHARS (607-608); [3] user last `_user_content = f"<documents>\n{context_str}\n</documents>\n\n<question>{q}</question>"` (or just `<question>` if chitchat 613) (610-616).
**1.5 System prompt + captured-slots (582-593)**: read state.get("bot_system_prompt") (582, pre-assembled upstream) OR _lang(state).prompt_generator (584); **{captured_slots} substitution _render_captured_slots (589-592)** — sacred-#10 binding ONLY, DATA not instruction.
**1.6 Token budget (481-528)**: context-char cap AFTER compression/reorder/token-opt; read generate_context_chars_cap_by_intent[intent] or DEFAULT_GENERATE_CONTEXT_CHARS_CAP (488-510); iterate graded accumulate _running, drop tail when _running+len>cap (514-523); whitelist chunk_ids_allowed (524-528). Output cap compute_output_cap (645-656).
**1.7 LLM call (630-802)**: structured (694-751) gate structured_output_enabled ∧ generate_use_structured_output (630-634) + no streaming sink (640-641), _resolve_generate_schema (700, GenerateOutput multi-fact / GenerateFlatOutput), _invoke_structured_llm_node binding_purpose (701-709), validate cited IDs vs chunk_ids_allowed drop invalid metric citation_validation_fail_total (720-751); free-form (753-802) _invoke_llm_node (755-762), _CITATION_RE.findall(answer) (771) validate (775-778) count invalid (793).
**1.8 Cost-aware routing (676-692)**: _binding_purpose=_resolve_purpose_for_intent(intent) (model_resolver._helpers.py:128-155) factoid→llm_factoid/chitchat→llm_chitchat/OOS→llm_oos/fallback llm_primary; metric llm_resolved_purpose_total.
**1.9 Post-hoc citation (809-821)**: LLM omits + graded → top-scored chunk, citations_source="posthoc_top_chunk".
**1.10 Action drift (873-952)**: action_config.enabled + conversation_state wired (882-885); detect_drift(prior_state, proposed_answer, chunks) severity-map __drift_severity (896-909); warn→audit flag / block→raise GuardrailBlocked (910-932); save state best-effort (941-952).
**1.11 SLA (841-871)**: _generate_elapsed_ms vs generate_p95_sla_ms warn; TTFT _stream_first_token_ms (859-861); record_llm model/tokens/cost (866-871).

### 2. SYSPROMPT ASSEMBLER (`application/services/sysprompt_assembler.py`)
3-tier (18-21): `Final = bot.system_prompt + language_packs[bot.language].sysprompt_default_rules − bot.plan_limits["sysprompt_rules_disabled"]`. Stateless DI (72-82 language_pack_service). assemble (83-126): extract base + locale (102-103); fetch platform rules _fetch_platform_rules (106-114, 131-137); extract disabled _extract_disabled_rules (120-125, 161-191 accepts ["rule_17"]/[17]/["17"]→canonical); strip _strip_rules _RULE_BLOCK_RE (66-69, 194-213); return base+rules (126 concat, no override). Seed alembic 20260611_0204_sysprompt_aprime UPDATE language_packs SET content WHERE prompt_key='sysprompt_default_rules' AND code (A-prime replaced 22-rule 8KB ~2400tok → concise grounding+computation). Per-locale vi/en separate seeds; new locale pure DB seed. opt-out plan_limits["sysprompt_rules_disabled"] (58-59). Graceful degrade port fail → bot.system_prompt unchanged (41-42). Wiring bootstrap.py:584; chat_stream:278; admin_bots:230 (effective-prompt endpoint); test_chat:391; chat_worker pipeline:580. Pin test_sysprompt_assembler_pin.py 5 pass.

### 3. SACRED-#10 VERDICT — NO INJECTION, NO OVERRIDE ✅
6.1 Prompt structural framing only (589-593 {captured_slots} DATA, 603-616 `<documents>`/`<question>` XML framing not instruction). 6.2 Answer verbatim passthrough free-form (755-802 answer=payload["text"] 763) / structured (701-712 answer=parsed.answer). 6.3 Refusal oos_template REPLACES answer not appended (269-287, 928-932 GuardrailBlocked → caller substitutes). 6.4 No hardcoded i18n (584 _lang.prompt_generator from DB; 512-527 _lang reads state["_language_pack_rows"] DB-driven OR get_pack fallback; locale text in language_packs DB + i18n.py fallback static vi/en). 6.5 Evidence docstrings (generate.py:10-12 "MUST NOT inject... read answer verbatim"; 135 action; 296 cascade "only model CHOICE changes"). _resolved_oos_template 7-tier (query_graph.py:685-716): bot column → plan_limits → workspace → tenant → system_config → language_pack → DEFAULT_OOS_ANSWER_TEMPLATE="". Per-rule response_message override (1751-1752).

### 4. MODEL RESOLUTION
resolve_purpose_for_intent (_helpers.py:128-155). resolve_llm (__init__.py:117-153): intent→purpose (127), cached bindings (128), sort rank fallback llm_primary (129-137), select primary iter=0 A/B variant OR fallback iter≥1 (143-151). Cascade (query_graph.py:302-351 cascade_router_helper.py): cascade_routing_enabled read complexity_score tier model update resolved_answer_model (336), graceful degrade (346-351). Silent fallback: missing binding → next tier; resolver gap → warn no raise; cascade disable default False.

### 5. TOKEN BUDGET (`shared/token_budget.py`)
compute_output_cap (63-78) base=system_output_default(≥1) or DEFAULT, extra=bot_extra_output(≥0). Context cap per-intent (generate.py:488-509). History cap (596-601) min(condense_history_limit, DEFAULT_GENERATE_HISTORY_MAX_MSGS); token-opt factoid skip-history (598-599 prompt_token_opt_factoid_skip_history). Sacred zero-default DB NOT NULL DEFAULT 0 CHECK≥0.

### 6. CITATIONS
_CITATION_RE=`\[chunk:([0-9a-f\-]+)\]` (query_graph.py:407). Validation chunk_ids_allowed (generate.py:524-528, 721-751 structured / 771-793 fallback). Post-hoc (809-821). Observability citations_extract step (823-836) n_valid/extracted/source/structured_succeeded/n_invalid; metric citation_validation_fail_total.

### §III-4 OBSERVED ISSUES
7.1 sysprompt bloat 2400 tok (A-prime 0204 reduced; new rules need token audit). 7.2 no API-level prompt-prefix cache hint visible in generate.py (may be at litellm layer). 7.3 multilingual: sysprompt_default_rules seed vi/en only, locale absent → _lang fallback DEFAULT_LANGUAGE="vi" (query_graph.py:527) — non-VI silently use VN text. 7.4 chunk-id drop (551-552) no audit event context_chunk_dropped_no_id. 7.5 OOS empty all-tier (DEFAULT_OOS_ANSWER_TEMPLATE="" constants/_04:37, resolver tier7 empty) → bot returns empty answer (language_packs tier should always non-empty). 7.6 cascade silent degrade (346-351 catch-all → warn, no metric). 7.7 sysprompt_rules_disabled opt-out operator-only no UI.

### §III-4 INTEGRATION
Chat entry → oos_template_resolver.resolve (pipeline.py:559-570) → sysprompt_assembler.assemble (576-583) → build_chat_initial_state (585-604) → graph.invoke. Generate node wired query_graph.py:3274-3284 functools.partial with closures.

## ███████ III-5. GUARDRAILS · GROUNDING · HALLU · REFUSAL ███████

### 1. INPUT GUARDRAIL FLOW
Entry `query_graph.py:1703-1761` guard_input node, catches GuardrailBlocked (1738). check_input (1719) → `local_guardrail.py:796-847`.
Checks: too_short (188-218 static, orchestrate 807-810) min_alpha _resolved_min_alpha (659-671) system_config guardrail_min_alpha_chars default DEFAULT_GUARDRAIL_MIN_ALPHA_CHARS=2 (0=skip), verdict rule_id=too_short severity=block. length_limit (105-117, orchestrate 812) max_len self._max_input_length DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH=4096 (constructor 644), block. DB regex rules (723-761 _run_db_input_regex_rules, orchestrate 817-828): if loader wired fetch ruleset.input_rules (guardrail_rules scope input/both, metadata.classic≠True), rule.pattern.findall; fallback static prompt_injection_patterns (120-142)/pii_vi (145-170)/pii_en (173-185)/sql_injection (221-233). Default patterns `_default_patterns.py:46-200` + seed `alembic 20260516_010f:60-89`: prompt_injection `(ignore previous|disregard|system prompt|you are now|DAN|base64:|decode this)` IGNORECASE block prio10; prompt_injection_classic_* (4) block prio20-23; pii_vi_phone `(0\d{9,10}|\+84\d{9,10})` warn/redact prio50; pii_vi_email warn prio51; pii_vi_cmnd `\b(\d{9}|\d{12})\b` warn prio52; pii_en_ssn `\b\d{3}-\d{2}-\d{4}\b` warn; sql_injection block. Severity block→GuardrailBlocked (845-846); warn→flags (847). Blocked response (1738-1761): _resolved_oos_template (1740); per-hit if block + response_message override (1752); return {guardrail_flags, answer=blocked_answer, answer_type=blocked, answer_reason}; persist guardrail_events (838-843).

### 2. OUTPUT GUARDRAIL FLOW (`guard_output.py:49-516`)
**2.2.1 system_prompt_leak** (local_guardrail.py:298-350, invoked guard_output.py:327-342/local 880-887): skip if OOS refusal Jaccard≥DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD=0.90 (_is_oos_refusal 257-279); hash sysprompt n-grams shingle_size _leak_shingle_size DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE=8 (244-251) sha256; doc-shingle subtraction default ON (260-270, shingle in chunks=legit relay); count matches; block if ≥min_match_count DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT=10 (105-112, refusal≈5, instruction≈13-89, 300-word≈277); per-bot override guardrail_leak_min_match_count. Intent gating skip greeting/chitchat DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS (221-227). Stats route skip retrieve_mode.startswith(stats) + sysprompt_leak_skip_stats_route=True (234-241). Verdict rule_id=system_leak severity=block.
**2.2.2 secret_scanner** (353-365): get_default_compiled("secret_leak"), rule_id=secret_leak block.
**2.2.3 citation/grounding pre-check** (368-414, gate citation_marker_required default False): pass1 marker `_CITATION_MARKER_RE = \[[a-zA-Z0-9_\-]{1,64}\]` (69); pass2 substring _grounding_substring_match (239-254) substring_min; pass3 numeric overlap _extract_numbers (282-288) all 2+ digit tokens in chunk, gate numeric_overlap_enabled. **Verdict rule_id=grounding_fail severity="warn" action=hitl — OBSERVABILITY ONLY, NEVER blocks** (line 410-411).
**2.2.4 LLM grounding judge** (417-553 llm_grounding_check): intent eligibility grounding_intents DEFAULT_GROUNDING_INTENTS (factoid,comparison,aggregation,multi_hop) (89-95); stats/structured skip (103-104); async option (109-152) gate grounding_check_async_enabled + intent in async_intents (factoid) + top_score≥async_top_score_threshold → sync skip, background post-response (489-496). Structured (556-587 _run_structured_judge GroundingVerdictsOutput.verdicts[].verdict SUPPORTED/NOT_SUPPORTED) OR text-parse (590-628 regex "N. SUPPORTED"/"N. NOT_SUPPORTED"). Judge prompt (475-494) "grounding verifier... Reply English only SUPPORTED/NOT_SUPPORTED". **Sentence split max_sentences=5 HARDCODED (413, 451) — tail-claims 6-9 unverified**. Verdict ratio=unsupported/checked > threshold(0.30 from 77) → rule_id=llm_grounding_fail severity=warn (529-552) — **NEVER blocks**. Silent degrade timeout/error → (0,0)→None (514-520, 570-572, 607-609), metric grounding_degraded_total. Parallel mode (272-302, 356) taskA regex-only + taskB standalone judge gather merge additive; serial single check_output (458-473). Blocked (500-516) _oos_template.

### 3. 🚨 GROUNDING / HALLU ENFORCEMENT
**HALLU=0 NOT enforced by guardrail (judge warn-only).** Enforced by: (1) sysprompt anti-fabricate (query_graph.py:6258-6292 verbatim, sacred #2 "LLM trả gì=user thấy nấy" 62-67, test_generate_no_app_injection.py:9-11); (2) retrieval quality (no chunks→chitchat template); (3) multi-stage CRAG (grade 5331-5341 + rewrite_retry, iter cap DEFAULT_MAX_TOTAL_GRAPH_ITERATIONS=8 5209-5217); (4) numeric grounding pre-check literal substring (does NOT verify arithmetic). 4 trap types: Fabricate ✅ citation/substring; Misinterpret ⚠️ judge maybe not deterministic; **Extrapolate(sum) ❌ numeric catches addends NOT sum**; **Conflate(entity-map) ❌ both entities present → grounding pass → BUG-1 NOT caught**. Grounding only flags ratio>threshold; does NOT prevent single-wrong-bundled-with-right (ratio low) NOR numeric conflate within one sentence.

### 4. REFUSAL (7-tier, non-hardcoded)
_resolved_oos_template (query_graph.py:685-716): bot column → plan_limits → workspace → tenant → system_config → language_pack → DEFAULT_OOS_ANSWER_TEMPLATE. Pre-pipeline resolved → state["oos_answer_template_resolved"]. Backward compat _pcfg fallback (715-716). Per-rule response_message (1751-1752). NON-hardcoded no brand/English-only/app-inject.

### §III-5 HARDCODE AUDIT
Thresholds all config-driven EXCEPT: max_sentences=5 (413 function-default no config) ← RISK; _SENTENCE_SPLIT_RE `[.!?]\s+` (70) assumes English (VN decimal "50?" splits). Brand literals NONE (7-tier resolver + per-rule + structlog). Broad-except 5 justified BLE001 (514 judge fail, 670 config read, 836/924 metric, 962 persist). Grounding system prompt English-only (479-483) → VN text-parse risk (structured mitigates Pydantic enum). PII VN phone/CMND VN-specific (145-170, _default_patterns 115-144). Numeric extract European separators (covers VN not CJK).

### §III-5 PER-CHECK FP/FN TABLE
| Check | FP | FN | Mitigation |
|---|---|---|---|
| too_short | Low | None | config |
| length_limit | None | None | — |
| prompt_injection | Low | Med (bypass) | Llama Guard planned |
| pii_vi_phone | Med | Med | override |
| pii_vi_email | High | Low | whitelist |
| citation marker | None | Low | — |
| substring | None | Low | judge fallback |
| **numeric_overlap** | **High** (addends present→pass dù truncated/sum sai) | Low | claim-level verify |
| system_prompt_leak | Med | Med | doc-subtract+OOS+min10 |
| secret_leak | Low | Med (base64) | entropy planned |
| **llm_grounding sentence-cap** | Low | **HIGH** (tail>5 unchecked 🐛-1) | remove cap |
| llm_grounding text-parse | Med | Med | structured |
| **llm_grounding silent-degrade** | N/A | **HIGH** (timeout→pass 🐛-3) | degraded metric |

### §III-5 HALLU=0 VERDICT + GAPS
HALLU maintained via sysprompt primary + retrieval secondary + CRAG. Guardrail observability (warn) not enforcement (block). Sacred #2 preserved. GAPS: 🐛-1 tail-claim (max_sentences=5); 🐛-2 temperature override bypass (multi_query/grounding/decompose direct llm.complete no temp param); 🐛-3 silent grounding degrade (timeout=answer passes unverified). **🚨 Conflate (BUG-1) + Extrapolate NOT caught by grounding → need structured/arithmetic route not threshold-tuning.**

---
## ███████ III-6. INGESTION · PARSING · CHUNKING · ENRICHMENT · EMBEDDING · STATS-INDEX ███████

### 1. ENTRY worker (`interfaces/workers/document_worker.py`)
handle_document_uploaded (83-107) tenant+mode context → _handle_document_uploaded_inner (110-370). **_is_refetchable_url (74-80)** protects Google `edit?gid=` HTML interstitial (audit 2026-05-13) — only http/https refetchable, local:// must have raw_content in DB else fail (351-354). U0 raw content (224-255): reuse documents.raw_content from prior endpoint, log worker_reused_raw_content + bytes; fallback registry parser (276+).

### 2. STAGES (`application/services/document_service/`)
**U1 validate** (ingest_core.py:275-286) `_phase_d_step ingest_validate` emit request_steps {n_bytes, mime_detected, language_in, channel_type, is_reindex}. Workspace resolve resolve_workspace_id RLS GUC (document_worker.py:127-139).
**U2 parse** (ingest_core.py:314-344) registry detect_parser(mime,ext) (`parser/registry.py:81-104` iterate first-match). Registry (38-45): google_sheets (row-per-chunk), excel_openpyxl (row-per-chunk cell structure), pdf (OCR fallback), docx (table-aware), markdown, null. Parser returns (extracted_text, parser_row_chunks) (314-344); row-shaped excel/google_sheets → parser_row_chunks metadata {parser:...} (669-670) bypass smart_chunk preserve 1-row-per-chunk (672-677). Phase D {parser_provider, mime, n_chars_in/out, n_pages}.
**U3 clean** (ingest_stages.py:221-337): CleanBase Tier-0 (258-275) HTML strip+NFC+zero-width+injection blacklist, opt-out cleanbase_tier0_enabled default ON, report {html_tags_stripped, zero_width, injection_matched, nfc_changed, redactions}; legacy _clean_document_text (text_processing.py) hyphenation+header-strip+whitespace+injection sweep; LLM metadata (321-336) metadata_extraction_enabled default OFF → {document_type, key_topics}.
**U4 chunk** (ingest_stages.py:339-800) AdapChunk Layers: L1 whole-doc (356-382) len<whole_doc_threshold + not CSV + topic_signals≤max → single chunk; L2 parent-child (400-445) parent_child_enabled OFF, generate_parent_child_chunks (chunking/__init__.py:109-200) HDT parents no-embed + recursive children; L3 doc profile (446-651) adapchunk_layer3 OFF, analyze_document → DocumentProfile 10 features (611-630); L4 select_strategy (analyze.py + strategies.py) (strategy,conf): hdt|semantic|recursive|table_csv|table_dual_index|parser_preserve|hybrid|proposition; L5 cross-check (chunking/__init__.py:~520 apply_cross_check) adapchunk_l5 ON rules (542-550 "formula_count>5 force hybrid"); VN heading promote (vn_structural.py promote_vn_hierarchical_headings "[Chương I]"→"# Chương I" 426/508/564); orphan merge merge_orphan_chunks NOT tabular (703-714); M25 block histogram _split_into_blocks_with_atomic (730-748). Phase D {strategy_used, n_chunks_out, chunk_size_avg, topic_signals, orphans_merged, blocks_by_type}.
**U5 enrich** (ingest_stages_enrich.py:120-500): NANO PATH#1 legacy CR (201-219) contextual_retrieval_enabled OFF, cr_max_doc_chars 50k, row gate skip tabular (190-199); NANO PATH#4 WA-3 enhanced CR (222-298) cr_enhanced_enabled OFF, _chunk_context_enricher.generate_contexts → document_chunks.chunk_context (WA-3 alembic 010l) NEVER in prompt (QG#10); VN compound segment (300-354) VI_DOMAIN_LANGUAGES gate, segment_vi_compounds asyncio.to_thread timeout vi_compound_segmentation_timeout_s 5s → content_segmented; concurrent CR+seg (310-400) _enrich_and_segment, prompt-cache warm seed 1 sequential then fanout (382-398), partial-failure → original.
**U6 vn_segment** (vi_tokenizer.py segment_vi_compounds, inline U5 _vn_seg_one 348-354) → ctx.segmented_chunks.
**U7 embed_store** (ingest_stages_store.py:120-999): embedding spec (154-158) jina/zeroentropy 1024/matryoshka; embed-text strategy (180-195) embedding_text_strategy_name auto/raw_only/prefix_plus_raw (auto: hdt→raw_only, prose/table/FAQ→prefix_plus_raw); NANO PATH#3 Narrate-then-Embed (227-276) narrate_then_embed_enabled OFF alembic 0230 rewrite TABLE/FORMULA/IMAGE→NL, DEFAULT_NARRATE_TIMEOUT_S fallback raw, metadata document_chunks.metadata_json; passage prefix (278-286) embedding_passage_prefix asymmetric; late chunking (288-388) sliding (long docs late_chunk_embed_sliding window/overlap chars) OR single-prefix late_chunk_embed default ON (299); standard embed (409-443) _embed_in_doc_batches embed_doc_batch_size 50, raise ExternalServiceError on fail; ingestion validation (498-535) ingestion_validation_enabled ON ingestion_min_chunk_chars 20 advisory; chunk identity M21 (537-560) chunk_hash_id_enabled OFF → deterministic_chunk_id UUID5 idempotent UPSERT / ON → time_ordered UUIDv7; M23 content-type histogram (562-595); structured-ref extraction (597-628) structured_ref_extraction_enabled OFF alembic 0231 article_no/chapter_no/section_no JSONB; insert parent-child (652-848) phase1 parents no-embed phase2 children FK / flat (851-950) single INSERT enriched_prefix+raw_chunk+chunk_type+chunk_context+narrate; per-row metadata JSON {chunk_index, total_chunks, document_title, enriched_prefix, chunking_strategy, chunking_confidence, quality_score, contextual_retrieval, structural_path, extracted_metadata, is_full_document, raw_chunk, article_no, chapter_no, narrated_text, block_type}; semantic cache invalidate P24-L1 (954-975); Phase D {n_chunks_embedded/stored, n_null_embedding, embedding_model/dim, is_reindex}.

### 3. FINALIZE + STATS (`ingest_stages_final.py:120-377`)
State flip atomic (145-227): COUNT embedded + COUNT null non-parent; total=0→failed (187-191); null_non_parent>0→failed (193-203); else active (205-206); parent legit NULL (149-153 only count leaf NULLs). **STATS-INDEX (305-342)** if stats_index_repo + rows: delete_by_document (repo 132-163 best-effort 320-327); **parse_table_chunks (document_stats.py:259-320)**: skip prose no-delimiter (283-289); detect header exact-token + no-money (305-307, 127-147); track category single-col non-price (309-314); extract ParsedEntity (316-318) (name, category, price_primary, price_secondary, attributes); parse_money_vn (82-103) "1.499.000"/"1,499,000"/"1tr499"/"499k"/"1M" filter <DEFAULT_PRICE_MIN_VND(50k); header tokens HARDCODE (58-65) `stt/ten/gia/vung/loai/dich vu/service/price/name/category`; aggregate_summary (323-383) {entity_count, price min/max, buckets under_500k/under_1M, categories}; bulk_insert (repo 57-130) chunk_index in attributes_json (112), session_with_tenant RLS (89-90); upsert documents.summary_json (338-342).

### §III-6 STATS-INDEX VERDICT
- **entity_category mostly NULL**: only set if CSV explicit category col OR multi-group structure with category heading; NOT LLM (HALLU=0) → **NOT reliable for self-query** (use entity_name + price). BUG-5.
- **Deduplication NONE in ingest**: 3-8 dupes/service because (a) table_dual_index emits per-row + group header chunks (same service twice); (b) CSV repeated headers per group section → parser detects each "Category" heading, emits same entity again with different category. parse_table_chunks deterministic; dupes from upstream chunking. BUG-5.
- **Self-Query usability**: entity_category unreliable.

### §III-6 ROOT CAUSE CONFLATE (BUG-1 ingest side)
table_dual_index emits BOTH per-row "Service A | 100k" AND group chunk "**Group1**\nService A 100k\nService B 200k". Group chunk embedding = average of multiple prices' semantic → matches unrelated single-service query at mid-confidence with diluted embedding. **Fix: table_csv per-row exclusive, don't emit group chunk.** Plus dedup at stats-repo insertion.

### §III-6 HARDCODE/MULTILINGUAL
document_stats.py:58-65 header tokens VN-biased no-config; DEFAULT_CHUNK_SIZE/OVERLAP/ORPHAN/MAX/WHOLE_DOC_THRESHOLD constants overridable; DEFAULT_PRICE_MIN_VND=50k VND-centric; number_format.parse_money_vn VN regex; VN segment vi-only; HDT Chương/Mục/Điều assume VN; chunking strategies hdt/semantic/recursive language-agnostic. Broad-except narrow ValueError/TypeError + BLE001 best-effort. Incremental re-index (ingest_core.py:612-658) hash-based unchanged/embed/stale diff.

## ███████ III-7. CACHING · MULTI-TENANCY · RLS · CONVERSATION-STATE · ACTION/BOOKING ███████

### EXECUTIVE: RLS LIVE-BUT-REQUIRES-DATABASE_URL_APP. Current .env superuser DSN + RAGBOT_ALLOW_SUPERUSER_RUNTIME=1 + no DATABASE_URL_APP → engine.py:69-81 superuser fallback → **RLS bypassed at runtime** (policies cosmetic). Code-level tenant checks live (1st line). Cache 2-tier tenant-scoped + NULL-tenant write gate.

### 1. SEMANTIC CACHE (`infrastructure/cache/semantic_cache.py`)
Hash fast-path (410-459): `SELECT answer,citations,model_name,cached_at_ts,metadata_json FROM semantic_cache WHERE record_bot_id=:bot AND record_tenant_id=:tenant AND query_hash=:h AND bot_version=:bv AND corpus_version=:cv AND (expires_at IS NULL OR expires_at>now()) ORDER BY created_at DESC LIMIT 1` — record_tenant_id explicit (420), key includes tenant+bot+hash+bot_version(system_prompt+oos_template)+corpus_version. Cosine slow-path (461-527): pgvector `<=>` HNSW WHERE same scope + `1-(query_embedding<=>:emb)>=:threshold` (default 0.97). store() (529-597): **SECURITY GATE record_tenant_id None → skip+warn return early (545-552)** (test_semantic_cache_no_null_tenant_write.py:79-98); INSERT tenant+bot+version+corpus+embedding+TTL (571-596). Stampede 2-tier (191-368 find_similar_with_text): Layer1 Redis SETNX `ragbot:cache:lock:{record_bot_id}:{qhash}` (230-301) winner runs _find_similar_impl + del lock, loser sleeps DEFAULT_SEMANTIC_CACHE_WAIT_RETRY_S recurse max~10; Layer2 asyncio.Lock per (bot,qhash) weakref (303-368). bot_version _compute_bot_cache_version (query_graph.py:870-873) sha256(system_prompt+"|"+oos_template)[:12]. bypass (1777-1780). Multi-turn skip (1787-1792) conversation_history present → skip (correctness>hit-rate).

### 2. EMBED CACHE (embed_cache.py): key `ragbot:embed:{safe_model}:{sha256(query)[:16]}` NOT tenant-scoped (same text=same vector, max reuse), normalize strip+casefold (55-62), Redis errors silent, TTL embed.cache_ttl_s 3600.
### 3. UNDERSTAND-QUERY CACHE (understand_query_cache.py:64): key `ragbot:uq:v{prompt_version}:{record_bot_id}:{sha256(query[:300])[:16]}` BOT-SCOPED, prompt_version namespace, errors→None.

### 4. 🚨 MULTI-TENANCY / RLS
Layer1 GUC+policies: alembic 0069 (14 direct + 2 child via FK, policy `record_tenant_id = current_setting('app.tenant_id',true)::uuid`, child EXISTS subquery, FORCE ROW LEVEL SECURITY 80-81, NULL→excluded fail-closed); 0141 workspace-aware app.workspace_id coalesce; 0187 re-assert. Layer2 role ragbot_app alembic 0186 (NOSUPERUSER NOBYPASSRLS NOLOGIN, DML-only grants SELECT/INSERT/UPDATE/DELETE + USAGE/SELECT/UPDATE sequences, no DDL, ALTER DEFAULT PRIVILEGES, login by ops DATABASE_URL_APP). Layer3 SET LOCAL: engine.py session_with_tenant (103-164) `SET LOCAL app.tenant_id='<uuid>'` (142-143) validate _assert_uuid_str interpolate, app.workspace_id (152-156), statement_timeout (158), raise if unbound (129-135 fail-secure), LOCAL clears on commit (118-120); session.py attach_rls_session_hook (188-203) _after_begin → _set_local_tenant OPT-IN (30 default OFF) idempotent (201) no-op unbound (152-154); create_rls_session_factory (213-227) bootstrap.py:174-176. **Current deployment**: .env DATABASE_URL=superuser postgres@<db-host> (3-5), RAGBOT_ALLOW_SUPERUSER_RUNTIME=1 (8), no DATABASE_URL_APP → engine.py:67-81 url_app None + escape=1 → admin DSN + WARN → **superuser ignores FORCE RLS, policies installed but bypassed**. Cross-tenant leak: READ paths explicit (semantic_cache 420/480, pgvector_store session_with_tenant, embed intentionally tenant-agnostic); WRITE gate (545-552 NULL); **POSSIBLE if bare SQL forgets WHERE**. Fix = ops DATABASE_URL_APP→ragbot_app (2-of-3 layers active).

### 5. CONVERSATION STATE (jsonb_conversation_state.py)
load_state (80-117): **conversation_id None → {} (85-86)**; `SELECT action_state FROM conversations WHERE id=:id AND (:ttl<=0 OR last_message_at IS NULL OR last_message_at>now()-make_interval(hours=>:ttl)) LIMIT 1` (92-101); TTL guard (96-98) DEFAULT_CONVERSATION_STATE_TTL_HOURS=24 → {} expired; errors→{}. save_state (119-147): **None → return (125-126)**; sanitize (127); `UPDATE conversations SET action_state=CAST(:s AS jsonb) WHERE id=:id` (131-137) ensure_ascii=False (138). _sanitize (149-168): drop keys not ACTION_STATE_ALLOWED_TOP_KEYS {intent, slots_filled, service_locked} (158), slots_filled non-null cap DEFAULT_MAX_ACTION_SLOTS=5 deterministic (160-165). detect_drift (170-240): service drift (196-218 locked name vs answer token CSV extract 256-260), price drift (220-238 locked price vs answer regex 263-286 range 10K-50M VND 277), __drift_severity runtime key warn default (183).

### 6. ACTION/BOOKING
slot_extractor.py:57-268: extract (69-163) guard empty (85-86), pick sub-schema by intent (89), normalize fields new/legacy (96), dynamic Pydantic field descriptions (102-103), user prompt VN "bắt buộc"/"tùy chọn" (113-129), LLM call_with_schema Anthropic tool_choice/OpenAI strict (137-146), scrub (160); model DEFAULT_SLOT_EXTRACTOR_MODEL_WIRE Haiku (53-54); no hardcode field names. resolve_action_conversation_id (`routes/_action_conversation.py:24-56`): action_config.enabled (40) + conv_repo (41) → repo.get_or_create(BotId, UserId, TenantId, WorkspaceId) (45-50) else None, SQLAlchemy/ValueError → None (51-56). **SSE wired chat_stream.py:287-300 (FIX, was None bug)** test_action_conversation_resolver.py:36-46. _render_captured_slots (query_graph.py:814-839): slots_filled (827), sub-schema by intent (832), required slots (833), render `key1="val1", key2="val2"; missing: slot3` (835-838) DATA only. action_config_validator.py validates slots_schema max 5.

### 7. CACHE INVALIDATION: corpus_version bump (re-ingest) → old entries orphaned (TTL cleanup); bot_version bump (system_prompt/oos change query_graph.py:870-873, 1797-1799) → old hash never matched.

### §III-7 VERDICT TABLE
| Layer | Status | Risk |
|---|---|---|
| semantic cache tenant scope | ✅ LIVE (420,480 + NULL gate) | LOW |
| embed cache | ✅ model-scoped | OK intentional |
| uq cache | ✅ bot-scoped | LOW |
| **RLS policies** | ✅ INSTALLED ⚠️ BYPASSED (superuser DSN) | MED (cosmetic; code-filter 1st line; ops fix DATABASE_URL_APP) |
| conv state TTL | ✅ LIVE 24h | LOW |
| action slot extraction | ✅ dynamic no-hardcode | LOW |
| SSE conversation_id | ✅ FIXED | LOW |
| cache invalidation | ✅ corpus+bot version | LOW |
Cross-tenant leak: POSSIBLE if code forgets WHERE; protected by code-level filter + tests; RLS 2nd line via DATABASE_URL_APP switch.

### §III-7 HARDCODE: conv_state.py:276-277 price 10K-50M VND spa-hardcoded; TTL 24h magic per-tenant only; slot_extractor VN "bắt buộc"/"tùy chọn"; drift CSV pattern assumes tabular.

## ███████ III-8. DI/BOOTSTRAP · CONFIG · WORKERS/STREAMS · OBSERVABILITY · GRAPH ASSEMBLY ███████

### 1. DI CONTAINER (`bootstrap.py:161-788`) 5-layer Port→Strategy→Registry→Null→DI
Infra Singletons (168-192): db_engine create_engine_app RLS SET LOCAL (168-170), session_factory create_rls_session_factory (174-176), uow_factory (177), redis_client (179-183 pool from settings), redis_streams_client (189-192 5s socket_timeout XREADGROUP), api_key_pool_factory DBBackedApiKeyPoolFactory (199-208 _PROVIDER_CODE). Adapters: cache RedisCache (211), semantic_cache PgSemanticCache (212), understand_query_cache (217-221), embed_cache (222), bus RedisStreamsEventBus (223-230), vector_store build_vector_store get_boot_config vector_store_provider default pgvector (238-247), lexical_retrieval Factory build_lexical_retrieval lexical_retrieval_provider default null/pg_textsearch per-call (255-263), embedder Singleton build_embedder env EMBEDDING_PROVIDER OR embedding_provider litellm registry litellm/jina/zeroentropy/bkai_vn (275-283), ocr build_ocr_parser parser_engine (287), system_config_service Redis-cached (288-292), secrets_port EnvSecretsAdapter AES-GCM (295), provider_key_resolver (300-305), guardrail_rule_loader alembic 010f (311-315), **guardrail Factory build_guardrail provider="local" HARDCODE TODO Phase4 (324-332 ⚠️DI-001)**, reranker build_reranker reranker_provider jina/jina-reranker-v3 (345-356), entity_extractor null (361-369), metadata_filter Factory null (377-389), crag_grader_factory (396-401), pii build_pii_redactor null (408-411), jwt_verifier RS256 (413-420). Repos (422-478): conv/document/bot/job/workspace/quota/stats_index/outbox/ai_config/request_log/audit/guardrail/message/message_feedback/tenant_policy/language_pack. Services (479-631): idempotency/tenant_guard/model_resolver/tenant_rate_limiter/rate_limiter/llm DynamicLiteLLMRouter (523-529)/token_ledger build db (517-521)/bot_registry_service/conversation_state build jsonb null per action_config (594-612)/slot_extractor/hyde_generator after llm (538-541)/hallu_verifier (552-555). Observability (473-492): invocation_logger model_invocations (473-475), pipeline_audit_logger OFF (477), metrics_port Prometheus (492). Chat hooks (712-729): ChatHookRegistry TokenUsageDbHook/TokenUsageRedisHook/QuotaThresholdNotifyHook. **DI COMPLIANCE**: ✅ 47 ports, ~12 registries (embedding/vector/reranker/guardrails/parser/pii/entity_extractor/metadata_filter), Null ubiquitous (NullVectorStore/NullReranker/NullGuardrail/...); ⚠️ guardrail hardcode "local" DI-001.

### 2. CONFIG 7-TIER
| Tier | Source | TTL | Scope |
|---|---|---|---|
| 7 constants | shared/constants/_NN_*.py (24 files) | compile | global |
| 6 bootstrap_config | get_boot_config→system_config | 30s in-proc | global |
| 5 system_config | DB JSONB | 5min Redis jittered ±10% | tenant |
| 4 plan_limits | bots.plan_limits JSONB | column | bot |
| 3 threshold_overrides | bots.threshold_overrides | column | bot |
| 2 pipeline_config | per-request build | none | request |
| 1 _pcfg() | query_graph node | per-node | turn |
bootstrap_config._ALLOWED_KEYS whitelist (46-251), get_boot_config (291-354) psycopg2 sync 30s cache. **Bug#7c 78 keys live in _pcfg but absent whitelist → operator UPDATE silent no-op → FIXED (173-251)**, defence test_pipeline_cfg_keys_parity.py. pipeline_config parity: test_chat/_pipeline_config.py (_PIPELINE_CFG_KEYS 200+keys) + chat_worker/pipeline_config.py (_CHAT_CONFIG_KEYS) MUST match. **Bug#7 38 per-intent keys (rerank_top_n_by_intent, generate_context_chars_cap_by_intent) missing worker → only test_chat honored → FIXED**. **NEW 2026-06-18 multi_query_complexity_min registered 4-site** (test_chat tuple+dict, worker config+builder). resolve_bot_limit (bot_limits.py): threshold_overrides > plan_limits > system_default, max() numeric except semantic_cache_threshold.

### 3. GRAPH ASSEMBLY (`orchestration/graph_assembly.py`)
build_graph_di_kwargs (92-122): GRAPH_DI_REQUIRED {llm, model_resolver, invocation_logger, guardrail, vector_store, embedder} fail-loud GraphAssemblyError 503 (107-115); _PROVIDER_ALIASES {audit_logger→pipeline_audit_logger, doc_repo→document_repo}; _resolve_optional catch (KeyError,AttributeError,TypeError)→None (71-89); emit graph_di_assembled (120-121). build_chat_initial_state (136-200): 25 keys {record_tenant_id, request_id, message_id, conversation_id, record_bot_id, channel_type, workspace_id, user_groups, query, raw_user_message, rewritten_query, retrieved_chunks, reranked_chunks, graded_chunks, answer, citations, guardrail_flags, tokens, cost_usd, model_used, conversation_history, pipeline_config, step_tracker, bot_system_prompt, bot_created_at, bot_extra_output_tokens, language, oos_answer_template_resolved, kg_service, session_factory}; raw_user_message never overwritten (173-176, slot extraction reads, root cause 2026-06-15). build_graph (query_graph.py:1037-1063) 28 kwargs. Node sequence: guard_input → understand → router → retrieve → rerank → grade → reflect → generate → guard_output → persist; conditional edges (guard_input→understand→router END-if-OOS; router→retrieve/oos_answer; retrieve→rerank if enabled; rerank→grade; grade→reflect/generate; generate→guard_output→persist). feature_name f"query.{purpose}" (1159).

### 4. WORKERS/STREAMS
chat_worker/pipeline.py handle_chat_received (91-106): resolve tenant (96), bind context trace_id/tenant/bot/conv (97-102), body (104), clear finally (105-106); _handle_chat_received_body (109-500) validate ChatReceivedPayload, load bot+system_config, _build_pipeline_config (81), build_chat_initial_state, resolve KG if graph_rag≠disabled (125-133), graph.invoke; callbacks _persist_and_callback (73) TokenUsageDbHook(db atomic)/Redis(post_commit INCR)/QuotaThresholdNotify(webhook). document_worker.py handle_document_uploaded (83-107) → DocumentService.ingest 7 steps. redis_streams_bus.py RedisStreamsEventBus at-least-once XREADGROUP/XACK: transactional inbox ADR-W1-D8b _INBOX_MARK_SQL (51-54) ON CONFLICT DO NOTHING, _INBOX_SEEN_SQL (55-58), _inbox_seen (169-191 fail-OPEN reprocess), _mark_processed (193-199 bus-tx after handler, fail→no XACK→redeliver); _INBOX_TX_PARAM "inbox_tx" (66) handler owns mark atomic; fairness _bot_channel_sems cap5 (106) + _workspace_sems cap10 (107) overflow DEFAULT_BUS_TENANT_SEM_MAX (146-165); XREADGROUP NOGROUP auto-create; DLQ after DEFAULT_BUS_DLQ_MAX_DELIVERIES. outbox_publisher.py transactional outbox (write+outbox same tx → publish Msg-Id=outbox.id → mark; subscriber event_inbox dedup).

### 5. OBSERVABILITY
request_logs (models_monitoring.py:75-155) 29 fields (identity record_tenant_id/workspace_id/channel_type/connect_id/message_id; bot/conv record_bot_id/record_conversation_id/trace_id; hashes question_hash/answer_hash; routing record_model_id/model_name/routing_reason; timing started_at/finished_at/duration_ms; tokens prompt/completion/total + cost_usd; status success/failed/timeout/moderated/refused + error_code/message; citations JSONB; feedback; metadata_json) indexes (tenant,started_at)/model/status/conv/question_hash/(tenant,message_id). request_steps (160-194) per-node (record_request_id FK CASCADE, record_tenant_id/workspace_id/channel_type/trace_id denorm, step_name 64ch, step_order, model_used, record_binding_id, started_at, duration_ms, input/output_tokens, cost_usd, status, error, metadata_json) indexes (request_id,step_order)/(step_name). **33 step_name** (list in §8). model_invocations (models_invocation.py) every LLM/embed/rerank (invocation_id, message_id, record_tenant_id/request_id/bot_id, step_id, attempt_no, purpose, provider, model_id/version, user_prompt, prompt/completion_tokens, cached, cost_usd, finish_reason, status, created/finished_at, feature_name). invocation_logger (102-150) async ctx invoke_model insert-running → record(response,tokens,cost,finish_reason,cached) → Prometheus cost_usd_total/tokens_used_total/model_invocation_total. pipeline_audit_logger OFF (RAGBOT_PIPELINE_AUDIT_ENABLED). audit_log (models.py:674+ alembic 010g) hash chain tamper-detect, AuditVerifier (bootstrap.py:453-455). StepTracker (step_tracker.py) per-step timing+tokens+cost → RequestStepModel, batch opt batch_step_logging_enabled. Metrics (metrics.py): citation_validation_fail_total, embedding_model_mismatch_total, decompose_skipped_low_confidence_total, intent_classifier_confidence histogram, mq_skipped_no_entities_total, mq_variants_deduped_total, llm_resolved_purpose_total, cliff_drop_total, grounding_fail_total, cost_usd_total, tokens_used_total, model_invocation_total, chat_worker_queue_depth, request_total, document_ingest_duration_seconds/total. structlog bind trace_id/tenant/bot/conv/mode_ctx.

### §III-8 OBSERVED ISSUES
- DI-001 guardrail hardcode "local" (bootstrap.py:326) — cannot Null without redeploy (TODO Phase4).
- CONFIG-001 78 keys whitelist (FIXED). CONFIG-002 38 per-intent keys worker (FIXED). DI-002 HyDE DI gap flag no-op (FIXED 538-541). CONFIG-003 pii frozen DI (410).
- Broad-except: 3 unjustified + 248 noqa BLE001 (graceful-degradation observability/audit). version-ref ~0 (4 dead comments vi_tokenizer). Magic numbers mostly false-positive (HTTP codes, max_length).

### §III-8 DEBUG CONFIG TRACE
rerank_top_n change? → bot.threshold_overrides→plan_limits→system_config rag_rerank_top_n→DEFAULT_RERANK_TOP_N=7→_pcfg node. system_config cache miss? → bootstrap_config 30s OR Redis 5min `ragbot:sysconfig:{key}` OR DB. Flip effect: bootstrap_config 30s TTL; system_config 5min Redis (invalidate_cache immediate); per-bot column immediate.

---

*HẾT FILE — đầy đủ. PART I (§0-§9) navigable map · PART II (II-A→II-H) detail nén · PART III (III-1→III-8) exhaustive verbatim. Mọi file:line verify read-only + load-test 2026-06-18. Debug từ §0 → §9-C → PART III subsystem.*














