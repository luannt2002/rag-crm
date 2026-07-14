> # ⚠️ ĐÃ BỊ THAY THẾ — KHÔNG DÙNG LÀM NGUỒN SỰ THẬT
> Nhiều claim trong file này đã bị bác ở tầng L5 (đọc code thật).
> Nguồn sự thật hiện tại: [reports/L5_CODE_TRUTH_20260714.md](L5_CODE_TRUTH_20260714.md)
> Giữ file này làm lịch sử điều tra, không phải kết luận.

---

# AUDIT TẦNG 2 — FLAG · CONFIG · LỊCH SỬ · STRUCTURED-OUTPUT

**Ngày**: 2026-07-14 · **HEAD**: `71682a2` · nhánh `fix-260623-ingest-expert`
**Tiếp nối**: `reports/TRUTH_VERIFICATION_20260713.md`
**Phương pháp**: 4 agent đào song song → **chủ session tự đối chứng lại bằng tay từng claim nặng**. Mọi số dưới đây **có lệnh chạy thật**, không suy luận.

---

## 0. TÓM TẮT — 6 phát hiện, xếp theo mức nguy hiểm

| # | Phát hiện | Trạng thái |
|---|---|---|
| **1** | **`alembic upgrade head` trên DB trắng seed ĐÚNG 5/264 key.** Dev/CI **KHÔNG THỂ** tái hiện prod | 🔴 **ĐO THẬT** (dựng DB, chạy, đếm, xóa) |
| **2** | **13 route ghi/xóa của `test_chat` KHÔNG có RBAC** — gồm `PUT /admin/config/{key}` và `PUT/DELETE /admin/api-keys/*` | 🔴 **ĐO THẬT** |
| **3** | **2,271 LLM call/ngày ĐI VÒNG QUA router** — không semaphore, không circuit-breaker, không retry | 🔴 **ĐO THẬT** |
| **4** | **Structured output tốn 2 round-trip** vì gateway phớt lờ `response_format`. `understand_query` avg **10.3 giây** | 🔴 **ĐO THẬT** (agent gọi gateway) |
| **5** | **F7/ADR-0007 đã BUILD rồi REVERT cùng ngày, body RỖNG** — và team **sắp build lại từ đầu** | 🔴 **ĐO THẬT** |
| **6** | **~30 flag TRƠ** (giá trị live được set, code **không đọc**) — gồm 1 **killswitch GIẢ** | 🔴 **ĐO THẬT** |

---

## 1. 🔴 SEED COVERAGE — DB KHÔNG TÁI TẠO ĐƯỢC (thí nghiệm thật)

### 1.1 Thí nghiệm

```bash
CREATE DATABASE ragbot_seedcheck_tmp;
ALEMBIC_SQLALCHEMY_URL=<tmp> alembic upgrade head     # chạy đủ 40 revision
SELECT count(*) FROM system_config;
DROP DATABASE ragbot_seedcheck_tmp;
```

```
FRESH DB (alembic upgrade head)  :    5 row
PROD DB                          :  264 row
                                    ─────
                          THIẾU     259 row
```

**5 key sống sót:** `adaptive_context_enabled` · `mmr_similarity_threshold` · `pipeline_multi_query_speculative_enabled` · `rerank_cliff_absolute_floor` · `vlm_caption_prompt`

### 1.2 Nguyên nhân cơ học

```bash
grep -cE "^(COPY|INSERT)" alembic/squashed_baseline.sql   →  0
grep -cE "^CREATE TABLE"  alembic/squashed_baseline.sql   →  44
```
**`squashed_baseline.sql` là `pg_dump --schema-only` — KHÔNG CÓ MỘT ROW DỮ LIỆU NÀO.**
Trong 12 migration active đụng `system_config`, **chỉ 3 có `INSERT`**. **9 cái còn lại dùng `UPDATE … WHERE key='…'` → match 0 row trên bảng rỗng → NO-OP IM LẶNG.**

| | archive (279 file) | chain ACTIVE |
|---|---|---|
| migration có seed INSERT | **93** | 7 |
| `INSERT INTO system_config` | **91** | 3 |
| key `system_config` được seed | **85** | **15** |
| **key mất khi squash** | | **75** |

### 1.3 Hệ quả — DB fresh chạy **STACK KHÁC HẲN**

| | PROD | DB FRESH (rơi về constant) |
|---|---|---|
| `embedding_provider` | `zeroentropy` | **`jina`** |
| `embedding_dimension` | **1280** | **1024** |
| `reranker_provider` | `zeroentropy` | **`jina`** |
| `reranker_model` | `zerank-2` | **`jina-reranker-v3`** |
| cột DB | `vector(1280)` | ← **INSERT 1024-dim = HARD FAIL** |
| `chunking_policy` | `table_dual_index` | **`table_csv`** (silent, chunk sai) |
| `mmr` / `cliff_gap` / `rerank_top_n` / `grounding` | 0.88 / 0.5 / 10 / 0.5 | **0.98 / 0.35 / 7 / 0.3** |

**`guardrail_rules`**: PROD có **13 rule**. Chain active **chỉ seed `prompt_injection_vi`** — 12 rule còn lại (`pii_vi_phone`, `pii_vi_email`, `pii_en_ssn`, `secret_leak`, `sql_injection`, 5 × `prompt_injection_legacy`) đến từ **migration ARCHIVE** `20260516_010f_guardrail_rules_table.py`, **không nằm trong chain**.
→ **DB fresh có ~1 guardrail rule thay vì 13.**

### 1.4 Rủi ro **ĐÃ ĐƯỢC BIẾT LÚC SHIP** — và vẫn ship

`STATE_SNAPSHOT.md:675-681`, dưới heading **"🔴 RISKS / CAVEATS"**:
> *"**#2. Squash là SCHEMA-ONLY** — `squashed_baseline.sql` **không có DATA**."*

### 1.5 Landmine kèm: **thứ tự deploy quyết định model stack**

`scripts/init_system_config.py` seed **158 key** (`ON CONFLICT DO NOTHING`) — **KHÔNG nằm trong quy trình deploy** (`README_DEVOPS.md` chỉ ghi `alembic upgrade head`).
- alembic → script : `llm_default_model = gpt-4.1-mini` (UPDATE no-op trước, rồi INSERT thắng)
- script → alembic : `openai/claude`

**Không gì cưỡng chế thứ tự nào.**

### 1.6 🔴 VI PHẠM SACRED RULE #7 — QUY MÔ 98%

CLAUDE.md: *"Mọi thay đổi DB content state CHỈ qua alembic tracked HOẶC admin UI có audit_log."*
→ **259/264 key (98%) KHÔNG nằm trong alembic.**

---

## 2. 🔴 AN NINH — 13 ROUTE GHI/XÓA KHÔNG CÓ RBAC

```
document_routes    require_min_level = 0   route ghi/xóa = 3
chat_routes        require_min_level = 0   route ghi/xóa = 3
admin_routes       require_min_level = 0   route ghi/xóa = 3
bot_admin_routes   require_min_level = 0   route ghi/xóa = 5
monitoring_routes  require_min_level = 0   route ghi/xóa = 2
bot_insights_routes require_min_level = 2  route ghi/xóa = 0   ← file DUY NHẤT có gate
```

**Danh sách route trần:**
```
PUT    /admin/config/{key}                        ← ĐỔI ĐƯỢC BẤT KỲ system_config NÀO
PUT    /admin/api-keys/{provider_code}            ← GHI API KEY
DELETE /admin/api-keys/{provider_code}/{label}    ← XÓA API KEY
POST   /bots                                      ← TẠO BOT
PATCH  /bots/{bot_uuid}
DELETE /bots/{bot_uuid}                           ← XÓA BOT
PUT    /bots/{bot_id}/{channel_type}/max-history
PATCH  /bots/{bot_uuid}/vocabulary
POST   /bots/{bot_id}/{channel_type}/documents
POST   /bots/{bot_id}/{channel_type}/documents/upload
DELETE /documents/{doc_uuid}                      ← XÓA TÀI LIỆU
POST   /reinit-bots
POST   /validate-link
```

**Mỉa mai**: `PUT /admin/config/{key}` là con đường mà sacred rule #7 gọi là *"admin UI có audit_log"* — **nhưng nó KHÔNG có RBAC.**

⚠️ **Giảm nhẹ (không phải xóa bỏ)**: CLAUDE.md quy định `test_chat` **KHÔNG BAO GIỜ expose ra ngoài**, chặn ở **gateway/network/auth-scope**. Nên bảo vệ hiện tại là **tầng mạng**, **không phải tầng app**. Ai vào được mạng nội bộ là vào được hết.

### 🔥 Và **FIX ĐÃ TỒN TẠI — trên nhánh MẮC KẸT**

`worktree-agent-a98b47eb8ed705bb5` (`cc9880c`) = **RBAC gating cho đúng mấy route này + `test_rbac_test_chat_destructive.py` (229 dòng)** → **CHƯA MERGE, mất luôn.**

---

## 3. 🔴 2,271 LLM CALL/NGÀY ĐI VÒNG QUA ROUTER

```
step              |  n   | avg_ms | in_tok     | cost_usd
generate          | 1751 | 16505  | 10,892,822 | $4.6088   ← QUA router ✔
understand_query  | 1530 | 10314  |          0 | $0.0000   ← BYPASS ✗
grade             |  741 |   911  |          0 | $0.0000   ← BYPASS ✗
rerank            |  741 |  1769  |          0 | $0.0000   ← BYPASS ✗
```

**Bằng chứng code:**
```
structured_output_helper.py:437   return await litellm_module.acompletion(**call_kwargs)   ← GỌI THẲNG
dynamic_litellm_router.py:442     self._provider_semaphores          ← semaphore max_concurrent=6
dynamic_litellm_router.py:447     self._provider_circuit_breakers    ← circuit breaker
dynamic_litellm_router.py:28      retry_with_backoff                 ← retry
                                  ↑ ĐỀU nằm trong _complete_runtime_one — structured KHÔNG đi qua
```

`query_graph.py:1352` lấy `llm._litellm_module` → `:1424` gọi `call_with_schema` → `litellm.acompletion` **trực tiếp**.

### Hệ quả — 3 lớp bảo vệ vừa ship **chỉ bảo vệ `generate`**

| Đã ship | Áp cho `understand`/`grade`? |
|---|---|
| Semaphore `max_concurrent=6` (`09546f8`) | ❌ **KHÔNG** |
| Rate-based circuit breaker (`3006171`) | ❌ **KHÔNG** |
| Retry 3-tầng budget (`213b3d2`/`91163d5`/`8251944`) | ❌ **KHÔNG** |
| `num_retries=0 / max_retries=0` (B7#1) | ✅ CÓ (có trong `_safe_acompletion`) |

→ **`understand` + `grade` đang NÃ gateway KHÔNG GIỚI HẠN** — rất có thể **chính chúng gây ra** 94 `InternalServerError` và việc gateway suy giảm p50 3.3s → 5.8s dưới tải.

### 💸 3,012 step LLM có `cost_usd = 0`

Gateway trả `usage: None`. `_emit_usage_sink` (`structured_output_helper.py:306-316`) **chỉ gọi `extract_usage_from_response`**, **không gọi `estimate_tokens_fallback`** (đường router thì có).
→ **Dashboard chi phí báo thiếu 2,271 lời gọi.**

---

## 4. 🔴 STRUCTURED OUTPUT — 2 ROUND-TRIP CHO MỌI REQUEST

### 4.1 Gateway **PHỚT LỜ `response_format`** (agent gọi thật, đo thật)

```
Call 1: response_format={"type":"json_object"}, KHÔNG có schema trong prompt
        → gateway trả VĂN XUÔI → validate fail → _fallback_json_parse không thấy '{' → None
Call 2: repair retry — _build_repair_messages ĐƯA SCHEMA VÀO PROMPT
        → 3/3 JSON HỢP LỆ, 3.1-4.0s   ✔
```
Thử luôn strict `json_schema` → **cũng văn xuôi**. `response_format` là **no-op trên gateway này**.
→ `ai_models.supports_json_mode = true` cho `openai/claude` là **SAI SỰ THẬT**.

### 4.2 Latency thật của lời gọi grade

| population | min | p50 | max |
|---|---|---|---|
| concurrency 1 | **2799ms** | 3255ms | 4162ms |
| concurrency 8 | **3852ms** | 5319ms | 8347ms |

> **Cap 2.0s nằm DƯỚI cả GIÁ TRỊ NHỎ NHẤT (2799ms).** 100% timeout là **tất yếu số học**.
> Không phải "treo". Nâng trần lên 3.0s vẫn dưới p50 → cứu được **0/30**.

### 4.3 `understand_query` — thuế 1 round-trip **mỗi request**

```
1,530 call  ·  avg 10,314ms
112 structured_output_repair_retry + 122 validation_failed  (journalctl 13/07)
grade: 0 repair-retry — vì wait_for HỦY trước khi call đầu kịp về
                        → CHÍNH CÁI TIMEOUT ĐÃ GIẤU BUG NÀY ĐI
```

### 4.4 Gateway **regress ngày 2026-07-08**

```
ngày        grade thành công   timeout
2026-07-07        12             91
2026-07-08         0             29    ← ĐIỂM GÃY
2026-07-13         0             63
```

### 4.5 `openai/claude` **KHÔNG phải cấu hình sai**

`openai/` là **tiền tố transport của litellm** (→ wire `model: "claude"`). Provider thật = `innocom` gateway (timeout 90s, max_concurrent 6). Agent gọi tay → **HTTP 200 + completion thật**.

### 4.6 FIX ĐÚNG TẦNG

| # | Fix | Lợi ích |
|---|---|---|
| **1** ⭐ | **Đưa schema vào prompt NGAY CALL ĐẦU** cho provider không cưỡng chế `response_format`. Repair turn **đã chứng minh nó hoạt động (3/3)** — chỉ là làm sớm hơn 1 vòng | Cắt **~1,530 round-trip/ngày**. `understand` 10.3s → ~3.5s. Grade chạy trong 1 call |
| 2 | `ai_models.supports_json_mode = false` cho `openai/claude` | Hết chọn nhầm transport |
| 3 | Cho structured path **đi QUA router** | Thừa hưởng semaphore + CB + retry. Nhiều khả năng dứt 94 `InternalServerError` |
| 4 | `_emit_usage_sink` gọi `estimate_tokens_fallback` | Thu hồi 2,271 call vào cost dashboard |
| 5 | **CẮT node CRAG grade** | 418 skip + 306 timeout + **17 grade thật (2.3%)**. `rewrite_retry` chạy **1 lần EVER** — và lần đó rewrite ra **query giống hệt trong 5ms** |

> 🔒 **Sacred #10 an toàn cho fix #1**: đây là prompt **nội bộ pipeline**, không phải prompt answer của bot owner. Và `_build_repair_messages` **đã làm đúng thế này rồi** ở vòng 2.

---

## 5. 🔴 F7 / ADR-0007 — BUILD RỒI VỨT, KHÔNG LÝ DO, SẮP BUILD LẠI

```
5db7922  2026-06-29  feat(stats): F7 attribute-generic stats index — every numeric column queryable
                     5 files, 312 insertions, + tests/unit/test_attribute_generic_stats.py (176 dòng)
9416f4d  2026-06-29  Revert "feat(stats): F7 ..."
                     body: [This reverts commit 5db7922...]      ← RỖNG. KHÔNG LÝ DO.
```

**`docs/adr/0007-stats-price-index-to-attribute-index.md` = status Proposed, CHƯA LÀM.** Nó **đề xuất chính xác cái đã bị vứt.**

> 🔥 **Đây là "fix đi fix lại" ở dạng thuần khiết nhất.** Code đã tồn tại, **lấy lại được bằng `git show 5db7922`**.
> **Hành động giá trị nhất toàn báo cáo: TÌM RA VÌ SAO NÓ BỊ REVERT — trước khi build lại.**

### 5.1 Và 1 revert IM LẶNG — **6 ngày tắt lưới HALLU** (`143ff38` là bằng chứng duy nhất)

```
062d6fa (06-25)  gate grounding cho stats-route, per-bot          ✔
3097755 (06-27)  ÂM THẦM bỏ gate trong 1 commit "integrate"       ✗ ← mọi câu stats bỏ qua grounding judge
143ff38 (07-03)  khôi phục                                        ✔
```
`143ff38` nguyên văn: *"…knob và default vẫn còn nhưng **node không đọc chúng nữa** — comment 'Per-bot overridable' là **SAI SỰ THẬT**."*

### 5.2 Cliff floor — **62 ngày code giữ một giá trị ĐÃ BỊ TỪ CHỐI**

```
archive 0068 (05-08)  DB: 0.05 → 0.15   (đo, từ chối 0.05)
cd08119     (06-17)  code VẪN = 0.05   ← 62 NGÀY code SSoT giữ giá trị đã bị bác
764f559     (07-09)  code → 0.2
```

### 5.3 Nhánh mắc kẹt — **~5,900 dòng, gồm cả bảo mật**

`integ-260624-wave1` = **102 file, 5,885 insertion**, `git merge-base --is-ancestor` → **KHÔNG PHẢI ANCESTOR**:

| nhánh | chứa gì | HEAD có? |
|---|---|---|
| `be94f58` | reranker fix · retrieve fan-out · BM25 soft-delete · pgvector segment gate · **6 file test (600+ dòng)** | ❌ **MẤT** |
| `cc9880c` | **RBAC cho route ghi/xóa test_chat** + test 229 dòng | ❌ **MẤT** (§2 xác nhận) |
| `4b94c28` | **IDOR write-fence 4 repository** + `test_idor_write_fence.py` (261 dòng) + RLS force-parity | ❌ **MẤT** |
| `5d6fb6d` | token-ledger rollup + admin-metrics RBAC (3 file test) | ❌ **MẤT** |
| `548e1c5` | stats `entity_synonyms` + 4 file test | ⚠️ **SPLIT-BRAIN** |

🔴 **SPLIT-BRAIN**: `alembic/versions/20260624_stats_index_entity_synonyms.py` **CÓ trên HEAD** và **sẽ chạy trên mọi DB** — nhưng **nhánh sinh ra nó thì mắc kẹt, và 4 file test biến mất.** Schema ship rồi, code + test thì không.

🔴 **`dcdc55a`** (B-FORMAT route DOCX/PDF/HTML/XLSX qua converter canonical): **7 file, 489 dòng, 2 file test (270 dòng)** → **bỏ**. Hôm sau `7e8dd38` ship bản **1 file, 8 dòng, 0 test**. **Bản hiện tại YẾU HƠN 60× so với cái đã có sẵn.**

---

## 6. 🔴 FLAG — ~30 CÁI TRƠ, TRONG ĐÓ 1 KILLSWITCH GIẢ

### 6.1 Phân loại (415 dòng toggle · 348 KẾ THỪA `cd08119` · chỉ 19 CÓ CHỦ ĐÍCH)

| CLASS | n | Nghĩa |
|---|---|---|
| **A. SETTLED-ON** (live ON, 0 override → **inline, xóa nhánh OFF**) | **42** | |
| **B. SETTLED-OFF** | 37 | ├ **B1 ORPHAN/INERT — xóa flag + code chết**: **15** |
| | | └ **B2 tính năng hoãn THẬT, wiring ĐÚNG — GIỮ CẢ HAI**: **22** |
| **C. LIVE-PRODUCT** (per-bot khác nhau thật → **BẮT BUỘC GIỮ**) | **16** | |
| **D. OPS-KILLSWITCH** (**GIỮ** dù chưa lật bao giờ) | **21** | |
| **E. UNKNOWN/RISKY** | 5 | |
| ☠️ **INERT** (giá trị live được set, **code KHÔNG ĐỌC**) | **~30** | cắt ngang A/B/D |

### 6.2 ☠️ Lớp INERT — nguy hiểm nhất

| flag | LIVE | vì sao **không làm gì** |
|---|---|---|
| **`circuit_breaker_enabled`** | **`true`** | 🔴 **KILLSWITCH GIẢ.** `grep circuit_breaker_enabled src/` → **chỉ có trong DOCSTRING**. `FailoverOrchestrator(` **KHÔNG BAO GIỜ được khởi tạo**. **Với tay lấy nó lúc sự cố = không có gì xảy ra** |
| `table_csv_emit_header_footer_chunks_enabled` | `true` | reader nằm trong `elif strategy == "table_csv"`; strategy live là `table_dual_index` → **TRƠ** |
| `adapchunk_layer5_cross_check_enabled` | `true` | `apply_cross_check` được gọi **VÔ ĐIỀU KIỆN**; flag chỉ đọc khi `strategy is None` |
| `tenant_rate_limit_enabled` | `true` | **0 reader.** Limiter luôn được dựng |
| `docs_only_strict_enabled` | `true` | **0 reader** |
| `understand_query_cache_enabled` | `true` | **0 reader** |
| `cache_stampede_singleflight_enabled` | `true` | **0 reader** |
| `parser_heading_detection` / `parser_table_detection` | `true` | ctor default của `SimpleTextParser`, mà `parser_engine = kreuzberg` → **không phải parser live** |
| `callback_ssrf_guard_enabled` | `true` | `CallbackDelivery` **không bao giờ được khởi tạo** |
| `robust_json_parser_enabled` | `true` | `robust_json_parse()` có **0 call site** |

### 6.3 🔴 `embedding_text_strategy = "auto"` — GIÁ TRỊ KHÔNG TỒN TẠI

```python
_REGISTRY = {"prefix_plus_raw", "raw_only", "field_selective", "null"}   # KHÔNG có "auto"
cls = _REGISTRY.get(key)
if cls is None:
    logger.warning("embedding_text_strategy_unknown_provider_fallback_null", ...)
    cls = NullEmbeddingTextStrategy          # ← RƠI VÀO ĐÂY, MỖI LẦN
```
LIVE: `"auto"` → **luôn rơi về Null Object**.
→ **Toàn bộ registry embedding-text (3 strategy thật) BẤT KHẢ TIẾP CẬN** vì giá trị config sai. Null = pass-through (vô hại), nhưng **config đang NÓI DỐI** và không ai chọn được strategy khác.

### 6.4 🔴 TEST-HARNESS ≠ PROD (pattern lặp lại lần thứ 2!)

```
heuristic_intent_enabled       →  worker=0   test_chat=2
guard_output_parallel_enabled  →  worker=0   test_chat=2
```
2 key này **CHỈ có trong `test_chat/_pipeline_config.py`**, **VẮNG khỏi `workers/chat_worker/pipeline_config.py`**.
→ **Override per-bot cho 2 key này bị PROD BỎ QUA IM LẶNG.**

> **Đây là PATTERN, không phải trùng hợp** — giống hệt bug **A1** (`raw_bytes`: route test truyền, worker prod **không**).
> **Test harness nội bộ và production KHÔNG chạy cùng một pipeline. Đó là lý do bug trốn được.**

### 6.5 Flag sai TÊN KEY (constant đặt tên 1 key, code đọc key khác)

| constant | key code thật sự đọc |
|---|---|
| `cr_prompt_cache_enabled` | `contextual_retrieval_prompt_cache_enabled` |
| `enriched_prefix_persist` | `enriched_prefix_persist_in_content` |
| `self_rag_enabled` | `self_rag_critique_enabled` |
| `rerank_intent_whitelist_enabled` | field DTO lồng `rerank_intent_whitelist.enabled` |
| `diff_reingest_enabled` | `diff_based_reingest_enabled` (mà cái đó chỉ log `not_implemented`) |

### 6.6 ⛔ 3 CÁI BẪY — TRÔNG NHƯ XÓA ĐƯỢC NHƯNG KHÔNG

1. **`decomposer_enabled`** — grep bảo orphan. **SAI.** Key live là **dotted**: `decomposer.enabled` (`query_decomposer.py:151`, live `true`). **Quét regex là giết flag đang sống.**
2. **22 flag Class-B2** (`neighbor_expand`, `self_rag_critique`, `cascade_routing`, `parent_child`, `autocut`, …) — **node CÓ đăng ký trong LangGraph**, enable-gate nằm **TRƯỚC** step span → **trông chết trong `request_steps` dù wiring ĐÚNG**. **OFF ≠ CHẾT.**
3. **`grounding_*` / `*_fidelity_action` / `degeneration_action`** — đây là **guard HALLU=0**. Class C+D, **không phải nợ**.

---

## 7. LEDGER CONSTANT vs DB

| Nhóm | n | Nghĩa |
|---|---|---|
| **L1 DRIFT** (có cả 2, **giá trị KHÁC**) | **24** | **DB thắng 24/24.** Chỉ **2/24** có document |
| **L2 SHADOWED** (giá trị trùng) | **54** | ➡️ **Constant CHẾT ở runtime.** Sửa = vô tác dụng |
| **L3 DB-ONLY** | **187** | trong đó **72 key KHÔNG code nào đọc** = **row rác** |
| **L4 CONSTANT-ONLY** | **87** | ➡️ **Constant DUY NHẤT còn chịu tải** |

**→ 78/171 constant đã chết ở runtime.**

### 7.1 KHÔNG có guard nào canh drift

```bash
grep -rn "== DEFAULT_" tests/ scripts/     # so DB với constant
→ 0 hit
```

| Guard hiện có | Thật sự kiểm gì |
|---|---|
| `check_config_completeness.py` | **chỉ KEY CÓ MẶT**, không kiểm giá trị |
| `audit_config_key_drift.py` | đúng **2 cặp tên key** hardcode |
| `test_seed_paths_agree.py` | ghim vào migration trong **`_archive_pre_squash`** — **KHÔNG nằm trong chain active**. **Test XANH trong khi canh thứ không bao giờ chạy** = **NIỀM TIN GIẢ** |

**Đó là câu trả lời cơ học cho "vì sao 0.98 vs 0.88 sống 9 ngày".**

---

## 8. 🗑️ BỀ MẶT CODE CHẾT — ĐÃ ĐƯỢC KIỂM CHỨNG SẴN

```
DEAD-CODE NOTICE modules  :   66 file  ·  6,477 dòng
Registry comment 100%     :   12 file  (cag · chunk_quality · convo_summary · hyde ·
                                        proximity_cache · self_rag_router · sentence_similarity ·
                                        tenant_model_tier · text_normalizer · tokenizer ·
                                        tools · multi_vector)
```
Mỗi file có header: *"DEAD-CODE NOTICE — 2026-06-03 … AST import-graph reachability scan … **Safe to delete physically; defer to operator decision**"*

⚠️ **Giữ `application/services/hyde_generator.py`** — đó là HyDE **THẬT** (`bootstrap.py:598`). Chỉ `infrastructure/hyde/*` là bản trùng đã chết.

---

## 9. 🛡️ COMMENT PHẢI BẢO VỆ — TRÍ NHỚ DUY NHẤT CÒN SÓT

Git bị xóa. **Một số quyết định CHỈ còn sống trong comment.** Xóa = mất vĩnh viễn.

| file:line | Ghi lại điều gì | Xóa thì vỡ gì |
|---|---|---|
| `nodes/routing.py:201` | *"Production audit (req 9cf611b5): reflect bắn **2×/turn, phí 3.57s**"* | Ai đó "bật tính năng chưa dùng" → tái tạo regression |
| `ingest_stages_enrich.py:232` | *"per-chunk nano + full-doc context = 19k token/call = **bão O(n²)**… **TỒN TẠI 2 bản CR — tắt #1 thì #2 vẫn bắn** (root cause 'đập chuột chũi')"* | Post-mortem của blocker ingest ép ra quyết định đổi stack Jina→ZE |
| `ingest_stages_enrich.py:445` | *"**ĐỪNG bật lại vì tưởng 'nhiều context hơn'** — bật lại là bão O(n²) quay về"* | Biển báo mìn trên config trông như đã chết |
| `ai_config_repository.py:39` | *"field này hardcode `None` từ **93b1258 (2026-05-12)** → secrets resolver luôn trả rỗng → **LiteLLM âm thầm fallback về `OPENAI_API_KEY` cho MỌI provider**"* | Bẫy silent-fallback kinh điển — xóa là tái diễn ở provider tiếp theo |
| `dynamic_litellm_router.py:469` | *"gateway fail RẢI RÁC 10-30% **không bao giờ** tạo ra `fail_max` lần liên tiếp… (**đo 2026-07-13: 236 fail, ZERO lần CB mở**)"* | Bằng chứng CB đếm-liên-tiếp là no-op với hình dạng lỗi thật |
| `_10_rbac.py:54` | *"**Root-cause 2026-06-13**: grounding judge async chiếm hết slot provider → `generate` turn sau xếp hàng → **p95 24-37s** trong khi steady-state 3-5s"* | Sự cố latency tệ nhất dự án. `=4` trông tùy tiện |
| `query_graph.py:2679` | *"**ĐỪNG append** raw per-entity chunk: nạp lại row bảng thô làm đổi câu trả lời (**COVERAGE 1.00→0.90 trong A/B B-1**)"* | Regression 10 điểm mã hóa thành 1 dòng cấm |
| `_06_llm_defaults.py:131` | *"`pii_vi_cmnd` **cố ý LOẠI TRỪ**: pattern là BẤT KỲ số 9/12 chữ số — trong corpus catalog đó là **GIÁ** (150000000 = 150 triệu, 9 chữ số)"* | "Fix hiển nhiên" (thêm rule PII thiếu) sẽ **redact hết giá** |
| `ingest_stages.py:751` | *"**Scope gate (2026-05-26)**: preserve path CHỈ an toàn khi parser intent là row-per-chunk. Với markdown/text, '1 chunk = cả doc' sinh ra **1 chunk 74KB cho corpus pháp lý 98KB**"* — *"root cause của **cụm over-refuse V13**"* | Nới gate = tổng quát hóa hấp dẫn với hậu quả recall thảm khốc |
| `_00_app_env_taxonomy.py:218` | `DEFAULT_EF_SEARCH` *"**hạ 100→64**… 100 là điểm lợi-ích-giảm-dần 1.56×. Recall giữ ≥95%"* — **dòng `= 100` comment lại là LỊCH SỬ CỐ Ý, không phải rác** | |

*(Danh sách đầy đủ ~40 comment — xem output agent. **Một PR "dọn comment" sẽ xóa sạch 2 tháng đo đạc.**)*

---

## 10. ĐÁP LẠI ĐỀ XUẤT CỦA OWNER — bằng số

### 10.1 *"Flag prod không dùng → xóa. Dùng → code thẳng."*

**Đúng về nguyên tắc, nhưng phải theo CLASS:**

| CLASS | n | Hành động |
|---|---|---|
| **A. SETTLED-ON** | 42 | ✅ **INLINE nhánh ON, XÓA nhánh OFF + flag** |
| **B1. ORPHAN/INERT** | 15 | ✅ **XÓA flag + code chết** |
| **B2. Tính năng hoãn** | 22 | ⛔ **GIỮ CẢ HAI** — wiring đúng, chỉ là OFF. Xóa flag = **xóa tính năng đã ship** |
| **C. LIVE-PRODUCT** | 16 | ⛔ **BẮT BUỘC GIỮ** — inline = **phá multi-tenancy** |
| **D. OPS-KILLSWITCH** | 21 | ⛔ **GIỮ** — tồn tại để lật lúc sự cố |
| **E. UNKNOWN** | 5 | 🔍 điều tra trước |
| ☠️ **INERT** | ~30 | 🔴 **SỬA hoặc XÓA — đây là lớp GIẤU BUG** |

### 10.2 *"Seed vào DB rồi thì cần hardcode làm gì?"*

**78/171 constant đã chết → anh ĐÚNG.**
**Nhưng xóa NGAY = tự bắn vào chân**: DB **chưa tái tạo được từ migration** (5/264). Xóa fallback trước khi vá seed → **259 fallback im lặng thành 259 crash** trên mọi DB mới / CI / unit test.

**Thứ tự BẮT BUỘC:**

| Phase | Việc | Cái gì vỡ |
|---|---|---|
| **1. Chặn máu** | **DRIFT GUARD** CI/startup: `system_config[k] != constant[k]` → **FAIL LOUD**, trừ khi có trong `CONFIG_DRIFT_ALLOWLIST` **kèm lý do + ngày** | ⚠️ **CI ĐỎ NGAY trên 24 key L1** — đó là **mục đích**. Phải **phân xử từng cái**, không đóng dấu cho qua |
| **2. Làm DB tái tạo được** ← **ROOT CAUSE** | **1 migration** `INSERT … ON CONFLICT DO NOTHING` **toàn bộ 264 key** + seed lại **12 guardrail rule**. Cho `init_system_config.py` nghỉ hưu. Trỏ lại `test_seed_paths_agree.py` khỏi archive | Phải là `ON CONFLICT DO NOTHING`, không thì **đè mất override operator trên prod** |
| **3. Xóa bề mặt chết** | 72 row rác · 3 key có **2 constant** · 2 key có **2 default khác nhau ở 2 call-site** · **mỗi key CHỈ MỘT NHÀ** | |

> **Chỉ SAU Phase 2, "xóa hẳn constant" mới KHẢ THI.** Và nếu chọn nó → **phải sửa CLAUDE.md** (rule zero-hardcode đang bắt buộc constants.py là SSoT).

---

## 11. BÀI HỌC PHƯƠNG PHÁP (bổ sung 4 bài ở report trước)

### 11.1 Test-harness ≠ production — **PATTERN, không phải trùng hợp**
3 chỗ đã tìm thấy: `raw_bytes` (A1) · `heuristic_intent_enabled` · `guard_output_parallel_enabled`.
→ **Mọi bug "trốn được lâu" đều nên hỏi: route test có đi cùng đường với prod không?**

### 11.2 Grep theo tên key phải xử lý **dotted key**
`decomposer.enabled` trông như orphan `decomposer_enabled`. **Quét regex = giết flag đang sống.**

### 11.3 "Flag live = true" **KHÔNG** có nghĩa là "code đọc nó"
~30 flag TRƠ. Phải trace **reader thật**, không chỉ đọc `system_config`.

### 11.4 Migration `UPDATE` **KHÔNG PHẢI** seed
9 migration dùng `UPDATE … WHERE key='…'` → **no-op im lặng trên DB rỗng**. Chỉ `INSERT` mới là seed.
