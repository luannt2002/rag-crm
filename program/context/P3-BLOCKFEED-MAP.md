# P3 — BLOCK-FEED END-TO-END MAP (ADR-W3-D1 prep)

> Agent: P3 RESEARCH (READ-ONLY) · Date: 2026-06-10 · Anchor: branch `fix-260604-action-slotmachine-dead-key`, HEAD `7dd1f84`.
> Every claim = `file:line` read this session. No edits to src/alembic/tests. Single file written: this one.
> Settles P2-B §5 Q9/Q11/Q12 + 🐛-A with exact call-graph for the un-flatten ADR.

---

## (1) Block schema — CANONICAL

**Class def:** `src/ragbot/domain/entities/document.py:41-51` — `@dataclass(frozen=True, slots=True) class Block`.

| Field | Type | Default | Note |
|---|---|---|---|
| `type` | `BlockType` (Literal: HEADING/TABLE/FORMULA/IMAGE/CODE/LIST/TEXT — `shared/types.py`) | — | parser-assigned |
| `content` | `str` | — | block text |
| `is_atomic` | `bool` | — | TRUE for HEADING/TABLE/FORMULA/IMAGE/CODE (`kreuzberg_parser.py:67,296`) |
| `context_before` | `str` | `""` | active heading prepended by parser (`kreuzberg_parser.py:286-290`) |
| `context_after` | `str` | `""` | unused by kreuzberg today |
| `page_number` | `int \| None` | `None` | |
| `ocr_metadata` | `dict[str,Any]` | `{}` | carries `kreuzberg_label` |

**Emitter (L1):** `kreuzberg_parser.py:292-303` builds `Block(...)` per element; element-type→BlockType via tuple matcher `:56-93`; atomic flag = `block_type in _ATOMIC_BLOCK_TYPES` (`:67`). Parser returns `ParsedDocument(blocks=list[Block], ...)` `:195-203`.

**Chunk entity (L6 output target):** `document.py:54-81` — `class Chunk` with `narrated_text`, `contextual_prefix`, `original_content`, `block_types: tuple[BlockType,...]`, `structural_path`, identity keys, `content_hash`, `metadata`. This is what `smart_chunk_atomic` emits.

---

## (2) LUỒNG HIỆN TẠI (production) — 2 flatten points, Block stream killed

```
[Upload bytes / source_url]
        │
        ├── worker OCR fallback path (PDF/scan/image):
        │     ocr.parse(source_url) -> ParsedDocument(blocks=list[Block])   document_worker.py:296-298
        │     ★ FLATTEN #1 ★  full_text = "\n\n".join(b.content for b in parsed.blocks)
        │                                                                    document_worker.py:298
        │     -> doc_service.ingest(content=full_text, ...)  (str only; NO raw_bytes, NO blocks)
        │                                                                    document_worker.py:391-403
        │
        └── ingest registry path (Excel/Sheets/DOCX/MD/CSV, raw_bytes present):
              _route_through_parser(raw_bytes) -> (joined_text, parser_row_chunks)  document_service.py:1274-1322
              ★ FLATTEN #2 ★  joined = "\n\n".join(c["content"] for c in chunks ...)  document_service.py:1321
              (parser_row_chunks list[dict] kept as a SIDE-CHANNEL, see §5)

        ▼ (both converge on a single str `content`)
  smart_chunk(content: str, strategy=...) -> list[str]                       document_service.py:2119-2124
     └─ if _atomic_protect_enabled(): _smart_chunk_with_atomic_protect(text=str)  chunking.py:2607-2615
        └─ _split_into_blocks_with_atomic(text: str) RE-DETECTS blocks from markdown  chunking.py:2492
        (FLAG OFF by default: DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False  _00:95)
  merge_orphan_chunks / parser_preserve bypass                                document_service.py:2142-2151
        ▼
  list[str] chunks ──► CR enrich ──► embed-text strategy (raw_only/prefix)  document_service.py:2857-2902
        ▼                                ★ narrate dispatch RE-DETECTS block_type from FLAT text ★
  narrate_dispatch.classify_chunk_block_type -> _split_into_blocks_with_atomic(text)  narrate_dispatch.py:94
        ▼
  embed + INSERT document_chunks
```

**Worker never passes `raw_bytes`/`blocks` to `ingest()`** — it always flattens first (`document_worker.py:391-403`). So the Block stream from the *most structural* engine (kreuzberg) is destroyed at the earliest point (#1) and the OCR path never even reaches the registry side-channel.

**Net consequence (= P2-B 🐛-A/-B root):** the parser already computed `Block.type`/`is_atomic`; both flatten points throw it away; every downstream consumer (`smart_chunk`, `_smart_chunk_with_atomic_protect`, narrate dispatch) **re-derives** block type from markdown via `_split_into_blocks_with_atomic`'s regex `_is_table_line` (`chunking.py:1150`, comma-rule `:253-256`) → ~163/211 prose mis-classified TABLE (P2-B §1 DB evidence).

---

## (3) LUỒNG ĐÍCH (Block-native)

```
parser -> ParsedDocument(blocks=list[Block])
   -> ingest(blocks=list[Block], ...)         # NEW kwarg, defaults None
   -> smart_chunk_atomic(blocks, strategy=..., record_*=...) -> list[Chunk]   chunking.py:2758
        ├─ atomic Block -> _block_to_atomic_chunk (never cut, ctx wraps)       chunking.py:2877-2888
        └─ TEXT run -> _chunk_text_blocks_to_chunks -> smart_chunk(joined str) chunking.py:2918-2947
   -> Chunk.block_types travels into narrate dispatch (NO re-detection)
   -> embed + INSERT
```

**`smart_chunk_atomic` signature** (`chunking.py:2758-2771`): already accepts `blocks: list[Block]`, `strategy`, and full identity (`record_tenant_id/record_bot_id/document_id/embedding_model_version/corpus_version/ingested_at`), returns `list[Chunk]`. **It is feature-complete; it has ZERO production callers** (grep: only `tests/unit/test_smart_chunk_atomic.py`). This is the survivor per P2-B Q12.

**KEY MISMATCH — why it can't be dropped in trivially:** `smart_chunk_atomic` returns `list[Chunk]` (rich entities), but the ingest pipeline from `document_service.py:2125` onward operates on `list[str]` (`chunks`, `cr_raw_chunks`, `enriched_chunks`, `chunks_to_embed`) and runs CR-enrich + embed-text-strategy + narrate + INSERT against those strings. The ADR must choose ONE of:
  - **(A) thin-adapter**: call `smart_chunk_atomic(blocks)` then map `Chunk.original_content`→`list[str]` + carry `Chunk.block_types` into a `block_type_by_idx` dict consumed by narrate dispatch (replaces the `_split_into_blocks_with_atomic` re-detection at `narrate_dispatch.py:94`). Minimal blast radius; keeps the str pipeline. **Recommended for D1.**
  - **(B) full Chunk-native pipeline**: rewrite CR/embed-text/INSERT to consume `list[Chunk]`. Large blast radius (the entire `:2125-3400` ingest tail). Defer post-D1.

---

## (4) GAP CHÍNH XÁC — what must change

| # | Site | Today | Target |
|---|---|---|---|
| G1 | `document_worker.py:296-298` | `parsed = ocr.parse(...)`; flatten `full_text` | keep `parsed.blocks`; pass `blocks=parsed.blocks` to ingest (still pass `content=full_text` as fallback for str-only sources) |
| G2 | `document_worker.py:391-403` `ingest(...)` call | no `blocks=` kwarg | add `blocks=...` |
| G3 | `document_service.py:1362-1379` `ingest()` sig | no `blocks` param | add `blocks: list[Block] \| None = None` |
| G4 | `document_service.py:1903-1997` B2 gate | `parsed_blocks: list = []` (`:1949`, dead) + flag `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True` (`_12:176`, dishonest) | feed `parsed_blocks = blocks or []`; flip flag honesty (set default False until wired, OR wire then keep True) |
| G5 | `document_service.py:2118-2124` | `smart_chunk(content:str)` | when `blocks` present: `smart_chunk_atomic(blocks, ...)` → map to `list[str]` + `block_type_by_idx` |
| G6 | `narrate_dispatch.py:94` | `_split_into_blocks_with_atomic(text)` re-detect | when block-feed active, read `block_type_by_idx[i]` instead of re-detecting |
| G7 | `document_service.py:1321` (registry FLATTEN #2) | join to str | leave for Excel/Sheets (they have the row-dict side-channel §5); only OCR path needs blocks first |

**Dead-code disposition (P2-B Q12 confirmed):**
- **WIRE:** `smart_chunk_atomic` (`chunking.py:2758`) — make it the block-feed survivor.
- **KEEP as fallback:** `_split_into_blocks_with_atomic` (`chunking.py:1150`) + `_smart_chunk_with_atomic_protect` (`chunking.py:2446`) — needed for the **direct-text API path** (caller supplies `content:str`, no Block source; e.g. manual ingest, legacy callers). Apply the T1 `_is_table_line` fix regardless because `narrate_dispatch.py:94` uses it for any block-less chunk TODAY.
- **DELETE candidate (separate ADR):** `_smart_chunk_with_atomic_protect`'s duplicated strategy elif-ladder (`chunking.py:2509-2519`) drifts vs `smart_chunk`'s (`:2649-2658`).

---

## (5) Excel/Sheets row-dict path (P2-B Q9)

`_route_through_parser` returns `parser_row_chunks: list[dict]` (each `{content, metadata:{parser}}`) `document_service.py:1306-1322`. Used at `:2106-2117`: if `metadata.parser ∈ {excel_openpyxl, google_sheets}` → `_chunking_strategy="parser_preserve"`, raw_chunks = row contents (bypass `smart_chunk`), orphan-merge skipped `:2142-2143`.

**Target mapping (Q9):** each row-dict → `Block(type="TABLE", is_atomic=True, content=row, context_before=header)`. Then `smart_chunk_atomic` emits one atomic Chunk per row = identical row-per-chunk behaviour, and the side-channel `parser_row_chunks` + the `_parser_is_row_shaped` special-case (`:2106-2117`) can retire. **NOTE:** this is *additive consolidation*, not required for D1 (Excel path already correct). Sequence it AFTER the OCR block-feed lands.

---

## (6) RỦI RO / BLAST-RADIUS

1. **Return-type incompatibility (largest risk):** `smart_chunk_atomic -> list[Chunk]` vs ingest tail expecting `list[str]` (`document_service.py:2125-3400`: CR, embed-text strategy `:2857`, narrate `:2895`, INSERT). Option-A adapter contains the blast to ~2 sites; Option-B touches the whole tail. **D1 MUST pick A.**
2. **`smart_chunk_atomic` strategy vocabulary drift:** it normalizes strategy to UPPERCASE Literal `{HDT,SEMANTIC,PROPOSITION,HYBRID}` and defaults non-matching → `HYBRID` (`chunking.py:2841-2849`), dropping `recursive`/`table_csv`. The live str pipeline's dominant strategies are `recursive` (50% of corpus) + `table_csv` (P2-B §1 DB). **Block-feed would silently re-route recursive→hybrid.** ADR must reconcile the two strategy vocabularies before flipping.
3. **`with_metadata` parity:** `smart_chunk_atomic` ignores `with_metadata` (no-op, `:2790-2792`); the str path uses `with_metadata=True` for parent-heading stacks (`test_chunking_parent_heading_o1.py`). Parent/heading metadata must be re-derived from `Block.context_before`.
4. **Tests that will break / need pins:**
   - `tests/unit/test_smart_chunk_atomic.py` — currently the ONLY caller; behaviour must stay green.
   - `tests/unit/test_adapchunk_wiring_honesty.py` (P2-B T4) pins `"parsed_blocks: list = []"` literal at `document_service.py:1949` — **will fail the day G4 lands** (that's by design; update it then).
   - `tests/unit/test_atomic_formula_image.py` + `test_ingest_block_stats_observability.py` + `test_chunking_footer_preserve.py` exercise `_split_into_blocks_with_atomic` (fallback path) — keep green by NOT deleting that fn.
5. **Backward-compat — direct-text API has no Block:** routes/manual ingest call `ingest(content=str)` with no parser/blocks. `blocks=None` → must fall through to the legacy `smart_chunk(content)` path unchanged. Default-None kwarg preserves this.
6. **Flag honesty:** `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True` (`_12:176`) already True while feed is empty — flipping the feed ON changes production behaviour *immediately* for any deployment using the constant default (no system_config row exists, per P2-B). ADR must gate the block-feed behind the parser actually surfacing blocks (i.e. `if blocks:`), not just the flag, OR set the constant False and require explicit opt-in.

---

## (7) STAGING ĐỀ XUẤT (small, each testable)

- **S1 (plumbing, no behaviour change):** add `blocks: list[Block] | None = None` to `ingest()` (G3) + thread `blocks=parsed.blocks` from worker (G1/G2). Guard everything behind `if blocks:`. Test: ingest a PDF, assert `blocks` arrives non-empty (unit + worker integration); existing str-only callers unaffected (default None).
- **S2 (block-feed into B2, behind real flag):** in B2 gate set `parsed_blocks = blocks or []` (G4); reconcile strategy vocabulary (risk #2) — map `smart_chunk_atomic` UPPERCASE back to the live lowercase set, or extend its normalizer to accept `recursive`/`table_csv`. Test: assert atomic Block (a real table) emerges as a single chunk (no mid-table cut); assert `recursive` doc still chunks as recursive (no silent→hybrid).
- **S3 (adapter Chunk→str + block_type_by_idx):** call `smart_chunk_atomic(blocks)` (G5), map `Chunk.original_content`→`list[str]`, build `block_type_by_idx`; feed it into narrate dispatch instead of `_split_into_blocks_with_atomic` (G6). Test: P2-B T1 case — a TABLE block narrates, a prose block does NOT (mis-classification gone because type comes from parser, not regex).
- **S4 (Excel consolidation, optional, post-D1):** map `parser_row_chunks`→`Block(type=TABLE,is_atomic)` (§5); retire `_parser_is_row_shaped` side-channel. Test: Excel row count == chunk count unchanged.

---

## (8) OPEN QUESTIONS cho ADR

1. **Strategy vocabulary:** unify `smart_chunk`'s lowercase `{recursive,table_csv,hdt,semantic,hybrid,proposition}` with `smart_chunk_atomic`'s UPPERCASE Literal `{HDT,SEMANTIC,PROPOSITION,HYBRID}`. Which is canonical? (recursive/table_csv have no uppercase entry — data loss on round-trip.)
2. **Chunk vs str pipeline:** commit to Option-A (adapter, D1) and schedule Option-B (full Chunk-native CR/embed/INSERT) as a later wave, or bite Option-B now? P2-B charter = EVOLVE → A first.
3. **Flag semantics:** keep `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True` + gate on `if blocks:` (block presence is the real switch), or set the constant False and require system_config opt-in per deployment? (Honesty fix for P2-B "dishonest flag".)
4. **kreuzberg gating:** `kreuzberg_parser_enabled` default OFF (`kreuzberg_parser.py:16-18`) — which parser actually feeds Blocks in prod today? If kreuzberg is OFF, the OCR path uses `SimpleTextParser` which may emit a single TEXT block → block-feed buys nothing until kreuzberg is enabled. ADR must confirm the live parser engine before claiming lift.
5. **Narrate raw_only interaction (P2-B 🐛-C):** once block_type is authoritative, does narrate still override `raw_only`? Resolve ordering in the same ADR or defer.

*P3 Phase-3 map complete. READ-ONLY. Single file written: this one.*
