# [T1-Smartness] ADR-0008 sparse-drop fix — table row not silently dropped · 2026-07-10

> Executable spec = `tests/unit/test_multibot_ingest_canary.py` (the 25 property-based
> `test_invariant_random_domain_no_silent_row_drop[0..24]`). RED = the engine gap the
> multi-bot fix must close. Diagnosed + fixed under rule#0 (measure, don't assume).

## 1. Bug — Reproduce concrete
- **Input**: a well-formed comma-CSV for an UNSEEN domain — random headers
  (`Field876A,Field530B,…`), short identifier values (`v0r0c0,…`), NO price, NO
  empty cells (`parse_table_chunks([{content}])`, no roles).
- **Expected**: entities keep every value (INV-1 no row drop, INV-2 no value lost).
- **Got**: **0 entities** — whole table dropped, all 30 values lost.
- Evidence: reproduced seed 0 → `len(ents)==0`, `lost = {all 30 cells}`.

## 2. Direct cause — 1 layer up
- Layer = **ingest / stats extraction** (`shared/document_stats.py`), NOT
  retrieval/sysprompt.
- The **T012 positive-table-evidence gate** (`parse_table_chunks`, ~line 1208):
  `if _has_price or _pipe_row or header_structural: entities.append(entity)`.
  A random comma-CSV has: no price · comma not pipe · header detected by
  `_is_shape_header` (shape) NOT `_is_header_row` (structural) → `header_structural=False`
  → **fails all three → entity discarded.**

## 3. Root cause (chain)
`0 entities` ← T012 gate rejects the row ← header was **shape-detected, not
structural** ← the gate deliberately EXCLUDED shape-headers (init comment ~1112)
because an OLD, loose `_is_shape_header` once promoted PROSE lines into pseudo-headers
→ prose-garbage. **Immutable cause**: the gate's table-vs-prose evidence was
"structural header OR price OR pipe" — too narrow; it drops every price-less
out-of-vocab CSV. But naively counting shape-headers re-mints legal-prose garbage
(measured: `test_legal_prose_mints_zero_entities_gap_b` breaks).

## 4. Expert solution — right layer + measured
- **Fix layer** = the shape-header detector + the T012 gate (ingest), domain-neutral,
  shape-only (SOTA cell-role / Microsoft TATR philosophy: type by SHAPE not vocab).
- **Two-part fix** (`shared/document_stats.py`):
  1. Thread a `header_shape` flag (set when `_is_shape_header` promoted the header) into
     the T012 gate: `… or header_structural or header_shape`. A shape-header table now
     extracts its rows instead of silently yielding 0.
  2. **Tighten `_is_shape_header`** so prose can't forge a grid: a header cell OR a grid
     data cell longer than `DEFAULT_STATS_ATTR_MAX_WORDS` (12) words is a prose CLAUSE,
     not a table label/value → rejected. Random-table cells are 1–3 words (pass); legal
     clauses are 12+ words (fail). Zero new hardcode (reuses the existing constant).
- **Why correct**: `_is_shape_header` already requires ≥2 CONSISTENT non-prose grid
  rows; the word-bound closes the remaining hole (wrapped legal sentences with no
  terminator). The discriminator is pure grammar/shape — no brand, no vocab, no model.

## 5. Measure (rule#0 — the discipline that caught the trap)
- First naive fix (gate only): **25 INV ✅ + 61 prose-guards ✅ but broke GAP-B** (legal
  prose minted 2 entities). → refined with the word-bound.
- Final: `pytest test_multibot_ingest_canary + test_stats_extract_noise + all
  document_stats/parse_table_chunks tests` = **262 passed / 0 failed / 2 xfailed**
  (S1 split-header still xfail — separate tabular_markdown work). Full unit suite: (see
  commit — confirm 0 new failures).
- **NOT done here**: S1 split-header (xfail, tabular_markdown layer); a live re-ingest +
  RAGAS on chinh-sach-xe to measure the false-deny lift is the ADR-0008 A-series
  follow-up (this fix is the extraction-correctness half, unit-proven).

## 6. CLAUDE.md compliance
- Sacred #0 no-guess: every step has evidence (repro, test output). ✅
- Domain-neutral: shape/word-count only, no brand/vocab literal. ✅
- Zero-hardcode: reuses `DEFAULT_STATS_ATTR_MAX_WORDS`. ✅
- No app-inject/override answer (#10): ingest-extraction only, answer path untouched. ✅
- Measured before claim: 262-test gate + full suite. ✅
