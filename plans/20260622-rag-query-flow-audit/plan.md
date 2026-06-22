# [T1-Smartness] RAG QUERY-flow audit — scope + deep-debug 8 step (song song 7 step upload)

> Tier: **T1-Smartness** (luồng trả lời câu hỏi đúng/expert). Ngày: 2026-06-22.
> Branch: `expert-rag-squash-conflate-logcenter-20260619`.
> Mindset (CLAUDE.md): rule #0 no-guess · /plan trước code · surgical · EVOLVE-not-rewrite.

---

## 0. Bối cảnh — tại sao cần plan này

- **Luồng UPLOAD (L1→L7) đã verify PASS** với happy-case data: 12 docs/1518 chunks, embeddings
  thật, `verify_happy_case_pipeline.py` ALL GREEN. → **data vào DB đã chuẩn scope/styling**.
- **NHƯNG luồng QUERY (câu hỏi → trả lời) CHƯA debug step-by-step** như upload. Load test 22/23
  (96%) cho thấy *mostly* work, nhưng multi-agent audit lộ **6 bug confirmed** ở luồng query
  (generate/retrieval/L7-narrate) — đặc biệt `generate.py` còn parse giá kiểu **CSV cũ**, chưa
  khớp happy-case **markdown table**.
- → Cần **debug luồng query CHẶT như upload**: định nghĩa step, verify từng step có evidence.

**Câu hỏi user**: "upload đã pass, còn query thì sao?" → plan này trả lời, step-by-step.

---

## 1. SCOPE — 8 STEP luồng QUERY (định nghĩa rõ, để debug từng cái)

| Step | Tên | Code | Input → Output |
|---|---|---|---|
| **Q1** | Understand query | `orchestration/nodes/understand.py` (intent + condense + rewrite) | câu hỏi thô → intent + query rewrite |
| **Q2** | Embed query | embedder | query text → vector |
| **Q3** | Retrieve (hybrid) | retrieval nodes (vector pgvector + BM25 lexical) | vector+text → top-N chunks |
| **Q4** | Rerank | reranker (cross-encoder) | N chunks → reranked |
| **Q5** | Grade / filter | grading node (score threshold) | reranked → kept chunks |
| **Q6** | Assemble context | parent-expand + sysprompt + chunk format | chunks → `<documents>` block |
| **Q7** | Generate answer | `orchestration/nodes/generate.py` (LLM) | context+sysprompt → answer |
| **Q8** | Post-process | cache (2-tier) + citations + guardrails + answer_type | answer → final |

→ **8 step query** (vs **7 step upload**). Mỗi step PHẢI verify với happy-case data (markdown table).

---

## 2. VẤN ĐỀ đã tìm (multi-agent audit, adversarial-verified) — map vào step

| Bug | Step | Sev | File:line | Vấn đề | Ảnh hưởng happy-case |
|---|---|---|---|---|---|
| **#6** | **Q7** | BLOCKER-thực | `generate.py:218-230` | extract giá bằng `line.split(",")` (CSV) | 🔴 happy-case là `\| table \|` markdown, KHÔNG phải CSV → fast-path giá **fail**, rớt về LLM |
| **#5** | **Q7** | WARN | `generate.py:227-229` | field `price_buoi_le`/`price_goc` + comment "Chăm sóc da chuyên sâu" hardcode | 🔴 **domain literal spa** trong answer-node (vi phạm domain-neutral) |
| **#3** | Q6 | BLOCKER | `ingest_stages_store.py:659` | parent chunks thiếu narrate metadata | 🟡 Q6 parent-expand lấy content parent **chưa narrate** → context kém |
| **#1** | (upload L2) | BLOCKER | `blocks.py:196` | regex header match mọi prose có `\|` | 🟡 ảnh hưởng upload, không trực tiếp query |
| retrieval | Q3/Q4 | 10×MINOR | retrieval nodes | (xe tire-size cross-match: 165/80R13 miss) | 🟡 BM25 AND + notation matching |
| cache | Q8 | — | semantic_cache | stale-cache trả câu-né cũ (TTL 1h) | 🟡 đã biết, transient |

**Điểm nóng nhất: Q7 `generate.py`** — bug #5+#6 trực tiếp làm answer flow chưa khớp happy-case markdown.

---

## 3. DEEP-DEBUG PROTOCOL mỗi step (như `verify_happy_case_pipeline.py` cho upload)

Dựng `scripts/verify_query_flow.py` — chạy 1 câu hỏi qua 8 step, assert từng step:

- **Q1**: intent đúng (factoid/list/aggregate)? query rewrite không mất nghĩa?
- **Q2**: query embed thành vector (dim đúng)?
- **Q3**: retrieve trả chunks > 0? chunk đúng doc? (debug: `top_k`, `score_max`)
- **Q4**: rerank đổi thứ tự hợp lý? top chunk relevant?
- **Q5**: grade giữ chunk score ≥ threshold? không drop chunk đúng?
- **Q6**: context có chunk markdown table đúng? parent-expand narrate? sysprompt lắp đúng?
- **Q7**: answer grounded (số từ chunk, không bịa)? KHÔNG dùng CSV-extract (#6)?
- **Q8**: cache không trả stale? citation đúng chunk? guardrail không over-block?

Mỗi step GREEN = evidence số thật (chunk count, score, answer trace). Đỏ → fix đúng tầng.

---

## 4. FIX prioritized (ship từng cái, đúng tầng)

### P1 — Q7 generate.py (nóng nhất, ảnh hưởng answer happy-case)
- **#6**: bỏ CSV-extract `line.split(",")`; happy-case là markdown table → hoặc (a) dùng
  stats-index `ParsedEntity` (đã có name/price/category từ ingest) thay vì re-parse chunk, hoặc
  (b) parse markdown-table-aware (`\| split`). **Ưu tiên (a)** — dùng nguồn đã chuẩn.
- **#5**: gỡ field `price_buoi_le`/`price_goc` hardcode + comment spa → generic (price_primary/
  price_secondary đã có trong ParsedEntity).
- ⚠️ Q7 nhạy cảm (đụng mọi answer) → TDD: test trước, verify load-test 22/23 không tụt.

### P2 — Q6 parent-narrate (#3)
- parent chunks cần narrate metadata trước embed (hoặc Q6 expand dùng narrate đã có).

### P3 — upload L2 (#1 blocks regex) — riêng luồng upload
- regex `^[A-Z...]+\|` siết lại (chỉ match khi >50% cell label-like, không match prose-có-pipe).

### P4 — Q3/Q4 retrieval (xe tire-size) + Q8 cache
- class riêng (BM25 AND notation) — defer, ADR nếu làm.

---

## 5. Verify sau fix
1. `scripts/verify_query_flow.py` — 8 step GREEN trên 5 câu/bot.
2. **Load test lại 23 câu** — phải ≥ 22/23 (không regression) + xe tire-size cải thiện.
3. Full pytest no-regression. Grep domain-neutral generate.py = 0 literal.

---

## 6. Compliance (CLAUDE.md)
- ✅ Sacred #10: KHÔNG app-inject/override — chỉ sửa cách EXTRACT giá (dùng stats-index sẵn).
- ✅ Domain-neutral: gỡ `price_buoi_le`/`price_goc` + comment spa.
- ✅ HALLU=0: answer vẫn grounded từ chunk/stats, không bịa.
- ✅ T1 declared · surgical · TDD cho Q7 (nhạy cảm).

---

## 7. Trả lời câu user
- **"Upload pass, query thì sao?"** → Query **mostly pass (96%)** nhưng có **6 bug** (nóng nhất Q7
  generate.py còn CSV-extract + domain literal, chưa khớp happy-case markdown).
- **"Bao nhiêu step debug?"** → **8 step query** (Q1→Q8), nhiều hơn 7 step upload 1 step (thêm
  Q8 post-process/cache).
- Sau fix P1-P3 + verify 8 step → query flow mới expert đồng bộ với upload.
