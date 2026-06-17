# Verify toàn diện + Vị thế vs Expert RAG (evidence-based, 2026-06-15)

> Đối chiếu bản phân tích ngoài (`z_luannt_question.txt`) với CODE + DATA thật.
> Nguyên tắc: CẤM ĐOÁN — mỗi claim phải có evidence kiểm chứng được.

## 0. Trả lời 3 câu meta
- **`z_luannt_question.txt`** = bản hội thoại với AI ngoài (1663 dòng), **KHÔNG phải kết quả test**. Kết quả test thật = `reports/QA_42Q_REPORT_20260615b.md` + 3 JSON.
- **"Đã apply report mới chưa?"** — Report mới (20260615b: 42/42, HALLU=0) ĐÃ có. Bản phân tích ngoài dựa trên **STATE/README CŨ** (trích "4.8/10", "p95 17s") → **KHÔNG phản ánh các fix phiên này** (key restored, 3-key resolve, catalog clean, grounding lane, UI 404 fix).
- Bản phân tích ngoài **đúng tinh thần** (verify-before-claim) nhưng **sai dữ kiện** ở nhiều điểm — xem bảng dưới.

## 1. VERIFY từng claim ngoài (claim → thực tế + evidence)

| Claim của AI ngoài | Thực tế (verified) | Evidence |
|---|---|---|
| "history = 5?" | ❌ SAI — **= 10** | `DEFAULT_MAX_HISTORY=10` + `DEFAULT_GENERATE_HISTORY_MAX_MSGS=10` |
| "U4 chunk flat-text NO-OP" | ❌ SAI — chunk CÓ type | DB: xe 482 table/4 text · spa 106 table/28 text · legal 4 table/76 text |
| "embedding chưa có / dead" | ❌ SAI — **sống 100%** | `0 NULL / 700` chunks |
| "honest grade 4.8/10" | ⚠️ docs CŨ — run mới **42/42, HALLU=0** | `QA_42Q_REPORT_20260615b.md` |
| "p95 17s" | ⚠️ cold+burst; warm single-user **3-5s** | đo phiên này |
| "over-refuse 12%" | ⚠️ lịch sử cũ — run mới **0 over-refuse** | 42/42, refuse chỉ đúng bẫy/out-of-corpus |
| "BM25 giả (ts_rank_cd)" | 🟡 ĐÚNG MỘT PHẦN — dùng **native FTS tsvector** (không phải ParadeDB BM25), RRF-fuse với dense | `pgvector_store.py` `websearch_to_tsquery`+`phraseto_tsquery` · ext `pg_trgm` |
| "RLS dead" | 🟡 ĐÚNG nhưng đã DOC — 24 policies tồn tại, app connect **superuser → bypass**, isolation qua **app-filter record_bot_id** (bắt buộc) | `pg_policies=24` · `rolsuper=t` · đã ghi README §6 |
| "rerank top_n=7 vs aggregation conflict" | ❌ đã xử lý — có **`DEFAULT_RERANK_TOP_N_BY_INTENT`** (per-intent override) | `_16_prompt_token_squeeze_phase_b.py` |
| "factoid SKIP multi_query = bug" | ❌ KHÔNG phải bug — **feature cố ý** (factoid chỉ cần 1 retrieve) | `DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT factoid=False` |
| "multi-turn coref gãy ở câu cụt" | 🟡 GIẢ THUYẾT HỢP LÝ — chưa có test coref. factoid skip rewrite → câu cụt theo ngữ cảnh CÓ THỂ không expand | **cần test multi-turn để verify** (chưa có) |

→ **3 claim tiền đề SAI hoàn toàn** (history/chunk/embed). **3 claim đúng-nhưng-đã-doc** (BM25 native FTS / RLS superuser / câu cụt). Phần còn lại dựa docs cũ.

## 2. VỊ THẾ vs EXPERT RAG — 5 tiêu chí (honest, evidence)

| Tiêu chí | Điểm | Evidence | Gap vs Expert 2026 |
|---|---|---|---|
| **Đúng/Faithful** | 🟢 **9.5/10** | 42/42 grounded · HALLU=0/6 sacred · retrieval phân biệt sạch (in-corpus answer, out-corpus refuse) · 3 tầng chống bịa (grade+cliff+grounding) | gần Expert. Thiếu: numeric-verify step cho câu cộng/so sánh nhiều số |
| **Cost** | 🟢 **9/10** | $0.0064/câu · factoid 4 call · ingest $0.013 · cache 2 tầng | rất tốt. Còn cắt được fan-out aggregation |
| **Performance** | 🟢 **8.5/10** | single-process · embedded workers · gather song song · ingest 57s/9doc | tốt. Concurrency cap 16 OK |
| **UX** | 🟡 **7/10** | booking multi-turn OK · citation · debug panel | thiếu: **streaming** (đợi full answer) · **multi-turn coref test** (chưa có) |
| **Nhanh/Latency** | 🟡 **7/10** | warm 3-5s · cold/burst p95 12-24s | DAG cứng (không speculative) · chưa streaming · aggregation fan-out chậm |

**Tổng: ~8.2/10 — RAG production-grade vững, mạnh nhất ở Faithful+Cost.**

## 3. Khoảng cách THẬT lên Expert (verified, KHÁC bản ngoài)

Bản ngoài đề xuất fix nhiều thứ KHÔNG hỏng (chunking, embedding, history). Gap THẬT:

1. **Latency UX — streaming** (T2): hiện đợi full answer 3-5s mới hiện. Expert RAG stream token. → thêm SSE streaming `/chat`. *Đo: TTFT.*
2. **Multi-turn coref** (UX): câu cụt ("còn cái kia?", "chi tiết hơn") — factoid skip rewrite nên có thể không expand theo history. → cần **bộ test coref 3 bot** (chưa có) + có thể cho rewrite chạy khi có history dù factoid. *Đo: load test multi-turn.*
3. **Numeric-verify cho aggregation** (Faithful): câu "rẻ nhất/đắt nhất/tổng" — LLM tự so sánh nhiều số có thể sai. → adaptive top_n cao hơn cho aggregation (đã có `rerank_top_n_by_intent`) + verify. *Đo: spa q08/q09 với debug chunk count.*
4. **RLS defense-in-depth** (T3): superuser bypass — isolation đang dựa app-filter. → non-superuser DSN. *Không gấp, app-filter đủ an toàn.*

→ **KHÔNG cần**: đổi chunking, đổi embedding, sửa history, đuổi agentic/multimodal. Khung đã expert (Hexagonal/DI/Port) — chỉ "nối dây" 4 điểm trên.

## 4. Lệnh tự verify (anh chạy lại bất kỳ lúc nào)
```bash
# history thật
grep -rn "DEFAULT_MAX_HISTORY\b\|DEFAULT_GENERATE_HISTORY_MAX_MSGS" src/ragbot/shared/constants/
# chunk type
psql "$DB" -c "SELECT b.bot_id,dc.chunk_type,count(*) FROM document_chunks dc JOIN documents d ON dc.record_document_id=d.id JOIN bots b ON d.record_bot_id=b.id GROUP BY 1,2"
# embedding sống
psql "$DB" -c "SELECT count(*) FILTER (WHERE embedding IS NULL), count(*) FROM document_chunks"
# RLS
psql "$DB" -c "SELECT count(*) FROM pg_policies; SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
# rerank/condense
grep -rn "DEFAULT_RERANK_TOP_N\b\|DEFAULT_CONDENSE_HISTORY_LIMIT" src/ragbot/shared/constants/
```

## Kết luận
- **Bản phân tích ngoài: directionally đúng (verify-first) nhưng dữ kiện SAI** ở 3 tiền đề + dựa docs cũ trước các fix phiên này.
- **Vị thế thật: ~8.2/10 — RAG production vững, Faithful+Cost gần Expert, gap ở Latency-streaming + multi-turn-coref.**
- Luồng chi tiết từng step: `docs/FLOW_QUERY_DETAIL.md` + `docs/FLOW_INGEST_DETAIL.md` (verified).
- **Đường lên Expert = 4 điểm "nối dây"** (streaming · coref test · adaptive top_n aggregation · RLS non-superuser), KHÔNG rewrite.
