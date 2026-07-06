# 00 — SYNTHESIS MASTER (running) — Deep-dive đa agent 2026-07-02

> File tổng hợp SỐNG — cập nhật mỗi đợt report agent về. Đọc file này TRƯỚC, rồi mới
> đọc report con. Mọi claim có evidence `file:line` trong report con tương ứng.
> Khung tổng hợp cuối = MASTER PROMPT 5-phase của owner (xem §6).

**Trạng thái fleet (cập nhật 17:35)**: 27 agents / 3 workflow slices, 6 chạy đồng thời.
- ✅ XONG (4): `refs-rag-anything` · `refs-adaptive-chunking` · `refs-open-notebook` · `refs-tldw-server`
- 🔄 ĐANG CHẠY (6): 2 web-SOTA (slice A) · `code:orchestration-graph` + `code:orchestration-nodes` (slice B) · `code:infra-ingest-stack` + `code:infra-safety-obs-rest` (slice C)
- ⏳ QUEUE (17): 4 web + refs-pdf2audio/cross-synthesis + 10 code readers + 3 test-health

Workflow run IDs: `wf_15cf3da9-2b0` (A refs+web) · `wf_229d5de3-e85` (B code1) · `wf_ecfdab72-67a` (C code2+tests).

---

## §1. Quyết định owner trong session (ĐÃ THI HÀNH — đừng làm lại)

| Quyết định | Thi hành | Commit |
|---|---|---|
| **Bỏ ING-F1** (numeric-column→price guard) | Revert document_stats.py về pre-4e83410; xóa test; Q13 stock-as-price = KNOWN LIMITATION, workaround per-bot `custom_vocabulary["column_roles"]` khai cột stock là `attribute` | `6796cd9` |
| **Giữ "innocom"** trong comment code | Không scrub | — |
| **QU-F1/F2 defer** (heuristic 0.85≥0.85 misroute price-factoid→aggregation) | Đã VERIFY là bug thật nhưng là routing-policy change đụng 9 pin tests → PHẢI load-test đo trước khi flip | — |
| Max 4-6 agents đồng thời | Split 1 workflow (cap 2/wf trên máy 4-core) → 3 workflow song song = 6 | — |

Commits deep-debug trước đó còn hiệu lực: `4e83410` (provider `.name`→`.code` — GIỮ phần query_graph), `cc5e1ea` (OBS-F6 streaming cost fallback), `86b9190` (GEN-F6 opt-out `# HEADER` format).

## §2. PHÁT HIỆN LỚN NHẤT — "AdapChunk" ragbot chưa phải AdapChunk thật

(Chi tiết + evidence: `refs-adaptive-chunking.md`)

Paper gốc: **chunk thử N cách → chấm điểm OUTPUT THẬT → argmax**. Ragbot lấy từ vựng
(5 metric, tên tầng, atomic block) nhưng KHÔNG lấy vòng lặp lõi:
- Production selector = rule cứng TRƯỚC KHI chunk (`analyze.py:407-541`)
- Ekimetrics selector tính metric trên **chunk giả lập equal-split** → điểm giống nhau
  cho mọi strategy = không phân biệt được (`intrinsic_metrics.py:291-296`); flag default OFF;
  docstring cite "Rule-Based Selector section" KHÔNG tồn tại trong paper code
- **Bake-off của chính ragbot đo được: adaptive == oracle 0/8 doc, lift +0.001 vs recursive,
  headroom 0.103** (`reports/bakeoff_chunking_20260620.md`)

→ Trả lời luôn câu MASTER PROMPT Phase-2.4 ("Thông tư chọn HDT thay vì PROPOSITION, cross-check
không bắt"): fix đúng tầng KHÔNG phải thêm rule mapping, mà là ĐÓNG VÒNG evaluate-then-select
(bake-off offline định kỳ → ghi `oracle_best` per-doc làm override).

## §3. Pattern "vòng lặp không đóng" — lặp toàn hệ thống

1. **Coverage gate phát hiện mất chữ nhưng KHÔNG vá** (`ingest_stages.py:889-905` observe-only;
   reference vá + assert). `CoverageResult.uncovered_spans` đã có offset sẵn — vá ≈ 15 dòng.
   Mất-nội-dung-im-lặng = nguồn class bug "corpus có đáp án mà bot mù" (Coverage metric).
2. **Layer 6 block-pipeline xây xong, 0 caller** (`chunking/__init__.py:653`; ingest vẫn flatten
   text `ingest_stages.py:770`); Layer-2 context buffer chạy xong bị VỨT output
   (`ingest_stages.py:597-613`); registry parsers emit 0 Block; 2 DI registry chết có notice
   (`infrastructure/chunk_quality/registry.py:1-23`, `infrastructure/chunking_strategy/registry.py:1-10`).
3. **Citation không được verify** — thiếu metric "% citation không nằm trong retrieved set"
   (detector bịa-nguồn, app-side, post-hoc, không đụng answer → không vi phạm sacred #10).

## §4. MULTI-FORMAT — xác nhận "chưa first-class" (owner đúng)

- **Ảnh nhúng trong PDF/DOCX bị VỨT hoàn toàn** — kreuzberg parser 0 xử lý image (grep 0 hit).
  RAG-Anything: VLM caption + context ±1 trang + chunk 2-đại-diện (raw + narrative).
- **Công thức = 0** (chỉ có `formula_count` đếm). Trick OMML extract DOCX lossless 0-dep có sẵn ở RAG-A.
- **`page_number` có trong domain model nhưng KHÔNG ghi DB** (`ingest_helpers.py:188-198` không cột
  page, metadata_json không ghi) → citation không bao giờ trỏ được trang.
- **Docling adapter gần chắc trả 0 block** — iterate tuple `(item, level)` không unpack
  (`docling_parser.py:114-116`) [HYPOTHESIS mạnh — chưa chạy runtime, docling opt-in].
- Parser contract type-blind `{"content", metadata}` vs taxonomy `text|table|image|equation|generic`.
- Chunking budget bằng CHAR không TOKEN (`DEFAULT_CHUNK_SIZE=1024` chars) — VN char/token drift.

## §5. Ragbot THẮNG cả 4 refs ở phần nền — EVOLVE đừng REWRITE

- Type-detection mime→ext→byte-sniff mạnh hơn CẢ 4 (RAG-A extension-only, unknown→"thử như PDF";
  tldw có MIME-wins nhưng ragbot có OOXML peek).
- Multi-tenant 4-key + RLS: cả 4 repo = 0 tenancy thật.
- Stacked 2-row header merge (`tabular_markdown.py:105-138`) — cả reference paper mangles case này.
- Test surface 41 file chunk-test vs 4 (paper) / 16 (open-notebook).
- Hexagonal + Port/Registry/DI + config-chain: không repo nào bằng.

## §6. Khung tổng hợp cuối = MASTER PROMPT 5-phase (owner giao 2026-07-02)

Mapping phase → nguồn dữ liệu:
- **P1 Research** = 6 web agents + 6 refs agents (đang chạy/xong)
- **P2 Audit Luồng 1 (ingest→chunk→embed→pgvector)** = `code:app-document-service` +
  `code:infra-ingest-stack` + `code:shared-data` + refs-adaptive-chunking (§2, §3, §4 trên)
- **P3 Audit Luồng 2 (question→retrieve→rerank→answer)** = `code:orchestration-*` +
  `web:retrieval-sota` + `web:agentic-query`
- **P4 Luồng 3 (logging/cost/RAGAS-agent-grader)** = `code:infra-safety-obs-rest` + `web:eval-hallu`;
  schema ground-truth + Agent Grader prompt = em tự viết trong synthesis cuối
- **P5 Bottleneck tổng + Top-5 P0 + roadmap 3 giai đoạn** = file này, section §9 khi đủ report

**KHÔNG ĐỦ DỮ LIỆU** (khai đúng rule MASTER PROMPT): 3 screenshot (trace 33.072s/661 blocks/
4_strategy_selector 4.506s/8_embedding 19.633s; sample point original_content=NULL; 77 chunks
Thông tư + structural_path; UI gpt-5-nano + threshold 0.35) — em chỉ có số liệu transcribe trong
prompt, KHÔNG có file gốc. Cần owner cung cấp: JSON trace ingest thật + dump 77 chunks + config UI
nếu muốn audit sâu hơn mấy điểm đó. Spec AdapChunk = `docs/design/ADAPCHUNK_ARCHITECTURE.md` (ĐÃ đọc full).

## §7. training_corpus — đã đọc chuyên sâu phần RAG-relevant (owner yêu cầu)

Vị trí: `training_corpus/` (gitignored, 42 file md, 1.011 refs). Đã đọc inline:
- `MASTER_INDEX.md` — 11 mục, mục đích gốc = fine-tune Hermes/DeepSeek local
- `02_rag_multitenant/CORPUS_INDEX.md` — 50 nguồn curated (RAG SOTA A1-A20, multi-tenant B1-B10,
  VN NLP C1-C9, observability D1-D14) + curriculum 5 phase — dùng làm nguồn đối chiếu web-SOTA agents
- `04_ragbot_idea/INDEX.md` — 15 lessons distilled từ chính codebase (tier A-E)
- `05_apr2026_cutting_edge/rag_blogs/` — 2 blog production RAG (Apr 2026) CÓ SẴN bảng gap-mapping vào ragbot:

**Gaps ragbot đã được chấm sẵn trong corpus** (blog 01 + 02, feed thẳng P5):
- ❌ Completeness verification (câu so sánh → đủ thuộc tính 2 vế?) — khớp bug "so sánh 185"
- ❌ Conflict-surfacing theo authority/date khi 2 chunk mâu thuẫn
- ❌ Deterministic citation verification (<5ms code check) — `citations_extract` chưa instrument
- ⚠️ Field-weighted BM25 (header 3×, mã hàng 5×) — chưa có per-tenant
- ⚠️ Threshold-rot monitoring (refusal-rate theo query-type) — dashboard chưa live
- ❌ Embedding drift detection

## §8. Adoption candidates từ 4 refs (bảng chạy — sẽ thành bottleneck table P5)

### Từ adaptive-chunking (T1 core)
| # | Việc | Effort | Ghi chú |
|---|---|---|---|
| A1 | Coverage gate: VÁ gap (port `repair_gaps_between_chunks`) | S | uncovered_spans có sẵn |
| A2 | Bake-off = feedback loop: chạy định kỳ → `oracle_best` per-doc override | M | đóng vòng evaluate-then-select, 0 latency ingest |
| A3 | Parser emit `split_points`/`titles` char-offset (keystone contract) | M | mở khóa BI thật + title attach + page map |
| A4 | Heading context → `Chunk.contextual_prefix` (slot có sẵn) thay vì nhét vào content | M | hash stability + dedup + coverage locate |
| A5 | Persist `page_number` vào metadata_json | S | citation trỏ trang |
| A6 | Quyết định 2 dead registries (revive cho bake-off HOẶC xóa) | S | |
| A7 | Fix/fence docling adapter (tuple unpack) | S | trước khi ai flip engine |

### Từ RAG-Anything (multi-format)
| # | Việc | Effort | Ghi chú |
|---|---|---|---|
| R1 | Modal-block taxonomy trong parser contract (`block_type` + `page_idx` + captions) | M | prereq mọi thứ dưới |
| R2 | Đường ảnh-nhúng: VLM caption + context-grounded + dual-representation chunk | L | gap cứng nhất; per-bot opt-in (cost) |
| R3 | Table description layer (1 summary chunk per table, giữ row-as-chunk) | M | |
| R4 | Modality probe questions trong load test | S | metric modality-coverage |
| R5 | Parse cache content-hash + parser-config key | M | skip reparse/re-embed |
| R6 | Doc status 2-trục + stage-labeled error (parse/chunk/embed/enrich) | M | |
| R7 | Sentinel-block degradation + zero-block fail-loud + JSON parse ladder + alias-tolerant readers | M | robustness bundle |

### Từ open-notebook (provenance + lifecycle)
| # | Việc | Effort | Ghi chú |
|---|---|---|---|
| O1 | Citation-ID allowlist (bot-owner template) + **app-side citation validation metric** | S | detector bịa-nguồn, sacred-safe |
| O2 | Per-doc inclusion policy (pin/retrievable/excluded) + token-cost preview | M | trị "corpus có mà retrieval trượt" cho doc nhỏ |
| O3 | Parent-doc aggregation (dedupe về doc, max score + evidence chunks) cho search/citation | S | parent_chunk_id đã có |
| O4 | Insights layer RAPTOR-lite (doc-summary embed riêng, citable) | M | EVALUATE — đo lift trước |
| O5 | Dimension guard pgvector SQL (`vector_dims(embedding)=expected`) | XS | đã dính class này Jina→ZE |
| O6 | `/documents/{id}/retry` từ persisted asset | S | |
| O7 | Context manifest (IDs thật trong prompt) trên chat API response | S | provenance không cần tin LLM |
| O8 | Token-threshold escalation → long-context binding (config-driven) | S | binding purpose có sẵn |

### Từ tldw_server (verification tier + retrieval)
| # | Việc | Effort | Ghi chú |
|---|---|---|---|
| T1 | **Numeric-fidelity detector** (VN normalizer: `1.499.000đ`, `1tr499`) observe-only node | S | HALLU-4-số detector; K1 bug class |
| T2 | **Hard-citation coverage** (câu→span chunk_id,start,end) | S | groundedness 0-LLM per turn |
| T3 | why_these_sources metadata (diversity/freshness/topicality) | XS | |
| T4 | Stable/unstable CI gate split (deterministic fail-hard, LLM-judge warn) | S | test-health |
| T5 | **Sentinel-calibrated rerank gate** (floor động per-query per-model) | M | HẾT recalibrate threshold khi đổi model; upgrade cliff-detect |
| T6 | Embeddings A/B arms harness (golden set, hit@k/MRR per candidate model) | M | migration có đo |
| T7 | Granularity→param-bundle routing (factoid→span nhỏ k cao; summary→parent) — patterns vào language_packs | M | |
| T8 | Alpha-weighted RRF per-intent (numeric/factoid lean BM25) | S code + M eval | |
| T9 | Knowledge strips (sub-chunk filter giữa rerank và prompt) | M | T2 token + T1 precision |
| T10 | Grading fallback-to-score khi CRAG grader fail/timeout | S | fix bug memory 2026-05-15 |
| T11 | Upload hardening: blocked-ext + MIME-wins + YARA hook + archive scan | M | GA tier |
| T12 | Per-page OCR fallback (<40 chars) + `ocr_confidence` gate lúc retrieve | L | scanned+digital PDF |
| T13 | Chunking templates DB + classifier block (tenant-editable) | L | AdapChunk B-series |
| T14 | Post-gen claim verify bounded-repair (re-retrieve, KHÔNG rewrite answer) + FVA offline | L-XL | CONTESTED cho mâu thuẫn cross-doc |
| T15 | Late-chunking rescue lúc retrieve (re-chunk parent khi top score < sentinel margin) | L | cứu chunk-boundary sai không cần re-ingest |
| T16 | Per-feature cost/time budget (decomposer, CRAG, verify) | M | |

### Anti-patterns ĐÃ LỌC — cấm copy
Extension-only detection; everything-to-PDF; fabricated page_idx; heading-blind flatten;
process-global prompt state; client-supplied context; brute-force cosine; 229-param mega-fn;
**answer overwrite/decline English hardcode** (sacred #10); English regex/wordlist hardcode;
auto-tune semantic-cache threshold; web-search fallback (phá corpus-grounding).

## §9. Trục kiến trúc chính đang nổi lên (draft P5 — chốt khi đủ report)

Cả 4 refs cùng chỉ về MỘT khoảng trống: **tầng VERIFICATION/OBSERVE sau generate**.

```
ragbot:    guard_in → retrieve → grade → generate → guard_out(shingle) → HẾT
4 refs:                                 → generate → numeric-fidelity ✚ hard-citation ✚
                                          citation-allowlist-validate ✚ claim-verify ✚
                                          why-these-sources   (observe-only, 0 override)
```

Draft Top-P0 (sẽ chốt lại sau khi code readers + web + tests về):
1. A1 coverage repair (S) — chặn mất-chữ-im-lặng
2. O1+T2 citation validation + hard-citation coverage (S+S) — detector bịa-nguồn
3. A5 page_number persist (S) — citation trỏ trang
4. T1 numeric-fidelity VN (S) — HALLU-4-số detector
5. A2 bake-off feedback loop (M) — AdapChunk thành thật
6. T5 sentinel rerank gate (M) — hết threshold-rot
7. T10 grading fallback-to-score (S) — fix chunks_used=0
8. R1→R2 modal taxonomy + ảnh nhúng (M→L) — multi-format thật
9. O2 per-doc inclusion policy (M)
10. R6 doc status 2-trục (M)

Tất cả lift = HYPOTHESIS đến khi đo (rule #0): mỗi item ship sau flag + A/B load-test.

## §9B. 🔴 CODE READERS — PHÁT HIỆN HỆ THỐNG (đợt 17:46, orchestration ×2)

(Chi tiết: `code-orchestration-graph.md` + `code-orchestration-nodes.md` — cả 2 CHẠY PROBE THỰC NGHIỆM)

### BUG CLASS HỆ THỐNG #1 — LangGraph 1.2.4 DROP mọi state key không khai trong GraphState TypedDict
**FACT thực nghiệm** (probe chạy trên langgraph cài thật): key không khai → bị drop khỏi initial
input, khỏi node return, và mutation in-place không qua node boundary. `state.py` tự cảnh báo
điều này 4 lần nhưng **≥12-16 key đang vi phạm**. Journal production xác nhận class này live
(warning `semantic_cache_preflight_no_embedding_column` bắn mỗi turn từ 2026-06-30).

**Hậu quả từng key (đã verify):**
| Key bị drop | Hậu quả | Mức |
|---|---|---|
| `bot_extra_output_tokens_per_response` | **Tính năng TRẢ TIỀN luôn = 0** — bot mua thêm token output không bao giờ nhận được | CRIT/revenue |
| `rerank_score_mode` | CRAG all-irrelevant fallback luôn dùng relative gate, floor tuyệt đối 0.25 chết → chunk rác lọt vào generate → **HALLU risk ↑** | HIGH |
| `bot_created_at` | XML-wrap default-on-from-date CHẾT → feature 100% unreachable (kill #2: knob không được builder populate) | HIGH |
| `_total_graph_iterations` | Loop cap chết → reflect→generate loop chỉ còn recursion_limit=50 → GraphRecursionError 500 | MED-HIGH |
| `retrieval_degraded`/`embed_degraded` | Flag an toàn HALLU (phân biệt lỗi-rỗng vs thật-sự-không-có) **0 reader** — bảo vệ được quảng cáo trong comment KHÔNG tồn tại | MED |
| `_corpus_version`, `crag_skip_retry`, `citations_source`, `action_state`... | memo/observability chết | LOW-MED |

**Fix 1 commit**: khai 12-16 key vào GraphState + đổi in-place→return + **AST pin test**
(walk nodes/*.py so key dùng vs `GraphState.__annotations__`) — đây là lần thứ 3 class này
xuất hiện (M17 đã fix 1 instance mà không thêm guard).

### CÁC PHÁT HIỆN CRITICAL/HIGH KHÁC (orchestration)
1. **Stats route KHÔNG có LLM verification ở BẤT KỲ tầng nào** — guard_output skip grounding
   vô điều kiện cho `retrieve_mode=stats*` (guard_output.py:105); pin test
   `test_guard_output_wires_stats_route_skip_grounding_flag` **ĐANG FAIL trên tree**; contract
   Fix-B 2026-06-25 (`DEFAULT_STATS_ROUTE_SKIP_GROUNDING=False`) chưa bao giờ được wire.
   Kết hợp: stats bypass rerank+grade+grounding, chunk synthetic score=1.0.
2. **`int(_price)` cắt cụt giá thập phân** (query_graph.py:2391,2411) — corpus USD 19.99 → bot trả
   "19" như grounded fact = misinterpret-class HALLU **do chính retrieval tier tiêm vào**. Dedup
   key va chạm 19.99 vs 19.50. [multi-currency = multi-tenant gap]
3. **F2 = root cause của 7/8 collection error đã thấy**: comment hứa re-export
   (`_cliff_detect_filter`, `_rerank_threshold_gate`, CRAG vocab, `parse_decomposed_sub_queries`)
   nhưng import không tồn tại → 7 file test pin CHẾT KHÔNG CHẠY → invariant cliff/threshold/CRAG
   đang không được guard. Fix = vài dòng import.
4. **Cascade routing = no-op hoàn toàn** — `resolved_answer_model` ghi vào key không ai đọc
   (và cũng bị drop); LLM call luôn resolve theo binding purpose. Owner bật cascade chỉ tốn
   1 lần resolve + log noise mỗi turn.
5. **GraphRAG chunks KHÔNG BAO GIỜ vào được prompt** — graph_retriever synthesize
   `chunk_id=None`, generate.py:633 drop chunk falsy-id → bật graph_rag_mode = trả tiền latency+SQL
   cho 0 đóng góp.
6. **Permission filter bị bypass** trên race-winner + speculative-hit early returns (ACL class,
   flags default OFF nên exposure gated).
7. **`cross_doc_reconcile_enabled` = mirage knob** (feature Phase-4 của chính session này!) —
   `_pcfg` default True inline, không builder nào populate → force-ON mọi bot không tắt được;
   reconcile chỉ khớp corpus có alias cell tên `question` (happy-case-shaped).
8. **Heuristic 0.85 ≥ 0.85** — re-confirm độc lập QU-F1 + thêm: cache GET không gate history,
   `force_re_understand` không ai set (orphan escape hatch), signals locale không được truyền
   (mọi bot classify bằng pattern vi).
9. **`generate_context_chars_cap_by_intent` system_config row KHÔNG có alembic seed** — nghi
   out-of-band drift (sacred #7!); fresh deploy sẽ tái phát bug "1tr499 mất 3/7 chunks" 2026-05-21.
10. **neighbor_expand span-union** — 2 seed xa nhau trong doc dài → fetch cả trăm row giữa chúng,
    budget bị fill bởi front-matter thay vì neighbor thật.
11. Orphans mới: `rrf_round_robin.py` (fairness layer cho comparison — built+tested+0 import),
    `_understand_greeting_short_circuit` (46 dòng + 20 tests + 0 caller), 6 dead metric imports,
    `_resolve_and_complete` dead fn.
12. Multi-locale: stats/list/count/superlative vocabulary CHỈ có vi+en → bot ja/fr/km silent mất
    completeness; sysprompt-leak shingle chết với ngôn ngữ không space-delimited (zh/ja/th).

### Fix order đề xuất từ 2 agents (khớp nhau độc lập)
1. F2 re-exports (vài dòng) → 7 test pin sống lại
2. GraphState 12-16 keys + AST pin test (1 commit — fix luôn paid-tokens, grade calibration, xml-wrap, loop cap)
3. Wire `stats_route_skip_grounding` (pin test đang fail → pass, HALLU net cho stats route)
4. `int(_price)` → Decimal/str render
5. Populate 3 mirage knobs vào 2 pipeline_config builders (`xml_wrap_enabled`, `cross_doc_reconcile_enabled`, `bot_custom_vocabulary` test_chat)
6. Cascade: wire hoặc xóa · GraphRAG: synthetic id hoặc gate off
7. Alembic-seed system_config row đang drift (sacred #7 remediation)

## §9C. 🔴 CODE READERS đợt 2 — infra-ingest + infra-safety + shared-data (17:35-17:51)

(Chi tiết: `code-infra-ingest-stack.md` · `code-infra-safety-obs-rest.md` · `code-shared-data.md` —
cả 3 đều RUNTIME-VERIFY, không chỉ đọc tĩnh)

### CRITICAL — runtime-proven
1. **OCR fallback trả 0 block cho MỌI document** — kreuzberg 4.9.7 `extract_bytes` là coroutine,
   adapter gọi sync không await (`kreuzberg_parser.py:258`) → "coroutine never awaited",
   block_count=0 luôn. Unit test mock SYNC API nên xanh giả. Mọi format trượt registry
   (ảnh không VLM, .doc/.xls/.ppt, format lạ) → "empty document text after parse" → DLQ.
2. **.doc/.xls/.ppt KHÔNG CÓ parser** (+.tsv/.json) — CLAUDE.md tuyên bố first-class; detect_parser
   trả None (không có OLE2 magic branch) → kết hợp #1 = không thể ingest.
3. **PII redaction TRƠ HOÀN TOÀN end-to-end** — bootstrap đóng băng provider="null" compile-time
   (`bootstrap.py:447-450`, comment nói "per-call từ system_config" là SAI); knob
   `pii_redactor_provider` 0 reader; facade fallback unreachable. Cả 2 boundary (chat + ingest)
   passthrough. Fix = 1 dòng bootstrap (Callable + get_boot_config).
4. **GraphRAG gãy CẢ 2 CHIỀU vì kwarg mismatch `bot_id=` vs `record_bot_id`** — TypeError bị
   broad-except nuốt cả query lẫn ingest (`graph_retriever.py:61`, `ingest_core.py:801`);
   LLM extract triples TỐN TIỀN rồi không bao giờ store. AsyncMock nhận mọi kwargs nên test mù.
   = đúng bug class naming-convention (memory đã ghi 2 lần) ở tầng call-site.

### HIGH
5. **RLS chết ở fallback stages 2-4** — `record_tenant_id` được thread nhưng bị **kwargs nuốt,
   bare session (không SET LOCAL) → dưới RLS runtime 3/4 stage trả 0 row; VÀ thiếu filter
   `doc_deleted_at IS NULL` → **document đã soft-delete sống lại qua fallback chain**.
6. **`parent_chunk_id` không bao giờ được SELECT** bởi bất kỳ SQL retrieval nào → parent-child
   expansion + stage-4 parent-expand + auto-merge = **3 tính năng no-op vĩnh viễn**. Fix = thêm
   1 cột vào SELECT.
7. **Redis Streams recovery KHÔNG re-dispatch** — XCLAIM xong vứt payload, không ai đọc PEL,
   consumer name có uuid/process → restart mồ côi PEL → message lỗi 0 lần retry, thẳng DLQ sau
   5 lần claim. Comment ở 3 chỗ hứa "retry until success" = fiction. At-least-once thực tế là
   at-most-once-then-DLQ.
8. **Sanitizer Tier-0 không bao giờ chạy** (`_sanitizer` không ai gán, flag default ON = fiction)
   + **Source-URL allowlist (PoisonedRAG defence) unreachable** (container không có provider).
9. **Grounding gate NGƯỢC** — judge đo được "ungrounded" → answer VẪN SHIP (warn, action="hitl"
   không có consumer); judge KHÔNG chạy được → refuse (fail_closed). Chặn đúng case không đo,
   thả case đo ra vi phạm.
10. **Embedding dim khóa cứng vector(1280) toàn cục** — binding embedder dim khác = config theater,
    fail lúc INSERT; `PgVectorStore.upsert_chunks` = dead code vi phạm NOT NULL nếu được gọi
    (Port write contract gãy, ingest bypass port bằng raw SQL).

### SHARED-DATA — "happy-case box" đo được bằng thực nghiệm (11 shape CONFIRMED chạy code thật)
**Box hoạt động**: catalog giá VND · header known-vocab hoặc có `| --- |` · tên ≤12 từ ·
giá 10k-500M · delimiter comma/tab/leading-pipe · không transposed. **Ngoài box = degrade im lặng:**
| # | Shape vỡ | Hậu quả (đã chạy verify) |
|---|---|---|
| B1 | Markdown table không leading-pipe | 0 entities — cả bảng vô hình với stats index |
| B2 | Header cột năm (`Chỉ tiêu·2023·2024`) | "2024" đọc là money → header mất → **metric doanh thu lọt vào price index, thắng cả superlative** |
| B3 | Merged column ở bảng KHÔNG giá (roster/lịch) | data rows thành phantom `## heading` — "phòng Kế toán có 1 người" |
| B4 | Corpus USD/EUR/JPY | mọi price=None → range/superlative/price route chết cả corpus |
| B5 | Bảng transposed | giá mất + noise entity |
| B6 | CSV chấm-phẩy (EU Excel) | 0 entities |
| B7 | Tên >12 từ | drop cả row kèm giá |
| B8 | Giá <10k VND (văn phòng phẩm, topping) | price bị zero im lặng |
| B9 | Stock-as-price (Q13) | KNOWN — owner accepted, workaround custom_vocabulary |
| B10 | `2tr5` compound | parse 2.005.000 (người hiểu 2.500.000); math_lockdown parse khác nữa (2.000.000) |
| B11 | Header dài >40 chars/6 từ | header demoted → col_N |
Cùng nhóm: money-shape quyết định STRUCTURE (vi phạm nguyên tắc "metadata hint, không dictate");
3 pipeline bảng khác nhau theo format (xlsx/sheets/docx qua tabular_markdown; CSV qua strategy
riêng; PDF/HTML qua kreuzberg thẳng).

### CHỦ ĐỀ XUYÊN SUỐT #2 (mới): "LAST-MILE DI WIRING"
≥5 tính năng safety/quality ship xong, unit test XANH, production = 0: PII (F-3), sanitizer (F-4),
source allowlist (F-5), GraphRAG ingest + query (F-6), cascade routing, XML-wrap, greeting-skip,
rrf_round_robin, parent-child (×3). Root cause chung: test mock strategy — không test WIRING;
degrade path im lặng hoặc DEBUG-level. **Guard đề xuất: "wiring audit" 1 trang — mỗi registry có
bootstrap provider đọc đúng system_config key chưa + 1 integration test chạy class THẬT un-mocked.**

## §9D. 🔴 ĐỢT 3 (23:21-23:34) — document-service (ingest core) + services-core + shared-rest + 4 web

### code-app-document-service — PHÁT HIỆN QUAN TRỌNG NHẤT CHO OWNER
**F4 · Đường ingest B2B CHÍNH THỨC (POST /documents/create → worker) FLATTEN row-chunks** —
worker join parser chunks thành full_text, gọi ingest KHÔNG raw_bytes → `parser_row_chunks=None`
→ `parser_preserve` không bao giờ fire trên Path B. Pipe-markdown không có comma → `_is_csv_format`
False → table fast-path chết → nhiều row/chunk (cross-row conflate) hoặc whole-doc collapse.
**→ Fix xe-bot row-per-chunk 2026-07-01 CHỈ bảo vệ đường test-harness (Path A), KHÔNG bảo vệ
đường production B2B.** (document_worker.py:465-467,613-625; ingest_core.py:314-337)

Các finding lớn khác (ranked, đều FACT):
- **F5**: re-ingest 1 phần XÓA stats entities của rows không đổi — sửa 1 row trong sheet 100 rows
  → delete_by_document toàn bộ + insert lại chỉ từ subset đổi → 99 entities biến mất đến khi
  full re-ingest. (ingest_stages_final.py:443,548)
- **F6**: `tool_name` collision (title lowercase 64 chars) → 2 doc khác nhau merge thành chimera —
  chunks 2 corpus trộn lẫn, chunk_index trùng.
- **F1**: flip `diff_based_reingest_enabled=true` → **NameError** (hàm bị comment-out trong
  dead module) → doc kẹt sau khi đã commit. Flag = landmine.
- **F9**: stats rows ghi dưới `record_tenant_id or uuid.uuid4()` — tenant BỊA khi thiếu → data
  vô hình vĩnh viễn thay vì fail-loud.
- **F10**: cleaner xóa MỌI dòng lặp ≥3 lần <100 chars — menu có "Giá: 500.000đ" lặp → mất số
  TRƯỚC khi chunk (đúng class number-HALLU).
- **F11**: `language="auto"` → hardcode `vi` — doc EN/JA bị VN-segment + sai embedding override.
- **F12**: KHÔNG có cách re-embed khi đổi embedding_text_strategy/prefix/narrate (hash chỉ tính
  trên text) — owner đổi config thấy "chunks_unchanged", kết luận "feature hỏng".
- **F7**: asyncpg 32,767 bind ceiling → sheet >~2,978 rows/statement fail SAU KHI đã trả tiền embed.
- **F13**: DOC/XLS legacy không parser (re-confirm) · **F17**: 3 chỗ gọi `litellm.acompletion` thẳng
  trong application (bypass router port: không CB, không binding, không cost) · **F18**: ~7 SELECT
  bots tuần tự mỗi ingest.
- Rating per-format (bảng đầy đủ trong report §2): PDF-text/DOCX/MD/TXT production-ready cả 2 path;
  **XLSX/CSV/Sheets production-ready Path A nhưng DEGRADED Path B**; PDF-scan broken Path A;
  DOC/XLS broken cả 2.

### code-app-services-core (headline)
- **F1**: heuristic classifier locale built-not-wired (re-confirm lần 3 — mọi bot dùng pattern vi)
- **F4**: SystemConfigService Redis KHÔNG guard trên hot-path config nóng nhất (T2 robustness)
- **F5**: `resolve_embedding` THIẾU system_config fallback mà chat+rerank có — **đúng bug class
  memory `feedback_resolver_must_fallback_system_config` đã cấm tái phạm**
- **F8**: 4 stack resolve API-key song song; reranker encrypted-key path chưa implement
- **F16**: verify API key chỉ implement Jina — provider khác "verified" giả
- **F6**: GENERIC_VOCABULARY duplicate key `"kh"` (python-verified) · **F11**: refusal text vẫn có
  thể rơi về i18n.py hardcode (vi phạm Application MINDSET #3)

### Landed thêm: web-eval-hallu, web-multitenant-arch, web-agentic-query, web-ingest-formats,
### code-shared-rest — để dành synthesis cuối (P1 research inputs).
**Còn chạy (9)**: app-ports-dto, infra-repos-db, infra-llm-embed-rerank, interfaces-http,
interfaces-workers, platform-core, tests×3.

## §9E. ĐỢT 4 (00:05) — ports-dto + repos-db + llm-embed-rerank (slice B đủ 7/7)

### code-infra-repos-db — 2 CRITICAL mới
- **F1 CRIT**: cả 5 method `ai_keys` query schema KHÔNG TỒN TẠI (`ragbot.ai_keys`) — encrypted
  key-pool đọc DB fail mọi lần (giải thích vì sao chỉ .env key hoạt động).
- **F2 CRIT (posture)**: RLS chết — runtime superuser qua escape hatch (re-confirm finding tenant).
- **F3 HIGH**: price-range filter "any" bug OR/AND chéo cột — match rows mà KHÔNG cột giá nào
  trong range → kết quả range query sai.
- **F4 HIGH**: `count_by_name_keyword` vs `query_by_name_keyword` match set KHÁC NHAU →
  **"có mấy X" ≠ "liệt kê X"** — count và list mâu thuẫn nhau trên cùng corpus.
- **F7**: `delete_by_document` (stats) tenant-unscoped → dưới RLS live sẽ silent no-op → stats
  index không bao giờ được dọn. **F11**: bulk_insert >~5.400 entities vượt bind-param → CẢ stats
  index mất im lặng. **F13**: workspace collapse về "system" trên 6 write path.

### code-app-ports-dto
- **F1 HIGH**: **Idempotency key BỎ QUÊN record_bot_id + workspace_id + document_name** — bot thứ 2
  cùng tenant ingest cùng source_url trong 24h bị NUỐT im lặng (trả kết quả bot 1). Multi-bot leak class.
- **F4**: 12 Strategy registries comment-out 100% (ports + Null tồn tại, unreachable).
- **F9**: DocumentService lắp tay ở 4 site với capability KHÁC NHAU — "one canonical funnel"
  chỉ đúng trên worker path (khớp F4 document-service Path A/B split).
- **F10**: `tool_name` slugify diacritic-fold collision ("Bảng Giá" vs "Bang Gia" cùng slug).

### code-infra-llm-embed-rerank
- **F1 HIGH**: reranker adapter construct MỖI TURN → CB/semaphore/client-reuse vô hiệu + leak HTTP client.
- **F2 HIGH**: LiteLLMReranker lệch index khi có chunk content rỗng → rerank scores gán sai chunk.
- **F3 HIGH**: per-bot `EmbeddingSpec.dimension` bị Jina/ZE adapter BỎ QUA (matryoshka ghim constant)
  — per-bot dim = config theater (khớp finding dim-locked của infra-ingest).
- **F4 HIGH (opt-in)**: `SPECULATIVE_REDO_SENTINEL` **leak nguyên văn vào answer user** (redo
  protocol chưa implement).
- **F5/F6**: fallback-hop + draft-model cost tính theo giá PRIMARY; legacy LLMPort path không có
  tokenizer zero-fill. **F7**: streaming KHÔNG có fallback failover (path nóng nhất mong manh nhất).
- **F15**: modality boost không bao giờ được gọi (built-not-wired thêm 1).

**Còn chạy (6)**: interfaces-http, interfaces-workers, platform-core, tests×3.

## §10. Log phân tích đã báo owner

- Phân tích #1 (17:23): 3 refs đầu — AdapChunk vỡ vòng lặp, multi-format gaps, top-8 P0 draft
- Phân tích #2 (17:33): tldw 13 patterns — verification tier, 3 vết đau lịch sử được giải
- File này ghi 17:35 theo yêu cầu owner "viết tất cả ra file report đi nhé chứ để quên"

**Next update**: khi 2 web-SOTA + 2 orchestration readers về (~17:45-17:55 ước tính).
