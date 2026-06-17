# [T3-Refactor + T1] PLAN CODE 10 VIỆC CÒN LẠI (step 7–16)

> Ngày: 2026-06-16. Tiếp nối `plans/260615-fix-all-16/plan.md`. Đã xong: chunking · document_service-split · model_resolver · CRM (0219) · booking · key · docs · EN-parity (0220).
> **Nguyên tắc bất biến**: EVOLVE không REWRITE · mỗi bước verify XANH (pytest + restart + smoke) mới qua · sacred rules (HALLU=0, 4-key, zero-hardcode, no-app-inject, no-psql-hotfix) · runtime behavior bất biến (carve = relocate, không đổi logic).
> **Luồng runtime hiện KHỎE** (verified: answer đúng, request_logs+request_steps(20 node)+monitoring_log+CRM log đủ, HALLU=0, 187 unit test xanh). 10 việc dưới = kiến trúc/dọn dẹp, KHÔNG đụng hành vi.

---

## THỨ TỰ CHẠY (an toàn → rủi ro; mỗi mục = 1 pass context-sạch)

```
PASS 1  → #7  ingest_core decompose      (không vướng path-guard; có golden 134-chunk)
PASS 2  → #8  test_chat split 43 route   (mechanical, churn cao, không cần decompose)
PASS 3  → #9  chat_worker split          (path-guard ~10 + decompose method)
PASS 4  → #11 VN-legal → config per-lang (T1, đo RAGAS)
PASS 5  → #12 hardcode lift + #13 comment rewrite  (trên file đã split)
PASS 6  → #14 RLS redesign               (security, có leak-test)
PASS 7  → #10 query_graph carve          (CUỐI — sacred answer-path, golden-net eval 42Q + path-guard ~20)
PASS 8  → #15 master-docs + #16 audit    (docs + close-out)
```

---

## #7 — ingest_core decompose `ingest()` 2913 → ≤1.2k  [PASS 1]
**Mục tiêu**: tách method `ingest()` (2655 dòng, 7 phase U1–U7) thành stage-method, `ingest_core.py` ≤1.2k.
**Approach (multi-agent đã map)**: `_IngestCtx` dataclass giữ TOÀN BỘ cross-phase state → mỗi phase = `async def _stage_uN(self, ctx) -> None` mutate ctx in-place.
**State-flow đã liệt kê** (đọc/ghi per phase trong carve-plan): content (U2,U3 mutate) · chunks→enriched_chunks→persist_chunks (U4→U5→U6→U7) · `_chunking_strategy` (U4→U5,U7) · `cr_raw_chunks` (U5→U7) · `_segmented_chunks` (U6→U7) · extracted_metadata (U3→U7) · new_embeddings (U7).
**Files**: `document_service/ingest_core.py` (giữ `_IngestMixin` + thêm `_IngestCtx` + 8 stage) · có thể tách `ingest_stages.py` nếu vẫn >1.2k.
**Phase boundaries** (line trong ingest_core): U1 252–279 · U2 290–349 · U3 524–624 · U4 1024–1079(+678–990 logic) · U5 1084–1499 · U6 1560–1613 · U7 1729–2564 · finalize 2566–2806.
**Risk callouts**: U5∥U6 concurrent `asyncio.gather` (giữ NGUYÊN trong `_stage_u5`) · parent-child 2-phase insert + `_parent_id_map` (giữ atomic trong `_stage_u7`) · ExternalServiceError raise sites trong U7 (giữ handler trong stage) · `parser_row_chunks` U2→U4 (thêm vào ctx).
**Steps**: (a) define `_IngestCtx` (~35 field) · (b) carve U1→U7→finalize từng stage, MỖI stage verify · (c) `ingest()` còn ~20 dòng orchestrate.
**Gate**: `test_phase_d_ingest_observability` + `test_upload_u5_u6` + `test_document_service_chunking` + `test_source_allowlist` + `test_recap_pii_vn` XANH · **golden-diff**: re-ingest 1 doc spa → so 134-chunk snapshot (md5+metadata) = 0 diff · restart + health.

## #8 — test_chat (5354) split 43 route → package ≤1.2k/file  [PASS 2]
**Mục tiêu**: tách route god-file. GIỮ URL `/api/ragbot/test/` (đổi = vỡ client). Đổi tên phản ánh BE thật.
**Package** `interfaces/http/routes/ragbot_demo_routes/`:
- `_shared.py` (~400): `_container/_sf/_sys_config/_find_bot_uuid/_tenant_scope/_caller_tenant_uuid/_resolve_body_tenant_int/_require_owner/_coerce_*/_audit_entry/_apply_rolling_summary` + `_build_pipeline_config`
- `schemas.py` (~200): 11 Pydantic DTO (CreateBotRequest…CreateTokenRequest)
- `chat_routes.py` (~1050): /chat, /chat/stream, /chat/history, DELETE /chat
- `bot_admin_routes.py` (~1150): bots CRUD + chunking-info + audit + quality-dashboard + generate-test-questions
- `document_routes.py` (~550): documents list/add/upload/delete
- `admin_config_routes.py` (~250): config + api-keys + redis + models
- `token_routes.py` (~120) · `monitoring_routes.py` (~150): /monitoring + seed + reinit + validate-link
- `pages.py` (~200): @pages_router UI pages + get_self_token
- `__init__.py`: aggregate `router` (include 7 sub) + `pages_router`
**Registration** (`http/router.py` line 95,100): đổi `test_chat` → `ragbot_demo_routes`, prefix giữ `{BASE}/test`.
**Importers cần update**: `chat_stream.py` (`_container/_sf/_build_pipeline_config`) + 11 test (`TestChatRequest`, `_tenant_scope`, `get_self_token`, endpoint introspection).
**Gate**: `test_audit_endpoint_rbac` + `test_token_crud_audit_emit` + `test_admin_config_audit_emit` + `test_p0_critical_auth_fixes` XANH · curl smoke mỗi nhóm endpoint (chat/bots/documents/admin/tokens/monitoring) trả đúng · OpenAPI path set = trước.

## #9 — chat_worker (1796) split  [PASS 3]
**Blocker**: ~10 path-guard test đọc `chat_worker.py` qua path (`test_no_literal_topk_defaults`, `test_per_intent_rerank_skip`, `test_cache_threshold_validation`, `test_conversation_history_for_llm_merge`, `test_multi_query_default_on`, `test_rerank_intent_whitelist`, `test_chat_worker_history_limit_config`, `test_chat_worker_no_redundant_cfg_round_trips`, `test_reranker_min_score_mode_aware`). PHẢI update path → module mới chứa pattern tương ứng.
**Package** `chat_worker/`: `config.py` (`_CHAT_CONFIG_KEYS`+`_cfg_*`+`_parse_intent_list`, 113–388) · `payload.py` (`_maybe_redact_chat_query`+`_resolve_record_tenant_id`, 391–486) · `pipeline.py` (`handle_chat_received`+`_handle_chat_received_body` monster 1207 dòng) · `callbacks.py` (persist/finalize/callback — CẦN decompose method để tách) · `__init__` re-export + `main`.
**Decompose `_handle_chat_received_body`** (1207): tách callbacks-block (1479–1702) thành `_persist_and_callback(ctx)` → pipeline.py ≤1.2k.
**Gate**: cập nhật 10 path-guard test (point tới module chứa pattern) · `test_chat_worker_*` + `test_pii_wire_chat_path` + `test_rls_tenant_scope_enforced` XANH · restart worker + chat smoke (request_steps log đủ).

## #11 — VN-legal logic → config per-language  [PASS 4 · T1]
**Vấn đề**: `Chương/Mục/Điều` + AGG-keywords hard-code (chunking/vn_structural.py, query_complexity.py) — bot EN/JP không được hỗ trợ structural.
**Approach**: đưa marker set + agg-keywords vào `language_packs` (key mới `structural_markers`, `aggregation_keywords` per locale) hoặc `system_config` per-bot `custom_vocabulary`. vn_structural đọc từ config thay vì literal. EN/JP locale có set riêng (rỗng nếu chưa cần).
**Gate**: RAGAS đo VN bot KHÔNG regress (load-test 42Q) · 1 bot EN end-to-end (answer + refuse) · zero-hardcode grep VN literal trong chunking = 0.

## #12 + #13 — hardcode lift + comment rewrite  [PASS 5]
**#12**: audit xong (provider==0 thật, brand-literal=0 thật, infra-import orchestration=3 review). Lift: 3 infra-import → Port nếu thật vi phạm; version-ref 69 hit filter (đa số comment/model-version) → lift các magic number thật vào `shared/constants/`. Grep-guard = 0.
**#13**: viết lại comment lan man → ngắn-gọn-WHY trên các file ĐÃ split (chunking/*, document_service/*, model_resolver/*, crm_*). Không đổi code, chỉ docstring/comment.
**Gate**: `scripts/audit_*` grep guard 0 hit · pytest toàn bộ XANH (comment không đổi behavior).

## #14 — Postgres RLS redesign  [PASS 6 · 🔴 security]
**Gap**: policy tồn tại nhưng `attach_rls_session_hook` chưa wire → repo trên plain session bypass; app connect superuser.
**Approach**: wire hook vào session factory (after_begin SET LOCAL app.tenant_id + workspace) · `DATABASE_URL_APP` = NOBYPASSRLS role `ragbot_app` · leak-test: tenant A query tenant B data → 0 rows.
**Gate**: `test_rls_tenant_scope_enforced` + leak-test mới XANH · không regress ingest/query (RLS không chặn legit path).

## #10 — query_graph (8020) carve build_graph  [PASS 7 · 🔴🔴 SACRED answer-path]
**Phase 0 (BẮT BUỘC)**: golden-net — chạy eval 42Q (anh đã duyệt) snapshot answer+chunks+intent+latency per câu. Mọi stage carve phải reproduce = 0 diff deterministic + faithfulness không tụt.
**Blocker**: ~20 static-source test đọc `query_graph.py` qua path (`inspect.getsource(build_graph)` / `Path(...).read_text()`) — update point tới module node mới.
**Approach**: lift 8 node lớn ra `orchestration/nodes/` (pattern như query_complexity.py có sẵn): retrieve_node (~650) · generate_node (~500) · grade_node (~400) · guard_output_node (~350) · persist_node (~200) · rerank_node (~200) · understand_node (~150) · reflect_node (~100). DI param đổi từ closure-capture → explicit kwargs (functools.partial khi wiring). Conditional-edge routers (8 hàm) + helper closures (`_audit`, `_invoke_llm_node`, `_embed_query`) GIỮ trong build_graph (Route B pragmatic).
**Inner closures** (`_race_vector`, `_run_hybrid_for_query`...) hoist cùng node ra module với explicit DI.
**Gate**: golden-net 42Q = 0 deterministic diff + HALLU=0 sacred · 20 path-guard test update XANH · graph topology test (compile + traverse) · restart + smoke mỗi intent.

## #15 + #16 — master-docs + close-out audit  [PASS 8]
**#15**: sync `RAGBOT_MASTER.md` + `docs/master/A–P` (chunking package, document_service package, model_resolver package, CRM, alembic 0220, ingest stages, node modules). Validate-before-edit (read full → diff → approve).
**#16**: final audit — sacred 11/11 grep guard · pytest full count · expert-RAG 5 tiêu chí đo lại (Fast/Faithful=100%/UX/Perf/Cost) · STATE_SNAPSHOT close-out.

---

## VERIFY MẪU MỖI PASS (đã chứng minh ổn định phiên này)
```
1. pytest <module tests> + importers  → XANH
2. grep re-export (from .X import *) + __all__ gồm class  → đủ
3. restart ragbot-py + /health  → ok
4. chat smoke (factoid answered + OOS refuse HALLU=0)  → đúng
5. (ingest/query) golden-diff snapshot = 0
```

## RỦI RO ĐÃ HỌC (phiên trước)
- `__all__` PHẢI gồm `class` (regex dễ sót → `IngestResult` NameError).
- Shared module-level const (`_SENTENCE_END_CHARS`) đi theo function khi move.
- Patch-target relocate: test `monkeypatch.setattr(mod, X)` phải point tới module MỚI chứa X (đôi khi cả 2 module nếu X dùng ở cả hai).
- Path-guard test (`read_text`/`getsource`) = blocker → update path khi convert package.
- Mixin-move: function `__globals__` = module định nghĩa, nên patch phải đúng module đó.
