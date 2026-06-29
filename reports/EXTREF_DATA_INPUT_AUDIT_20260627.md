# EXTREF DATA-INPUT AUDIT — Header/Table detection: 4 ref + our-code

Ngày: 2026-06-27. Scope: cách 5 nguồn control data-input (parse → chunk → header/table detect), đối chiếu với code mình. Mindset CLAUDE.md: domain-neutral TUYỆT ĐỐI, no-hardcode, no per-bot/per-domain logic in core, root-cause + expert-solution, evidence `file:line`.

ROOT SYMPTOM (sự thật, có evidence): `col_N` rubbish (`col_0`, `col_1`...) thay cho header thật trên ~9 docs / 3 bot. Đáp án đúng = label thật của header (vd `MARKS | CARGO DESCRIPTION | NGÀY VỀ`). Code mình trả = `col_{idx}`. Diff: header KHÔNG nằm trong vocab VN-thương-mại → `_is_header_row` trả False → role unbound → fallback `col_N`.

---

## 1. Cách mỗi RAG-product control input

| Ref | Parse → Chunk | Header/Table approach | Hardcode vocab? | Domain-neutral | Multilang |
|---|---|---|---|---|---|
| **RAG-Anything** | MinerU layout model → typed `content_list` (type, không vocab) → modal processors → LLM semantics | STRUCTURAL từ parser layer HOẶC LLM semantic task; column-meaning hỏi LLM (`table_prompt`); render pipe generic; runtime language registry (`prompt_manager` en+zh+any ISO) | **NO** | YES | YES |
| **tldw_server** | parse → structure-tree → chunk qua Strategy registry | **100% POSITIONAL**: table = pipe-line + separator `^[\s\|:\-]+$` + pipe-lines; header = `lines[0]` (row trên separator); cell giữ verbatim; placeholder `Col{i+1}` CHỈ khi thật-sự không có header row; LLM không tham gia detect | **NO** (zero frozenset, zero vocab) | YES | YES (vocab chỉ drive TOKENIZATION: jieba/fugashi/konlpy theo Unicode range) |
| **adaptive-chunking** (LREC 2026, Ekimetrics) | parse → canonical JSON contract (typed blocks + char-offset `split_points` + `titles`) → RecursiveSplitter (length-driven, no vocab) → score 5 metric → select best/doc | **STRUCTURAL + POSITIONAL**: header-role từ parser block (Azure DI / Docling label / PyMuPDF regex `^#{1,6}`); ExcelParser PROMOTE first-row → header positionally; `col_{i}` CHỈ khi cell NaN/blank/dedup, KHÔNG khi "fail vocab" (không có vocab để fail); large table split theo row + re-emit header | **NO** (grep src: no name/price/category set) | YES | YES |
| **open-notebook + llama** | delegate parse → 1 markdown → structural header-aware split (Markdown/HTML splitter) + size-cap; no cell extraction | **PURELY STRUCTURAL by SYNTAX**: header = `^#{1,6}\s+` count + `<h[1-6]>`/`<table>` tag; KHÔNG tokenize cell, KHÔNG match label-word (grep price/name/category = 0 hit); table rows giữ verbatim trong section chunk; llama = word-count chunk, không có khái niệm header | **NO** | YES | YES (`#` heading + `<h2>` language-agnostic) |
| **our-code** | Sheet → CSV → `rows_to_structured_markdown` [converter, structural] → markdown `## title` + `\| h \| h \|` + `\| --- \|` → persist → ingest `parse_table_chunks` [extractor] RE-PARSE từ đầu, vocab-gated | **MIXED & DECOUPLED**: converter structural (`_looks_header` shape-based, no vocab) NHƯNG extractor authoritative lại VOCAB EXACT-TOKEN (`_is_header_row` cần `normalised in _HEADER_EXACT_TOKENS`) + PRICE-CENTRIC (any cell parse money → not-header) | **YES** (7 word-set VN/EN, lines 155-205) | **PARTIAL** | **NO** |

NHẬN ĐỊNH: cả 4 ref đều hội tụ về MỘT nguyên lý — **"is-this-a-header?" là QUYẾT ĐỊNH CẤU TRÚC (vị trí/cú pháp/block-role), KHÔNG phải quyết định TỪ VỰNG.** Mình là nguồn DUY NHẤT gate header bằng vocab → cũng là nguồn duy nhất fail multilang/multi-domain.

---

## 2. CODE MÌNH chưa chuẩn ở đâu (file:line + sacred bị vi phạm)

**V1 [ROOT CAUSE] `document_stats.py:275-300` — `_is_header_row` gate bằng vocab.**
`:298` → `if normalised in _HEADER_EXACT_TOKENS or normalised in declared_labels`. Header ngoài word-list VN-thương-mại (`MARKS|CARGO DESCRIPTION|NGÀY VỀ`, mọi domain/ngôn ngữ khác) → KHÔNG phải header → role unbound → `col_N`.
- Vi phạm: **Domain-neutral TUYỆT ĐỐI**, **multilang**, **no per-domain logic in core** (thêm domain = sửa core, vỡ Open-Closed).

**V2 `document_stats.py:155-205` — 7 hardcode word-set VN/EN.**
`_NAME_COL_TOKENS` (155-161), `_CATEGORY_COL_TOKENS` (162-168), `_PRICE_COL_TOKENS` (169-175), `_ALIASES_COL_TOKENS` (181-184), `_HEADER_EXTRA_TOKENS` (186-188), `_HEADER_EXACT_TOKENS` union (191-194), `_AGGREGATE_TOKENS` (201-204). Plus VN-grammar prose filter `_STATS_DISCOURSE_OPENERS` (75-77) + `_STATS_CLAUSE_OPENER_FIRST` (78-80).
- Vi phạm: **Zero-hardcode**, **Domain-neutral** (per-industry + per-language coupling).

**V3 `document_stats.py:294` + `parse_money_vn` — header/data discrimination PRICE-CENTRIC.**
`:294` row có ANY cell parse money → "not a header". Corpus không có cột tiền KHÔNG BAO GIỜ detect được là table. `ParsedEntity.price_primary/secondary` (269-270) + `aggregate_summary` (1040) toàn price.
- Vi phạm: **Domain-neutral** (giả định mọi table = catalog có giá), **T1-Smartness** (table phi-giá bị mù).

**V4 `constants/_21_streaming_upload_wb_2_p1_5.py:64,69` — `PRICE_MIN_VND=10_000`, `PRICE_MAX_VND=500_000_000`.**
Bake magnitude VND vào parser. Cột numeric = count/date/USD/percent bị mis-bucket hoặc floor-reject. `number_format.py:46-81` VN-only currency suffix multiplier.
- Vi phạm: **Zero-hardcode** (magic magnitude window), **Domain-neutral**.

**V5 `document_stats.py:899,917` — KHÔNG trust markdown separator.**
Converter ĐÃ emit `\| --- \|` và ĐÃ label header line, nhưng extractor vứt bỏ: separator chỉ bị `continue` (`_is_separator_line` :303 → True → skip), KHÔNG dùng làm positional signal "line trên = header". Header bị RE-JUDGE bằng vocab thay vì tin vị trí.
- Vi phạm: **root-cause / surgical** (2 stage decoupled, drift), **Simplicity** (re-detect cái converter đã biết).

Ghi chú DUPLICATE-DEFECT: `col_N` fallback ở `:554,:595,:623,:627` (extractor) VÀ `tabular_markdown.py:120,123` (converter). `_premerge_split_headers` (:804) structural/domain-neutral đúng — nhưng chỉ fire SAU khi `_is_header_row` pass, nên header non-vocab không bao giờ tới được nó.

---

## 3. PATTERN nên học (domain-neutral, KHÔNG vocab)

Cả 4 ref đồng thuận — port các pattern sau (EVOLVE, không rewrite):

1. **Trust markdown `\| --- \|` separator → row ngay trên = header (positional).** (tldw `structure_aware.py:543-545` header=`lines[0]` trên separator; adaptive `parsing.py:594-599` promote first-row; open-notebook syntax-anchor). Mình ĐÃ có `_is_separator_line` (:303) + converter ĐÃ emit separator → chỉ cần DÙNG nó làm anchor.

2. **Structural header = all-text-label row trước data-rows** (zero vocab). tldw: row trên separator + cell verbatim. open-notebook: row là header khi KHÔNG cell nào parse value. Mình ĐÃ có detector đúng: `tabular_markdown.py:90-99 _looks_header` (≥2 cell, no pure-money, majority short label-like) — promote thành SSoT cho cả converter + extractor.

3. **Block-Integrity guard (ground-truth-free regression gate).** adaptive `metrics.py:264-307`: % structural block KHÔNG bị split cắt, tính thuần từ `split_points` + chunk offset, có `tolerance_chars`. Áp vào ingest: assert không chunk-boundary nào rơi trong table/heading block → deterministic, domain-neutral, bắt được class bug col_N/table-shatter.

4. **Multi-row merge structural.** adaptive split table theo row + re-emit header mỗi part (`_split_by_rows` 527-565; Docling `_split_table_markdown` 926-953 re-prepend header+separator). Mình ĐÃ có `_premerge_split_headers` (:804) structural — chỉ cần để nó chạy độc lập với vocab gate.

5. **Col-role = HINT optional, tách khỏi DETECTION.** RAG-Anything: role naming là enrichment ON TOP, không phải precondition. Vocab CHỈ sống ở per-bot `column_roles`/`custom_roles` (ADR-0006 Tier-2, đã có :383+) là AUTHORITATIVE, built-in vocab demote thành Tier-1.5 hint. Header không infer được role → surface dưới REAL header label, KHÔNG `col_N`.

6. **Multi-lang + N+1 qua structure/template, không content-vocab.** tldw: language chỉ drive tokenizer (Unicode-range detect, graceful ImportError fallback). RAG-Anything: runtime language registry. Mình: mirror `language_packs` DB; thêm domain/ngôn-ngữ = thêm config, KHÔNG sửa core (N+1 = thêm 1 row config).

7. **Value-shape role inference (domain/lang-neutral).** Cột đa số pure-money = price; cột non-numeric short-text đầu = name; cột low-cardinality = category. Infer theo phân bố giá trị thực, không theo header-word (TATR-style cell-role mà comment mình cite nhưng chưa implement).

---

## 4. FIX đề xuất cho `document_stats.py` (generic, bỏ hardcode)

KHÔNG sửa code lần này — đây là đề xuất. Mọi fix structural, no vocab, EVOLVE.

**F1 [highest leverage] TRUST THE SEPARATOR.** Trong `parse_table_chunks`, line ngay TRÊN `\| --- \|` separator = header positionally (structural fact converter đảm bảo). `_is_header_row` vocab-path tụt xuống fallback CHỈ cho raw CSV không-separator. Một mình F1 giết col_N cho toàn flow Sheets→markdown.

**F2 Thay exact-vocab detection bằng `_looks_header` đã có ở `tabular_markdown.py`.** Unify: converter + extractor dùng MỘT header oracle (kill drift V5). Detector này domain-neutral + multilang qua Unicode-letter test.

**F3 Col-role structural-first.** NAME = cột đầu majority non-numeric/non-money trên data-rows; VALUE = cột majority-numeric; CATEGORY = cột low-cardinality. Per-bot `custom_roles` (Tier-2) = AUTHORITATIVE override; built-in vocab = optional Tier-1.5 hint, KHÔNG phải gate.

**F4 Decouple stats khỏi price.** Generalise `aggregate_summary` → numeric-column stats keyed theo header label (không phải hardcode price schema). Drop magnitude floor `PRICE_MIN/MAX_VND` khỏi header detection; giữ nó CHỈ cho owner-declared role `price`.

**F5 Never emit `col_N` khi header label tồn tại.** Header không infer được role + owner không declare → surface dưới REAL header label (converter đã preserve). `col_{idx}` CHỈ cho cell thật-sự blank/NaN (đúng pattern adaptive `:512-514`).

ĐỘ TIN: F1–F2 là root-cause fix (structural), F3–F5 hardening. Cả 5 dùng lại helper ĐÃ CÓ (`_is_separator_line`, `_looks_header`, `custom_roles`, `_premerge_split_headers`) → minimal surgical. **CHƯA VERIFY** — cần (a) debug-trace 1 doc `col_N` qua converter→extractor xác nhận separator có mặt, (b) load-test/eval re-ingest 9 docs đo header recovery rate trước khi tuyên bố fixed.

NOTE evidence-integrity: adaptive-chunking + RAG-Anything + tldw + open-notebook đều checkout `_external_refs/` có file:line thật. `program/context/*` là Phase-1 report của mình (Docling/TATR/MixRAG chỉ cited trong comment, không checkout). Structural pattern trên recoverable từ CHÍNH `tabular_markdown.py` của mình — nó đã domain-neutral, cần promote thành SSoT mà `document_stats.py` đang bypass.
