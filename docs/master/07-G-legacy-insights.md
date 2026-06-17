# PHẦN G — LEGACY INSIGHTS (PORT TỪ NESTJS)

## 35. 11 Design Patterns đáng giữ

### 35.1 Pipeline Step-Based Config (per-bot DAG)
- **Concept**: Bot config JSON `steps[]` (webhook/transformer/branch/debounce/ai_call/debug) với condition + onError.
- **Port**: giữ UX low-code, thay runtime bằng **LangGraph compile YAML → StateGraph** khi bot load. Pydantic validate. CRUD API.

### 35.2 Multi-Tenant Model
- `tenant → bot → conversation → message`, lookup composite `(bot_id, user_id)`.
- **Port**: SQLAlchemy unique constraint, Repository base enforce tenant filter, Postgres RLS, Qdrant payload filter, cache prefix.

### 35.3 Execution Logging full context
- Log mỗi step với `input_data, output_data, status, durationMs, stepIndex, parentStepIndex`.
- **Port**: `execution_trace` table + `trace_id` (OTel), aggregate stats qua materialized view. Langfuse span mapping planned.

### 35.4 Bidirectional Moderation
- Input → HTTP 400, Output → HTTP 500.
- **Port**: FastAPI `Depends(check_input_moderation)`, post-process output, Llama Guard 3.

### 35.5 Webhook Context Injection
- Filter `passToAI: true` → append system prompt `--- Webhook Data ---`.
- **Port**: LangGraph state `webhook_context`, wrap `<webhook_data>` tag chống injection.

### 35.6 Variable Substitution 3 mode
- Literal digit / quoted / template.
- **Port**: **Jinja2 SandboxedEnvironment** (safe, chặn `__class__`, `__subclasses__` RCE). Context dict với 3 mode.

### 35.7 Conversation History Debouncing
- Gộp 3 tin user liên tiếp → 1 LLM call.
- **Port (event-driven)**: 202 + WebSocket/webhook callback, Taskiq `group_key=(bot_id, sender_id)`, Redis TTL `N ms`, pop all + merge khi expire.

### 35.8 Output Mapping transformer expressions
- AI output → bot-specific fields qua transformer.
- **Port**: Pydantic discriminated union per template, transformer dùng JSONPath hoặc Jinja2 Sandboxed, validate schema trước trả.

### 35.9 AI Tools Junction per-bot config
- Bot enable subset tool với config riêng.
- **Port**: `bot_ai_tools (bot_id, tool_id, enabled, config JSONB)`, **Pydantic discriminated union** per tool type, circuit breaker per tool.

### 35.10 Stats Aggregation từ logs
- Cron 00:05 aggregate daily stats.
- **Port**: **Materialized view Postgres** CONCURRENTLY refresh; Prometheus metrics realtime song song.

### 35.11 Conversation History Merge Logic
- Merge consecutive user trước khi gửi AI.
- **Port**: Repository trả `list[Message]`, domain service `merge_consecutive_user_messages()` pure function, rolling summary cho > 20 turn.

## 36. 5 Anti-Patterns phải bỏ

1. **N+1 trong Stats Aggregation** → single GROUP BY + materialized view.
2. **Raw SQL template literals** (SQL injection risk) → SQLAlchemy ORM + bind param.
3. **Blocking Debounce Timer HTTP hang** → 202 Accepted + WebSocket callback.
4. **Cascade Delete không audit trail** → soft delete (`deleted_at`) + S3 snapshot trước hard delete sau 90 ngày.
5. **Tool Config JSON không type-safe** → Pydantic discriminated union.

**Bonus**: Sync blocking calls → httpx.AsyncClient + async generator.

## 37. Schema Migration Strategy

1. Dump Prisma → Alembic migration.
2. Đổi tên snake_case, giữ column DB cũ.
3. Không thay DB schema trong migration đầu — chỉ code access.
4. Enum Prisma → `StrEnum` + Postgres enum type.
5. Dual-write 2 tuần → cutover sau canary 5%.

---
