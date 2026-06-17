# P1-C PRE-SEED · Multi-tenancy / Security audit (đã verify file:line)

> Chạy trước Phase 1 bằng 5 read-only agent (2026-06-10). Mọi mục có evidence `file:line`.
> Phase 1-C dùng làm điểm xuất phát; Phase 2-C gán nhãn ✅/🕰/↔️/🐛.

## 1. RLS enforcement — 🐛 BLOCKER (đúng như README/STATE_SNAPSHOT tự khai)
- `attach_rls_session_hook` **định nghĩa nhưng 0 production callsite**:
  def tại `src/ragbot/infrastructure/db/session.py:154`; chỉ test gọi (`tests/unit/test_rls_set_local.py:112/116/137`).
  `bootstrap.py:159-163` build `session_factory` nhưng KHÔNG attach hook.
  Xác nhận: `README.md:221`, `STATE_SNAPSHOT.md:25`.
- RLS policy **CÓ tồn tại** trong DB (23 policy, 20+ bảng): migration `0069` (direct tenant tables),
  `0108` (document_chunks JOIN), `0141` (workspace-aware dual-GUC), `0187` (re-assert canonical).
  Bảng phủ: documents, document_chunks, bots, conversations, messages, semantic_cache, audit_log...
- Repo session split:
  - ✅ pgvector path DÙNG `session_with_tenant` (SET LOCAL app.tenant_id): `pgvector_store.py:361-363`.
  - 🐛 bot/doc/conversation/message repo dùng **bare `_new_session()`** không GUC:
    `repositories/_base.py:34`, `bot_repository.py:145`, `conversation_repository.py:94`, `document_repository.py:99`.
  - ↔️ semantic_cache dùng bare `self._sf()` + WHERE thủ công (`semantic_cache.py:408/562`) — không leak nhưng RLS không phải cơ chế enforce.
- Role `ragbot_app` NOBYPASSRLS CÓ (migration `0073`/`0186`) nhưng **runtime dev vẫn dùng postgres superuser** →
  toàn bộ RLS inert ở dev (`docs/PROJECT_FLOWS.md:741-742`, `engine.py:60-81` gate qua `DATABASE_URL_APP`).
- **Kết luận**: isolation hiện chỉ dựa vào app-code nhớ filter + manual WHERE. RLS = defence-in-depth CHƯA bật. → D3.

## 2. Semantic cache (L2 pgvector) — ✅ scoped đúng (trái với lo ngại review ngoài)
- Cosine search **CÓ** filter `record_bot_id` AND `record_tenant_id` TRƯỚC khi so cosine:
  `semantic_cache.py:474-496` (slow path), `:416-434` (exact-hash fast path). Threshold 0.97
  (`constants/_04_jwt_auth.py:144`). KHÔNG có cross-tenant vector scan.
- Row INSERT có đủ record_bot_id + record_tenant_id + workspace_id (`semantic_cache.py:573-579`),
  guard refuse khi tenant None (`:545-552`). RLS policy enabled (0069/0141/0187).
- 🐛 phụ: `build_response_cache_key` (`application/ports/cache_port.py:103-113`) scoped đẹp NHƯNG **dead code** (0 callsite).
- ↔️ invalidation: L2 xóa theo document mutation (`document_service.py:3582/3995/4038/4088`) NHƯNG
  `bot_management_service.delete_bot` (soft-delete) **KHÔNG** purge semantic_cache → orphan rows tới khi TTL.
  Không có `BotLifecycleService` tập trung. → D4.

## 3. Ingest worker tenant context — ✅ phần lớn đúng, 1 gap nhỏ
- Worker BIND tenant GUC: `document_worker.py:78-86` `bind_request_context()` từ payload →
  `engine.py:139-145` `SET LOCAL app.tenant_id` trong `session_with_tenant`; UoW cũng enforce (`uow.py:44-54`).
- 🐛 1 bare `self._sf()` không GUC: `document_service.py:905` `_upsert_doc_summary` (scoped bằng PK, low risk).
- Event payload mang đủ 4-key: publish `use_cases/ingest_document.py:109-123`, parse `document_worker.py:92-99`.
- content_hash dedup **per-bot** (đúng): query `document_service.py:1563-1574`, unique index `(record_bot_id, content_hash)` migration `0048:79-83`. Hai tenant upload cùng PDF = hợp lệ, không dedup chéo.
- 🐛 ingest fairness: **KHÔNG có** per-tenant limit. 1 stream global + 1 `Semaphore(5)` chia chung mọi tenant
  (`redis_streams_bus.py:153-170`). Noisy neighbor thật. → D8.

## 4. Workspace / RBAC / Quota — 🐛 workspace chỉ là slug
- workspaces **KHÔNG phải table thật**: `workspace_id VARCHAR(64)` stamp lên 16 bảng (migration `0062`),
  backfill từ `bots.workspace_id` (`models.py:127`). Không entity, không FK, không lifecycle. → D2.
- RBAC **global per-tenant**: 1 role string trong JWT → `request.state.role` (`tenant_context.py:423`),
  check numeric level `rbac.py:39-48`. KHÔNG có workspace_members / workspace_roles map. → D2.
- Quota: **tenant-level only** — `tenants.rate_limit_per_min` (`tenant_rate_limiter.py:12-17`) +
  `tenants.monthly_token_cap` (`tenant_token_meter.py:8-17`). KHÔNG có tầng workspace. plan_limits per-bot
  chỉ tune pipeline, không phải counted quota. → D2/D8.
- ✅ per-tenant query rate limit CÓ enforce: `tenant_context.py:208-302`, key `rl:tenant:{uuid}:{minute}`, HTTP 429.
  Token preflight cap CÓ nhưng default warn-only (`enforce_preflight_cap=False`).

## 5. Schema FK / embedding dim — ✅ FK cascade tốt, 🐛 không guard đổi embed model
- FK ON DELETE CASCADE đầy đủ: `documents.record_bot_id→bots` (migration `0107c`),
  `document_chunks.record_document_id→documents` (`0013`), `document_chunks.record_bot_id→bots` (`0108`).
  Xóa bot → chunks wiped 2 đường cascade, không orphan trong HNSW. ✅
- embedding column **fixed `vector(1280)`** (zembed-1 matryoshka, migration `0085`). Toàn bảng 1 dim cho mọi bot.
  Per-bot embed model khác dim → fail ingest (Postgres dim mismatch) hoặc garbage cosine.
- 🐛 **KHÔNG guard** đổi embedding model khi đã có chunks: `bot_management_service.update_bot:170-215` không check chunk count.
  Chỉ có Prometheus counter detection-only `query_graph.py:702-727` (`_check_embed_model_consistency`, never raises).
  corpus_version = hash(MAX(updated_at)) (`corpus_version_service.py:113-158`), KHÔNG bump khi đổi binding. → D10.

## Map decision
RLS→D3 · semantic cache invalidation→D4 · ingest fairness→D8 · workspace entity→D2 ·
quota cascade→D2 · embedding versioning→D10.
