# MASTER — Tổng hợp TẤT CẢ vấn đề Ragbot (phiên 2026-06-23 → 06-25)

> Gom toàn bộ bug/gap/RÁC tìm được qua nhiều ngày debug + CHUẨN-audit vs tldw_server + test 40Q golden.
> Mọi dòng có evidence (`file:line` / DB / live-trace). Nhãn: ✅ FIXED · 🔴 OPEN. rule#0 áp dụng.

## 0. GỐC RỄ (verified thesis) — 2 tầng
1. **INPUT-DATA CONTROL — column-ROLE silent-drop** (tầng GỐC, đòn bẩy cao nhất). Cột header ngoài
   từ-vựng vi cố định → drop âm thầm thành `col_N`/`attributes_json` không search được. KHÔNG crash —
   degrade IM LẶNG → "ingest success" nhưng cột giá/tồn/date/ảnh đã chết. `INPUT_DATA_CONTROL_FLOW_DESIGN.md`
   tự verdict "thesis TRUE, load-bearing".
2. **Vài "dây chưa nối" ở code** (RÁC = có nhưng chưa chuẩn): reranker config-dead, stats short-circuit
   topK=1, các orphan (LLM-selector, block-pipeline, semantic-cosine, narrate passthrough).

**KHÔNG phải**: "RAG yếu khắp nơi", "nhiều format" (SHAPE machine đạt **91%** PASS+GRACEFUL), "answer-flow
dở" (hybrid+RRF+cliff+CRAG+grounding mạnh hơn open-notebook, ngang tldw), "LLM dở" (LLM trả đúng cái nó
được đưa).

**Điểm số đo thật**: NotebookLM chấm bot xe **44%** (18/41) → sau các fix phiên này **72%** (29/40).

---

## 1. ✅ ĐÃ FIX phiên này (commits trên `fix-260623-ingest-expert`)

| # | Bug | Gốc rễ | Commit |
|---|---|---|---|
| F1 | xe-4 bảo hành 0-chunk | ingest 06-23 lúc Jina key chết; re-ingest từ URL | (re-ingest) |
| F2 | **state-flip vỡ MỌI ingest** (regression em tự gây) | SQL `:s='active'` asyncpg AmbiguousParameter | `addacb8` |
| F3 | stats route bịa số ("26") không bị chặn | grounding skip cho stats → bật lại (per-bot opt-out) | `062d6fa` |
| F4 | "mã '2-R13 155/80 LPD'" không tìm thấy | code regex cắt SKU ở space; + attributes_json không search | `e3b2cb6` |
| F5 | rechunk bytes-doc fail "source_url required" | Document entity invariant sai cho bytes-upload | `90492f4` |
| F6 | câu "bao nhiêu NGÀY" route nhầm stats → tire junk | parse_list_query coi "bao nhiêu" là catalog-count | `f717dda` |
| F7 | ⭐ **reranker CHẾT toàn hệ thống** | config drift `provider=jina` ⊥ `model=zerank-2` → NullReranker | `cf7f09b` |
| F8 | stats-index trùng entity (xe ×3) | re-ingest insert không idempotent | `f684c82` |
| F9 | UI "tài liệu = 0" | doc active nhưng deleted_at set | `52d04ff` |
| F10 | reranker 429 burst | thiếu per-key concurrency gate | `d42bace` |
| ADR | 0003 entity-join + 0004 long-context | (Proposed) | `4e63975` |

---

## 2. 🔴 OPEN — INPUT-CONTROL (tầng gốc, ưu tiên cao nhất)

| Gap | Vấn đề | Evidence | Tier |
|---|---|---|---|
| **G1** | column-role **exact-match vi-only** → cần fuzzy/substring/synonym | `document_stats.py:135-164,323` | T1 |
| **G2** | KHÔNG multi-language — `column_role_tokens[locale]` DB-seed (EN/Spanish/Thai → 0 role) | `document_stats.py` (no locale) | T1 |
| **G3** | U5 enrich **SKIP cho table row** → chunk bảng thiếu context-header → embed kém. ADAPT tldw `_build_contextual_header` (breadcrumb) | `ingest_stages_enrich.py:180`; tldw `structure_aware.py:696` | T1 |
| **G4** | ingest KHÔNG warn khi cột bị demote → attributes_json (silent "success") | `document_stats.py:460` | T1 |
| **G-Linearize** | row-as-chunk mất NHÃN cột ("dòng 5: Giá=700k, Tồn=404") → Nhóm B HALLU | `ingest_stages.py` | T1 |
| **G-Wire** | checker `check_happy_case.py` **offline-only**, chưa wired vào ingest (admission-controller) | offline script | T1 |
| **G-OOM** | file lớn chỉ reject, KHÔNG map-reduce split sub-doc (224KB→2643 chunk OOM) | `ingest_core.py` | T2 |
| **G-Batch** | Jina embed batch cap (≤32/req) cần verify | — | T2 |

---

## 3. 🔴 OPEN — DATA (bot xe cụ thể)

| Vấn đề | Evidence | Hệ quả |
|---|---|---|
| **Cột date1/ảnh/tồn-kho KHÔNG vào stats-index** (DB-verified **0/492 entity** có date1/drive.google) | DB query | N5 Date/Ảnh **0/5**, N2 Stock **4/8 fail** |
| 4 sheet RỜI mỗi cái 1 thuộc tính, không join → cùng sản phẩm = 4-6 fragment | DB (165/65R14 = 6 entity) | = entity-join ADR-0003 |
| File test xe = anti-pattern (xe-3 synonym-export 62 cột; xe-2 multi-header CJK) | doc phân loại | "happy-case" thật chưa đạt |

→ **N5/N2 = "CHƯA CÓ DATA"** (cột bị drop ở extraction), KHÔNG phải LLM/chunking/topK. Fix = capture
stock/date/image vào index (G1+G-Linearize) + re-ingest + entity-join.

---

## 4. 🔴 OPEN — CODE "RÁC" (CHUẨN-audit vs tldw, `CHUAN_AUDIT_VS_TLDW_20260625.md`)

| RÁC | Vấn đề | Evidence | Fix |
|---|---|---|---|
| **Stats short-circuit topK=1** | route stats → ghim 1 synthetic chunk, **bỏ qua rerank/mmr/grade**; raw rows suppressed | `retrieve.py:537-577`; `query_graph.py:2456` | cho phép merge thêm chunk / fallback khi field thiếu |
| **B-1 LLM chunking selector orphan** | built `llm_resolver.py` nhưng bootstrap 0 provider → U4 chạy rule-selector | `ingest_stages.py:563` | wire vào bootstrap (config-gated) |
| **B-2 block-pipeline no-op** (Excel/CSV/Docx) | parser `→ list[dict]`, blocks chỉ set ở OCR branch → flag ON mà chạy rỗng | `document_worker.py:448` | parser emit typed Block → cascade unblock atomic+narrate |
| **narrate passthrough-default** | log `narrate_then_embed_applied` là passthrough (enabled=False) → table embed raw | `narrate_dispatch.py:107` | log phân biệt + carry block_type (cần B-2) |
| **atomic-protect OFF + orphan** | `smart_chunk_atomic` 0 callers; gate OFF → TABLE/FORMULA cắt giữa | `__init__.py:490,653` | route qua smart_chunk_atomic sau B-2 |
| **semantic = lexical** | live `_chunk_semantic` = SequenceMatcher; cosine variant `_chunk_semantic_embed` 0 callers | `strategies.py:416,487` | wire cosine sau A/B |
| **rrf_round_robin orphan** | entity-quota fairness không import vào graph | `nodes/rrf_round_robin.py` | wire hoặc xóa |
| neighbor_expand OFF / HyDE OFF | mặc định tắt → atomic-section doc bị mù lân cận | `_15:20`; `_00:129` | A/B measure |

---

## 5. 🔴 OPEN — CONFIG / PERF

| Vấn đề | Evidence | Tier |
|---|---|---|
| guardrail_rules KHÔNG seed → input guardrail rỗng | README known gap | T2 |
| p95 22s vs SLA 8s | load test | T2 |
| ragas coverage metric còn stub | `ragas_metric_adapter` | T2 |

---

## 6. KẾT LUẬN CHIẾN LƯỢC (đừng đổi triết lý — sửa đúng chỗ)

- **Triết lý ĐÚNG = NORMALIZE-to-IR** (1 happy-case markdown IR + checker + normalizer). Mình đã làm **70%**
  (7 parser registry). KHÔNG đi ABSORB-zoo của tldw (maintenance vô hạn) cũng KHÔNG long-context-only của
  NotebookLM (không scale 100K docs multi-tenant). **EVOLVE không REWRITE.**
- **Cái mình CÓ mà tldw + NotebookLM KHÔNG**: checker (admission-control) + dual-representation (stats-index
  ParsedEntity). Vừa là sophistication vừa là điểm giòn (cần column-role sạch).
- **tldw KHÔNG có column-role recognition** (grep 0 hit) → mình đã MẠNH HƠN tldw ở bài toán gốc. Chỉ bê 1
  thứ: `structure_aware._build_contextual_header` breadcrumb (G3).
- **Đòn bẩy fix (rẻ→đắt)**: G1+G4 (role fuzzy + warn) → N5/N2 data capture → G3 breadcrumb → entity-join →
  (G2 locale, B-2 block, long-context = sau).

## 7. Plan đã viết
- `plans/20260625-input-control-silentdrop-multilocale/plan.md` (G1-G4 + tokenizer, TDD).
- ADR `docs/adr/0003-entity-join-multi-sheet.md` + `0004-long-context-mode-small-corpus.md`.
- `reports/INPUT_CONTROL_ROOT_CAUSE_3PHILOSOPHIES_20260625.md` + `CHUAN_AUDIT_VS_TLDW_20260625.md`.

**Bottom line**: gốc là **input column-role silent-drop** (tầng trên), kéo theo retrieval nghèo → answer
trông yếu. Bug ở tầng DATA/INPUT, đúng chuỗi `hallucination ← retrieval ← chunking ← DATA`. Answer-flow +
khung kiến trúc đã CHUẨN — chỉ cần nối dây input.
