# DEEPDIVE 2026-07-02 — shared data layer: document_stats · tabular_markdown · number_format · i18n · constants/*

Scope (every line read): `src/ragbot/shared/document_stats.py` (1234 L), `src/ragbot/shared/tabular_markdown.py` (399 L),
`src/ragbot/shared/number_format.py` (242 L), `src/ragbot/shared/i18n.py` (828 L), `src/ragbot/shared/constants/` (28 files, 5865 L).

Method: full read + **runtime verification** (every CONFIRMED finding below was reproduced by executing the actual module in this session; outputs quoted). Labels: **FACT** = evidence with file:line and/or runtime output; **HYPOTHESIS** = code-shape inference not reproduced end-to-end.

Owner directive honored: the ING-F1 numeric-column-as-price fix was **REVERTED by owner decision (commit 6796cd9)**. The stock-as-price case is documented below as a KNOWN limitation with the per-bot `custom_vocabulary["column_roles"]` workaround — no engine fix re-proposed (see T-14).

---

## 1. Per-file: what it does + pipeline wiring

### 1.1 `shared/number_format.py` — canonical money/number normalizer
- **Purpose**: single source of truth for parsing VN/EN money strings to integer VND. Declares a platform "NUMBER STANDARD" (docstring L13–36): `.`/`,`/space thousands, single-separator disambiguation, suffix multipliers (`tỷ/triệu/tr/M/nghìn/ngàn/k/đồng`), the `1tr499` compound. Also `find_dropped_numbers()` — lossless-coverage observe gate (source numeric tokens missing from all chunks).
- **Key functions**: `parse_money_vn(text, min_value, max_value)` (L156–199, 3-stage: tr-compound → suffix → bare number), `_normalize_literal` (L94–138), `_guard` floor/ceiling (L141–153), `find_dropped_numbers` (L208–239).
- **Pipeline wiring** (FACT, grep): ingest side via the `document_stats.parse_money_vn` wrapper (document_stats.py:279–302, applies `DEFAULT_PRICE_MIN_VND=10_000` / `DEFAULT_PRICE_MAX_VND=500_000_000`); query side via `query_range_parser.py:30` (thin wrapper, floor 0); structure detection via `tabular_markdown.py:31` (`_is_pure_money`, floor 0); coverage gate via `ingest_stages.py:868–869`. This oracle is what makes corpus prices and query range filters agree.

### 1.2 `shared/tabular_markdown.py` — spreadsheet rows → structured markdown (AdapChunk L1)
- **Purpose**: converts raw sheet rows (multi-sub-table, section titles, split headers, merged cells) into section-bound markdown; `split_markdown_to_row_chunks` then emits atomic chunks = `## section + header + separator + ONE data row`.
- **Key functions**: `_is_pure_money` (L60–72, money-shape oracle: parse + residue-letter test), `_is_label_like`/`_looks_header` (L79–102), `_is_header_continuation`/`_merge_header_fill` (2-row split header, L105–137), `_normalize_rows` (L144–211: blank-head/tail trim, gap-K collapse via `DEFAULT_TABLE_GAP_ROWS`, leading-sparse-column forward-fill), `rows_to_structured_markdown` state machine (L214–349: SEPARATOR / SECTION_TITLE / SECTION-IN-HEADER / HEADER + multi-row merge / DATA / NOTE), `split_markdown_to_row_chunks` (L358–396).
- **Pipeline wiring** (FACT, grep): `infrastructure/parser/excel_openpyxl_parser.py:85,115`, `infrastructure/parser/google_sheets_parser.py:84,104`, `infrastructure/parser/docx_parser.py:115` (DOCX in-table cells). **Not** used by PDF/HTML/PPTX/CSV/TXT/MD paths — see F17.

### 1.3 `shared/document_stats.py` — deterministic stats-index extractor (Pinecone/AI21 metadata-filter pattern)
- **Purpose**: parses table/CSV **chunks** (post-chunking; prefers `raw_chunk` over enriched `content`, L966–974) into `ParsedEntity(name, category, price_primary/secondary, attributes, aliases)` for `document_service_index`; `aggregate_summary()` builds `documents.summary_json` (entity_count, min/max price, VND buckets, categories); `analyze_table_headers()` = owner-facing data-quality advisory (ADR-0005, never blocking).
- **Key machinery**: header detection `_is_header_row` (L348–387: value-contrast + **structural separator trust** `next_is_separator` + vocab hint `_HEADER_EXACT_TOKENS`), role binding `_column_roles` (L500–580: Tier-2 owner `custom_vocabulary["column_roles"]` authoritative > Tier-1 G1 cascade exact-100 / phrase-60 / word-30 with tie-skip > Tier-3 generic attribute), row extraction `_extract_entity_from_row` (L583–809: role-aware, pure-money gate on unknown columns, owner-`attribute` price suppression, ragged-row name fallback, out-of-scope name defenses), split-header premerge `_premerge_split_headers` (L905–934, incl. stored-`col_N` placeholder re-merge L236–242, 844–894), prose/noise guards (`_is_prose_row` L824–841, `_is_noise_entity` L245–266, bullet/metadata/URL/discourse/section-lead rejections L44–77).
- **Pipeline wiring** (FACT): `application/services/document_service/ingest_stages_final.py:446–500` — feeds `raw_chunk` rows, resolves per-bot `custom_vocabulary["column_roles"]` from `bot_repo` scoped by `record_tenant_id` (Tier 2 wired — multi-bot honored), calls `parse_table_chunks` + `analyze_table_headers` (advisory `ingest_data_quality` warning), best-effort (never blocks ingest). Query side consumes the index via the stats routes (range/superlative/code/price-of-entity) configured in `constants/_21`.

### 1.4 `shared/i18n.py` — LanguagePack dataclass + boot fallback + RoutingSignals
- **Purpose**: DB (`language_packs`) is the runtime source; this module keeps (a) the `LanguagePack` dataclass (13 prompt fields + `refuse_message` + `sysprompt_default_rules` + `routing_signals`), (b) verbatim `_VI_PACK`/`_EN_PACK` seeds for boot/DB-outage fallback, (c) `RoutingSignals` — locale-scoped signal lists (count/list/below/above/superlative/price tokens, strip phrases, `measure_unit_re`, intent regexes) + JSON serde, (d) `language_pack_from_dict` hydrator.
- **Pipeline wiring** (FACT): `language_pack_service.py:178` (in-mem fallback), `oos_template_resolver.py:13,57,135` (tier-6 `refuse_message`), `query_range_parser.py:29,117`, `heuristic_intent_classifier.py:28,67`, `orchestration/nodes/retrieve.py:79,212` (routing signals).

### 1.5 `shared/constants/` — 28-module split SSoT
- `__init__.py` star-re-exports `_00`…`_26` **in order**; each module `from ._NN import *` chains its predecessor, so **later modules silently shadow earlier definitions** (F14). `_09` ends with a 571-line `_LEGACY_ALL` list nothing references (F15).
- Groups relevant to this scope: `_09`/`_21` price buckets + floor/ceiling (duplicated); `_13` table-label/gap knobs (`DEFAULT_TABLE_LABEL_MAX_CHARS=40`, `DEFAULT_TABLE_GAP_ROWS=2`) + `CUSTOMER_CONTEXT_COLUMN_NAMES`; `_21` stats-index routing (reverse-match floors, code-query pattern, superlative/race knobs, `DEFAULT_STATS_ATTR_MAX_CHARS/WORDS`, synthetic chunk sentinel); `_24`/`_25`/`_26` locale packs (structural markers, discourse openers, script ranges, narrate prompt templates); `_00` chunking/retrieval; `_11` table_csv strategy; the rest = HTTP/CB/RL/LLM plumbing (true technical constants — compliant).

---

## 2. THE TABULAR EXTRACTION GRAMMAR — input shapes handled vs broken

### 2.1 Handled (by design)
- Multi-sub-table sheets with section titles (1-cell short rows → `##`), local headers per sub-table (converter L268–335).
- Split 2-row headers, both fresh (converter L314–334) and already-stored `col_N` headers (stats `_premerge_split_headers` + placeholder merge L879–894).
- SECTION-IN-HEADER colspan rows `title | gap | col | col` (converter L294–304).
- Merged-cell / rowspan group label **in the leftmost sparse column of a money-bearing table** (`_normalize_rows` forward-fill L186–211).
- Stray blank spacer rows (< `DEFAULT_TABLE_GAP_ROWS`=2) skipped; Excel used-range blank tails trimmed (L160–180).
- Names containing money phrases ("Gói 6 triệu") kept as names, not prices (residue test L60–72; stats L667–672).
- Header-language independence via separator-trust (`next_is_separator`, stats L332–345, 384–385) — Spanish/English headers work **in the main parse loop**.
- Per-bot Tier-2 `column_roles` (name/value/category/aliases/attribute) — wired end-to-end (ingest_stages_final.py:468–486); owner `attribute` declaration suppresses the pure-money→price fallback (stats L541–543, 615–616, 660–663).
- Known price column parsed leniently ("500k/buổi", "Giá: 300k") (stats L664–666).
- Quoted CSV cells (RFC-4180 via `csv.reader`, stats L436–442); literal `|` inside CSV cells (leading-pipe gate, L418–425).
- Prose sentences mis-split by commas rejected (`_is_prose_row` L824–841); bullets, `key: value` metadata leads, URLs, discourse openers, aggregate rows ("Tổng cộng") rejected as names (L750–793).

### 2.2 Broken / silently degrades (verified this session unless labeled HYPOTHESIS)

**T-1 · Headerless tables → first DATA row becomes the "header" of every other row — CONFIRMED, HIGH.**
`rows_to_structured_markdown([["Serum X","500000"],["Cream Y","300000"],["Mask Z","200000"]])` emits three bare pipe rows (converter L342–346, no separator); `split_markdown_to_row_chunks` then takes the **first pipe row as the table header** (tabular_markdown.py:390–392). Observed chunk: `"| Serum X | 500000 |\n| Mask Z | 200000 |"` — Mask Z's chunk carries Serum X's row as its column labels. This is precisely the cross-row value mis-binding the module docstring (L358–369) promises to prevent. Any header-row-less export (CSV dump, copy-pasted range) hits this.

**T-2 · Bare numbers are "money" → year/quantity headers destroyed — CONFIRMED, MEDIUM.**
Runtime: `_is_pure_money("2024") == True`, so `_looks_header(["2024","2025","2026"]) == False` (money veto, tabular_markdown.py:98). Pivot-style tables with year/period headers never open a table → rows degrade to bare pipes → T-1 cascade.

**T-3 · Forward-fill only fires on the FIRST sparse column — merged labels behind an STT column never fill — CONFIRMED, MEDIUM.**
`_normalize_rows` breaks at col 0 when fully populated (`if not 0 < filled < n_data: break`, tabular_markdown.py:192). Runtime: `STT | Nhóm | Tên | Giá` with merged `Nhóm` → rows 2–3 emitted with **empty Nhóm** (group binding lost). Real sheets almost always carry an STT column, so the advertised rowspan recovery (docstring L150–158) rarely applies.

**T-4 · Text-only tables: merged col-0 continuation row is DESTROYED — CONFIRMED, MEDIUM-HIGH.**
Fill seeds only from money-bearing rows (L203–204). Runtime: `[["Khu vực","Trạng thái"],["Miền Bắc","Hoạt động"],["","Tạm dừng"]]` → the `["","Tạm dừng"]` row becomes a **section heading `## Tạm dừng`** (single-non-empty-cell branch L268–281) — the data row vanishes and a bogus section pollutes category binding. Status matrices / schedules / any non-price table with a merged first column silently lose rows.

**T-5 · Semicolon-delimited tables → 0 entities — CONFIRMED, MEDIUM.**
`_split_cols` handles `|`, `\t`, `,` only (document_stats.py:407–442); the chunk-level delimiter gate also ignores `;` (L981–984). Runtime: chunk `"Tên;Giá\nSerum A;500000"` → `parse_table_chunks` → `[]`. EU-locale CSV (semicolon standard where comma is the decimal mark) produces **no stats index at all**, silently.

**T-6 · Dotted dates / IPs / version strings parse as PRICES — CONFIRMED, HIGH.** See F1. Runtime end-to-end: chunk `"Tên,Ngày hết hạn,Giá\nSerum A,31.12.2026,500000"` → `ParsedEntity(price_primary=31122026, price_secondary=500000, attributes={'Ngày hết hạn': 31122026,…})` — the expiry **date became price_primary** (first-money-wins, document_stats.py:675–679), demoting the real price. Poisons `aggregate_summary` min/max and every price-range/superlative SQL answer. ("ngay het han" binds no role → the unknown-column pure-money fallback L667–672 fires.)

**T-7 · Space-grouped thousands broken — docstring contradicts code — CONFIRMED, HIGH.**
Docstring L16 declares space a thousands separator; `_NUMERIC_RE` (number_format.py:89–91) excludes spaces. Runtime: `parse_money_vn("1 600 000") == 1`; `"1 600 000 đ" == 1` (bare `đ` also missing from `_SUFFIX_RE` L81–85, though the converter's `_MONEY_UNIT_RE` has it — dual-oracle drift). Ingest: 1 < 10 000 floor → price silently dropped. Query (floor 0): a spaced bound yields `price_max=1` → stats filter matches nothing → falls back to vector (degraded). French-locale / Excel-display exports use exactly this format.

**T-8 · Unit-suffix ambiguity: `m` = million, IGNORECASE — measurements become prices — CONFIRMED, MEDIUM.**
`_SUFFIX_MULT["m"]=1e6` + `re.IGNORECASE` (number_format.py:48–57, 81–85). Runtime: `"cao 1,8 m"` → 1 800 000; `"chiều dài 30 m"` → 30 000 000; `_is_pure_money("30 m") == True`. Spec/dimension columns in meters (real estate, furniture, construction tenants) fabricate prices inside the floor/ceiling band.

**T-9 · Transposed / pivot catalogs — no transpose detection anywhere — FACT (by absence).**
`_AGGREGATE_TOKENS` (document_stats.py:220–224) stops `Giá`/`Tổng tiền` label rows becoming entities (good), but a fully transposed catalog (one COLUMN per product, one ROW per attribute) yields zero entities; neither module attempts transpose recovery. Degrades to vector-only for such sheets.

**T-10 · Long/real-world headers break label detection — FACT (code shape).**
`_is_label_like` caps at `DEFAULT_TABLE_LABEL_MAX_CHARS=40` chars (`_13:31`) **and ≤ 6 words** (tabular_markdown.py:90); `_looks_header` needs a label-like majority (L102). "Tên sản phẩm/dịch vụ áp dụng khuyến mãi" (7 words) or "Giá dịch vụ đã bao gồm VAT (nghìn đồng)" (>40 chars) fails → header demoted to DATA → T-1 cascade. Both caps are compile-time constants with no per-bot/system_config override (disguised behavior knobs).

**T-11 · Multi-currency: the entire stack is VND-only — FACT.**
No `$ € £ ¥ USD/EUR` handling in number_format/document_stats. Runtime: `parse_money_vn("$50") == 50` → below the 10 000 floor → a USD catalog indexes **zero prices** (range/superlative/price-of-entity routes always fall back to vector); `"USD 1,200"` → 1 200 → same. Buckets `under_500k…above_5M`, floor 10 000, ceiling 500M are VND semantics baked into constants (`_21:57–69`). A sub-10k-VND F&B catalog (8 000đ items) indexes nothing (floor); a real-estate tenant (2 tỷ listings) is gutted by the 500M ceiling. Per-tenant business parameters living as engine constants.

**T-12 · Split-header premerge is vocab-gated — out-of-vocab languages never merge — FACT.**
`_premerge_split_headers` calls `_is_header_row(cols, declared_labels)` **without** `next_is_separator` (document_stats.py:922) — the structural zero-vocab header signal (the col_N-CRUX fix) does not reach the premerge pass. Non-VN/EN split headers merge only if the owner declared labels; otherwise they keep `col_N`.

**T-13 · Two money oracles disagree — FACT.**
Converter `_is_pure_money` floor 0 (`"2024"`→money) vs stats `_is_header_row` using the 10k-floored wrapper (`"2024"`→not money) (document_stats.py:377, 300–302). The same row can be DATA to the converter and header-candidate to the extractor — the "dual-oracle drift" the module itself warns about (L339–345) persists at the money-concept level.

**T-14 · KNOWN LIMITATION (owner decision — do NOT re-fix in engine): numeric column as price.**
A quantity/stock column whose header binds no role produces pure-money cells → `price_primary` (document_stats.py:667–672). The ING-F1 engine fix was **REVERTED by owner decision (commit 6796cd9)**; the sanctioned workaround is per-bot `custom_vocabulary["column_roles"] = {"<header>": "attribute"}`, which suppresses the fallback (L660–663) and is fully wired (ingest_stages_final.py:468–486). Documented as limitation + workaround only.

---

## 3. Findings register

### F1 — `_normalize_literal` does not enforce 3-digit grouping → dates/IPs/versions become prices — **CONFIRMED, HIGH (T1)**
- `number_format.py:122–127`: multi-separator branch `if all(p.isdigit()): return float("".join(parts))` — the NUMBER STANDARD's "groups of EXACTLY 3 digits" (docstring L16) is not implemented.
- Runtime: `31.12.2026 → 31 122 026`, `192.168.1.1 → 19 216 811`, `1.2.3 → 123`; end-to-end a date column became `price_primary` (T-6).
- Failure: wrong `price_primary`, poisoned `summary_json` min/max + buckets, wrong range/superlative SQL answers — a grounded-but-misinterpreted numeric HALLU class.
- Fix tier: number layer — require every group after the first to be exactly 3 digits in the multi-separator branch (preserves "1.499.000").

### F2 — VI routing seed token `"tu"` + substring matching → "tư vấn … 300k" misrouted as a ≥300k range filter — **CONFIRMED, HIGH (T1)**
- Data: `_VI_ROUTING_SIGNALS.above_tokens = (…, "tu", …)` — i18n.py:227. Consumer: `query_range_parser.py:259` `if token in folded` (substring, not word-boundary).
- Runtime: `parse_range_query("tư vấn gói 500k cho mình") → RangeFilter(price_min=500000, confidence=0.85)`; `"em muốn được tư vấn combo 300k" → price_min=300000`. Both clear `RANGE_QUERY_MIN_CONFIDENCE=0.7` (`_21:77`) → stats-SQL route with a wrong ≥ bound instead of entity lookup / vector. "tư vấn" is among the most frequent VN commerce stems. Same class plausible for `"hon"` (HYPOTHESIS).
- The `vi` seed is deliberately "byte-identical to legacy" (i18n.py:190–195) — the bug is inherited, now pinned in data.

### F3 — Headerless tables: first data row becomes header of every row chunk — **CONFIRMED, HIGH (T1)** — T-1; `tabular_markdown.py:342–346` + `:390–392`.

### F4 — Space-grouped thousands parse as their first digit — **CONFIRMED, HIGH** — T-7; `number_format.py:16` vs `:89–91`; bare `đ` missing from `_SUFFIX_RE` (`:81–85`).

### F5 — `m`/`k` suffixes swallow measurements — **CONFIRMED, MEDIUM** — T-8; `number_format.py:48–57,81–85`; `tabular_markdown.py:43–46`.

### F6 — VND-baked business constants; documented system_config override NOT wired — **FACT, HIGH (multi-tenant)**
- T-11 plus: `_09_message_feedback_thumbs_verd.py:139–141` claims "Override at runtime via system_config.price_buckets_vnd … tuple is the fallback when DB row is absent", but grep shows the only consumers are direct constant imports (`document_stats.py:29,1186–1232`) — **no system_config read path exists** for buckets, floor, or ceiling. Doc-vs-code drift + built-but-not-wired override; tenants with USD / sub-10k / >500M price bands have no escape hatch.

### F7 — Merged-cell forward-fill breaks behind a filled col 0; text-only tables lose rows — **CONFIRMED, MEDIUM-HIGH** — T-3/T-4; `tabular_markdown.py:186–211` (break L192, money-gate L203–204), `:268–281`.

### F8 — Semicolon CSV → zero stats entities — **CONFIRMED, MEDIUM (multi-format/locale)** — T-5; `document_stats.py:407–442, 981–984`.

### F9 — Year/number-only header rows rejected (money veto) — **CONFIRMED, MEDIUM** — T-2; `tabular_markdown.py:60–72, 93–102`.

### F10 — P0-3 locale packs for the stats extractor are built-but-not-wired — **FACT, MEDIUM (multi-locale)**
`_25_locale_structure_packs.py:69–82` ships `en`/`ja` discourse/clause opener sets; `_is_discourse_opener(label, lang=…)` supports the param (document_stats.py:98–112) — but all three call sites pass no lang (`:766, :784, :1040`) and `parse_table_chunks` has **no language parameter** (:937–938). Every document, any locale, is guarded with the Vietnamese opener sets; the EN pack is dead code. English prose rows ("However, …") can leak as entity names (HYPOTHESIS for the leak; FACT for the unreachable packs).

### F11 — Split-header premerge vocab-gated (no separator-trust) — **FACT, MEDIUM** — T-12; `document_stats.py:922`.

### F12 — Unknown locale falls back to Vietnamese everywhere — **FACT, MEDIUM (multi-locale)**
- `get_pack()` i18n.py:668–670: unknown language → `_VI_PACK`, whose generator prompt instructs "Trả lời bằng tiếng Việt tự nhiên" (L394) — a `ja`/`fr` bot with no DB pack gets Vietnamese-instructed internal prompts.
- `get_routing_signals()` i18n.py:741–753: unknown locale → **VI signals**, contradicting the module-header claim "an unknown locale resolves to the empty set (no VN leak)" (L96–99). VI seeds carry generic tokens (`min`, `max`, `list`, `count`) + foldable VN syllables ("tu", "hon", "tren") that can substring-match other languages → mis-route risk instead of the promised neutral fall-through. `_EMPTY_ROUTING_SIGNALS` (L378) is reachable only via the JSON-serde default.

### F13 — i18n hard-coded refusal text vs CLAUDE.md MINDSET #3 — **FACT, POLICY**
CLAUDE.md: "Refusal text origin: bots.oos_answer_template … KHÔNG fallback i18n.py hardcoded text — empty string nếu bot không set." Yet `_VI_PACK.refuse_message` (i18n.py:522–525) and `_EN_PACK.refuse_message` (:655–658) carry full customer-facing refusal sentences served as **tier 6** of `OosTemplateResolver` (oos_template_resolver.py:13,135) when the owner sets nothing. The dataclass's own comment (i18n.py:169–173: "Empty default means the resolver returns ''") contradicts the seeds. Needs an explicit carve-out (ADR) or the seeds emptied.

### F14 — Constants package: silent last-writer-wins duplicates — **CONFIRMED, MEDIUM (T3/health)**
- `DEFAULT_PRICE_BUCKETS_VND` + `DEFAULT_PRICE_MIN_VND` defined twice: `_09:141–150` and `_21:57–64` (only `_21` adds `DEFAULT_PRICE_MAX_VND`). Import chain makes `_21` win; editing `_09` changes nothing — drift trap.
- `DEFAULT_CHARS_PER_TOKEN_ESTIMATE`: `_04:128` `Final[float] = 4.0` vs `_15:40` `Final[int] = 4`. Runtime-confirmed the package exports the **int** from `_15`.
- Triplicates: `RRF_K`/`DEFAULT_RRF_K`/`DEFAULT_LEXICAL_RRF_K` (=60: `_04:150`, `_00:203`, `_17:68`); `SEMANTIC_CACHE_THRESHOLD`/`DEFAULT_SEMANTIC_CACHE_THRESHOLD`/`DEFAULT_CACHE_SIMILARITY_THRESHOLD` (=0.97: `_04:151`, `_05:91`, `_04:15`). `Final` + star-import gives no redefinition protection; a single-definition pin test would close the class.

### F15 — Dead code / orphan constants — **FACT, LOW-MEDIUM**
- `_LEGACY_ALL` (`_09:169–739`, 571 lines) — zero references anywhere (grep src/scripts/tests). Looks like an `__all__` but isn't.
- `DEFAULT_BOT_ID = "1774946011723"` + `DEFAULT_CONNECT_ID = "test-user"` (`_03:72–73`) — zero usages outside constants (grep). The value is a real-looking external bot slug in tracked code — brushes the tenant-identifier-literal ban; delete.
- `_STATS_URL_NOISE_RE` contains the literal `auditcontext` (`document_stats.py:58–61`) — a vendor/corpus-specific URL-param fragment inside an engine regex; borderline vs domain-neutral.

### F16 — Disguised behavior knobs among constants — **FACT, MEDIUM**
Most of the package is genuinely technical (timeouts, CB, pools — compliant). These, however, change per-tenant answer behavior with no override path:
- `DEFAULT_TABLE_LABEL_MAX_CHARS=40` (`_13:31`) + inline 6-word label cap (`tabular_markdown.py:90`) + 8-word title cap (`:277`) — header/section geometry (T-10).
- `DEFAULT_TABLE_GAP_ROWS=2` (`_13:36`) — table-boundary semantics.
- `DEFAULT_PRICE_MIN_VND / MAX_VND / BUCKETS` (`_21:57–69`) — F6.
- `DEFAULT_STATS_ATTR_MAX_CHARS=120 / MAX_WORDS=12` (`_21:129–137`) — drops long attribute values from the synthetic stats chunk; "Override via system_config" claimed at `_21:137` (unwired-override pattern — HYPOTHESIS for this key, same smell as F6). `DEFAULT_STATS_ATTR_MAX_CHARS` also doubles as the **name-length reject** in `_extract_entity_from_row` (document_stats.py:709, 761) — one constant, two unrelated semantics.

### F17 — Multi-format parity: structured-table grammar covers only XLSX / Sheets / DOCX-tables — **FACT, MEDIUM (multi-format)**
`rows_to_structured_markdown` callers: excel_openpyxl_parser, google_sheets_parser, docx_parser only (grep §1.2). PDF/HTML/PPTX tables ride the kreuzberg-markdown path; raw CSV rides `table_csv` chunking (`_11:28`) — none get the section-binding / split-header / row-atomic treatment. CLAUDE.md declares every format first-class with "một output markdown-CÓ-CẤU-TRÚC thống nhất"; the crux grammar is format-siloed today — a PDF price table's quality bar is kreuzberg's, not this grammar's.

### F18 — `_STATS_SECTION_LEAD_RE` false-drops roman-numeral-lead codes — **HYPOTHESIS, LOW**
`^([IVXLCDM]+\s*[/.)]…` (document_stats.py:75–77): a product code "VI/PL-2" or "XL/45" matches the section-enum shape → name rejected (L764) → row dropped. Shape collision for codes whose head letters are all in IVXLCDM. Not reproduced against a real corpus.

### F19 — Loadtest refuse patterns are Vietnamese-only — **FACT, LOW (test-health)**
`DEFAULT_LOADTEST_REFUSE_PATTERNS` (`_15:155–197`): ~40 VN patterns, no EN set. EN-locale bots' load tests undercount refuses → REFUSE_GAP invalid for non-VN bots. Test-side only, but declared the SSoT for all harnesses.

### F20 — Compliance positives (for the record)
- `math_lockdown` import in `orchestration/nodes/persist.py:149` is **decide-only** (skip numeric answers from the cosine cache); it never alters the answer — no sacred-#10 violation in this scope; guard_output.py:67 documents the override removal.
- Version-ref scan: stream subjects `*.v1` are wire-protocol topics (documented exception, `_21:26–27`); `DEFAULT_EMBEDDING_FALLBACK_VERSION="v1"` (`_05:67`) is a data value — borderline, not a code identifier. No `_v2` files/classes in scope.
- Broad-except: none in the four logic modules (narrow `csv.Error/StopIteration`, `ValueError/TypeError` only — document_stats.py:438, i18n.py:712).
- Zero-hardcode: logic modules import magic numbers from constants; remaining inline literals carry `noqa: PLR2004` tied to structural minimums (≥2 cells etc.) — acceptable, though the 6/8-word caps deserve promotion (F16).
- Tenant isolation: these modules are correctly stateless/pure; tenant scoping is done by the caller (`ingest_stages_final.py:472–474` passes `record_tenant_id` into the bot lookup).

---

## 4. Multi-axis summary

| Axis | State | Anchors |
|---|---|---|
| **multi-doc** | Stats index + `aggregate_summary` strictly per-document (`documents.summary_json`); no cross-doc price-conflict handling in this layer — two docs disagreeing on one item's price both index and both surface. | document_stats.py:1149–1208 |
| **multi-bot** | Tier-2 `column_roles` honored end-to-end (good). Price floor/ceiling/buckets, label geometry, attr caps are constants — bots cannot tune (F6, F16). | ingest_stages_final.py:468–486; `_21:57–69` |
| **multi-format** | Structured-table grammar = XLSX/Sheets/DOCX only (F17); `;`-CSV drops out entirely (F8). | §1.2 grep; document_stats.py:407–442 |
| **multi-tenant/locale** | VND-baked money engine (F6); stats locale packs unreachable (F10); unknown locale → Vietnamese prompts + VI routing signals (F12); VN-only refuse patterns in the harness (F19). | i18n.py:668–670, 750–753 |
| **T1 smartness** | F1/F2/F3/F4 are live wrong-answer/mis-route classes on realistic inputs — all runtime-reproduced. | §2.2 outputs |
| **test-health** | None of the confirmed repros (dotted-date→price, "tư vấn 500k" mis-route, headerless mis-binding, space thousands, "30 m") has a pin test — each §2.2 snippet is a ready failing test. | — |

## 5. Suggested fix order (engine-layer, all measurable; excludes T-14 by owner decision)
1. **F1** enforce exact-3-digit grouping in `_normalize_literal`'s multi-separator branch (+ pins: dates/IPs/versions vs "1.499.000").
2. **F2** word-boundary matching for below/above tokens in `query_range_parser` (or contextualize the bare "tu"/"hon" seeds) — measure on the VN probe set.
3. **F3** in `split_markdown_to_row_chunks`, promote a pipe row to header **only when a separator row follows** (the converter always emits one for real headers); bare-row tables then chunk as standalone rows instead of inheriting a fake header.
4. **F4** allow space-grouped digits in `_NUMERIC_RE` with exact-3 validation; add bare `đ` to `_SUFFIX_RE`.
5. **F6/F16** actually wire `system_config.price_buckets_vnd` (+ floor/ceiling, attr caps) and thread per-bot values through `parse_table_chunks`/`aggregate_summary`.
6. **F10** thread the detected document language into `parse_table_chunks(..., lang=…)`.
7. **F14/F15** delete `_LEGACY_ALL` + `DEFAULT_BOT_ID`; dedupe double-defined constants; add a single-definition pin test for the constants package.
