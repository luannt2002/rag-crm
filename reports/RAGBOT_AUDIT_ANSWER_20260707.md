# Ragbot — Audit Answer (evidence-based)

> Trả lời `docs/RAGBOT_AUDIT_QUESTIONS.md` theo quy tắc: mỗi claim có `file:line` / SQL result / lệnh chạy — hoặc ghi rõ **CHƯA VERIFY**.
> **Ngày**: 2026-07-07 · **Branch**: `fix-260623-ingest-expert` · **Alembic head**: `stats_brand_csx_260707`
> **Phương pháp**: 5 subagent map call-chain (ingest/query/chunk/guard/cost-rls-scripts) + read-only SQL probe + lệnh recon trực tiếp. KHÔNG đọc README làm nguồn.
> **Redaction**: brand khách hàng thay bằng `<brand-A>`/`<brand-B>` (domain-neutral rule). `innocom` = platform gateway operator (giữ nguyên).

---

## Phần 0 — Executive truth

### Q0.1 — Trạng thái: **MỘT PHẦN** (LIVE, HALLU-số ~0, Coverage bộ-khó chưa đạt)
- DB hiện có `documents.state`: **active=25, failed=2** (SQL `group by state`) → upload→embed→active hoạt động thật. Corpus stamp `6e6c0774`.
- **Lần verify gần nhất**: 2026-07-06, agent-graded DB-verified, 200 câu, block ON — anchor `specs/002-deepdebug-luannt/evidence/step20_full_detail_verdicts.json`, báo cáo `reports/TRUTH_STATE_REPORT_20260706.md`:
  - **gate100 (câu thường) = 91/100**
  - **luannt100b (bộ BẪY khó) = 74/100** (~77-78 sau chain-fix 002-J)
  - **HALLU bịa-SỐ ≈ 0** (numeric-fidelity block chặn fabricate).
- 35 câu sai phân tầng root-cause: **retrieve 13 · grounding-phi-số 11 · ingest 5 · block-gate 3 · coreference 3**. Đòn bẩy #1 = **RETRIEVE** (data có, topK bỏ sót), KHÔNG phải anti-HALLU.

### Q0.2 — Source of truth
- `alembic current` = **`stats_brand_csx_260707`** (= head, single, không lệch).
- ⚠️ **DRIFT**: git status có 3 alembic untracked (`20260707_stats_name_by_shape…`, `…brand_scope_observe…`, `…stats_brand_aware…`) + `src/ragbot/shared/{brand_scope,table_shape,numeric_fidelity}.py` **chưa commit** — DB đã migrate tới head nhưng code sinh ra chưa vào git.
- Commit pipeline gần nhất: `b5fc6cb fix(guard): feed conversation history to numeric-fidelity grounding (002-J chain)`. HEAD=`db7ee52`.
- File truth-of-record: **`STATE_SNAPSHOT.md`** (always-updated). Khi README ↔ code mâu thuẫn → code + STATE thắng; README có claim lỗi thời.

### Q0.3 — Entry point runtime
- **1 process API**: `python -m ragbot.main` → `main.py:24` `uvicorn.run(...)` (uvloop + httptools).
- **Workers EMBEDDED in-process** (không tách process): `settings.app.embed_workers_enabled` default **True** (`config/settings.py:124`), spawn tại `interfaces/http/app.py:433` trong lifespan.
- `docker compose up` services (`docker-compose.yml`): `postgres` · `redis` · `api` (uvicorn) · `infinity` (self-host embed) · `tei-reranker` (self-host rerank) + volume `pg-data`,`redis-data`. Không Qdrant, không OCR service riêng.

### Q0.4 — Test suite (đã CHẠY)
- `pytest --collect-only -q` → **7356 tests collected, 12 errors** (KHÔNG phải 5926 như README — README lỗi thời).
- 829 test file: **755 unit · 62 integration · 1 golden · 5 eval · 1 scenarios**.
- 12 collection error = pydantic `ValidationError` load `.env` ở ~12 file (`test_runtime_db_role_check.py`, `test_streaming_upload.py`, `test_embedded_workers.py`…) — do hardcode `.env` path, không phải logic fail.
- E2E/integration thật: `test_chat_production_endpoint.py`, `test_bot_lifecycle_purge_e2e.py`, `test_ingest_embed_failure_no_orphan.py`, `test_chat_async_worker.py`, `test_tenant_rl_e2e.py`. **Pass-rate hôm nay: CHƯA VERIFY.**

### Q0.5 — External deps (từ `.env` keys thật)
| Service | Key | Vai trò | Thiếu → dừng ở |
|---|---|---|---|
| PostgreSQL+pgvector | `DATABASE_URL` (role `postgres`, **superuser**) | store + vector + BM25 tsvector | mọi thứ |
| Redis | `REDIS_URL` | stream ingest + L1 cache | ingest async + cache |
| **ZeroEntropy** | `ZEROENTROPY_EMBEDDING_API_KEY`, `ZEROENTROPY_RERANKER_API_KEY` | **embed=zembed-1 (dim 1280)** + **rerank=zerank-2** | embed/store U7 + rerank |
| OpenAI | `OPENAI_API_KEY` | grounding judge / aux | grounding |
| innocom gateway | `INNOCOM_API_KEY` | answer-LLM (per-bot binding) | generate |
| Jina/Cohere/LMStudio | `JINA_*`,`COHERE_*`,`LMSTUDIO_*` | alternatives (không operative) | — |
| OCR | `OCR_PROVIDER` (Kreuzberg, in-process) | parse pdf/pptx/html | parse U2 |

Không có `MISTRAL_*` / `QDRANT_*` → pgvector + Kreuzberg (trap T2/T3 đúng). Embedding operative = **zembed-1 1280**, KHÔNG phải Jina 1024.

---

## Ground-truth DB (read-only SQL, dùng làm evidence)

| Fact | Đo được | Ý nghĩa |
|---|---|---|
| DB role runtime | `current_user=postgres`, `is_super=True` | **RLS INERT** (superuser bypass FORCE RLS) |
| RLS tables | **24 bảng** `relforcerowsecurity=true` | policy có nhưng chưa enforce |
| Embedding | `embedding_model=zembed-1`, `provider=zeroentropy`, `dim=1280` | vs audit giả định 1024 |
| Reranker | `zerank-2`, filter `cliff` (floor 0.2, gap 0.5, min_keep 3) | — |
| token_ledger | TỒN TẠI — 28 cột (`cost_usd numeric(14,8)`, unit prices, cached_tokens, purpose, trace_id) | Q9.1=YES |
| semantic_cache | tồn tại — `query_embedding` vector, `corpus_version`, `query_hash`, `expires_at` | dim 1280 |
| guardrail_rules | **12 rows** (đo thật) | KHÔNG rỗng — trap "not seeded" lỗi thời |
| documents | `raw_content`, `current_step`, `progress_percent`, `chunks_total/processed`, `summary_json`, `deleted_at` | worker đọc raw_content; progress polling |
| document_chunks | `embedding` (vector 1280), `search_vector` (tsvector BM25), `parent_chunk_id`, `content_segmented`, `chunk_type` | BM25=`search_vector`; parent/child |
| doc states | active=25, failed=2 | terminal states thật |

---

## Phần 1 — Tổng quan
- **Q1.1** 2 LangGraph: ingest (`DocumentService.ingest`, U1-U7+finalize) + query (`query_graph.py`, **3071 dòng, 21 node**).
- **Q1.2** Giao tiếp qua `document_chunks` (DB) + Redis Streams `ragbot:document.uploaded.v1`, không direct call.
- **Q1.4** Port/Adapter/Registry/DI thật: reranker/embedder/narrate/parser đều Port + adapter-per-file + registry + resolver DI (bootstrap).
- **Q1.5** ⚠️ **Brand literal CÓ trong src (comment)**: `shared/table_shape.py:23,108` (`<brand-A>`,`<brand-B>`,"Lốp"), `shared/numeric_fidelity.py:154` (`<brand-A>`), `infrastructure/.../*.py` ("innocom" gateway). Đây là 3 file **untracked chưa commit** — vi phạm domain-neutral ở comment, cần scrub trước khi commit.
- **Q1.6** Headless BE; primary = `POST /documents/create` + `/chat`. UI test giữ view-only.

---

## Phần 2 — Identity & Multi-tenant
| ID | Sự thật |
|---|---|
| Q2.1 | `BotRegistryService.lookup(record_tenant_id, workspace_id, bot_id, channel_type)` `application/services/bot_registry_service.py:103`, Redis-first + single-flight + DB fallback |
| Q2.3 | RLS baseline 20 FORCE (`squashed_baseline.sql`, 21 `CREATE POLICY`); migration sau thêm → **đo live 24 bảng FORCE** |
| Q2.4 | ⚠️ **App connect = `postgres` SUPERUSER**; `.env` KHÔNG có `DATABASE_URL_APP`; `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` cho phép fallback (`engine.py:67-81`, WARNING `app_dsn_superuser_fallback`) |
| Q2.5/7/8 | **RLS INERT** — superuser bypass mọi FORCE policy; isolation hiện dựa app-filter (`SET LOCAL app.tenant_id` bị superuser ignore). `test_tenant_rl_e2e.py` tồn tại — CHƯA VERIFY chạy hôm nay. **trap T6=TRUE** |
| Q2.6 | Cross-tenant workers dùng system/BYPASSRLS factory (`embedded_workers.py`) |

---

## Phần 3 — Ingest / Upload

**⚠️ 2 luồng ingest LIVE, cả hai emit `document.uploaded.v1`:**
- A. Canonical: `POST /documents/create` → `interfaces/http/routes/documents.py:91`, **HTTP 202**.
- B. Test/demo ("Action 1/2"): `POST /test/bots/{bot_id}/{channel_type}/documents` → `test_chat/document_routes.py:171`.
- Hội tụ: outbox → `outbox_publisher` → Redis `ragbot:document.uploaded.v1` → `document_worker` → `DocumentService.ingest()` → `_stage_finalize`.

| ID | Sự thật |
|---|---|
| Q3.1 | 202 Accepted (`documents.py:94`), body `status="queued"`. ⚠️ docstring nói replay=200 nhưng decorator ép 202 mọi path → thực tế 202 |
| Q3.2 | Action-1 sync: `IngestDocumentUseCase.execute` (`use_cases/ingest_document.py:54`) INSERT `documents` (state=DRAFT, hash="pending") + `jobs` + outbox. <1s |
| Q3.3 | Consumer `handle_document_uploaded` (`document_worker.py:138`); stream `SUBJECT_DOCUMENT_UPLOADED="document.uploaded.v1"` → runtime `ragbot:document.uploaded.v1` |
| Q3.4 | **BOTH, branch theo scheme**: `local://` → đọc `raw_content` DB (`document_worker.py:350`); **http(s) → LUÔN refetch** source_url (`document_worker.py:443`). ⚠️ comment "DO NOT refetch" :332 mâu thuẫn code |
| Q3.5 | Runtime raw-SQL: **DRAFT → active/failed**. Typed domain machine (`types.py:64`) bị ingest bypass |
| Q3.6 | `run_recovery_loop` (`document_recovery_worker.py:363`): interval **300s**, stuck **900s**, cooldown **3600s**; quét DRAFT-quá-hạn + active-0-chunk |
| Q3.7 | `sha256(content)` (`ingest_core.py:420`); live same-hash+không reindex → DuplicateError→400. ⚠️ canonical async = `is_reindex=True` → **dedup SKIP** (`ingest_core.py:429`) |
| Q3.8 | `MAX_DOCUMENT_CONTENT_CHARS=500_000` (`_03…:78`), enforce `ingest_core.py:377`. Inline→**400**; async→failed; funnel-B pre-queue(2M)→**413** |

**U-stage map** (trong `DocumentService.ingest`, `ingest_core.py:176`):
| Stage | Function (file:line) | LIVE? |
|---|---|---|
| U0 identity/tenant | `_record_tenant` (documents.py:83) | ✅ |
| U0.5 bot resolve 4-key | `registry.lookup` (documents.py:72) | ✅ |
| U1 validate | ingest_core.py:274 | ✅ |
| source allowlist | ingest_core.py:296 | OFF default |
| U2 parse | `_route_through_parser` (ingest_core.py:304) | ✅ khi có raw_bytes |
| U3 clean | ingest_stages.py:274 | ✅ |
| U4 chunk | ingest_stages.py:392 | ✅ |
| U5 enrich (Contextual Retrieval) | ingest_stages_enrich.py:120 | **OFF default** |
| U6 vn_segment | ingest_stages.py:954 | ✅ default ON, gated VI |
| narrate-then-embed | document_worker.py:547 | **OFF** (seeded 0230) |
| U7 embed+store | ingest_stages_store.py:154 | ✅ |
| finalize | ingest_stages_final.py:219 | ✅ |

| ID | Sự thật |
|---|---|
| Q3.18 | `_decide_ingest_state` (ingest_stages_final.py:188): embedded<=0→failed; null_non_parent<=0→active; else coverage=embedded/(embedded+null) ≥ **0.8**→active. ⚠️ chunks_null>0 KHÔNG tự động failed |
| Q3.19 | `GET /test/bots/.../documents` (document_routes.py:30): `state,status,ready,current_step,progress_percent,chunks_total,chunks_processed,eta_seconds` |
| Q3.21 | Error ở `jobs.error` (document_worker.py:706) + `DocumentFailed` outbox. **`documents` KHÔNG có cột error** → user thấy THAT failed, không thấy WHY từ documents endpoint |

---

## Phần 4 — Parse & Multi-format
- Q4.1 Format qua registry `detect_parser`; parser: kreuzberg (pdf/pptx/html), dedicated docx/xlsx/google_sheets/csv/md, OCR fallback.
- Q4.2 Kreuzberg = in-process lib (không service docker); `OCR_PROVIDER` env. Không Mistral/Qdrant (T2/T3 ✅).
- Q4.3/4.4 `scripts/check_happy_case.py` (code-only) + `scripts/normalize_to_happy_case.py`.
- Q4.7 `adapchunk_block_pipeline_enabled` ON nhưng **registry parser KHÔNG emit block list** → no-op; U2 output = flat structured-markdown text. Chỉ OCR path build typed blocks.

---

## Phần 5 — Chunking / AdapChunk
> Code THẬT ở `src/ragbot/shared/chunking/` (KHÔNG phải `document_service/chunking` như spec).

| ID | Sự thật |
|---|---|
| Q5.1 | `smart_chunk()` `shared/chunking/__init__.py:412`. Strategies: `table_csv`, `table_dual_index`, `recursive`(default), `hdt`, `semantic`(**lexical**), `proposition`(**rule-based, không LLM**), `hybrid` |
| Q5.2 | `analyze_document(text)` nhận **flat text** (analyze.py:215/221); block-aware `analyze_document_blocks` chỉ chạy khi `ctx.blocks` non-empty (OCR-only) |
| Q5.3 | `select_strategy()` **RULE-BASED** (analyze.py:407) — weighted scorer + CSV/VN-legal fast-path, **không LLM** → **T1 ✅** |
| Q5.4 | `apply_cross_check()` (analyze.py:576): 5 rule priority, default **ON** (`DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED=True`) |
| Q5.5 | Field `original_strategy` vs `override_strategy`, log `adapchunk_l5_strategy_overridden` (__init__.py:466-480) |
| Q5.6 | `DEFAULT_CHUNK_SIZE=1024`, `DEFAULT_CHUNK_OVERLAP=128`, `DEFAULT_PARENT_CHUNK_SIZE=1024`, `DEFAULT_CHILD_CHUNK_SIZE=256`, `DEFAULT_CHILD_CHUNK_OVERLAP=50` (không phải 128 cho child) |
| Q5.7/5.8 | TABLE giữ nguyên path `recursive` (strategies.py:140), NHƯNG **HDT path CẮT ĐÔI bảng** khi section>2×chunk_size (splitter không table-aware, strategies.py:338-349). FORMULA/IMAGE atomic-protect **OFF default** |
| Q5.9 | Breadcrumb inline `[H1 > H2 > H3]\n<content>` (strategies.py:309); VN legal `[Chương N > Mục M > Điều K. title]` |
| Q5.10 | `promote_vn_hierarchical_headings()` (vn_structural.py:288), fire khi ≥3 marker |
| Q5.11 | **Parent no-embedding**: `"emb": None` (ingest_stages_store.py:815), filter khỏi embed-set (:196) → **T9 ✅** |
| Q5.13 | **Block pipeline NO-OP confirmed**: flag ON (`DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True`) nhưng `smart_chunk_atomic(blocks)` (__init__.py:653) **0 caller**; chunk production = text `smart_chunk(content)` (ingest_stages.py:770) |
| Q5.14 | Narrate-then-embed wired U7 (ingest_stages_store.py:291) nhưng **OFF default** (`DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`) |
| Q5.15 | Proposition strategy = rule-based sentence/clause split, **không LLM per section** (strategies.py:631-694) |
| Q5.16 | Semantic strategy = **lexical** (SequenceMatcher+Jaccard, strategies.py:365); embedding-cosine variant `_chunk_semantic_embed` = **dead code** (registry commented out) |

---

## Phần 6 — Embed & Store
- Q6.1 embedding operative = **zembed-1 / zeroentropy / dim 1280** (system_config SQL).
- Q6.4 BM25 = cột `search_vector` (tsvector) trên `document_chunks`.
- Q6.2 parent-no-embed verify: `emb=None` + filter (ingest_stages_store.py:196,815); `n_null_embedding` logged (:1090).

---

## Phần 7 — Query / Retrieval
`query_graph.py` = **3071 dòng, 21 node** (add_node :2903-2944). Runtime factoid: `guard_input → cache_check_and_understand_parallel → understand_query → query_complexity → retrieve → rerank → mmr_dedup → neighbor_expand(no-op) → grade → generate → critique_parse(no-op) → guard_output → persist`.

| ID | Sự thật |
|---|---|
| Q7.3 | Router `nodes/routing.py`: rewrite skip factoid/greeting/oos (:146); decompose chỉ multi_hop+comparison (:111); reflect skip khi off (:211); 0-chunk/stats→generate (:219) |
| Q7.4 | `check_cache.py:27`; hash `sha256(query.strip().lower())` (semantic_cache.py:137); threshold **0.97**; row TTL 3600s; hit short-circuit→persist (routing.py:56); multi-turn skip cache |
| Q7.5 | understand=**LLM** (understand.py:58) + heuristic pre-pass; query_complexity=**heuristic/regex, không LLM** (query_complexity.py:96) |
| Q7.6 | temp0 enforce router `_resolve_effective_temperature` (dynamic_litellm_router.py:331-351). ⚠️ **caveat**: structured-output path (query_graph.py:1433) bypass force-0, dùng temp binding |
| Q7.7 | `DEFAULT_TOP_K=20`; per-intent (factoid 15, comparison 25, multi_hop 30, aggregation 40). RRF k=60. Không có 2 hằng dense-vs-BM25 tách (CHƯA VERIFY) |
| Q7.8 | graph_retrieve **OFF default** (`graph_rag_mode="disabled"`) |
| Q7.10 | rerank provider default `jina`, operative binding = **zerank-2**. Cliff: drop<floor, cắt gap `(prev-curr)/prev>ratio`, giữ≥min_keep (retrieval_filter.py:94-162) |
| Q7.11 | mmr lambda **0.7**, threshold **0.98** (nâng 0.88→0.98 2026-07-04 = fix root-cause D 002), min_keep 3 |
| Q7.12 | neighbor_expand **OFF default** |
| Q7.13 | grade CRAG 3-state; retry max **1**; skip-retry khi top≥0.7; total iteration cap **8** |
| Q7.14 | speculative MQ **OFF default**; ở query_graph.py:1857 (không phải retrieve.py:642); KHÔNG drop decompose (protected sub_queries≥2) |
| Q7.15 | adaptive_context trong **generate** (generate.py:477), **OFF default** |
| Q7.16 | `build_vn_structural_like_clauses` (vn_structural.py:262) match `[Chương>Mục>Điều]`, boundary-safe |
| Q7.18 | 0-chunk → route generate → **refuse short-circuit KHÔNG gọi LLM** (generate.py:318-363) trừ chitchat/action |

---

## Phần 8 — Generate & Anti-HALLU (Sacred)
| ID | Sự thật |
|---|---|
| Q8.1 | `DEFAULT_GENERATION_TEMPERATURE=0.0` (_10_rbac.py:200) cho purpose="generation". ⚠️ CÓ override per-bot `pipeline_config["generation_temperature"]` — default không phải ceiling |
| Q8.2 | **App KHÔNG inject** (UPHELD). Prompt = `bot_system_prompt` (generate.py:678) + data envelope. Ngoại lệ = governed `SysPromptAssembler` APPEND `language_packs[locale].sysprompt_default_rules` (sysprompt_assembler.py:141), opt-out `sysprompt_rules_disabled` |
| Q8.3 | **App KHÔNG override prose** (UPHELD). Answer verbatim (generate.py:1071). guard_output.py:250 ghi rõ không math_lockdown. ⚠️ CÓ substitute-toàn-answer-thành-refusal (owner `oos_answer_template`) khi guardrail BLOCK — thay cả câu, không sửa giữa câu, mostly default-observe |
| Q8.4 | `bots.oos_answer_template` String(1000) (models.py:222), 7-tier resolver, Tier7 default="". Zero-chunk refuse KHÔNG gọi LLM (generate.py:339-363) |
| Q8.5 | **Citation validation TỒN TẠI**: allowed-set graded (generate.py:604-608), drop citation không ∈ set (:816-829), metric `citation_validation_fail_total` |
| Q8.6 | guard_output: numeric-fidelity, cross-row misattribution, brand-scope gate, system-leak shingle (sha256 24-word), regex guards, grounding judge. **guardrail_rules=12 rows LIVE (đo SQL)** + 12 default pattern fallback code |
| Q8.7 | Grounding judge **XOR** (sync OR async, không double; guard_output.py:346-353). Model=`resolve_runtime(purpose="grounding")` → innocom openai/claude (revived migration 20260626). async OFF default, threshold 0.3, confirmed-action observe, fail_closed |
| Q8.8 | critique_parse (Self-RAG) **OFF default** |
| Q8.9 | reflect max=**1** (`DEFAULT_MAX_REFLECT_RETRIES`); total cap 8 |
| Q8.10-12 | numeric_fidelity.py deterministic classify grounded/derived/unsupported + cross-row; **observe default, block per-bot opt-in** (bật cho chinh-sach-xe, f22a808); 002-J feed conversation history. ⚠️ **Anti-HALLU 4-type KHÔNG có taxonomy code** — chỉ track `n_unsupported`(≈fabricate)+`n_misattributed`(≈conflate); misinterpret/extrapolate = **spec-only** |
| Q8.13 | False-refuse: nhiều câu sai eval là retrieve-miss→refuse oan (13/35), không phải bịa |

---

## Phần 9 — Cost-Log / Token Ledger
| ID | Sự thật |
|---|---|
| Q9.1 | `token_ledger` TỒN TẠI (`squashed_baseline.sql:842`), 28 cột gồm `cost_usd`, unit prices, cached_tokens, purpose, trace_id |
| Q9.2 | ⚠️ **COST LEAK**: LLM emit (dynamic_litellm_router.py:837). Embed/rerank operative (zeroentropy/voyage/bkai) **KHÔNG emit** — chỉ Jina (retired) gọi `emit_aux_usage` → token embed/rerank hiện tại không log |
| Q9.3 | `request_logs` 1 row/request (create pipeline.py:301, finalize :458); `request_steps` 1 row/step |
| Q9.4 | `pipeline_step_log` **KHÔNG TỒN TẠI** — dùng `request_steps` |
| Q9.5 | Rollup `stats_bot_daily/tenant/platform` **KHÔNG TỒN TẠI** — analytics on-the-fly SQL trên request_logs/model_invocations |
| Q9.7 | `plan_limits.monthly_token_cap`: cột có nhưng **hard-enforce INERT** — `check_token_cap` (tenant_token_meter.py:276) **0 caller**; router preflight default-OFF no-op; chỉ soft alerter |
| Q9.8 | `model_pricing` table **KHÔNG TỒN TẠI** — pricing ở cột `ai_models.*_price_per_1k_usd`. cost_usd tính lúc insert cho LLM; embed/rerank không tính cost |

---

## Phần 10 — Workers & Async
- Q10.1 **5 embedded workers** (embedded_workers.py): `run_embedded_document_consumer`(:59), `run_embedded_outbox_publisher`(:100), `run_embedded_recovery_worker`(:121), `run_embedded_cost_cap_alerter`(:140), `run_embedded_cache_purge`(:171).
- Q10.2 `APP_EMBED_WORKERS_ENABLED` default **True**.
- Q10.3 outbox DDL `squashed_baseline.sql:557`; exactly-once `FOR UPDATE SKIP LOCKED` (outbox_repository.py:62).

---

## Phần 11 — Cache
- Q11.1 L1 key `sha256(query.strip().lower())` + scope `record_bot_id,record_tenant_id,bot_version,corpus_version,expires_at`. system_prompt/oos KHÔNG literal trong key NHƯNG `bot_version=_compute_bot_cache_version(system_prompt,oos)` (persist.py:182) → đổi prompt ⇒ bust (**T10=YES**). corpus_version cũng trong key.
- Q11.2 L2 threshold **0.97**, bảng `semantic_cache`, dim **1280** (widened 1024→1280, alembic 20260626) → **T14 "1024" lỗi thời**.
- Q11.3 GC = `run_embedded_cache_purge` DELETE `expires_at<now()-grace` trên system factory.

---

## Phần 12 — Evaluation & Verify scripts
| ID | Sự thật |
|---|---|
| Q12.1 | ~60 script. `verify_*` (11: per-layer L1→L7, query Q1→Q8, DB-grounded), `loadtest_*` (18), `eval_*`/`run_conversational_eval.py` |
| Q12.2 | `verify_happy_case_pipeline.py` last GREEN 2026-06-22 (9/9). CHƯA re-run hôm nay |
| Q12.4 | `verify_answer_quality.py` 11/11×3 ghi nhận 2026-06-22. CHƯA reproduce hôm nay |
| Q12.5 | `loadtest_graded.py` last run 2026-07-06: gate100 **91/100**, luannt100b **74/100**, HALLU bịa-số ≈0 |
| Q12.7 | full-fleet automated bị chặn TPM/503 innocom → workaround batch mode (`LOADTEST_BATCH_MODE.md`) |
| Q12.8-10 | Intrinsic SC/CC (`score_chunks_intrinsic.py`), RAGAS (`eval_ragas*.py`), layer-attribution (`loadtest_hard_forensic.py`) |

**8-step DEBUG mapping**: PARSE→`verify_adapchunk_layers.py`; CHUNK→same; EMBED→`verify_rag_health.py`; RETRIEVE/GENERATE/GUARD→`verify_query_flow.py`+`verify_answer_quality.py`; SCORE→`loadtest_graded.py`.

---

## Phần 13 — Database & Migration
- Q13.1 baseline `20260618_squash_baseline.py`+`squashed_baseline.sql`.
- Q13.2 head thực = **`stats_brand_csx_260707`** (không phải rls_..._20260619).
- Q13.3 documents/document_chunks columns (xem bảng ground-truth).
- Q13.5 stale 1536/1024 **đã fix** → zembed-1 1280.
- Q13.6 24 bảng FORCE RLS (đo live).

---

## Phần 14 — Config & Zero-hardcode
- Q14.1 Resolve chain: `pipeline_config`(per-bot plan_limits) → `system_config` → `constants`; choke-point `_pcfg(state, key, DEFAULT)`.
- Q14.3 Per-bot binding = `bot_model_bindings`; resolver fallback system_config SSoT (fix 2026-06-30).
- Q14.4 `DEFAULT_DETERMINISTIC_LLM_PURPOSES` = decompose/rewrite/multi_query/condense/routing/understand_query/grade/grounding/reflect (_10_rbac.py:211).

---

## Phần 15 — Gap & Technical Debt (honest, đo được)
1. **RLS inert** — app=postgres superuser, `DATABASE_URL_APP` chưa cấp, `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`. Blocked bởi ops chưa provision role `ragbot_app` NOBYPASSRLS.
2. **Cost leak** — embed/rerank operative (zeroentropy/voyage/bkai) KHÔNG emit token_ledger; không có model_pricing table.
3. **Token cap không enforce** — `check_token_cap` 0 caller (dead); chỉ soft alerter.
4. **Block pipeline no-op** — smart_chunk_atomic 0 caller; registry parser không emit blocks.
5. **Narrate-then-embed OFF** — seeded OFF alembic 0230.
6. **guard_output substitution** — thay cả answer bằng owner refusal khi block (không sửa prose, nhưng thay cả câu).
7. **Uncommitted drift** — DB migrate tới `stats_brand_csx_260707` nhưng 3 alembic + brand_scope/table_shape/numeric_fidelity chưa vào git.
8. **HDT path cắt đôi bảng** khi section>2×chunk_size.
9. **README lỗi thời** — "5926 tests", "jina 1024".

---

## Phần 16 — Trap questions (verdict theo code)
| ID | Bẫy | Verdict thật |
|----|-----|----|
| T1 | LLM Strategy Selector | ❌ **rule-based** (analyze.py:407) |
| T2 | Qdrant DSN | ❌ pgvector |
| T3 | Mistral OCR key | ❌ Kreuzberg in-process |
| T4 | Upload xong chat ngay? | ❌ async → đợi active |
| T5 | TABLE cắt đôi fixed? | ⚠️ một phần — HDT path vẫn cắt; block pipeline no-op |
| T6 | RLS enforce DB-level? | ❌ **INERT** (superuser bypass) |
| T7 | 5926 tests E2E LLM? | 7356 collect; 62 integration file, ít E2E-LLM thật |
| T8 | App inject "Tôi không biết"? | ❌ emit `bots.oos_answer_template` (default="") |
| T9 | Parent có embedding? | ❌ `emb=None` |
| T10 | Đổi system_prompt bust cache? | ✅ qua bot_version |
| T11 | ZeroEntropy còn dùng? | ✅ **đang dùng** zembed-1 1280 + zerank-2 |
| T12 | Chat khi DRAFT? | ❌ chỉ chunks doc active |
| T13 | Grounding sync VÀ async? | ❌ **XOR** |
| T14 | semantic_cache dim? | **1280** |
| T15 | Proposition mọi doc? | ❌ chỉ proposition/hybrid; rule-based không LLM |

---

## Phụ lục — Template filled
```
E2E works: PARTIAL (LIVE, 25 docs active; HALLU-số ~0; Coverage bộ-khó chưa đạt — retrieve miss)
Last verified: 2026-07-06 loadtest_graded.py → gate100 91/100, luannt100b 74/100 (~77-78 post-fix)
Alembic head: stats_brand_csx_260707 (⚠️ 3 alembic files chưa commit)
Tests: 7356 collect (12 err), 755 unit / 62 integration — pass-rate CHƯA VERIFY hôm nay

AdapChunk: selector RULE-BASED · block pipeline NO-OP · narrate OFF · atomic TABLE recursive✅/HDT⚠️
Multi-tenant: RLS policies có nhưng INERT (postgres superuser) · leak test tồn tại, chưa chạy hôm nay
Anti-HALLU: no-inject UPHELD · no-override-prose UPHELD (substitute-to-refusal owner text) · grounding XOR
Cost-Log: token_ledger YES · leak embed/rerank · rollup on-the-fly · token cap DEAD
Demo 15': python -m ragbot.main → POST /documents/create → poll active → POST /chat
  Blockers: .env đủ key; RLS inert (single-tenant demo OK); full-fleet score TPM/503 innocom
```

## README vs code — mismatches
| Claim | Reality | Evidence |
|---|---|---|
| 5926 tests | 7356 collect, 12 err | `pytest --collect-only` |
| jina-v3 1024 | zembed-1 1280 | system_config SQL |
| RLS enforced | INERT (superuser) | `current_user=postgres is_super=True` |
| block pipeline full | ON nhưng NO-OP | smart_chunk_atomic 0 caller |
| token_ledger logs all | embed/rerank leak | chỉ Jina emit_aux_usage |
| head rls_..._20260619 | stats_brand_csx_260707 | `alembic current` |
| semantic_cache 1024 | 1280 | alembic 20260626 |

---

**Bottom line**: Ragbot LIVE, HALLU-bịa-số ~0 (sacred giữ). Khung expert thật (Port/DI, 2 graph, 21 node, RLS policies, token_ledger, outbox exactly-once). 5 gap đo được: (1) RLS inert do superuser DSN, (2) cost-leak embed/rerank, (3) token-cap dead code, (4) block-pipeline no-op, (5) Coverage bộ-khó do retrieve-miss (đòn bẩy #1). Không gap nào là "bịa số".
