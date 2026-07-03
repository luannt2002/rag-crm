# QUERY-FLOW BEST-PRACTICE AUDIT — nhận câu → trả lời

> 2 workflow (14 agent Opus, ~1M tokens) audit toàn luồng query + intent taxonomy vs RAG best-practice 2023-2025 (Adaptive-RAG/CRAG/Self-RAG/RAG-Fusion). Mọi claim `file:line`. KHÔNG code — audit + đề xuất (chuẩn mindset CLAUDE.md).

---

## VERDICT TỔNG: khung SOTA-grade, 5/7 stage cần tinh chỉnh

Pipeline có ĐỦ kỹ thuật RAG hiện đại (hybrid dense+sparse, RRF, cross-encoder rerank, CRAG grade, MMR, LITM, multi-query, HyDE, small-to-big) → **kiến trúc thực sự best-practice**. Nhưng 5/7 stage có refinement gaps.

| Stage | Verdict | HIGH | Tổng issue |
|---|---|---|---|
| 1. Input + Cache | ⚠️ PARTIAL | 1 | 5 |
| 2. Understand | ⚠️ PARTIAL | 0 | 5 |
| 3. Query-transform | ✅ ALIGNED | 0 | 4 |
| 4. Retrieve | ✅ ALIGNED | 0 | 4 |
| 5. Rank+Filter | ⚠️ PARTIAL | 1 | 6 |
| 6. Generate | ⚠️ PARTIAL | 1 | 5 |
| 7. Output-guard | ⚠️ PARTIAL | 1 | 4 |

**2 stage ĐÃ chuẩn** (transform, retrieve). **5 stage PARTIAL.** 4 issue HIGH.

---

## 🔴 4 ISSUE HIGH (ưu tiên)

### H1 — Grounding gate NGƯỢC (output-guard) ⭐ HALLU sacred
`guard_output.py`: LLM grounding judge xác nhận answer BỊA (ratio>threshold) → chỉ set `severity=warn/hitl` + flag, **KHÔNG block**. → answer bịa vẫn ship (chứng minh: q20 bịa "1.250.000đ" cho Neoterra không có giá).
**Best-practice:** confirmed-ungrounded → **refuse/block** (dùng `oos_answer_template`), gate đối xứng, config-driven. **Đây là vi phạm HALLU=0 sacred — ưu tiên #1.**

### H2 — Char-cap chạy SAU LITM reorder (generate) — drop nhầm chunk quan trọng nhất
`generate.py:586-595`: char-cap loop cắt từ ĐUÔI list, NHƯNG chạy SAU `reorder_for_lost_in_middle` (đã đẩy chunk relevant vào GIỮA) → **cắt mất chunk relevant nhất**. Bug logic thứ tự.
**Best-practice:** cap/filter theo score-desc TRƯỚC reorder, rồi mới reorder chunk sống sót.

### H3 — Semantic cache KHÔNG có safety floor (input+cache) — HALLU vector
`bot_limits.py`: per-bot `semantic_cache_threshold` clamp chỉ [0,1], operator set = 0.0 → cosine match BẤT KỲ cached row → trả answer SAI câu. Hằng số floor `SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED=0.95` đã định nghĩa nhưng **UNUSED**.
**Best-practice:** enforce floor (max(resolved, 0.90)) + warn khi set dưới floor.

### H4 — Không có "chunk survival trace" (rank+filter) — silent drop
Chunk đáp án có thể bị cắt ở 6 stage độc lập (rerank cap → cliff floor → cliff gap → mmr → grade → max cap) mà **KHÔNG có 1 trace nào nói chunk chết ở đâu**. → khó debug "sao đáp án không tới LLM" (q27-class).
**Best-practice:** 1 trace keyed by chunk_id ghi stage nào drop mỗi candidate.

---

## Chi tiết 7 stage + đề xuất best-practice

### Stage 1 — INPUT + CACHE ⚠️ PARTIAL
- **[H3]** semantic cache no floor (trên).
- **[MED]** Injection chỉ regex → dễ bypass (paraphrase/base64/unicode). → thêm ML-moderation strategy (Llama Guard 3/Lakera) sau regex, config-gated (Port đã sẵn).
- **[MED]** HyDE embed hypothetical → cache cosine key drift run-to-run (non-deterministic). → cache probe dùng embed RAW query, HyDE chỉ cho retrieve.
- **[LOW]** Exact-hash chỉ strip().lower(), thiếu Unicode NFC → miss cache cho VN đa-compose. → normalize NFC + collapse space.
- **[LOW]** Threshold 0.97 chưa calibrate cho embedding model hiện tại. → offline calibration harness.

### Stage 2 — UNDERSTAND ⚠️ PARTIAL
- **[MED]** Taxonomy drift 3 surface (LLM 9 / heuristic 5 / cheap-intent) → single-source + assert subset (khớp taxonomy audit).
- **[MED]** `router_select_model` là **misnomer + dead-for-routing** — chỉ ghi telemetry, không route model thật. → rename + centralize cost-routing.
- **[MED]** Condense (coreference) gated >2 turn AND >=100 char → follow-up ngắn ("còn hàng không?") không condense. → condense khi có BẤT KỲ prior turn.
- **[LOW]** confidence 2 nghĩa lẫn lộn (heuristic tier 0.80/0.90 vs LLM self-report). → tách source-tagged.
- **[LOW]** query_complexity thuần lexical (đếm phẩy/số/?) → fragile. → dùng LLM intent làm complexity signal chính.

### Stage 3 — QUERY-TRANSFORM ✅ ALIGNED
- **[MED]** Comparison/multi-hop decompose fuse TẤT CẢ sub-query vào 1 global RRF pool rồi truncate → nửa entity yếu bị đè (q27!). → **balanced merge**: mỗi sub-query đảm bảo share tối thiểu trong top_k (round-robin interleave). **← fix q27.**
- **[LOW]** HyDE replace thay vì combine → mean(embed(query), embed(hyp)).
- **[LOW]** 2 decompose impl song song → consolidate 1 node.
- **[LOW]** Comparison phụ thuộc LLM label + gate 0.7 → thêm lexical detector OR-condition ("so sánh/vs/khác nhau").

### Stage 4 — RETRIEVE ✅ ALIGNED
- **[MED]** "BM25" **KHÔNG phải BM25 thật** — là Postgres `ts_rank_cd` (không TF-saturation, không IDF). → ParadeDB pg_search/pg_bm25 thật, hoặc chấp nhận + đổi tên "lexical" cho đúng.
- **[MED]** Candidate width nông: mỗi arm `top_k*2`=40, lightweight ít hơn. → tách `retrieve_candidate_pool` (80-150) khỏi final top_k.
- **[LOW]** RRF weight flat 0.5/0.5 chưa tune; adaptive-weight OFF. → A/B per-corpus.
- **[LOW]** 2 RRF impl (SQL weighted + app Cormack) → document + align rank-miss.

### Stage 5 — RANK+FILTER ⚠️ PARTIAL
- **[H4]** No chunk-survival trace (trên).
- **[MED]** MMR chạy TRƯỚC grade → diversity dùng rerank score, drop chunk mà grade sẽ giữ. → MMR SAU grade.
- **[MED]** CRAG grader quá lenient (prompt "ưu tiên giữ", giữ cả ambiguous) → không lọc precision. → neutral prompt, giữ "relevant" mặc định, ablation đo.
- **[MED]** Safety-net stamp MIN score + append cuối → chunk retrieval-mạnh bị chôn. → stamp theo original rank + re-sort.
- **[MED]** MMR cosine fallback về trigram Jaccard cho CẢ batch nếu 1 chunk thiếu embedding. → backfill embedding hoặc exclude chunk thiếu.
- **[LOW]** floor 0.05 / min_score 0.30 chưa recalibrate cho ZeroEntropy hiện tại. → histogram calibrate.

### Stage 6 — GENERATE ⚠️ PARTIAL
- **[H2]** Char-cap sau LITM reorder (trên).
- **[MED]** context_chars_cap=2900 char (không phải token) quá chặt cho model 100K+ context. → token-budget theo context window model.
- **[MED]** Chitchat hard-drop toàn bộ `<documents>` chỉ dựa intent → factoid misclassify thành chitchat sẽ mất context. → không drop context khi có graded chunks.
- **[LOW]** sysprompt_default_rules append (ADR-W1-S10 governed exception) → chạy ablation Phase-5 đo lift.
- **[LOW]** LITM no-op khi len<=2 → fix H2 làm LITM có nghĩa lại.

### Stage 7 — OUTPUT-GUARD ⚠️ PARTIAL
- **[H1]** Grounding gate NGƯỢC (trên) ⭐.
- **[MED]** Threshold 0.3 chưa calibrate + chỉ check 5 câu đầu. → calibrate labelled set.
- **[MED]** Citation = attribution (posthoc top chunk) KHÔNG phải entailment → marker/substring short-circuit judge. → tách id-validation vs entailment.
- **[LOW]** System-leak shingle exact-hash brittle (paraphrase defeats). → thêm semantic similarity layer.

---

## ĐỀ XUẤT THỨ TỰ (chuẩn mindset CLAUDE.md — evidence → duyệt → code → load-test)

| Ưu tiên | Fix | Vì sao | Rủi ro |
|---|---|---|---|
| **1** ⭐ | H1 grounding gate → block confirmed-bịa | HALLU=0 sacred (q20) | cần load-test HALLU + owner-chốt sacred#10 |
| **2** | H2 char-cap TRƯỚC reorder | drop nhầm chunk relevant (correctness) | thấp — sửa thứ tự, có test |
| **3** | H3 semantic cache floor | HALLU vector | thấp — enforce floor |
| **4** | H4 chunk-survival trace | observability debug | thấp — thêm log |
| **5** | Stage-3 balanced merge decompose | fix q27 comparison | trung — đổi merge |
| 6 | Taxonomy de-drift (xóa dead QUERY_INTENT_*) | de-drift | thấp (dead code) |
| 7 | Còn lại (BM25 thật, calibrate, ML-moderation...) | refinement | trung-cao, cần đo |

**Nguyên tắc:** #2,#3,#4,#6 = an toàn/thấp-rủi-ro, làm được sớm + test. #1,#5 + phần calibrate = cần load-test (rule#0) + owner duyệt (sacred#10).
