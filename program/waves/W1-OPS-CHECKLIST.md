# W1 OPS CHECKLIST — các bước con-người (code đã land, chờ ops)

> Mỗi mục có verify command. Code-side W1 KHÔNG phụ thuộc các bước này để land
> (mọi thứ no-op an toàn cho tới khi ops bật) — nhưng GATE AN TOÀN chỉ đóng khi xong.

## 1. RLS DSN flip (ADR-W1-D3 piece 3) — P0
- [ ] Cấp password cho role `ragbot_app` (đã tồn tại, NOBYPASSRLS NOSUPERUSER, alembic 0073/0186):
      `ALTER ROLE ragbot_app WITH LOGIN PASSWORD '<strong>';` (chạy bởi DBA, KHÔNG commit password)
- [ ] Thêm `DATABASE_URL_APP=postgresql+asyncpg://ragbot_app:<pw>@<host>:5432/<db>` vào `.env`
- [ ] GỠ `RAGBOT_ALLOW_SUPERUSER_RUNTIME` khỏi `.env`
- [ ] Restart services → verify journal KHÔNG còn `engine.app_dsn_superuser_fallback`
- [ ] Chạy leak-test: `DATABASE_URL_APP=... pytest tests/integration/test_rls_leak_2tenant.py -v`
      (4 test: role-guard / cross-tenant=0 / workspace=0 / negative-control)
- [ ] Smoke graded 91Q: HALLU=0 + ≥85/91 (session role đổi → CRAG/cache path phải xanh)
- [ ] `EXPLAIN ANALYZE` hybrid query dưới `ragbot_app` (P2-C Q5 — JOIN-policy cost), lưu vào `program/eval/`

## 2. KEK cho API-key encryption (ADR-W1-KEY bước 0) — P0
- [ ] Sinh KEK: `openssl rand -base64 32` → thêm `RAGBOT_CONFIG_KEK=<value>` vào `.env`
      (KHÔNG commit; backup KEK vào secret manager — mất KEK = mất key đã mã hoá)
- [ ] Sau merge W1-KEY: `alembic upgrade head` (0196 encrypt-copy → verify → 0197 NULL-out)
- [ ] Verify: `SELECT count(*) FROM api_keys WHERE value_plain IS NOT NULL` = 0
- [ ] Watch 48h: structlog event `api_key_plaintext_read` = 0 lần → xoá dual-read ở kill-date

## 3. Alembic chain W1 (sau merge các worktree)
- [ ] Thứ tự: 0196 + 0197 (KEY) → 0198 (event_inbox, D8b). `alembic heads` phải ra đúng 1 head.

## 4. DR (D11 — W6 nhưng ghi sớm vì ops-side)
- [ ] WAL/PITR hoặc pgBackRest (RPO hiện tại ≈ 24h pg_dump nightly — DR doc hứa 5min)
- [ ] Off-host dump shipping
