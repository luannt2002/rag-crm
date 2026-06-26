# [T1-Smartness + Security] FIX-ALL Plan — 2026-06-26

> Fix triệt để ~48 issue từ `reports/COMPLETE_INVENTORY_20260626.md` (4 workflow / 16 agent + runtime verify).
> **Stance: EVOLVE không REWRITE** — khung Hexagonal/Port/DI/4-key/sacred GIỮ; fix = nối-dây + late-binding parser (vùng rewrite được phép).
> **Mandate**: mỗi phase có GATE = user approve + load-test backward-verify (Coverage + HALLU=0) trước khi sang phase sau. KHÔNG psql hotfix (sacred#7) — content-state qua alembic.

---

## 0. Mô hình multi-agent execution

- **Coordinator = main session (Opus)**: review diff, chạy gate (load-test + grep-guard + pytest), merge tuần tự, quyết sequencing. KHÔNG delegate decision.
- **Fix-agent = Opus, `isolation: worktree`** (Fable unavailable → opus; Sonnet CẤM ghi `src/`). Mỗi stream 1 agent worktree riêng → parallel code-edit không đụng nhau.
- **Phase barrier**: hết phase → coordinator gom diff → gate → user approve → phase sau.
- **Verify per stream**: failing-test-first (TDD) + real assertion; grep self-verify zero-hardcode/domain-neutral; 4-key; narrow-except.

---

## PHASE 0 — P0 SECURITY + REVIVE (wiring, ~0 framework change, nhanh)

**Mục tiêu**: đóng lỗ bảo mật + hồi sinh empty-answer + HALLU-net. Rủi ro thấp, làm trước.

### S0-A — RLS hardening 🔴 (security, làm CẨN THẬN, test kỹ)
- **Issues**: RLS-1 (app=superuser bypass), RLS-2 (policy thiếu `missing_ok` → crash-on-fix), SB-2 (conversation_state no-tenant-scope + no-GUC).
- **Approach**: (1) đổi `DATABASE_URL` → role `ragbot_app` (ĐÃ TỒN TẠI, non-super) + gỡ `RAGBOT_ALLOW_SUPERUSER_RUNTIME`; (2) alembic: thêm `missing_ok`/fix mọi RLS policy chịu được khi RLS engage; (3) bảo đảm session SET `app.current_tenant` GUC mỗi request + worker; (4) `conversation_state.save_state` thêm scope `record_tenant_id`; (5) health-check boot fail-loud nếu connect = superuser.
- **Risk**: đổi role → query thiếu GUC sẽ trả 0-row (fail-closed) → phải test mọi path set GUC. **Rollback**: revert DSN.
- **Verify**: live probe — set GUC tenant A đọc chỉ A; không GUC → 0 row; smoke 3 bot vẫn trả lời.
- **Test**: `tests/integration/test_rls_tenant_isolation.py` (set GUC → scoped; no GUC → empty).

### S0-B — Provider revive-complete
- **Issues**: #1 (4 binding query-path còn OpenAI dead), CB-CLIENT-4XX (circuit-breaker mở provider khỏe vì client-4xx).
- **Approach**: alembic re-point binding `grounding`+`slot/SlotSchema`+enrichment query-path → innocom/zembed phù hợp (giữ embed/enrichment ingest-time nếu cần). CB: phân biệt client-4xx (không count) vs provider-5xx/timeout (count).
- **Verify**: trace hotline/gai-mòn → hết empty; CB không OPEN khi 1 bot 4xx.

### S0-C — HALLU-safety + qwen3 capability-route
- **Issues**: AG-A2 (grounding fail-OPEN), #5/SB-3/PLM-5 (qwen3 route-by-name).
- **Approach**: (1) `grounding_failure_mode` config (default `fail_closed`) → grounder dead → substitute `oos_answer_template` (nhánh đã có), KHÔNG pass unverified; (2) structured-output dispatch đọc `cfg.supports_json_mode` (resolver ĐÃ surface tại `_binding_mixin.py:222`) → 3-bậc: strict-schema / json_object / tool-mode; route theo CAPABILITY không substring tên.
- **Verify**: grounder dead → answer = refuse (không silent-pass); qwen3 → json_object branch, validation-fail giảm.

### S0-D — Multi-turn reconcile
- **Issue**: MT-1 (history tách `chat_histories` HTTP vs `conversations`/`messages` worker).
- **Approach**: hợp nhất 1 source-of-truth (canonical conversation store) hoặc reconcile-read gộp 2 nguồn theo (bot, connect_id). Domain-neutral, không per-bot.
- **Verify**: turn SSE → turn worker cùng connect_id → turn sau thấy lịch sử.

**GATE 0**: load-test 3 bot (HALLU=0, empty=0, Coverage giữ ≥ baseline) + RLS probe + pytest. → user approve.

---

## PHASE 1 — P1 QUALITY (re-ingest, late-binding table = fix gốc)

### S1-A — LATE-BINDING TABLE FLOW ⭐ (fix gốc, giải 6 issue cùng lúc)
- **Issues**: #2 PRICE_MIN_VND lọc số phi-giá, #3 price-centric, #4 header col_N, #6-ingest media-col, #8 cross-sheet, ING-3/ADR-0007.
- **Approach** (parser-adapter = vùng rewrite được phép):
  1. Parser **giữ table-block** nguyên (mỗi hàng = record gắn nhãn header tự động) — tham khảo `_external_refs/RAG-Anything/enhanced_markdown` + MinerU pattern.
  2. **Table-aware chunking** (Block Integrity: KHÔNG cắt ngang hàng/bảng).
  3. **Attribute-generic**: bỏ price-centric → mọi cột = attribute gắn nhãn header (price chỉ là derived-VIEW backward-compat). Bỏ PRICE_MIN_VND cho cột phi-giá.
  4. Header robust: merge 2-dòng + nhận dòng-title-section (không ăn thành data) + cross-sheet reconcile theo entity_name.
  5. **Late-binding**: LLM đọc bảng-có-nhãn lúc answer → KHÔNG cần `column_roles`.
- **N+1-proof**: property-based canary random-domain (ngành/cột bất kỳ ingest đúng, 0 config). Test `tests/unit/test_multibot_ingest_canary.py` mở rộng.
- **Verify**: re-ingest xe → "tồn kho 165/65R14"=404 (không 702); spa giữ 92%; bot random-domain pass canary.

### S1-B — Anti-fabricate floor
- **Issues**: #6-sysprompt (bịa URL), AG-A1.
- **Approach**: append rule URL-grounding vào `language_packs` (sacred#2 APPEND-only, KHÔNG override answer): "chỉ trả link/số có trong context, thiếu → refuse". Per-bot opt-out qua plan_limits.
- **Verify**: hỏi link thiếu → refuse (không namphat.vn).

### S1-C — Lifecycle fail-loud
- **Issues**: ING-7 (delete không purge stats), DLC-1 (idempotency không 'done'), DLC-2 (failed kẹt vĩnh viễn).
- **Approach**: DeleteDocumentUseCase purge `document_service_index` + filter `deleted_at` mọi serving query; idempotency mark_done/mark_failed wire thật; failed + transient → auto-retry bounded.
- **Verify**: xóa doc → entity biến mất khỏi serving; doc failed-transient → retry.

### S1-D — Observability fail-loud
- **Issues**: OBS-1 (empty=success no-warn), OBS-2 (completion_tokens=0 14%), 15/27 step chưa instrument.
- **Approach**: empty LLM answer → warn + status; fix completion_tokens count qwen3 streaming; instrument step thiếu (kwargs không `extra=` — bug structlog đã biết).
- **Verify**: empty → log warn; completion_tokens>0; request_steps đủ.

### S1-E — Retrieval quality
- **Issues**: RQ-1 (sparse VN-pin → non-VN bot), RQ-2 (article-filter waste), RQ-3 (anisotropy → BM25-dependent), RQ-5 (chunk_quality dead).
- **Approach**: sparse preprocessing config-driven theo locale; article-filter gate per-bot; cân BM25/vector weight (đo); wire chunk_quality.
- **Verify**: non-VN smoke; legal article không 2× round-trip waste.

**GATE 1**: load-test ĐẦY ĐỦ (Coverage ≥ target, HALLU=0, xe stock đúng) + canary random-domain pass + pytest. → user approve.

---

## PHASE 2 — P2 ARCH (defer tới T1≥95%, T2/T3)

### S2-A — Flow cleanup (T3)
god-node `retrieve.py` 96KB tách theo trách-nhiệm; 120 config-key phân loại (per-bot vs rác); gộp 2 decomposer; gỡ dead `condense_question`; DI-leak orchestration→Port.

### S2-B — Boundary/security còn lại (T2)
FMT-3 (app-inject caption hardcoded VN → language_pack, sacred#10); FMT-1 (`local://` qua parser); SB-4 (SSRF webhook deliver-time validate); SB-5 (PII-redact vs slot-extractor).

### S2-C — Schema/cleanup
ADR-0007 schema-migration (nếu late-binding cần); stale-entity purge xác nhận; cache key tenant-scope.

**GATE 2**: regression-free + arch-metric (LOC/complexity giảm). → user approve.

---

## Verification mandate (mọi phase)
- **Load-test parallel** (asyncio.gather sem N=8-10, KHÔNG sequential — feedback_ragas_parallel) với `bypass_cache` + check chunk thật.
- **2 metric**: Faithfulness (HALLU=0 sacred) + **Coverage** (% câu corpus-có-đáp-án trả đúng) ≥ 0.95 blocker.
- **Backward-verify từng step**: chunk ingest→retrieve→topK→prompt→answer đúng.
- Grep-guard: domain-literal/version-ref/provider-hardcode/zero-hardcode = 0.

## Risk & rollback
- S0-A (RLS) rủi ro cao nhất → test integration trước, rollback = revert DSN.
- Mỗi stream worktree riêng → rollback = drop worktree.
- Alembic mọi content-state → downgrade path.

## Thứ tự đề xuất
**Phase 0 trước** (security + revive, rủi ro thấp, giá trị cao ngay) → GATE → **Phase 1** (late-binding = fix gốc N+1) → GATE → **Phase 2** (defer được).

## Open (cần trước khi exec)
- Tenant#2 để probe cross-tenant thật (RLS verify).
- Bot ngành-khác để verify N+1 thật sau S1-A.
- Provider embed cho re-ingest Phase 1 (ZE đang sống).
