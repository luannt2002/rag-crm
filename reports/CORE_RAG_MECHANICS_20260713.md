# Cơ chế CORE RAG — Query · Ingest · Chunking · CRAG — deep-dive + suy luận

> **Ngày:** 2026-07-13 · **Phương pháp:** 4 agent read-only trace cơ chế thuật toán
> từng luồng (không phải audit flag) → main session (Opus) tổng hợp + suy luận.
> Mọi threshold/decision-point neo `file:line`. Đây là tài liệu **hiểu máy móc hoạt
> động**, để đọc là biết dữ liệu biến đổi ra sao qua từng node.

---

# PHẦN 1 — QUERY PIPELINE (LangGraph)

## 1.1 DAG (topology)

```
guard_input ──[blocked]──► persist
   │
cache_check_and_understand_parallel ──[cache HIT]──► persist
   │ (MISS)
understand_query  (intent + condense)
   │
query_complexity ──[complex]──► adaptive_decompose ─┐
   │ (simple)                                        │
router ──[multi_hop/comparison, conf≥0.7]──► decompose┤
   │  ──[factoid/greeting/oos]──► retrieve            │
   │  ──[else]──► rewrite_and_mq_parallel ────────────┤
   │                                                  ▼
   └──────────────────────────────────────────────► retrieve
                                                       │
                              ┌──[0 chunks | stats]──► generate (short-circuit)
                              ▼
                            rerank → mmr_dedup → neighbor_expand → grade
                                                       │
                              ┌──[not adequate, retry<1]──► rewrite_retry ──► retrieve (loop×1)
                              ▼
                            generate → critique_parse → guard_output
                                                       │
                              ┌──[blocked | reflection off]──► persist
                              ▼
                            reflect ──[no answer]──► generate (regen)
                                                       │
                                                    persist → END
```

**8 điểm rẽ nhánh có điều kiện** (routing.py). **Short-circuit bỏ hẳn generation:** input-block · cache-hit · 0-chunk refuse.

## 1.2 Node mechanics (condensed) — INPUT → LOGIC → OUTPUT

| Node | Cơ chế cốt lõi | Threshold |
|---|---|---|
| **guard_input** | check_input guardrail; block → OOS template DB | — |
| **check_cache** | 2-tier: exact-hash + cosine pgvector; key = f(sysprompt+oos+vocab) × corpus_version | **cosine ≥ 0.97** (`_04:15`); skip multi-turn |
| **understand_query** | 3 tầng rẻ→đắt: Redis memo → heuristic → LLM condense+intent | heuristic conf ≥ **0.85** (`_21:226`); fallback intent=factoid |
| **query_complexity** | additive scoring: comma×0.5, conj×0.4, num×0.3, ?×0.6, len/20 | score ≥ **1.2** → complex (`_14:127`); structural 1-ref ≤80 char → simple |
| **router** | decompose gate (intent∈{multi_hop,comparison} + words≥8 + conf≥0.7); skip-rewrite {factoid,greeting,oos} | conf ≥ **0.7** (`_14:112`) |
| **retrieve** | hybrid vector+BM25 RRF; top_k per-intent | factoid **15**, comparison **25**, multi_hop **30**, aggregation **40** (`_16:84`) |
| **rerank** | cross-encoder → cliff filter → hard cap to LLM | top_n factoid **7**; cliff floor 0.2/gap 0.35; **cap 5 chunks→LLM** (`_01:226`) |
| **mmr_dedup** | near-dup drop | sim **0.98**, λ=0.7 |
| **grade** | stats-bypass; smart-skip (top≥0.7); batched CRAG grade | skip ≥ **0.7** (`_10:166`) |
| **generate** | refuse short-circuit (0 chunk); intent→binding purpose; 1 LLM call | — |
| **guard_output** | empty→numeric→brand→citation→grounding (mọi block dùng OOS template DB) | grounding threshold **0.3** |
| **reflect** | self-RAG keep/rewrite; default OFF | max_retry **1**; skip floor 0.30 |

## 1.3 Hai đường trả lời (CỰC QUAN TRỌNG)

| | **Path A — stats-synthetic** | **Path B — raw chunk** |
|---|---|---|
| Kích hoạt | parsed filter khớp (range/code/price-of/list/superlative) + không structural-ref + không decompose | mọi câu còn lại |
| Nguồn | SQL trên `document_service_index` | vector/hybrid retrieval |
| Score | **1.0 sentinel** | cross-encoder [0,1) |
| Funnel | **BỎ QUA** rerank/cliff/grade (score 1.0 miễn nhiễm) | qua đủ funnel + grade CRAG |
| Chunk | 1 synthetic record sạch (raw per-entity KHÔNG append) | nhiều chunk thật |
| Grounding | ON (threshold 0.3) | ON |

→ **Suy luận:** bug #13 (bịa giá Neoterra, score 0.29) đi **Path B** — vì dòng NULL giá không khớp stats filter → rơi xuống raw-chunk có số rác "26". Nếu route được sang Path A (SQL) thì đã có null-price marker. Đây là lằn ranh quyết định giữa "an toàn" và "lọt".

## 1.4 Số LLM call thật (đo bằng trace)

- **Factoid:** ~1–3 call (understand + generate + grade/grounding tuỳ smart-skip). Tốt nhất = **1** (heuristic-cache + grade skip + async grounding).
- **Comparison:** ~4–5 call (understand + decompose + grade + generate + grounding). Nhiều hơn factoid 2–4 call.

→ **Suy luận:** đây là lý do latency comparison > factoid, và tại sao "reflect chạy vô điều kiện" (Tier-A perf) đáng bỏ — nó cộng thêm 1 call trên đường đã dài.

---

# PHẦN 2 — INGEST PIPELINE (bytes → indexed chunks)

## 2.1 DAG

```
POST /documents/create → 202 → outbox(DocumentUploaded) → Redis Stream
   → document_worker → DocumentService.ingest:
      U1 validate → U2 parse (mime→ext→byte-sniff) → upsert documents row
      → U3 clean → U4 chunk → U5 enrich → U6 vn-segment
      → hash-diff (chunk nào đổi) → U7 embed+store
      → finalize (state flip + stats extraction → document_service_index)
   → outbox(DocumentIngested) → job success
```

## 2.2 Stage transformations (INPUT → TRANSFORM → OUTPUT)

| Stage | Biến đổi cốt lõi | Ghi chú/hazard |
|---|---|---|
| **U2 parse** | `sniff_real_mime` (magic bytes nếu mime ambiguous) → registry `detect_parser` (kreuzberg_markdown TRƯỚC pdf) → structured markdown. Excel/Sheets = **1 dict/row** (row-shape stamp) | ⚠️ parse chạy **2 lần** (worker + service); parser lỗi → **flat passthrough** (mất cấu trúc) |
| **U3 clean** | sanitize (HTML strip, NFC, prompt-injection→REDACTED) + `_clean_document_text` | ⚠️ repeated-header strip có thể drop dòng lặp hợp lệ |
| **U4 chunk** | whole-doc gate (nhỏ+không CSV → 1 chunk) → policy → parent-child OR `analyze_document`+`select_strategy`+`smart_chunk`. Row-shape → **parser_preserve** (bypass smart_chunk giữ 1-row-1-chunk) | ⚠️ **coverage gap DETECT nhưng KHÔNG sửa** (`find_dropped_numbers`, `check_chunk_gaps` observe-only) |
| **U5 enrich** | CR (Anthropic context prefix, LLM) — **TẮT prod**; Jina late_chunking cấp context ở embed pass thay | 3 đường enrich LLM đều OFF; fail → original chunk |
| **U6 vn-segment** | `segment_vi_compounds` (underthesea) cho BM25; chỉ lưu khi đổi | timeout → original (BM25 giảm recall) |
| **hash-diff** | `_compute_chunk_hashes(enriched)` vs existing → chỉ embed chunk đổi | hash trên **enriched** (đổi prefix → re-embed) |
| **U7 embed+store** | embed-text strategy (structural→raw_only, row→field_selective); **fail-loud** nếu len mismatch → doc=failed; bulk INSERT 1 statement | ⚠️ whitespace chunk → placeholder vector (no signal) |
| **finalize** | state flip (leaf coverage ≥ floor → active); stats extraction | stats best-effort, không block ingest |

## 2.3 Stats extraction — row → entity (bug-relevant)

`parse_table_chunks` (`document_stats.py:1053`):
1. Đọc `raw_chunk` (PRE-enrichment) — tránh nhiễu prose.
2. **Header detection** `_is_header_row` (`:349`) — **shape-based, zero-vocab**: (a) không cell nào parse ra money, (b) row ≥2 cell ngay trên `|---|` = header. Vocab chỉ là hint fallback.
3. **Role** `_column_roles` — 3 tier: owner-declared (authoritative) > vocab/structural infer (exact 100 > phrase 60 > word 30, hoà→SKIP) > generic attribute.
4. **Row→entity** `_extract_entity_from_row` (`:630`): name (column role / shape-pick nếu `name_by_shape` / positional first-non-money); price (first money→primary, second→secondary); **`col_N` fallback khi header sai** → mất nghĩa cột (documented "col_N CRUX").
5. **Positive-evidence gate:** dòng NULL giá chỉ giữ nếu pipe/tab-delimited HOẶC header shape-detected — comma-fragment không giá bị **DROP**.
6. Dedup (richest wins) → **delete-then-insert** vào `document_service_index`.

## 2.4 Data model (3 bảng)
- **documents** — upsert `ON CONFLICT uq_doc_tool`; soft-delete (forensics).
- **document_chunks** — `content` (enriched), `content_segmented` (VN), `content_hash` (enriched sha), `embedding` (pgvector, NULL cho parent), `metadata_json`, `chunk_type`, `parent_chunk_id`.
- **document_service_index** — 1 row/entity: name, category, price_primary/secondary, attributes (col_N/header→value), entity_synonyms.

## 2.5 Chỗ mất/hỏng dữ liệu âm thầm (13 điểm)
Nổi bật: parser lỗi→flat; **coverage gap chỉ log**; CR fail→original; **col_N** khi header sai; comma-fragment không giá bị drop; **2 cách derive tool_name** khác nhau (`slugify` vs `derive_tool_name` — latent bug).

---

# PHẦN 3 — CHUNKING (document → chunks)

## 3.1 Cây chọn strategy (L5 cross-check ON mặc định)

```
promote_vn_hierarchical_headings (≥3 VN markers → markdown #)
│
├─ is_csv & 0 heading & 0 vn_marker ──► table_csv (row-as-chunk)
├─ (heading + vn_marker) ≥ 3 ────────► hdt
└─ weighted scorer → best/confidence
     ├─ conf < 0.45 → recursive
     │      └─(L5) < 0.6 → HYBRID   ← điểm mấu chốt
     ├─ best=hdt →(L5) heading<5 → semantic
     ├─ best=semantic →(L5) avg<50 → proposition
     └─ else keep best
```

⚠️ **Suy luận:** L5 rule-1 (`confidence < 0.6 → hybrid`) fire rất thường (best score hay rơi 0.45–0.6) → **hybrid mới là default THẬT cho prose mơ hồ**, không phải recursive. Đây là "default ẩn" mà đọc constant không thấy.

## 3.2 Các strategy — thuật toán + hazard

| Strategy | Thuật toán | Hazard |
|---|---|---|
| **recursive** | H1 hard-break → block split; table ≤3072 giữ nguyên, oversized → row-group giữ header; text ≤1024 giữ, else RecursiveSplitter overlap 128 | text không separator → cắt raw 1024 giữa từ |
| **table_csv** | header=lines[0]; mỗi row → 1 chunk `header\n row`; oversized row giữ nguyên (không split) | doc có intro-comma trước header → intro thành "header" mọi row; empty-cell row **drop** |
| **table_dual_index** | emit CẢ group chunk (cả bảng ≤4000) LẪN row chunk | — |
| **proposition** | split ở connector `và\|hoặc\|nhưng\|and\|or\|but` (chỉ COORDINATING); merge clause ngắn | ⚠️ connector là literal VN+EN (ngôn ngữ 3 không split); **min_clause_len=20 CHỮ không phải TỪ** → "Giá: 5 tỷ" (9 char) bị gộp vào chunk kế → **mờ 2 fact** |
| **parent-child** | parent 1024 (overlap 0) → child 256/overlap 50; child=embed unit, parent=served | non-table parent >256 không separator → cắt raw 256 |
| **semantic** | lexical (SequenceMatcher+Jaccard) split khi sim<0.3; embedding variant OFF | VN paraphrase sim≈0 → over-segment |
| **atomic protect** | formula/image/code giữ nguyên | ⚠️ **OFF mặc định** → `$$...$$` bị cắt giữa công thức |

## 3.3 Coupling enrich ↔ hash (subtle)
- Embedding = `contextual_prefix + narrated_text`; nhưng `Chunk.content_hash` = original only → prefix đổi vector KHÔNG đổi hash. *(Lưu ý: ingest `_compute_chunk_hashes` lại hash trên ENRICHED — 2 hash khác nhau cho 2 mục đích. Cần thống nhất.)*

---

# PHẦN 4 — CRAG (Corrective RAG)

## 4.1 Cây quyết định

```
grade(state):
 ├─ graph iterations > 8 → top-2, adequate=TRUE (loop cap)
 ├─ 0 chunk → adequate=FALSE
 ├─ retrieve_mode "stats*" → pass-through, adequate=TRUE (§bypass)
 ├─ SMART-SKIP: top score ≥ 0.7 → all RELEVANT, adequate=TRUE (no LLM)
 ├─ GRADE (1 batched LLM call): yes/no/partial → relevant/irrelevant/ambiguous
 │    · timeout 2.0s → all AMBIGUOUS, adequate=TRUE
 │    · compound intent: irrelevant→ambiguous (lenient)
 │    · giữ relevant+ambiguous
 ├─ adequacy = (relevant_count ≥ 1) AND (fraction ≥ 0.0[inert])
 ├─ has_relevant → adequate=TRUE
 ├─ all_irrelevant → fallback gate (score ≥ per-intent floor | ≥ 0.5×top) → recover or FALSE
 └─ else (ambiguous, ít relevant): lenient|retries≥1 → TRUE else FALSE
 → route: adequate → generate; else rewrite_retry → retrieve (loop ×1)
```

## 4.2 Điểm YẾU cốt lõi của "Corrective"

1. **Không có corrective retrieval thật:** correction duy nhất = rewrite query → query **CÙNG vector store** 1 lần (`max_grade_retries=1`). **KHÔNG web-search, KHÔNG KB ngoài, KHÔNG re-decompose** như CRAG chuẩn (Yan 2024). grep xác nhận 0 web_search node.
2. **Adequacy gần như KHÔNG BAO GIỜ hard-fail:** 6 đường ép `retrieval_adequate=True` (timeout, total-fail, iteration cap, stats, smart-skip, lenient). `False` thật chỉ sống sót ở all-irrelevant + fallback rỗng.
3. **Fraction gate inert** (0.0) → adequacy = "≥1 relevant".
4. **Package `crag_grader/*` là CODE CHẾT** — đăng ký DI nhưng KHÔNG ai gọi; live logic inline trong `grade.py`. Sửa package = 0 tác dụng runtime (maintenance trap).

→ **Suy luận:** CRAG ở đây thực chất là **"lenient grader + 1 rewrite retry"**, không phải Corrective RAG đúng nghĩa. Nó **ưu tiên trả lời một phần hơn là từ chối**, và **đẩy toàn bộ trách nhiệm HALLU=0 xuống `grounding_check` cuối**. Điều này giải thích vì sao bug coverage (spa listing, #20) tồn tại: khi retrieval miss, CRAG không "sửa" bằng cách tìm nguồn khác — nó chỉ nới lỏng rồi trả lời với chunk đang có.

---

# PHẦN 5 — SUY LUẬN TỔNG HỢP (nối cơ chế → bug → thiết kế)

## 5.1 Vì sao kiến trúc tốt mà vẫn có bug — chuỗi nhân quả

**Triết lý thiết kế lộ ra từ cơ chế:** toàn hệ được xây theo nguyên tắc **"fail-open + defense-in-depth"** — mỗi tầng ưu tiên **trả lời được** hơn là chặn, và tin rằng tầng cuối (`grounding_check` + numeric gate) sẽ bắt lỗi. Bằng chứng: CRAG ép adequate=True 6 đường; grade smart-skip; refuse chỉ khi 0 chunk; enrich fail→original; coverage gap chỉ log.

**Hệ quả suy luận:** triết lý này **đúng cho coverage** (bot ít từ chối oan) nhưng **nguy hiểm cho HALLU NẾU tầng cuối để observe**. Và tầng cuối ĐANG observe (audit flag). → Fail-open mà lưới cuối không chặn = **lỗi chảy thẳng ra user**. Đây là mắt xích nối "kiến trúc tốt" với "bug thật".

## 5.2 Ba "default ẩn" mà đọc code thường không thấy
1. **Hybrid là chunking-default thật** (L5 coerce), không phải recursive.
2. **Path B (raw chunk) là đường của câu khó** — stats Path A chỉ nhận câu khớp filter; câu NULL-giá/size rơi Path B nơi số rác lọt.
3. **CRAG = lenient + 1 retry**, không phải corrective thật — adequacy hiếm khi fail.

→ Ba cái này giải thích trọn 3 bug: #13 (Path B + số rác), coverage (CRAG không corrective), degeneration (không tầng nào bắt output hỏng).

## 5.3 Điểm mạnh cơ chế (đáng giữ)
- **Route-by-parsed-filter** (không route theo intent label) — stats route quyết định bằng cấu trúc query, chính xác hơn.
- **Shape-based header detection** (zero-vocab) — đúng tinh thần domain-neutral, đa ngôn ngữ.
- **Config-on-state** (không closure build-time) — cross-tenant safe.
- **Fail-loud embed guard** — không bao giờ lưu NULL embedding (doc→failed).
- **Two-phase parent-child**, **row-preserve bypass**, **positive-evidence gate** — chống conflation giá đúng bài.

## 5.4 Điểm yếu cơ chế (root, không phải triệu chứng)
1. **Không có corrective retrieval thật** (CRAG danh nghĩa) → coverage miss không tự sửa.
2. **Path B không có null-price/số-rác handling** → #13.
3. **Không tầng nào kiểm output well-formed** (degeneration) → #8.
4. **Coverage gap chỉ observe** (U4) → mất dữ liệu âm thầm ở ingest, phát hiện mà không sửa.
5. **Fail-open + guard observe** = HALLU chảy ra (mắt xích 5.1).
6. **Code chết** (crag_grader package) + **2 tool_name derivation** + **2 content_hash** = bẫy bảo trì.

## 5.5 Kết luận (1 đoạn)

> Core RAG của Ragbot **được thiết kế tinh vi theo triết lý fail-open + defense-in-depth**, với nhiều cơ chế vượt SOTA phổ thông (shape-header zero-vocab, route-by-filter, deterministic numeric gate, fail-loud embed). NHƯNG triết lý fail-open **chỉ an toàn khi lưới cuối cùng thật sự chặn** — mà lưới cuối đang để observe, CRAG không corrective thật, và không có tầng bắt output hỏng. Kết quả: mỗi tầng "nhường" trách nhiệm cho tầng sau, đến tầng cuối thì "nhường" cho log. Bug không nằm ở 1 chỗ — nó nằm ở **chuỗi nhường trách nhiệm chưa có điểm dừng cứng**. Fix đúng = đặt lại **điểm dừng cứng** ở đúng tầng (bật numeric block, thêm degeneration guard, corrective retrieval thật cho intent listing/comparison), không phải sửa từng node.

---

## Tài liệu liên quan
- `reports/FLAG_ONOFF_AUDIT_20260713.md` — mọi công tắc ON/OFF
- `reports/BEST_PRACTICE_AUDIT_20260713.md` — đối chiếu SOTA + suy luận
- `reports/CODE_DEEPDIVE_REVIEW_20260711.md` — review 5 flow + SOLID
- `reports/PERF_LATENCY_INNOCOM_CONTROL_20260711.md` — độ trễ + xử lý innocom
