# ADR-W1-D3 — RLS enforcement end-to-end (hook + app-role DSN + workspace GUC + leak-test CI)

> Phase 3 ADR · Wave W1 · Tier **[T1-AnToàn/P0]** · Date 2026-06-10
> Nguồn gap: P2-C RLS-1/2/3 (psql-proven inert) · Research: deep-research wf_38df9b83 (10 findings HIGH, 3-0 unanimous)
> STANCE = EVOLVE: policy 23 cái + FORCE + role `ragbot_app` ĐÃ TỒN TẠI — chỉ cắm dây, KHÔNG redesign.

## 1. Context (SỰ THẬT psql 2026-06-10)
- App connect `postgres` (rolsuper=t, rolbypassrls=t) → 23 policy bypass 100%. Bypass-proof: bogus `app.tenant_id` vẫn trả 21 bot rows (P2-C §4).
- `.env` không có `DATABASE_URL_APP` + `RAGBOT_ALLOW_SUPERUSER_RUNTIME` set → `engine.py:67-81` superuser fallback (có WARNING).
- `attach_rls_session_hook` (`session.py:154`) 0 production callsite; `bootstrap.py:160-165` build factory nhưng không attach.
- `app.workspace_id` GUC không được SET ở đâu → workspace clause của policy 0141 degrade tenant-only (`COALESCE(...)=''` → OR short-circuit).
- Research xác nhận (PG docs + AWS + Bytebase + Crunchy, 3-0): (i) superuser/BYPASSRLS bypass VÔ ĐIỀU KIỆN kể cả FORCE — FORCE chỉ trị table-OWNER; (ii) app role phải non-superuser + NOBYPASSRLS + **non-owner**; (iii) SET LOCAL + custom GUC + `current_setting()` là pattern chuẩn duy nhất sống được sau pooler; (iv) **client không được tự SET GUC** — chỉ trusted server code; (v) RLS = primary control, app-WHERE = secondary belt (giữ cả hai).

## 2. Decision (4 mảnh, thứ tự land an toàn)
1. **(code, land TRƯỚC — no-op an toàn)** Attach `attach_rls_session_hook(session_factory)` một lần trong `bootstrap.py` ngay sau `create_session_factory` (`:162-165`). Hook là no-op khi ctx unbound (`session.py:126-127`) và no-op thực tế dưới superuser → land không đổi behavior, chỉ chờ DSN flip. Workers đã bind ctx (`document_worker.py:73`) → tự enforce khi DSN đổi.
2. **(code, cùng PR)** Thêm `workspace_id_ctx: ContextVar[str|None]` (cạnh `tenant_id_ctx`, `config/logging.py:25`); populate trong `bind_request_context()` từ bot resolve; emit `SET LOCAL app.workspace_id` ở CẢ `session_with_tenant` (`engine.py:143`) lẫn `_set_local_tenant` (`session.py:110`) — chỉ khi ctx có giá trị (None → không SET → policy giữ tenant-only semantics, backward-compat).
3. **(ops, gate riêng — rollback ADR)** Set `DATABASE_URL_APP` → DSN role `ragbot_app` (đã tồn tại, NOBYPASSRLS, non-owner — verify `rolcanlogin` + cấp password). Gỡ `RAGBOT_ALLOW_SUPERUSER_RUNTIME`. Rollback = trả lại env cũ (1 dòng), app code không đổi.
4. **(test CI, land TRƯỚC DSN flip — RED có chủ đích)** Leak-test integration KHÔNG green-vacuous:
   - **Role guard bắt buộc**: `SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname=current_user` → cả hai FALSE, nếu không `pytest.fail("leak-test ran as bypass role")`. Đây là chốt chống "test xanh vô nghĩa trên superuser".
   - Seed 2 tenant A/B (+ 2 workspace W1/W2 trong A): connect `ragbot_app`, `SET LOCAL app.tenant_id=A` → `SELECT count(*) FROM documents WHERE record_tenant_id=B` = **0**; thêm `SET LOCAL app.workspace_id=W1` → đếm bots W2 = **0**.
   - Negative control: cùng query chạy as `postgres` thấy CẢ HAI (chứng minh test nhạy với role).
   - Đánh dấu `@pytest.mark.rls_integration`, skip-with-reason khi không có `DATABASE_URL_APP` (CI gate AN TOÀN bật khi ops xong bước 3).
5. **GUC hardening (research finding 3)**: kiểm rằng không endpoint nào cho client SET GUC trực tiếp (raw SQL từ input) — grep guard + giữ nguyên nguyên tắc GUC chỉ set từ `session_with_tenant`/hook.

## 3. Alternatives rejected
| Alt | Lý do |
|---|---|
| Chỉ dựa app-WHERE (bỏ RLS) | Mất defence-in-depth; third-party tooling/psql tay xuyên thủng; research 3-0 chốt RLS = primary control |
| Per-tenant DB role | Vỡ pooling; research bác (Bytebase/Crunchy) |
| FORCE-only không đổi DSN | FORCE không trị superuser — vô dụng với DSN hiện tại (finding 1) |
| Đổi DSN trước, hook sau | Bare-session repos (bot=6/conv=3/doc=7 callsites) sẽ fail-CLOSED 0-row → app chết. Hook PHẢI land trước. |

## 4. Implementation plan Phase 4 (failing-test-first)
1. Unit test hook-attached: factory sau bootstrap có listener (introspect event registry) — RED.
2. Unit test `workspace_id_ctx` + SET LOCAL emit (fake session ghi câu lệnh) — RED.
3. Code mảnh 1+2 (~30 dòng) → GREEN.
4. Leak-test integration (mảnh 4) — file mới `tests/integration/test_rls_leak_2tenant.py`, skip khi thiếu `DATABASE_URL_APP`.
5. Ops checklist (mảnh 3) ghi `program/waves/W1-OPS-CHECKLIST.md`: cấp password ragbot_app → set env → restart → chạy leak-test → smoke 91Q.
6. Risk: HNSW JOIN-policy cost dưới ragbot_app (P2-C Q5) — đo `EXPLAIN ANALYZE` SAU DSN flip, trước khi đóng gate.

## 5. Gate metric
- Leak-test pass as `ragbot_app` (role-guard active) · cross-tenant=0 · cross-workspace=0 · negative-control sees both.
- Full pytest 0 regression; 91Q graded HALLU=0 + ≥85/91 sau DSN flip (CRAG/cache path đổi session role).
- Journal không còn `engine.app_dsn_superuser_fallback` WARNING.
