# AUDIT CHUYÊN SÂU RAGBOT — Tổng hợp cuối theo MASTER PROMPT 5-phase

> Nguồn: 27 agent Fable 5 đọc **toàn bộ 127k LOC** `src/ragbot` + 76k LOC 5 reference repo +
> 10 chủ đề web-SOTA 2025-2026 + training_corpus. ~150 findings, **mọi claim có `file:line`**,
> nhiều finding **runtime-verified** (probe chạy code + query DB thật). Report con:
> `reports/DEEPDIVE_20260702/*.md`. Nhãn: **FACT** (có evidence) vs **HYPOTHESIS** (chưa đo runtime).
> 3 tiêu chí xuyên suốt: **Correctness/UX** · **Performance** · **Cost**.

---

## 0. KẾT LUẬN 1 DÒNG

Khung xương ragbot **expert-grade** (Hexagonal · Port/Registry/DI · 4-key · RLS design · 41 file
chunk-test) — nhưng **~40% tính năng đã ship KHÔNG chạy trong production** vì 3 lỗi hệ thống:
(1) LangGraph drop state-key, (2) DI wiring last-mile thiếu, (3) code chỉ vững trong "happy-case box"
(catalog VND · header known-vocab · Path A). **EVOLVE đúng hướng — vấn đề là nối dây + đóng vòng lặp
+ vá degrade, KHÔNG phải khung sai.** Owner nói "mới support happy case" = **CONFIRMED có bằng chứng chạy được**.

---

## 1. PHASE 1 — RESEARCH BỐI CẢNH (web-SOTA 2025-2026, nguồn có URL)

Tổng hợp từ `web-retrieval-sota` · `web-chunking-tables` · `web-eval-hallu` · `web-agentic-query` ·
`web-multitenant-arch` · `web-ingest-formats` + training_corpus (2 blog production Apr-2026).

| Chủ đề | Insight áp dụng cho ragbot | Nguồn |
|---|---|---|
| Hybrid > pure-vector | Cosine mù với exact-match (mã hàng/số/ID); PHẢI BM25 + RRF k=60. "43% pipeline pass offline rồi rot <90 ngày, lỗi hầu hết ở retrieval" | ragaboutit / medium Apr-2026 (training_corpus) |
| Contextual Retrieval (Anthropic) | Prepend chunk-context summary trước embed → -49% retrieval error; prompt-cache làm rẻ | anthropic.com/news/contextual-retrieval |
| Late chunking / late interaction | ColBERT/PLAID multi-vector; matryoshka dim (ragbot dùng ZE 1280) | arxiv BGE-M3, jina-v3 |
| Rerank SOTA | cross-encoder / Cohere 3.5 / jina-v3 listwise; top-N=5 sau top-K 20-30 | cohere blog, arxiv 2509.25085 |
| Completeness verification | Câu so sánh → check đủ thuộc tính 2 vế TRƯỚC synthesis; câu procedural → đủ steps | ragaboutit blog 02 |
| Deterministic citation verify | Check citation marker vs retrieved-doc array bằng CODE (<5ms) > LLM entailment | medium blog 01 |
| pgvector vs Qdrant (câu MASTER PROMPT) | pgvector HNSW OK <10M vector, RAM ~150GB/50M×768d, hybrid `tsvector`+vector trong 1 query = **lợi thế ragbot** (Qdrant phải app-side fuse); giữ pgvector, KHÔNG chuyển Qdrant | supabase/pinecone/weaviate eng blogs |
| RAGAS evolution | LLM-judge có bias (position/verbosity); dùng detector deterministic + %sample human-review | arxiv RAGAS, FActScore, LLM-as-judge NeurIPS |

**Kết luận Phase 1**: spec AdapChunk (`docs/design/ADAPCHUNK_ARCHITECTURE.md`) đề Qdrant nhưng
**pgvector là lựa chọn ĐÚNG cho ragbot** (hybrid 1-query + RLS + đã deploy). SOTA khớp hướng ragbot;
gap lớn nhất so SOTA = **tầng verification sau generate** (xem §4).

---

## 2. PHASE 2 — LUỒNG 1: Upload → Chunking → Embedding → pgvector

### 2.1 Bảng điểm nghẽn Luồng 1

| # | Vấn đề | Sev | Bằng chứng | Ảnh hưởng |
|---|---|---|---|---|
| L1-1 | **Path A/B split**: `POST /documents/create` (B2B chính thức) flatten row-chunks → `parser_preserve` không fire; XLSX/CSV/Sheets DEGRADED. Fix xe-bot 01/07 chỉ bảo vệ test-UI | CRIT | document_worker.py:465-467,613-625; ingest_core.py:314-337 | **Correctness**: khách B2B vẫn dính col_N/cross-row-conflate |
| L1-2 | **OCR fallback trả 0 block MỌI doc** — gọi async `extract_bytes` sync (kreuzberg 4.9.7). Format trượt registry → DLQ | CRIT | kreuzberg_parser.py:258 (runtime probe: "coroutine never awaited") | **Correctness**: ảnh/scan/.doc/.xls không ingest được |
| L1-3 | **.doc/.xls/.ppt KHÔNG parser** (CLAUDE.md tuyên bố first-class); ảnh nhúng + công thức bị vứt hoàn toàn | HIGH | registry.py:45-61; grep image/equation=0 | **Correctness** multi-format |
| L1-4 | **PII redaction TRƠ** — bootstrap đóng băng `"null"`, knob system_config 0 reader | CRIT | bootstrap.py:447-450 | **Correctness/compliance**: CCCD/sđt lưu thô |
| L1-5 | **Coverage gate phát hiện mất chữ nhưng KHÔNG vá** (reference vá+assert) | HIGH | ingest_stages.py:889-905 | **Correctness**: mất chữ im lặng = bot mù |
| L1-6 | **Re-ingest 1 phần XÓA stats entities rows không đổi** — sửa 1/100 row → 99 entity biến mất | HIGH | ingest_stages_final.py:443,548 | **Correctness**: count/price sập |
| L1-7 | **Happy-case box** đo bằng thực nghiệm: 11 shape vỡ (bảng năm→doanh thu vào price index; roster merged→"1 người"; USD→giá None; CSV `;`→0 entity; tên>12từ→drop cả row+giá) | HIGH | code-shared-data §2.2 (chạy verify) | **Correctness** đa dạng corpus |
| L1-8 | **Header raw-CSV không vocab → 0 entity** (canary 25/25 fail): header thành data → col_N → noise-filter xóa sạch | HIGH | document_stats.py:348-387,245-266 (live-debug seed 0) | **Correctness**: stats route mất cho corpus lạ |
| L1-9 | Money-shape quyết định STRUCTURE (vi phạm "metadata hint, không dictate") | HIGH | tabular_markdown.py:93-102 | **Correctness** |
| L1-10 | `page_number` có trong domain nhưng KHÔNG ghi DB → citation không trỏ trang | MED | ingest_helpers.py:188-198 | **UX** citation |
| L1-11 | Cleaner xóa dòng lặp ≥3 lần → menu giá lặp mất số trước chunk | MED | text_processing.py:97-102 | **Correctness** number-HALLU |
| L1-12 | `language=auto`→hardcode `vi`; doc EN/JA bị VN-segment | MED | ingest_core.py:532 | **Correctness** multi-locale |
| L1-13 | Bake-off đo: **adaptive selector == oracle 0/8, lift +0.001** → AdapChunk chưa phải AdapChunk (chọn strategy TRƯỚC chunk, Ekimetrics tính trên chunk giả lập) | HIGH | reports/bakeoff_chunking_20260620.md; intrinsic_metrics.py:291-296 | **Correctness** + **Cost** (LLM selector 4.5s vô ích) |
| L1-14 | asyncpg 32,767 bind → sheet >~2,978 rows/statement FAIL sau khi trả tiền embed | MED | ingest_helpers.py:200-241 | **Correctness** doc lớn |
| L1-15 | 3 chỗ gọi `litellm.acompletion` thẳng trong application (bypass router: no CB/binding/cost) | MED | ingest_core.py:865; enrich.py:479 | **Cost** + **T3** |

### 2.2 Trả lời trực tiếp câu hỏi AdapChunk trong MASTER PROMPT
- **Cross-checker TẮT có phải rủi ro?** Layer-5 cross-check thực ra **default ON** (`_12:149`), nhưng
  screenshot cho thấy cross-checker off ở lượt đó → **KHÔNG có lớp bảo vệ khi LLM chọn sai strategy**.
  NHƯNG gốc rễ sâu hơn: dù cross-check bật, selector không nhìn OUTPUT thật (L1-13) nên mọi rule mapping
  chỉ là đoán trước. **Fix đúng tầng = đóng vòng evaluate-then-select, KHÔNG phải thêm rule PROPOSITION.**
- **Thông tư chọn HDT thay PROPOSITION**: đúng, spec §4.2 khuyên PROPOSITION cho pháp lý; cross-check §5.2
  không có rule "văn bản pháp quy → PROPOSITION" → **gap CONFIRMED**. Nhưng lại là hệ quả L1-13.
- **`original_content=NULL`**: với chunk TEXT thuần là ĐÚNG thiết kế. CHƯA ĐỦ DỮ LIỆU verify với chunk
  TABLE/FORMULA thật (cần dump chunk có bảng). Ragbot: `Chunk.contextual_prefix` slot có sẵn nhưng
  heading context đang bị nhét vào content thay vì metadata (churn hash + defeat dedup).
- **pgvector vs Qdrant**: giữ pgvector (§1).

---

## 3. PHASE 3 — LUỒNG 2: Câu hỏi → Hiểu → Retrieval → Rerank → Top-5 → Answer

### 3.1 Bảng điểm nghẽn Luồng 2

| # | Vấn đề | Sev | Bằng chứng | Ảnh hưởng |
|---|---|---|---|---|
| L2-1 | **LangGraph drop ≥12-16 state key** → paid-tokens=0, rerank floor 0.25 chết (HALLU↑), xml-wrap chết, loop-cap chết, degraded-flags 0 reader | CRIT | state.py vs graph_assembly.py (probe verify) | **Correctness/Revenue/Cost** |
| L2-2 | **Stats route = 0 verification** (bypass rerank+grade+grounding); pin test đang FAIL trên tree | CRIT | guard_output.py:105; test fail | **Correctness** HALLU |
| L2-3 | **`stats_route_skip_grounding` HALLU-net bị revert** commit `3097755` → skip vô điều kiện; commit `062d6fa` từng vá đúng breach "stock number leaked from history" → **có thể tái mở breach** | CRIT | guard_output.py:105-106; bot_limits.py:63-70 | **Correctness** HALLU sacred |
| L2-4 | **GraphRAG gãy CẢ 2 CHIỀU** (`bot_id=` vs `record_bot_id` TypeError nuốt) — LLM extract triples tốn tiền rồi vứt; query trả 0; `chunk_id=None` bị generate drop | CRIT | graph_retriever.py:61; ingest_core.py:801 | **Correctness** multi-doc + **Cost** |
| L2-5 | **`int(_price)` cắt giá thập phân** — USD 19.99→"19" grounded fact; score=1.0 bypass rerank+grade | HIGH | query_graph.py:2391 | **Correctness** multi-currency HALLU |
| L2-6 | **RLS chết fallback stage 2-4** (kwargs nuốt tenant_id, bare session) + **doc soft-delete sống lại** | HIGH | bm25_only_stage2.py:86; keyword_stage3.py:112 | **Correctness/security** |
| L2-7 | **`parent_chunk_id` không bao giờ SELECT** → parent-child + stage-4 + auto-merge = no-op vĩnh viễn | HIGH | pgvector_store.py:340-350 | **Correctness** small-to-big |
| L2-8 | **"có mấy X" ≠ "liệt kê X"** (count vs list match set khác) + price-range OR/AND chéo cột bug | HIGH | count_by_name_keyword vs query_by_name_keyword | **Correctness** aggregation |
| L2-9 | **Cascade routing no-op** (resolved model 0 reader) — owner bật chỉ tốn resolve + log | HIGH | generate.py:399-417 | **Cost/T1** |
| L2-10 | Heuristic 0.85≥0.85 misroute price-factoid→aggregation (top_k=40 cho 1-fact) + locale signals không truyền (mọi bot dùng pattern vi) | HIGH | understand.py:125; heuristic_intent_classifier.py:117 | **Cost** + **Correctness** |
| L2-11 | **Grounding gate NGƯỢC**: judge đo "bịa"→answer VẪN ship (warn, hitl không consumer); judge không chạy được→refuse | HIGH | local_guardrail.py:541-552; guard_output.py:355 | **Correctness** HALLU |
| L2-12 | Per-bot embedding dim BỎ QUA (Jina/ZE ghim matryoshka constant; vector(1280) khóa cứng); `SPECULATIVE_REDO_SENTINEL` leak nguyên văn vào answer | HIGH | EmbeddingSpec bỏ qua; llm-embed-rerank F3/F4 | **Correctness** multi-bot |
| L2-13 | Reranker adapter construct mỗi turn (CB/semaphore vô hiệu + leak client); LiteLLMReranker lệch index khi chunk rỗng | HIGH | llm-embed-rerank F1/F2 | **Perf/Correctness** |
| L2-14 | Streaming (path nóng nhất) KHÔNG có fallback failover; fallback-hop cost tính giá PRIMARY | MED | llm-embed-rerank F7/F5 | **Perf/Cost** |
| L2-15 | Threshold rerank tuyệt đối phải recalibrate mỗi lần đổi model (đã dính Jina→ZE) | MED | (memory) | **Perf** |
| L2-16 | `ai_keys` query schema KHÔNG tồn tại → encrypted key-pool fail (chỉ .env hoạt động) | CRIT | code-infra-repos-db F1 | **Correctness/ops** |

### 3.2 Sơ đồ luồng đề xuất (bổ sung — MASTER PROMPT yêu cầu)
```
Q → [normalize + intent-classify(locale-aware) + query-classify simple/complex]
  → simple: embed → hybrid(BM25+vector RRF k=60, alpha-per-intent) → rerank(sentinel-gated) → top-5
  → complex: decompose → fan-out(per-subquery budget) → RRF-round-robin(fairness) → rerank → top-5
  → [metadata filter structural_path/doc_type để tăng precision]
  → generate(model routing: nano cho simple, escalate cho multi-hop) — gửi cả original_content + narrative
  → [POST: numeric-fidelity ✚ hard-citation coverage ✚ citation-ID validate ✚ why-these-sources] (observe-only)
```
Threshold 0.35 hiện tại: **quá thấp = nhiễu, quá cao = miss** — thay bằng **sentinel-gate động**
(P1 tldw) thay vì hằng số cứng, hết phải recalibrate.

---

## 4. PHASE 4 — LUỒNG 3: Logging → Cost → Agent Grader (RAGAS-style)

### 4.1 Trục kiến trúc chính đang thiếu — VERIFICATION/OBSERVE sau generate
Cả 5 reference + 2 blog SOTA cùng chỉ về MỘT khoảng trống:
```
ragbot:  guard_in → retrieve → grade → generate → guard_out(shingle) → HẾT
cần bổ sung (observe-only, 0 override answer = sacred #10 an toàn):
  → generate → numeric-fidelity(VN normalizer) ✚ hard-citation-coverage(câu→span)
             ✚ citation-ID-validate(cited ⊆ retrieved) ✚ why-these-sources ✚ completeness-check
```

### 4.2 Schema log đề xuất (đã có phần lớn — cần bổ sung)
Ragbot **đã có**: `request_logs` (+ plaintext question/answer_text ship 43a32ed), `request_steps`
(per-step latency), `model_invocations` (token+cost, hash prompt). **THIẾU**: `retrieved_chunk_ids`
+ điểm số per chunk trong response; citation-validity metric; numeric-fidelity metric; segmented
refusal-rate theo query_type.

### 4.3 Agent Grader (thiết kế mới — MASTER PROMPT yêu cầu)
- **Ground-truth file** `{question, expected_answer, expected_source_chunk_ids, question_type}` với
  question_type = 6 loại spec §8.2 (fact-đơn / cần-heading / liên-quan-bảng / liên-quan-công-thức /
  tổng-hợp-nhiều-đoạn / tham-chiếu-chéo).
- **Agent Grader TÁCH BIỆT Answering Agent**, output JSON: Faithfulness · Context-Precision ·
  Context-Recall · Answer-Relevance · Strategy-Selection-Accuracy. Lợi thế Agent (Fable 5) vs
  LLM-judge đơn: tự tra lại chunk gốc, so `expected_source_chunk_ids` thay vì chỉ đọc text.
  Giới hạn: vẫn bias → **%sample human-review** (khớp CLAUDE.md `feedback_ragas_parallel`:
  asyncio.gather semaphore N=8-10, KHÔNG sequential).
- **Auditor batch**: cost TB/câu, p50/p95, %Faithfulness thấp theo question_type → trace ngược Luồng 1/2.

### 4.4 Test-health — trả lời "tất cả phase test đều lỗi?"
**CONFIRMED có cấu trúc** (agent chạy full suite thật):
- `pytest tests/unit/ -q` **KHÔNG chạy được** — 8 collection error abort ngay cửa (CI-style hỏng cửa trước).
- Với `--continue-on-collection-errors`: **6.439 pass / 67 fail / 33 xpass / 24 skip-module (260 test parked)**.
- Phân loại 75 (67 fail + 8 error): **39 stale-import** (commit `24f2451` xóa re-export + `eafddaa` SSRF),
  **9 env** (FastAPI 0.135 vs helper ≥0.137), **6 REAL bug src**, **25 canary = spec cố-tình-fail** (đúng
  happy-case gap L1-8), **0 flaky**.
- **~25-30% test file sẽ KHÔNG bắt được behavioral break** (source-regex pin, dead-code park, import drift) — HYPOTHESIS từ sample 15 file.
- 6 REAL bug: E1 stats-grounding revert (=L2-3), E2 cross_doc mirage knob, E3 per-intent max-tokens
  orphan (greeting không còn cap 60 token → **Cost regression**), E4×3 hygiene-guard (price-coupling
  133>127, broad-except 250>249, version-ref 9>7).

---

## 5. PHASE 5 — BOTTLENECK TỔNG + TOP-5 P0 + ROADMAP

### 5.1 Ba lỗi HỆ THỐNG (gộp ~150 finding thành 3 root class)

| Class | Bản chất | Fix cấu trúc + guard chống tái phát |
|---|---|---|
| **S1 — State-key drop** | LangGraph 1.2.4 drop key không khai GraphState → ≥12-16 feature chết im lặng | Khai keys + **AST pin test** (walk nodes/*.py return/get vs `__annotations__`). Lần thứ 3 tái phát. |
| **S2 — Last-mile DI wiring** | ≥9 feature ship + test XANH + production=0 (PII, sanitizer, allowlist, GraphRAG×2, cascade, xml-wrap, parent-child×3, modality-boost) | **"Wiring audit" 1 trang** + 1 integration test chạy class THẬT un-mocked mỗi registry. Test mock strategy không test wiring. |
| **S3 — Happy-case box** | Code vững chỉ trong {VND · header-vocab · Path A · vi-locale · money-cell}; ngoài box degrade về 0 thay vì graceful | Shape-only header fallback + `_is_noise_entity` scope-aware + currency config + Path-A/B parity. Canary INV-1/INV-2 là spec. |

### 5.2 TOP-5 VIỆC PHẢI LÀM NGAY (P0) — cụ thể, hành động được

**P0-1 · Fix S1 state-key drop (1 commit) + AST pin test** — [T1/Revenue/Cost]
Khai 12-16 key vào `GraphState`, đổi in-place→return. Fix CÙNG LÚC: paid-tokens=0, rerank floor
0.25 (HALLU), xml-wrap, loop-cap. Effort **S**. Rủi ro nếu không: khách trả tiền không nhận token +
chunk rác lọt generate.

**P0-2 · Khôi phục HALLU-net stats route (L2-2/L2-3) + un-break 7 test pin (F2)** — [T1/HALLU sacred]
(a) Restore `_pcfg(stats_route_skip_grounding)` gate ở guard_output (hoặc owner chốt xóa knob+comment);
(b) thêm lại dòng re-export `_cliff_detect_filter`/`_rerank_threshold_gate`/CRAG vocab (vài dòng import)
→ 7 test pin sống lại. Effort **S**. Rủi ro: HALLU breach "stock number leaked" tái mở + invariant
cliff/threshold/CRAG không được guard.

**P0-3 · Fix Path A/B parity (L1-1) + OCR fallback async (L1-2)** — [T1/multi-format]
(a) Worker path truyền row-shape signal / dùng `insert_content_list`-style seam thay vì flatten;
(b) đổi `extract_bytes`→`extract_bytes_sync` (hoặc await đúng) + un-mock contract test. Effort **M**.
Rủi ro: khách B2B XLSX/CSV vẫn col_N + mọi format trượt registry chết ingest.

**P0-4 · Coverage repair (L1-5) + persist page_number (L1-10) + citation-ID validate (Luồng 3)** — [T1/HALLU-adjacent]
(a) Vá gap dùng `uncovered_spans` sẵn có (~15 dòng); (b) ghi page_number vào metadata_json (no migration);
(c) node observe-only đếm %citation không ⊆ retrieved (detector bịa-nguồn, không đụng answer).
Effort **S+S+S**. Rủi ro: mất chữ im lặng + citation không trỏ nguồn.

**P0-5 · GraphRAG kwarg fix HOẶC gate off (L2-4) + PII wiring 1-dòng (L1-4)** — [T1/Cost/compliance]
(a) `bot_id=`→`record_bot_id=` cả 2 site (hoặc gate off tránh đốt token extract vô ích);
(b) bootstrap `providers.Callable(get_boot_config("pii_redactor_provider"))`. Effort **S+S**.
Rủi ro: đốt token extract triples rồi vứt + CCCD/sđt lưu thô vi phạm compliance.

### 5.3 RỦI RO NẾU KHÔNG SỬA (nói thẳng)
- **HALLU sacred vỡ**: stats route (L2-2/3) + int-price (L2-5) + grounding-gate-ngược (L2-11) → bot trả
  số sai/bịa như "grounded fact". Với văn bản pháp quy ngân hàng (corpus MASTER PROMPT) = **trả sai điều
  luật/lãi suất** — hậu quả nghiêm trọng.
- **Doanh thu rò**: paid-tokens=0 (P0-1) + idempotency quên bot_id (bot 2 bị nuốt) → khách trả tiền
  không nhận tính năng, không thấy lỗi.
- **Multi-format thất hứa**: .doc/.xls/ảnh/công thức = CLAUDE.md first-class nhưng không ingest được.
- **Silent data loss**: coverage không vá + re-ingest xóa stats + canary 0-entity → "corpus có đáp án mà
  bot mù" (đúng lo ngại Coverage của owner).

### 5.4 CÂU HỎI CÒN THIẾU DỮ LIỆU (khai đúng rule MASTER PROMPT)
1. **3 screenshot gốc** (trace 33s/661 blocks; sample point original_content=NULL; 77 chunks Thông tư
   + structural_path; UI gpt-5-nano + threshold 0.35) — em chỉ có số transcribe. Cần file JSON/dump để
   verify L1-13 timing + original_content với chunk TABLE thật.
2. **Log Luồng 2 thật** (retrieved_chunk_ids + scores per turn) — chưa expose trong response.
3. **Bộ ground-truth 15-20 câu/bot** theo 6 question_type → chạy Agent Grader đo lift.
4. **Load-test số thật** cho mọi P0 (rule #0 CẤM ĐOÁN): mọi "fix được X%" phải đo trước commit.

### 5.5 ROADMAP 3 GIAI ĐOẠN

**Tuần 1 — Fix P0 (S1/S2 wiring, không đụng khung):**
P0-1 state-key + AST guard · P0-2 HALLU-net + re-export · P0-3 Path A/B + OCR · P0-4 coverage+page+citation
· P0-5 GraphRAG+PII. Kèm: un-break 8 collection error (fix FastAPI pin + stale import) để CI chạy lại.
→ Deliverable: HALLU-net kín, ~9 feature built-not-wired sống lại HOẶC xóa sạch, CI xanh cửa trước.

**Tuần 2-3 — Build Luồng 2 hardening + Luồng 3 logging/eval:**
Sentinel rerank gate (hết recalibrate) · numeric-fidelity VN detector · hard-citation coverage ·
alpha-per-intent RRF · granularity routing · knowledge strips · currency config (thoát VND-only) ·
shape-only header fallback (thoát happy-case box) · expose retrieved_chunk_ids + scores trong response.
→ Deliverable: tầng verification observe-only + escape happy-case box + log đủ để chấm.

**Tuần 4+ — Eval end-to-end + đóng vòng AdapChunk (spec §8.3):**
Ground-truth 6-config ablation (Baseline/HDT/SEMANTIC/PROPOSITION/AdapChunk/no-cross-check) · Agent
Grader RAGAS-style parallel · bake-off thành feedback loop (oracle_best per-doc override) · embeddings
A/B arms harness (đo mọi migration) · modality probe questions. Chấm theo question_type, trace ngược
Luồng 1/2 theo %Faithfulness thấp.
→ Deliverable: AdapChunk thành AdapChunk thật (evaluate-then-select), mọi lift có số đo (rule #0).

---

## 6. GHI CHÚ TUÂN THỦ CLAUDE.md CHO MỌI FIX
- Mọi detector Luồng 3 = **observe-only, KHÔNG override answer** (sacred #10).
- Behavior text (refusal, decline) → `oos_answer_template`/guardrail config, KHÔNG hardcode i18n.
- Vocab/regex/currency → language_packs + system_config, KHÔNG bake (thoát VND/vi-only).
- Content DB (rules, config) → alembic tracked (sacred #7 — đã phát hiện `generate_context_chars_cap`
  row drift không seed, cần remediate).
- Mọi lift = load-test đo trước commit (rule #0). Model tier: Opus main / subagent research.
- EVOLVE không REWRITE: giữ khung + 4-key + 9 sacred; chỉ nối dây + đóng vòng lặp + vá degrade.
