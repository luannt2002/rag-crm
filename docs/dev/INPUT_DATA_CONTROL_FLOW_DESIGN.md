# Input-Data Control Flow — Canonical Template + Robustness Design

> **Status**: DESIGN / research spec (read-only). No `src/` edits proposed here — this
> document defines the target architecture and the acceptance harness. Every
> "current behavior" claim is tagged **SỰ THẬT** (evidence `file:line`) vs
> **GIẢ THUYẾT/PROPOSAL** (design). Aligns with CLAUDE.md: happy-case template,
> fix-source-first, domain-neutral, multi-tenant, multi-language, sacred rule#0.

**Thesis under test (user)**: the platform's critical weakness is the *input-data
flow* — anything outside the recognised shape is **silently dropped (dead)**. Goal:
a template + control that standardizes the WIDEST range of real-world input WITHOUT
being over-restrictive, NOT per-bot, multi-language by construction.

**Verdict (measured)**: the thesis is **partially TRUE and load-bearing**. The
SHAPE machine (`tabular_markdown.py`) and money parser are already broad
(stress harness 80/87 = 91% PASS+GRACEFUL — §7). The real silent-drop surface is
narrower but real: **column-ROLE recognition is a closed, vi-only, exact-match
vocabulary** (`document_stats.py:135-165`). A header outside that set does not crash —
it falls to `attributes_json` or positional fallback, so the column's *semantic role*
(price/category/aliases) is **dropped without a warning**, and there is **no English /
no second-locale token set at all**. The fix is a DATA-tier CHECKER + NORMALIZER that
makes every dropped column SURFACE, plus a locale-pluggable role map — not a bigger
parser.

---

## 0. Evidence map (what was read)

| Concern | File:line | Tag |
|---|---|---|
| Current template spec | `docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md:40-66` | SỰ THẬT |
| Golden templates | `docs/dev/templates/{catalog_single,catalog_multisection}.csv`, `document.md` | SỰ THẬT |
| Role token sets (closed vocab) | `document_stats.py:135-165` | SỰ THẬT |
| Exact-match normalise + role assign | `document_stats.py:172-179, 307-328` | SỰ THẬT |
| Positional fallback (name+price only) | `document_stats.py:331-491` | SỰ THẬT |
| Money parser (locale-coupled) | `document_stats.py:182-205`, `tabular_markdown.py:40-69` | SỰ THẬT |
| SHAPE state machine (L1) | `tabular_markdown.py:106-216` | SỰ THẬT |
| Checker (report-card) | `scripts/check_happy_case.py:92-168` | SỰ THẬT |
| Normalizer (data-tier rewrite) | `scripts/normalize_to_happy_case.py:30-106` | SỰ THẬT |
| Parser registry + byte-sniff | `infrastructure/parser/registry.py:97-179` | SỰ THẬT |
| Stats index queries (name kw, unaccent) | `stats_index_repository.py:435-565` | SỰ THẬT |
| Orphan LLM strategy selector | `infrastructure/chunking_strategy/llm_resolver.py:139-243` | SỰ THẬT |
| Locale-driven content precedent | `application/ports/language_pack_port.py`, `language_packs` table | SỰ THẬT |
| Stress harness | `scripts/table_taxonomy_stress_test.py` | SỰ THẬT |
| The 9 real bot files | `reports/happy_case_clone/{xe-1..4,spa-1..4,thongtu-09-2020}.{csv,md}` | SỰ THẬT |

---

## 1. Current weakness — MEASURED

The flow does NOT crash on unknown input. It **degrades silently**: the structural
markdown (the LLM/vector path) is preserved, but the **stats-index entity extraction**
(the deterministic price/list/superlative path) loses the *semantic role* of any column
whose header is not in a fixed vi vocabulary. Four concrete drop/mis-bind mechanisms:

### 1.1 Closed-vocab exact-match → unlisted header role lost (SỰ THẬT)

`_column_roles()` (`document_stats.py:307-328`) assigns a role ONLY when the
accent-stripped header is **exactly** in one of three frozensets
(`document_stats.py:135-145`):

```python
_NAME_COL_TOKENS  = {"ten","name","dich vu","service","san pham","goi","combo", ...}
_CATEGORY_COL_TOKENS = {"nhom","danh muc","category","loai","vung","type","khu vuc"}
_PRICE_COL_TOKENS = {"gia","price","phi","amount","cost","don gia","gia le", ...}
```

`_normalise()` is `lower()` + NFD accent-strip only (`document_stats.py:172-179`) — no
fuzzy, no substring, no synonym. Consequences observed in the 9 files:

| Real header | File | Role today | Drop |
|---|---|---|---|
| `Tên hàng` → norm `ten hang` | `xe-1.csv:1` | ❌ not in `_NAME_COL_TOKENS` (`ten` ≠ `ten hang`) | name role missed; positional fallback may still grab col-0 by luck, but `Tên kho` (warehouse) competes |
| `Vùng triệt` → `vung triet` | `spa-3.csv:1` | ❌ (`vung` is a *category* token, but `vung triet` ≠ `vung`) | the de-facto NAME column is unrecognised |
| `Giá buổi lẻ`, `Giá Combo 10 buổi` | `spa-3.csv:1` | ❌ exact-match fails (`gia le` ≠ `gia buoi le`) | rescued ONLY by the pure-money positional fallback (`document_stats.py:377-407`); the *column semantics* ("combo" vs "lẻ") survive only because the header string is echoed into `attributes` (`:404-406`) |
| `Aliases` | `xe-3.csv:1` | ❌ no `aliases`/`synonym` token in ANY set | the entire synonym column → a string `attributes_json["Aliases"]` value; never a first-class searchable role |
| `Gói 6 triệu`/`Gói 7 triệu` (column headers) | `spa-1.csv:1` | ❌ these are *availability/tier* columns marked `x`; no role | the `x` cells → ignored; tier membership is lost as a queryable axis |
| `Marks`/`Cargo`/`Ngày về` (+ CJK row 2) | `xe-2.csv:1-3` | ❌ none in vocab; multi-header (vi+CJK+en stacked) | no name, no price → **0 entities** (it is an inventory manifest, but the drop is silent) |

**Key point (SỰ THẬT)**: none of these emit a WARNING. The unassigned column lands in
`attributes_json` (`document_stats.py:427-428`) or is skipped. The owner gets a
`success` ingest status and never learns the price/alias column was demoted to an
opaque blob. This is the silent-drop the thesis names.

### 1.2 Positional fallback only resolves name + price (SỰ THẬT)

When `_column_roles()` finds NO name column, `_extract_entity_from_row()` falls back
to positional: first non-money, non-ordinal cell = name (`document_stats.py:411-425`);
pure-money cells = `price_primary`/`price_secondary` (`:377-392`). There is **no
positional fallback for category, aliases, qty, unit, sku**. So a 2-locale or
unlabelled sheet recovers AT MOST `(name, price1, price2)` — every other column becomes
a string attribute with its (possibly unrecognised) header as the key. A `Số lượng`
(qty) or `Aliases` column is structurally invisible to the typed query routes
(`query_by_price_range`, `top_by_price` — `stats_index_repository.py:179-334`).

### 1.3 Aliases / synonym column rejected as an entity, not promoted to a role (SỰ THẬT)

`xe-3.csv` was *manually* normalized to add an `Aliases` column
(`normalize_to_happy_case.py:30-54`) precisely because the search-index export (`xe-1`'s
sibling) floods the index with synonym rows. But the parser has **no `aliases` role**:
the `Aliases` cell survives only as `attributes_json["Aliases"]` (a string), and BM25
match on those 62 spelling variants depends on the chunk markdown carrying them, NOT on
any structured alias field. `query_by_name_keyword` (`stats_index_repository.py:435`)
matches `entity_name`/`entity_category` only — **never `attributes_json`** — so the
aliases are not used by the deterministic name route at all.

### 1.4 No multi-language headers; silent failure on a non-vi sheet (SỰ THẬT)

Every role token set is Vietnamese-first with a *handful* of incidental English words
(`name`, `price`, `service`, `product`, `amount`, `cost`, `category`, `type`). There is:
- no notion of `locale` anywhere in `document_stats.py`;
- no `column_role_tokens[locale]` map;
- no per-bot/per-document language signal feeding role detection.

An English bot whose sheet header is `Treatment | Rate | Tier` gets: `treatment` ∉
name-set, `rate` ∉ price-set, `tier` ∉ category-set → **0 roles assigned → positional
fallback → name+price-only**, every other column to attributes. A Spanish or Thai sheet
fares worse. The money parser is also vi-coupled: `_MONEY_UNIT_RE` recognises `triệu/
nghìn/tr/k/đ/m` (`tabular_markdown.py:40-43`) — fine for vi/en shorthand, but a `€`/`$`
prefix or `1,234.56` *decimal-comma-Western* sheet is not normalised.

### 1.5 Measured drop summary (the 9 files, today)

| File | Shape | Entities extracted (mechanism) | Silent loss |
|---|---|---|---|
| `xe-1.csv` | warehouse export, stub rows `Tên kho : …`, image URLs | low — URL/metadata-lead guards reject most rows (`document_stats.py:55-58, 50`) | qty (`date1`), images intentionally dropped; **name = `Tên hàng` role unrecognised** |
| `xe-2.csv` | tri-lingual stacked header (vi/CJK/en) | ~0 (no name/price token) | whole manifest silent |
| `xe-3.csv` | normalized catalog w/ `Aliases` | name+price OK; **`Aliases` → string attr** | alias role not queryable |
| `xe-4.md` | prose policy, no headings (normalized → headings) | doc path, N/A | (fixed by normalizer) |
| `spa-1.csv` | tier-availability grid `Gói N triệu` cols = `x` | name+`Giá lẻ` price OK | tier-membership axis lost |
| `spa-2.csv` | banner row + single section | name+price OK | banner row → NOTE (OK) |
| `spa-3.csv` | `Vùng triệt | Giá buổi lẻ | Giá Combo` | name+2 prices via **positional+pure-money fallback** | combo/lẻ semantics survive only as echoed attr |
| `spa-4.md` | consultation script (normalized → doc) | doc path, N/A | (fixed by normalizer) |
| `thongtu-09-2020.csv` | legal Thông tư (prose+headings) | doc path, price N/A | none (correct) |

**Conclusion**: silent-drop is real for **role semantics** (category/aliases/qty/tier)
and for **non-vi headers**. It is NOT broadly true for raw name+price (the positional
fallback + money parser already cover the ~90% happy case — §7 proves it).

---

## 2. Input-shape taxonomy (the "rộng" part)

Reference SOTA table-structure taxonomy: **Docling**, **Microsoft TATR**
(Table Transformer), **PubTables-1M**, **SciTSR**, **unstructured.io**, **Lautert**
web-table types, **Crestan-Pantel** web-table genre classification. The rule below:
*support relational shapes by SHAPE rules; keep non-relational shapes as readable
markdown (vector/LLM path) and emit NO garbage entities; defer true 2-D/ragged to ML.*

Legend: ✅ supported-how · 🟡 normalize-to-canonical-how · 🔴 reject-why + owner action.

### 2.A Catalog-table variants

| Shape | Example header / row | Status | How |
|---|---|---|---|
| Single-section relational | `STT, Tên, Giá` → `1, Item A, 700000` | ✅ | `_looks_header`→`open_header`→role assign or positional; A01 PASS |
| Multi-section `## group` | `Nhóm Alpha` / `Tên,Giá` / … blank / `Nhóm Beta` | ✅ | SECTION_TITLE state binds rows to `## section` (B3); A13/A14 PASS |
| Category-stub + rowspan forward-fill | `Nhóm | Tên | Giá` with blank stub continuation | ✅ | `stub_fill` forward-fill (`document_stats.py:628-632`); A05/A11 PASS |
| Section-in-header row | `Gói A | <gap> | Thời gian | Giá` | ✅ | SECTION-IN-HEADER split (`tabular_markdown.py:180-192`); A27 PASS |
| Long title above table | `Bảng giá … cao cấp 2026` then `Tên,Giá` | ✅ | `_precedes_table` lookahead (`tabular_markdown.py:131-140`); A29 PASS |
| Name-contains-money | `Gói 6 triệu` = NAME vs `6000000` = PRICE | ✅ | `_is_pure_money` residue-letter test (`tabular_markdown.py:57-69`); A28/B07 PASS |
| Total / aggregate row | `Tổng cộng | 300000` | ✅ reject-as-entity | `_AGGREGATE_TOKENS` exact-match drop (`document_stats.py:161-165`); A19 PASS |
| Multi-currency / tier columns | `Dịch vụ | Giá lẻ | Gói 6tr | Gói 7tr` (`x` marks) | 🟡 | name+lẻ price extracted; **tier `x` axis lost** → normalize tiers to rows OR add `tier` role (§3.2, §6) |
| Aliases / synonym column | `… | Aliases` (62 variants) | 🟡 | survives as string attr; **promote to `aliases` role** + index it (§3.2, §6) |
| Pivot / year-as-columns | `Sản phẩm | 2022 | 2023` | 🔴 defer-ML | entity↔cell semantics need TATR/Docling cell-role; A06/A07/A09 GRACEFUL (grid kept, no garbage). Owner: unpivot to `Sản phẩm | Năm | Giá` |
| Transposed / key-value | attrs=rows, entities=cols | 🔴/🟡 | A02 GRACEFUL (no garbage); A16 kv GRACEFUL. Owner: transpose to row-oriented |
| Ragged (varying col count) | rows with 2,4 cols under a 3-col header | 🟡 | A20 PARTIAL — best-effort by position; owner: pad to rectangular |

### 2.B Non-table inputs

| Shape | Status | How |
|---|---|---|
| Plain-text / TXT (no structure) | ✅ keep as prose | passthrough parser → recursive chunk; NO field extraction |
| Doc / heading (legal, contract, SOP) | ✅ | heading markdown → HDT chunk; price extraction N/A (`check_doc`, `check_happy_case.py:152-160`) |
| Prose-with-incidental-comma (legal sentence) | ✅ guard | `_is_prose_row` (`document_stats.py:506-523`) skips it — no false entity |
| Bullet list / PPTX slide | ✅ | 0 entities, kept as markdown text (B08/B18 GRACEFUL) |
| Mixed (prose + buried table) | ✅ | table extracted, prose kept (B04/B13 PASS) |

### 2.C What to support now vs defer to ML

- **Support now (SHAPE rules, deterministic)**: every ✅ above + the two 🟡 role
  additions (aliases, tier/qty/unit) which are *role-vocabulary* extensions, not ML.
- **Defer to ML (typed-IR + TATR/Docling)**: pivot/year-columns, true 2-D matrices,
  ragged reconstruction, merged-cell colspan reconstruction. These need a typed cell
  model the hand state-machine cannot represent. Per `HAPPY_CASE_DOCUMENT_FORMAT.md:124-129`
  the platform stance is: keep them as readable markdown (vector path stays correct),
  emit NO garbage entities, and ask the owner to unpivot — until a typed-JSON-sidecar
  ADR lands.

---

## 3. Canonical schema (broad, NOT over-restrictive)

### 3.1 Minimal required structure (the contract floor)

A conforming input is EITHER:

- **TABULAR (catalog)**: a CSV/sheet/table where, after L1 structuring, each sub-table
  has a header row whose columns can be ROLE-mapped, and one entity per data row. The
  ONLY hard requirement is a **NAME-bearing column** (a column that identifies the
  row's entity). A price column is required *only if it is a price catalog*; an
  inventory/manifest with no price is valid (`check_happy_case.py:114-116`).
- **DOC (prose)**: a document structured by **markdown headings** (`#`/`##`/`###`,
  ≥3 → `_is_doc`, `check_happy_case.py:87-89`). No table required; price extraction N/A.

That is the whole floor. Everything else is OPTIONAL.

### 3.2 Optional roles (the broad part — locale-extensible vocabulary)

```
Required: name              (entity identity — the one mandatory tabular role)
Optional: category          (group/section/stub — forward-filled)
          price_primary     (single/base price)
          price_secondary   (combo/package/sale price)
          aliases    [NEW]  (synonym list; indexed for name-keyword route)
          qty        [NEW]  (inventory count — non-price numeric)
          unit       [NEW]  (currency/measure unit, e.g. VND, /buổi, kg)
          sku/code   [NEW]  (identifier — kept, never a price)
          tier       [NEW]  (availability axis: package membership 'x' grid)
          attributes (catch-all for any RECOGNISED-but-unmodelled column —
                      with its header preserved as the key; NEVER silently dropped)
```

The `[NEW]` roles are **role-vocabulary additions** (token-set + extraction wiring),
not new ML. They directly close §1.2/§1.3 losses. Each must be queryable: `aliases`
folds into `query_by_name_keyword` (today it ignores `attributes_json` —
`stats_index_repository.py:489-498`); `qty`/`tier` get their own optional index columns
or stay in `attributes_json` *but surfaced by the checker*.

### 3.3 What NOT to do (anti-over-restriction)

- **Don't fabricate**: never invent a price/category the source lacks (HALLU=0 sacred).
  A row with no parseable price → `price=None`, not a guessed value.
- **Don't over-flatten**: never force a prose doc or a transposed/pivot sheet into a
  fake relational table (that is the A02/A06 GRACEFUL path — keep the grid, skip
  entities).
- **Don't field-extract prose**: figures inside sentences (`499K/buổi` in a policy
  clause) stay IN the markdown — extracting them loses the qualifying condition and
  becomes a HALLU source (`HAPPY_CASE_DOCUMENT_FORMAT.md:85-86`).
- **Don't reject on an unknown column**: an unrecognised header must produce a
  **WARN + attribute-preservation**, never a hard drop and never a crash.

---

## 4. MULTI-LANGUAGE (locale-extensible by construction)

**Current state (SỰ THẬT)**: role tokens are a single vi-first frozenset with incidental
en words; money is vi/en shorthand; there is no `locale` axis. The `language_packs`
table + `LanguagePackPort` (`application/ports/language_pack_port.py`) ALREADY prove the
platform's pattern for locale-driven content: *"adding a new language is a seed, not
code"*, with default-language fallback. The role vocabulary must adopt the SAME pattern.

### 4.1 `column_role_tokens[locale]` — the proposed shape (PROPOSAL)

Replace the three module-level frozensets with a locale-keyed map, sourced from
`system_config` / a `column_role_tokens` table (DB-seeded, alembic-tracked — never a
hardcoded vi set, satisfies zero-hardcode + domain-neutral):

```jsonc
// column_role_tokens (DB-seeded, per-locale; default-locale fallback like language_packs)
{
  "vi": {
    "name":     ["ten", "ten dich vu", "ten san pham", "ten hang", "dich vu", "san pham", "goi", "combo", "muc"],
    "category": ["nhom", "danh muc", "loai", "vung", "khu vuc"],
    "price":    ["gia", "don gia", "gia le", "gia goc", "gia sale", "phi", "thanh tien"],
    "aliases":  ["alias", "aliases", "dong nghia", "ten goi khac", "tu khoa"],
    "qty":      ["so luong", "ton kho", "sl"],
    "unit":     ["don vi", "dvt"],
    "sku":      ["ma", "ma hang", "ma sp", "code"]
  },
  "en": {
    "name":     ["name", "item", "service", "product", "title", "treatment"],
    "category": ["category", "group", "type", "section", "region"],
    "price":    ["price", "rate", "amount", "cost", "unit price", "fee"],
    "aliases":  ["alias", "aliases", "synonym", "synonyms", "keywords"],
    "qty":      ["qty", "quantity", "stock", "count"],
    "unit":     ["unit", "uom"],
    "sku":      ["sku", "code", "id"]
  }
  // add "es", "th", … = a seed row, no code change
}
```

Role resolution at runtime: `tokens = merge(column_role_tokens[doc_locale],
column_role_tokens[default_locale])` (mirrors `get_pack`'s default-merge,
`language_pack_port.py`), so a partially-translated locale still works and an EN bot
gets EN tokens without losing the vi fallback. `doc_locale` is resolved from the
document's `detected_language` (already computed — `DocumentProfile.detected_language`,
`llm_resolver.py:83`) or the bot's configured locale. **No per-bot literal** — the map
is platform-wide, locale-keyed.

### 4.2 Language-agnostic entity extraction (PROPOSAL)

- **Numbers/prices**: keep money parsing but parameterise the unit/grouping by locale —
  a `number_format[locale]` map (decimal/thousands separator, currency symbols/words).
  vi: `.`=thousands, `triệu/nghìn/k/tr/đ`; en-US: `,`=thousands, `$/k/M`; de/es:
  `.`=thousands `,`=decimal `€`. The residue-letter test (`tabular_markdown.py:57-69`)
  stays locale-neutral (any leftover letter ⇒ name).
- **Accent-fold**: `_normalise` (NFD strip) is already accent-folding and works for any
  Latin-diacritic locale; `unaccent()` in the query route folds đ→d, ế→e
  (`stats_index_repository.py:451-453`). Non-Latin scripts (CJK/Thai) need NO folding —
  exact/substring match suffices.
- **Aliases/narrate multilingual**: the `aliases` role holds variants in ANY language;
  `query_by_name_keyword` OR-expands them (it already supports per-bot `synonyms` —
  `stats_index_repository.py:470-475`), so a vi query can hit an en alias and vice versa.

### 4.3 EN vs VI through the SAME control (PROPOSAL)

```
EN bot sheet  "Treatment | Rate | Tier"        VI bot sheet  "Vùng triệt | Giá buổi lẻ"
        │                                                │
        └──► detect locale = en ──┐          ┌── detect locale = vi ──┘
                                  ▼          ▼
                      merge(column_role_tokens[locale], default)
                                  │
                  role-detect (exact → substring → fuzzy → positional → WARN-unknown)
                                  │
                      same CHECKER · same NORMALIZER · same canonical IR
```

One control path; the only locale-varying inputs are two DB-seeded maps
(`column_role_tokens`, `number_format`). This ties directly to the program's
multi-language findings (the locale-resolved `language_packs` / `sysprompt_default_rules`
pattern, e.g. `program/gaps/P2-H-bot-owner-control-plane.md:11` `_VI_RULES`/`_EN_RULES`):
the same "seed a locale, don't fork code" discipline.

---

## 5. THE CONTROL FLOW (robustness in the DATA tier, not guess-in-code)

```
                          ┌─────────────────────────────────────────────────────────┐
SOURCE (bytes / URL)      │  POST /api/ragbot/documents/create  (ONE canonical path) │
  any format ────────────►│  idempotent X-Idempotency-Key                            │
                          └───────────────────────────┬─────────────────────────────┘
                                                      ▼
                          ┌─────────────────────────────────────────────┐
                          │  DETECT-FORMAT  mime → file-ext → BYTE-SNIFF  │  registry.py
                          │  (%PDF-, PK OOXML, kreuzberg detect)          │  :123-179
                          └───────────────────────────┬─────────────────┘
                                                      ▼
                          ┌─────────────────────────────────────────────┐
                          │  PARSE → UNIFIED STRUCTURED-MARKDOWN          │  parser/*, ocr/*
                          │  (## heading + | table | + atomic blocks)    │  tabular_markdown.py
                          └───────────────────────────┬─────────────────┘
                                                      ▼
        ┌──────────────────────────  CHECKER (report-card, NO LLM)  ──────────────────────────┐
        │  per-column role-assignment table:  header → role | UNKNOWN                          │
        │  • every column gets a verdict; UNKNOWN ⇒ WARN (never silent)                        │  check_happy_case.py
        │  • REJECT only on a structural fault (no name col / prose-in-name / 0% price-cov)     │  (extend to per-column)
        │  • score + ACTIONABLE fix per dimension                                              │
        └───────────────┬──────────────────────────────────────────────────┬──────────────────┘
                ✅ HAPPY / 🟡 MINOR                                    🔴 NON-HAPPY
                        │                                                   │
                        ▼                                                   ▼
        ┌───────────────────────────────┐                 ┌────────────────────────────────────┐
        │  NORMALIZER (data-preserving)  │                 │  return report-card to owner;       │
        │  • rename owner header→canonical│ (auto for 🟡)  │  owner fixes SOURCE (re-export /     │  normalize_to_happy_case.py
        │    via locale-aware role map    │                 │  unpivot / add header / split doc)  │
        │  • restructure shapes (kv→rows, │                 │  loss-report proves additive        │
        │    section-in-header split)     │                 └────────────────────────────────────┘
        │  • NEVER drop a column: unknown │
        │    → keep as named attribute    │
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  CANONICAL IR  (one form)      │
        │  structured-markdown + roles   │
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  INGEST                        │
        │  • markdown → chunk (LLM/vec)  │
        │  • stats-index entities (price/│  stats_index_repository.py
        │    list/superlative routes)    │
        └───────────────────────────────┘
```

**The silent-drop-impossible invariant (PROPOSAL)**: the CHECKER emits a
**per-column role-assignment report**. Every column header in every sub-table is listed
with its resolved role OR the literal verdict `UNKNOWN → kept as attribute "<header>"`.
A column can be: mapped (✅), normalized (🟡 auto-rename), or unknown-but-preserved
(🟡 WARN). It can NEVER vanish without a line in the report. This is the structural
answer to the thesis: *drops become visible at the gate*.

**Today vs proposed (SỰ THẬT vs PROPOSAL)**:
- Today the checker scores 4 document-level dimensions (header clarity, row atomicity,
  price coverage, doc heading-structure — `check_happy_case.py:108-160`). It does NOT
  enumerate per-column role assignment, so an UNKNOWN price/alias column passes under
  "header clarity ✅ name found" without flagging the demoted column.
- Proposed: add a `check_columns()` dimension that runs `_column_roles()` per sub-table
  header and prints `header → role | UNKNOWN(kept as attr)` for every column, WARN on
  any UNKNOWN that carries data, REJECT on a missing name role.

---

## 6. Role-detection generalization (beyond closed-vocab exact-match)

**Current (SỰ THẬT)**: `_column_roles` = exact normalised-token membership only
(`document_stats.py:318-328`). `Tên hàng`, `Vùng triệt`, `Giá buổi lẻ`, `Aliases` all
miss. Positional fallback rescues name+price; everything else → attribute.

**Proposed cascade (PROPOSAL)** — deterministic, ordered, each step domain-neutral:

1. **Exact** (locale-merged token set) — unchanged, fastest, zero false-positive.
2. **Substring / head-token** — `Giá buổi lẻ` contains `gia`; `Tên hàng` contains `ten`;
   `Đơn giá gói N` contains `don gia`. Guard: substring match only on a *leading* token
   (`ten …`, `gia …`) to avoid `Service A` matching the `service` name-token mid-cell
   (the exact-match comment at `document_stats.py:116-121` warns of this — so substring
   must be anchored at the cell start + word boundary).
3. **Fuzzy (bounded)** — Levenshtein/ratio ≥ threshold for typos (`Giáa`, `Têntreatment`).
   Threshold in `system_config` (zero-hardcode). Optional, behind a flag.
4. **Positional** — extend beyond name+price: a pure-numeric non-price column after a
   name+price = `qty`; a long-list cell (`;`-joined, > N variants) = `aliases`; a short
   code-shaped cell (`^[A-Z0-9/-]{2,12}$`) = `sku`.
5. **Explicit UNKNOWN** — any column unresolved by 1–4 ⇒ role `UNKNOWN`, value kept as
   `attributes_json["<header>"]` AND surfaced by the checker WARN. **Never silent.**

### 6.1 Optional: rule-based vs LLM-assisted column-role classifier (ADR-worthy)

There is an **orphan LLM selector already in the codebase**:
`LLMChunkingStrategyResolver` (`infrastructure/chunking_strategy/llm_resolver.py:139-243`).
It is a clean Port+DI strategy that sees only SHAPE statistics (domain-neutral,
`llm_resolver.py:8-9`), degrades to a rule fallback on any error
(`:202-224`), and is gated by `chunking_strategy_provider` config. **It could be
repurposed** as the pattern for an LLM-assisted *column-role* classifier (same shape:
LLM sees header tokens + a few sample rows, returns `{header → role}`, deterministic
rule fallback, cross-check guard).

| Option | Pros | Cons |
|---|---|---|
| **Rule cascade (1–5)** | deterministic, HALLU=0, zero cost, no latency, fully auditable | needs token-set + fuzzy maintenance per locale; misses truly novel headers |
| **LLM-assisted classifier** (repurpose `llm_resolver` pattern) | covers novel/multilingual headers no token set anticipated; one model, all locales | cost + latency at ingest; non-determinism (mitigated by rule fallback + cross-check); must NEVER override a confident rule (rule wins, LLM only fills UNKNOWN) |
| **Hybrid (recommended)** | rule cascade decides; LLM consulted ONLY for residual UNKNOWN columns; cross-check guard rejects an unreasonable LLM role | best coverage at bounded cost | extra moving part; ADR to govern the LLM-fills-UNKNOWN boundary |

**Recommendation (PROPOSAL)**: ship the rule cascade (1–5) first — it closes §1.1/§1.2/
§1.3 deterministically and is HALLU-safe. Defer the LLM-assisted UNKNOWN-filler to an
ADR (hard-to-reverse + real trade-off: cost/latency/non-determinism at the ingest
boundary). The LLM step, if adopted, fills ONLY role `UNKNOWN`, never overrides a rule,
and runs the same cross-check guard the strategy selector uses (`llm_resolver.py:14-16`).

---

## 7. Acceptance + measurement

### 7.1 Stress harness baseline (MEASURED — rule#0)

`python scripts/table_taxonomy_stress_test.py` (3 suites, 87 cases) — run on current
production code:

```
GRAND TOTAL (87 cases): FAIL=2  GRACEFUL=12  INFO=4  PASS=68  RISK=1
  → PASS+GRACEFUL (acceptable) = 80/87 = 91%
```

- **PASS 68** = relational shapes extracted name↔price correctly + sections bound.
- **GRACEFUL 12** = non-relational (transposed/matrix/kv/list/layout) kept as grid with
  NO garbage entity — the correct degrade.
- **RISK 1 / FAIL 2** = the deferred-to-ML edges (pivot/ragged) + one money quirk
  (`2tr5`→2,005,000, C24) + range cells (C12/C23 INFO, no crash).

This is the harness that proves a case-study is supported. **Acceptance gate**: a new
shape is "supported" only when it lands PASS (relational) or GRACEFUL (non-relational)
— never RISK/FAIL silently. Add a fixture per new shape (the harness is already
domain-neutral, synthetic `Item A`/`Region` — `table_taxonomy_stress_test.py:14-16`).

### 7.2 Target after the role-vocabulary + checker work (PROPOSAL — must MEASURE before claiming)

- Add fixtures for the §2 🟡 rows: `aliases` column, tier-grid, qty column, multi-word
  price header (`Giá buổi lẻ`), EN header (`Treatment | Rate`). Re-run; target every new
  fixture PASS.
- Per-file proof: `python scripts/check_happy_case.py --db <doc>` must print a
  per-column role report with ZERO silent UNKNOWN-carrying-data columns un-WARNed.
- The contract test `tests/unit/test_happy_case_template.py` (templates → 0 errors)
  must stay GREEN; add EN-locale template variants.

### 7.3 Onboarding checklist for ANY new bot's data (PROPOSAL)

```
[ ] 1. Run check_happy_case.py on each source file → read the report-card.
[ ] 2. Verdict ✅ HAPPY → ingest as-is.
[ ] 3. Verdict 🟡 MINOR → run normalize_to_happy_case (or owner renames headers to a
        canonical role token); re-check until ✅; confirm loss-report = "no data loss".
[ ] 4. Verdict 🔴 NON-HAPPY → owner fixes SOURCE (the report names the fix:
        unpivot / transpose / split prose to DOC / add header / add name column).
[ ] 5. Confirm the per-column role report: every column is mapped, normalized, or
        UNKNOWN-but-WARNed — NO column missing from the report (silent-drop check).
[ ] 6. Confirm locale: doc_locale detected; merged role tokens cover the headers
        (EN/VI/…); money format matches the locale's number_format.
[ ] 7. Run the stress harness; confirm PASS/GRACEFUL — no new RISK/FAIL.
[ ] 8. Spot-check the stats index: query_by_name_keyword + query_by_price_range return
        the expected entities (deterministic path live), not just vector retrieval.
```

---

## 8. Compliance check (CLAUDE.md)

- **Rule#0 no-guess**: every "current behavior" claim carries `file:line` + the 91%
  baseline is a MEASURED harness run, not an estimate. Proposals are tagged PROPOSAL.
- **Domain-neutral**: zero per-bot/brand literal in the design — generic roles + a
  locale-keyed token MAP seeded in DB; the 9-file examples are case-studies, not code.
- **Zero-hardcode**: role tokens, fuzzy threshold, number formats → `system_config` /
  seeded tables (alembic-tracked), never inline vi frozensets.
- **Multi-tenant**: stats index already scoped `record_tenant_id`+`record_bot_id` (RLS,
  `stats_index_repository.py:94-96`); the role map is platform-wide, not per-tenant.
- **Sacred #10 (no app-inject/override)**: extraction is deterministic Python (HALLU=0);
  the optional LLM step only CLASSIFIES columns at ingest, never injects/overrides an
  answer. No fabrication: `price=None` when absent.
- **One canonical path**: design keeps `POST /api/ragbot/documents/create` as the sole
  ingest; the CHECKER/NORMALIZER are pre-ingest DATA-tier gates, not parallel endpoints.
- **Fix-source-first**: 🔴 cases return a report-card; the platform does NOT grow the
  parser to absorb arbitrary formats — it standardises the source.

---

## 9. Summary — what changes, what stays

| Layer | Stays (already broad) | Changes (close the silent-drop) |
|---|---|---|
| Format detect | mime→ext→byte-sniff registry | — |
| Parse→markdown | SHAPE state machine, money parser, prose guards | locale-param number format |
| **Role detect** | exact-match name+price | **+ substring/fuzzy/positional cascade + explicit UNKNOWN + new roles (aliases/qty/unit/sku/tier) + `column_role_tokens[locale]` map** |
| **Checker** | 4 doc-level dimensions | **+ per-column role report; UNKNOWN-carrying-data ⇒ WARN; silent-drop impossible** |
| Normalizer | per-file one-time rewrites | generalise: locale-aware header→canonical rename, data-preserving |
| Ingest / query | stats index + routes | `aliases` folds into name-keyword route; `qty`/`tier` queryable |

The architecture is **EVOLVE not REWRITE** (strangler-fig per CLAUDE.md): the SHAPE
machine and money parser are kept; the fix is a locale-pluggable role vocabulary + a
per-column report-card gate that makes every dropped column SURFACE. That, not a bigger
parser, is the answer to "input is the critical weakness."
```
