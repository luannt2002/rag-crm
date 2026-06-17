# KỊCH BẢN TEST RAGBOT v1 — 1 Room, Stage-by-Stage Metrics

> Author: QA architect
> Date: 2026-04-28
> Version: v1 (initial — Sprint 12A baseline)
> Scope: Đo metrics REAL của 1 bot trên 1 room duy nhất, không tự phong điểm, không hardcode brand.
> Phụ thuộc: `pipeline_audit_logger` (Sprint 13 P1) để có per-stage timing & top_score per turn.

---

## Section 1 — Mục tiêu test

### 1.1. Why (vì sao cần kịch bản này)

Trước nay, các báo cáo nội bộ thường có dạng "bot đạt 8.5/10", "chất lượng tốt", "ổn rồi".
Đó là **điểm tự phong**. Leader không có cơ sở để verify, không biết bot YẾU Ở GIAI ĐOẠN NÀO.

Kịch bản này thay điểm tự phong bằng **thông số load test real**, ép từng câu trả lời đi qua
pipeline 6 stage rồi báo cáo verdict per stage. Khi bot fail, leader đọc report là biết ngay
"chunking lỗi" hay "retrieval lỗi" hay "generation lỗi" — không phải đoán mò.

### 1.2. What (đo gì)

- Stage-by-stage metrics: ingest → chunk → embed → retrieve → rerank → generate.
- Per-turn metrics: top_score, faithfulness, latency, cost.
- Aggregate: answered rate (on-topic) vs refuse rate (off-topic) vs hallucinated count.
- Output: markdown verdict + raw JSONL (append-only across versions).

### 1.3. How (kỹ thuật đo)

- Reset DB sạch DOWN data (documents, chunks, chat_histories, conversations, semantic_cache).
- Ingest lại 4 docs từ đầu → đo ingest metrics REAL từ DB (không tin response API).
- Chạy ~43 câu hỏi qua 1 room duy nhất (giảm token spend, giữ context focus).
- Phân loại 5 categories (PRICE / SERVICE / INFO / OFF-CORPUS / NOISE) theo tỉ lệ 80/20.
- Verdict per turn: PASS / WARN / FAIL theo rule cụ thể (Section 4.3).
- Format report leader-friendly: "Stage X ✅/❌ vì lý do Y, dẫn chứng Z".

### 1.4. Success criteria

Test này **không có "điểm tổng"**. Leader đọc 7 hàng metric trong Section 5 + 1 bảng vấn đề
trong Section 7. Mỗi hàng có target rõ ràng, verdict ✅ hoặc ❌. Không có thang 8.5/10.

---

## Section 2 — Pre-condition

### 2.1. Bot identity (3-key REQUIRED, env-driven)

Theo CLAUDE.md "IDENTITY RULE — TUYỆT ĐỐI 3-KEY REQUIRED":

```bash
# .env (KHÔNG commit, không hardcode trong code/docs)
RAGBOT_TEST_TENANT_ID=<int-from-upstream>
RAGBOT_TEST_BOT_ID=<bot-slug>
RAGBOT_TEST_CHANNEL_TYPE=web
RAGBOT_BASE_URL=http://localhost:8000
RAGBOT_TEST_API_TOKEN=<jwt-bearer>
```

`run_kich_ban_test.py` đọc 3 keys qua `os.getenv()`. Nếu thiếu → fail fast với exit code 1.

### 2.2. Database access

User chạy script phải có quyền:

- `DELETE` trên các bảng: `document_chunks`, `documents`, `chat_histories`, `conversations`,
  `semantic_cache`, `audit_log` (filter theo `record_bot_id`).
- `SELECT` trên `bots`, `document_chunks`, `documents` để verify metrics.

DSN đọc qua `DATABASE_URL_SYNC` (psycopg2) hoặc `DATABASE_URL` (asyncpg). KHÔNG hardcode.

### 2.3. Source docs ready

4 docs (Google Sheets export hoặc paste content) — mỗi sheet đại diện 1 nhóm thông tin:

| Doc # | Loại | Nội dung mong đợi |
|-------|------|------------------|
| 1     | pricing  | Bảng giá đầy đủ (X dòng × giá VND) |
| 2     | products | Danh sách sản phẩm + mô tả ngắn   |
| 3     | services | Danh sách dịch vụ + quy trình     |
| 4     | packages | Combo gói + thành phần + giá tổng |

**Note**: Tên cụ thể của sản phẩm/dịch vụ/gói = data của tenant, KHÔNG xuất hiện trong file
này. Khi chạy test thực, dùng `init_questions_from_corpus.py` (Sprint 13) để tự rút entities
từ docs và replace placeholder `<product_name_1>` / `<service_name_2>` / `<package_name_1>`.

### 2.4. Pipeline audit logger ENABLED

```bash
RAGBOT_PIPELINE_AUDIT_ENABLED=true   # Sprint 13 P1 — JSONL per-turn audit
RAGBOT_PIPELINE_AUDIT_PATH=reports/audit_jsonl/
```

Khi flag = `true`, mỗi turn sẽ ghi 1 dòng JSONL với fields:

```
trace_id, turn_id, stage_timings: {condense_ms, intent_ms, retrieve_ms, rerank_ms,
generate_ms}, retrieved_chunks: [{id, score, content_hint}], rerank_scores: [...],
faithfulness_score, refuse_triggered, total_latency_ms, total_tokens, cost_usd
```

Run script đọc JSONL này để build Section 6 stage breakdown.

**TBD nếu chưa ship**: nếu Sprint 13 chưa landed `pipeline_audit_logger`, các metric stage-level
sẽ ghi `"TBD sau pipeline_audit_logger ship"` thay vì giả số.

---

## Section 3 — Phase 1: Ingest test (Stage 1 of 2)

### 3.1. Reset DB state (xóa DOWN data)

**SQL script** chạy trước mỗi test run:

```sql
BEGIN;

-- xóa chunks trước (FK)
DELETE FROM document_chunks
 WHERE record_document_id IN (
   SELECT id FROM documents WHERE record_bot_id = :record_bot_id
 );

-- xóa docs
DELETE FROM documents WHERE record_bot_id = :record_bot_id;

-- xóa lịch sử chat
DELETE FROM chat_histories WHERE record_bot_id = :record_bot_id;
DELETE FROM conversations  WHERE record_bot_id = :record_bot_id;

-- xóa cache
DELETE FROM semantic_cache WHERE record_bot_id = :record_bot_id;

-- xóa audit log của bot này (nếu cần re-test sạch)
DELETE FROM audit_log WHERE record_bot_id = :record_bot_id;

COMMIT;
```

**KHÔNG** `DROP` bảng `bots`. Bot row được giữ nguyên — chỉ xóa data thuộc về nó.

### 3.2. Ingest 4 docs

```bash
# script gọi POST /api/ragbot/sync/documents — payload theo SyncDocumentsRequest
curl -X POST http://localhost:8000/api/ragbot/sync/documents \
  -H "Authorization: Bearer $RAGBOT_TEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @docs_payload.json
```

`docs_payload.json` format:

```json
{
  "tenant_id": 12345,
  "bot_id": "test-bot-v1",
  "channel_type": "web",
  "documents": [
    {"title": "pricing.csv",  "content": "...", "source_type": "sync"},
    {"title": "products.csv", "content": "...", "source_type": "sync"},
    {"title": "services.csv", "content": "...", "source_type": "sync"},
    {"title": "packages.csv", "content": "...", "source_type": "sync"}
  ]
}
```

Response expected: `{"ok": true, "total_documents": 4, "total_chunks": <N>, ...}`.

### 3.3. Verify ingest metrics (đọc DB, KHÔNG tin response)

| # | Metric | Query | Target | Verdict |
|---|--------|-------|--------|---------|
| 1 | Số docs DB | `SELECT count(*) FROM documents WHERE record_bot_id = :bid` | =4 | ✅ / ❌ |
| 2 | Tổng chunks | `SELECT count(*) FROM document_chunks c JOIN documents d ON d.id = c.record_document_id WHERE d.record_bot_id = :bid` | 20-50 | ✅ / ❌ |
| 3 | Avg chunk length (chars) | `SELECT avg(length(content)) FROM document_chunks c JOIN documents d ON ... WHERE d.record_bot_id = :bid` | 800-1500 | ✅ / ❌ |
| 4 | Min chunk length | `SELECT min(length(content)) FROM ...` | ≥ 100 | ✅ / ❌ |
| 5 | Max chunk length | `SELECT max(length(content)) FROM ...` | ≤ 3000 | ✅ / ❌ |
| 6 | Embedding NULL count | `SELECT count(*) FROM document_chunks c JOIN ... WHERE d.record_bot_id = :bid AND c.embedding IS NULL` | 0 | ✅ / ❌ |
| 7 | Embedding dimension | `SELECT array_length(embedding::float[], 1) FROM document_chunks LIMIT 1` | =1536 | ✅ / ❌ |
| 8 | Duplicate raw_content | `SELECT content, count(*) FROM document_chunks c JOIN ... WHERE d.record_bot_id = :bid GROUP BY content HAVING count(*) > 1` | =0 row | ✅ / ❌ |
| 9 | Ingest duration / doc | `pipeline_audit JSONL.ingest_ms` | < 30000 ms | ✅ / ❌ |

### 3.4. Sample chunks audit (manual, 5 random)

Lấy 5 chunks random:

```sql
SELECT c.content, length(c.content) AS len, d.title
FROM document_chunks c
JOIN documents d ON d.id = c.record_document_id
WHERE d.record_bot_id = :bid
ORDER BY random()
LIMIT 5;
```

Câu hỏi audit cho mỗi chunk:

1. Chunk có atomic semantic không? (1 row giá đứng riêng / 1 paragraph procedure đứng riêng)
2. Có split giữa con số tiền không? (vd: chunk 1 kết thúc bằng "999.000", chunk 2 bắt đầu "VNĐ" → fail)
3. Có split giữa câu không? (vd: chunk 1 kết thúc bằng "khách hàng có thể", chunk 2 "đặt online" → fail)
4. Có chứa nhiễu/header lặp không? (vd: "Trang 1/12 — Sheet pricing — ..." lặp ở mỗi chunk)
5. Có giữ heading/context section không? (vd: chunk về giá có ghi "Bảng giá dịch vụ X" ở đầu)

### 3.5. PASS/FAIL gate Stage 1 (ingest)

- ✅ **ĐẠT** nếu **toàn bộ** dòng 1-9 ở 3.3 pass + 5/5 sample chunks ở 3.4 OK.
- ❌ **FAIL** nếu **bất kỳ** điều kiện sau:
  - chunks_total < 10 → chunking lỗi nặng (chia đoạn quá to hoặc không chia)
  - embed_null_count > 0 → embedding service fail/timeout
  - duplicate_raw_count > 0 → ingest pipeline đang re-process duplicate
  - ≥3/5 sample chunks split giữa số/câu → cần fix chunker (xem `plans/260424-P34-zero-hardcode-sweep-chunking/`)

**Khi fail Stage 1, KHÔNG chạy tiếp Stage 2.** Fix ingest trước rồi reset + ingest lại.

---

## Section 4 — Phase 2: Query test (Stage 2 of 2)

### 4.1. Question bank — 1 room, ~43 câu, 5 categories (80/20)

File: `golden_set/kich_ban_questions_v1.json` (đã ship cùng kịch bản này).

| Category | Tag | Số câu | Tỉ lệ | Expected type | Lý do test |
|----------|-----|--------|-------|---------------|------------|
| PRICE      | `[PRICE]`      | 18 | 41.9% | answered | Hỏi nhiều nhất khi end-user mua hàng — bot phải trả lời chính xác giá từ corpus |
| SERVICE    | `[SERVICE]`    | 9  | 20.9% | answered | Quy trình + thời gian + đối tượng dịch vụ — kiểm tra chunk dài có trả đúng không |
| INFO       | `[INFO]`       | 6  | 14.0% | answered | Địa chỉ/giờ/hotline — meta info, kiểm tra retrieval cross-doc |
| OFF_CORPUS | `[OFF-CORPUS]` | 5  | 11.6% | refused  | Hỏi sản phẩm/dịch vụ KHÔNG có trong 4 sheets — bot phải refuse, không bịa |
| NOISE      | `[NOISE]`      | 5  | 11.6% | refused  | Câu vu vơ ngoài domain — bot phải redirect lịch sự, không trả general knowledge |

**Tổng**: 43 câu, on-topic 76.7% / off-topic 23.3% (xấp xỉ 80/20 user yêu cầu).

### 4.2. Per-turn record format (CSV/JSON)

```
turn_id, category_tag, question, expected_answer_type, expected_keywords,
actual_answer, actual_chunks_n, actual_top_score, actual_answer_type,
faithfulness, latency_ms, cost_usd, verdict, fail_reason
```

Mỗi field:

- `turn_id` — int, tăng dần 1..43
- `category_tag` — `[PRICE]` / `[SERVICE]` / `[INFO]` / `[OFF-CORPUS]` / `[NOISE]`
- `question` — text gốc (đã replace placeholder bằng entity từ corpus)
- `expected_answer_type` — `answered` | `refused`
- `expected_keywords` — list từ khóa MUST có trong answer khi expected_type = answered (vd `["<product_name_1>", "giá"]`)
- `actual_answer` — text bot trả về (truncate 500 chars trong report, full trong raw JSONL)
- `actual_chunks_n` — số chunks bot retrieved (sau rerank/grade)
- `actual_top_score` — float, top score sau rerank (hoặc cosine nếu reranker OFF)
- `actual_answer_type` — classify từ answer: matches refuse pattern → `refused`, else `answered`
- `faithfulness` — score 0-1 từ deepeval/ragas, optional (chạy offline sau test)
- `latency_ms` — float, t0 → response received
- `cost_usd` — float, từ usage tokens × pricing system_config
- `verdict` — `PASS` | `WARN` | `FAIL`
- `fail_reason` — text ngắn nếu verdict ≠ PASS

### 4.3. Per-turn verdict logic

```
if expected_type != actual_type:
    verdict = FAIL  # bot bịa khi cần refuse, hoặc refuse khi cần trả lời
elif expected_type == "answered" and faithfulness is not None and faithfulness < 0.5:
    verdict = FAIL  # answer "có vẻ on-topic" nhưng bịa số/sai data
elif expected_type == "answered" and faithfulness is not None and faithfulness < 0.85:
    verdict = WARN  # answer borderline, leader xem lại
elif expected_type == "answered" and not all(kw in actual_answer for kw in expected_keywords):
    verdict = WARN  # thiếu keyword chính (vd hỏi giá X mà answer không nhắc X)
else:
    verdict = PASS
```

**Refuse pattern detection** (regex hoặc set patterns, đọc từ `system_config`):

```
"xin lỗi", "không có thông tin", "không tìm thấy",
"không thể trả lời", "vui lòng liên hệ", "tôi chỉ có thể trả lời về"
```

### 4.4. Run command

```bash
# Reset state + ingest + run + report
python scripts/run_kich_ban_test.py \
  --questions golden_set/kich_ban_questions_v1.json \
  --docs      golden_set/docs_4sheets.json \
  --output-dir reports/kich_ban_$(date +%Y%m%d_%H%M)
```

Identity (3-key) đọc từ ENV trong `.env` — script `_load_identity()` validate trước khi chạy.

**Time budget**: 1 room × 43 câu × ~3s/câu ≈ 130s + ingest ~30s → tổng < 4 phút.

---

## Section 5 — Aggregated metrics 1 room (output cho leader)

Bảng output cuối cùng — mỗi hàng có target + verdict, KHÔNG có "điểm tổng":

| # | Metric | Cách tính | Value | Target | Verdict |
|---|--------|-----------|-------|--------|---------|
| 1 | Answered rate (on-topic) | `count(actual=answered) / count(category∈{PRICE,SERVICE,INFO})` | TBD | ≥ 95% | ✅ / ❌ |
| 2 | Refuse rate correct (off-topic) | `count(actual=refused) / count(category∈{OFF_CORPUS,NOISE})` | TBD | ≥ 80% | ✅ / ❌ |
| 3 | Faithfulness avg | `mean(faithfulness over answered turns)` | TBD | ≥ 0.90 | ✅ / ❌ |
| 4 | Hallucinated count | `count(category∈{OFF_CORPUS,NOISE} AND actual=answered)` | TBD | = 0 | ✅ / ❌ |
| 5 | Top score avg (on-topic) | `mean(actual_top_score over PRICE+SERVICE+INFO)` | TBD | ≥ 0.30 cosine OR ≥ 0.40 rerank | ✅ / ❌ |
| 6 | Avg latency | `mean(latency_ms)` | TBD | < 5000 ms | ✅ / ❌ |
| 7 | Avg cost / turn | `mean(cost_usd)` | TBD | < $0.005 | ✅ / ❌ |

**Lưu ý leader**: 7 hàng = 7 dimensions độc lập. Nếu hàng 1+2 ✅ nhưng hàng 4 = 3 → bot
"refuse đúng quá nửa, nhưng vẫn còn 3 lần bịa trên off-topic" — vẫn FAIL.

---

## Section 6 — Stage-by-stage breakdown (leader-readable)

Sau khi run xong, parse `reports/audit_jsonl/*.jsonl` → render block sau:

### 6.1. Stage 1 — INGEST (1 lần, đo per-doc)

```
Stage 1 INGEST: ✅ ĐẠT
  - 4/4 docs ingest thành công
  - 24 chunks total (target 20-50)  ✅
  - Avg chunk length: 1102 chars (target 800-1500)  ✅
  - Embedding NULL: 0 / 24  ✅
  - Embedding dim: 1536  ✅
  - Duplicate raw: 0  ✅
  - Ingest duration: avg 4.2s/doc (target <30s)  ✅
  - Sample chunks audit: 5/5 atomic semantic OK
```

Hoặc nếu fail:

```
Stage 1 INGEST: ❌ FAIL
  - 4/4 docs ingest, BUT
  - 8 chunks total (target 20-50)  ❌  → chunker cấu hình quá to hoặc CSV row gộp
  - Avg chunk length: 3120 chars (vượt max 3000)  ❌
  - Sample audit: 4/5 chunks split giữa giá tiền  ❌
  → ROOT CAUSE: chunker đang dùng default 4000-char window cho CSV. Cần switch
    sang HYBRID mode hoặc CSV-aware splitter (xem plans/260424-P34-zero-hardcode-sweep-chunking).
  → ACTION: STOP, fix chunker, reset, ingest lại. KHÔNG chạy Stage 2.
```

### 6.2. Stage 2 — QUERY pipeline (avg per turn over 43 turns)

```
Stage 2 QUERY pipeline: ⚠️ MIXED
  - condense (history → query):       ✅ 100% success, avg 180ms
  - intent extract:                   ✅ 95% success (5% fallback OOS detection)
  - rewrite (multi-query expansion):  ✅ 100% success, avg 320ms
  - retrieve (pgvector cosine):       ⚠️ avg top_score = 0.31 (target ≥ 0.30, sát ngưỡng)
  - rerank (Cohere/local):            ❌ DISABLED (COHERE_API_KEY empty trong .env)
                                          → fallback RRF, top_score thấp giả
                                          → xem reference_reranker_disabled MEMORY
  - grade (LLM-as-judge):             ⚠️ avg 2.1/20 chunks marked relevant
  - generate (LLM):                   ✅ refuse template fires 8/10 đúng
                                          ❌ 2/10 bịa trên OFF_CORPUS (turn 35, 37)
```

### 6.3. Stage 3 — OUTPUT quality

```
Stage 3 OUTPUT quality:
  - Faithfulness avg over answered:   0.87 (target 0.90)  ⚠️
  - Refuse appropriate (off-topic):   8/10 = 80%  ✅
  - Hallucinated (off-topic answered): 2  ❌  (turn_id 35, 37 — bot bịa product không có)
  - Keyword match (PRICE category):   16/18 = 88.9%  ⚠️
```

---

## Section 7 — Vấn đề cụ thể + giải pháp (leader template)

Format leader-friendly (1 hàng = 1 vấn đề, có dẫn chứng + effort estimate):

| # | Vấn đề | Vì sao | Dẫn chứng | Giải pháp | Effort |
|---|--------|--------|-----------|-----------|--------|
| 1 | Reranker OFF, top_score giả | `COHERE_API_KEY` empty trong .env, fallback RRF | `audit_jsonl line.rerank_model="rrf"` 43/43 turns | Set COHERE_API_KEY hoặc activate ViRanker local (Sprint 8 done) | S — env config |
| 2 | Bịa 2/5 OFF_CORPUS | Refuse template miss khi LLM tự confident | turn 35: hỏi `<unknown_product_1>` → bot trả "có ạ, giá X" | Tăng grade threshold + thêm refuse pattern "không có sản phẩm/dịch vụ này" | M — system_config + prompt |
| 3 | Avg chunk 1102 chars borderline | Chunker default 1024, đôi khi merge | `length(content)` distribution skewed right tail | Switch HYBRID chunker, target avg 900-1100 | M — config + ingest re-run |
| 4 | Faithfulness 0.87 < target 0.90 | LLM thêm filler "thường thì", "có thể" | turn 8, 12: số tiền đúng nhưng câu modal | Tighten prompt: "Trả lời chỉ dùng số có trong context" | S — prompt |
| 5 | Latency 5400ms > 5000ms | rerank 1200ms + generate 3500ms | audit_jsonl stage_timings | Cache exact-hash + warmup pgvector index | M — Phase 4 plan |

(Số liệu trên là **placeholder template**. Run thực sẽ điền giá trị thật.)

---

## Section 8 — Run script template

File: `/var/www/html/ragbot/scripts/run_kich_ban_test.py` (skeleton ~250 lines).

Skeleton bao gồm:

```python
async def main_async(args):
    tenant_id, bot_id, channel_type, base_url, api_token = _load_identity()  # ENV
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # PHASE 1: reset DB state (DELETE DOWN data)
    await reset_bot_state(record_bot_id)

    # PHASE 2: ingest 4 docs + verify metrics REAL từ DB
    await ingest_docs(base_url, api_token, tenant_id, bot_id, channel_type, docs)
    ingest_metrics = await verify_ingest_metrics(record_bot_id)
    if not ingest_metrics.verdict_pass:
        return 1   # fail fast, không chạy Stage 2

    # PHASE 3: run 43 questions, 1 room
    questions = json.loads(Path(args.questions).read_text())["questions"]
    turns = []
    for q in questions:
        tr = await run_one_turn(...)
        turns.append(tr)

    # PHASE 4: aggregate + render report
    summary = aggregate(turns)
    report_path = write_report_md(out_dir, ingest_metrics, turns, summary)
```

**Status hiện tại**: SKELETON — phase markers + pseudocode rõ ràng từng bước.
Sprint 13 sẽ implement đầy đủ (phụ thuộc `pipeline_audit_logger`).

---

## Section 9 — Append-only logs (cross-version compare)

Mỗi version test ghi vào:

```
reports/
  kich_ban_test_v1_20260428_1430.md      ← run đầu, baseline
  kich_ban_test_v1_20260428_1830.md      ← sau khi bật Cohere reranker
  kich_ban_test_v1_20260429_0900.md      ← sau khi fix chunker HYBRID
  kich_ban_test_v2_20260505_1000.md      ← question bank v2 (mở rộng 60 câu)
  audit_jsonl/
    20260428_1430_trace_*.jsonl          ← raw per-turn audit
    20260428_1830_trace_*.jsonl
```

**Quy tắc append-only**: KHÔNG sửa file cũ. Mỗi run = file mới, timestamp trong tên.
So sánh version cũ/mới = `diff reports/kich_ban_test_v1_20260428_1430.md reports/kich_ban_test_v1_20260428_1830.md`
hoặc dùng `scripts/eval_diff.py` để tự render bảng so sánh.

**Cross-version metrics tracking** (Sprint 13+):

```
reports/kich_ban_history.csv   ← append-only, 1 row / run
columns: run_id, timestamp, ingest_pass, chunks_total, answered_rate,
         refuse_rate_correct, faithfulness, hallucinated, top_score_avg,
         latency_avg_ms, cost_avg_usd, fail_reasons_summary
```

---

## Section 10 — Constraints + ràng buộc kịch bản

### 10.1. Domain-neutral (CLAUDE.md TUYỆT ĐỐI)

- KHÔNG hardcode brand, tên cụ thể trong file này.
- Placeholder: `<product_name_1>`, `<service_name_2>`, `<package_name_1>`,
  `<unknown_product_1>` — replace lúc runtime từ corpus.
- 3-key identity tenant_id/bot_id/channel_type chỉ đọc qua `os.getenv()`.

### 10.2. Honest reporting

- Metric chưa đo được → ghi `TBD sau khi pipeline_audit_logger ship`, **KHÔNG bịa số**.
- Verdict ✅ / ❌ phải có dẫn chứng (column/line/SQL query).
- Stage failed → STOP, không chạy stage sau (vd Stage 1 fail → không chạy Stage 2).

### 10.3. Dependency Sprint 13

| Dependency | Status | Block |
|------------|--------|-------|
| `pipeline_audit_logger` (per-stage timings + scores JSONL) | TBD Sprint 13 P1 | Section 6 stage breakdown |
| `init_questions_from_corpus.py` (auto-fill placeholder từ docs) | TBD Sprint 13 P2 | Section 4.1 entity replacement |
| `eval_diff.py` (cross-version compare table) | TBD Sprint 13 P3 | Section 9 history tracking |
| Faithfulness scorer (deepeval/ragas integration) | Partial — `scripts/deepeval_runner.py` | Section 4.3 verdict logic |
| ViRanker local activation hoặc Cohere key | Sprint 8 done OR env config | Section 6.2 rerank stage |

### 10.4. Out-of-scope (kịch bản này KHÔNG cover)

- Multi-room / load test concurrent (đã có `scripts/test_rooms_100_v3.py`).
- Multi-bot regression (1 bot tại 1 thời điểm).
- Streaming chunked response timing (mới chỉ đo total latency).
- Authorization/RBAC test (đã có Sprint 11B test suite).
- Tenant isolation test (đã có Sprint 9 P0 fix verify).

### 10.5. CẤM trong kịch bản này

- KHÔNG fix code khi đang chạy test (test = đo, fix = task khác).
- KHÔNG commit code/data ngẫu nhiên trong run script (chỉ ghi reports/).
- KHÔNG thêm "điểm tự phong" vào report (vd "bot đạt 8.5/10") — chỉ dùng 7 metrics + verdict ✅/❌.
- KHÔNG chạy test trên prod tenant — phải tenant_id test riêng (env-driven).

---

## Section 11 — Checklist trước khi chạy

```
[ ] .env có RAGBOT_TEST_TENANT_ID, RAGBOT_TEST_BOT_ID, RAGBOT_TEST_CHANNEL_TYPE
[ ] .env có RAGBOT_TEST_API_TOKEN (JWT bearer hợp lệ)
[ ] .env có RAGBOT_PIPELINE_AUDIT_ENABLED=true (Sprint 13 dependency)
[ ] DATABASE_URL_SYNC accessible (run test SELECT count(*) FROM bots)
[ ] 4 docs ready trong golden_set/docs_4sheets.json (HOẶC paste content)
[ ] golden_set/kich_ban_questions_v1.json placeholder đã replace bằng entity thật
[ ] reports/ dir tồn tại + writable
[ ] Bot row đã sync trước (POST /sync/bot) — không lẫn với reset chunks
[ ] User chạy script có quyền sync.documents_upsert + DB DELETE
[ ] Backup DB nếu chạy lần đầu
```

---

## Section 12 — Định nghĩa "TEST PASS" của kịch bản v1

Toàn bộ test = PASS chỉ khi:

1. **Stage 1 INGEST**: 9/9 metrics PASS + 5/5 sample chunks atomic semantic OK.
2. **Stage 2 QUERY** (43 turns):
   - Answered rate on-topic ≥ 95% (≥ 32/33 PRICE+SERVICE+INFO).
   - Refuse rate correct off-topic ≥ 80% (≥ 8/10 OFF+NOISE).
   - Hallucinated count = 0 (zero tolerance trên off-topic).
   - Faithfulness avg ≥ 0.90 trên answered turns.
3. **Stage 3 OPS**: avg latency < 5000ms, avg cost < $0.005/turn.

Bất kỳ điều kiện nào fail → **report = FAIL**, kèm Section 7 vấn đề + Section 6 stage breakdown
để leader biết fix gì trước.

---

## Phụ lục A — Mapping câu hỏi → corpus expectation

| Category | Expected retrieve target | Expected generate target |
|----------|--------------------------|--------------------------|
| PRICE      | ≥1 chunk từ pricing.csv (top_score ≥ 0.30) | Đáp số tiền có trong context, không round/làm tròn |
| SERVICE    | ≥1 chunk từ services.csv (top_score ≥ 0.28) | List bước/quy trình theo đúng order trong corpus |
| INFO       | ≥1 chunk từ products.csv hoặc packages.csv (top_score ≥ 0.25) | Trả lời ngắn, có địa chỉ/giờ/hotline cụ thể |
| OFF_CORPUS | top_score < 0.20 → trigger refuse | Câu refuse template, không bịa data |
| NOISE      | top_score < 0.15 hoặc intent=OOS → refuse | Refuse + redirect "Bot chỉ trả lời về sản phẩm/dịch vụ của shop" |

---

## Phụ lục B — Common failure patterns + hint

| Pattern | Triệu chứng | Hint debug |
|---------|------------|------------|
| Chunks split giữa số tiền | Câu hỏi giá trả thiếu đơn vị, hoặc trả "999.000" mà không "VNĐ" | Chunker boundary regex chưa giữ "X.XXX VNĐ" atomic |
| Reranker bypass silent | Top score luôn ≈ 0.05-0.15 | `COHERE_API_KEY` empty → check preflight log |
| Refuse miss trên OFF | Bot bịa product không có | Grade threshold quá thấp + prompt thiếu "chỉ trả lời từ context" |
| Hallucinate trên NOISE | Bot trả general knowledge | Intent detection thiếu OOS class hoặc bypass khi refuse confidence < 0.5 |
| Latency spike turn 1 | First-call cold cache | pgvector index warmup hoặc embedder model load |
| Duplicate chunks | Cùng raw_content, khác id | Ingest pipeline retry không idempotent — check dedupe by content_hash |

---

> **End of KICH_BAN_TEST_RAGBOT_v1.**
> Next iteration: v2 mở rộng 60 câu + multi-room comparison + cross-version automated diff.
> Author hand-off: khi run lần đầu, ghi kết quả vào `reports/kich_ban_test_v1_<date>.md`
> theo format Section 6+7 — KHÔNG sửa file kịch bản này (append-only versioning).
