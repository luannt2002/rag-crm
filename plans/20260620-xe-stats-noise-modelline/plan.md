# [T1-Smartness] xe stats-index noise cleanup + model-line granularity

**Trigger:** full eval 2026-06-20 → only fail = `chinh-sach-xe/q02` ("liệt kê các loại lốp" → bot trả 2 hãng, expect model `CITYTRAXX`). Đào gốc rễ phát hiện vấn đề LỚN hơn: xe stats-index ~49% noise.

**Tầng:** data/ingest extraction (KHÔNG phải sysprompt/LLM). HALLU=0 đã giữ — đây là COVERAGE gap.

---

## Root cause (evidence-backed)

1. **Noise pollution (chính):** xe `document_service_index` = 2112 entity, chỉ **790 branded-real / 1027 clear-noise (49%)**.
   - 100 URL-noise: cột image Google-Drive bị trích thành entity_name (`h3.google.com/...auditContext=forDisplay`).
   - 927 meta-noise: xe-3 là **bảng search-synonym** (`question: 155/80R13, <40 variant>,...`) — parser split theo dấu phẩy → `col[0]="question: 155/80R13"` (ngắn, lọt field-like guard) → entity rác. Tương tự `date1: 26`, `quantity: 29`.
   - Evidence: `psql document_service_index WHERE record_bot_id=8dfd4a6e...` ; chunk `xe-3. Đoạn đầu`.
   - **xe-specific**: spa(0 url-noise/353), thong-tu(0/984) sạch — CSV xe có cột image+synonym.

2. **q02 keyword name-bias:** `parse_list_query("liệt kê các loại lốp")` → keyword `"lốp"` → match 406 entity tên "Lốp xe..." nhưng **0/406 chứa CITYTRAXX** (named "108/106T CITYTRAXX H/T", không có chữ "lốp"). Enumerate-all fallback `list_all_entities` chỉ fire khi keyword→0; "lốp"→406≠0 → fallback bị defeat.
   - Verified: `list_all_entities` (created_at order, cap 100) → **67/100 entity đầu CHỨA CITYTRAXX** → routing-fix khả thi.

3. **Granularity:** entity SKU-level (2112), không có entity model-line. Recurring-token hypothesis VALID: LANDSPIDER 462, ROVELO 175, CITYTRAXX 136, WILDTRAXX 8, NEOTOUR 5 (trích domain-neutral được).

---

## Phases (incremental — đo sau Phase 1 rồi quyết Phase 2/3)

### Phase 1 — Noise filter tại extraction `_extract_entity_from_row` (document_stats.py) [MUST]
Domain-neutral rejects cho entity name (KHÔNG hardcode brand/tire):
- **URL-valued**: name match URL pattern (`https?://`, `drive.google`, `=w\d+-h\d+`, `auditContext`) → reject.
- **Metadata key-value prefix**: name match `^\w+:\s` (vd "question: ", "date1: ", "quantity: ") → reject. Catalog product name KHÔNG bao giờ lead bằng "label: ". (Giữ "Giá Combo: 1199000" — colon ở CUỐI, không phải prefix.)
- Đã có sẵn: bullet-lead, >120 char, >12 word.
- **Files:** `src/ragbot/shared/document_stats.py` (+ const nếu cần `shared/constants/`).
- **Tests (TDD, fail trước):** `tests/unit/test_document_stats_parser.py` — "question: 155/80R13"→reject; URL→reject; "CITYTRAXX H/T"→keep; "Lốp xe NEOTERRA 195/65R16"→keep; "Giá Combo: 1199000"→keep.

### Phase 2 — Re-ingest xe + đo lại [MUST, sau Phase 1]
- Re-ingest xe-1/2/3/4 **serial** (per-key Jina limiter, finalize-resilience đã có).
- Re-eval full 3 bot. **Gate:** xe noise <5%, spa/thong-tu vẫn 1.00, HALLU=0.
- Kiểm: sau noise-cleanup, `list_all` có sạch CITYTRAXX không → quyết Phase 3.

### Phase 3 — q02 enumerate fix [CHỌN 1 sau khi đo Phase 2]
- **3a (nhẹ, ưu tiên thử trước):** enumerate-WHOLE-category route → `list_all_entities` thay vì keyword name-bias. Discriminator domain-neutral cần test kỹ không regress spa "liệt kê dịch vụ X".
- **3b (deep, user chọn ban đầu):** model-line aggregation entity (recurring-token freq≥threshold → entity "CITYTRAXX"/"WILDTRAXX"). "list types" trả ~5-10 model-line sạch. Phức tạp hơn, config threshold.
- Quyết 3a vs 3b dựa trên kết quả Phase 2 (nếu noise-clean + list_all đã đủ surface CITYTRAXX → 3a; nếu cần grouping sạch → 3b).

---

## Risks + mitigations
- Re-ingest xe degrade → finalize-resilience (7a60c47) + serial + verify state='active' sau mỗi doc.
- Noise filter over-reject real product → TDD keep-cases + eval coverage gate (xe phải giữ ≥0.86, không tụt).
- Model-line threshold fragile → config-driven (`system_config`), không hardcode.

## DoD
- [ ] xe noise entity <5% (từ 49%)
- [ ] q02 COVERAGE pass (CITYTRAXX surface)
- [ ] spa + thong-tu vẫn 1.00 (zero regression)
- [ ] HALLU=0 toàn 42 câu
- [ ] unit tests pass; CLAUDE.md sacred 11/11 self-audit
- [ ] no app-inject/override; domain-neutral; no version-ref; zero-hardcode

## CLAUDE.md compliance
- Tầng fix = data/extraction (khớp gốc rễ) — KHÔNG patch sysprompt (tránh lỗi 2026-06-03).
- Domain-neutral: URL/key-value/token-freq, không brand/tire literal.
- Opus main cho mọi edit src/. TDD failing-test-first.
