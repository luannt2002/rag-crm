# Luồng Naming Convention — tenant_id vs record_tenant_id (và mở rộng)

> **Muc dich**: Dau hieu nhan biet 1 key la EXTERNAL (INT / VARCHAR tu upstream) hay INTERNAL (UUID PK cua DB minh). Day la file tham chieu khi debug / code / review.

**Trigger**: User nhac nhieu lan "tenant_id co the la external, record_tenant_id la internal UUID, record_<model>_id la UUID cua model trong DB minh". File nay ghi chinh thuc cac luong + vi tri vi pham.

---

## 1. Quy tac dau hieu (user spec)

| Prefix | Type | Nguon goc | Vi du |
|--------|------|-----------|-------|
| `tenant_id` | **INT / VARCHAR** | External (upstream NestJS / API client) | `12345`, `"tenant-abc"` |
| `record_tenant_id` | **UUID** | Internal (PK cua `tenants.id` trong DB minh) | `"00000000-0000-..."` |
| `bot_id` | **VARCHAR** | External slug | `"<test-bot-id>"` |
| `record_bot_id` | **UUID** | Internal (PK cua `bots.id`) | `"cbc3b275-..."` |
| `record_<model>_id` | **UUID** | Internal (PK cua `<model>s.id`) | `record_document_id`, `record_conversation_id`, ... |

**Quy tac chinh**:
- `record_` prefix = UUID + reference noi bo trong DB cua minh
- KHONG prefix = gia tri tu ben ngoai, co the la INT, VARCHAR, hoac gi khac tu upstream
- Composite external pair: `(bot_id, channel_type)` — luon di cung nhau tu ngoai vao
- DB composite index: `(record_bot_id, channel_type)` — internal FK + channel scope

**Dau hieu phan biet tai sao `tenant_id` co 2 bien the**:
- `bots.tenant_id INT NULL` — column luu ID tenant tu NestJS identity service, KHONG phai FK toi `tenants.id`
- `record_tenant_id UUID` — FK chinh thuc trong DB minh, point den `tenants.id`
- 1 tenant co the co ca 2: `tenant_id = 12345` (upstream ref) va `record_tenant_id = <uuid>` (local FK)

---

## 2. Luong EXTERNAL (INT / VARCHAR, khong prefix)

Noi nhan `tenant_id`, `bot_id`, `channel_type`... tu ngoai vao:

### 2.1. HTTP request body (chat endpoint)
- `src/ragbot/application/dto/chat_payload.py:15` — `tenant_id: int | None` (comment: "INT tu NestJS identity")
- `src/ragbot/interfaces/http/schemas/chat_schema.py` — `bot_id` VARCHAR slug
- Conversion: resolver tra ve `bot.id` (UUID) = `record_bot_id` cho internal dung

### 2.2. Bot config / bot management
- `src/ragbot/application/dto/bot_config.py:42` — `tenant_id: int | None`
- `src/ragbot/application/services/bot_management_service.py:31,41` — `tenant_id: int | None`
- `bots.tenant_id INT` column — stores upstream ID de sync

### 2.3. Sync / webhook routes
- `src/ragbot/interfaces/http/routes/sync.py` — INSERT INTO bots (... tenant_id INT ...)
- Nhan tenant_id tu upstream service

### 2.4. RLS session variable
- `src/ragbot/infrastructure/db/engine.py:62` — `SET LOCAL app.tenant_id = '<uuid>'` — CHU Y: day la **UUID** (record_tenant_id) cast thanh string cho Postgres session var. Name `app.tenant_id` trong Postgres session KHONG khop naming convention cua code Python. Lich su / legacy — xem xet doi ten `app.record_tenant_id` trong migration tuong lai.

---

## 3. Luong INTERNAL (UUID, co `record_` prefix)

Noi dung UUID PK noi bo:

### 3.1. Domain entities (DUNG chuan)
- `src/ragbot/domain/entities/conversation.py:32` — `record_tenant_id: TenantId`
- `src/ragbot/domain/entities/document.py:60,89` — `record_tenant_id: TenantId`, `record_bot_id: BotId`
- `src/ragbot/domain/value_objects/tenant_scope.py` — `record_tenant_id: TenantId`

### 3.2. Infrastructure repositories (DUNG chuan)
- `_base.py` — `record_tenant_id` check
- `conversation_repository.py`, `document_repository.py` — `record_tenant_id` scope

### 3.3. DB models ORM (DUNG chuan)
- `models.py` — `record_tenant_id`, `record_bot_id` columns
- `bots.tenant_id INT` la external ref, TACH BIET voi `bots.id UUID` (= record_bot_id khi reference tu chunk/doc)

---

## 4. CAC CHO VI PHAM (P17 plan fix)

### 4.1. application/services/ai_config_service.py
Lines 112, 154, 184, 235, 289, 346, 376: param `tenant_id: UUID | None` — **SAI**: phai la `record_tenant_id: UUID | None`. 7 method signatures + tat ca caller.

### 4.2. application/ports/outbox_port.py:19
Field `tenant_id: UUID` trong `OutboxRecord` dataclass — **SAI**: phai la `record_tenant_id: UUID`.

### 4.3. application/services/model_resolver.py:430-431
`_runtime_cache_key(tenant_id: object, bot_id: object, purpose: str)` — **SAI**:
- Type `object` mask identity confusion
- Nen la `_runtime_cache_key(record_tenant_id: UUID, record_bot_id: UUID, purpose: str)` voi type nghiem ngat

### 4.4. application/services/tenant_guard.py:17-22
`first = scopes[0].tenant_id` — accessing `tenant_id` property on TenantScope — can verify TenantScope thuc te co attr `record_tenant_id` (xem section 3.1), co the day la lich su luc chua rename.

### 4.5. infrastructure/repositories/ai_config_repository.py:497, message_repository.py:31
Docstring/error message viet "tenant_id required" — can cap nhat "record_tenant_id required" de khop voi param name that.

### 4.6. infrastructure/repositories/outbox_repository.py:95
`tenant_id=cast(UUID, r.tenant_id)` — dung `tenant_id` nhu ten attr va column name. Phai la `record_tenant_id`.

### 4.7. infrastructure/repositories/guardrail_repository.py:26, 34, 63
- Line 26: docstring "action_taken, details(dict), tenant_id, request_id, step_id"
- Line 34: `tenant_id=event.get("tenant_id")` — dict key "tenant_id"
- Line 63: `"tenant_id": str(r.tenant_id)` — output dict
Tat ca SAI neu gia tri la UUID internal (guardrail_events table co column `record_tenant_id UUID`).

---

## 5. CAC CHO DUNG (khong can doi)

- `bots.tenant_id INT` column + tat ca access to `cfg.tenant_id` int ben bot_management — external INT, keep
- `chat_payload.tenant_id: int` — external INT tu NestJS, keep
- Comment trong code "INT tu NestJS" noi ro — keep

---

## 6. Checklist khi review code moi

Truoc khi approve PR/commit:

1. **Grep** `def .*tenant_id\s*:\s*UUID` — neu co result, can rename thanh `record_tenant_id: UUID`
2. **Grep** `def .*bot_id\s*:\s*UUID` — neu co result, rename thanh `record_bot_id: UUID`
3. **Grep** `def .*tenant_id\s*:\s*int` — OK, do la external INT
4. **Grep** `def .*bot_id\s*:\s*str` — OK, do la external VARCHAR slug
5. **SQL** `WHERE tenant_id = :<whatever>` trong SQL text() — neu variable la UUID, col name phai la `record_tenant_id`
6. **JSON payload** thay `tenant_id` — check type, neu UUID thi vi pham

---

## 7. Lien ket

- `CLAUDE.md` § Naming Convention — quy tac nguyen goc
- `plans/260422-P17-codebase-sweep-debug/plan.md` — plan fix cac cho vi pham
- Memory: `feedback_naming_convention.md` — rule quick-reference
