# Expert fix — CHUNKING + INGEST-REMAINDER + NARRATE/i18n · 2026-06-23

Worktree branch: `worktree-agent-a63d8bbc3a41831eb` (base `230d041`).
Method: TDD (failing test first) per `docs/dev/DEEP_DEBUG_TO_EXPERT_PROTOCOL.md`.
Source of truth for findings: `reports/EXPERT_DEEP_AUDIT_20260623.md` (CHUNKING / INGEST-REMAINDER / MULTI-LANGUAGE).

## Outcome per fix

| ID | Status | Summary |
|---|---|---|
| **A-I5 / B-2** | DONE | Typed `Block` stream now flows from the 4 structured parsers through the worker registry path. |
| **B-1** | DEAD-CODE NOTICE (sanctioned fallback) | Full wiring regresses the CSV/legal fast-paths; documented + headered, not shipped as a silent orphan. |
| **B-3** | DONE (flag-gated, default OFF) | `smart_chunk_atomic(blocks)` executor wired into U4 behind `atomic_block_chunking_enabled`; byte-identical by default. |
| **B-4** | SKIPPED (reasoned) | `DocumentProfile` entity lacks `is_csv` / `vn_markers`; removing the dual-source regresses fast-paths. Needs entity extension + load-test. |
| **A-I4** | DONE | `late_chunk_embed` slices oversize chunk lists by `DEFAULT_EMBED_DOC_BATCH_SIZE` above a new `DEFAULT_LATE_CHUNKING_MAX_CHUNKS` ceiling. |
| **I-1 / NEW-2** | DONE | `language` threaded through narrate Port→service→dispatch→call site; VN-hardcoded `_BLOCK_PROMPTS` replaced with language-neutral scaffolds. |
| **I-2** | DONE | `get_pack()` unseeded-locale fallback now ENGLISH, not Vietnamese. |
| **NEW-1** | ALREADY DONE | `infrastructure/chunk_quality/*` already carry a DEAD-CODE NOTICE (2026-06-03) with bodies commented out — verified, no action. |
| **Hygiene** | DONE (surgical) | Version-refs stripped in the comment regions touched by the fixes (block-pipeline gate, row-preserve, narrate_dispatch). Untouched-region comments left intact per surgical rule. |

## A-I5 / B-2 — Typed Block emission (keystone)

- `application/ports/document_parser_port.py`: added optional `StructuredParserPort` Protocol (`parse_blocks() -> list[Block]`), additive — `parse()` contract unchanged.
- `shared/structured_blocks.py` (NEW): domain-neutral `markdown_to_blocks(markdown) -> list[Block]`. Reuses `shared/chunking/blocks._split_into_blocks_with_atomic` (single detector source of truth) + ATX heading split. Mapping: heading→HEADING (atomic), pipe-table→TABLE (atomic), formula/image/code→atomic, prose→TEXT.
- 4 structured parsers gained `parse_blocks()` reusing a private `_build_markdown` / `_extract_markdown` helper (no parse logic duplicated): `docx_parser`, `excel_openpyxl_parser`, `google_sheets_parser`, `kreuzberg_markdown_parser`.
- `interfaces/workers/document_worker.py`: after the registry parse succeeds, `isinstance(parser, StructuredParserPort)` → `parse_blocks()` → `parsed_blocks`, mirroring the OCR path; degrades to None on `(ValueError, TypeError, OSError)`. This is the upstream unblock for B-3/B-5.
- Tests: `test_structured_blocks.py` (5), `test_structured_parser_blocks.py` (4).

## B-1 — chunking strategy resolver (DEAD-CODE NOTICE)

Evidence the full wire would regress, not improve:
- `RuleChunkingStrategyResolver.resolve_strategy` scores via `profile_to_dict(dp)` which HARDCODES `is_csv_format=False` + `vn_hierarchical_markers=0` (`rule_resolver.py:38-39`).
- `select_strategy` resolves the CSV→table and legal→HDT fast-paths from exactly those fields (`analyze.py:429-438`).
- So replacing the inline `select_strategy()` with the resolver bypasses both fast-paths → strategy-selection regression (spa-07 class).
- The Port carries no `text` / `table_strategy` / `ekimetrics` inputs; preserving fast-paths means extending Port + both resolvers + tests + U4. The `llm` branch additionally needs a load-test soak.

Action: DEAD-CODE NOTICE header on `infrastructure/chunking_strategy/__init__.py` documenting the blocker + reactivation steps. Code kept intact + reachable (registry valid). Honest, per the prompt's explicit fallback ("do NOT ship a silent orphan").

## B-3 — block-native executor (flag-gated)

- New constant `DEFAULT_ATOMIC_BLOCK_CHUNKING_ENABLED = False` (`shared/constants/_00_app_env_taxonomy.py`, exported via `_09`).
- U4 (`ingest_stages.py`): when `atomic_block_chunking_enabled` AND `ctx.blocks` present (and not a row-shaped preserve case), route through `smart_chunk_atomic(blocks)`; extract `original_content or narrated_text` per Chunk; defensive fallback to `smart_chunk` if the block stream is degenerate. Default OFF = byte-identical `smart_chunk` path.
- `smart_chunk_atomic` is no longer an orphan (was 0 production callers).
- The `formula_image_atomic_protect_enabled` default flip stays deferred (load-test soak), per audit.
- Tests: `test_atomic_block_chunking_flag.py` (2).

## A-I4 — late-chunking batch ceiling

- New constant `DEFAULT_LATE_CHUNKING_MAX_CHUNKS = 500` (`shared/constants/_18_*`).
- `late_chunk_embed` slices the contextualized list into `DEFAULT_EMBED_DOC_BATCH_SIZE` chunks when the count exceeds the ceiling — order preserved, `<= 0` disables. Bounds the orchestrator-side memory peak for very large docs.
- Tests: `test_late_chunking_batch_ceiling.py` (3) — N chunks ⇒ `ceil(N/batch)` calls; small N ⇒ 1 call; order parity.

## I-1 / NEW-2 — narrate language threading

- `_BLOCK_PROMPTS` (TABLE/FORMULA/IMAGE) VN literals replaced with language-neutral English scaffolds that name the target `{language}` at runtime; the system instruction's "preserve source language" is the second guard.
- `language` threaded additively (default `DEFAULT_LANGUAGE`): narrate Port → `LLMNarrateGenerator.narrate` / `NullNarrateGenerator.narrate` → `NarrateService.narrate_chunk` → `narrate_chunks_for_embed` → call site (`ingest_stages_store.py` passes `ctx.language`).
- Long-term language_packs-driven prompts (alembic-seeded) deferred (governance/alembic).
- Tests: `test_narrate_language_threading.py` (2: EN doc → "en language" + no VN literal; `_BLOCK_PROMPTS` carry no VN). Existing narrate tests updated to the new contract.

## I-2 — i18n unseeded-locale fallback

- `get_pack()` last-resort fallback for an unseeded locale → English pack (lingua franca for internal LLM prompts) instead of Vietnamese. Seeded locales + DB-row path unchanged; `DEFAULT_LANGUAGE` remains the new-bot default.
- Tests: `test_i18n_get_pack_fallback.py` (3: `get_pack('km'|'fr'|'zh').code == 'en'`; seeded packs intact).

## Verification

- `set -a && source .env && set +a` then `pytest` (worktree `pythonpath=src`):
  - Full touched-flow suite (22 files incl. 6 new): **167 passed**.
  - Broad regression `-k "chunk|parser|narrate|i18n|ingest|late|block|document|embed|adapchunk|profile|strateg"`: **1595 passed, 0 failed** (25 skipped = pre-existing dead-module skips).
- 3 collection errors (`test_feedback_loop_wire`, `test_admin_documents_debug_route`, `test_route_workspace_scope_pin`) are PRE-EXISTING — a FastAPI-version `_EffectiveRouteContext` import mismatch in a shared test helper, reproducible on the base tree, unrelated to these files.
- ruff HEAD==NOW: zero NEW error TYPES on every touched src file; the new `structured_blocks.py` passes all checks. `document_worker.py` PLR0912/0915 counts ticked up (32→34 / 156→162) — pre-existing accepted thresholds, not new violation types; the B-2 block-threading is the cause.

## Skipped / deferred (load-test or alembic gated)

- B-1 full wire (Port extension + fast-path inputs + LLM-branch load-test).
- B-4 dual-source removal (extend `DocumentProfile` with `is_csv`/`vn_markers`, then load-test).
- `formula_image_atomic_protect_enabled` + `atomic_block_chunking_enabled` default flips (load-test soak).
- Narrate language_packs-driven prompt text (alembic-seeded, ADR-W1-S10 governance).
