# [T1-Smartness] Happy-case input control — scope/format/styling gate cho data đầu vào

> Tier: **T1-Smartness** (data sạch → bot trả lời đúng) + T2 (ops/UX: gate validate).
> Ngày: 2026-06-22 · Branch: `expert-rag-squash-conflate-logcenter-20260619`
> Mindset (CLAUDE.md): rule #0 no-guess (mọi claim có evidence) · /plan trước code ·
> surgical · domain-neutral · **code NHẸ, KHÔNG LLM đọc data** (rẻ, deterministic) ·
> EVOLVE-not-rewrite.

---

## 0. Triết lý (chốt với user 2026-06-22)

> **Scope = TEMPLATE bắt buộc user theo. Sửa styling ở tầng DATA (user/normalizer).
> KHÔNG phình string ở tầng CODE.** Không cố parse mọi format bẩn (vô hạn, brittle) —
> định nghĩa template + gate validate → user sửa source về template.

Khớp SOTA "fix source first" (Databricks/Anyscale/unstructured) + Crestan-Pantel taxonomy.

---

## 1. Trạng thái HIỆN TẠI (evidence, đã DONE phiên này)

| Hạng mục | Trạng thái | Evidence |
|---|---|---|
| Spec quy chuẩn | ✅ | `docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md` |
| Template golden (3) | ✅ | `docs/dev/templates/` + contract test 4/4 |
| Scope SSoT (token-set = từ vựng template) | ✅ | `document_stats.py` (NAME/PRICE/CATEGORY tokens) — checker IMPORT, hết drift |
| Checker (code-only, no LLM) | ✅ | `scripts/check_happy_case.py` — chấm điểm + recommendation |
| Normalizer (fix styling tầng data) | ✅ | `scripts/normalize_to_happy_case.py` — data-preserving |
| Verifier L1→L7 per-layer | ✅ | `scripts/verify_happy_case_pipeline.py` |
| 9 file thật (3 bot) styled theo scope | ✅ | **L1→L7 ALL GREEN** · git-ignored |
| Out-of-scope code đã mark | ✅ | `document_stats.py` (OUT-OF-SCOPE DEFENSE comments) |
| Full suite | ✅ | **1042 passed** sau cleanup |

→ **Trả lời 3 câu hỏi của user (rule #0):**
1. *Data happy-case theo scope → work chưa?* ✅ **CÓ** — 9 file GREEN L1→L7.
2. *Upload → L1→L7 expert clean chưa?* 🟡 luồng kỹ-thuật chuẩn (canonical endpoint, byte-sniff, registry); **THIẾU gate validate** (Phase 1 dưới).
3. *7 tầng pass cho happy-case?* ✅ verifier xác nhận per-layer GREEN.

---

## 2. CÒN THIẾU — luồng "check data input" (gate). Đây là plan để LÀM.

### Phase 1 — API verify data input (cấp endpoint mới) — **CODE-ONLY, NO LLM**

- **Endpoint**: `POST /api/ragbot/documents/check` (header `X-Schema-Version`, KHÔNG `/v2`).
  - Input: GIỐNG `/create` (bytes/url + mime + 4-key identity) NHƯNG **KHÔNG ingest**.
  - Xử lý: fetch (nếu url) → `detect_parser` → `rows_to_structured_markdown`/parser →
    chạy **logic `check_happy_case`** (thuần Python: role-token, density, coverage,
    synonym/prose detect) → **report-card**.
  - Output JSON: `{verdict: HAPPY|MINOR|NON_HAPPY, dimensions: [...], coverage, recommendations: [...]}`.
  - **KHÔNG LLM** — chỉ regex/shape/token (rẻ ~0đ, deterministic, HALLU=0).
  - **Timeout cao hơn** `/create` (chạy parse + check full file) — knob `system_config.doc_check_timeout_s`.
- **Move checker logic** `scripts/check_happy_case.py` → `src/ragbot/application/services/happy_case_check.py`
  (Port + service, test-friendly) — script thành thin CLI gọi service.
- **Log phần chưa đạt**: structured event `doc_scope_check` (verdict + failed dimensions
  + bot identity) → ops thấy bot nào upload data lệch scope.
- **Test**: golden template → HAPPY; 9 file gốc → đúng verdict (4 HAPPY/2 MINOR/3 NON).

### Phase 2 — Wire gate vào `/create` (pre-ingest)

- `/create` gọi `happy_case_check` TRƯỚC khi queue worker:
  - `NON_HAPPY` → **422** + report-card (BE consumer sửa source rồi upload lại).
  - `MINOR` → ingest + warn (log).
  - `HAPPY` → ingest bình thường.
- **Per-bot opt-out** `plan_limits.scope_check_disabled` (governed, alembic) — bot legacy
  data bẩn không bị chặn đột ngột.
- **Test**: NON_HAPPY payload → 422 + card; HAPPY → 202 ingest.

### Phase 3 — Clean luồng upload + comment + SOLID

- `documents.py` + `ingest_*`: rà comment historical, mark out-of-scope, gom scope SSoT.
- Đảm bảo `detect_parser` registry = Port+Strategy (thêm format = thêm adapter).
- Verify: grep zero-hardcode + 4-key + no-version-ref + full suite xanh.

---

## 3. Done-criteria "luồng upload + happy-case" (đánh dấu DONE khi đủ)

- [ ] Phase 1: `/documents/check` API live, code-only, report-card, test pass.
- [ ] Phase 2: gate wired vào `/create`, 422 cho NON_HAPPY, opt-out governed.
- [ ] Phase 3: upload code clean, comment rõ, SSoT, full suite xanh.
- [x] Happy-case data → L1→L7 GREEN (đã chứng minh 9 file).
- [x] Checker/normalizer/verifier/template/spec/contract-test (toolkit).

→ **Khi 3 phase done = "luồng upload + xử lý format đầu vào ĐÃ CONTROL"** (gate + 7 tầng + happy-case pass-through).

---

## 4. Ràng buộc (CLAUDE.md)

- ✅ **Code-only check, 0 LLM** — rẻ, deterministic, HALLU=0 (user yêu cầu).
- ✅ Domain-neutral · scope token = template vocab (FIXED, không grow).
- ✅ Header-based versioning (`X-Schema-Version`), endpoint purpose-named.
- ✅ 4-key identity ở boundary · tenant-scoped log.
- ✅ /plan trước code · surgical · verify test sau mỗi phase.
- ✅ EVOLVE: thêm endpoint check + gate, KHÔNG đập luồng `/create` canonical.

---

## 5. Anti-pattern CẤM

- ❌ Dùng LLM/AI đọc data để validate (đắt, non-deterministic) — CHỈ code shape/token.
- ❌ Grow token-set trong code để "đỡ" data bẩn — sửa ở tầng DATA (normalize/user).
- ❌ Thêm endpoint upload song song — check là endpoint RIÊNG, ingest vẫn 1 canonical `/create`.
- ❌ Chặn cứng bot legacy không opt-out — phải governed `plan_limits`.
