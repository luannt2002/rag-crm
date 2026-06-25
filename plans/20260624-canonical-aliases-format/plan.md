# [T1-Smartness] Canonical input-format + Aliases role — robust, domain-neutral

> Root cause (verified): stats-index column-role detection is closed-vocab exact-match on name/price/category
> ONLY; an Aliases/synonym column + any non-canonical header name is dropped to `attributes_json` (unsearchable),
> and mis-named columns (xe-1 "Tên kho" grabbed before "Tên hàng"; spa-3 "Vùng triệt"→category) mis-bind.
> Fix is DATA-tier (normalizer renames → canonical) + CODE (add Aliases role + searchable field) + CHECKER
> (warn on unassigned column). 0 hardcode per bot — token + map are generic.

## Canonical schema (the template — domain-neutral, ALL bots)
**Catalog sheet** → `## <Nhóm>` section + flat header from these roles:
| Role | Canonical header (recognised tokens) | Required |
|---|---|---|
| **name** | `Tên` (Tên/Tên dịch vụ/Tên sản phẩm/Dịch vụ/Sản phẩm/Gói/Combo/Name/Item) | YES |
| **category** | `Nhóm` (Nhóm/Danh mục/Loại/Category) | recommended (for grouping/list queries) |
| **price** | `Giá` (Giá/Đơn giá/Giá lẻ/Giá gốc/Price/Amount) + secondary `Giá combo`/`Gói N` | if catalog |
| **aliases** | `Aliases` (Aliases/Synonyms/Từ khoá/Keyword/Biến thể) — `;`-separated search variants | optional (search-key) |
**Doc** → heading-structured markdown (legal/thông tư — unchanged).

## Stages (the user's 4)
### Stage 1 — TEMPLATE + CODE (robust role detection)  [worktree agent A]
- `document_stats.py`: add `_ALIASES_COL_TOKENS`; `ParsedEntity.aliases: str | None`; extract aliases cell → entity.
- `ParsedEntity` + `_insert_stats_index` (`document_service/__init__.py:259`): write aliases → NEW column `entity_synonyms TEXT` (alembic migration + index).
- `stats_index_repository.query_by_name_keyword`: OR-match `unaccent(entity_synonyms) ILIKE` + fold, so an alias hits even when the entity_name uses a different notation (solves 265/50ZR20 — both rows list "265/50R20" in Aliases).
- `check_happy_case.py`: WARN/score-down when a header column maps to NO role (was silently dumped to attributes_json) → owner learns to rename.
- `normalize_to_happy_case.py`: rename owner columns → canonical via a generic map (Mặt hàng→Tên, Phân loại→Nhóm, Từ khoá→Aliases, Tên hàng→Tên, Vùng→Nhóm…); data-preserving.
- Update `docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md`: add the Aliases role + "header MUST be canonical" rule + the rename map; remove the "Aliases = reject" stance (now a first-class role).
- TDD every change; domain-neutral grep = 0; ruff HEAD==NOW.

### Stage 2 — DATA rewrite (9 files → canonical)  [agent B]
Rewrite from `reports/happy_case_clone/*.csv` → `reports/happy_case_clone/canonical/`:
- spa-1/2/3: header → `Tên | Nhóm | Giá | [Giá combo]` (Nhóm = service group: Triệt lông/Massage/Gội đầu/CSD); fix spa-3 "Vùng triệt"→`Tên`, add `Nhóm=Triệt lông`.
- xe-1/2/3: `Tên | Nhóm | Giá | Aliases` — fix xe-1 "Tên kho"→drop/`Kho`, "Tên hàng"→`Tên`; keep xe-3 Aliases; add Aliases to xe-1/2 if size-variants exist.
- legal: doc, heading-structured — no column rewrite.
Run `scripts/check_happy_case.py` on each → target HAPPY (after Stage-1 checker update).

### Stage 3 — UPLOAD (re-ingest)
Merge Stage 1 → main branch the app runs from; `systemctl restart ragbot-py`; wipe + re-ingest the canonical files via the ingest API (`ingest_happy_case_via_api.py` or `/documents/create`); wait state=active.

### Stage 4 — TEST (delta)
Re-run the 3-bot QA (same question sets) → measure Coverage delta (esp. xe 44%→? on alias/variant; spa category-group; ). HALLU must stay 0.

## Verification gate
TDD green · ruff HEAD==NOW · domain-neutral grep 0 · 3-bot QA Coverage delta measured (rule#0, no claim without re-run) · HALLU=0.
