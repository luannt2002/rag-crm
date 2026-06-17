# ADR-W1-KEY — Mã hoá provider API keys at-rest (xoá plaintext `api_keys.value_plain`)

> **Status**: ADR-DRAFT (chờ GATE 3 approve) · **Wave**: W1 STOP-THE-BLEED · **Tier**: [T2-CostPerf/AN-TOÀN] security P0
> **Nguồn gap**: `program/gaps/P2-J-ops-slo-dr-compliance.md` 🐛-KEY (HIGH) · **Plan row**: `program/EXPERT-PLAN.md` §2 W1 "API-key encrypt (P0)"
> **Stance**: EVOLVE — AES-GCM machinery ĐÃ TỒN TẠI (`env_secrets.py`), ADR này CHỈ nối dây + backfill. KHÔNG xây KMS/Vault mới (KMS = D11 dài hạn).
> **Redaction note**: file này KHÔNG chứa bất kỳ giá trị key/KEK nào — mọi DB evidence chỉ là boolean/count.

---

## 1. Context — vì sao plaintext = P0

### 1.1 Hiện trạng đo được (evidence 2026-06-10, READ-ONLY verify)

**SỰ THẬT (có evidence):**

1. **Bảng `api_keys` là kho key DUY NHẤT đang sống — và nó plaintext.**
   - Schema: `alembic/versions/20260512_0086_api_keys_hot_swap.py:42-43` tạo cả 2 cột `value_plain TEXT` + `value_encrypted TEXT`; docstring `:14-15` tự thú: *"`value_plain` is plain-text. AES-GCM at-rest encryption is a planned follow-up commit (column `value_encrypted` reserved for it)"*. Follow-up đó **chưa bao giờ ship** (alembic head hiện tại = `0195`, không migration nào đụng tới).
   - DB ground-truth (psql, chỉ boolean/count):
     ```
     SELECT provider_code, label, (value_plain IS NOT NULL), (value_encrypted IS NOT NULL), active, rotation_state
     FROM api_keys WHERE deleted_at IS NULL;
     →  zeroentropy|primary  |t|f|t|live
        zeroentropy|rerank   |t|f|t|live
        zeroentropy|secondary|t|f|t|live
     SELECT count(*) FROM api_keys WHERE value_plain IS NOT NULL;  → 3
     ```
   - Hai kho key legacy **đều rỗng**: `SELECT count(*) FROM ai_keys WHERE api_key_encrypted IS NOT NULL → 0`; `SELECT count(*) FROM ai_providers WHERE api_key_encrypted IS NOT NULL → 0`. ⇒ 100% provider credentials sản xuất nằm cleartext trong `api_keys.value_plain`.

2. **Chuỗi khuếch đại P2-C + P2-J:**
   - App connect DB bằng **superuser**: `SELECT current_user, usesuper FROM pg_user WHERE usename = current_user → postgres|t` (khớp P2-C RLS-1: chưa có role `ragbot_app`). Bất kỳ SQL-injection / compromised dependency nào đọc được DB = đọc được key thẳng.
   - **pg_dump backup mang cleartext key đi xa hơn DB**: `scripts/backup_db.sh` (cron `0 2 * * *`) dump toàn bộ; comment tại `backup_db.sh:39` *"backups contain PII / secrets in encrypted form"* — claim này **SAI** cho 3 rows trên. Ai cầm được file backup = cầm được key ZeroEntropy (embedding + rerank của toàn platform).

3. **Máy mã hoá ĐÃ CÓ SẴN và ĐANG được dùng ở path legacy:**
   - `src/ragbot/infrastructure/security/env_secrets.py:15-50` — `EnvSecretsAdapter`: AES-256-GCM, envelope `base64( nonce[12] || ciphertext+tag )` (docstring `:4`), KEK từ env (xem §2d), `encrypt()` static `:41-50`, `resolve()` async `:24-38`, **fail-loud RuntimeError khi KEK thiếu** `:29-33` + `:44-45`.
   - Path legacy dùng nó thật: `application/services/ai_config_service.py:344` (rotate_key → encrypt), `:404-405` (add_key → encrypt vào `ai_keys.api_key_encrypted`), `:469-471` (verify → decrypt). Port contract: `application/ports/secrets_port.py` (`SecretsPort.resolve`). DI singleton đã đăng ký: `bootstrap.py:458` `secrets_port = providers.Singleton(EnvSecretsAdapter)`.

4. **Hot-swap path (mới hơn, đang sống) bỏ qua máy mã hoá:**
   - **Read**: `application/services/provider_key_resolver.py:89` — `SELECT value_plain FROM api_keys ...`. Thêm exposure phụ: `:118` `setex` cache **plaintext key vào Redis** (TTL 30s, `_CACHE_TTL_S` `:29`).
   - **Write** (⚠ đính chính so với đề bài: đường ghi hot-swap KHÔNG nằm ở `admin_ai.py` mà ở `test_chat.py`): `interfaces/http/routes/test_chat.py:4218` `PUT /admin/api-keys/{provider_code}` → `:4244` `UPDATE api_keys SET value_plain = :v` + `:4261` `INSERT ... value_plain`. List endpoint `:4180` tính fingerprint **từ `value_plain`** tại `:4205-4206`. (`admin_ai.py:158/:182` rotate-key/add-key là path legacy `ai_providers`/`ai_keys` — đã encrypted, ngoài scope.)
   - `api_keys` **không có ORM model** (grep `class ApiKey|api_keys` trong `infrastructure/db/models.py` = 0 hit) — toàn raw SQL → backfill migration phải self-contained.

5. **Defect liền kề (cùng contract decrypt, phát hiện khi verify):** `shared/api_key_pool.py:318` gọi `EnvSecretsAdapter.resolve(ref=None, encrypted=row[0])` — `resolve` là **async instance method** nhưng bị gọi unbound (thiếu `self`) và không `await` → `TypeError` mỗi row, bị nuốt bởi `except Exception` `:319-324` (`db_pool_decrypt_failed`) → DB-key pool path của `ai_keys` **chết silent** từ ngày ship. Fix 2-dòng, gộp vào W1 (xem §6 bước 7).

**GIẢ THUYẾT = 0.** Mọi câu trên có file:line hoặc query result.

### 1.2 Vì sao xếp P0
Cleartext-key × superuser-DSN × nightly-pg_dump = một file backup rò rỉ ⇒ toàn bộ provider credentials lộ, kéo theo chi phí token bị abuse + phải rotate khẩn cấp toàn platform. Charter trục AN TOÀN + EXPERT-PLAN W1 gate "0 plaintext key" chốt đây là code-fix đầu tiên.

---

## 2. Decision

### (a) Write-path — mọi key mới ghi `value_encrypted`, NGỪNG ghi `value_plain`

- Sửa handler `PUT /admin/api-keys/{provider_code}` (`test_chat.py:4218-4288`):
  - Encrypt trước khi chạm DB: `encrypted = EnvSecretsAdapter.encrypt(req.value)` (qua DI `_container(request).secrets_port()` — KHÔNG import adapter trực tiếp trong route; thêm method `encrypt` vào `SecretsPort` Protocol tại `application/ports/secrets_port.py` để giữ Port+DI, Quality-Gate #3).
  - `UPDATE ... SET value_encrypted = :v, value_plain = NULL, ...` (`:4244`) và `INSERT ... (.., value_encrypted, ..)` (`:4261`) — cột `value_plain` **không còn xuất hiện trong bất kỳ câu SQL ghi nào**.
  - **Fingerprint tính từ plaintext TẠI THỜI ĐIỂM GHI** (sha256[:12] như hiện tại `:4280`) và **lưu vào `metadata_json['fingerprint']`** (cột JSONB có sẵn từ 0086:47). List endpoint (`:4205-4206`) đổi sang đọc `metadata_json->>'fingerprint'`; fallback trong migration window: row chưa có fingerprint → decrypt-rồi-hash (admin-only path, tần suất thấp). ⇒ list endpoint hết phụ thuộc `value_plain` và không cần decrypt thường xuyên.
  - KEK thiếu → `EnvSecretsAdapter.encrypt` raise RuntimeError (`env_secrets.py:44-45`) → endpoint trả 500 fail-loud. **Đúng chủ đích**: từ giờ KHÔNG còn đường ghi key không mã hoá; graceful-degradation KHÔNG áp cho secret-write (client-bug class → fail loud).

### (b) Read-path — resolver encrypted-first, dual-read có kill-date

- `ProviderKeyResolver` (`provider_key_resolver.py`):
  - Constructor nhận thêm `secrets: SecretsPort` (DI tại `bootstrap.py:280-284`, inject singleton `secrets_port` đã có ở `:458`).
  - SQL `:89` → `SELECT value_encrypted, value_plain FROM api_keys ...` (giữ nguyên predicate active/live/not-deleted/ORDER BY).
  - Ưu tiên: `value_encrypted` → `await secrets.resolve(None, value_encrypted)`; nếu NULL → fallback `value_plain` **kèm structlog warning `api_key_plaintext_read`** (provider_code + label, KHÔNG log value) — đếm event này = đo tiến độ backfill, về 0 trước kill-date.
  - **Redis cache lưu CIPHERTEXT, decrypt sau cache-hit** (sửa `:118` setex + `:68-75` nhánh đọc): plaintext biến mất khỏi Redis at-rest luôn. AES-GCM decrypt cỡ µs — không ảnh hưởng p95. Negative-cache sentinel `""` giữ nguyên. Cache-hit mà decrypt fail (entry plaintext cũ còn sót ≤30s sau deploy, hoặc KEK xoay) → coi như cache-miss, đọc lại DB; TTL 30s (`:29`) tự làm sạch transition, không cần đổi key template.
  - Env fallback `_ENV_FALLBACK` (`:34-40`) giữ nguyên (dev/bootstrap path, ngoài scope).
- **Kill-date dual-read**: sau khi Migration B (§c) áp + verify count=0 + soak 48h với `api_key_plaintext_read` = 0 event trong journal → commit cuối W1 xoá `value_plain` khỏi SELECT của resolver và khỏi list-endpoint fallback. Gate W1 close = code không còn reference `value_plain` ngoài alembic history (grep §7).

### (c) Backfill — 2 alembic migrations (no-psql-hotfix, rollback được)

Đánh số theo head thực tế lúc implement (hiện `0195` → dự kiến `0196`/`0197`; coder lấy `alembic heads` lúc đó, tránh collision như sự cố 0078).

- **Migration A — `encrypt_api_keys_backfill`**: với mọi row `WHERE value_plain IS NOT NULL AND value_encrypted IS NULL` → `value_encrypted = encrypt(value_plain)`, đồng thời `metadata_json = metadata_json || {'fingerprint': sha256(value_plain)[:12]}`. **GIỮ `value_plain`** (chưa xoá — để rollback tức thời). Code AES-GCM **inline self-contained trong migration** (~15 dòng AESGCM + os.urandom(12) nonce, chuẩn alembic không import `src/`), envelope **PHẢI khớp** `env_secrets.py:4` `base64(nonce[12] || ct+tag)`; KEK đọc cùng env var (§d), thiếu KEK → raise (migration fail-loud, không chạy mù). `downgrade()` = `UPDATE api_keys SET value_encrypted = NULL WHERE value_plain IS NOT NULL` (plain còn nguyên nên thuận nghịch hoàn toàn).
- **Migration B — `null_out_api_keys_value_plain`** (revision riêng, áp SAU soak window §b): `UPDATE api_keys SET value_plain = NULL WHERE value_encrypted IS NOT NULL`. `downgrade()` = decrypt `value_encrypted` bằng KEK ghi ngược lại `value_plain` (cũng inline AESGCM) — **thuận nghịch thật sự**, không phải no-op giả.
- 2-step thay vì 1 vì: A fail giữa chừng → plain còn nguyên, hệ chạy tiếp; B chỉ chạy khi A đã verified; rollback từng nấc độc lập. KHÔNG drop cột `value_plain` trong W1 (DDL drop = bước 3 tuỳ chọn ở wave sau, sau ≥1 release ổn định).
- Verify bắt buộc sau B: `SELECT count(*) FROM api_keys WHERE value_plain IS NOT NULL` = **0** (gate metric §8).

### (d) KEK — env `RAGBOT_CONFIG_KEK` (đính chính tên + prerequisite ops)

- **Tên thật đã verify**: `RAGBOT_CONFIG_KEK` (`env_secrets.py:16,:21,:32,:42`) — KHÔNG phải `RAGBOT_SECRETS_KEK` như đề bài phỏng đoán; grep `RAGBOT_SECRETS_KEK` toàn repo = 0 hit. ADR dùng tên có sẵn, không đổi (đổi tên = touch path legacy đang chạy, vi phạm surgical).
- **⚠ PREREQUISITE P0 — KEK hiện CHƯA set**: `grep -ci KEK /var/www/html/ragbot/.env → 0`. Path legacy "sống" được vì 2 bảng legacy rỗng (encrypt chỉ chạy khi admin rotate — sẽ RuntimeError nếu gọi hôm nay). **Ops bước 0 trước mọi deploy/backfill**: sinh KEK base64 32-byte (lệnh mẫu đã có sẵn tại `scripts/db/seed_ai_config.py:7`: `python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"`), set vào `.env` + mọi systemd unit (api + chat_worker + document_worker), **backup KEK ngoài máy chủ** (mất KEK = mất key, chỉ còn đường re-enter qua admin PUT). KHÔNG commit KEK vào repo (tenant-secret rule).
- **KEK rotation** = ops-runbook ghi nhận (decrypt-all → re-encrypt-all script, chưa cần trong W1 vì 3 rows). **KMS/Vault thật = D11/W6** (decision register `00-DECISION-REGISTER.md:25`).
- `.env` trên host = file chứa cả KEK lẫn DSN → threat model §3 nói rõ giới hạn này.

---

## 3. Threat model (ngắn, trung thực)

| Threat | Trước | Sau ADR | Ghi chú |
|---|---|---|---|
| `pg_dump` backup file bị lộ / copy đi nơi khác | ❌ key cleartext trong dump | ✅ chỉ ciphertext (KEK không nằm trong DB) | đúng claim `backup_db.sh:39` trở lại thành thật |
| psql read bởi role DB bất kỳ / SQL-injection SELECT | ❌ đọc thẳng key | ✅ chỉ ciphertext | độc lập với fix RLS/`ragbot_app` (D3) — defence-in-depth |
| Redis dump / MONITOR trên cache key `ragbot:apikey:*` | ❌ plaintext 30s TTL | ✅ ciphertext (decrypt sau cache-hit, §2b) | |
| Admin list endpoint | ✅ đã chỉ trả fingerprint (`test_chat.py:4206`) | ✅ giữ nguyên | |
| **Process memory** (worker đang chạy) | ❌ | ❌ **KHÔNG bảo vệ** | key phải plaintext trong RAM để gọi provider — chấp nhận |
| **KEK lộ** (`.env` readable trên host, hoặc env của systemd) | — | ❌ **KHÔNG bảo vệ** — KEK + ciphertext cùng host = giải mã được | mitigation: file perms `.env` 600 + KMS D11 |
| Operator có quyền alembic/deploy | ❌ | ❌ ngoài scope (trust boundary = host) | |

Tóm: ADR bảo vệ **data-at-rest và data-in-backup**, không bảo vệ host-compromise. Đó là đúng kích thước cho W1; host-level = D11.

---

## 4. Alternatives rejected

1. **pgcrypto in-DB (`pgp_sym_encrypt`)** — REJECT: key/KEK xuất hiện trong câu SQL → lộ qua `pg_stat_statements`, server log, `log_min_duration_statement`; và KEK phải gửi vào DB ⇒ DB-dump threat (threat chính của ADR) không được giải quyết trọn.
2. **Vault / cloud-KMS ngay bây giờ** — REJECT cho W1: thêm 1 runtime dependency + ops surface mới cho đúng 3 rows key; vi phạm Simplicity-First + EVOLVE (machinery nội bộ đã có, chỉ chưa nối). Đã có chỗ trong register: D11/W6.
3. **Hash-only (như password)** — REJECT: provider key phải **khôi phục được plaintext** để đặt vào header HTTP gọi ZeroEntropy/OpenAI — đây là credential outbound, không phải credential verify-inbound. Hash là sai công cụ.
4. **Envelope KEK→DEK per-row** — REJECT cho W1: over-engineering ở quy mô 3 rows / rotation thấp; AES-GCM trực tiếp + nonce random 12-byte per-encrypt (đã đúng trong `env_secrets.py:48`) là đủ; nonce-collision risk ở volume này không đáng kể. Nâng cấp envelope = một phần D11 nếu KMS vào.
5. **Đổi sang bảng `ai_keys` (đã encrypted) thay vì sửa `api_keys`** — REJECT: `ai_keys` thiếu semantics hot-swap (`label`, `rotation_state` live/cooldown/revoked, resolver+Redis 30s đã wired vào `api_keys`); migrate consumer = rewrite, vi phạm EVOLVE. Hợp nhất 2 kho key = câu hỏi cho D11, không phải W1.

---

## 5. Sacred-rule / CLAUDE.md compliance tự-audit

- **Zero-hardcode**: không số magic mới — TTL 30s (`_CACHE_TTL_S`) giữ nguyên; nonce 12-byte là thuộc tính envelope đã tồn tại; fingerprint length 12 đã tồn tại (`test_chat.py:4206`) — coder lift `12` thành constant chung khi sửa (nó xuất hiện ≥3 chỗ). ✅
- **No-psql-hotfix**: backfill 100% qua alembic tracked-in-git, KHÔNG psql UPDATE tay. ✅
- **Strategy+DI**: encrypt/decrypt qua `SecretsPort` injected, không import adapter trong route/resolver. ✅
- **No-version-ref**: tên migration/hàm theo PURPOSE (`encrypt_api_keys_backfill`), không `_v2`. ✅
- **Domain-neutral**: không brand literal mới trong code (provider codes là DB data + `_ENV_FALLBACK` đã tồn tại). ✅
- **Secret-redaction**: ADR + log events không in value; structlog event chỉ provider_code/label. ✅
- **Sacred #10 (no app-inject/override answer)**: N/A — không chạm answer path. ✅

---

## 6. Implementation plan Phase 4 (failing-test-first, thứ tự bắt buộc)

> Coder đọc xong mục này phải implement được không hỏi lại. Mọi test viết TRƯỚC, chứng kiến FAIL, rồi mới code.

**Bước 0 — ops prerequisite (chặn toàn bộ pipeline nếu thiếu)**
Set `RAGBOT_CONFIG_KEK` (lệnh sinh ở §2d) vào `.env` + systemd units; verify: `python3 -c` encrypt/decrypt roundtrip ad-hoc OK. Ghi vào ops-runbook: KEK backup off-host.

**Bước 1 — failing tests (file mới `tests/unit/test_provider_key_encryption.py`)**
1. `test_env_secrets_roundtrip` — monkeypatch env KEK (random 32B b64) → `EnvSecretsAdapter.encrypt(s)` → `await resolve(None, enc) == s`; assert envelope: `len(base64.b64decode(enc)) == 12 + len(s) + 16`. (Hiện CHƯA có test trực tiếp cho `env_secrets.py` — grep tests = chỉ `test_ai_config_service_add_key.py` đụng gián tiếp.)
2. `test_resolver_prefers_encrypted` — fake session trả row `(value_encrypted=enc, value_plain="SHOULD-NOT-BE-USED")` → `get()` trả plaintext decrypt từ `enc`, KHÔNG trả cột plain.
3. `test_resolver_dual_read_plain_fallback_warns` — row `(None, plain)` → trả plain + structlog `api_key_plaintext_read` emitted (caplog assert event name, assert value KHÔNG xuất hiện trong log record).
4. `test_resolver_caches_ciphertext_not_plaintext` — fake redis: assert tham số `setex` == ciphertext (≠ plaintext); cache-hit ciphertext → get() vẫn trả plaintext đúng.
5. `test_resolver_cache_stale_plaintext_treated_as_miss` — cache-hit chứa chuỗi không decrypt được → resolver rơi xuống DB path, không raise.
6. `test_write_path_writes_encrypted_only` — gọi handler upsert (extract logic ra service-func nếu cần testability) với fake session: captured SQL params có `value_encrypted`, KHÔNG có `value_plain` non-NULL; `metadata_json` chứa fingerprint sha256[:12] của plaintext.
7. `test_write_path_fail_loud_without_kek` — unset KEK env → upsert raise (HTTP 500 / RuntimeError), KHÔNG ghi row plaintext.
8. `test_backfill_encrypt_function` — unit test hàm `_encrypt_row`/inline-AESGCM của Migration A trên giá trị mẫu: decrypt bằng `EnvSecretsAdapter.resolve` ra lại bản gốc (chứng minh 2 envelope tương thích).
Tất cả 8 FAIL trước khi code (3/4/6/7 fail vì code chưa tồn tại — đó là failing đúng nghĩa TDD).

**Bước 2 — code surgical (thứ tự)**
a. `application/ports/secrets_port.py`: thêm `def encrypt(self, plain: str) -> str: ...` vào Protocol (EnvSecretsAdapter đã có sẵn — chỉ port khai báo thêm; chỉnh signature static→instance-compatible nếu cần, giữ backward-compat 3 callsite `ai_config_service.py:344,:405` + pool).
b. `provider_key_resolver.py`: constructor `+ secrets`, SELECT 2 cột, decrypt-first + plain-fallback + warning event, cache ciphertext (§2b).
c. `bootstrap.py:280-284`: inject `secrets=secrets_port`.
d. `test_chat.py` upsert (`:4240-4266`): encrypt + NULL-out plain + fingerprint vào metadata_json; list (`:4204-4206`): đọc `metadata_json->>'fingerprint'`, fallback decrypt.
e. Migration A (head+1) theo §2c — chạy `alembic upgrade head` trên dev → verify 3 rows có `value_encrypted IS NOT NULL` và resolver trả key đúng (smoke: 1 chat request dùng ZE embedding thành công).
f. Soak ≥48h production: journal `api_key_plaintext_read` count = 0 (vì resolver đã prefer encrypted) + 0 lỗi `provider_key_resolver_db_failed` mới.
g. Migration B (head+2) → verify query count=0 (§8).
h. Kill-date commit: xoá `value_plain` khỏi SELECT resolver + list-fallback; chạy grep guard §7.

**Bước 3 — adjacent fix (gộp W1, 2 dòng + 1 test)**
`shared/api_key_pool.py:318`: `EnvSecretsAdapter.resolve(ref=None, encrypted=row[0])` → `await EnvSecretsAdapter().resolve(None, row[0])` (hàm bao `_load_db_keys` đã async). Test: fake row encrypted → pool trả plaintext (hiện tại FAIL vì TypeError bị nuốt — bằng chứng dead-path).

**Out-of-scope W1 (ghi nhận, không làm)**: DDL drop cột `value_plain`; hợp nhất `api_keys`/`ai_keys`/`ai_providers` 3 kho key; KMS/Vault; KEK-rotation script — tất cả → D11.

---

## 7. Grep guards (pre-commit, sau kill-date = 0 hit)

```bash
# Sau kill-date: không còn code đọc/ghi value_plain ngoài alembic history
grep -rn "value_plain" src/ scripts/ tests/ | grep -v __pycache__ | grep -v "alembic/versions/"
# Không route/resolver import adapter trực tiếp (DI port only)
grep -rn "from ragbot.infrastructure.security" src/ragbot/interfaces/ src/ragbot/application/services/provider_key_resolver.py
# Không log key value (event chỉ metadata)
grep -rn "api_key_plaintext_read" src/ | grep -vE "provider_code|label" || true  # manual review
```

## 8. Gate metrics (W1 close — đo, không đoán)

| Metric | Target | Verify |
|---|---|---|
| Plaintext rows | **0** | `SELECT count(*) FROM api_keys WHERE value_plain IS NOT NULL` = 0 |
| Decrypt roundtrip + dual-read tests | 8/8 xanh | pytest output đính kèm |
| Plaintext trong Redis | 0 | `redis-cli --scan --pattern 'ragbot:apikey:*'` → GET từng key = ciphertext b64 hoặc `""` |
| `api_key_plaintext_read` events sau Migration B | 0 / 48h | journalctl structlog |
| Regression | 0 (suite hiện hành xanh) + HALLU=0 hold | pytest + load-test gate W1 chung |
| Smoke chat (ZE key resolve qua encrypted path) | 1 request OK, chunks > 0 | curl + request_logs |

## 9. Rollback plan

- **Code rollback**: dual-read nghĩa là binary cũ (đọc `value_plain`) vẫn chạy chừng nào Migration B CHƯA áp → revert git ở bước e/f là vô hại.
- **Migration A downgrade**: NULL-out `value_encrypted` (plain còn nguyên) — tức thời.
- **Migration B downgrade**: decrypt `value_encrypted` → ghi lại `value_plain` (cần KEK) — thuận nghịch thật.
- **Mất KEK sau Migration B**: key không khôi phục từ DB → operator re-enter qua `PUT /admin/api-keys/{code}` (hot-swap có sẵn, hiệu lực ≤30s/TTL). Đây là lý do KEK backup off-host là prerequisite bước 0.
- **Trigger rollback**: bất kỳ provider-call fail vì key-resolve sau deploy (CB OPEN trên embedding/rerank) → downgrade B → A → revert code, post-mortem trước khi thử lại.

---

*Evidence thu thập READ-ONLY 2026-06-10. 0 file src/alembic/tests bị sửa. DB queries chỉ boolean/count — không giá trị secret nào rời DB.*
