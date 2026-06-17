# STATE SNAPSHOT — Ragbot (fresh phase 2026-06-14)

> Always-updated current state. Git history was reset on 2026-06-14 (fresh start);
> commit-SHA anchors no longer apply — this file is the source of truth.

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
