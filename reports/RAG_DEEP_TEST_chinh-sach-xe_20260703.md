# RAG DEEP-TEST — chinh-sach-xe — quy trình chi tiết + chẩn đoán per-layer

> Server: `ragbot-py.service` (đã restart, load code working-tree có Q7-Q12 + nhóm A/B fixes). Health 200.
> Ground-truth: 20 QA từ Google Sheet (rows 8-27) → `tests/scenarios/chinh-sach-xe-qa20_scenario.json`.
> Harness: `scripts/debug_qa_layers.py` (per-layer failure classify) + full-trace capture + DB cross-check.
> Mọi số liệu = chạy thật trên server, có evidence. Không đoán.

---

## PHẦN 1 — LUỒNG UPLOAD (INGEST) chi tiết + config thật

### 1.1 Chunking mặc định — CHAR, không phải token
| Tham số | Giá trị | Nguồn |
|---|---|---|
| **Đơn vị cắt** | **KÝ TỰ (char)**, KHÔNG phải token | `chunking/__init__.py:341` `if len(content) > chunk_size` |
| `DEFAULT_CHUNK_SIZE` | **1024 chars** | `constants/_00:38` |
| `DEFAULT_CHUNK_OVERLAP` | **128 chars** | `constants/_00:39` |
| `DEFAULT_CHUNK_MAX_SIZE` | 1024 | `constants/_00:44` |
| Parent-child (nếu bật) | parent 1024 / child 256 / overlap 50 | `constants/_10:268-270` |
| CSV | **row-based** (không cắt theo char) — mỗi row/nhóm row = 1 chunk | `shared/chunking/csv_chunker.py` |

→ `chinh-sach-xe` `plan_limits` rỗng → dùng default 1024 char. Docs là CSV (11111, 2222, 3333) → **chunking theo hàng CSV**, không phải char-split.

### 1.2 Luồng ingest 8 step (đã instrument trong `request_steps`)
```
POST /api/ragbot/documents/create
 └─ ingest_parse    → mime→ext→byte-sniff → detect_parser → markdown-có-cấu-trúc
 └─ ingest_clean    → cleaner (⚠ I10: xóa dòng lặp ≥3× <100char — risk xóa data cell)
 └─ ingest_chunk    → smart_chunk / csv_chunker (char 1024 | CSV row)
 └─ ingest_enrich   → contextual prefix (nếu cr_enhanced_enabled)
 └─ ingest_vn_segment → VN word-segment cho BM25
 └─ ingest_validate → char-coverage check (⚠ I6: chỉ observe, không repair)
 └─ ingest_embed_store → embed (ZeroEntropy 1280-dim) + INSERT document_chunks
                         (⚠ I12 đã fix: batch <32767 bind) + stats_index extract
```

### 1.3 Trạng thái chinh-sach-xe (verify DB thật)
- 5 docs live: 11111 (CSV 86k), 2222 (CSV 6.9k), 3333 (CSV **253k**), 44444 (HTML 2.5k), 5555-tomtat (txt).
- **476 chunks, 476/476 CÓ embedding** ✓ (embedding lưu chuẩn, không NULL).
- **451 stats_index entities** (price/product) ✓.

---

## PHẦN 2 — LUỒNG QUERY chi tiết (nhận câu → trả lời)

### 2.1 Pipeline 20+ step (mọi step log vào `request_steps`: duration_ms, input/output_tokens, status)
```
Câu hỏi
 ├─ guard_input          → chặn injection/abuse
 ├─ cache_check          → exact-hash cache
 ├─ semantic_cache_check → pgvector cache (threshold 0.97)
 ├─ understand_query     → HIỂU CÂU: heuristic layer (⚠ Q9 đã fix: complex→LLM) → intent
 ├─ query_complexity     → chấm độ phức tạp (cascade routing)
 ├─ router_select_model  → chọn model theo intent
 ├─ multi_query_fanout   → sinh N biến thể query (nếu multi_query_enabled)
 ├─ rewrite              → VIẾT LẠI CÂU (condense history, HyDE)
 ├─ adaptive_decompose   → tách sub-query (comparison/multi-hop)
 ├─ retrieve             → QUERY DB: 2 route:
 │     • STATS route: câu giá/đếm/range → SQL document_service_index (⚠ Q8 đã fix per-column)
 │     • VECTOR route: hybrid dense(cosine)+sparse(BM25) → RRF → topK
 │       topK = retrieval_top_k (default) → rerank_top_n (default 7)
 ├─ rerank               → XẾP LẠI: reranker cho điểm lại (⚠ Q12 đã fix: cache CB per bot)
 ├─ filter_min_score     → cắt chunk dưới floor (0.3)
 ├─ mmr_dedup            → khử trùng lặp (diversity)
 ├─ grade (CRAG)         → grader chấm chunk relevant? (drop nếu irrelevant)
 ├─ litm_order           → sắp xếp chống "lost-in-the-middle"
 ├─ prompt_build         → lắp prompt (system_prompt bot + chunks trong tag <documents>)
 ├─ prompt_compression   → nén prompt (nếu bật)
 ├─ generate             → LLM TRẢ LỜI
 ├─ guard_output         → chặn system-leak + grounding judge (HALLU-net)
 ├─ citations_extract    → trích citation
 └─ persist              → lưu request_logs + request_steps
```

### 2.2 6 tầng lỗi (để biết SAI Ở ĐÂU — mỗi miss quy về 1 tầng)
| Tầng lỗi | Nghĩa | Signal |
|---|---|---|
| `RETRIEVAL_ZERO` | 0 chunk retrieve | chunks=0 |
| `RETRIEVAL_MISS` | top_score < floor → chunk đúng không lên topK | **chunking/embedding** yếu |
| `CRAG_REJECT` | chunk có nhưng grader drop hết | **grader** quá gắt |
| `WRONG_CHUNK` | retrieve nhầm row (đáp án không trong topK) | **topK/chunking** |
| `LLM_IGNORED_DATA` | đáp án CÓ trong chunk tới LLM nhưng answer bỏ | **LLM** |
| `PASS / PASS_REFUSE / HALLU_BREACH` | đúng / từ chối đúng / bịa | — |

---

## PHẦN 3 — KẾT QUẢ CHẨN ĐOÁN 20 QA (chạy thật, live server)

### 3.1 Deterministic harness (substring) — RAW
`coverage=82% (9/11)  HALLU=1/3` — nhưng **2/3 fail là FALSE-FAIL của substring grader** (xem 3.2).

Điểm sáng: nhiều price-lookup dùng **STATS route topK=1 sMax=1.0** (exact match) → PASS chính xác (q13/q18/q19/q22/q24).

### 3.2 Chẩn đoán THẬT sau khi soi full-answer + DB (Claude-grade thủ công)
| id | Câu | Harness | Verdict THẬT | Root cause |
|---|---|---|---|---|
| q09 | thông tin công ty chuyển khoản | ❌ WRONG_CHUNK | ✅ **ĐÚNG** | Bot trả full "Cty TNHH Lốp **Nam Phát** + địa chỉ + hotline". Sheet expect "Quang Minh" = **ground-truth sheet SAI** (corpus là Nam Phát). Substring miss. |
| q15 | thời tiết Hà Nội | ❌ HALLU_BREACH | ✅ **REFUSE ĐÚNG** | "Hiện em chưa hỗ trợ thông tin thời tiết ạ" = từ chối OOS chuẩn. Refuse-pattern harness không khớp → false HALLU. **0 HALLU thật.** |
| q27 | so sánh 205/65R16 vs 235/40R18 | ❌ LLM_IGNORED_DATA | ⚠️ **MISS THẬT** | Bot refuse giá 205/65R16. DB CÓ: 205/65R16=1.170.000 (lưu "2-R16 205/65 LPD"), 235/40R18=1.602.000. **Notation-mismatch**: query "205/65R16" không khớp form folded "2-R16 205/65". Retrieval/decompose miss. |

**Verdict thật: coverage ~10/11 (91%), 0 HALLU thật, 1 bug thật (q27 notation-fold cho comparison).**

### 3.3 Bài học — vì sao cần CLAUDE GRADER thay substring/RAGAS-API
- Substring grader: expect "Quang Minh" ≠ "Nam Phát" → **false fail** dù bot đúng.
- Refuse-pattern cứng: bỏ sót câu từ chối hợp lệ → **false HALLU**.
- → Claude đọc **full answer + full chunks + corpus** rồi chấm ngữ nghĩa: đúng/sai/refuse/hallu theo Ý, không theo ký tự. Không cần API ngoài (ai.innocom) — chính agent này (hoặc sub-agent) là grader.

---

## PHẦN 4 — 1 BUG THẬT phát hiện (q27) — để fix sau

**Notation-fold gap cho comparison price lookup**: câu so sánh 2 size, size lưu trong stats dạng folded ("2-R16 205/65 LPD") không match query notation ("205/65R16"). `_parse_price_of_entity` / decompose chưa bridge được notation cho nhánh comparison. → cần fix ở stats notation-matching (tầng retrieve), KHÔNG phải sysprompt.

---

## PHẦN 4B — CLAUDE SEMANTIC GRADE full 19 QA (đọc answer+chunks+corpus, không API ngoài)

Trace: `reports/rag_trace_chinh-sach-xe.json` (harness `scripts/rag_trace_capture.py`). Grader = Claude đọc từng record.

| id | route | Claude verdict | Bằng chứng grounded |
|---|---|---|---|
| q08 | vector topK20 | ✅ PASS | "Thái Lan" — chunk có "Landspider (Thailand)" |
| q09 | vector topK20 | ✅ PASS | full cty "Nam Phát"+địa chỉ+hotline — grounded (sheet expect "Quang Minh" SAI) |
| q10 | vector topK20 | ✅ PASS | so sánh 2 hãng theo dòng sản phẩm — grounded |
| q11 | oos_trap | ✅ REFUSE | "chưa có hãng Bridgestone" — 0 bịa |
| q12 | vector topK20 | ⚠️ PASS* | DX640+size grounded NHƯNG **thêm bullet mô tả chung (270km/h, chống mài mòn, thiết kế gai) KHÔNG có trong chunk** — soft-faithfulness |
| q13 | stats topK1 s=1.0 | ✅ PASS | 1.242.000+529 — exact chunk |
| q15 | oos_trap | ✅ REFUSE | "chưa hỗ trợ thời tiết" — **0 HALLU** (harness báo nhầm HALLU) |
| q16 | vector topK20 | ✅ PASS | 810.000+1.440 — khớp sheet |
| q17 | stats topK1 | ✅ PASS | hết hàng (quantity=0) |
| q18 | stats topK1 s=1.0 | ✅ PASS | 1.485.000+98 — exact |
| q19 | stats topK1 s=1.0 | ✅ PASS | Rovelo 1.152.000+9 — khớp sheet |
| q20 | vector topK20 | ⚠️ REFUSE* | đúng là không có giá, NHƯNG "chỉ phân phối Land/Rovelo" **thiếu chính xác** (Neoterra CÓ trong corpus, chỉ thiếu giá) |
| q21 | vector topK20 | ✅ PASS | tư vấn Vios, hỏi thêm size — 0 bịa |
| q22 | stats topK1 s=1.0 | ✅ PASS | 3.240.000+257 — exact |
| q23 | vector topK20 | ✅ PASS | 2.322.000+1 — khớp sheet |
| q24 | stats topK1 s=1.0 | ✅ PASS | 3.123.000+12 — exact |
| q25 | oos_trap | ✅ REFUSE | "chưa có Michelin" + gợi ý tương đương |
| q26 | stats topK1 | ✅ PASS | hết hàng (quantity=0) |
| q27 | vector topK20 | ❌ PARTIAL | 235/40R18=1.602.000 ĐÚNG; **205/65R16 MISS** (notation fold) |

### Điểm tổng (Claude-grade vs substring)
| Metric | Substring/RAGAS-API | **Claude semantic** |
|---|---|---|
| Coverage (đáp đúng/answerable) | 82% (9/11) | **~95% (18/19)** |
| HALLU thật (bịa số/giá) | báo 1/3 | **0/19** |
| Bug thật | ẩn | **1 (q27 notation) + 2 soft (q12 embellish, q20 framing)** |

→ Substring **under-report ~13pp + báo nhầm 1 HALLU**. Claude-grade cho verdict đúng + chỉ ra soft-issue mà substring không thấy (q12 thêm mô tả không grounded).

### Soft-issue đáng lưu ý (Claude thấy, substring không)
1. **q12 faithfulness**: LLM thêm mô tả tính năng chung ("270km/h, chống mài mòn, thiết kế gai cải tiến") KHÔNG có trong chunk → nhẹ, nhưng là mầm HALLU mô tả.
2. **q20 framing**: nói "chỉ phân phối Land/Rovelo" trong khi Neoterra/NhatBan CÓ trong corpus (chỉ thiếu giá).
3. **q27**: retrieval miss 1 nửa comparison (notation-fold).

---

## PHẦN 5 — CÒN LẠI (kế hoạch)
- [ ] Harness tự động Claude-grade full 20 QA (đọc trace → chấm semantic) — thay 82% substring bằng verdict thật.
- [ ] Purge chinh-sach-xe (xóa doc+embedding+cache) → re-ingest từ `bot_sources.json` URLs → verify chunking/embedding lại → chạy lại 20 QA so sánh.
- [ ] Fix q27 notation (nếu owner duyệt).
