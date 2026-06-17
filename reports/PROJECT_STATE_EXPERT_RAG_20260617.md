# Ragbot — Toàn cảnh dự án vs Expert RAG (2026-06-17)

> Tài liệu tổng hợp: TẤT CẢ luồng hiện tại + đối chiếu "expert là như nào" (dẫn chứng
> code file:line + research) + trạng thái fix THẬT (rule#0: SỰ THẬT có evidence vs
> GIẢ THUYẾT chưa đo). Nguồn: 4 deepdive agent + 7 web source + AdapChunk spec +
> adaptive-chunking repo + load-test thật phiên này.

---

## 0. EXECUTIVE SUMMARY — scorecard

| Trục | Trạng thái | Evidence |
|---|---|---|
| **Đúng/Faithful (HALLU=0)** | ✅ **GIỮ** (sacred) | load-test 3/3 trap refuse, 0 fabricate |
| **Coverage** | 🟡 **legal 100% · spa ~83% · xe ~67%** | load-test 12 câu phiên này |
| **Số chuẩn hoá** | ✅ DONE + verified | 21/21 format đúng |
| **Aggregation** (đắt nhất/dưới X) | ✅ DONE + verified | "dưới 500k/700.000" list đúng, "đắt nhất" Meso 3tr |
| **xe code lookup** | 🔴 CHƯA xong | "195/65R15 về hàng" vẫn refuse (chunking nát) |
| **Test suite** | ✅ 5916 pass / 0 fail | full run phiên này |
| **CLAUDE.md compliance** | ✅ 6/6 PASS | agent audit |
| **Push GitHub** | 🟡 commit xong, push chặn bởi harness | user tự push |

**Fix done all? → CHƯA.** Core (số + aggregation + test) DONE+verified. xe-chunking + spa-flaky + structured-fields = root-caused, plan sẵn, **chưa code**.

---

## 1. TẤT CẢ LUỒNG HIỆN TẠI (file:line)

### 1.1 Luồng INGEST (7 bước, embedded trong `ragbot-py.service`)
```
Upload → document_worker → DocumentService ingest_stages:
 U3 clean   (ingest_stages.py:221)  HTML strip, NFC, prompt-inject blacklist
 U4 chunk   (ingest_stages.py:339)  smart_chunk(content) → select_strategy
            → strategies.py: hdt/semantic/recursive/proposition/hybrid
            → csv_chunker.py: table_csv / table_dual_index   ← BẢNG đi đây
 U5 enrich  (ingest_stages_enrich.py)  CR/quality-score (OFF/observability)
 U6 vi-seg  (ingest_stages.py)  segment_vi_compounds → content_segmented
            → trigger to_tsvector('simple', content_segmented) → search_vector
 U7 embed   (ingest_stages_store.py:120)  Jina v3 (late_chunking) → document_chunks
 final      (ingest_stages_final.py)  document_stats.ParsedEntity
            → document_service_index (price/category/attributes_json)
```

### 1.2 Luồng QUERY (LangGraph `query_graph.py`)
```
guard_input → cache_check+understand (parallel) → understand_query (intent)
 → query_complexity → [route]:
     adaptive_decompose / rewrite_and_mq / decompose / retrieve
 → RETRIEVE (retrieve.py:154):
     • condense_question (query_graph.py:1956) GHI ĐÈ state["query"]  ← BUG flaky
     • parse_range_query (retrieve.py:196) → stats route?
         - range/superlative → _do_stats_lookup (query_graph.py:2736)
           → query_by_price_range / top_by_price → synthetic chunk + graded_chunks
           → retrieve_mode="stats_index" → ROUTE THẲNG generate (bypass rerank/grade)
         - else → hybrid_search (pgvector_store.py:317): dense + BM25(+symbol-phrase)
 → rerank (Jina) → mmr_dedup → neighbor_expand → grade (CRAG)
 → generate (generate.py:114 đọc graded_chunks) → critique → guard_output(grounding HALLU=0)
 → persist
```

---

## 2. "EXPERT RAG" LÀ NHƯ NÀO — 5 tiêu chí + DẪN CHỨNG

| Tiêu chí | Định nghĩa expert | Dẫn chứng (research phiên này) |
|---|---|---|
| **Nhanh** | cache-hit <50ms, stats short-circuit, p95 hợp lý | EvidentlyAI: tách retrieval vs generation latency |
| **Đúng=100%** | Faithfulness reference-free gate + Coverage đo riêng | EvidentlyAI: "retrieval miss vs generation hallucinate — sửa đúng tầng" |
| **UX cao** | không refuse oan, coreference, multi-turn | n8n culture: greeting→qualify→answer |
| **Performance** | parallel gather, bounded concurrency, hybrid | Async mindset (CLAUDE.md) |
| **Cost thấp** | Jina (no nano-ingest), late-chunking (no per-chunk LLM) | Jina/arXiv 2409.04701: late-chunk = free context vs contextual-retrieval (LLM/chunk đắt) |

**Expert CHUNKING = (dẫn chứng hội tụ 6 nguồn):**
1. **Chunk theo Ý NGHĨA, không kích thước** — adaptive-chunking paper "no single method fits all".
2. **Block Integrity — không cắt giữa bảng/dòng/điều** — AdapChunk spec Tầng-5; Vision-Guided (arXiv 2506.16035) "separate chunk EACH ROW, include headers".
3. **Self-contained breadcrumb** — AdapChunk HDT `structural_path`; legal mình = 100% nhờ `[Chương>Điều]`.
4. **Dual-view (lexical vs dense)** — variants→BM25, field sạch→embed (thiết kế mình, web confirm hướng).
5. **Narrate-then-embed bảng** — AdapChunk Tầng-6; = contextual-retrieval ở mức table (Anthropic). ⚠️ chưa đo lift.
6. **Late chunking** — gửi FULL-doc 1 call cho Jina, pool token theo boundary; nếu pre-split thì `late_chunking:true` **vô hiệu** (Jina/Elastic). ROI cao nhất chưa verify.

**Eval expert (EvidentlyAI + PMC):** mỗi golden question gắn **gold-chunk map** → đo **Hit Rate / Recall@k** TÁCH KHỎI answer-score → miss tự gán tầng (retrieval vs generation). Tránh lặp lỗi 2026-06-03 (3 alembic sysprompt fix nhầm tầng retrieval).

---

## 3. HIỆN TẠI vs EXPERT — gap per layer

| Layer | Expert | Hiện tại | Gap |
|---|---|---|---|
| Parse bảng | RFC-4180 (ô-quoted nhiều dòng nguyên) | `text.split("\n")` (csv_chunker.py:42) | 🔴 ô variants vỡ mảnh |
| Header detect | theo column-signature | "dòng phẩy đầu tiên" (csv_chunker.py:236) | 🔴 lấy nhầm `1.唛头` |
| Block integrity | dòng/bảng không cắt | row-as-chunk ✓ nhưng oversized cram | 🟡 |
| Breadcrumb | mọi chunk có heading-path | HDT có (legal ✓), bảng KHÔNG | 🟡 header `NGÀY VỀ` mồ côi |
| Dual-view | variants→BM25, sạch→dense | dual_index CÓ nhưng chưa wire default | 🟡 |
| Late chunking | full-doc 1 call | `late_chunking:true` set — **chưa verify gửi full hay per-chunk** | 🔴 có thể vô hiệu |
| Structured field | answer/quantity/date/image → record | chỉ price/category surface | 🔴 xe thiếu field cho n8n |
| Boilerplate | de-weight lặp | không có | 🔴 Hán tự dìm embedding |
| Number standard | 1 canonical SSoT | ✅ `number_format.py` | ✅ DONE |
| Aggregation | numeric SQL node | ✅ stats_index + route | ✅ DONE |
| HALLU gate | grounding reference-free | ✅ guard_output | ✅ DONE |

---

## 4. CASE STUDY — control sao? (3 bot, evidence)

### 4.1 LEGAL (thong-tu-09-2020) — ✅ CONTROLLED 100%
- Chunk = HDT breadcrumb `[Chương N > Điều M]` (strategies.py:277) → self-contained.
- Load-test: "Điều 56 hiệu lực 01/01/2021" ✓, "báo cáo sự cố 24h Điều 54" ✓, trap mức-phạt refuse ✓.
- Dẫn chứng expert: arXiv 2409.13699 VN-legal section-based R@10=0.98 — mình khớp top-end.

### 4.2 SPA (test-spa-id) — 🟡 CONTROLLED ~83%
- ✅ Số: "dưới 500k" → Triệt Mép 129k... / "dưới **700.000**" → list (số chuẩn hoá hoạt động).
- ✅ Superlative: "đắt nhất" → Meso 3.000.000.
- 🔴 **"dưới 500k" FLAKY** — `condense_question` ghi đè `state["query"]` (query_graph.py:1956) → đôi lúc mất "dưới 500k" → refuse. **Fix: đọc `original_query` (1 dòng)**.
- 🟡 "rẻ nhất"→700k (lẽ ra 129k) — entity-name rác "Hiện tại"/"Gội đầu".
- 🔴 field `answer` không surface (chỉ name:price) — structured gap.

### 4.3 XE (chinh-sach-xe) — 🔴 CONTROLLED ~67%
- ✅ Bảo hành (prose, 5 chunk) → trả đúng (72h, 3 tháng đổi mới).
- ✅ Trap Michelin → refuse.
- 🔴 **"195/65R15 về hàng" REFUSE** — 3 lỗi chunking đồng thời:
  1. CSV split `\n` → ô variants 64-cách-gõ vỡ mảnh 39-53 ký tự.
  2. Header `NGÀY VỀ` ở chunk RIÊNG → data chunk mất nhãn cột.
  3. Hán tự `1.唛头` lặp mọi chunk → embedding sụp.
- ✅ Đã fix 1 nửa: code-token `195/65R13` intact (DB match 11 chunk, was 0) + symbol-phrase OR — nhưng dòng nát + "về hàng" không trong chunk → chưa ghép được.
- **Nhận thức đúng (n8n prompt):** xe = STRUCTURED LOOKUP, cần `results[]` đủ field `answer/price/quantity/date/image`, COUNT_RULE trả đủ record. → cần record-route, KHÔNG prose-RAG.

---

## 5. FIX STATUS — đã done chưa? (rule#0, HONEST)

### ✅ DONE + VERIFIED (có evidence)
| Fix | Evidence |
|---|---|
| Canonical number standard (`number_format.py`) | 21/21 format; 700,000✓ 1.200.000✓ 5000nghìn=5tr✓ |
| Wire ingest+query dùng chung parser | document_stats + query_range_parser delegate |
| Superlative aggregation (top_by_price) | đắt nhất Meso 3tr, rẻ nhất Gội 60k (DB proof) |
| Stats→generate route bypass | dưới 500k/700.000 list đúng (load-test) |
| BM25 code-token preservation (vi_tokenizer) | DB match 195/65r15 = 11 chunk (was 0) |
| Symbol-phrase OR cho codes (pgvector_store) | phraseto match target ✓ |
| Fix 6 git-env test + 9 orphan test | full suite 5916 pass / 0 fail |
| Synthetic chunk currency-neutral (bỏ VND) | compliance agent PASS |
| CLAUDE.md compliance | 6/6 PASS (zero-hardcode/domain-neutral/no-inject) |

### 🔴 CHƯA DONE (root-caused, plan sẵn, chưa code)
| Việc | Tầng | Đã biết gì |
|---|---|---|
| xe chunking nát | csv_chunker | RFC-4180 + header-signature + de-weight (file:line) |
| spa "dưới 500k" flaky | retrieve | parse từ `original_query` (1 dòng) |
| structured field (answer/quantity/date) | document_stats + stats route | surface attributes_json + record route |
| late-chunking wiring verify | ingest_stages_store | check gửi full-doc hay per-chunk |
| record_chunk_id ở ingest | stats_index repo | bulk_insert thiếu FK → doc-level fallback |
| narrate-then-embed per-table | csv_chunker | OFF, cần đo lift trước |

### ⚠️ COMMIT nhưng CHƯA PUSH
- Commit `45e3920` local (2121 file, 0 secret). Push bị harness chặn → user tự `git push`.

---

## 6. ROADMAP — đường tới Expert (ranked theo ROI)

1. **[1 dòng]** spa flaky: parse range từ `original_query`. → hết flaky.
2. **[verify, rẻ]** late-chunking: check ingest gửi full-doc cho Jina (nếu pre-split = đang vô hiệu suốt nay). ROI cao nhất.
3. **[HIGH]** xe chunking: RFC-4180 parse + header-signature + de-weight boilerplate. → hết nát manifest.
4. **[HIGH]** structured field: surface `attributes_json` (answer/quantity/date/image) + record-route `results[]`. → xe + n8n.
5. **[MED]** dual-index FAQ default + `record_chunk_id` ở ingest.
6. **[MED]** narrate-then-embed per-table (đo lift trước — rule#0).

Mỗi việc: unit test + load-test 3 bot trước/sau + HALLU=0 gate + grep zero-hardcode/domain-neutral. Rollback nếu HALLU>0 / coverage giảm.

---

## 7. Index evidence (đọc thêm)
- `reports/DEEPDIVE_CHUNKING_20260617.md` — chunking gap file:line
- `reports/DEEPDIVE_RETRIEVAL_20260617.md` — flakiness root cause
- `reports/DEEPDIVE_COMPLIANCE_20260617.md` — CLAUDE.md 6/6 + structured gap
- `reports/N8N_PROMPT_CULTURE_20260617.md` — 15 n8n prompt → conversion
- `docs/dev/N8N_TO_RAGBOT_PROMPT_MINDSET.md` — guide chuyển hoá
- `reports/validate_20260617/fixverify_raw.jsonl` — load-test 12 câu raw
