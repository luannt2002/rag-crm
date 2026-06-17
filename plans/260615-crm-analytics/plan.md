# [T2-CostPerf] CRM Analytics read-layer + dashboard

> Tier: **T2** (cost/perf/observability — không đụng T1 answer-path).
> Alembic head: 0218. Build **0219** (token_budgets only).
> Ngày: 2026-06-15. Nguồn yêu cầu: `z-luannt-new-feature.txt` + 2 HTML design (`z-luannt/*.html`).

## MINDSET — EVOLVE không REWRITE (evidence-driven)

Reality-check codebase (Explore subagent, evidence file:line) chứng minh masterplan ngoài **đã build sẵn ~90%**:

| Masterplan đề xuất | Thực tế codebase | Quyết định |
|---|---|---|
| `request_events` table (partitioned) | `request_logs` ĐÃ CÓ: 4-key identity, trace_id, tokens, cost, duration, status, refusal_reason, quality | **KHÔNG tạo bảng mới** — đọc `request_logs` |
| `trace_logs` node-level latency | `request_steps` ĐÃ CÓ: step_name/order/duration/tokens/cost per node (2855 rows live) | **KHÔNG tạo bảng mới** — đọc `request_steps` |
| `token_usage_events` durable | `monitoring_log` (alembic 0217) ĐÃ CÓ, no-FK, survives bot-delete | **KHÔNG tạo bảng mới** |
| Phase-1 "fixes": CircuitBreaker, Semaphore, intent-aware cliff, DI/Port/Registry | TẤT CẢ đã EXISTS (evidence) | **KHÔNG đụng** (đập code chuẩn = lỗi nặng) |
| nano/mini hardcode routing | DB-driven qua bindings (đúng zero-hardcode) | **KHÔNG hardcode** |
| `token_budgets` config table | KHÔNG tồn tại | **TẠO MỚI** (0219) |
| Condense topic-anchor (short follow-up) | MISSING (genuinely) | **DEFER** — T1 RAG-quality, cần đo (no load test now) |

PII guard: `request_logs` lưu `question_hash` (KHÔNG raw text) by design. Top-N "expensive questions" group theo `question_hash` — KHÔNG bịa raw text vào forensic table.

## Deliverables (scope NÀY)

- [ ] **0219** `token_budgets` — per (tenant/workspace/bot) token+cost limit + alert_at_pct (config, không enforce trong scope này).
- [ ] CRM read-layer endpoints (test_chat.py, pattern `_require_owner` + `_caller_tenant_uuid` tenant-scope):
  - `GET /crm/analytics/tokens` — timeline + hierarchy rollup (tenant→workspace→bot_channel).
  - `GET /crm/analytics/latency` — p50/p95/p99 per bot+channel (PERCENTILE_CONT).
  - `GET /crm/analytics/nodes` — per-step latency/token (request_steps) — bottleneck nodes.
  - `GET /crm/analytics/top-questions` — top-N by tokens, group by question_hash, n=min(n,50).
  - `GET /crm/analytics/quality` — status/refusal/error distribution + grounding.
  - `GET /crm/budget/status` — budget vs used %.
- [ ] **static/crm.html** — dashboard tabs (Tokens / Latency / Nodes / Quality / Budget) gọi các endpoint trên.

## Isolation (sacred)
- Mọi query `AND record_tenant_id = :tid` khi caller có tenant (None = platform admin xem all).
- RBAC `_require_owner(request)` mọi endpoint.
- n cap `min(n, 50)`. Zero-hardcode: cost/price từ DB (cost_usd đã tính sẵn ở write-path).

## Verify (KHÔNG cần load test — dùng live data 142 req)
- curl mỗi endpoint → số khớp psql đối chiếu.
- crm.html load → render thật.
- HALLU=0 không liên quan (read-only analytics, không đụng answer-path).
