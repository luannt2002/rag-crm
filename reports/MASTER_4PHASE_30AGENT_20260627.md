RAGBOT INGEST/CHUNK FLOW — MASTER REPORT & FIX PLAN (AdapChunk 7-layer, 2026-06-27)

> Synthesis of 30 agents across 4 phases (P1 gold standard, P2 ideal flow, P3 code-vs-standard, P4 verify). Every load-bearing claim in §0/§3/§4/§5 was re-verified by the synthesizer against live source (file:line shown). CLAUDE.md rule#0 honoured: claims tagged SỰ THẬT (code-evidence verified) vs GIẢ THUYẾT (needs runtime measurement).

---

## 0. TL;DR VERDICT — is our flow standard?

**NO — PARTIAL/REJECTED on the CRUX layer.** The framework is expert-grade (Hexagonal/DDD, Port+Registry+DI, Null-Object, narrow-except, zero-hardcode of numerics, byte-sniff type detection, idempotent bot-scoped chunk ids, graceful degradation). The problem is exactly what the charter predicted: **"dây chưa nối hết" (wiring not finished) + a domain-coupled table layer**, NOT a wrong skeleton. EVOLVE, do not rewrite.

**THE #1 BUG (P0, CRUX) — header detection is gated on a vocabulary word-list, not structure.** VERIFIED:

```
src/ragbot/shared/document_stats.py  _is_header_row():
    if parse_money_vn(col) is not None: return False      # any money cell ⇒ "not header"
    if normalised in _HEADER_EXACT_TOKENS or declared_labels: has_label_match = True
    return has_label_match                                 # ← vocab is the SOLE gate
```

`_HEADER_EXACT_TOKENS` (document_stats.py:155-205) is ~70 hardcoded VN/EN words (`ten`, `name`, `gia`, `price`, `dich vu`, `gia ban (vnd)`…). A correctly-shaped non-VN/non-VND table header (`MARKS | CARGO DESCRIPTION`, Spanish `Producto | Precio`, legal `Điều | Khoản`) matches nothing → the row is NOT recognized as a header → table collapses to positional `col_N` or drops. This single decision makes us the **only reference** that fails multi-language + multi-domain. All 9 P1 gold-standard agents + 4 external refs (tldw, RAG-Anything, adaptive-chunking, open-notebook) converge on the opposite law: **header-ness is STRUCTURAL** (a `^[\s\|:\-]+$` separator line / label-shape + no value-cell + value-contrast next row), never lexical.

**Cruel irony confirmed:** our own converter already does it right. `tabular_markdown.py:90 _looks_header()` decides structurally (≥2 label-shaped cells, no pure-money cell) and emits the `| --- |` separator — but the extractor (`document_stats.py`) re-parses from scratch with vocab and throws the separator away. **Two header oracles = guaranteed drift.**

### Biggest gaps (ranked, all VERIFIED file:line)

| # | Gap | Severity | Evidence |
|---|-----|----------|----------|
| 1 | Header detection vocab-gated (CRUX `col_N`) | **P0** | document_stats.py:290-300 + 155-205 |
| 2 | Single-currency VND value-cell test gates table/header classification | **P0/P1** | tabular_markdown.py:40-43 `_MONEY_UNIT_RE`; number_format `parse_money_vn` |
| 3 | Wired LLM narrate prompts hardcoded Vietnamese (contradicts own "preserve source language") | **P0** | llm_narrate.py:53 vs 59-72 |
| 4 | Parallel `/documents/upload-stream` endpoint (CLAUDE.md HEADLESS §2 forbids) | **P0** | documents_stream_upload.py EXISTS |
| 5 | Version-ref filenames (`_13_…layer_1…`, `_19_sprint3…`, `_21_…wb_2_p1_5`, `_16_…phase_b`, `_17_260509…`) | **P0** | `ls shared/constants/` |
| 6 | Tầng-4 LLM Strategy Selector Port layer is ORPHAN dead code (0 runtime callers) | **P1** | `chunking_strategy_provider` read by nothing; resolvers test-only |
| 7 | `smart_chunk_atomic` (the only producer of `original_content`/`block_types`/`structural_path` on the Chunk entity) is test-only — never wired | **P1** | __init__.py:653 + comment :676 "Wave B2 will wire" |
| 8 | `original_content` (§7.3 HALLU=0 anchor) never read back at retrieval — dual-read round-trip broken end-to-end | **P1** | retrieve.py has 0 raw_chunk reads |
| 9 | NO lossless-coverage assert anywhere (`check_chunk_gaps` = 0 hits in src) | **P1** | grep src = NONE |
| 10 | `document_worker.py:623` hardcodes `strategy_used="SEMANTIC"` for every doc (record-of-truth bug) | **P1** | VERIFIED literal |
| 11 | VN structural markers compiled at import from default-lang slot; call sites pass no locale | **P1** | vn_structural.py:55-89; ingest_stages.py:451,547,604 |
| 12 | `detected_language` computed but never selects embedding model (multi-lang routing absent) | **P1** | ingest_stages.py:669 logged only |
| 13 | Narrate-then-Embed default OFF + provider 'null' → core Tầng7 transform inert in prod | P2 | alembic 0230 |
| 14 | Block-Integrity scorer exists but offline-only (not an ingest gate) | P2 | scripts/score_chunks_intrinsic.py |

---

## 1. THE EXPERT STANDARD (per AdapChunk layer, condensed from Phase 1)

One cross-cutting law unifies all 4 mature refs: **structure is detected from MARKUP/GRAMMAR/SHAPE and driven by config tables — never from a domain word-list.**

- **L1 OCR/Parse → ONE canonical typed-block IR.** Every parser is an adapter emitting the SAME contract (`{type, content/table_body/latex, text_level, page_idx, char_start, char_end}` + doc-level `split_points:[int]`, `titles:[{title,start,end,level}]`). Type detection = mime→ext→**byte-sniff as hard authority** (octet-stream/spoofed-ext URLs route wrong otherwise; tldw Upload_Sink.py:677-690 "Do NOT fall back to extension-derived MIME"). Add format = add one adapter (RAG-Anything register_parser, override-guard parser.py:2441). Carry char offsets so lossless-coverage is computable.
- **L2 Block Detection.** HEADING level = `len(match.group(1))` on `^(#{1,6})\s+(.+)$` (4 refs converge). TABLE + its header row = the `^[\s\|:\-]+$` separator line positionally (tldw structure_aware.py:494); header-less tables pad to `max(len(row))` and emit grid — never synthesize `col_N` for a vocab miss (RAG-Anything utils.py:34-58). Alias-unify fields at the boundary (`table_body→table_data→text`). Atomic blocks (TABLE/FORMULA/IMAGE) marked never-cut + context-bound.
- **L3 Feature Extraction → Document Profile.** Pure deterministic reduction over the typed-block IR: heading_counts by '#'-count, table_count via separator, mixed_content_score = non-TEXT/total, `detected_language` by **Unicode script-range** (tldw multilingual.py:49-116 — but its table lacks `vi`, the trap to avoid). 100% code, no LLM (it is the ground truth that polices the selector). All thresholds in config.
- **L4+L5 Strategy Selector + Cross-check.** The strongest evidence (adaptive-chunking analysis.py:294-327) shows selection by **measured intrinsic metrics** (NaN-skipping weighted argmax over RC/ICC/DCC/BI/SC), not LLM opinion — intrinsic mean 91.07 vs best-fixed 90.7, answered 65/99 vs 49/99. If an LLM is used it is one **validated, overridable** signal: validate-then-degrade (LLMRegexSplitter re.compile→auto-repair→`[text]`, splitters.py:545-578), then deterministic profile rules veto structural impossibilities + log every override.
- **L6 Executors.** HDT structural_path = level-stack breadcrumb over a GLOBAL header index (tldw 696-722); section span = next-title-of-≤level (adaptive parsing.py:462-471). SEMANTIC = data-driven dissimilarity threshold **bounded by token budget**, not a tuned cosine literal (splitters.py:362-381). PROPOSITION = swappable engine emitting pronoun-free self-contained facts with offset-preserved spans (atomic blocks held OUT of the LLM). HYBRID = HDT macro + PROPOSITION micro. Never-cut-atomic enforced ONCE in a shared pre-pass + size-regularization (re-split>max / merge<min) AFTER any strategy.
- **L7 Narrate-then-Embed.** Dual-payload: embed narration (NL vector for recall) + persist verbatim `original_content` (LaTeX/grid) in metadata for generation-time exact-number read (RAG-Anything table_chunk/equation_chunk templates, identical en+zh — proving language-neutrality). One base processor + injected caption-fn + per-modality subclass (Port+DI). Context-bind before narrating. Idempotent content-hash upsert; temperature-0 for reproducibility.
- **§8 Eval.** Always-on deterministic ground-truth-free gate (Block-Integrity + size_compliance + **lossless-coverage assert** `check_chunk_gaps`==True), THEN 6-config ablation (Baseline-512 / HDT / SEMANTIC / PROPOSITION / AdapChunk-full / AdapChunk-no-crosscheck) with a **strong** baseline through end-to-end RAGAS, stratified by question type. Two-track: intrinsic = hypothesis, extrinsic = evidence; both must move the same direction.
- **AVOID (counter-lessons, all refs):** English-only NLP (langdetect-English-gated coref, Stanza/spaCy 'en', capital-letter splitting, English wh-word routers); extension-only dispatch + office→PDF→OCR funnel (lossy for inline math/tables); inline magic numbers; provider hardcodes (`if provider==`).

---

## 2. IDEAL end-to-end flow + data contracts + risks (Phase 2)

**Contract chain (single hand-off per layer):**
`bytes → [L1 byte-sniff→parser-adapter] → canonical {full_text, pages, ordered typed-blocks[char_start/char_end, is_atomic, ocr_metadata], titles, detected_language} → [L2 tag+atomic+context-bind] → [L3 deterministic Document Profile] → [L4 selector(profile+full block list)→{strategy,confidence,reasoning}] → [L5 rule cross-check over profile→final_strategy+override_audit] → [L6 executor, never-cut-atomic pre-pass, size-regularize, lossless-coverage ASSERT] → [L7 narrate-only embed + original_content metadata + idempotent upsert] → eval(end-to-end, never chunk-in-isolation).`

**Key ideal decisions** (agreed across P2 agents):
- OCR is ONE branch (a parse MODE: born-digital text-layer vs scanned), not all of L1 — forcing OCR on born-digital docs wastes cost and introduces transcription HALLU.
- Two-phase parse: persist engine-native RAW, derive canonical separately → resumable, never re-pay OCR.
- TABLE = exactly ONE verbatim atomic block (the col_N fix at the contract level); `original_content` separated from narration so flattening is reversible.
- Confidence + cross-check are **asymmetric** safety layers: low confidence collapses any pick to HYBRID first; profile rules then veto even high-confidence structural impossibilities (HYBRID can't "confidence its way past" <5 headings).

**Top risks the ideal flow must guard:**
1. **Fragile char-offset substring contract** — L6 PROPOSITION (rewrites prose) and L7 narration (rewrites non-prose) both break substring-to-`full_text` matching → store spans at creation, not post-hoc.
2. **OCR single point of failure / silent number corruption** — a wrong digit becomes faithful-but-wrong `original_content` (Faithfulness 1.0, answer wrong). Native formats must bypass OCR; swappable Port + circuit-breaker.
3. **Spec omits the lossless-coverage gap-repair invariant** the LREC ref relies on — SEMANTIC/PROPOSITION can silently drop inter-block prose (answer vanishes, Faithfulness still 1.0 = the "honest but blind" failure).
4. **HYBRID is an absorbing state** — no rule overrides OUT of HYBRID, so a mis-extracted profile silently lands there; only the offline no-cross-check ablation surfaces it.
5. **Per-doc LLM selector + per-section PROPOSITION + per-block narration = 3 stacked LLM passes** at ingest → T2 cost/latency; the LREC paper deliberately avoided the LLM selector (used 5 intrinsic metrics). Needs content-hash cache + cheap model + pure-rule fast-path.
6. **Cross-lingual mismatch** — narration language ≠ query language → recall drops while faithfulness holds.

**Methodology caveat (P2 verify):** the assigned survey PDF `2402.13116v4` is the WRONG paper (Knowledge Distillation; grep: chunk=0, ragas=0, block-integrity=0). The real source for §8 is the ekimetrics "Adaptive Chunking" poster. Any claim that 2402.13116 validates the eval is fabrication.

---

## 3. OUR CURRENT FLOW mapped to the 7 layers (Phase 3) — present / missing / violations

| Layer | Present (✓ clean) | Missing vs spec | Violations (file:line · severity) |
|---|---|---|---|
| **L1 Parse** | Port+Registry+Null DI (registry.py:45-89); mime→ext→byte-sniff (registry.py:123-179, mime_sniff.py:99-163); Kreuzberg substitutes Mistral OCR; narrow-except dir-wide (0 broad); size caps from constants | Typed-block IR not emitted (parsers return flat markdown); FORMULA detection absent in ALL parsers; page_number only on legacy pdf path; atomic-block tagging not at parse time | Excel/Sheets table classification → `parse_money_vn` VN single-currency (excel_openpyxl_parser.py:22 + tabular_markdown.py:24 · **P1**); DOCX heading English-style-name-only (docx_parser.py:36 `startswith("heading")` · P2 — VERIFIED); worker URL path uses strict `detect_parser` not `_robust` (document_worker.py:428 · P2) |
| **L2 Block / CRUX** | `_looks_header` structural (tabular_markdown.py:90 — CORRECT); domain-neutral state machine; multi-row header merge (document_stats.py:783-833); 3-tier role cascade (413-493) | Spec Document-Profile features unimplemented in these files (this is a price/catalog index, out-of-spec) | **`_is_header_row` vocab-SOLE gate (document_stats.py:290-300) · P0 — VERIFIED**; `_HEADER_EXACT_TOKENS` ~70 VN/EN words (155-205) · P0; single-currency `_MONEY_UNIT_RE`/`parse_money_vn` value-cell test gates whole classification · P0; two divergent value-cell tests (`parse_money_vn` vs `_is_pure_money`) · P1; VN discourse-opener lists (75-93) · P1 |
| **L3 Feature Extract** | Clean entity+Port+Registry+Null (document_profile.py, doc_profile_port.py, rule_based_doc_profile.py); all thresholds in DEFAULT_* constants; flag-gated default-OFF | Entity is TELEMETRY-ONLY — computed then discarded (ingest_stages.py:648, comment 624-627 "NOT yet wired"); two divergent feature impls (entity vs dict path) | `detected_language` hardcoded VN-vs-auto binary, cannot emit other locales (rule_based_doc_profile.py:61-86 + VN_DIACRITIC_CHARS) · **P1**; live dict path hardcodes VN clause/heading markers (analyze.py:156-201) · P2 |
| **L4 Selector** | Port+Registry+DI factory (registry.py:24-49); domain-agnostic LLM instruction (judges shape only); graceful degrade to rule resolver; out-of-vocab strategy rejected | Spec §4.1 "no truncate" violated (max_blocks=60); detected_type/risk_factors parsed then dropped | **Entire resolver layer ORPHAN — 0 runtime callers, `chunking_strategy_provider` read by nothing (registry.py+llm_resolver.py+rule_resolver.py) · P1 — VERIFIED**; max_blocks=60 inline magic (llm_resolver.py:87) · P2; misleading feature_flag log field (217) · P2; ChunkingStrategyName Literal vs lowercase/'recursive' drift · P2 |
| **L5 Cross-check** | IS implemented + config-driven + WIRED (analyze.py:551-688, all 6 thresholds from get_boot_config; called ingest_stages.py:575-590) | Never applied to LLM-selected strategy (selector is orphan) | — |
| **L6 Executor** | `_ATOMIC_BLOCK_TYPES` + 6-way block typing (blocks.py:146,184-276); atomic-protect flag config-gated; 5-strategy selector + cross-check; CSV/table linearization; idempotent bot-scoped chunk ids (chunk_identity.py) | **NO `check_chunk_gaps` lossless-coverage assert anywhere in src (VERIFIED 0 hits)**; SEMANTIC/PROPOSITION executors out-of-slice; context-buffer only for OCR block stream, not registry parsers; FORMULA/IMAGE get no context buffer | VN markers compiled at import from default slot, call sites no locale (vn_structural.py:55-89; ingest_stages.py:451,547,604) · **P1 — VERIFIED**; TOC literals 'mục lục'/'table of contents' (analyze.py:278,346) · P2; `'[Document context: ...]'` English label into embed text (late_chunking.py:83,256) · P2; `context_prefix_chars=200` magic (late_chunking.py:59,161) · P2; `vn_`/`_VN_` naming on lang-parameterizable logic · P2 |
| **L7 Narrate+Embed** | Embedding Port+Registry+Null+DI (zero if-provider); dims/models/endpoints from constants, URLs from env; narrate Port + dual-content persistence + HALLU=0 raw fallback; CircuitBreaker+retry+key-pool; storage-only (no app-inject, Gate#10 honored) | `original_content` has no live persistence path — `smart_chunk_atomic` (only producer) is test-only; never read back at retrieval (round-trip broken); `detected_language`→embedding-model routing absent; multi-vector registry dead code | **Wired `LLMNarrateGenerator` prompts hardcoded Vietnamese (llm_narrate.py:59-72) contradicting line 53 "Preserve the source language" · P0 — VERIFIED**; rule-based $0 `table_narrator` orphaned (narrate/__init__.py:19 only, not in active funnel) · P1; two divergent prompt families (EN constant vs VN inline) · P1; English structural labels in table_narrator (59-62) · P2; metadata schema drift flat vs parent-child path (ingest_stages_store.py:691 vs 889) · P2 |
| **End-to-end** | Worker = thin adapter → DocumentService.ingest thin orchestrator (U1-U7); one canonical funnel (registry→byte-sniff→OCR); lossless-leaf-coverage state gate (ingest_stages_final.py:194-216, floor 0.8) | L4-as-LLM substituted by rule selector (defensible deviation); page_number not threaded; n_pages=None | **`document_worker.py:623 strategy_used="SEMANTIC"` literal masks real per-doc strategy · P1 — VERIFIED**; narrate dispatch 2-branch ladder (535-549) · P2; stale flag-default comment (515-524) · P2 |
| **§8 Eval** | End-to-end collector (eval_collect.py:62-70); golden-per-bot externalized; layer-attribution step_of (eval_replay_debug.py:29-35); transport-vs-client split | No chunk-boundary/Block-Integrity gate in eval slice; no 6-config ablation; no lossless-coverage assert | VN-only refusal phrases (eval_replay_debug.py:34) · P1; single-currency `DEFAULT_PRICE_BUCKETS_VND`/MIN/MAX (constants _21:57-69) · P1; `_VI`-suffixed query-pattern tuples not by-lang (_21:166,184) · P1; `BASE` hardcoded no env (eval_collect.py:11) · P2 |

---

## 4. CLAUDE.md MINDSET compliance verdict (pass/fail per rule, with evidence)

| Sacred rule | Verdict | Evidence |
|---|---|---|
| **Zero-hardcode (numerics)** | **PASS (mostly)** | Thresholds in shared/constants; dims/models/endpoints from constants, URLs/tokens from env. Leaks: max_blocks=60 (llm_resolver.py:87), context_prefix_chars=200 (late_chunking.py:59), eval BASE (eval_collect.py:11) — all P2. |
| **Zero-hardcode (VOCAB/behavior)** | **FAIL** | `_HEADER_EXACT_TOKENS`, `_MONEY_UNIT_RE`, VN discourse/heading/TOC/refusal word-lists, `DEFAULT_PRICE_BUCKETS_VND` baked into structure-deciding paths. |
| **Domain-neutral** | **FAIL** | document_stats.py price/catalog coupling + VND magnitude window applied to every doc (a price-less or non-VND table goes blind). |
| **Multi-language** | **FAIL (P0/P1)** | Header vocab VN/EN-only; narrate prompts VN-only (wired); VN markers compiled at import no locale-threading; detected_language VN-vs-auto binary. The CORRECT pattern (`_24` by-lang dict, vi/en/ja) exists but is bypassed — regressions against the team's own convention. |
| **Multi-format** | **PASS (parse) / partial** | Byte-sniff funnel + per-format native parsers clean. But single-currency value-cell test couples format to VND; `strategy_used` literal flattens per-doc reality. |
| **Per-bot (no per-bot logic in core)** | **PASS** | No bot-name branches in orchestration; behavior via plan_limits/system_config/custom_roles. |
| **No version-ref** | **FAIL (P0)** | Filenames `_13_…layer_1…`, `_19_sprint3…`, `_21_…wb_2_p1_5`, `_16_…phase_b`, `_17_260509…`; comments 'Wave M3', 'Phase D2', 'Bug #9', '260525-4BUG'. |
| **No app-inject text into LLM answer** | **PASS** | Narration is INGEST-time corpus representation (storage-only), not answer-path injection — Gate#10 explicitly honored. |
| **No app-override LLM answer** | **PASS** | No math_lockdown / blocked_answer in slice. |
| **HALLU=0 anti-fabricate** | **PARTIAL** | Raw-fallback on narrate failure + lossless-leaf state gate are good; BUT `original_content` dual-read round-trip is BROKEN (never read at retrieval) and no `check_chunk_gaps` assert → silent number-drop / answer-vanish risk is unguarded (GIẢ THUYẾT until load-test). |
| **HEADLESS single canonical ingest** | **FAIL (P0)** | Parallel `documents_stream_upload.py` exists — CLAUDE.md HEADLESS §2 forbids ("CẤM thêm endpoint upload song song … phải gỡ"). |
| **Broad-except policy** | **PASS** | Narrow-except dir-wide; the few `except Exception` carry `# noqa: BLE001` + reason (2 in chunk_quality/strategies should narrow further — P2). |
| **Strategy + DI** | **PASS (pattern) / FAIL (wiring)** | Pattern textbook everywhere; but the L4 selector Port is orphan (anti-pattern "ship strategy stubs orphan") and `smart_chunk_atomic` is built-not-wired. |
| **EVOLVE-not-rewrite** | **VIOLATED by inaction** | The "dây chưa nối hết" state the charter warns against: new layers shipped inert (entity profile telemetry-only, selector orphan, smart_chunk_atomic test-only, narrate default-OFF). |

---

## 5. THE FIX/REWRITE PLAN — domain-neutral, EVOLVE-not-rewrite, ordered

Solves ALL problems with NO hardcode, NO per-bot/format/language coupling. Each step maps to files + a standard ref. Lead with the CRUX trio.

**P0 — structural header SSoT + kill the vocab gate + coverage gate (the core fix):**

1. **Extract ONE locale-neutral `_is_value_cell(cell)`** (Unicode `\p{Sc}` currency-symbol + digit-group shape, optional per-locale unit pack as a HINT) into a shared module; call it from BOTH `_is_header_row` and `_looks_header`. Rename `parse_money_vn`→`parse_amount`. *(skill: table-header-detect-structural, metadata-optional-hint)*
2. **Promote `tabular_markdown._looks_header` to the shared structural header oracle** and make `document_stats._is_header_row` a thin wrapper: structural floor FIRST (all cells label-shaped + no value-cell + next row has value-cells / same col-count), vocab only an optional fast-path. Trust the `| --- |` separator the converter emits — never re-judge. *(removes the col_N P0)*
3. **Add the lossless-coverage gate**: implement `check_chunk_gaps(chunks, full_text)` + `repair_gaps` and assert it as an L6 exit gate after EVERY strategy; pin in a unit test. *(skill: block-integrity-quality-gate — currently 0 hits in src)*

**P0 — domain-neutral / multi-language / single-funnel hygiene:**

4. **Locale-key all word-lists**: move `_HEADER_EXACT_TOKENS`, discourse/TOC/refusal/range-query lists, narrate prompts into per-locale config packs keyed by language code (mirror `_24_structural_markers_by_lang.py`); thread doc/bot language into every structure-deciding function; default 'vi'/VND = byte-identical. Detect language by Unicode script-range, not VN-diacritic binary. *(skill: multilingual-no-vocab)*
5. **Fix wired narrate prompts**: source `_BLOCK_PROMPTS` from the locale pack; make the prompt body say "reply in the source/document language" not "tiếng Việt"; reconcile the EN-constant vs VN-inline duplication.
6. **Remove the parallel `/documents/upload-stream` endpoint** + its `_21_*` streaming constants; fold streaming into the canonical `POST /documents/create`. *(skill: canonical-ingest-flow)*
7. **Rename version-ref files** to purpose names (`_13_adapchunk_layer_1_ocr_parser`→`_NN_ocr_parser`, `_19_sprint3_ekimetrics_selector_`→`_NN_strategy_selector`, `_21_…wb_2_p1_5`→`_NN_stats_index`, `_16_…phase_b`, `_17_260509…`); strip Sprint/Wave/Phase/Bug#/date tokens from comments (WHY-only).

**P1 — wire the orphans (EVOLVE, "nối dây"):**

8. **Fix `strategy_used`**: surface resolved strategy out of `ingest()` (add `IngestResult.strategy_used` from `ctx.chunking_strategy`); pass into `DocumentIngested` instead of the `"SEMANTIC"` literal (document_worker.py:623).
9. **Wire OR delete the L4 selector Port**: either bind `build_chunking_resolver` into bootstrap DI + call `resolve_strategy`→`apply_cross_check` from ingest, OR delete the orphan package — do not leave two parallel selection paths. (Recommend wire behind the existing flag; lift max_blocks=60 to a constant; carry detected_type/risk_factors into metadata.)
10. **Wire `smart_chunk_atomic` as the canonical Block→Chunk producer** feeding the store; persist `original_content`/`block_types`/`structural_path` uniformly for ALL atomic blocks regardless of narrate flag; unify a single `_build_chunk_meta()` helper across flat + parent + child write paths.
11. **Close the dual-read round-trip**: in the retrieval/context-assembly node, when a chunk's metadata carries the raw block (table/formula), append it (fenced) to the LLM-visible content — config-gated, domain-neutral. *(HALLU=0 §7.3)*
12. **Wire the entity Document Profile into selection** (replace the telemetry-only dict path); thread `detected_language` into embedding-model selection.
13. **Wire OR remove the $0 rule-based `table_narrator`** as a registry provider ('rule' key) so operators pick the deterministic TABLE path; decide LLM-vs-rule by measured §8.3 ablation lift, not guess.

**P2 — polish:** narrow the 2 remaining broad-excepts; replace `'hdt'` string literals with `STRUCTURAL_CHUNK_STRATEGIES` membership; env-source eval `BASE`; lift `context_prefix_chars`; rename `vn_*` symbols to locale-neutral with back-compat aliases; correct stale flag-default comment.

**VERIFY GATE (rule#0):** none of the above may be reported "fixed/works" until (a) an ingest backward-trace on a non-VND/non-VN-vocab sheet shows the header recognized + chunk reaching topK, AND (b) a load-test reports Coverage + Faithfulness numbers + HALLU=0. Current findings are SỰ THẬT at the **code-evidence (STATIC)** level (file:line verified); the coverage-loss magnitude and post-fix lift are GIẢ THUYẾT until measured. *(skill: ingest-backward-trace-debug, rag-loadtest)*

---

## 6. Per-phase AGENT EFFECTIVENESS scorecard

See agent_scorecard for per-agent ratings. Summary: **P1 agents were uniformly high-signal** (every gold-standard claim re-verified against external refs with exact file:line; the cross-cutting + Tầng2 + §8 agents were the strongest, surfacing the structural-vs-vocab law and the lossless-coverage gate). **P3 agents were the highest-value of the program** — the Tầng2-CRUX agent (conf 0.93) and the narrate/worker/eval agents found the concrete P0/P1 bugs that I independently confirmed; their file:line citations were accurate on every spot-check. **P2 agents were strong on reasoning but one wasted budget on the wrong survey PDF** (correctly self-flagged it as the wrong paper — honest, but low net contribution to eval evidence). No agent was found to have fabricated a citation in the verification sweep.