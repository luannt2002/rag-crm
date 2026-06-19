# Ragbot — Hiểu Toàn Dự Án + Lộ Trình Expert RAG (5 tiêu chí) · 2026-06-18

> Tổng hợp từ **6 agent read-only** deep-read toàn bộ 9 subsystem (mọi claim `file:line`) + đối chiếu
> research chunking (bản tổng hợp VN + Firecrawl 2026 + EvidentlyAI + AdapChunk/Ekimetrics + UX case study).
> Nhãn: **SỰ THẬT** = verified trong src; **GIẢ THUYẾT** = chưa đo trên corpus mình.
>
> ⚠️ Coverage honesty (rule #0): 6 agent đọc **đại diện sâu** mọi subsystem với evidence, KHÔNG phải
> "đọc literal từng dòng" của ~300k LOC. Đủ để hiểu kiến trúc + tìm gốc rễ; chưa phải line-by-line audit.

---

## 0. LUẬN ĐIỂM TRUNG TÂM (đọc cái này trước)

**Khung đã expert-grade. "Trí thông minh" ĐÃ được code — nhưng phần lớn bị TẮT bằng feature-flag `DEFAULT=False`, hoặc nối vào input rỗng.** Dự án KHÔNG cần rewrite; cần **bật đúng cái + nối đúng dây + sửa 2-3 điểm routing sai tầng**. Đây là chiến lược EVOLVE (strangler-fig), khớp ràng buộc CLAUDE.md.

Bằng chứng cô đặc (mỗi cái 1 agent verify):
- **Block-native chunking**: `smart_chunk_atomic(list[Block])` code đầy đủ nhưng `parsed_blocks` bị **hardcode `= []`** ([ingest_stages.py:501] theo agent B) → flag bật mà chạy y hệt path text-flatten. Fix = 1 dòng `parsed_blocks = ctx.blocks or []`.
- **Cascade routing, Async grounding, Adaptive context, Ekimetrics 5-metric selector, Narrate-then-embed, Late-chunking-sliding, HyDE, MQ-complexity-gate** — TẤT CẢ đã implement, `DEFAULT=False`, chờ A/B per-bot (agent B/C).
- **Routing giá** đi **regex hardcode tiếng Việt** thay vì LLM intent+entity → đây là chỗ "just code" gây BUG-1 conflate (agent A).

→ Trả lời thẳng câu hỏi "làm sao thành Expert RAG đủ 5 tiêu chí": **không phải viết lại — mà là một chuỗi EVOLVE có đo lường**, dưới đây.

---

## 1. DỰ ÁN LÀ GÌ — Kiến trúc 9 subsystem (đã verify)

Stack: Python 3.12 · FastAPI · LangGraph (StateGraph) · pgvector · Redis Streams · structlog · Docker Compose. Hexagonal/DDD · Port+Registry+NullObject+DI (`bootstrap.py` ~47 port, ~12 registry). 4-key identity. 9 sacred rules.

| # | Subsystem | Vai trò | Điểm neo (file) |
|---|---|---|---|
| §1 | HTTP entry · auth · 4-key | 13 middleware (reverse-order), JWT HS256 service + RS256 user, resolve `(tenant,workspace,bot,channel)`→`record_bot_id` | `interfaces/http/app.py`, `middlewares/tenant_context.py`, `services/bot_registry_service.py` |
| §2 | Query understanding · routing | understand(LLM intent) → complexity → decompose → **route stats vs vector** | `nodes/understand.py`, `nodes/retrieve.py:176-273`, `shared/query_range_parser.py` |
| §3 | Retrieval dual-path | stats SQL (deterministic) **/** hybrid vector+BM25 → RRF → rerank(cross-encoder) → CRAG grade | `nodes/retrieve.py`, `nodes/rerank.py`, `nodes/grade.py`, `stats_index_repository.py` |
| §4 | Generation · sysprompt · model | context cap → prompt build → LLM → citations; SysPromptAssembler (ADR-W1-S10 append-only) | `nodes/generate.py`, `services/sysprompt_assembler.py`, `model_resolver/` |
| §5 | Guardrails · grounding | input block (injection/pii/sql); output: leak/secret **block**, grounding **warn-only** | `nodes/guard_output.py`, `infrastructure/guardrails/local_guardrail.py` |
| §6 | Ingest · chunk · embed · stats | U1 validate→U2 parse(kreuzberg/docling)→U3 clean→U4 chunk(AdapChunk L1-L5)→U5 enrich→U6 vn-segment→U7 embed+stats-index | `application/services/document_service/ingest_stages*.py`, `shared/chunking/*` |
| §7 | Cache · tenancy · conversation/action | 2-tier semantic cache; RLS 3-layer; booking slots JSONB `action_state` | `cache/semantic_cache.py`, `db/engine.py`, `conversation_state/jsonb_conversation_state.py` |
| §8 | DI · config · workers · observability | Container; config 7-tier ladder; Redis-Streams inbox/outbox exactly-once; request_logs/steps/model_invocations | `bootstrap.py`, `shared/bootstrap_config.py`, `redis_streams_bus.py` |
| §9 | Data layer | bots(4-key) · document_chunks(embedding 1024/content_segmented/chunk_context) · document_service_index(stats) · 240+ alembic | `db/models.py`, `alembic/versions/*` |

Đường đi 1 câu hỏi: `guard_input → understand(+cache parallel) → complexity → [decompose|rewrite/MQ] → retrieve(stats|vector) → rerank → mmr → neighbor → grade → generate → guard_output → persist`.

---

## 2. 5 TIÊU CHÍ — STATE HIỆN TẠI (đo 2026-06-18, có evidence)

| Tiêu chí | State | Gốc rễ (evidence) |
|---|---|---|
| **Đúng/Faithfulness** | ❌ chưa 100% | **BUG-1 CONFLATE giá**: factoid-giá named-entity → vector → chunk đa-dịch-vụ → LLM gán nhầm giá. Chuỗi: `query_range_parser.py:374-377` loại "gia bao nhieu" + `table_dual_index` group-chunk centroid + grounding warn-only không chặn (agent A/B/C đều xác nhận) |
| **Nhanh** | ❌ p95 ~15s | MQ fanout always-on + rerank + CRAG + **grounding SYNC** (`grounding_check_async_enabled=False`) trên critical path (agent C) |
| **UX** | ⚠️ | booking slot đã fix; list ok; **giá phrasing loạn** (11/12 cách hỏi rơi vector — agent A); OOS empty-string tier → có thể trả rỗng (agent C) |
| **Performance** | ⚠️ | **RLS BYPASSED runtime** (BUG-4): `DATABASE_URL_APP` unset + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` → superuser DSN, 23 policy chết (agent E, `engine.py:69-82`). Isolation chỉ còn app-level WHERE |
| **Cost** | ⚠️ | sysprompt ~2400 tok (A-prime đã giảm, chưa đo lại); cascade routing OFF → mọi intent dùng model đắt; MQ fanout đốt embed/LLM (agent C) |

---

## 3. CÁC ĐIỂM "JUST CODE, CHƯA THÔNG MINH" — cụm gốc rễ

Gom từ 6 agent, xếp theo tầng:

**A. Routing = đống regex VN hardcode (không phải intelligence)** — agent A
- `parse_range_query/list_query/code_query` 100% regex; `_LIST_SIGNALS`/`_LIST_STRIP_PHRASES`/`_COUNT_SIGNALS` là tuple VN cứng, không config-override, không đa ngôn ngữ.
- `query_range_parser.py:374-377`: `if "gia bao nhieu" in folded: return None` — 2 cụm-từ quyết định cả routing → vừa over- vừa under-inclusive. Đây là **boundary nơi code im lặng fail** rồi mới tới LLM (LLM nhận input đã sai).
- `price_column="any"` SQL OR-logic chéo 2 cột giá → false-positive ngay cả khi đã route stats (agent A DUMB-6).

**B. Chunking thông minh nhưng nối vào input rỗng** — agent B
- `parsed_blocks = []` hardcode → block-pipeline flag là **no-op shell**; `smart_chunk_atomic` là **dead code**.
- `table_dual_index` group-chunk = centroid đa-row → conflate (đúng "Noise Problem" của research). Narrate-then-embed OFF → bảng embed dưới dạng CSV thô, lệch xa câu hỏi NL.
- Ekimetrics 5-metric selector (SC/ICC/DCC/BI/RC) implement sẵn, OFF. Late-chunking hiện chỉ là **prefix 200 char** (xấp xỉ), chưa phải token-pooling thật.

**C. Faithfulness KHÔNG được enforce** — agent C
- `grounding_check` + `llm_grounding_check` đều `severity="warn"` → **đo hallucination nhưng không chặn**. Conflate (cả 2 giá đều có trong chunk → numeric-overlap pass) + Extrapolate (cộng sai tổng) lọt. `max_sentences=5` hardcode → câu 6+ không check.

**D. Isolation tầng 2 đang tắt** — agent E
- RLS 3-layer cài đủ (policy 0069/0187 + role ragbot_app 0186 + SET LOCAL hook) nhưng runtime chạy superuser → chỉ còn app-level WHERE. 1 env-var sai = RLS chết, chỉ 1 dòng WARN log.

**E. Data quality** — agent F
- `document_service_index` không unique constraint + bulk_insert không ON CONFLICT → dupe khi re-ingest non-reindex. `entity_category` mostly NULL. Drift VND range `10K-50M` hardcode (spa-calibrated).

---

## 4. ĐỐI CHIẾU RESEARCH CHUNKING + UX CASE STUDY

Research (Noise/Context/Cost paradox · Recursive 400-512 default · Late chunking · VN word-seg + 256-tok PhoBERT · Unstructured/Docling multimodal · 4-trụ eval) ⟷ code:

| Research mandate | Mình có? | Ghi chú |
|---|---|---|
| Recursive 400-512 + overlap 10-20% default | ✅ | strategy recursive + DEFAULT_CHUNK_SIZE/OVERLAP |
| VN word-seg trước BPE ("sân_bay") | ✅ một nửa | `segment_vi_compounds` → `content_segmented` **chỉ dùng cho BM25**, embedder dense vẫn thấy text thô (agent B gap E) → có thể feed `content_segmented` vào embed |
| Section-split Điều/Khoản | ✅ Điều | HDT promote Chương/Mục/Điều; **Khoản dài chưa atomic** (agent B gap F) |
| Late chunking | ⚠️ xấp xỉ | prefix-200-char, chưa token-pooling; sliding OFF |
| Multimodal layout-aware (Unstructured/Docling) | ⚠️ | parser kreuzberg/docling có block, nhưng `parsed_blocks=[]` chặn |
| Micro-headers metadata (CLAREDI) | ✅ | HDT breadcrumb prefix `[Chương>Mục>Điều]` |
| 4-trụ eval (precision/recall/efficiency/resource) | ⚠️ | có Coverage/Faithfulness; **thiếu Context-Precision tách bạch** |

**UX case study (CSAT 2.4→4.6)** map thẳng tiêu chí UX của mình: 3 đòn bẩy họ dùng — (1) RDRSegmenter word-seg, (2) Sentence-Window parent-context, (3) CLAREDI micro-headers — mình đã có (1) phần BM25, (3) HDT; **thiếu (2) sentence-window/parent-child đang OFF** (`parent_child_enabled=False`, agent B). Đây là đòn bẩy UX "câu trả lời mạch lạc không cụt" mà case study nhấn mạnh.

---

## 5. LỘ TRÌNH EXPERT RAG — EVOLVE theo 5 tiêu chí (ranked, no-guess)

Nguyên tắc: **ưu tiên T1 Faithfulness trước** (CLAUDE.md core MVP order), mỗi đòn bẩy ghi rõ **measurement cần** — KHÔNG tuyên bố % trước khi load-test.

### 🥇 ĐÚNG/FAITHFULNESS (T1, làm trước)
1. **Fix routing BUG-1** — gỡ exclusion `query_range_parser.py:374-377` + thêm route `price-of-named-entity → query_by_name_keyword` (stats deterministic 1-row). *EVOLVE, surgical.* **SOTA hơn**: thêm 3 field vào `UnderstandOutput` schema (`query_type`, `entity_name`, `price_filter`) — LLM intent+entity extractor (Self-Query pattern) thay regex pile, đa ngôn ngữ by-construction (agent A SOTA-1/2). *Measure*: `scripts/verify_fixes_loadtest.py` 6-phrasing trap, conflate-rate trước/sau (baseline 3-5/6 sai → target 0).
2. **Ingest: bỏ group-chunk** — `table_csv` per-row exclusive + RFC-4180 parse (agent B). *Measure*: MRR trên price-lookup golden set.
3. **Bật arithmetic/structured verify cho số** — numeric claim → trace về đúng `chunk_id`, không chỉ "có mặt trong corpus" (agent C SOTA-3.1). Optionally nâng grounding factoid lên block-capable. *Measure*: conflate+extrapolate catch-rate.

### 🥈 NHANH (T2)
4. **Bật async grounding** (`grounding_check_async_enabled=True`, gate top_score≥0.7) — gỡ LLM judge khỏi critical path (agent C SOTA-3.4). *Measure*: p50/p95 trước/sau per intent.
5. **Bật reflect-skip-if-grounded** + streaming TTFT (đã build). *Measure*: `_stream_first_token_ms`.
6. **MQ complexity-gate** (`multi_query_complexity_min≈0.65`) — chặn fanout cho query đơn giản (Adaptive-RAG; cần move `query_complexity` trước MQ node — agent C SOTA-3.7). *Measure*: recall aggregation trước/sau.

### 🥉 COST (T2)
7. **Bật cascade routing** per-bot (đã build, `model_resolver`) — greeting/chitchat/factoid → model rẻ, multi_hop → model mạnh (agent C SOTA-3.3). *Measure*: cost/turn + HALLU rate A/B.
8. **Sysprompt intent-conditional** + đo lại token post-A-prime. *Measure*: token counter.
9. **Semantic cache threshold 0.93 cho factoid** (đang 0.97, hằng số tự nhận MIN_RECOMMENDED=0.95). *Measure*: hit-rate vs semantic-drift trên 100 paraphrase.

### UX (T2)
10. **Bật parent-child / sentence-window** (case study CSAT lever) — chunk nhỏ ngắm bắn + parent context dồi dào (agent B). *Measure*: CSAT/answer-completeness.
11. **OOS language_pack tier non-empty** — không bao giờ trả rỗng (agent C). *Measure*: empty-answer rate.

### PERFORMANCE / ISOLATION (T2)
12. **Bật RLS runtime** — ops set `DATABASE_URL_APP=ragbot_app` + gỡ `RAGBOT_ALLOW_SUPERUSER_RUNTIME` (KHÔNG sửa code, agent E). *Measure*: cross-tenant probe test.

### CHUNKING SOTA (T1/T2, sau khi #1-#3 land)
13. **Nối block-native**: `parsed_blocks = ctx.blocks or []` (1 dòng) → kích hoạt atomic-protection thật + per-block narrate (agent B SOTA-D).
14. **Bật per-table LLM description** (RAG-Anything T1) **type-gated O(tables) không O(rows)** — narrate chỉ TABLE/FORMULA/IMAGE (agent B SOTA-C). *Measure*: precision price-table query.
15. **Bật Ekimetrics 5-metric selector** (config flip) + feed `content_segmented` vào dense embed. *Measure*: strategy-distribution + answer-correctness golden set.

---

## 6. THỨ TỰ THỰC THI ĐỀ XUẤT (gate-discipline)

```
Phase A (Faithfulness, surgical):  #1 routing → #2 per-row → #3 numeric-verify   → load-test gate
Phase B (Latency/Cost config-flip): #4 async-grounding, #5 reflect-skip, #6 MQ-gate, #7 cascade  → A/B gate
Phase C (Chunking SOTA):            #13 block-wire → #14 per-table-narrate → #15 ekimetrics       → ingest re-eval
Phase D (Ops/UX):                   #12 RLS, #10 parent-child, #11 OOS                            → verify
```

Mỗi Phase = 1 plan riêng (`plans/YYMMDD-*/plan.md`) + user-approve trước khi đụng `src/` (CLAUDE.md /plan mandate). Phase A là 1 plan T1-Smartness; load-test PHẢI pass (Coverage≥0.95, HALLU=0, conflate=0) trước khi sang B.

---

## 7. CAVEAT (rule #0)
- DB local (5434) **rỗng** — muốn đo trên data thật phải chạy alembic + nạp corpus (server `10.0.1.160` unreachable). Đọc-hiểu code thì xong; **đo lường thì chưa có data**.
- Mọi % trong research là corpus của họ, không transfer. Mọi "impact" ở §5 là **hướng + cần đo**, chưa phải kết quả.
- 6 agent = deep-read đại diện, không phải literal mọi dòng. Điểm nào cần chắc tuyệt đối → re-verify `file:line` trực tiếp trước khi sửa.

---
*Nguồn: 6 agent read-only (retrieval/ingest/generation/http/DI-RLS/data) + research chunking + UX case study. Sibling reports: [DEEPDIVE_CHUNKING_20260617.md], [PROJECT_ALL_FLOWS_20260618.md], [CHUNKING_RESEARCH_VS_CODE_20260618.md].*
