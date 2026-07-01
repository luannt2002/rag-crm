# Live Verify — Wave 1 (P0 purge + P1a count + P1 converter) — 2026-07-01

Bot: `chinh-sach-xe | web | xe` (record_bot_id `c6e1fc56…`), tenant `c2f66cb2…`.
Method: legitimate `POST /api/ragbot/sync/documents` (sacred#7 — no psql content mutation),
self-token (owner, tenant-matched) + loadtest bypass. Source = real Google Sheet CSV exports.

## VERIFIED FIXED (evidence)

### P0 — Purge single-source-of-truth
- Re-sync (`replace_documents_for_bot` → `_purge_content_tables`) left **0 duplicate (doc,entity)
  groups** in `document_service_index`.
- `audit_log` **append-only preserved**: 46 → 48 across two re-syncs (old rows intact, +1 per sync).

### P1 — Converter `_normalize_rows` (L1 structure-recovery) — DEFINITIVE
Clean local test through the real `GoogleSheetsParser` + `parse_table_chunks` on the 3 real CSVs:

| Sheet (gid) | entities | col_N | pipe-leak | fabricated price |
|---|---|---|---|---|
| 11111 catalog (295547450) | 208 | **0** | 0 | 0 |
| 2222 shipping (0) | 63 | **0** | 0 | 0 |
| 3333 price/alias (1058146012) | 187 | **0** | 0 | **0** (was `1558013`) |

Root causes fixed: blank-row-after-header (11111), leading-blank + 2-row header (2222),
quoted variant-cell mis-split that fabricated a price from tyre-size digits (3333).
`155/80R13` now → real price **684000**, quantity **214** (from the `price`/`quantity` columns).

### Live functional queries (end-to-end `/api/ragbot/test/chat`)
- "155/80R13 còn bao nhiêu" → **"còn 214 lốp"** (was stale "26").
- "giá lốp 155/80R13" → **"684.000đ/lốp, còn 214"** (was fabricated 1558013).
- "có bao nhiêu loại lốp Landspider" (fresh ctx) → **"137 loại"** (COUNT(*) via B-AGG count dispatch).

Live DB after real-CSV re-sync: col_N **216 → 56**, pipe-leak 0, absurd prices 0, dupes 0.

## KNOWN REMAINING (NOT claimed fixed — honest)

1. **[Phase 2 — L2 chunking] 2222 tiny sheet: 56 col_N.** The 2.6 KB parsed pipe-markdown of a
   small CSV collapses to ONE whole-doc chunk (`ingest_stages.py:382 is_whole_document`; the
   already-parsed pipe-markdown is no longer `_is_csv_format`), so the header + `| --- |`
   separator is lost and `parse_table_chunks` falls back to `col_N`. Fix: when the parser
   produced row-chunks (`ctx.parser_row_chunks`), never override with whole-doc. Low value
   (shipping-date sheet) but a real small-doc/row-chunk interaction bug.

2. **[Phase 3 — Analytical] Multi-turn "how many types" fabrication (HALLU).**
   In an accumulated conversation (155/80R13 turns already in context), "có bao nhiêu loại
   Landspider" invented a non-existent "155/80R13 H/P 725.000đ còn 187" pair. Corpus check:
   **no 725000 price and no 155/80R13 H/P anywhere.** Single-turn answers are clean. This is the
   analytical-aggregate weakness (needs SQL GROUP-BY substrate + capped-honesty), NOT a Wave-1
   regression. HALLU=0 sacred → Phase 3 must land the structured-aggregate path before this bot
   is answer-safe on "how many / list" analytical queries in multi-turn.
