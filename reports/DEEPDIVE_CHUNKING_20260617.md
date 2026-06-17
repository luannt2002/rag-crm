# DEEPDIVE — Chunking pipeline audit vs AdapChunk / RAG-Anything (2026-06-17)

READ-ONLY audit. No `src/` files edited. Every claim carries `file:line` evidence.

Scope: `src/ragbot/shared/chunking/{__init__,analyze,strategies,csv_chunker,blocks}.py`,
ingest wiring (`document_service/ingest_stages.py`), reference designs in
`_external_refs/adaptive-chunking` (Ekimetrics "AdapChunk") and `_external_refs/RAG-Anything`.

---

## 0. TL;DR

- The **live ingest path only calls `smart_chunk(content: str)`** (`ingest_stages.py:679`). The
  Block-native `smart_chunk_atomic(list[Block])` has **zero call sites in `src/`** (asserted by
  `tests/unit/test_block_feed_s1_plumbing.py:51`) — it is dormant Wave-B2 scaffold.
- **Atomic-block protection exists but is OFF by default** (`DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False`).
  Even when ON it operates on the flattened text string, not parser Blocks.
- **Narrate-then-embed exists but is OFF by default** (`DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`,
  `narrate_provider="null"`). Tables/formulas/images are embedded as raw text.
- **HDT breadcrumb (structural_path) is REAL and working** (`strategies.py:_chunk_hdt` lines 277-357).
- The customs-manifest + FAQ shatter are produced by the **`table_csv` row-as-chunk path**
  (`csv_chunker.py:_chunk_table_csv_with_context` / `_chunk_table_csv`) and are explained in §2.

---

## 1. WHAT WE HAVE vs MISS (AdapChunk 7-layer + RAG-Anything table)

| AdapChunk / RAG-Anything concept | Status in our code | Evidence (file:line) |
|---|---|---|
| **L1 Block Detection & Tagging** (typed blocks text/table/formula/image/code) | PARTIAL — regex line classifier on flattened text, NOT parser-native | `blocks.py:_split_into_blocks_with_atomic` 184-276; parser Blocks exist (`analyze_document_blocks` 296-354) but ingest never feeds them (§4 below) |
| **L2 Feature Extraction** (profile: headings/tables/formula/image/code counts, ratios) | HAVE | `analyze.py:analyze_document` 202-293 (adds `formula_count`/`image_count`/`code_block_count`/`heading_ratio`) |
| **L2b Intrinsic metrics RC/ICC/DCC/BI/SC** (Ekimetrics 5-metric selector) | HAVE but **OFF/optional** | `analyze.py:select_strategy` 391-406 gated on `ekimetrics_enabled` (default False); impl in `shared/intrinsic_metrics.py` |
| **L3 LLM Strategy Selector** | MISS (rule-based only, by design — domain-neutral) | `analyze.py:select_strategy` 408-492 is a weighted rule scorer, no LLM |
| **L4 Rule Cross-check** (post-selector overrides) | HAVE, **ON** | `analyze.py:apply_cross_check` 527-664; `DEFAULT_ADAPCHUNK_L5_CROSS_CHECK_ENABLED=True` |
| **L5 Chunking Executor w/ atomic-block protection** (TABLE/FORMULA/IMAGE/CODE never split) | HAVE but **OFF by default** + operates on text not Blocks | `__init__.py:_smart_chunk_with_atomic_protect` 261-347, `_emit_atomic_block` 219-258; flag `DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False` |
| **L6 Narrate-then-Embed** (LLM describes non-prose; embed narration, keep raw) | HAVE but **DORMANT** | port `application/ports/narrate_port.py`; dispatch `narrate_dispatch.py:107`; `DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`, provider `"null"` |
| **L7 Embedding + metadata** (`structural_path`, `original_content`) | PARTIAL | `Chunk` entity carries `structural_path`/`original_content` (`smart_chunk_atomic` path) BUT live `smart_chunk` only emits `{content, metadata.parent_headings}` (`__init__.py:511-518`); `original_content` not preserved on live path |
| **HDT breadcrumb** `[Chương > Mục > Điều]` prefix | HAVE, working | `strategies.py:_chunk_hdt` 277-357 (path stack 308-322, prefix 311); `extract_structural_path` 24-39 |
| **table_csv row-as-chunk + header attach per row** | HAVE | `csv_chunker.py:_chunk_table_csv` 22-56 (`f"{header}\n{row}"` line 48); `_chunk_table_csv_with_context` 251-354 |
| **table_dual_index** (whole-table group chunk + row chunks → aggregation) | HAVE | `csv_chunker.py:_chunk_table_dual_index` 357-434 |
| **RAG-Anything Technique 1: per-table LLM description** | **MISS** (consciously reverted) | `csv_chunker.py:33-39` docstring: a key:value render "measured neutral-to-slightly-negative … real fix is a per-table LLM description (RAG-Anything Technique 1)" — NOT implemented |
| **Per-image VLM caption / per-formula explanation** | MISS on live path | narrators exist (`narrate/formula_narrator.py`) but dormant; image narrator routes only under narrate flag |
| **Boilerplate de-weight** (repeated header/Chinese boilerplate down-weighted) | **MISS** | no de-dup/de-weight anywhere in `csv_chunker.py`; header is re-prepended verbatim to every row (line 48, 331) |

### Direct answers to the three pointed questions

1. **Does the chunker protect atomic blocks (never split a table row)?**
   - For CSV/`table_csv` strategy: YES — each data row = one chunk; oversized rows are kept whole
     (`csv_chunker.py:49-55`, only a warning, no cut). So a single CSV row is never split.
   - For markdown/pipe tables under `recursive`: tables ≤ `3×chunk_size` kept whole; larger ones split
     by **row groups with header re-prepended** (`strategies.py:142-168`) — row boundaries respected.
   - For other strategies: only protected when `formula_image_atomic_protect_enabled` is ON
     (`__init__.py:428`, `_emit_atomic_block` 245-248), default **OFF**. With the flag OFF and a
     non-recursive strategy, tables are isolated via `_split_into_blocks` (`__init__.py:459-473`) — still
     routed to `_chunk_recursive_with_tables`, so rows stay intact in practice. **Verdict: table rows
     are not split; but FORMULA/IMAGE/CODE atomicity depends on a default-OFF flag.**

2. **Does it attach the column HEADER to each table-row chunk?**
   - YES — `f"{header}\n{row}"` (`csv_chunker.py:48`, `331`). BUT `header` = `_doc_table_header(lines)`
     = the **first CSV-shape line in the doc** (`csv_chunker.py:217-239`), which is only correct when
     row 0 is the real header. See §2 for the manifest failure where it is not.

3. **Narrate-then-embed — present and ON or OFF?**
   - PRESENT, **OFF**. `DEFAULT_NARRATE_THEN_EMBED_ENABLED=False`; default `narrate_provider="null"`
     → `NullNarrateGenerator`; even when a `NarrateService` is constructed (`document_worker.py:391-423`)
     it short-circuits to passthrough. Tables/formulas/images are embedded as raw text on the live path.

---

## 2. ROOT CAUSE — manifest CSV shatter & FAQ 5287-char balloon

Both cases flow through the **`table_csv` fast path**: `analyze.py:select_strategy` line 424
(`if is_csv and total_headings == 0 and vn_markers == 0: return (table_strategy, 1.0)`) →
`smart_chunk` line 440-449 → `_chunk_table_csv_with_context` (`csv_chunker.py:251`), which with the
header/footer flag OFF (`DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED=False`) delegates to
`_chunk_table_csv` (`csv_chunker.py:288-291`).

### Case A — customs manifest: wrong header + boilerplate repeat + embedded-newline orphan

Observed chunk: `1.唛头,2.货物描述,⏎,195/65R15 91H CITYTRAXX G/P,28-thg 11`
and column label `NGÀY VỀ` ends up in a SEPARATE ~48-char chunk; Chinese boilerplate
`1.唛头,2.货物描述` repeats in every chunk.

Three compounding root causes, all in `csv_chunker.py`:

1. **Line-splitting destroys multi-line cells (the `⏎` orphan).**
   `_chunk_table_csv` and `_chunk_table_csv_with_context` both do
   `lines = [ln for ln in text.split("\n") if ln.strip()]` (`csv_chunker.py:42`, `284`). A spreadsheet
   cell containing an embedded newline (the customs "货物描述" description cell, and the multi-line
   "NGÀY VỀ" header label) is split into **two physical lines**. The fragment that is not CSV-shape
   (`NGÀY VỀ`, 1 comma-free token) fails `_is_csv_shape_line` (`csv_chunker.py:112-126`, needs
   `≥DEFAULT_CSV_MIN_COMMAS=1` comma AND `≥2` non-empty cells), so it is **not joined to its row** —
   it surfaces as a standalone ~48-char orphan chunk. **Root cause = there is no RFC-4180 CSV parse;
   the code splits on raw `\n`, so any quoted multi-line cell shatters.** Immutable cause: `text.split("\n")`.

2. **`_doc_table_header` picks a DATA row as the header.**
   `_doc_table_header` returns the **first CSV-shape line** (`csv_chunker.py:236-239`). In the manifest
   export, the real header is a multi-line/decorated row that is NOT the first comma-rich line, so the
   first qualifying line is a Chinese boilerplate row `1.唛头,2.货物描述,...`. That boilerplate is then
   re-prepended to **every** row chunk via `f"{header}\n{row}"` (`csv_chunker.py:331`). **Root cause =
   "header = first csv-shape line" heuristic mis-fires on manifests whose top rows are decorative
   boilerplate, not column names.** This is the same class as the 2026-06-13 xe-warehouse bug the
   docstring at `csv_chunker.py:222-234` already describes — fixed for *duplicate-data-row* but NOT for
   *boilerplate-as-header*.

3. **No boilerplate de-weight.** Because the boilerplate row is the chosen header, `1.唛头,2.货物描述`
   appears identically in 100+ chunks → embeddings collapse toward that shared prefix (low
   discriminativeness). Nothing de-dups or down-weights it (`csv_chunker.py` has no de-weight path).

Net: the column label `NGÀY VỀ` is divorced from its value `28-thg 11`, the rows carry the wrong
column names, and every chunk shares a boilerplate prefix — a retrieval-killing combination.

### Case B — FAQ table: one mega-cell balloons the row to 5287 chars

Observed: FAQ table columns `question,code,productname,answer,quantity,price,date1,date2,image`;
the `question` column holds **64 spelling-variants**, making one row ~5287 chars.

Root cause (single, simple): **row-as-chunk keeps an oversized row whole.**
`_chunk_table_csv` builds `chunk_text = f"{header}\n{row}"` and when `len > max_chunk_chars`
(`DEFAULT_TABLE_CSV_MAX_CHUNK_CHARS=1500`) it only **logs a warning and keeps the row whole**
(`csv_chunker.py:48-55`; same at `_chunk_table_csv_with_context:331-338`). There is no column-aware
handling: a single cell stuffing 64 variants is treated as one atomic tuple, so the chunk is 5287 chars —
3.5× the cap. The variants (which should be *retrieval keys*) and the `answer` (the actual payload) are
fused into one diffuse embedding. **Immutable cause = no per-column treatment; the "row is atomic"
assumption breaks when one column is a synonym dump.**

---

## 3. CONCRETE MINIMAL FIX LIST (ranked by impact, domain-neutral)

Ranking by retrieval-quality impact (T1) per CLAUDE.md priority. No per-bot hardcode; all thresholds
already live in `system_config`/constants.

1. **[HIGH · Case A core] Parse CSV with a real RFC-4180 parser (quoted multi-line cells).**
   Replace `text.split("\n")` row-splitting in `_chunk_table_csv*` (`csv_chunker.py:42,284`) with
   `csv.reader`/quoted-field aware parsing so multi-line cells (description, "NGÀY VỀ") stay in their
   row. Kills the 48-char orphan and re-attaches the column label to its value. Highest impact, lowest
   surface (one detector swap, behind existing table_csv path).

2. **[HIGH · Case A header] Robust header detection (not "first csv-shape line").**
   `_doc_table_header` (`csv_chunker.py:217-239`) should detect the header by column-name signature
   (mostly-alpha cells, no numeric/price tokens, comma-count == modal data-row comma-count) instead of
   "first comma-rich line", so decorative boilerplate (`1.唛头,2.货物描述`) is never chosen as header.
   Fixes wrong-column-names on every manifest row.

3. **[HIGH · Case B] Column-aware row handling for mega-cells → dual-index variants → BM25.**
   When one cell dominates a row past `max_chunk_chars` (`csv_chunker.py:49`), split that cell's
   synonym list into a **variants/keys field routed to lexical (BM25) retrieval** and keep the
   `answer`/payload as the embedded chunk — i.e. the dual-index pattern (`_chunk_table_dual_index`
   already exists, 357-434) extended to per-column. Matches the prompt's "dual-index variants→BM25".
   Prevents the 5287-char diffuse embedding.

4. **[MED · Case A noise] Boilerplate de-weight / de-dup across row chunks.**
   Detect a prefix line repeated in ≥N row chunks (the boilerplate) and either drop it from the embed
   text or move it to metadata, so per-row embeddings regain discriminativeness. Pairs with fix #2.

5. **[MED · cross-cutting] Turn on RAG-Anything Technique 1 — per-table LLM description (narrate-then-embed).**
   The narrate subsystem already exists but is dormant (§1 L6). Enabling a **per-table description**
   (`narrate_provider="llm"` for TABLE blocks, O(tables) cost) gives each table a semantic NL summary to
   embed alongside row chunks — the documented "real fix" at `csv_chunker.py:33-39`. Bigger lever for
   aggregation/NL queries ("đắt nhất", "dưới 500k") that row text reformatting cannot solve. Higher
   cost/complexity → ranked below the cheap structural fixes; gate per-bot via existing flag.

6. **[LOW · structural] Feed parser Blocks into `smart_chunk_atomic` (Wave B2) + persist `original_content`.**
   Live path flattens to text (`ingest_stages.py:679`) and `_stage_u4_chunk` never reads `ctx.blocks`
   (hard-coded `parsed_blocks=[]`, ingest_stages.py:501). Wiring the Block stream makes atomic
   protection real (not text-regex) and preserves `original_content`/`structural_path` per L7. Largest
   change surface; defer until #1–#4 land.

### CLAUDE.md compliance note
All fixes are retrieval-layer (correct layer for a retrieval bug — not a sysprompt patch),
domain-neutral, zero-hardcode (thresholds in `system_config`), no app-inject/override of LLM answer,
HALLU-neutral. Fixes #1–#4 are low-risk structural; #5 is the SOTA lever but cost-gated per-bot.
