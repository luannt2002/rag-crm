# ADR-W1-D4 — Bot/Tenant lifecycle purge + stuck-doc reaper + semantic_cache FK

| | |
|---|---|
| **Status** | ADR-DRAFT — chờ GATE approve trước Phase 4 |
| **Date** | 2026-06-10 |
| **Wave** | W1 — STOP-THE-BLEED (`program/EXPERT-PLAN.md:43`) |
| **Decision ID** | D4 (`program/decisions/00-DECISION-REGISTER.md:13`) |
| **Inputs** | `program/gaps/P2-F-data-cache-event.md` §2 (H-BOT/H-TEN/H-FK/H-REAP) + §5 (Q23/Q24) + §6 |
| **Tier** | [T1-Smartness] data-correctness (stale-cache → sai answer) + [T2] storage unbounded |
| **Stance** | EVOLVE — compose service/FK/worker đã có; 0 engine swap, 0 rewrite |

---

## 0. EVIDENCE CORRECTIONS (đo lại 2026-06-10, trước khi quyết)

Re-verify toàn bộ claim của P2-F trên working tree + live DB. **2 corrections quan trọng**:

1. **Alembic head thật = `0195`**, KHÔNG phải 0260 như brief.
   Evidence: `ls alembic/versions | tail -1` = `20260609_0195_purge_lmstudio_grounding_grading_openai.py`;
   `psql SELECT version_num FROM alembic_version` = `0195`. File mới nhất cho coder lấy số **0196+**
   (check lại collision tại implement-time — multi-coder W1 đang chạy song song).

2. **H-FK ĐÃ ĐÓNG — semantic_cache FK→bots ON DELETE CASCADE đã TỒN TẠI trên live DB.**
   P2-F §2 H-FK ("no FOREIGN KEY", evidence alembic `0014:24-40`) là **STALE**: alembic `0014` đúng là
   không có FK, nhưng `20260516_0107c_missing_fks_orphan_reset.py:87` đã add
   `fk_semantic_cache_bot FOREIGN KEY (record_bot_id) REFERENCES bots(id) ON DELETE CASCADE`,
   và 0107c < head 0195 = đã apply. psql verify trực tiếp:
   ```
   pg_constraint @ semantic_cache:  fk_semantic_cache_bot  FOREIGN KEY (record_bot_id)
                                    REFERENCES bots(id) ON DELETE CASCADE        ← LIVE
   ```
   → **Decision (d) hạ cấp từ "add FK migration" thành "pin regression test"** (§2d). Repro sketch
   `test_semantic_cache_fk.py` của P2-F sẽ **PASS ngay hôm nay**, không FAIL.

**Full FK map → bots (psql `pg_constraint`, 2026-06-10)** — nền của saga §2a:

| Child table | Constraint | ON DELETE |
|---|---|---|
| documents | `fk_documents_bot` | CASCADE |
| document_chunks | `fk_chunks_bot` (+ `fk_chunks_document`→documents CASCADE) | CASCADE |
| conversations | `conversations_record_bot_id_fkey` | CASCADE |
| messages | `fk_messages_bot` (+ FK→conversations CASCADE, `models.py:256`) | CASCADE |
| semantic_cache | `fk_semantic_cache_bot` | CASCADE |
| request_logs | `fk_request_logs_bot` | CASCADE |
| bot_model_bindings | `bot_model_bindings_record_bot_id_fkey` | CASCADE |
| knowledge_edges / message_feedback / tenant_model_policy / document_service_index | (4 FK) | CASCADE |
| request_chunk_refs | `fk_rcr_chunk` → document_chunks | CASCADE (transitive) |

**FK → tenants (RESTRICT chain)**: `bots_record_tenant_id_fkey` (`models.py:130`) + 7 FK RESTRICT từ
`0107c:76-82` (audit_log, documents, conversations, messages, request_logs, quotas, guardrail_events).

**RLS**: `relrowsecurity = t` trên cả 6 bảng (bots, documents, document_chunks, semantic_cache,
conversations, messages); policy `tenant_isolation` polcmd=`*` trên semantic_cache. Liên đới D3 (§4-R3).

**Live stuck-doc count hôm nay** = 0 (`SELECT count(*) FROM documents WHERE state='active' AND
COALESCE(chunks_processed,0)=0` → 0; 44/44 doc `state='active'`). Window là REAL (code path §1.3)
nhưng hiện không có row kẹt — gate test phải **seed** row giả lập, không dựa prod data.

---

## 1. Context — bug chain (rút gọn từ P2-F §2, evidence re-verified)

1. **H-BOT** — `BotManagementService.delete_bot` (`bot_management_service.py:235-269`) = soft-delete
   (`bot_repository.py:270-291` set `is_deleted=true, deleted_at=now()`) + audit + registry
   invalidate (`:256-258`) + outbox `registry_changed` (`:260-268`). **Không purge gì downstream**:
   semantic_cache rows sống mãi (FK CASCADE chỉ fire khi HARD delete — không bao giờ xảy ra),
   chunks/conversations/documents nguyên vẹn, corpus_version Redis key lingering TTL 300s
   (`constants/_04_jwt_auth.py:140` prefix + `corpus_version_service.py:106` TTL), understand_query
   cache `ragbot:uq:v{pv}:{record_bot_id}:{h}` (`understand_query_cache.py:64`) sống tới hết TTL.
   Storage unbounded; không leak read-time chỉ vì UUID rotate.
2. **H-TEN** — `tenant_repository.py:360-392` `soft_delete_tenant` set `deleted_at`, guard
   `TenantHasActiveBotsError` khi còn bot active (`:378-382`). Không có orchestrated cascade; hard
   delete tenant bị chặn cấu trúc bởi 8 FK RESTRICT (§0).
3. **H-REAP** — ingest UPSERT INSERT `state='active'` **đồng bộ** (`document_service.py:1629`)
   TRƯỚC khi chunk+embed async; terminal flip `active`/`failed` chỉ sau khi đếm chunks
   (`document_service.py:3663-3705`, flip wrapped trong `except Exception … best-effort` `:3705`).
   Worker crash giữa 2 điểm → row kẹt `state='active' AND chunks=0`. Reaper chỉ scan
   `WHERE d.state = 'DRAFT'` (`document_recovery_worker.py:155`) → window vô hình. Hai bộ từ vựng
   state lệch nhau: domain entity DRAFT/PUBLISHED/ARCHIVED (`domain/.../document.py`) vs ingest
   `active`/`failed` — ↔️ P2-F §1.
4. **H-FK** — ĐÃ ĐÓNG (§0 correction 2). Còn lại: không có test pin giữ FK này.
5. **Dead invalidate** — `corpus_version_service.py:160-178` `invalidate()` định nghĩa, **0 callsite**
   (grep toàn `src/ragbot`: chỉ definition). Ingest/delete dựa TTL-lag 300s thay vì event-driven bump.
   Đáng chú ý: `DocumentService.delete_all_for_bot` / `delete_document` / `replace_documents_for_bot`
   (`document_service.py:4009-4097`) ĐÃ purge semantic_cache per-bot (P24-L1) — prior art compose được.

---

## 2. Decision

### (a) `BotLifecycleService.purge()` — saga idempotent, hard-delete-anchored

**File mới**: `src/ragbot/application/services/bot_lifecycle_service.py`.
**KHÔNG đổi semantics `delete_bot`** (soft-delete giữ nguyên = reversible, EVOLVE). Purge là bước 2
riêng biệt, chỉ chạy trên bot **đã soft-deleted** (grace window cho undo).

```python
class BotPurgeReport(BaseModel):
    record_bot_id: UUID
    purged: bool                      # False = guard từ chối (bot chưa soft-deleted / không tồn tại)
    db_rows_bots: int                 # rowcount DELETE FROM bots (0 hoặc 1; 0 = idempotent re-run)
    redis_uq_keys: int                # số key uq cache UNLINK
    skipped: list[str]                # ["embedding_cache", "outbox_dedup"] — deliberate no-op, document

class BotLifecycleService:
    def __init__(
        self,
        *,
        session_factory: Any,                 # async_sessionmaker — engine.session_with_tenant
        uow_factory: UnitOfWorkFactory,       # outbox emit (uow.add_outbox_raw, uow.py:106)
        registry: BotRegistryService,         # .invalidate(4-key) (bot_registry_service.py:239)
        corpus_version_service: CorpusVersionService,  # .invalidate(tid, bid) (:160) — WIRE dead code
        redis_client: Any,                    # SCAN+UNLINK uq keys
    ) -> None: ...

    async def purge_bot(
        self,
        record_bot_id: UUID,
        *,
        record_tenant_id: UUID,              # REQUIRED — RLS scope, KHÔNG optional
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> BotPurgeReport: ...

    async def purge_tenant(
        self,
        record_tenant_id: UUID,
        *,
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> list[BotPurgeReport]: ...
```

**Saga order trong `purge_bot`** (mỗi step idempotent; re-run từ đầu sau crash = an toàn):

| # | Step | How | Why thứ tự này |
|---|---|---|---|
| S1 | **Guard** | `SELECT id, workspace_id, bot_id, channel_type, is_deleted FROM bots WHERE id=:b AND record_tenant_id=:t` trong `session_with_tenant` (`engine.py:104`). Không row → return `purged=False`. `is_deleted=false` → raise `BotNotPurgeableError` (chưa soft-delete = chưa qua grace) | Purge chỉ trên rác đã đánh dấu; chặn nhầm bot sống. Đọc 4-key TRƯỚC khi xóa (cần cho S4) |
| S2 | **DB hard-delete + audit + outbox — MỘT transaction** | Trong cùng `session_with_tenant`: (i) `DELETE FROM bots WHERE id=:b AND record_tenant_id=:t AND is_deleted=true` → **FK CASCADE wipe 11 bảng con** (§0 map — KHÔNG viết explicit child-DELETE, CASCADE đã verify live; Simplicity First); (ii) `insert_audit_row(action="purge", resource_type="bot", before=snapshot)` (`audit_chain_writer`, pattern `bot_management_service.py:316-329`); (iii) outbox `bot.purged.v1` qua raw INSERT cùng session (pattern `document_recovery_worker.py:224-246` — KHÔNG dùng uow_factory ở đây vì uow mở session khác = 2 tx); commit | Atomic: bot mất ⇔ audit + event ghi. Crash trước commit = rollback toàn bộ, re-run sạch |
| S3 | **corpus_version bust** | `await corpus_version_service.invalidate(record_tenant_id, record_bot_id)` — wire dead code `:160`. Best-effort (hàm tự nuốt RedisError `:174`) | Sau commit; key tự hết TTL 300s nếu miss |
| S4 | **registry bust** | `await registry.invalidate(record_tenant_id, workspace_id, bot_id, channel_type)` — 4-key từ S1 snapshot | DB đã trống → invalidate reload thấy None → key removed (`bot_registry_service.py:249-252`) |
| S5 | **understand_query cache bust** | `SCAN MATCH ragbot:uq:v*:{record_bot_id}:* COUNT <batch>` + `UNLINK` (HOLE-1 P2-F). Wildcard version vì `prompt_version` thay đổi theo config | Best-effort, TTL backstop |
| S6 | **Deliberate skips — log + report** | embedding L1 cache (content-keyed `ragbot:emb:{model}:{dim}:{sha}` — SHARED cross-bot, purge = evict bot khác, **CẤM đụng**, P2-F §6.5); outbox dedup keys (keyed msg-UUID, TTL 86400s tự hết) | Ghi vào `BotPurgeReport.skipped` để gate test assert chủ đích |

Redis steps S3-S5 đặt SAU commit S2: nếu Redis bust trước mà DB delete fail → cache rebuild từ DB còn
sống (vô hại); nếu DB commit xong crash trước S3-S5 → stale Redis keys có TTL backstop (300s corpus /
uq TTL / registry invalidate đã chạy 1 lần lúc soft-delete `bot_management_service.py:256`) + re-run
`purge_bot` lần 2 = S2 DELETE 0 rows nhưng S3-S5 vẫn chạy → converge. Đây là saga-idempotent đúng
nghĩa: **mỗi step re-runnable, không cần distributed tx**.

**`purge_tenant` = fan-out tuần tự** (`for bot in bots: await purge_bot(...)`) — KHÔNG
`asyncio.gather` per Async Rule 7 (mỗi purge_bot mở session riêng nhưng cùng pool; cascade DELETE là
heavy write, gather N bot = N tx dài đồng thời spike pool + lock). Sau fan-out: gọi
`tenant_repository.soft_delete_tenant` (guard `TenantHasActiveBotsError` giờ pass vì bots đã hard-delete).

**Event mới**: `SUBJECT_BOT_PURGED: Final[str] = "bot.purged.v1"` vào
`shared/constants/_09_message_feedback_thumbs_verd.py` (cạnh `SUBJECT_CORPUS_VERSION_CHANGED:23`).
Payload: `{event_type, record_tenant_id, workspace_id, bot_id, channel_type, bot_uuid, trace_id}` —
4-key đầy đủ cho peer-replica bust.

**Route mới**: `POST /admin/bots/{bot_uuid}/purge` trong `interfaces/http/routes/admin_bots.py`
(cạnh `admin_delete_bot:102-116`), guard `Depends(require_permission_dep("bot", "delete"))` y hệt
route delete (`admin_bots.py:104`). KHÔNG auto-purge theo cron trong W1 (xem Alternatives A3).

**DI**: provider `bot_lifecycle_service = providers.Factory(BotLifecycleService, ...)` trong
`bootstrap.py` cạnh `bot_management_service` (`bootstrap.py:583-589`); reuse singletons
`bot_registry_service:521`, `corpus_version_service:578`, `redis_client`, `session_factory`.

### (b) Tenant purge — fan-out + RESTRICT giữ làm guard (KHÔNG lift schema)

FK `bots→tenants ON DELETE RESTRICT` (`models.py:130`) + 7 RESTRICT chain (§0) **GIỮ NGUYÊN**.
"Guarded" = behavioral, không phải schema: saga drain children trước (fan-out §2a) nên RESTRICT không
bao giờ fire trên happy path; nó còn lại đúng vai trò guard chống hard-delete bậy.

**Tenant row KHÔNG hard-delete trong W1.** Lý do evidence: `fk_audit_log_tenant … ON DELETE RESTRICT`
(`0107c:76`) — audit_log là forensic chain (HMAC `audit_chain_writer`), retention của nó thuộc D11
(Nghị định 13 / PDPD, W6). Hard-delete tenant kéo theo quyết định xóa/giữ audit trail = scope D11,
không phải stop-the-bleed. W1 ship: `purge_tenant` = fan-out purge bots + `soft_delete_tenant`
(row tenant ở lại làm FK anchor cho audit_log). Hard tenant delete = follow-up D11 với
retention policy rõ.

### (c) Stuck-doc reaper — predicate extend (NGẮN HẠN, W1) + DRAFT-unify (TRUNG HẠN, W3)

**NGẮN HẠN — sửa `_scan_stuck_documents`** (`document_recovery_worker.py:146-161`), extend SQL:

```sql
SELECT d.id, d.record_tenant_id, d.workspace_id, d.record_bot_id,
       d.source_url, d.document_name, d.tool_name, d.mime_type
FROM documents d
LEFT JOIN outbox o
    ON convert_from(o.payload, 'UTF8')::jsonb->>'document_id' = d.id::text
    AND o.subject = :subject
    AND o.status IN ('pending', 'processed')
    AND o.created_at > GREATEST(d.created_at, d.updated_at)   -- ĐỔI: was > d.created_at
WHERE d.deleted_at IS NULL
  AND o.id IS NULL
  AND (
        (d.state = 'DRAFT'
         AND d.created_at < now() - make_interval(secs => :stuck_threshold_s))
     OR (d.state = 'active'
         AND COALESCE(d.chunks_processed, 0) = 0
         AND NOT EXISTS (SELECT 1 FROM document_chunks dc
                          WHERE dc.record_document_id = d.id)
         AND d.updated_at < now() - make_interval(secs => :stuck_threshold_s))
      )
ORDER BY d.created_at ASC
LIMIT :batch_size
```

Chi tiết quyết định trong predicate (coder không phải đoán):
- **`chunks_processed` nullable** (psql `\d documents`: `integer`, nullable; alembic 0093) →
  `COALESCE(..., 0)`. Double-check `NOT EXISTS` document_chunks vì `chunks_processed` chỉ được flip
  ghi (`document_service.py:3694`) — row crash giữa chừng có thể NULL nhưng chunks đã ghi một phần.
- **Active-branch dùng `updated_at`, không `created_at`**: UPSERT re-ingest bump `updated_at = now()`
  (`document_service.py:1639`) chứ không bump `created_at` — doc cũ re-ingest mà dùng `created_at`
  sẽ false-positive ngay sweep đầu.
- **Anti-dup JOIN đổi sang `GREATEST(d.created_at, d.updated_at)`**: event upload gốc luôn được
  insert TRƯỚC khi worker UPSERT bump `updated_at` → không exclude row crash; còn replay row do
  chính sweeper emit (created_at > updated_at) exclude đúng các sweep sau. DRAFT-branch không đổi
  hành vi (DRAFT row chưa bị UPSERT → updated_at ≈ created_at).
- **Residual risk chấp nhận**: ingest chạy thật chậm > `DEFAULT_RECOVERY_STUCK_THRESHOLD_S` (900s,
  `constants/_20_cag_mode_cache_augmented_gen.py:230`) sẽ bị replay đè khi đang chạy —
  `content_hash` per-chunk dedup (`document_service.py:2720-2761`) làm replay gần-no-op; threshold
  config-driven, operator nâng được. Không thêm cờ "in-flight" mới (giữ Simplicity).
- **Replay path giữ nguyên `_replay_one_document`** (`document_recovery_worker.py:203-264`) — re-emit
  `document.uploaded.v1` với `document_id` + `source_url` có sẵn; ingest đi nhánh `is_reindex`
  (source_url/existing_doc_id match, exempt khỏi `DocumentDuplicateError` guard
  `document_service.py:1559-1584`); chunks=0 nên per-chunk hash-compare thấy toàn chunk mới → full
  embed. Máy móc này đã prove bằng chính sự cố Thông tư 09/2020 (docstring worker `:5-10`).

**TRUNG HẠN (khuyến nghị, tách PR ở W3 cùng D1/D17)** — **DRAFT-unify**: ingest INSERT
`state='DRAFT'` tại `document_service.py:1629` + `ON CONFLICT DO UPDATE SET state='DRAFT'` (re-ingest
cũng về DRAFT), flip `active` chỉ tại `:3682`. Khi đó nhánh `active`+0-chunk của predicate thành
dead-guard (giữ lại 1-2 release làm safety net rồi gỡ), và 2 bộ từ vựng state (domain `document.py`
vs ingest literal) collapse về một.
- Evidence vô hại với retrieval: cả BM25 lẫn vector chỉ filter `deleted_at IS NULL`
  (`pg_bm25_retrieval.py:118`, `pgvector_store.py:235`), KHÔNG filter `state` — doc DRAFT chưa có
  chunk nên không vào kết quả dù gì.
- Trade-off phải xử ở W3: (i) admin/status API nào đọc `state='active'` làm "ready" sẽ thấy doc
  re-ingest tạm biến mất khỏi list (đúng về ngữ nghĩa — đừng serve doc chưa embed — nhưng đổi UX);
  (ii) index `ix_doc_bot_state` phân bố thay đổi nhẹ. KHÔNG ship chung W1 vì đụng write-path ingest
  (sacred hot path) trong wave stop-the-bleed.

### (d) semantic_cache FK — KHÔNG migration mới; pin regression test

FK `fk_semantic_cache_bot ON DELETE CASCADE` đã live (§0 correction 2). Quyết định:
1. **0 alembic mới cho FK.** (D4 tổng cộng **0 alembic** — mọi mục a/b/c/e đều code-only.)
2. Thêm **pin test** `tests/unit/test_lifecycle_fk_pins.py` (integration-DB hoặc đọc
   migration-source assert) giữ 3 FK CASCADE nền của saga: `fk_semantic_cache_bot`,
   `fk_chunks_bot`, `fk_documents_bot` — migration tương lai drop FK sẽ đỏ CI ngay.
3. Cập nhật `program/gaps/P2-F-data-cache-event.md` H-FK status (orchestrator Phase-3 làm khi
   nhận ADR này — file gaps ngoài quyền Write của ADR-author).

### (e) Wire `corpus_version_service.invalidate()` — 3 callsite

`invalidate()` (`corpus_version_service.py:160-178`) hết dead-code, gọi tại:

| # | Callsite | Vị trí | Ghi chú |
|---|---|---|---|
| 1 | Ingest terminal flip thành công | `document_service.py` ngay sau commit block `:3704` | bust sớm hơn TTL 300s → câu hỏi ngay sau upload thấy corpus mới |
| 2 | `delete_document` / `delete_all_for_bot` / `replace_documents_for_bot` | `document_service.py:4009-4097` (cạnh các DELETE semantic_cache P24-L1 sẵn có) | delete doc → bust |
| 3 | `BotLifecycleService.purge_bot` S3 | §2a | — |

DI: thêm kwarg optional `corpus_version_service: Any | None = None` vào `DocumentService.__init__`
(`document_service.py:786-801`, pattern y hệt `config_service`/`narrate_service` optional) + pass
`container.corpus_version_service()` tại **3 construction site**: `document_worker.py:372`,
`sync.py:507`, `sync.py:693`. `None` → skip (Null-tolerant, không đổi behavior caller cũ).
Best-effort sẵn: hàm tự nuốt Redis errors (`:174-178`) — không cần wrap thêm.

---

## 3. Alternatives rejected

| Alt | Mô tả | Vì sao reject |
|---|---|---|
| **A1. Event-driven purge listener** — subscribe `bot.registry.changed.v1 action=deleted`, purge async | Decouple đẹp, nhưng đứng TRÊN event-bus đang **at-most-once on handler failure** (P2-F §3 H-EO — D8b mới fix trong cùng W1). Purge miss silent = orphan quay lại đúng bug đang chữa. Purge là admin-action tần suất thấp — synchronous saga đơn giản, observable, retry-able hơn. Có thể nâng cấp thành listener SAU khi D8b inbox-pattern prove ổn định ≥1 wave |
| **A2. Hard-delete-only** — `delete_bot` xóa cứng ngay, bỏ soft-delete | Mất grace window undo (bot xóa nhầm = mất sạch corpus + conversations không khôi phục); phá semantics route hiện hữu (`admin_bots.py:102` trả `deleted`, demo flow `test_chat.py:1811` recycle soft-deleted row `test_chat.py:1574`). Two-phase (soft → purge) là pattern chuẩn recycle-bin |
| **A3. Cron sweep-only** — không purge service, một retention worker quét `is_deleted=true` cũ hơn N ngày rồi xóa | Không có purge-on-demand cho GDPR/PDPD "xóa ngay" request; thêm 1 worker mới trái khuyến nghị Q24 ("extend existing sweeper, don't add a 2nd worker"). Có thể THÊM cron gọi `purge_bot` sẵn có ở D11 — service này là building block cho nó |
| **A4. Explicit child-DELETE từng bảng trong saga** (8 DELETE thủ công như Q23 table) | FK CASCADE đã live + verified trên cả 11 bảng con (§0) — viết tay 8 DELETE là duplicate logic DB, drift khi thêm bảng mới (bảng mới có FK CASCADE tự được wipe; DELETE-list tay thì quên). Gate test assert 0-orphan là chốt chặn đủ |
| **A5. Lift `bots→tenants` RESTRICT → CASCADE** | Một `DELETE FROM tenants` lỡ tay = xóa nguyên tenant data không qua saga/audit. RESTRICT là guard đúng; "guarded cascade" làm ở application layer (§2b) |
| **A6. Reaper: thêm state máy mới (`INGESTING`) ngay W1** | Đổi write-path ingest + alembic enum + sửa mọi reader trong wave stop-the-bleed = quá scope. DRAFT-unify W3 (§2c) đạt cùng đích với từ vựng ĐÃ có |
| **A7. Purge semantic_cache bằng `DELETE WHERE record_tenant_id` khi purge tenant** (bypass per-bot) | Nhanh hơn 1 query nhưng phá invariant "mọi purge đi qua saga per-bot" (report/audit per-bot mất); tenant nhiều bot purge tuần tự vẫn bounded (admin path, không hot) |

---

## 4. Consequences + Risks

**Consequences (tốt)**
- Orphan family (chunks / semantic_cache / corpus_key / uq keys / registry key) đóng bằng 1 service
  compose toàn linh kiện sẵn có — 0 alembic, 0 engine swap, đúng EVOLVE.
- `invalidate()` wired → corpus bust sub-TTL; trả lời ngay-sau-upload hết lag 300s (T1 win nhỏ).
- Reaper bắt đúng crash-window thật của prod (`active`+0-chunk) — silent-degrade giảm.
- `BotPurgeReport` cho admin/forensic số liệu thật từng purge (no-guess measurable).

**Risks + mitigation**

| # | Risk | Mitigation |
|---|---|---|
| R1 | **HNSW dead-tuple bloat khi cascade DELETE** — `semantic_cache.query_embedding` HNSW (`0014:45-49`, re-dim `0105`) + `document_chunks` HNSW: DELETE chỉ mark dead, index không shrink tới VACUUM; purge bot corpus lớn = tx dài + bloat | Purge là admin path tần suất thấp (P2-F H-FK đã chấp nhận trade-off này cho FK CASCADE). Level-2 (chỉ khi đo thấy đau): pre-delete `document_chunks` theo batch trước `DELETE FROM bots` với bot có `count(chunks) > threshold` (config `system_config`, KHÔNG hardcode). W1 ship đường thẳng + đo `duration_s` trong structlog event `bot_purged` |
| R2 | **Purge khi RLS bật (D3 cùng wave)** — sau khi runtime chạy `ragbot_app NOBYPASSRLS`, `DELETE FROM bots` chịu policy `tenant_isolation` → saga PHẢI chạy trong `session_with_tenant` (GUC `app.tenant_id`); quên = DELETE 0 rows **silent** | Signature ép `record_tenant_id` REQUIRED (không Optional); S1 guard raise nếu SELECT không thấy row; gate test chạy 2 chế độ (superuser + `ragbot_app`) — chế độ app-role assert vẫn purge được row đúng tenant và KHÔNG đụng tenant khác. Note: FK CASCADE con là RI-action của Postgres, **bypass RLS by design** (PG docs: referential actions always bypass row security) → children wipe đủ kể cả khi policy bật — đây là feature cho purge, đồng thời là lý do `DELETE FROM bots` phải guard tenant chặt (test T6) |
| R3 | Crash giữa S2 commit và S3-S5 → stale Redis keys | TTL backstop (corpus 300s, uq TTL, registry đã invalidate lúc soft-delete) + re-run purge idempotent converge (§2a) |
| R4 | Reaper replay đè ingest đang chạy > 900s | `content_hash` per-chunk dedup gần-no-op + threshold config (§2c residual risk) |
| R5 | Pin test FK chạy unit-tier không có DB | Viết dạng đọc nguồn migration `0107c` assert tuple `("fk_semantic_cache_bot", …, "CASCADE")` (pure-python, chạy mọi CI) + integration test psql khi `DATABASE_URL` có (skip-if-absent) — 2 lớp |
| R6 | `purge_tenant` fan-out fail giữa chừng (bot 3/10 lỗi) | Saga per-bot độc lập; report list trả `purged=False` cho bot lỗi; re-run purge_tenant chỉ làm việc còn lại. Không rollback bot đã purge (hard-delete không undo — chính vì vậy guard S1 đòi soft-deleted trước) |

---

## 5. Implementation plan Phase 4 (tuần tự, TDD failing-test-first)

> Ước lượng tổng: **~1.5 ngày** code+test. 0 alembic. Mọi bước: test viết TRƯỚC, fail, rồi code.
> Branch `w1-d4-lifecycle-purge`. KHÔNG đụng publisher half / bot_version / corpus dual-bump (P2-F §6).

| # | Bước | Failing test trước | File đụng | Est |
|---|---|---|---|---|
| 1 | **Pin FK regression** | `tests/unit/test_lifecycle_fk_pins.py::test_semantic_cache_fk_cascade_pinned` — parse `alembic/versions/20260516_0107c_missing_fks_orphan_reset.py` `_FK_CONSTRAINTS` assert chứa `("fk_semantic_cache_bot","semantic_cache","record_bot_id","bots","id","CASCADE")` + `fk_chunks_bot` + `fk_documents_bot` (PASS ngay — đây là pin, mục đích chống regression tương lai; thêm integration variant skip-if-no-DB query `pg_constraint`) | test file mới | 0.5h |
| 2 | **Constants** | `test_lifecycle_constants.py` — assert `SUBJECT_BOT_PURGED == "bot.purged.v1"` import được (FAIL: chưa có) | `shared/constants/_09_message_feedback_thumbs_verd.py` (+`__all__`) | 0.5h |
| 3 | **Reaper predicate** | Sửa 2 pin test sẵn có **trước**: `tests/unit/test_document_recovery_worker.py:220` + `:283` (đang assert `"state = 'DRAFT'" in sql_text`) → assert thêm `"d.state = 'active'"`, `"COALESCE(d.chunks_processed, 0) = 0"`, `"NOT EXISTS"`, `"GREATEST(d.created_at, d.updated_at)"` (FAIL) + test mới `test_reaper_active_zero_chunk.py::test_scan_returns_active_zero_chunk_row` (fakedb/sqlite-style fixture theo conftest sẵn của file đó: seed row `state='active', chunks_processed=NULL, updated_at=now()-1h` → `_scan_stuck_documents` trả row; seed row `chunks_processed=5` → KHÔNG trả) | `document_recovery_worker.py:146-161` (SQL only — `_replay_one_document` giữ nguyên) | 2h |
| 4 | **BotLifecycleService — purge_bot** | `tests/unit/test_bot_lifecycle_purge.py` (repro P2-F §2 H-BOT làm gốc, mock session/redis): `test_purge_refuses_live_bot` (is_deleted=false → BotNotPurgeableError); `test_purge_deletes_bot_row_scoped` (DELETE có `record_tenant_id` trong WHERE); `test_purge_emits_audit_and_outbox_same_tx` (audit row + outbox INSERT trước commit duy nhất); `test_purge_busts_registry_corpus_uq` (3 collaborator gọi đúng args, uq SCAN pattern `ragbot:uq:v*:{bid}:*`); `test_purge_idempotent_rerun` (lần 2: db_rows_bots=0, redis steps vẫn chạy, không raise); `test_purge_skips_shared_embedding_cache` (report.skipped chứa marker, KHÔNG UNLINK key `ragbot:emb:*`) | file mới `application/services/bot_lifecycle_service.py` | 4h |
| 5 | **purge_tenant fan-out** | cùng file test: `test_purge_tenant_fans_out_sequential` (N bot → N purge_bot tuần tự, không gather); `test_purge_tenant_soft_deletes_tenant_after_drain` (gọi `soft_delete_tenant` SAU fan-out, không raise `TenantHasActiveBotsError`); `test_purge_tenant_partial_failure_reports` (bot 2/3 raise transient → report purged=False, bot 3 vẫn chạy) | cùng file service | 2h |
| 6 | **Wire invalidate() 3 callsite** | `test_corpus_version_invalidate_wired.py`: ingest-flip success path gọi `invalidate(tid,bid)` 1 lần (mock); `delete_document`/`delete_all_for_bot`/`replace_documents_for_bot` gọi invalidate; `None` service → không raise (FAIL cả 4) | `document_service.py` (`__init__:786` + kwarg, flip `:3704`, 3 method `:4009-4097`), `document_worker.py:372`, `sync.py:507`, `sync.py:693` | 2h |
| 7 | **DI + route** | `test_admin_bots_purge_route.py`: `POST /admin/bots/{uuid}/purge` 200 trên bot soft-deleted (service mock), 409/422 trên bot sống, 404 không tồn tại, guard `require_permission_dep("bot","delete")` present (route dependency introspect) | `bootstrap.py` (~`:583` thêm provider), `admin_bots.py` (sau `:116`) | 2h |
| 8 | **Integration gate** (cần DB + Redis thật, skip-if-absent) | `tests/integration/test_bot_lifecycle_purge_e2e.py` — kịch bản §6 gate: seed tenant+bot+2 docs+3 chunks+1 semantic_cache row+corpus_key+uq key → `delete_bot` (soft) → `purge_bot` → assert 0-orphan toàn bộ; chạy thêm biến thể connect `ragbot_app` (R2) nếu role tồn tại | test file mới | 3h |
| 9 | **Regression + ship** | full `pytest tests/unit` 0 regression; grep guards (§7); commit theo Quality Gate 11-item | — | 1h |

Thứ tự cứng: 3 độc lập với 4-7 (có thể song song nếu 2 coder), nhưng 4 → 5 → 7 → 8 tuần tự.

---

## 6. Gate metric (W1 row D4, `program/EXPERT-PLAN.md:43`)

**G1 — 0-orphan e2e** (integration test #8, đo thật không đoán):
```
seed: 1 tenant + 1 bot + 2 documents + 3 document_chunks + 1 semantic_cache row
      + corpus_version Redis key + 1 uq Redis key + registry Redis key
act : delete_bot (soft) → purge_bot
gate: SELECT count(*) FROM document_chunks  WHERE record_bot_id=:b  == 0
      SELECT count(*) FROM semantic_cache   WHERE record_bot_id=:b  == 0
      SELECT count(*) FROM documents        WHERE record_bot_id=:b  == 0
      SELECT count(*) FROM conversations    WHERE record_bot_id=:b  == 0
      redis EXISTS ragbot:corpus_version:{t}:{b}                    == 0
      redis SCAN  ragbot:uq:v*:{b}:*                                == empty
      redis registry key ragbot:bot:{t}:{ws}:{bot_id}:{channel}     == absent
      redis EXISTS ragbot:emb:* seeded key                          == 1   (shared cache CÒN NGUYÊN)
      audit_log: 1 row action='purge' resource_id=:b
      outbox:    1 row subject='bot.purged.v1'
```
**G2 — reaper**: seed doc `state='active', chunks_processed=NULL, 0 chunks, updated_at=now()-1h` →
`_scan_stuck_documents` trả row đó; seed control (`chunks_processed=5` / `updated_at=now()-10s` /
DRAFT có outbox replay mới hơn) → không trả.
**G3 — idempotent**: chạy `purge_bot` lần 2 → exit sạch, `db_rows_bots=0`, mọi assert G1 vẫn giữ.
**G4 — W1 chung**: HALLU=0 hold + 0 regression trên full unit suite.

---

## 7. Rollback plan

- **Code-only ship** (0 alembic) → rollback = `git revert` 1 commit-range; không có schema để hạ.
- Route purge mới là **additive endpoint** — revert không phá caller nào (chưa ai gọi trước W1).
- Reaper predicate revert = trả SQL về `state='DRAFT'`-only (`document_recovery_worker.py:155` cũ);
  2 pin test ở bước 3 revert cùng commit.
- `invalidate()` wiring revert: kwarg optional + None-tolerant → revert từng callsite độc lập an toàn.
- Trigger rollback: (a) purge xóa nhầm row khác tenant (test T6/R2 fail trên prod-like) — sự cố P0,
  revert ngay + post-mortem; (b) reaper replay-storm (metric `document_recovery_replayed_total`
  spike bất thường sau deploy) → revert predicate, giữ phần purge.

## 8. CLAUDE.md compliance tự-audit

- **4-key**: registry invalidate dùng đủ 4-key snapshot từ S1; payload `bot.purged.v1` chở đủ 4-key. ✅
- **Tenant isolation**: mọi DB write trong `session_with_tenant` (`engine.py:104` raise nếu unbound);
  `record_tenant_id` REQUIRED trong signature; WHERE kèm tenant trên DELETE bots. ✅
- **Zero-hardcode**: subject string → constant `_09`; threshold/cadence reuse `DEFAULT_RECOVERY_*`
  (`_20:225-234`); không số mới inline (batch SCAN count lấy constant sẵn hoặc thêm vào constants). ✅
- **No-psql-hotfix**: orphan hiện hữu trên prod KHÔNG dọn bằng psql tay — dọn bằng chạy `purge_bot`
  qua admin route (audit-logged) sau khi ship. ✅
- **Broad-except**: service mới narrow (`SQLAlchemyError, RedisError, OSError, asyncio.TimeoutError`)
  theo policy; không thêm `except Exception` (lưu ý KHÔNG bắt chước `document_service.py:3705`
  noqa-BLE001 sẵn có). ✅
- **No version-ref / domain-neutral**: tên file/class theo purpose; không bot-name literal. ✅
- **Sacred #10 (no app-inject/override answer)**: D4 không đụng answer path. ✅
- **Plan title tier**: `[T1-Smartness]` + T2 storage — declared ở header. ✅

---
*ADR-author Phase 3 · evidence đo lại 2026-06-10 trên head `0195` + live DB `ragbot_v2_dev` ·
READ-ONLY src — file này là output duy nhất.*
