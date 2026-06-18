# Plan 260618 — Fix 3 verified real issues

> Tầng: **[T1-Smartness]** (C2 booking correctness, H2 list coverage) + **[T2-CostPerf]** (MQ auto-gate).
> Tất cả claim dưới đây đã verify trên code/DB thật (rule#0). Mỗi phase đo load-test/test trước khi claim.

## Bối cảnh — verified findings (code thật)

| # | Vấn đề | Evidence (file:line) | Verdict |
|---|---|---|---|
| C1 | RLS bypass | `.env` superuser DSN + DB `rolbypassrls=True`; 23 FORCE-RLS policy có sẵn | **ops fix** (ngoài code) |
| C2 | Booking slot mất ở SSE | `chat_stream.py:291` `conversation_id=None` (inline graph, no get-or-create). `chat.py` OK qua `answer_question.py:83` | **fix 1 site** |
| H2 | Route LIST thiếu | `stats_index_repository.py:31` raw `unaccent ILIKE`; không synonym; `entity_category` NULL | **multi-level** |
| MQ | Multi-query gate theo intent, không complexity | `query_graph.py:2398` intent-map; `query_complexity.py` score chỉ gate adaptive_decompose | **auto-gate** |

**Đã bác bỏ (FUD/outdated)**: cache leak (scope tenant+bot đúng), AdapChunk dead (selector chạy adaptive), worker re-fetch (đã guarded), observability thiếu (33 step live). KHÔNG đụng.

## Phase 1 — C2 SSE booking slot (low-risk, no schema)

- **Root**: "dây chưa nối hết" — `_resolve_action_conversation_id` (production-grade) chỉ wired vào test path (`test_chat/_shared.py:282`).
- **Fix**:
  1. Promote helper → `interfaces/http/routes/_action_conversation.py` (`resolve_action_conversation_id`).
  2. Re-export trong `test_chat/_shared.py` (giữ pin import + `__all__`).
  3. Wire `chat_stream.py:291`: gọi resolver bằng `container.conv_repo()` + `connect_id`/`tenant_uuid`/`workspace_id`. Factoid bot → None (no churn).
- **Files**: `_action_conversation.py` (new), `test_chat/_shared.py`, `chat_stream.py`.
- **Verify**: unit test multi-turn slot persist (action bot) + factoid bot None; pytest test_chat pin xanh.

## Phase 2 — MQ auto-gate by complexity (Adaptive-RAG)

- **Root**: complexity_score đã tính nhưng multi-query không đọc.
- **Fix**: thêm `DEFAULT_MULTI_QUERY_COMPLEXITY_MIN` (constants) + gate trong `_run_multi_query_expansion` (sau intent gate): `if complexity_score < min: return []`. Default-ON nhưng chỉ fire khi câu đủ phức tạp.
- **Files**: `shared/constants/_*` (1 const), `query_graph.py` (gate), test.
- **Verify**: câu factoid đơn giản → MQ skip (token giảm); câu phức tạp → MQ fire (Coverage giữ). Unit test gate.

## Phase 3 — H2-short: synonym-expand keyword cho stats route

- **Root**: route LIST không dùng `custom_vocabulary` synonym.
- **Fix**: trước khi vào `query_by_name_keyword` ILIKE, expand keyword qua `custom_vocabulary` synonym map (per-bot, domain-neutral). `da → (da OR "da chết" OR "chăm sóc da")` chỉ khi owner định nghĩa. Empty vocab = behavior cũ.
- **Files**: `stats_index_repository.py` (OR-expand kw list), `retrieve.py` stats route (pass vocab), test.
- **Verify**: bot có vocab → list đủ; bot không vocab → không đổi. Coverage ≥0.95 khi vocab set.

## Phase 4 — H2-long: entity_category populate + self-query (DEFER, cần alembic + owner vocab)

- Populate `entity_category` lúc ingest (rule heading / LLM-tag) → self-query `WHERE entity_category = :cat`.
- Set-retrieval: route LIST trả toàn bộ row khớp category, bỏ topK-cut.
- **Cần**: alembic + owner định nghĩa category vocab + load-test. KHÔNG làm session này — chờ duyệt.

## Compliance gate (mọi phase)
zero-hardcode · domain-neutral (synonym/category trong DB) · #10 no inject/override · EVOLVE-not-REWRITE · no broad-except · đo trước claim.
