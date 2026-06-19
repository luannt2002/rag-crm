# STATE SNAPSHOT — Ragbot (fresh phase 2026-06-14)

> Always-updated current state. Git history was reset on 2026-06-14 (fresh start);
> commit-SHA anchors no longer apply — this file is the source of truth.

## Session 2026-06-19 — Phase 6 god-file split (batch 1+2, behavior-preserving)  ⟵ LATEST

**[T3-Refactor · LOWEST priority · evidence-driven, green-gate per step]**

### ✅ ĐÃ LÀM + VERIFIED
- **Tạo module mới** `src/ragbot/orchestration/query_graph_helpers.py` (156 dòng, **0 ruff error**) — gom các helper THUẦN (stateless, không close over `build_graph` di_kwargs).
- **Batch 1** (commit `5a515c5`): tách 5 helper — `_uuid_or_none`, `_parse_doc_type_vocabulary`, `_render_captured_slots`, `_compute_bot_cache_version`, `_is_null_lexical`. Gỡ import thừa (`hashlib`, `DEFAULT_BOT_CACHE_VERSION_HASH_LEN`).
- **Batch 2** (commit `8e73b57`): tách 2 parser leaf-pure — `parse_decomposed_sub_queries`, `expand_parent_chunks`. Gỡ `DEFAULT_PARSE_DECOMPOSED_MAX_SUB` thừa.
- **Re-export pattern**: `query_graph` import-lại mọi tên → MỌI đường import cũ (`from ragbot.orchestration.query_graph import X` trong tests + threading di_kwargs vào node funcs) GIỮ NGUYÊN, 0 call-site phải sửa.
- **Verify mỗi bước**: full unit suite **5912 pass / 0 fail** (Y HỆT baseline `0a73211` — 39 skip/34 xfail/34 xpass) ×3 lần (baseline + sau batch1 + sau batch2). Behavior preserved exact (trong phạm vi unit coverage).
- **query_graph.py**: 3945 → **3820 dòng** (-125). Pre-existing ruff debt 254→249 (KHÔNG thêm lỗi mới — file god này chưa từng ruff-clean).

### ✅ build_graph surgery ĐANG CHẠY (user chốt "Phẫu thuật build_graph closures", green-gate mỗi bước)
Strangler-fig: hoàn thiện pattern `functools.partial(_node, di=…)` đã có sẵn (retrieve/rerank/grade/generate đã tách trước). MỖI bước verify full suite **5912 pass / 0 fail** = behavior-preserving.

| Phase | Nội dung | Commit | Kết quả |
|---|---|---|---|
| A | `_pcfg` → query_graph_helpers (pure) | (Phase A) | 5912 ✓ |
| B | 9 routing deciders → `nodes/routing.py` (pure state→str, 0 di_kwargs) | `8435c17` | 5912 ✓ + fix 1 brittle source-test |
| C.1 | mmr_dedup, neighbor_expand, graph_retrieve → nodes/* (bind _pcfg/_audit) | `a6cd479` | 5912 ✓ + fix 1 brittle source-test |
| C.2 | critique_parse, rewrite_retry → nodes/* (bind _oos_text / rewrite) | `b58bb9d` | 5912 ✓ |

- **query_graph.py: 3945 → 3407 dòng** (-538). Mỗi node-body chuyển sang `nodes/<name>.py`, build_graph chỉ giữ `functools.partial` binding (~3 dòng/node). Mọi import cũ + di_kwargs threading GIỮ NGUYÊN qua re-export/partial.
- **2 brittle test fix** (HONEST, không che regression): cả 2 là `inspect.getsource(build_graph)` grep text đã di chuyển — behavior verified intact (consume-set + mmr_filter strip_embedding), assertion retarget tới đúng construct/module.
- **CÒN LẠI**:
  - **Phase D** — node capture nhiều di_kwargs: guard_input, check_cache, condense_question, router, rewrite, decompose, query_complexity_node, adaptive_decompose (+ sub-helpers). Lớn hơn, cùng pattern.
  - **Phase E** — composite/parallel: cache_check_and_understand_parallel, rewrite_and_mq_parallel + _run_* sub-helpers.
  - **Infra-closures GIỮ trong build_graph** (capture di_kwargs, shared by-ref): _audit, _resolve_corpus_version, _invoke_llm_node, _invoke_structured_llm_node, _so_usage, _prewarm_embedding_cache, _embed_query, _llm_complete_fn.
  - **Load-test milestone** (stack up) sau khi Phase D/E xong — verify HALLU=0 + bot answer đúng runtime (unit mock chưa đủ cho sacred pipeline).
  - `retrieve.py` (1888 dòng) = god-file riêng, chưa đụng.
- **Plan**: `plans/260619-phase6-buildgraph-split/plan.md`.

---

## Session 2026-06-18→19 — Phase 0 EXECUTED: bots WORKING + squash 240→1 + tests GREEN

**[T1+T2 · đã sửa `src/`, schema, tests — verified runtime]**

### ✅ ĐÃ LÀM + VERIFIED (evidence thật)
- **3 bot chạy end-to-end**: `test-spa-id`(ws spa) · `chinh-sach-xe`(ws xe) · `thong-tu-09-2020-tt-nhnn`(ws legal) — **trả lời đúng từ corpus** (retrieve vector+BM25 → Jina rerank → gpt-4.1-mini generate → grounded). Verified nhiều lần.
- **Model stack** (user chốt): **gpt-4.1-nano** (light: routing/intent/condense) + **mini** (generation/grade/ground) · **Jina** embed `jina-embeddings-v3` 1024-dim + rerank `jina-reranker-v3`. Provider chọn qua `system_config.embedding_provider/reranker_provider='jina'`.
- **Single-process** (devops yêu cầu): 1 PID `python -m ragbot.main` + 4 embedded asyncio worker (consumer/outbox/recovery/cost-cap). Scale = nhân bản process, consumer-group chia việc.
- **SQUASH 240→1**: `alembic/versions/` = **1 file** (`20260618_squash_baseline.py` + `squashed_baseline.sql`, 44 bảng). 278 file cũ → `alembic/_archive_pre_squash_20260618/`. **Validated: fresh DB `alembic upgrade head` → 45 bảng, exit 0.** Chain-break 0006 fixed (guard column-exist).
- **1 seed file**: `scripts/db/seed_dev.py` (orchestrate: system_config + RBAC + language_packs + 3 bot + provider=jina + quota). + `scripts/db/seed_3test_bots.py` (import sysprompt từ archived 0239/0236).
- **Schema gaps vá (vào squash)**: `document_chunks`(embedding vector(1024)+content+chunk_context+doc_deleted_at+search_vector+trigger) · `document_service_index` · `token_ledger`/`monitoring_log`/`token_budgets` (**FK-free** = xóa chunk giữ cost, user yêu cầu) · `quotas`.
- **Tests**: multi-agent (6 agent Workflow) fix **28 file** post-squash → **5897 pass / 0 fail** / 39 skip. Ruff+mypy file mới sạch. `pyproject` thêm per-file-ignore seed scripts.
- **CLAUDE.md**: thêm dòng no-guess (cấm tuyên bố ≥X/100 khi chưa backward-verify + load-test output).
- **BUG-1 CONFLATE fix (CODE, Phase 2)**: thêm `parse_price_of_entity_query` ([query_range_parser.py]) → route "<entity> giá bao nhiêu" sang `operation="keyword"` (structured name lookup, 1 entity=1 giá có nhãn → conflate bất khả). Wired `retrieve.py` (range→code→**price-of-entity**→list) + constant `DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED` (per-bot opt-out). **TDD 15 test pass + full suite 5912 pass/0 fail.** ⚠️ **Runtime chưa kích hoạt**: route đọc `document_service_index` đang RỖNG (dead-wire #2 — xem risk #7) → hiện fallback vector an toàn (no regression). Conflate hết HẲN khi stats-index được populate.
- **STATS-INDEX — KHÔNG phải dead-wire, là ORDERING**: `parse_table_chunks` CHẠY ĐÚNG (spa 30-chunk→200 entities). Index rỗng vì **ingest chạy TRƯỚC khi `document_service_index` được tạo** (em add bảng lúc debug query, sau ingest → bulk_insert fail silent best-effort). **Fix = backfill** `scripts/db/backfill_stats_index.py` (reusable, idempotent) → **1335 entities** (spa 350·xe 973·legal 12) với giá có nhãn.
- **✅ CONFLATE FIX RUNTIME-VERIFIED**: sau backfill, "triệt lông nách giá bao nhiêu" → **"199.000đ buổi lẻ"** (đúng giá entity "Nách" trong stats `Nách|199000`, KHÔNG conflate). Route price-of-entity → `query_by_name_keyword` → 1 entity=1 giá nhãn. **BUG-1 đóng hoàn toàn** (code+TDD+data+runtime).
- ⚠️ **Backfill cần chạy SAU ingest** (chunk phải tồn tại) — thêm vào runbook ingest, KHÔNG vào seed_dev (chạy pre-ingest).
- **Phase 3 token-stats (rerank-capture CODE done)**: helper `infrastructure/token_ledger/aux_usage.py::emit_aux_usage` (đọc ctx 4-key, fire-and-forget) + `JinaReranker` nhận `ledger` + emit `action="rerank"` sau response + bootstrap move `token_ledger` provider lên trước embedder/reranker + pass `ledger=token_ledger`. Container build OK, **258 test pass/0 regression**. ⚠️ Verify runtime BỊ CHẶN: **Jina rerank node BYPASS** (rerank=1ms, **0 `rerank_executed` event**) → reranker không execute dù provider="jina"+JinaReranker resolve đúng + whitelist rỗng → retrieval đang dựa RRF-only (finding quality riêng). Embed-capture (cùng pattern) CHƯA wire.
- **✅ RERANK-BYPASS FIXED + token-stats VERIFIED**: rerank-node thấy `null_reranker` vì **per-bot `RerankerResolver` trả Null** do 3 seed-gap: (1) `ai_providers.code`=NULL → `provider_code` rỗng, (2) `system_config.reranker_model`="cohere/rerank-v3.5" (init-default) → JinaReranker model sai → 422, (3) `ai_providers.api_key_ref`=NULL → resolver `os.getenv(None)` → `rerank_resolver_api_key_empty`→Null. **Fix**: set `code=name` + `api_key_ref=JINA_API_KEY/OPENAI_API_KEY` (đưa vào `seed_3test_bots.py` reproducible) + `reranker_model=jina-reranker-v3`. **Kết quả**: rerank ACTIVE (Jina cross-encoder 444ms — trước RRF-only → **quality TĂNG**) + wire ledger vào `RerankerResolver` → **token_ledger có `rerank|jina-reranker-v3|1918tok|444ms`** = log-center capture rerank VERIFIED. 282 test pass/0 regression.
- **✅ Phase 3 token-stats D1 COMPLETE+VERIFIED**: embed-capture wired (JinaEmbedder + `build_embedder` signature-filter + bootstrap ledger) → **token_ledger ghi ĐỦ 3 action**: `embedding|2|50tok` + `llm|52|14423tok|$0.0045` + `rerank|3|6337tok`. Mọi external paid call (LLM/rerank/embed) giờ durable trong log-center với provider/model/tokens/duration. 572 test pass/0 regression. **Còn**: cost_usd cho rerank/embed=0 (chưa snapshot unit_price — chỉ token); **D2 timeseries API** `/metrics/usage/timeseries` (date_trunc trên token_ledger, RBAC-scoped) chưa làm.
- **✅ Phase 3 D2 DASHBOARD API COMPLETE+VERIFIED**: `TokenLedgerAnalyticsRepository.usage_timeseries` (date_trunc hour|day|month · breakdown none|model|action|provider · whitelist chống injection · optional bot/workspace filter · all_tenants) + endpoint `GET {BASE}/admin/metrics/usage/timeseries` (RBAC: tenant≥admin, scope=all→L100) + wired container. **Test thật**: `?group_by=day&breakdown=action` → trả `[{ts, bucket_key:llm/rerank/embedding, tokens_in/out/total, cost_usd, calls}]` — đúng màn dashboard "verify 1 bot/ws/tenant dùng bao nhiêu token theo khoảng thời gian". Full suite **5912 pass/0 regression**. → **LOG-CENTER (D1 capture + D2 dashboard) HOÀN CHỈNH**, đúng yêu cầu ban đầu của user.

### 📈 RE-SCORE load-test (verified, post conflate+rerank fix, conc=2)
| Metric | Baseline (pre-fix) | **Now** |
|---|---|---|
| Coverage (content answered) | 13/15 = 87% | **15/15 = 100%** (2 false-refuse hết) |
| Latency p95 | 70s (RPM burst) | **8.8s** (conc=2 + rerank fast) |
| Latency p50 | 10s | **5.3s** |
| Errors | 0 | 0 |
| Trap refuse | 6/7 | 7/7 (2 ⚠REVIEW đã đọc tay → **refuse đúng, HALLU=0**) |
| **HALLU** | ~0 | **0 VERIFIED** (fabricate-price trap: spa vàng-24k + xe 999/99R99 đều refuse, KHÔNG bịa giá) |
→ **conflate fix + rerank-active = Coverage 87→100%, latency -88%, HALLU=0 giữ nguyên.** Số thật, không đoán. Quality 100% (Faithfulness 1.0 + Coverage 1.0).

### 📊 CHẤM ĐIỂM LẠI (verified runtime vs baseline tĩnh ~62)
| Flow | Baseline tĩnh | **Now (verified)** | Lý do |
|---|---:|---:|---|
| Nền tảng (migration/DB/test) | 32 | **78** | squash reproducible + DB seeded chạy + suite green 5897 + 1-seed |
| RAG-CRM | 73 | **80** | squash + cost-tables FK-free verified |
| Trace-log | 87 | **87** | giữ |
| RAG Query | 62 | **70** | bot trả đúng (verified), Coverage 87% đo thật; conflate chưa fix, few-shot chưa A/B |
| RAG Ingest | 67 | **68** | embedding Jina works; **U4 chunk dead-wire `parsed_blocks=[]` VẪN còn** |
| Token-stats | 52 | **55** | token_ledger có; rerank/embed capture chưa wire (Phase 3) |
| **OVERALL** | ~62 | **~73** | nền + verified kéo lên; smartness (conflate/coverage) chưa chạm |
- **Load-test đo**: Coverage **87%** (13/15) · HALLU **~0** (trap refuse đúng 7/7) · Errors 0 · latency p95=70s (=OpenAI RPM backoff burst).

### 🔴 RISKS / CAVEATS (quan trọng — đọc trước khi tiếp)
1. **OpenAI tier THẤP (~500 RPM)** — mỗi load-test 22-case song song → burst → backoff 64s/call (p95=70s) + đốt quota. **Gate Phase 1/2/4/5** (vòng đo-sửa-đo). → cần nâng tier HOẶC load-test `LOADTEST_CONCURRENCY=2` (đã set default thấp).
2. **Squash là SCHEMA-ONLY** — `squashed_baseline.sql` không có DATA. **Data-migration content** (few-shot prompts 010w/010z, money-norm 0114...) KHÔNG ở fresh-DB seed. Dev hiện dùng **prompt BASE (version=1)**. Thử accumulate few-shot (version=4) → **0/15** nhưng đó là **app-quota gate**, không phải few-shot → **đã revert, cần A/B lại khi quota ổn**.
3. **DEV-ONLY hacks (KHÔNG được lên prod)**: `bots.bypass_token_check=true` (3 bot), `tenants.bypass_rate_limit=true`, `RAGBOT_ALLOW_SUPERUSER_RUNTIME` trong `.env`, redis `protected-mode no`, docs `state` flip thủ công→active. Mọi thay đổi DB-content thủ công cần đưa vào `seed_dev.py` cho reproducible.
4. **Ingest robustness (Phase 2)**: pool=20 → `MaxConnectionsError` khi burst; FK-violation khi `--wipe` xóa doc giữa lúc worker chèn chunk (no guard doc-deleted-mid-flight).
5. **Corpus chưa đầy đủ**: spa 888 / xe 2192 / legal 576 chunk (ingest rate-limit churn); 8 doc flip active thủ công.
6. **278 migration + 8 test archived** (`_archive_pre_squash_20260618/`) — recoverable qua git; squash baseline là source mới.

### 🎯 EXPERT SOLUTION cho phase còn lại (best-practice)
- **Phase 2 conflate** (BUG-1): plan có sẵn `plans/260618-phaseA-bug1-conflate/` — `parse_price_of_entity_query` → `query_by_name_keyword` (structured-first routing, LlamaIndex SQLAutoVector pattern). + wire `parsed_blocks` (block-pipeline). + grounding warn→enforce. Verify: load-test conc=2.
- **Phase 3 token-stats**: capture Jina rerank/embed `usage` → `AsyncDBTokenLedger` (đã có hạ tầng, chỉ wire 5 adapter); ContextVar auto-attribute. + API `/metrics/usage/timeseries` date_trunc trên token_ledger, RBAC-scoped. Ref `reports/LOG_CENTER_OBSERVABILITY_DESIGN_20260618.md`.
- **Phase 4 A/B**: bật từng cờ DEFAULT=False + few-shot, đo Coverage/latency delta conc=2, giữ cờ +lift (bài học Wave E: đừng tin paper -30%).
- **Phase 6 refactor**: god-file `query_graph.py`(~3900) → tách 1-node-1-file; behavior-preserving + suite green gác.
- **Phase 7 docs**: 50 keep / 35 orphan (đã audit) → 1 INDEX + archive orphan.

### ➡️ NEXT (post-/compact — tiếp từ đây)
- **API KEYS verified khỏe (2026-06-19)**: 2 Jina key (...ktcljw, ...hTshEC) embed+rerank HTTP 200; OpenAI key (...CzvlIA) gpt-4.1-mini OK. (Lỗi 403/1010 lúc test = Cloudflare chặn urllib, KHÔNG phải key chết — app httpx OK, 1125 embeddings đã lưu.)
- **Kiến trúc model XÁC NHẬN đúng** (token_ledger): `llm→openai` (gpt-4.1-mini/nano, CHỈ generation/routing) · `rerank→jina` (jina-reranker-v3) · `embedding→jina` (jina-embeddings-v3). ChatGPT KHÔNG ở rerank/embed.
- **Phase 6 refactor — đang dở PHÂN TÍCH (chưa edit code)**: chỉ 2 god-file >1200: `query_graph.py` (3945 = ~24 helper module-level dòng 410-1036 + `build_graph` monolith 1037-3915 ~2878 dòng node-closures) · `retrieve.py` (1888 = 1 hàm `retrieve` ~1740 dòng full-closure). **Kế hoạch AN TOÀN**: tách cụm pure leaf helpers (`_uuid_or_none`/`_parse_doc_type_vocabulary`/`_render_captured_slots`/`_compute_bot_cache_version`/`_is_null_lexical`/`_resolve_*`...) → `query_graph_helpers.py`, import back, suite green gác MỖI bước. KHÔNG đụng `build_graph` node-closures (rủi ro vỡ graph). Mục tiêu <1200 cần nhiều bước → làm dần, commit nhỏ.
- **Phase 4 A/B**: coverage đã 100% (hết headroom) → A/B giờ nhắm COST/LATENCY (cascade nano-vs-mini, adaptive-context). Cần load-test `conc=2`.

### ➡️ NEXT cũ (đã xong phần lớn — giữ tham khảo)
1. ~~Phase 3 token-stats~~ ✅ · ~~Phase 7 docs~~ ✅ · Phase 6 refactor (đang làm).
2. ~~Phase 2 conflate~~ ✅ · Phase 4 A/B · ~~Phase 5 re-score~~ ✅ (Coverage 100%).

---

## Session 2026-06-18 — Expert-RAG deep-read + research (~80 agent) + dev-DB rebuild  ⟵ HANDOFF/COMPACT ANCHOR

**[T1-Smartness · research/analysis, KHÔNG sửa hot-path `src/`]**

### Đã làm (artifacts ở root + reports/ + plans/)
- **Deep-read 9 subsystem** (6 read-only agent, file:line) → [reports/PROJECT_UNDERSTANDING_EXPERT_RAG_20260618.md](reports/PROJECT_UNDERSTANDING_EXPERT_RAG_20260618.md). Verdict: khung expert-grade, **trí thông minh bị TẮT bằng flag `DEFAULT=False`** (cascade/async-grounding/adaptive-context/Ekimetrics/narrate/late-sliding/HyDE/MQ-gate) + **block-pipeline dead-wire** (`parsed_blocks=[]` hardcode → `smart_chunk_atomic` never called).
- **Research SOTA ~80 agent** (web+arXiv 2024-26, adversarial-verified) → **[RAG_RESEARCH_MASTER_20260618.md](RAG_RESEARCH_MASTER_20260618.md)** (PART1, 7 trục) + **[RAG_RESEARCH_MASTER_PART2_20260618.md](RAG_RESEARCH_MASTER_PART2_20260618.md)** (PART2, 9 trục: GraphRAG/RAPTOR/embeddings/pgvector-scaling/security/reranking/hybrid-tuning/table-RAG/eval-CI/caveats). Đối chiếu code → [reports/CHUNKING_RESEARCH_VS_CODE_20260618.md](reports/CHUNKING_RESEARCH_VS_CODE_20260618.md).
- **Bản đồ luồng full** (debug handoff) → [reports/PROJECT_ALL_FLOWS_20260618.md](reports/PROJECT_ALL_FLOWS_20260618.md) §0 KNOWN BUGS.
- **Plan Phase A** (chờ approve) → [plans/260618-phaseA-bug1-conflate/plan.md](plans/260618-phaseA-bug1-conflate/plan.md).

### Bug đã đo (rule#0) + gốc rễ verify
- 🚨 **BUG-1 CONFLATE giá**: gốc `shared/query_range_parser.py:374-377` (loại "gia bao nhieu" → vector → conflate). Fix = sản xuất `RangeFilter(operation="keyword")` cho price-of-entity → `query_by_name_keyword` (cơ chế ĐÃ CÓ, verified `stats_index_repository.py:418-494`, trả per-row giá-có-nhãn atomic).
- grounding **warn-only = faithfulness KHÔNG enforce** · RLS **bypass runtime** (.env superuser) · routing regex VN hardcode · i18n superlative/tokenizer hardcode tuple `("vi","en")` · 1-bot-1-language.

### DB local: migration vỡ → rebuild bằng runbook (KHÔNG squash)
- `alembic upgrade head` fresh DB FAIL (`bot_model_bindings.tenant_id` không tồn tại rev 0006 — history sửa sau). Dùng [scripts/db/REBUILD_DEV_DB_RUNBOOK.md](scripts/db/REBUILD_DEV_DB_RUNBOOK.md): `create_all`(26)+`bootstrap_ddl_only_tables.sql`+`stamp head` → **35 bảng @ 0240, 158 system_config, RBAC seeded** ✅ trên local 5434 (ragbot/ragbot, redis 6380). **CHƯA seed bot** (cần `scripts/db/seed_dev_drmedispa_bot.py`, API-heavy). Bug phụ: `scripts/seed_ai_config.py` (top-level) **stale** (`provider_id` vs `record_provider_id`).
- `.env` = local DB 5434 + key thật (gitignored). Server `10.0.1.160` unreachable.

### TOP-7 adoption (map bug) — chi tiết 2 research file
1. 🔴 routing price-of-entity→stats (Phase A) + **table STC per-row** (conflate; STC MRR+66%/R@1+106%)
2. 🔴 atomic-claim NLI + numeric-verify (faithfulness enforce)
3. 🔴 eval-CI dual-gate + ARSP (thoát fix-bừa; fix LOW-recall ở retrieval không sysprompt)
4. pgvector tune (ef_construction=128/ef_search=160 + halfvec + iterative_scan) — p95
5. 🔴 retrieval-layer injection scanner + URL provenance (security P0; guardrail mù chunk-injection)
6. cascade/async-grounding/MQ-gate (config-flip đã build) — p95/cost
7. ViRanker/Qwen3-0.6B swap (VN recall, same 1024-dim)
- ❌ **KHÔNG full GraphRAG** (cost ~350× token, win-rate thổi phồng — adversarial bác). Caveat vendor (pgvectorscale 28×, per-tenant 37.2×, ef40 90-93%) đã bác — PART2 §J.

### ➡️ NEXT (sau /compact, tiếp tục từ đây)
- **(a)** Code **Phase A1** TDD: thêm `parse_price_of_entity_query` → wire `retrieve.py` (sau code-query, trước list-query) → `query_by_name_keyword`. Failing test trước. Cần user approve plan trước khi đụng `src/`.
- **(b)** Seed bot local (`seed_dev_drmedispa_bot.py`) để load-test (`scripts/verify_fixes_loadtest.py` gate: conflate=0, Coverage≥0.95, HALLU=0).
- **(c)** Eval-CI harness (RAGAS dual-gate) — anti-whack-a-mole.

### Lesson
Code KHÔNG "5/100" — ruff+mypy strict, 6158 test, ~0 broad-except; nợ tập trung (god-file `query_graph.py` 3945 dòng, 71 file dead-code, `z_luannt_*.txt` commit nhầm). "Test lòi bug = test đúng việc". Scorecard 5-tiêu-chí: Faithfulness ❌(conflate) · Nhanh ❌(p95~15s) · UX ⚠️ · Perf ⚠️(RLS off) · Cost ⚠️. Overall ~63/100.

## Session 2026-06-17 (cont) — Aggregation + number standard + 3-bot role-fix (alembic 0235–0236)

**[T1-Smartness]** Đào sâu retrieval/aggregation/sysprompt cho 3 bot demo (spa/xe/legal), fix tận gốc nhiều bug, HALLU=0 giữ vững. Commits: `4c61deb`→`e5c71ee` (chưa push — harness chặn, user tự push).

**Fixed + verified (rule#0 — evidence load-test):**
- **Quy chuẩn số canonical** `shared/number_format.py` — 1 SSoT cho ingest+query (trước có 2 parser lệch: `700,000`→700 bug). 21/21 format đúng (`1.200.000`/`5000 nghìn`/`1tr499`/negative). Wire `query_range_parser` + `document_stats` delegate.
- **Aggregation route** (đắt nhất/rẻ nhất/dưới X): superlative `top_by_price` + range `query_by_price_range` → stats→generate bypass (rerank/grade drop chunk SQL nên route thẳng generate, seed `graded_chunks`). "dưới 700.000"/"đắt nhất Meso 3tr"/"rẻ nhất" chạy.
- **semantic_cache dim 1280→1024** (alembic 0235) — di sản ZE→Jina (0228 bỏ sót cột này) gây runtime `DataError: expected 1280` MỖI cache write. **API/cache bug thật.**
- **symbol-phrase rank boost** (pgvector_store) — mã `195/65R15` match FAQ chunk nhưng rank=0 (AND-query), boost lên top sparse. Đúng ở hybrid; downstream rerank/grade vẫn drop → cần structured-route.
- **Sysprompt 3 bot role-correct** (alembic 0236, 3-agent design): GATE off-topic (từ chối code/game), identity+greeting-intro overview, category list-all, legal doc-orientation. Verified: identity/off-topic/hallu/orientation ✓, HALLU=0 (Michelin/phun-xăm/Điều-78 refuse) ✓.
- **Greeting leak-skip** (`DEFAULT_SYSPROMPT_LEAK_SKIP_INTENTS`): greeting bị `system_prompt_leak` guard chặn oan (LLM copy persona verbatim → shingle match). Skip cho greeting/chitchat. Chào chạy cả 3 bot + HALLU giữ.
- Test suite **5916 pass / 0 fail**; fix 9 orphan test + 6 git-env skip-graceful.

**Root-caused, CHƯA code (3 bug = 1 gốc):** spa "tẩy da chết mấy loại"/"tư vấn về da list-all" + xe "195/65R15 còn hàng" — đều **retrieval coverage** (chunks_used=1-2, miss matching services). Sysprompt RULES đúng nhưng retrieval không surface đủ. **1 fix giải cả 3: keyword-list structured route** trong `document_service_index` (lookup name LIKE keyword → trả đủ record → LLM list/đếm/lookup). Mở rộng stats route (hiện chỉ price-range/superlative).

**Docs viết:** `reports/PROJECT_STATE_EXPERT_RAG_*`, `DEEPDIVE_{CHUNKING,RETRIEVAL,COMPLIANCE}_*`, `N8N_PROMPT_CULTURE_*`, `docs/master/DB_SCHEMA_AND_MIGRATION_MINDSET.md` (273-migration synth, head 0236), `docs/dev/{N8N_TO_RAGBOT_PROMPT_MINDSET,CONSULTANT_BOT_BEHAVIOR_RULES}.md`, `reports/{SPA,XE,LEGAL}_BIZFLOW_*`. **Lesson:** đa số bug coverage truy về retrieval/chunking, KHÔNG phải LLM/sysprompt — fix đúng tầng.

## Session 2026-06-17 — ZE→Jina swap + pure-Jina ingest (alembic 0228–0230)

**[T2-CostPerf]** Embedding/rerank provider ZeroEntropy → **Jina**; ingest made
nano-free so upload is queryable in seconds, không storm 200k OpenAI TPM.

- **Root cause đo tận tay**: 1 doc ingest = nano CR sinh ~72 call / **1.54M input token / ~8 phút / chunks=0** (enrich chặn embed). Bệnh = per-chunk nano nhồi full-doc = **O(n²)**, KHÔNG phải provider.
- **Provider swap (alembic 0228)**: `embedding_provider=jina` (jina-embeddings-v3, **1024-dim**, late_chunking), `reranker_provider=jina` (jina-reranker-v3). Seed `ai_providers('jina_ai')` + 2 `ai_models` + repoint bindings. Cột `document_chunks.embedding` vector(1280)→**vector(1024)** + rebuild HNSW. `api_key_ref='JINA_API_KEY'` (0229). Health: embed+rerank **healthy**.
- **Late chunking** (Jina native, verify bằng curl: 3 câu → 3 vector 1024, 0 LLM): ngữ cảnh cross-chunk nằm trong lần embed → thay thế hẳn nano CR.
- **3 nano-in-ingest path TẮT hết** (alembic 0228+0230): `contextual_retrieval_enabled=false` (#1 CR), `enrichment_enabled=false` (#2 legacy enrich), `narrate_then_embed_enabled=false` (#3 table→sentence storm). → ingest = parse→chunk→**Jina embed**→store, **0 ChatGPT**. ChatGPT chỉ còn ở query (understand/rewrite/grade/answer). Mỗi gate đã comment `NANO-IN-INGEST PATH #n/3 — DEFAULT OFF` + WHY trong code (ingest_stages_enrich.py + document_worker.py) để không nhầm.
- **Code mới**: `infrastructure/embedding/jina_embedder.py` (EmbeddingPort, late_chunking windowing theo token budget, task retrieval.passage/query, response `data[]`), registry `jina`/`jina_ai`. Constants `DEFAULT_JINA_EMBEDDING_*`. Key trong `.env` (JINA_API_KEY/EMBEDDING/RERANKER).
- **Jina limit**: embed + rerank đều **100 RPM / 100k TPM** (free key, theo key) — ngân sách RIÊNG tách OpenAI 200k; late_chunking O(n) nên 100k thừa.
- **Còn đo (rule#0)**: load test query đối chiếu recall sau khi bỏ narrate (bảng spreadsheet) — chất lượng table có giữ với late_chunking không. CB fix 429-không-trip-breaker (phiên trước) vẫn hiệu lực.

## Session 2026-06-15/16 — God-file refactor + CRM + key rotate (alembic 0219)
Strangler-fig refactor (EVOLVE not REWRITE), mỗi bước verify xanh + service healthy:
- **chunking god-file (3192)** → package `shared/chunking/` 6 module ≤1.2k (`__init__` core + `strategies` 778 + `analyze` 683 + `csv_chunker` 445 + `blocks` 328 + `vn_structural` 278) + 32 unit test (`test_chunking_modules_split.py`) + API-guard.
- **document_service god-file (4436)** → package: `__init__` 989 (≤1.2k) + `ingest_core` 2913 (ingest() mixin — đang decompose `_IngestCtx` 8-stage) + `ingest_helpers` 494 + `ingest_phases` 339 + `text_processing` 200. Tất cả test xanh.
- **model_resolver god-file (1230)** → package: `__init__` 1070 (≤1.2k) + `_helpers` 242. 11 test.
- **test_chat god-file (5354)** → package `routes/test_chat/` 12 module (max 1178 ≤1.2k), route-count khớp 36 api + 7 pages, 166 test. URL `/api/ragbot/test/` giữ nguyên. (multi-agent execute)
- **chat_worker god-file (1796)** → package `workers/chat_worker/` 6 module (max 762 ≤1.2k), 15 path-guard test updated, 72+ test. (multi-agent execute)
- **retrieval_filter.py** tách khỏi query_graph (CRAG filters thuần).
- **ingest_core decompose** (`_IngestCtx` + 8 stage trên mixin) → `ingest_stages*.py` (max 992 ≤1.2k). 129 ingest-test pass.
- **query_graph carve (8020)** → lift 8 node nặng (retrieve/generate/grade/guard_output/persist/rerank/understand/reflect) ra `orchestration/nodes/*.py` (mỗi ≤1.2k); query_graph.py 8020→3743 (build_graph wiring + helper + node nhỏ). **Golden-net 42Q: intent 42/42 + chunk-id 42/42 IDENTICAL, HALLU=0/6** — behavior bất biến chứng minh. (~20 path-guard test updated)
- **VN-lang → config per-lang** (constants `_24_structural_markers_by_lang`): VN Chương/Mục/Điều + agg-keywords ra config, **VN byte-identical**, EN markers+agg thêm, JP placeholder.
- **7/7 god-file >1k đã xử lý** (6 ≤1.2k hoàn toàn; query_graph 8 node nặng ra module ≤1.2k, file wiring 3743). Validator agent: **5906+ pass, 0 ImportError từ package split**. 2 self-regression đã tự fix (`_ATOMIC_BLOCK_TYPES` __all__, monitoring_log test-mock).
- **Còn defer:** RLS activation (machinery sẵn: role ragbot_app NOBYPASSRLS + 24 policy + hook + leak-test — nhưng flip prod role = ops-rollout, không làm lúc context cạn) · comment-rewrite (low-value, file split đã có docstring) · 4 test model-alias stale (haiku/sonnet → gpt-4.1-mini, pre-existing model-cleanup) · 6 test no-git env.
- **CRM analytics read-layer** (alembic **0219** `token_budgets`): `CrmAnalyticsService` + route `crm.py` (`/api/ragbot/crm/analytics/{tokens,latency,nodes,top-questions,quality}` + `/crm/budget/status`) trên `request_logs`+`request_steps`+`monitoring_log` + dashboard `static/crm.html`. Tenant-scoped, RBAC owner.
- **monitoring_log** (alembic 0217) durable per-request + `GET /api/ragbot/test/monitoring`.
- **Booking fix**: bare-slot turn (`raw_user_message`) + sysprompt booking-precedence (alembic 0218) → 5/5 CONFIRM, OOS HALLU=0.
- **OPENAI_API_KEY rotated** trong `.env` (provider `api_key_ref=OPENAI_API_KEY`), models vẫn gpt-4.1-nano/mini.
- Plan 16-việc: `plans/260615-fix-all-16/plan.md`. Còn lại: ingest_core decompose · test_chat rename+split · chat_worker/model_resolver/dynamic_litellm_router · query_graph (golden-net) · multi-lang EN parity · hardcode sweep · RLS · docs.

## Platform
Multi-tenant RAG-as-a-Service. Python 3.12 / FastAPI / LangGraph / pgvector / Redis.
Single process `ragbot-py.service` (uvicorn `--workers 1`) = API + 4 embedded asyncio
workers (ingest consumer, outbox, recovery, cost-cap alerter). 4-key bot identity
`(record_tenant_id, workspace_id, bot_id, channel_type)`. Product = the API (BE-to-BE,
~90% real traffic); FE/demo pages are a test harness only.

## Model catalog (LOCKED 2026-06-14, alembic 0216)
Only these models exist — haiku + gpt-4.1 (full) + gpt-5 removed entirely so they
can never be selected:
- **gpt-4.1-mini** — primary LLM (answer, grade, grounding, rewrite, multi_query, narrate, slot-extract). $0.40/$1.60 per 1M.
- **gpt-4.1-nano** — available for cheap small tasks. $0.16/$0.64 per 1M.
- **zembed-1** (ZeroEntropy) — embedding, 1280-dim matryoshka. Separate API.
- **zerank-2** (ZeroEntropy) — cross-encoder reranker. Separate API.

A fresh `alembic upgrade head` ends pruned (0216 runs last, repoints any drift → mini, deletes haiku/full).

## Quality (last full run 2026-06-15b, key restored)
- **41/42 no-fail (42/42 thực chất đúng — 1 câu harness gắn REFUSE nhầm)**, **HALLU = 0/6 sacred**, 0 pipeline-layer failures.
- xe 14/14 · spa 18/18 · legal 10/10. Booking multi-turn OK (slot capture + grounded confirm); refuse bẫy đúng.
- Report: `reports/QA_42Q_REPORT_20260615b.md` + JSON detail per bot.

## Incident 2026-06-15 (resolved)
- OpenAI key cũ (.env `sk-proj-2Q`) mất scope `model.request` (restricted trên dashboard sau hóa đơn $200) → mọi LLM call 400 → circuit breaker OPEN → run rỗng. KHÔNG phải lỗi code (model resolve đúng gpt-4.1-mini).
- Fix: thay key mới `sk-proj-wXsi7q` (có permission + quota) vào `.env` → restart (reset breaker) → 42/42 lại, HALLU=0.
- Bài học: set **Project → Model access = chỉ mini+nano** + **Budget cap** trên OpenAI để chặn cost surprise; key cần **Models: Write** (đừng restrict nhầm thành None).

## Cost profile (verified, gpt-4.1-mini)
- **Chat ~$0.006/câu** blended (factoid ~4 calls/$0.004 · aggregation ~12 calls/$0.012 · cache hit $0).
- **Upload ~$0.013/ingest** (CR enrichment, context capped 100 tokens — cheap).
- Factoid (70% traffic) already lean: multi_query + rewrite intent-gated OFF (`factoid:False`); grounding sync XOR async (not double).
- The $200 OpenAI spend 1→13/6 = one-time DEV/EVAL (overnight 3-model matrix + DeepEval×12bot on 8-10/6 + gpt-4.1-FULL window 11-13/6, since reverted to mini). NOT the current flow.
- ⚠️ Cost NOT persisted to DB (request_logs wiped on bot-delete CASCADE) — exact per-day $ only on OpenAI dashboard. Fix = durable billing ledger (pending, optional).

## Recent changes (this phase, working tree)
- **Latency fix**: async grounding judge runs on isolated provider semaphore lane (`{code}::background`, cap 4) so it can't starve foreground generate under burst. Post-burst factoid 26.8s→3.3s. (`dynamic_litellm_router.py` + `_10_rbac.py` constant + `query_graph.py` `background=True`). +5 tests, suite green.
- **UI workspace fix**: read-path endpoints resolve a bot by `find_by_3key_unique` when no `workspace_id` given (unique-match), so the demo UI lists docs without passing the slug. Fixes "3 bot trống" regression. Guard `test_route_workspace_scope_pin.py`.
- **Perf (earlier)**: per-chunk narrate parallelized (gather+semaphore) — 9-doc ingest 6-7min→57s. CR prompt-cache warm 75%→99%.
- **Model cleanup**: haiku/full/gpt-5 removed (DB + constants + alembic 0216); narrate/slot defaults → gpt-4.1-mini.
- **Source cleanup**: removed reports/ var/parsed_md/ plans/ scratch/ test_results/ + dead scripts (stategov/haiku/wave pilots) + old docs (academic-papers, medispa-sysprompt drafts). Git history reset.

## Pending (optional, not blocking)
- Cost-persistence: durable billing table (no CASCADE) → auditable per-day/bot/câu.
- Cost trim at scale: cap multi_query 5→3 for aggregation; reduce context re-send across grade/grounding/generate.
- xe booking-intent: sysprompt guide "muốn mua" → ask slots instead of refuse (Tier-A owner config).
- Doc curation: docs/master kept; rewrite fresh as the new phase stabilizes.

## Demo bots (test harness — FE is test-only)
- `test-spa-id` (ws spa, 4 docs/134 chunks) · `chinh-sach-xe` (ws xe, 4/486) · `thong-tu-09-2020-tt-nhnn` (ws legal, 1/80). Reseed via `POST /api/ragbot/test/reinit-bots?bot=all&wipe=true`.
