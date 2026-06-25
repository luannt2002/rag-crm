# [T1-Smartness] Plan — Input-control hardening (silent-drop + multi-locale + NORMALIZE-to-IR)

> **Status**: PROPOSAL (chưa code — phiên này READ-ONLY). Cần user approve trước khi implement.
> **Ngày**: 2026-06-25 (cập nhật thêm 4 gap từ `z-luannt-system-design.txt`) · **Branch hiện tại**: `fix-260623-ingest-expert`
> **Nguồn**: design doc `docs/dev/INPUT_DATA_CONTROL_FLOW_DESIGN.md` §4/§6 + [[INPUT_CONTROL_ROOT_CAUSE_3PHILOSOPHIES_20260625]] + tldw ADAPT #1/#2/#3 + `z-luannt-system-design.txt`.
> **Tier**: T1 (input-control → coverage). **Stance**: EVOLVE, không REWRITE. Domain-neutral, zero-hardcode, DB-seeded, HALLU=0.

## QUYẾT ĐỊNH KIẾN TRÚC (chốt cho plan này — ADR-worthy, xem mục 6)

**CONSTRAIN vs ABSORB → chọn NORMALIZE-to-IR.** KHÔNG ném sở-thú-parser/OCR kiểu tldw (unmaintainable, 0 role-semantic). KHÔNG chỉ reject (quá hẹp). Giữ mô hình **mọi format → 1 normalizer riêng → 1 Unified IR (structured-markdown) → checker gate**. Ragbot ĐÃ ở mô hình này ~70% (registry đã có `excel/docx/google_sheets/pdf/kreuzberg/markdown/vlm` — verified). Việc cần = **đào sâu NORMALIZER + nối CHECKER**, KHÔNG thêm format.

---

## 0. Trạng thái THẬT (verified 2026-06-25 — KHÔNG làm lại)

| Đã DONE (verified — KHÔNG làm lại) | Evidence |
|---|---|
| aliases first-class role | `document_stats.py:151` `_ALIASES_COL_TOKENS` |
| entity_synonyms column + trigram | alembic `20260624_stats_index_entity_synonyms.py` |
| checker phát hiện cột unassigned | `check_happy_case.py:86` `_unassigned_header_cols` |
| dedicated parser per format | registry: `excel_openpyxl/docx/google_sheets/pdf/kreuzberg/markdown/vlm` |
| sync/async split + Redis Streams + 5 worker | `embedded_workers.py` (2-action) |
| size guard fail-fast REJECT | `ingest_core.py:376-378` (`max_ingest_content_chars`) |
| cliff filter · corpus_version cache-bust · decompose · HDT breadcrumb | (case study 1-4 trong system-design = đã có) |

| Gap THẬT còn lại (scope plan này) | Evidence |
|---|---|
| **G1** role-vocab exact-match (không fuzzy/substring) | `document_stats.py:172-179, 307-328` |
| **G2** KHÔNG multi-locale (vi-only + vài EN lẻ) | `document_stats.py` 0 locale awareness |
| **G3** U5 enrich SKIP cho table row → chunk bảng không breadcrumb | `ingest_stages_enrich.py:190` `should_skip_row_enrich` |
| **G4** checker offline-only, ingest KHÔNG surface warning | verified: `check_happy_case` chỉ self-import, chưa wired |
| **G-OOM** chỉ REJECT file lớn, **không map-reduce SPLIT** → 224KB→2643 chunk OOM | `ingest_core.py:376` reject-only; README known gap |
| **G-Linearize** row-as-chunk có nhưng **nhãn cột drop** (không `"Giá=700k, Tồn=404"`) → Nhóm B HALLU | `document_stats.py` synthetic chunk col không nhãn |
| **G-Wire** checker chưa thành admission-controller middleware | offline script |
| **G-Batch** Jina embed batch-size cap (≤32/req) — *cần verify* | README 224KB→27 batch |

---

## 1. Mục tiêu đo được (acceptance — phải có số THẬT)

- **G1+G2**: sheet header EN/Spanish/Thai + vi-variant (`Tên hàng`, `Treatment|Rate|Tier`) → role gán đúng ≥ 95% (test fixture đa-locale).
- **G3**: chunk table-row mang breadcrumb `# Doc > ## Section` → retrieval của câu warranty/section-scoped lift (đo bằng load-test coverage delta, không đoán %).
- **G4**: ingest 1 sheet có cột lạ → response/log emit **WARNING liệt kê cột bị demote** (hết silent "success").
- **Sacred**: HALLU=0 giữ nguyên; 0 brand literal; defaults trong `shared/constants`; role-map **DB-seeded** (không hardcode frozenset mới); narrow except.

---

## 2. Phases (TDD: failing test TRƯỚC)

### Phase 0 — Feedback loop (BẮT BUỘC trước mọi code)
- **Test reproduce** (`tests/unit/test_column_role_multilocale.py`):
  - `Tên hàng|Giá|Tồn` → assert role(name)=col0, role(price)=col1, **role(qty)=col2** (hiện FAIL: qty không có role).
  - `Treatment|Rate|Category` (EN) → assert 3 role gán đúng (hiện FAIL: 0 role).
  - table-row chunk → assert chunk_context chứa breadcrumb section (hiện FAIL: empty cho table).
- **Reproduce harness**: chạy `check_happy_case.py` trên 1 fixture đa-locale → confirm cột bị demote.
- Gate: tất cả test trên **FAIL** trước khi sang Phase 1.

### Phase 1 — Multi-locale + fuzzy column-role (G1+G2)
- **Schema**: bảng/seed `column_role_tokens(locale, role, token)` (alembic mới) — tokens hiện tại migrate thành `locale='vi'`; thêm `locale='en'` seed. Domain-neutral, tracked alembic (KHÔNG psql).
- **Resolver**: `document_stats._column_roles()` đọc token theo locale (detect từ doc-profile) + **substring/fuzzy match** (normalized contains, ví dụ `ten hang` contains `ten`), tie-break theo độ dài match. Giữ exact-match ưu tiên 1.
- **Locale detect**: thêm char-range detector nhẹ (pattern từ tldw `multilingual.py:75-116`, **không** pull `langdetect`) → trả locale cho resolver. Land ở doc-profile.
- **Money parser** (G2 phụ): nới `_MONEY_UNIT_RE` cho `$`/`€` prefix + `1,234.56` decimal-comma-Western (config-driven, `tabular_markdown.py:40-69`).
- Files: `shared/document_stats.py`, `shared/tabular_markdown.py`, doc-profile, `shared/constants` (default locale), alembic seed, `scripts/check_happy_case.py` (sync token source — đã import chung vocab).
- Test: Phase-0 EN/vi-variant test PASS.

### Phase 2 — U5 breadcrumb cho table rows (G3) — ADAPT tldw #1
- **Helper thuần** (`shared/chunking/breadcrumb.py`): port *thuật toán level-stack* từ `structure_aware.py:711-722` (KHÔNG port code/loguru/except) → từ heading ancestry sinh chuỗi `# Doc > ## Section`. Deterministic = HALLU-safe.
- **Wire**: trong U4/U5 table-row path, set `chunk_context = breadcrumb` cho table chunk (thay vì skip enrich hoàn toàn). `document_stats.py:617-620` đã bắt `## heading` → mở rộng thành full ancestry.
- Files: `shared/chunking/` (helper mới), `ingest_stages.py`/`ingest_stages_enrich.py` (wire), `shared/constants` (toggle nếu cần).
- Test: table-row chunk có breadcrumb; load-test coverage delta đo sau.

### Phase 3 — Surface-loud ingest warning (G4)
- **Verify trước** (rule #0): grep xem ingest có gọi checker/emit unassigned-column event không. Nếu KHÔNG → wire `_unassigned_header_cols` logic vào ingest-finalize → emit structured event + đưa vào ingest result/document state metadata để owner thấy "N cột bị demote: [...]".
- KHÔNG fail ingest (graceful) — chỉ WARN. Hết silent "success".
- Files: `ingest_stages_final.py` hoặc ingest result DTO, `check_happy_case` (tách hàm reusable).
- Test: ingest sheet cột lạ → assert warning event emitted + liệt kê đúng cột.

### Phase 4 — (T2 defer) Tokenizer registry theo locale — ADAPT tldw #3
- Chỉ làm khi có bot non-vi thật. Thêm `infrastructure/tokenizer/registry.py` keyed by locale + adapter CJK/Thai behind import-fallback (Null Object). Port đã có (`tokenizer_port.py`).
- Defer cho tới khi T1 G1-G4 verified bằng load-test.

---

## 3. Verify cuối (biến GIẢ THUYẾT → SỰ THẬT)
1. Normalize 4 sheet xe về 1 catalog WIDE (source-fix, theo happy-case) + thêm cột Tồn → re-ingest.
2. Chạy load-test parallel (asyncio.gather sem=8, bypass_cache) trên 40 câu test → đo **Coverage** trước/sau.
3. Check chunk từng câu fail còn lại (ingest→retrieve→topK→prompt→answer).
4. Report số THẬT, KHÔNG tuyên bố "fixed" tới khi có load-test output.

---

## 2b. Phases BỔ SUNG — input-control hardening (4 gap từ system-design)

### Phase 5 — G-Linearize: row linearization CÓ NHÃN cột (diệt Nhóm B HALLU)
- NORMALIZER table-row emit câu có nhãn: `"dòng 5 | Tên=X | Giá=700000 | Tồn=404 | Ngày về=28/11"` (nhãn từ header role, kể cả cột `Tồn/Ngày về` sau khi G2 mở role).
- Synthetic stats chunk + table chunk đều mang nhãn → LLM hết vớ nhầm số.
- Files: `shared/document_stats.py` (entity→text), `shared/tabular_markdown.py`. Test: assert số đi kèm nhãn cột.

### Phase 6 — G-OOM: Map-Reduce sub-document SPLIT (chống OOM, không chỉ reject)
- Khi file > ngưỡng (config, KHÔNG hardcode): chia theo Sheet/Chương/khối N-ký-tự thành sub-document, mỗi cái 1 Task Redis Stream → 5 worker xử song song.
- KHÔNG đập size-guard reject hiện có (`ingest_core.py:376`) — thêm nhánh SPLIT trước khi reject cứng.
- Files: `ingest_core.py` / `ingest_stages.py`, `shared/constants` (sub_doc_char_limit). Test: file lớn → N sub-doc, RAM phẳng, không OOM.

### Phase 7 — G-Wire: checker = data-quality ADVISORY (KHÔNG chặn upload) — chốt ADR-0005
- **Stance (ADR-0005)**: checker là **advisory, KHÔNG phải admission-controller chặn**. FORMAT không bao
  giờ reject; chỉ **báo owner** cột bị demote / 4 sheet không join / thiếu cột Tồn → owner biết VÌ SAO bot
  không trả được, KHÔNG bị bắt sửa format. Reject CỨNG chỉ cho an-toàn-hệ-thống (vượt ngưỡng OOM) — và kể
  cả đó cũng map-reduce SPLIT (G-OOM) trước khi reject.
- Wire logic `_unassigned_header_cols` + score vào ingest finalize → emit structured `ingest_data_quality`
  event (liệt kê cột demote + fragment chưa join) → đưa vào ingest result/document metadata để owner thấy.
  KHÔNG flip `FAILED` vì format/cột lạ; chỉ surface advisory (state vẫn active, coverage limited).
- Async, KHÔNG block event-loop (ingest path đã async — gọi hàm reusable).
- Files: tách hàm reusable từ `check_happy_case.py` → `shared/`, gọi ở `ingest_stages_final.py`. Test:
  sheet cột lạ → advisory event đúng + state vẫn active (không chặn).

### Phase 8 — G-Batch: cap batch-size Jina embed (T2)
- Verify trước (rule #0): grep batch logic U7. Nếu chưa cap → giới hạn ≤ N chunks/request (config) tránh TPM rate-limit + spike.
- Files: `ingest_stages_store.py`. Defer T2 nếu đã có cap.

---

## 4. Anti-pattern phải tránh (CLAUDE.md)
- ❌ Hardcode frozenset locale / ngưỡng split / batch trong Python → phải DB-seed / `shared/constants`.
- ❌ Port nguyên parser-zoo/propositions/LLM-claimify (HALLU surface, anti-happy-case).
- ❌ Đổi sang Qdrant/Kafka/RabbitMQ/K8s (advice file khuyên) → REWRITE, vi phạm stance. Giữ pgvector + Redis Streams + single-process.
- ❌ "Tenant Profiling" heading-rule riêng mỗi bot → per-bot logic trong core ([[feedback_no_per_bot_logic]]). Nếu cần = config schema DB.
- ❌ Copy verbatim code mẫu trong `z-luannt-system-design.txt` (hardcode VN string, broad except, typo `List[String]`).
- ❌ psql hot-fix role token → chỉ alembic.
- ❌ Tuyên bố % lift trước khi load-test.
- ❌ Đập stats short-circuit / cliff rerank / size-guard (đã chuẩn) — chỉ nối dây / thêm nhánh.

---

## 5. Out-of-scope (plan khác)
- Stats short-circuit topK=1 (retrieve) → plan riêng `[T1]`.
- Bind reranker cho bot xe (config/binding) → ops/admin.
- Long-context mode kiểu NotebookLM cho bot nhỏ → ADR riêng (hướng c).
- Worker autoscaling / priority-queue noisy-neighbor → ops/infra (không phải code core).

## 6. ADR cần viết (hard-to-reverse + real-trade-off)
- **ADR-input-control**: ✅ ĐÃ VIẾT + Accepted — `docs/adr/0005-normalize-to-ir-input-philosophy.md`.
  Khóa: NORMALIZE-to-IR (hệ thống tự normalize, KHÔNG bắt khách viết lại format); phân biệt FORMAT (không
  giới hạn, auto-normalize) vs DATA-CONTENT (thiếu thì không đẻ ra được → advisory cho owner); checker =
  advisory KHÔNG chặn. Lý do lock: anh xoay quanh quyết định này nhiều lần = load-bearing.

## 7. Thứ tự ưu tiên đề xuất (impact, rẻ→đắt)
1. **Source-fix** 4 sheet xe → 1 catalog WIDE + cột Tồn (data-tier, rẻ nhất, đòn bẩy cao nhất — ngoài code).
2. Phase 5 G-Linearize (diệt Nhóm B HALLU).
3. Phase 1 G1+G2 multi-locale role (mở input đa ngôn ngữ).
4. Phase 2 G3 breadcrumb table (ADAPT tldw #1).
5. Phase 7 G-Wire checker (hết silent success).
6. Phase 6 G-OOM split (chống sập khi scale).
7. Phase 3 G4 surface warning · Phase 8 G-Batch · Phase 4 tokenizer (T2 defer).

---

*Lập bởi Claude Opus 4.8 (1M context). PROPOSAL — chưa implement. Cần approve. Phiên READ-ONLY: 0 dòng `src/` sửa, chỉ cập nhật plan doc.*
