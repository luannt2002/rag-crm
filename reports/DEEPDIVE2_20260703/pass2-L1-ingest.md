# PASS-2 DEEP RE-ANALYSIS — LUỒNG 1 (INGEST)

**Slug**: `pass2-L1-ingest` · **Date**: 2026-07-03 · **Branch**: `fix-260623-ingest-expert`
**Role**: Staff/Principal RAG engineer — skeptical SECOND look. DO NOT trust pass-1 blindly.
**Method**: RE-READ every cited `file:line` in source + runtime probes against installed libs (kreuzberg 4.9.7, tabular/number modules executed). Read-only on src/tests/alembic; this report is the only file created.
**Rule #0**: every claim carries `file:line`/runtime output evidence; FACT vs HYPOTHESIS labelled. Verdicts per finding: CONFIRMED / REFUTED / REFINED / OVERCLAIMED.

Flow scope: `POST /api/ragbot/documents/create` → outbox → `document_worker` → fetch/parse/OCR → `DocumentService.ingest` (U1–U7: validate → parse → clean → chunk (AdapChunk) → enrich → embed → store) → `document_chunks` + `document_service_index` (stats).

---

## 0. Executive verdict (one screen)

Pass-1's L1 picture is **substantially CONFIRMED** by source re-reading and runtime probes. I re-verified 15 material findings at the cited lines; **13 CONFIRMED, 2 REFINED, 0 REFUTED, 0 OVERCLAIMED** — plus **1 NEW finding** pass-1's L1 reports under-weighted (the PII redactor is dead at TWO layers, not one — the DI singleton is frozen to the null provider at boot, independent of the F2 config-wiring gap).

The immutable root cause behind the two worst multi-format defects (F4 row-flatten, F1 OCR 0-block) is the same class: **the canonical B2B worker (`document_worker.py`) is NOT a thin adapter over `DocumentService.ingest` — it does its own parse pre-pass and hands the service a flattened string with no `raw_bytes`**, so every raw-bytes-gated capability inside `ingest()` (row-preserve, whole-doc row guard, mime re-sniff) is structurally unreachable on the path real customers use. This is the "unwired loop / parallel-parse-path" class the EVOLVE stance targets — the framework is right; the last-mile wiring diverged between the internal test path and the production path.

Highest-severity CONFIRMED (all with repro): **F1 (OCR fallback returns 0 blocks for EVERY doc — runtime-proven coroutine bug), F4 (row-shaped formats degraded on the canonical path), F5 (partial re-ingest wipes stats entities of unchanged rows), F2+NEW (ingest-boundary PII redaction dead at both the config gate AND the DI provider), F1-diff (NameError landmine on a default-OFF cost flag).**

---

## 1. Re-verification ledger (pass-1 claim → my source re-read → verdict)

| pass-1 id | claim | my re-read evidence | verdict |
|---|---|---|---|
| F4 / W-04 / L1-1 | Path B flattens row-chunks; `parser_preserve` unreachable | `document_worker.py:464-466` join, `:613-626` no `raw_bytes`; `ingest_core.py:318` gate `if raw_bytes is not None`; `ingest_stages.py:141` `_parser_row_shaped` False on empty | **CONFIRMED** |
| F-1 / L1-2 | OCR adapter calls async `extract_bytes` sync → 0 blocks always | runtime: `iscoroutinefunction(extract_bytes)=True`; probe `extract_bytes(pdf,mime)` → `coroutine`, no `.elements`/`.blocks` → `()`; `kreuzberg_parser.py:258,270-274` | **CONFIRMED (runtime)** |
| F13 / F-2 / L1-3 | `.doc/.xls/.ppt` no parser + no OLE2 sniff | docx `.docx`-only, excel `.xlsx`-only, kreuzberg excludes legacy (`kreuzberg_markdown_parser.py:44-56`); `mime_sniff.py` grep OLE2/`d0cf`=0 | **CONFIRMED** |
| F2 / L1-4 | ingest-boundary PII redaction dead (config not passed) | `ingest_core.py:347-353` omits `config_service`; `ingest_helpers.py:251,319-320`; `DEFAULT_RECAP_PII_ENABLED=False` (`_19:108`) | **CONFIRMED** + see NEW-1 |
| F3 | CleanBase Tier-0 sanitizer never wired | `ingest_stages.py:310` `getattr(self,"_sanitizer",None)`; grep `self._sanitizer =` = 0; no `sanitizer` param in `__init__` | **CONFIRMED** |
| F5 / L1-6 | partial re-ingest wipes stats of unchanged rows | `rows=ctx.rows` from `chunks_to_embed` (`ingest_core.py:657-660` changed-only); `final.py:548` `delete_by_document` deletes ALL (`stats_index_repository.py:176-177`) | **CONFIRMED (+edge)** |
| F1 / L1 | `_diff_reingest_compute` dangling → NameError on flag flip | called `ingest_core.py:695,701`; defined nowhere (AST: not in module or star-imports; leading `_` not exported); flag `DEFAULT_DIFF_REINGEST_ENABLED=False` (`_04:118`) | **CONFIRMED** |
| F7 / L1-14 | `_bulk_insert_chunks` exceeds asyncpg 32767 bind ceiling | `ingest_helpers.py:204-235` = 11 params/row (+1 shared, 12 w/ parent) single INSERT; ceiling ≈2978 rows | **CONFIRMED (math FACT / live HYPOTHESIS)** |
| F9 | stats rows under fabricated tenant UUID | `ingest_stages_final.py:562` `record_tenant_id or uuid.uuid4()` | **CONFIRMED** |
| F10 / L1-11 | cleaner strips repeated lines → drops legit repeats | `text_processing.py:95-101` (≥3×, <100 chars); gated `ingestion_cleaning_enabled` default True (`ingest_stages.py:297`) | **CONFIRMED (REFINED: full-line-exact only)** |
| F11 / L1-12 | `language="auto"` → hardcode `vi`, no detection | `ingest_core.py:532` → `DEFAULT_LANGUAGE="vi"` (`_02:230`); worker `parsed_language or "auto"` set only on (dead) OCR path | **CONFIRMED** |
| L1-13 / H1 | AdapChunk selector strategy-invariant; bake-off 0/8, +0.001 | `analyze.py:478` `compute_intrinsic_metrics(text)` (no blocks/chunks) → `intrinsic_metrics.py:286-296` simulated; `bakeoff_chunking_20260620.md:21-24` | **CONFIRMED** |
| M4 / L1-10 | `page_number` not persisted | grep `page_number` in document_service+repositories = 0; `_bulk_insert_chunks` col list has no page column | **CONFIRMED** |
| H6 / L1-5 | coverage gate detects, never repairs | `ingest_stages.py:889-905` logs only; `_cov.uncovered_spans` computed, no repair call | **CONFIRMED** |
| T-1/T-2/T-6/T-7 | 4 tabular shapes broken | runtime re-run (§3): headerless mis-bind, year-veto, `31.12.2026→31122026`, `1 600 000→1` | **CONFIRMED (runtime)** |
| F16 | `chunk_type` misses row semantics for parser_preserve/dual_index | `text_processing.py` `is_table_row` short-circuits only when True; call sites pass `is_table_row=(strategy=="table_csv")` (`store.py:1031`) | **CONFIRMED** |
| — | K8 CJK semantic 1-chunk (shared-chunking) | `strategies.py:436-437` `if len(sentences)<=1: return [text.strip()]`; splitter no CJK terminators | **CONFIRMED (REFINED: requires semantic selected)** |

No pass-1 L1 finding was REFUTED or OVERCLAIMED on re-read. The one RETR-F1-style false-positive risk I probed for (a default that silently rescues PII, or a star-import that resolves the dangling diff name, or a live default that already fixes late_chunking canonicalize) did not materialise — each check confirmed the defect.

---

## 2. CONFIRMED findings — full case-study chains (CLAUDE.md 5-step)

### CS-1 · CRITICAL · OCR fallback returns 0 blocks for EVERY document (async API called sync)

**1. Problem (repro).** Any ingest that misses the parser registry — legacy `.doc/.xls/.ppt`, image with VLM off, unknown format — reaches `document_worker.py:494-495` (`ocr = container.ocr(); parsed = await ocr.parse(source_url,...)`). Runtime probe in the project venv:
```
python3: extract_bytes(pdf_bytes, "application/pdf") → type=coroutine
  hasattr .elements = False ; hasattr .blocks = False
  getattr(r,"elements",None) or getattr(r,"blocks",None) or () → ()
```
→ `blocks=[]`, `page_count=0`, `full_text=""` → worker raises `RuntimeError("empty document text after parse")` (`document_worker.py:511`) → retry/DLQ.

**2. Direct cause (layer + số liệu).** Infra/parser layer. `kreuzberg_parser.py:258` `result = extract_bytes(data, mime_type_arg)` inside the sync `_extract_blocks` (run via `run_in_executor`, `:179`). Installed kreuzberg **4.9.7**: `inspect.iscoroutinefunction(kreuzberg.extract_bytes) == True` (runtime FACT). The sync twin `extract_bytes_sync` exists and is what the WORKING `KreuzbergMarkdownParser` uses. Calling a coroutine function returns an un-awaited coroutine; the `except TypeError` legacy branch (`:259-268`) never fires because a 2-arg call to a coroutine function does not raise — it just builds the coroutine (probe confirmed no exception). So `ocr_language` never reaches the engine either.

**3. Root-cause chain.** `0 blocks` ← `elements=()` ← `result is a coroutine, not an ExtractionResult` ← `extract_bytes is async in kreuzberg≥4 but the adapter treats it as sync` ← **immutable cause: the adapter was written against a pre-4.0 sync signature and the test double (`test_kreuzberg_parser.py:60`) is a SYNC `fake_extract_bytes`, so CI green-lights the exact path that is dead against the real lib.** The graceful-degrade contract only catches ImportError at construction (`ocr_factory.py`), never runtime emptiness → no fall-through to `SimpleTextParser`.

**4. Expert solution (right layer).** Fix at the adapter: `extract_bytes_sync(data, mime, ExtractionConfig(ocr_language=..., output_format=...))` — mirror the proven markdown parser. SOTA pattern: **contract test against the real dependency** (Consumer-Driven Contract / "no-mock-at-the-boundary"). Short-term: swap to sync + un-mock one smoke test asserting `not iscoroutinefunction(extract_bytes)` before sync use. Mid-term: OCR-confidence router (text-layer check → Tesseract-with-confidence → escalate low-confidence pages to structure-preserving OCR) per `web-ingest-formats` R1. Long-term: parser-emitted typed Block stream with char-offset `split_points` (refs-adaptive-chunking M2 keystone).

**5. Is this the expert solution or a patch?** Expert at the adapter layer, but the *systemic* fix is the contract-test discipline — the one-line API swap without an un-mocked test would let the next lib rename silently re-break it. The router (mid-term) is the real capability, not this fix.

**Trade-offs.** Sync-in-executor keeps the event loop free (already the pattern); no downside. The contract test costs one real-lib fixture in CI.

**Impact.** Correctness — every registry-miss format is un-ingestable today (silent DLQ). Blast radius: all scanned PDFs / images-with-VLM-off / legacy Office via URL. HALLU-adjacent (missing corpus → bot blind, not fabricating).

---

### CS-2 · CRITICAL · Canonical B2B path degrades every row-shaped format (Path A/B split)

**1. Problem (repro).** An XLSX / Google-Sheets / CSV ingested via `POST /api/ragbot/documents/create` (the ONLY external canonical API) loses 1-row-per-chunk atomicity. A small sheet (< whole-doc threshold) can even whole-doc-collapse → `col_N` stats regression — exactly the bug the 2026-07-01 xe-bot fix was meant to close.

**2. Direct cause.** Application/worker boundary. `document_worker.py:464-466` `full_text = "\n\n".join(c["content"] ...)` then `:613-626` `ingest(content=full_text, blocks=parsed_blocks)` with **no `raw_bytes`, no `file_name`**. Inside `ingest()`, `parser_row_chunks` is populated ONLY inside `if raw_bytes is not None:` (`ingest_core.py:318`). So `parser_row_chunks=None` → `_parser_row_shaped(None)=False` (`ingest_stages.py:141`) → the whole-doc row guard's `parser_is_row_shaped` arm (`:167`) and the `parser_preserve` bypass (`:763 if parser_row_chunks and _parser_is_row_shaped`) can NEVER fire on this path. Evidence the flattened pipe-markdown then defeats CSV detection: `_is_csv_format` is comma-based (`analyze.py:73,92-99`) and `rows_to_structured_markdown` output has `##` headings so the table fast-path (`is_csv and total_headings==0`) never triggers.

**3. Root-cause chain.** `row atomicity lost on canonical path` ← `parser_preserve/whole-doc-guard unreachable` ← `parser_row_chunks=None` ← `worker passes no raw_bytes` ← **immutable cause: the worker is a PARALLEL parse path, not a thin adapter over `ingest()` — it parses, flattens to text, and re-feeds a string the service must re-detect surface-form.** The authoritative parser stamp (`ingest_stages.py:136-140` "independent of the parsed markdown's surface form") only exists on the raw_bytes path (`sync.py:566`, test harness). "MỌI format đi CÙNG 1 luồng canonical" is violated: format fidelity is transport-dependent (local bytes > OCR URL > registry URL).

**4. Expert solution.** Collapse the worker into a thin adapter (`canonical-ingest-flow` skill): worker fetches bytes and passes `raw_bytes=_raw` + `file_name` + `mime_type` to `ingest()`; delete its own parse pre-pass so U2 owns detection once. SOTA pattern: single canonical funnel + tolerant typed-block contract (RAG-Anything `content_list` / `insert_content_list` seam, refs-rag-anything §2.4, §8). Short-term: thread the parser chunk dicts through instead of joining (pass `parser_row_chunks` param). Mid-term: promote a parser-port capability flag `emits_row_chunks` so the app layer stops hardcoding `_ROW_PRESERVE_PROVIDERS` (F14). Long-term: typed Block stream end-to-end (Layer-6 `smart_chunk_atomic` already built, refs-adaptive-chunking H2).

**5. Patch or expert?** Passing `raw_bytes` is the minimal correct fix; the *expert* fix is deleting the worker's parse pre-pass so there is genuinely ONE detection point — anything less keeps two code paths that will drift again.

**Trade-offs.** Worker holds bytes in memory (already does — `_raw`); moving parse into the service adds one boundary crossing but removes the flatten+re-detect. Re-ingest of existing docs needed to heal already-degraded corpora.

**Impact.** Correctness — cross-row value conflate + `col_N` on the path real customers use; T1 answer quality on price/spec sheets. Blast radius: every tabular corpus ingested B2B (the platform's stated first-class case).

---

### CS-3 · HIGH · Partial re-ingest wipes stats-index entities of unchanged rows

**1. Problem (repro).** Re-ingest a 100-row price sheet with 1 edited row. The 99 unchanged rows keep their `document_chunks` (hash match) but their `document_service_index` entities are deleted and never re-inserted → count/price/aggregate answers collapse to ~1 entity until a full re-ingest.

**2. Direct cause.** `ingest_stages_final.py:443` gates the stats path on `rows` = `ctx.rows`. `ctx.rows` is built in the flat-insert loop `for chunk_idx, chunk_text, chunk_hash in chunks_to_embed` (`ingest_stages_store.py:946`, `ctx.rows=rows` at `:1096`). On re-index `chunks_to_embed` holds ONLY changed chunks (`ingest_core.py:657-660`: hash-match → `unchanged_indices`; else → `chunks_to_embed`). Then `parse_table_chunks(_stats_rows)` extracts entities from the changed subset, and `delete_by_document(doc_id)` (`final.py:548`) deletes ALL rows for the doc (`stats_index_repository.py:176-177` `DELETE ... WHERE record_document_id=:doc_id`), followed by insert of the subset only.

**3. Root-cause chain.** `99 entities vanish` ← `delete-all + insert-changed-subset` ← `rows fed to stats = changed chunks only` ← `stats path reuses the incremental-embed row set` ← **immutable cause: two orthogonal concerns share one variable — the embed-diff optimisation (`chunks_to_embed`, correct for embedding) is wrongly reused as the stats-extraction input, which must always be the FULL current chunk set.** Inverse edge (also confirmed): if the changed chunk parses to 0 entities, `if _stats_entities:` (`:534`) is False → delete never runs → stale entities survive. So the coupling breaks in BOTH directions.

**4. Expert solution.** Feed the stats extractor the FULL current chunk set on every ingest, not `chunks_to_embed`. Right layer: `_stage_finalize` should read all live `document_chunks` for the doc (or carry the full `ctx.chunks` rows, tagged raw-vs-enriched) before `parse_table_chunks`. SOTA pattern: idempotent rebuild — stats index is a materialised view of the doc; a view rebuild reads the whole source, not the delta. Short-term: after the chunk INSERT, `SELECT` the doc's persisted rows and pass those. Mid-term: derive stats from a stable per-row raw source so re-ingest is a pure recompute. Long-term: event-sourced stats (upsert per entity key) so unchanged entities are untouched.

**5. Patch or expert?** Reading the full chunk set is the expert fix (correct data contract), not a patch — the delete-all is fine once its input is the whole doc.

**Trade-offs.** Re-parsing all rows' stats on a 1-row edit costs a little CPU (deterministic, no LLM — cheap). Acceptable vs silent count collapse.

**Impact.** Correctness — aggregation/count/price-range/superlative answers wrong after any partial re-ingest. Blast radius: every catalog/price bot that re-uploads an edited sheet (the normal maintenance flow).

---

### CS-4 · CRITICAL (compliance) · Ingest-boundary PII redaction is dead at BOTH layers

**1. Problem (repro).** Owner sets `plan_limits.pii_redaction_enabled=true` AND operator sets `system_config.recap_pii_enabled=true` AND selects a real `pii_redactor_provider`. Raw PII (CCCD / phone / bank acct) is STILL chunked, embedded, and persisted. Two independent breaks:

**2. Direct cause + 3. Root-cause chain.**
- **Break A (config gate, = pass-1 F2).** `ingest_core.py:347-353` calls `_maybe_redact_ingest_content(...)` WITHOUT `config_service`. In `ingest_helpers.py:251` `config_service` defaults `None` → `else` branch `:319-320` `feature_enabled = bool(DEFAULT_RECAP_PII_ENABLED)` = **False** (`_19:108`, runtime-confirmed). The DB kill-switch is unreadable from this call site.
- **Break B (DI provider, NEW — see NEW-1).** Even with A fixed, `container.pii()` is `providers.Singleton(build_pii_redactor, provider=DEFAULT_PII_REDACTOR_PROVIDER)` (`bootstrap.py:447-450`) = frozen to `"null"` (`_13:100`) at boot. `pii_redactor_provider` is whitelisted in `bootstrap_config.py:61` but NEVER read into the DI wiring — the comment "resolved PER-CALL from system_config.pii_redactor_provider" (`bootstrap.py:441-442`) is false; there is no `providers.Callable(get_boot_config,...)`. So the redactor is always `NullPiiRedactor` (passthrough).
- **Immutable cause**: the two-knob PII gate was wired at the call sites but the *plumbing that would make either knob observable* (pass config_service into the helper; resolve the provider from config in DI) was never completed — a last-mile DI-wiring gap (the S2 systemic class).

**4. Expert solution.** (a) Pass `config_service=self._cfg` at `ingest_core.py:347`. (b) Change `bootstrap.py:447` to `providers.Callable(build_pii_redactor, provider=providers.Callable(get_boot_config, "pii_redactor_provider", DEFAULT_PII_REDACTOR_PROVIDER))` so the provider actually reads system_config. SOTA/compliance pattern: PII redaction at the ingest boundary (claude-mem "redact at the hook/boundary layer"). Short: 2-line wiring. Mid: one un-mocked integration test that flips both knobs + a real provider and asserts a masked entity in the persisted chunk. Long: audit `recap_pii_detect` event coverage so ops can prove redaction fires.

**5. Patch or expert?** Two-line wiring IS the expert fix here (the redactor and gate logic are correct); the missing piece is exactly the DI last-mile. The systemic fix is the "wiring audit + un-mocked registry integration test" so built-not-wired features can't ship green again.

**Trade-offs.** None functional; redaction is opt-in so behaviour unchanged for bots that don't enable it. Enabling adds one regex pass per ingest.

**Impact.** Correctness/compliance — regulated tenants believe PII is masked at ingest; it is stored raw. Blast radius: every bot that opted in. This is the docstring contract at `__init__.py:224-228` ("raw document content is masked at the ingest boundary") being false.

---

### CS-5 · HIGH · `_diff_reingest_compute` dangling name → NameError landmine on a cost flag

**1. Problem (repro).** Operator flips `system_config.diff_based_reingest_enabled=true` (a T2 cost-observability flag). Every re-ingest then raises `NameError: name '_diff_reingest_compute' is not defined` at `ingest_core.py:695` — AFTER the `documents` row was already committed (INSERT/UPDATE at ~`:500-550`). Doc stuck; recovery sweeper re-emits, hits the same NameError → loop.

**2. Direct cause.** `ingest_core.py:695` `_diff_reingest_compute(...)` and `:701` `_diff_reingest_log_event(...)`. AST scan: neither name is defined or imported in the module; the three star-imports (`:144-146` `ingest_phases`/`ingest_helpers`/`text_processing`) do not define them, and leading-underscore names are not exported by `import *` regardless. `shared/diff_reingest.py` is fully commented dead code whose own header falsely claims the helpers were "copy-pasted inline into document_service.py" (grep: they were not).

**3. Root-cause chain.** `NameError on flag flip` ← `two helper names referenced but never defined` ← `refactor moved/deleted the helpers but left the call sites behind a default-OFF flag, so tests never exercise them` ← **immutable cause: a feature flag guards a code path that does not compile-resolve; the flag is a landmine, not a toggle.** Gated by `is_reindex and diff_based_reingest_enabled` (default False, `_04:118`, not seeded in alembic).

**4. Expert solution.** Either (a) delete the dead call block + the `diff_based_reingest_enabled` flag (the diff is already computed for the incremental-index path; the telemetry is redundant), or (b) restore the two helpers as pure functions in `ingest_helpers.py` with a real pin test. SOTA pattern: "no flag without a test that flips it on" (feature-flag hygiene). Short: delete the block (simplest, `Simplicity-First`). Mid: a lint/AST guard that every `system_config.get(...enabled)` flag has a test exercising the True branch. Long: retire the dead `diff_reingest.py` module.

**5. Patch or expert?** Deleting is the expert move (the flag adds no value the incremental path lacks) — restoring the helpers would re-add code for a telemetry line nobody consumes.

**Trade-offs.** Deleting loses a per-feature cost-attribution log; acceptable — `incremental_indexing` (`:665-673`) already logs unchanged/to_embed/stale counts.

**Impact.** Correctness/ops — a documented ops knob bricks re-ingest. Blast radius: any operator who trusts the flag; zero today because nobody has flipped it (latent).

---

### CS-6 · HIGH · Coverage gate detects dropped source spans but never repairs (observe-only)

**1. Problem (repro).** A chunking strategy drops a source span (a price line, an answer sentence). `check_chunk_gaps` computes `_cov.uncovered_spans` but the span stays lost — the bot is blind to content the corpus contains (Coverage failure, Faithfulness stays 1.0 = "honest but blind").

**2. Direct cause.** `ingest_stages.py:889-905`: on `not _cov.ok` it only `logger.warning("chunk_char_coverage_gap", ..., sample=_cov.uncovered_spans[:5])` + step metadata. `coverage.py` docstring: "OBSERVE-only … NEVER raises". No call to any repair. The reference (`_external_refs/adaptive-chunking/postprocessing.py:128-151` `repair_gaps_between_chunks`) prepends each dropped span to the next chunk then re-asserts; `pipeline.py:112-118` hard-fails on unrecoverable gaps.

**3. Root-cause chain.** `silent content loss` ← `gap detected, not repaired` ← `the lossless-coverage invariant was adopted as telemetry, not enforcement` ← **immutable cause: half-adoption of the reference's evaluate→repair→assert discipline — ragbot took the detector, not the repair loop.** `CoverageResult.uncovered_spans` already carries the original-offset spans needed to repair.

**4. Expert solution.** Wire repair using existing `uncovered_spans` (≈15-line port of `repair_gaps_between_chunks`): prepend each dropped span onto the following chunk, keep the observe-log, add a per-strategy `assert check_chunk_gaps` in tests (`block-integrity-quality-gate` skill). SOTA: lossless-coverage invariant (Ekimetrics AdapChunk). Short: repair at the U4 call site. Mid: per-strategy assert so a text-dropping strategy is caught at the strategy, not masked by a later merge (refs M9). Long: parser-emitted `split_points` so gaps are attributable to gold boundaries (refs M2).

**5. Patch or expert?** Repair is the expert fix at the right layer (chunk post-process), directly HALLU-adjacent. Not a patch — it closes the silent-loss class the module's own docstring warns about.

**Trade-offs.** Repair can slightly enlarge the following chunk (bounded by gap size); acceptable vs losing the answer span. Must keep observe-log for attribution.

**Impact.** Correctness/Coverage — the owner's #1 metric. Blast radius: any doc where the selected strategy drops text (more likely on the HDT table-blind path, K1).

---

### CS-7 · HIGH · No language detection: `language="auto"` → `vi`; wrong-locale segmentation + model override

**1. Problem (repro).** An English/Japanese doc ingested via the registry path is recorded `language='vi'` → becomes VN-compound-segmentation-eligible (underthesea on non-VN text = CPU waste + token mutation) and any `embedding_model_by_language` override keys on the wrong language.

**2. Direct cause.** `ingest_core.py:532` `"language": language if language != "auto" else DEFAULT_LANGUAGE` with `DEFAULT_LANGUAGE="vi"` (`_02:230`). The worker passes `parsed_language or "auto"` (`document_worker.py:619`), and `parsed_language` is set ONLY on the OCR fallback (`:502`) — which returns 0 blocks (CS-1), so it's effectively never set. Re-index UPDATE also never refreshes `language` (`ingest_core.py:461-483`).

**3. Root-cause chain.** `EN/JA doc tagged vi` ← `no detector; "auto"→platform default` ← `language treated as a caller-supplied constant, not detected data` ← **immutable cause: the platform never verifies the doc language; multi-locale tenants must remember to set `bots.language` per doc.** Contradicts multilingual-no-vocab (language should be DATA, detected by script-range).

**4. Expert solution.** Cheap script-range detection at ingest (Unicode block histogram → dominant language) feeding `effective_language`, with `bots.language` as an override hint not a dictator (`metadata-optional-hint` skill). SOTA: langdetect/fastText-lite or pure script-range (no model). Short: script-range heuristic in U1. Mid: thread detected language into VN-segmentation gate + `embedding_model_by_language`. Long: per-locale packs already exist (`_24/_25/_26`) — route by detected language.

**5. Patch or expert?** Script-range detection is the expert fix (language as detected data); defaulting to `vi` is the anti-pattern the multilingual skill bans.

**Trade-offs.** Detection can misfire on very short/mixed docs; mitigate with a confidence floor → fall back to `bots.language` then platform default.

**Impact.** Correctness (multi-locale) + T2 CPU (underthesea on non-VN). Blast radius: every non-VN doc on a multi-locale tenant.

---

### CS-8 · HIGH · AdapChunk selector is strategy-invariant — "AdapChunk is not AdapChunk"

**1. Problem (repro).** The Ekimetrics selector's metrics do not depend on the candidate strategy, so it cannot pick per the paper. Project's own bake-off: `adaptive == oracle_best: 0/8`, `adaptive lift over recursive: +0.001`, `headroom 0.103` (`bakeoff_chunking_20260620.md:21-24`).

**2. Direct cause.** `analyze.py:478` `compute_intrinsic_metrics(text)` — passes only `text`, no `blocks`, no `chunks`. So `intrinsic_metrics.py:286-296`: blocks default to paragraph splits, chunks default to a simulated equal char-split → the 5-metric vector is a function of the DOCUMENT ONLY, identical across candidate strategies. The reference computes metrics per method on the method's REAL output then argmax (`_external_refs/adaptive-chunking/paper/analysis.py:294-327`).

**3. Root-cause chain.** `selector no better than recursive` ← `metrics can't discriminate strategies` ← `metrics computed on raw text + simulated chunks, not real per-strategy output` ← **immutable cause: ragbot adopted the vocabulary (5 metrics, "AdapChunk" naming, bake-off) but not the evaluate-then-select LOOP; strategy is chosen BEFORE chunking by hand-tuned rules.** (Flag default OFF anyway — `ekimetrics_5metric_selector_enabled` — so today the rule router runs.) Docstring cites a "Rule-Based Selector" paper section (`intrinsic_metrics.py:319`) that does not exist in the vendored reference code (HYPOTHESIS on the paper PDF; FACT the code has no such selector).

**4. Expert solution.** Make the bake-off the selector's feedback loop: run `scripts/bakeoff_chunking_strategies.py` per corpus on ingest-idle cadence, persist per-doc `oracle_best` as a `chunking_policy` override (evaluate-then-select, amortised OFFLINE — zero per-ingest latency). SOTA: Ekimetrics adaptive-chunking (LREC 2026). Blocker to honesty first: lexical BI/ICC/DCC are weak (refs M2/M8) — fix the gold-boundary contract or use embed-based scoring offline before trusting picks. Short: keep the rule router (it ties recursive baseline); stop paying the LLM/simulated selector. Mid: offline oracle override. Long: real per-output scoring at ingest for high-value docs.

**5. Patch or expert?** The expert fix is the offline evaluate-then-select loop; adding a "legal→PROPOSITION" cross-check rule (the pass-1-screenshot temptation) would be a symptom patch on a selector that never looks at real output.

**Trade-offs.** Offline bake-off is pure CPU (already runs against the live corpus); cost is bounded and amortisable. The rule router stays as the safety net.

**Impact.** Correctness (chunk quality → retrieval) + Cost (the flagged LLM selector is ~4.5s of wasted ingest work when on). Blast radius: all docs when the selector flag is flipped; today, opportunity cost only.

---

### CS-9 · HIGH · Legacy `.doc/.xls/.ppt` unsupported — declared first-class, silently 0 chunks

**1. Problem (repro).** A `.doc` (application/msword) upload: registry miss → on Path A `_route_through_parser` returns `(None, None)` → `content` stays the caller's (empty for binary) → 0 useful chunks; on the worker path → OCR fallback → 0 blocks (CS-1) → `RuntimeError` → DLQ.

**2. Direct cause.** No `supports()` accepts legacy Office MIME/ext: docx `.docx`+OOXML-word only (`docx_parser.py:23,25,64`), excel `.xlsx`+OOXML-sheet only (`excel_openpyxl_parser.py:31,33,59`), kreuzberg_markdown explicitly excludes them (`kreuzberg_markdown_parser.py:44-56`, and only `.pptx` not `.ppt`). `mime_sniff.py` has no OLE2 (`D0 CF 11 E0`) branch (grep=0) → octet-stream legacy Office returns ambiguous mime → registry no-match.

**3. Root-cause chain.** `.doc → 0 chunks` ← `no parser + no OLE2 sniff` ← `only OOXML/PDF/HTML/CSV/image adapters registered` ← **immutable cause: the "add format = 1 adapter" contract was never exercised for the OLE2 family CLAUDE.md declares first-class ("PDF · DOCX/DOC · XLSX/XLS · PPTX").**

**4. Expert solution.** Decide the legacy story loudly: either (a) add an OLE2 sniff branch + route `.doc/.xls/.ppt` to kreuzberg (verify OLE2 support post-CS-1) or a LibreOffice-convert adapter, or (b) reject at the API with a clear 4xx ("legacy Office not supported; convert to .docx/.xlsx") instead of DLQ-after-timeout. SOTA: `parser-adapter-pattern` (1 file + 1 registry row) + `type-detection-mime-sniff` (byte-sniff rescues no-ext). Short: OLE2 signature in `mime_sniff` + explicit reject if unsupported. Mid: LibreOffice-headless convert adapter (RAG-Anything §2.3 pattern, but as a real adapter not everything-to-PDF). Long: per-bot parser tier (`web-ingest-formats` R2).

**5. Patch or expert?** Loud rejection is the honest minimum; the real fix is a legacy adapter behind the registry so the first-class claim holds.

**Trade-offs.** LibreOffice convert is heavy/opt-in; rejection is cheap but drops the format. Owner must choose per the first-class mandate.

**Impact.** Correctness/multi-format — a declared first-class format is un-ingestable. Blast radius: any tenant with a legacy Office corpus.

---

### CS-10 · MEDIUM–HIGH · Cleaner strips repeated lines → drops legitimate repetitive values (REFINED)

**1. Problem (repro).** A TXT/DOCX menu where a standalone line like `Giá: 500.000đ` or a repeated size label appears ≥3 times → all occurrences stripped BEFORE chunking → the number is unreachable at retrieval; only the observe-only `chunk_numeric_coverage_gap` warning hints at it.

**2. Direct cause.** `text_processing.py:95-101`: with `>10` lines, remove any full line whose `.strip()` occurs `count >= 3` and `len(line) < 100`. Runs in `_clean_document_text` when `ingestion_cleaning_enabled` (default True, `ingest_stages.py:297`).

**3. Root-cause chain.** `repeated price/size dropped` ← `≥3×-repeat lines removed as headers/footers` ← `a PDF-header heuristic applied to ALL text-path formats` ← **immutable cause: a format-specific de-boilerplate rule generalised to every format without a structure signal distinguishing a running header from legitimate repeated data.**

**REFINEMENT vs pass-1:** the strip is on FULL lines that are exactly identical after `.strip()`. A table ROW like `Serum X | 500.000đ` is NOT dropped (distinct product name). The realistic hit is standalone repeated value lines (menu size labels, repeated disclaimers-with-numbers) — narrower than "any repeated number" but still a genuine number-loss class.

**4. Expert solution.** Restrict de-boilerplate to page-header/footer POSITIONS (top/bottom N lines per page) from the parser's page structure, not global line frequency. SOTA: trafilatura-style boilerplate detection uses position + density, not raw repetition (`web-ingest-formats` §5). Short: gate the strip to lines that are ALSO position-consistent (needs page info — see CS-12). Mid: move de-boilerplate to the parser (kreuzberg tags page headers/footers) so data lines are never candidates. Long: per-bot opt-out.

**5. Patch or expert?** The expert fix uses page position (a structure signal), not frequency; frequency-only is the anti-pattern.

**Trade-offs.** Position-based needs page metadata (currently dropped — CS-12); interim, raise the repeat threshold or exempt numeric-bearing lines.

**Impact.** Correctness (number-HALLU-adjacent silent loss). Blast radius: menus/price lists with repeated standalone value lines.

---

### CS-11 · MEDIUM · asyncpg 32,767 bind-param ceiling on large row-per-chunk sheets

**1. Problem (repro).** A ~500K-char catalog sheet on the raw_bytes path → ~4,000 row chunks → single INSERT binds 4,000×11 ≈ 44,000 params → asyncpg raises (int16 protocol limit 32,767) AFTER embed cost was paid → doc stuck/failed.

**2. Direct cause.** `_bulk_insert_chunks` (`ingest_helpers.py:204-241`) emits ONE `INSERT ... VALUES (...),(...)` with 11 params/row (+1 shared `_bot_id`, 12 with `parent_chunk_id`). Ceiling ≈ 32766/11 ≈ 2,978 rows/statement. `MAX_DOCUMENT_CONTENT_CHARS = 500,000` permits sheets far above that (the code's own docstring cites a real "3851-chunk document", `__init__.py:477`).

**3. Root-cause chain.** `INSERT fails on big sheet` ← `single multi-row INSERT unbounded by row count` ← `no batching of the value tuples` ← **immutable cause: the bulk writer assumes N stays under the driver's bind ceiling; row-per-chunk formats (Excel/Sheets/CSV) break that assumption at realistic sizes.**

**4. Expert solution.** Chunk the INSERT into batches ≤ `~2900/params_per_row` rows (or use `executemany`/`COPY`). SOTA: bounded batch writes (the same class as Async Rule 6 bounded gather). Short: loop the value-clause build in sub-batches. Mid: switch to asyncpg `copy_records_to_table` for row-heavy docs. Long: stream-insert during embed so cost isn't sunk before the write.

**5. Patch or expert?** Batching is the correct fix; note it must run BEFORE embed cost is sunk to avoid paying then failing.

**Trade-offs.** Multiple INSERTs per doc (minor); simpler than COPY and keeps the whitelist-validated SQL.

**Impact.** Correctness — large catalogs fail after paying embed. FACT for the math; live occurrence is HYPOTHESIS (needs a >3k-row sheet load test).

---

### CS-12 · MEDIUM · `page_number` never persisted → citations cannot point at a page

**Chain (condensed).** Problem: PDF citations on the default path cannot cite a page. Cause: `Block.page_number`/`Chunk.page_number` exist in the domain, but `_bulk_insert_chunks` col list (`ingest_helpers.py:186-198`) has no page column and no ingest path writes page into `metadata_json` (grep `page_number` in document_service+repositories = 0). Root: page provenance is dropped at the flatten boundary (worker joins block content, discards `page_number`; the default `kreuzberg_markdown` parser returns ONE block with no page info by design). Expert fix: persist `page_number` into `metadata_json` (no schema migration) at the flat/parent/child insert sites, sourced from the block stream once CS-1/CS-2 deliver real blocks. SOTA: reference `get_page_info` interval-overlap per chunk (refs M4). Impact: UX citation granularity; HALLU-adjacent (can't verify a claim against a page).

---

### CS-13 · MEDIUM · CleanBase Tier-0 sanitizer never wired (built-but-not-wired)

**Chain (condensed).** Problem: HTML-tag strip / zero-width removal / NFC / blacklist Tier-0 never runs for ANY tenant. Cause: `ingest_stages.py:310` `getattr(self,"_sanitizer",None)` → always None (no `sanitizer` param in `DocumentService.__init__`, zero `self._sanitizer=` sites in src). Flag `cleanbase_tier0_enabled` default True is a permanent no-op ("no_sanitizer_wired"). Root: S2 last-mile DI gap — the orphan factory `infrastructure/safety/registry.py:build_sanitizer` was never bound in `bootstrap.py` nor passed to the service. Expert fix: add the `sanitizer` DI param + bind `build_sanitizer` in bootstrap, or delete the dead branch + flag. Impact: only the legacy regex sweep in `_clean_document_text` defends; sanitize-report observability永远 empty. Latent (no active tenant depends on it) — but the flag lies.

---

### CS-14 · MEDIUM · Money-shape vocabulary decides table STRUCTURE (metadata-dictates violation)

**Chain (condensed).** Problem: header-vs-data, section-vs-data, and merged-cell forward-fill all pivot on a VN+EN money vocabulary → non-money / non-VN tables mis-structure. Cause: `tabular_markdown.py:43-46` `_MONEY_UNIT_RE=(triệu|...|vnd|tr|đ|k|m)` IGNORECASE drives `_is_pure_money` (`:60-72`) → `_looks_header` money-veto (`:93-102`) and `_has_money` gates forward-fill (`:203`). Runtime consequences re-verified §3. Root: currency/language used as a STRUCTURE dictator, not a hint (violates `metadata-optional-hint` + `multilingual-no-vocab`). Expert fix: structure by FORM (label-shaped vs value-contrast vs separator-follows) — the `table-header-detect-structural` skill — and treat money as one optional value-shape among many, currency-config-driven not baked. Impact: T-2 (year headers vetoed), T-8 (`30 m`→30M), text-only tables (T-4), multi-currency (T-11). Blast radius: any non-VND / measurement / text-only sheet.

---

### CS-15 · MEDIUM · `chunk_type` misses row semantics for `parser_preserve` / `table_dual_index`

**Chain (condensed).** Problem: Excel/Sheets rows (strategy `parser_preserve`) and dual-index rows are labeled `table`/`text`, not `table_row`. Cause: `chunk_type_for(..., is_table_row=(_chunking_strategy=="table_csv"))` at all insert sites (`store.py:817-821,921-925,1029-1032`) — only literal `"table_csv"` short-circuits; other row strategies fall through to the heuristic classifier. Root: the row-semantics signal is hardcoded to ONE strategy literal instead of a capability set (`ROW_PRESERVE_CHUNK_STRATEGY`/`CR_ROW_GATED_STRATEGIES` constants exist but are bypassed — F14). Expert fix: derive `is_table_row` from a `strategy in ROW_ATOMIC_STRATEGIES` set (or a parser-port `emits_row_chunks` capability). Impact: any downstream keying on `chunk_type='table_row'` (modality rerank, analytics) mis-treats parser-preserved rows.

---

## 3. Runtime re-verification of the "11 broken tabular shapes"

Executed the actual modules this session (`python3` on `number_format` + `tabular_markdown` + `document_stats`):

```
T-6 dotted date → price:   rows_to_structured_markdown([["Tên","Ngày hết hạn","Giá"],["Serum A","31.12.2026","500000"]])
                           → ParsedEntity(name="Serum A", price_primary=31122026, price_secondary=500000)
                           # the expiry DATE became price_primary; real price demoted (first-money-wins)
T-7 space thousands:       parse_money_vn("1 600 000") == 1 ; parse_money_vn("1 600 000 đ") == 1
                           # < 10_000 floor → price silently dropped at ingest
T-2 year-only header:      _is_pure_money("2024") == True → _looks_header(["2024","2025","2026"]) == False
                           # money veto rejects the year header row → table never opens → T-1 cascade
T-1 headerless table:      rows_to_structured_markdown([["Serum X","500000"],["Cream Y","300000"],["Mask Z","200000"]])
                           split_markdown_to_row_chunks → chunk = "| Serum X | 500000 |\n| Mask Z | 200000 |"
                           # Mask Z's chunk carries "Serum X / 500000" as its column labels (cross-row mis-bind)
```

All four reproduce exactly the pass-1 (`code-shared-data.md`) outputs → **CONFIRMED at runtime**. These are engine-level (stats/tabular) defects that apply on both paths; they compound CS-2/CS-3 for tabular corpora. (The remaining shapes T-3/T-4/T-5/T-8/T-9/T-10/T-11 were re-read but not re-executed here; pass-1 executed them and I found no contradiction in the source.)

---

## 4. NEW findings (pass-1 L1 under-weighted or missed)

### NEW-1 · CRITICAL · PII redactor DI singleton frozen to `null` at boot — second, independent PII break
Pass-1 F2 flagged only the config-gate break (config_service not passed). RE-READ of `bootstrap.py:447-450` shows a SECOND, independent break: `pii = providers.Singleton(build_pii_redactor, provider=DEFAULT_PII_REDACTOR_PROVIDER)` binds the provider to the constant `"null"` (`_13:100`) at boot. `pii_redactor_provider` is in the `bootstrap_config.py:61` whitelist (readable) but is NEVER wired into the DI graph — the comment "resolved PER-CALL from system_config" (`bootstrap.py:441-442`) is false. So even if F2 is fixed, `container.pii()` returns `NullPiiRedactor` (passthrough) forever. **PII is dead at BOTH the gate and the provider** — folded into CS-4. FACT (source + constant).

### NEW-2 · MEDIUM · The whole-doc row-shape guard AND the mime re-sniff share the same raw_bytes dependency as F4 — a single unwired input disables three protections at once
`ingest_core.py:264` `if raw_bytes is not None: mime_type = sniff_real_mime(...)`, `:318` `if raw_bytes is not None:` (parser_row_chunks), and the whole-doc guard's `parser_is_row_shaped` (`ingest_stages.py:433,167`) ALL key off `raw_bytes`. On the canonical worker path (no raw_bytes) all three are inert simultaneously: (a) no mime re-sniff, (b) no parser_row_chunks, (c) no whole-doc row protection. Pass-1 treated these as separate findings; the immutable cause is ONE — the worker doesn't pass raw_bytes (CS-2). Fixing CS-2 (thin-adapter) lights all three. FACT.

### NEW-3 · LOW–MEDIUM · Stats extractor + tabular grammar are format-siloed to XLSX/Sheets/DOCX-tables — PDF/HTML/PPTX price tables never get the crux grammar
`rows_to_structured_markdown` callers are only excel/sheets/docx (grep). A price table inside a PDF or HTML rides the kreuzberg-markdown path and never sees section-binding / split-header merge / row-atomic treatment; its quality bar is kreuzberg's pipe-table re-derivation, not the grammar. CLAUDE.md declares "một output markdown-CÓ-CẤU-TRÚC thống nhất" for every format — the crux grammar is siloed. This is the parity gap that CS-2 (row-preserve unreachable on canonical path) makes worse for the formats that DO have the grammar. FACT (from `code-shared-data.md` F17, re-confirmed by grep here); flagged as NEW-for-L1 because the L1 code reports focused on Path A/B, not on the PDF/HTML table blind spot.

---

## 5. What is genuinely good (EVOLVE, don't rewrite — for balance, all FACT)

- `detect_parser_robust` order (declared mime → registry → byte-sniff → registry) is correct and runtime-verified for octet-stream PDF; OOXML `[Content_Types].xml` zip-manifest peek disambiguates xlsx/docx/pptx — stronger than naive `PK\x03\x04` (`mime_sniff.py:72-96`, `registry.py:153-179`).
- `KreuzbergMarkdownParser` (the registry path, distinct from the broken OCR adapter) correctly uses `extract_bytes_sync` + `OutputFormat.MARKDOWN` — runtime-verified emitting headings + pipe tables. The bug is ONLY in the OCR fallback adapter.
- Purge consolidation: all delete/replace paths route through `_purge_content_tables` (chunks + stats) with whitelist-guarded table names; semantic_cache invalidated on mutation; corpus_version busted.
- Doc-level sha256 dedup + `(record_bot_id, source_url)` UPSERT + chunk-level hash-diff re-embed = the LlamaIndex docstore-upsert pattern at finer grain (at-SOTA per `web-ingest-formats` §7).
- Multi-row stacked header merge + merged-cell forward-fill + form-based header detection (`tabular_markdown.py:105-212`) EXCEEDS the Ekimetrics reference (single-row-header only) — the crux grammar itself is strong; the problem is its reach (siloed formats, VND vocab), not its quality.
- Idempotent stats write (delete-before-insert regardless of is_reindex) is the RIGHT intent for at-least-once delivery — the F5 bug is the INPUT set (changed-only), not the delete discipline.
- Broad-except sweep in scope is policy-compliant (all `except Exception` carry `# noqa: BLE001` + best-effort/entrypoint justification); no version-refs, no brand/tenant literals, no `if provider ==` ladders in the ingest orchestrator (F14's provider-name frozenset is the one boundary leak).

---

## 6. Root-cause synthesis — three immutable causes behind the L1 findings

1. **Parallel parse path (not thin adapter)** → CS-2, CS-1-reachability, NEW-2, CS-9-worker, CS-12. The worker parses + flattens + re-feeds a string with no raw_bytes; every raw-bytes-gated capability inside `ingest()` is unreachable on the canonical path. Fix = collapse worker to adapter over `ingest()` (`canonical-ingest-flow`).
2. **Last-mile DI wiring gaps (built-but-not-wired)** → CS-4 (PII ×2), CS-13 (sanitizer), CS-5 (dangling diff helpers). Features shipped with green mock tests but the DI/plumbing seam was never closed. Fix = wiring audit + one un-mocked integration test per registry that exercises the real class.
3. **Half-adopted invariants / happy-case box** → CS-6 (coverage detect-not-repair), CS-8 (selector strategy-invariant), CS-7 (language default not detected), CS-10/CS-14 (VN/money-shape structure decisions), CS-3 (stats input coupling), CS-11 (bind ceiling). The reference disciplines (repair+assert, evaluate-then-select, language-as-data, form-based structure) were adopted in name; the enforcement loop was not. Fix = close each loop at the layer of the root cause, never a symptom patch one layer down.

None of these is "wrong architecture" — Hexagonal / Port+Registry+DI / 4-key / structured-markdown IR are all correct and, where wired, expert-grade. The L1 defects are unwired loops, parallel paths, and happy-case gaps. EVOLVE, don't rewrite.

---

## 7. Compliance & FACT/HYPOTHESIS register (rule #0)

- **FACT (source re-read this session)**: F1 diff-dangling (AST), F2 config-omit + `DEFAULT_RECAP_PII_ENABLED=False`, NEW-1 DI-frozen null provider, F3 sanitizer unwired, F4 raw_bytes gate + `_parser_row_shaped`, F5 `chunks_to_embed` changed-only + delete-all, F7 param count, F9 uuid4 tenant, F10 cleaner, F11 language, F13 no OLE2, F16 chunk_type, CS-6 coverage log-only, CS-8 `compute_intrinsic_metrics(text)`, M4 no page column.
- **FACT (runtime probe this session)**: CS-1 `extract_bytes` coroutine → `()` blocks (kreuzberg 4.9.7); tabular shapes T-1/T-2/T-6/T-7 (modules executed, outputs quoted §3); `DEFAULT_RECAP_PII_ENABLED=False`.
- **HYPOTHESIS (needs a traced ingest / load test)**: exact live strategy pick per tabular doc on Path B (F4 magnitude); whether a live doc has hit the 32,767 bind ceiling (CS-11); the paper-PDF "Rule-Based Selector" section claim (CS-8 — code-absence is FACT); downstream behaviour (refuse vs wrong-answer) after each mis-parse.
- **Sacred-rule check for all proposed fixes**: every fix is at the ingest/storage layer or offline eval — none injects text into the answer LLM or overrides the answer (#10 safe). Content/config changes route through alembic/system_config (#7). No brand/domain literals introduced (domain-neutral). Currency/language moves TO config, not baked (metadata-hint). 4-key identity untouched. HALLU=0 preserved (CS-1/CS-3/CS-6/CS-10 REDUCE silent-loss/misinterpret HALLU classes; none adds a fabrication surface).
- **NOT verified at runtime**: no ingest was executed end-to-end through `POST /documents/create`, no DB rows queried (psql auth unavailable) — the async OCR bug, PII deadness, and tabular mis-parses are proven by direct code + isolated-module runtime probes, not by a full traced ingest. Per the `ingest-backward-trace-debug` skill, a single traced ingest (chunk → retrieve → topK → prompt → answer) on one tabular doc via the canonical API is the outstanding VERIFIED-vs-baseline gate before claiming any %.
