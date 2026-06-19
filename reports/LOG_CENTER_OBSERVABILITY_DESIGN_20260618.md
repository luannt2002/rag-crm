# [T2-CostPerf] Log Center / Usage-Cost Observability — Design (RAG+CRM)

> Status: **READ+REPORT** (Phase 1-3 discipline — chưa đụng `src/`). Mọi điểm có evidence `file:line` đào trực tiếp 2026-06-18 (2 Explore agent).
> Câu hỏi user: mọi external paid call (LLM / rerank / embed) phải log token in/out + model + start/end → dashboard verify per-bot → workspace → tenant → system-admin, query theo khoảng thời gian. RAG+CRM report chuẩn.

---

## 0. VERDICT — Hệ thống ĐÃ CÓ ~80%. KHÔNG xây mới, chỉ WIRE 4 lỗ hổng.

Rule #0 (no-guess): trước khi design em map terrain thật. Kết quả — phần lớn "log center" **đã build sẵn**:

| Thành phần user yêu cầu | Đã có? | Evidence |
|---|---|---|
| Bảng log token in/out + model + cost + start/end per call | ✅ **`token_ledger`** | `alembic/versions/20260616_0226_token_ledger.py` — đủ cột: `mode`(ingest/query), `action`(llm/embedding/rerank), `provider`, `model`, `input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`, `cost_usd`, `started_at`, `finished_at`, `duration_ms`, 4-key + `request_id`/`trace_id` |
| Per-request mirror (FK-free, survive delete) | ✅ `monitoring_log` | `20260615_0217_monitoring_log.py` |
| Per-LLM-call forensic (hash, chunk ids) | ✅ `model_invocations` | `20260415_0007_model_invocations.py` |
| Per-bot rollup theo tháng | ✅ `bot_token_usage_log` | `20260514_0101_…` JSONB `usage_by_month` keyed `"YYYY_MM"` |
| Phân cấp tenant→workspace→bot | ✅ first-class | `tenants` (models.py:66-95) · `workspaces` (alembic 0199) · `bots` 4-key unique (models.py:135-254) |
| RBAC scope (ai xem được scope nào) | ✅ | `shared/rbac.py:17-31` — 100 super_admin (all-tenant) · 80 tenant · 60 admin · 20 user |
| Tenant-isolation tự động | ✅ RLS GUC | alembic 0069/0141/0187 `current_setting('app.tenant_id')`; session hook `db/session.py:141-162` |
| Admin analytics endpoints | ✅ một phần | `admin_analytics.py` (cost/latency/usage/all-tenants L100/workspace-aggregate) · `admin_metrics.py` (overview/by-model/top-questions) |
| LLM token capture | ✅ ĐẦY ĐỦ | `dynamic_litellm_router.py:715,755-786` extract_usage + emit ledger + emit `model_invocations` |

**→ Cái user gọi "thêm log center" thực chất = `token_ledger` đã tồn tại từ 2026-06-16.** Việc còn lại KHÔNG phải build bảng, mà là **bịt 4 lỗ hổng wiring**.

---

## 1. 4 LỖ HỔNG THẬT (evidence-based, honest)

### GAP-1 — Rerank + Embed usage bị VỨT (chỉ LLM được log) ⚠️ nghiêm trọng nhất
`token_ledger` có sẵn `action='rerank'` / `action='embedding'`, nhưng **adapter rerank/embed KHÔNG đọc `usage` từ response → KHÔNG emit ledger**:
- Jina rerank: `jina_reranker.py:275` chỉ đọc `data.get("results")` — bỏ `data["usage"]["total_tokens"]` (Jina có trả).
- Voyage rerank: `voyage_reranker.py:299` chỉ đọc results.
- LiteLLM rerank: `litellm_reranker.py:94` chỉ đọc `response.results`.
- Jina embed: `jina_embedder.py:289` chỉ đọc `data["data"]` — bỏ `usage.total_tokens`.
- LiteLLM embed: `litellm_embedder.py:169` chỉ đọc `resp.data` — bỏ `resp.usage.prompt_tokens`.

**Hệ quả**: câu user "tất cả request qua external để tốn tiền … đều log" → **HIỆN TẠI SAI**. Chỉ LLM được log. Rerank (Jina/ZeroEntropy) + embed (ingest cost lớn nhất) **vô hình** trong cost report. Đây là lỗ hổng "report chuẩn chỉnh" lớn nhất.

### GAP-2 — KHÔNG endpoint nào đọc `token_ledger`
Mọi analytics hiện query `request_logs` (qua `RequestLogRepository`/`AuditRepository`). `token_ledger` — bảng canonical giàu nhất (per-action, per-provider, per-model, time-indexed) — **chưa được surface ở bất kỳ dashboard nào**. Index đã built sẵn cho date_trunc: `(record_tenant_id, started_at)`, `(record_bot_id, started_at)`, `(mode, started_at)`, `(provider, started_at)` — nhưng 0 query dùng.

**Hệ quả**: muốn "verify 1 bot dùng bao nhiêu token in/out, model nào, theo khoảng thời gian, breakdown LLM vs rerank vs embed" → data CÓ trong ledger nhưng **không có API trả ra**.

### GAP-3 — Per-request token KHÔNG cộng dồn
`GraphState.tokens` (state.py:43) là **last-writer-wins** — chỉ giữ token của LLM call CUỐI. 1 request có nhiều LLM call (understand + generate + grade + rewrite). Node `persist` đọc `state.get("tokens")` = chỉ call cuối. Per-call ledger thì đúng (mỗi call emit riêng), nhưng **per-request total trong audit event bị thiếu**.

### GAP-4 (scale, optional) — Chưa có rollup/partition
Team query analytics 100% live `date_trunc` GROUP BY append-only table (không materialized view, không partition — xác nhận NOT FOUND). Ổn ở volume thấp; CRM "report theo khoảng thời gian" ở scale lớn (token_ledger ngàn-call/ngày × nhiều tháng) sẽ chậm dần. Cần rollup nightly hoặc partition-by-month KHI đo thấy chậm (KHÔNG làm sớm — T2, đo trước).

---

## 2. DESIGN — WIRE đúng tầng (EVOLVE, giữ Port+DI+RLS+zero-hardcode)

### D1 — Capture rerank/embed usage tại adapter boundary (fix GAP-1) — ƯU TIÊN 1
- **Port**: mở rộng `RerankerPort.rerank()` / `EmbeddingPort.embed()` trả thêm usage (vd `RerankResult(items, usage: TokenUsage|None)`), KHÔNG đổi orchestrator signature (Open-Closed). Hoặc adapter tự emit ledger qua injected `TokenLedger` (giống LLM router đã làm).
- **Reuse hạ tầng có sẵn**: `AsyncDBTokenLedger` (đã drain background-queue → `token_ledger`). Adapter gọi `ledger.emit(TokenLedgerEntry(action="rerank"|"embedding", provider=…, model=…, input_tokens=…, …))`. ContextVar (`tenant_id_ctx`, `record_bot_id_ctx`, `trace_id_ctx`, `mode_ctx` — đã bound ở `config/logging.py`) → attribution tự động, KHÔNG cần truyền tay.
- **Zero-hardcode**: unit price lấy từ `ai_models`/`ai_providers` (giống LLM cost), KHÔNG inline.
- **Provider không trả usage** (vài rerank): fallback ước lượng token = `len(tokenize(query+docs))` hoặc để 0 + flag `status='no_usage'`. Honest, không bịa số.
- Đụng: 5 adapter file (jina/voyage/litellm rerank + jina/litellm embed) + port + DI closure. KHÔNG đụng orchestrator logic.

### D2 — Stats query layer trên `token_ledger` (fix GAP-2) — ƯU TIÊN 2
Repo mới `TokenLedgerAnalyticsRepository` (read-only) + endpoints, RBAC-scoped:

```
GET /metrics/usage/timeseries
  ?scope=bot|workspace|tenant|all
  &record_bot_id=… | &workspace_id=… | &record_tenant_id=…
  &from=ISO &to=ISO
  &group_by=hour|day|month        # date_trunc
  &breakdown=model|action|provider|none
```
SQL (theo precedent `crm_analytics_service.py:87-91` + `monitoring_routes.py:144-152`):
```sql
SELECT date_trunc(:bucket, started_at) AS ts,
       <breakdown_col>,
       sum(input_tokens)  AS tok_in,
       sum(output_tokens) AS tok_out,
       sum(total_tokens)  AS tok_total,
       round(sum(cost_usd)::numeric, 6) AS cost_usd,
       count(*) AS calls
FROM token_ledger
WHERE started_at >= :from AND started_at < :to
  AND record_tenant_id = current_setting('app.tenant_id')::uuid   -- RLS-equiv; or rely on GUC
  [AND record_bot_id = :bot | AND workspace_id = :ws]
GROUP BY ts, <breakdown_col> ORDER BY ts;
```
**RBAC scope gate** (`require_min_level`): bot-scope (own) ≥20 · workspace-aggregate ≥60 · tenant-aggregate ≥80 · `scope=all` (cross-tenant) **=100** (giống `/admin/analytics/all-tenants` đã có pattern). RLS GUC tự chặn cross-tenant ở DB layer; `scope=all` chạy admin-DSN có kiểm `require_min_level(100)` ở app layer.
- **3 màn user yêu cầu** map thẳng:
  - "1 bot dùng bao nhiêu token in/out, model nào, start/end" → `scope=bot&breakdown=model`.
  - "workspace có bao nhiêu bot, tổng bao nhiêu" → `scope=workspace&breakdown=none` + join `bots` count.
  - "admin thấy tất cả tenant" → `scope=all` (L100).

### D3 — Cumulative token accumulator (fix GAP-3)
Thêm key `GraphState._cumulative_tokens: dict` cộng dồn mỗi `_invoke_llm_node`; `persist` đọc tổng thay vì call cuối. KHÔNG đổi graph topology (persist đã là điểm hội tụ mọi path — `query_graph.py:3900` `add_edge("persist", END)`). Nhỏ, surgical.

### D4 — Rollup khi scale (fix GAP-4, defer tới khi đo chậm)
2 lựa chọn, chọn sau khi `EXPLAIN ANALYZE` thấy p95 query > ngưỡng:
- (a) `usage_rollup_daily` (tenant, workspace, bot, model, action, day, tok_in/out, cost) — nightly job từ token_ledger; dashboard đọc rollup cho range >7d, live cho hôm nay.
- (b) Postgres native `PARTITION BY RANGE (started_at)` theo tháng trên token_ledger.
**Đo trước, không build sớm** (T2 + Async Rule 3 measure-don't-guess).

---

## 3. Compliance (sacred 11/11 self-audit)

| Rule | Check |
|---|---|
| #1 zero-hardcode | ✅ unit price từ `ai_models`/`ai_providers`; bucket/scope là enum không magic |
| #2 Strategy+DI | ✅ reuse `AsyncDBTokenLedger` port; adapter emit qua injected ledger, không hard-code |
| #4 tenant isolation | ✅ RLS GUC + RBAC scope gate; cross-tenant=L100 |
| #5 RBAC | ✅ `require_min_level` numeric (20/60/80/100) |
| #6 4-key | ✅ ledger đã có đủ 4-key, không đụng |
| #7 tests real | ✅ TDD: assert ledger row có tok_in/out đúng sau rerank/embed call (mock provider usage) |
| #8 domain-neutral | ✅ thuần technical metering, 0 brand |
| #9 T1/T2/T3 | ✅ T2-CostPerf (observability/cost) — KHÔNG phải T1 (không đổi answer quality) |
| #10 no app-inject/override | ✅ chỉ log + đọc, KHÔNG đụng answer-path LLM |
| #11 model tier | ✅ research = Sonnet subagent read-only; design/code = Opus main |
| HALLU=0 | ✅ provider không trả usage → flag, KHÔNG bịa số |

---

## 4. Ưu tiên ship (nếu user approve)

1. **D1** (rerank/embed capture) — bịt lỗ hổng "cost vô hình" lớn nhất. Cần plan riêng + TDD.
2. **D2** (timeseries API trên token_ledger) — mở khóa 3 màn dashboard user mô tả.
3. **D3** (cumulative token) — surgical, nhỏ.
4. **D4** — defer, đo trước.

**Liên kết 5-tiêu-chí Expert-RAG**: phần này = trục **Cost + Performance** (đo được mới tối ưu được) trong `reports/PROJECT_UNDERSTANDING_EXPERT_RAG_20260618.md`. Faithfulness/Accuracy (trục Đúng) vẫn do Phase A (BUG-1 conflate) + eval-CI dual-gate phụ trách — observability này là điều kiện CẦN để "report chuẩn chỉnh" nhưng KHÔNG thay thế eval-CI.

---

## 5. NOTE — chưa verify trên data
DB local 5434 chưa seed bot/corpus → các bảng ledger rỗng. Em **CHƯA chạy được** query thật để verify số. Honest: design grounded bằng schema + code evidence, nhưng "đã chạy ra số" thì CHƯA — cần seed data trước (cùng blocker với load-test gate Phase A).
