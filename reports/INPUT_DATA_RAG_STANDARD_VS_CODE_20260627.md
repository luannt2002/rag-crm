# INPUT-DATA & RAG — EXPERT STANDARD vs OUR CODE (2026-06-27)

Synthesis of the refs-derived GOLD STANDARD (`/var/www/html/ragbot/reports/GOLD_STANDARD_input_data_RAG.md`) against 9 deep code-audits of the ingest → parse → chunk → embed → retrieve → rerank → grade slice. All claims are `file:line`-grounded; spot-checks re-verified live (header gate `document_stats.py:290-300`, structural header `tabular_markdown.py:90-99`, duplicate constant `_09_:141/150` vs `_21_:57/64`).

---

## 0. TL;DR verdict (are we expert-grade?)

- **THE ONE LAW is broken on the table/CSV structural backbone — P0.** Both `document_stats._is_header_row` (`document_stats.py:290-300`) and the whole `tabular_markdown` row classifier decide HEADER-vs-DATA by a **hardcoded VN/EN currency + column-word VOCABULARY** (`_HEADER_EXACT_TOKENS` + `_MONEY_UNIT_RE = triệu|nghìn|vnd|tr|đ|k|m`). A fully-custom-language or non-VND sheet with no `custom_roles` silently degrades to positional col-0 / every-row-is-a-header. This is the headline gap and it sits on the **sole** structural path for every XLSX/CSV/Sheets parser.
- **The architecture is otherwise expert-grade and EVOLVE-ready.** Port+Strategy+Registry+Null-Object is clean across parser/embedder/reranker/chunking-strategy (`registry.py` dict-dispatch, **zero `if provider==` ladders**). The seam to fix the P0 already exists: `tabular_markdown._looks_header` is a pure structural detector, and `custom_roles`/`declared_labels` already override. Fix = move vocab to config + add structural fallback, not rewrite.
- **HALLU=0 and sacred-rule #10 hold on the answer path.** Ingest is no-LLM deterministic; `guard_output` does NOT override the LLM answer; refusal text comes from `bots.oos_answer_template` (DB). The one smell is `_modality_boost.py` mutating the reranker score post-hoc (`_modality_boost.py:130-158`) — adjacent to rule #10.
- **Multi-language is the systemic weakness, not domain/brand.** Brand-literal grep is clean everywhere. But single-language coupling recurs: `has_toc` literal `"mục lục"/"table of contents"` (`analyze.py:278`), VN-only `detect_vn_structural_anchor` bound at import (`vn_structural.py:55-57`) so the shipped `en`/`ja` marker dicts are **dead code**, VN-only discourse/clause word-lists (`document_stats.py:75-93`), and VND-only price axis everywhere.
- **Two real capability gaps vs standard.** (1) **No lossless-coverage invariant** — `check_chunk_gaps` / start_char-end_char assert does not exist (grep=0), so a strategy can silently drop source text. (2) **Chunk-quality gate is dead code** (`shared/chunk_quality.py` zero callers; `infrastructure/chunk_quality/*` commented out 2026-06-03) and the **worker has a duplicate ingest funnel** (`document_worker.py:392-468`) that skips byte-sniff + flattens parser chunks — violating the canonical-funnel + no-parallel-upload rule.

**Overall: structurally expert (DI/ports/zero-hardcode/HALLU=0), but the ONE LAW is violated on the table backbone (P0) and multi-language/coverage/quality-gate are P1 gaps. NOT yet expert-grade on input-data correctness for non-VN, non-VND, non-priced corpora.**

---

## 1. THE GOLD STANDARD (refs-derived checklist A–K, condensed)

**THE ONE LAW (cross-cutting):** structure is decided by **FORM** (markup tokens, typography, char-shape, column count, byte magic), **never by VOCABULARY** (no domain/brand/single-language word lists in any structure-deciding path).

- **A — Canonical funnel:** one ingest entrypoint; every source/format converges; one structured-markdown output; content-hash idempotency; safe-replace not batch-wipe; permanent-vs-transient retry split; soft-failure sentinel detection (Error/Failed/login-page body must be caught, never embedded).
- **B — Type detection:** layered `mime → ext → byte-sniff`; declared trusted first, byte-sniff rescues octet-stream/no-ext; metadata REFINES, never DICTATES type; ext-only typing is a known gap.
- **C — Parser adapter:** Port (Protocol) + Registry (dict keyed on config string, validated `register_*`) + Strategy (one file/provider) + Null Object; new format = drop a file + a registry row, orchestrator untouched.
- **D — Structural detection:** heading level = COUNT of markup marker (any depth); element type by structural shape (pipe-row, `|---|`, fence, image syntax); separator by FORM (all-empty row); column count derived structurally with `col{i}` fallback; output preserves source order.
- **E — Chunking/template:** strategy via registry/dispatch on generic names (no `if doctype==`); atomic blocks (TABLE/FORMULA/IMAGE/CODE) never cut; header re-attached on row-split; never nuke doc to zero chunks; statistical/structural boundary not word-dictionary.
- **F — Quality gate:** block-integrity (fraction of parser split-points not cut by a chunk boundary, label-free); **lossless-coverage INVARIANT — `assert check_chunk_gaps(...)` after EVERY strategy run, fail loud**; intrinsic cohesion (ICC/DCC) as a regression gate.
- **G — Embedding/cache/warmup:** cache identity tuple = `provider:model:sha256(text)[:base_url]`; per-batch cap; idempotent embed (DELETE-then-INSERT + deterministic id); adaptive batching keyed by `(provider,model,config-SHA)`; fallback-vector poisoning guarded.
- **H — Dim-drift:** self-describing dim check at warmup (catch drift before pgvector INSERT), not at insert time.
- **I — Retrieval/rerank/grade:** RRF rank-based k=60 + dedupe; language-NEUTRAL FTS (never `to_tsquery('english')`); reranker Strategy+DI; reranker sentinel/decoy-calibration GATE (strongest anti-HALLU); parent/child + neighbor expand; tiered grade fallback (recall-only); granularity router (BROAD/SPECIFIC/FACTOID).
- **J — Metadata as hint:** metadata refines/hints, never dictates; language = pass-through metadata, behavior keyed by locale; numeric/currency policy is config not hardcoded default.
- **K — Multi-lang / zero-literal:** zero hardcoded literals in any structure path; language auto-detected by script-range; per-language behavior is DATA in config (add a language = add config, not code); all knobs config/constant-sourced (single SSoT); multi-format parity.

---

## 2. Are we standard? per-section A–K

| § | Standard (condensed) | Our status | Evidence (our file:line) |
|---|---|---|---|
| **A** | Canonical funnel, idempotency, safe-replace, soft-failure sentinel | **PARTIAL** | Service funnel conform: `document_service/__init__.py:710-779`, `ingest_core.py:421,452`, safe-replace `__init__.py:819-887`. **Violate:** worker duplicate funnel `document_worker.py:392-468`; soft-failure sentinel absent (`document_worker.py:483-491`) |
| **B** | mime→ext→byte-sniff, metadata refines | **PARTIAL** | Service path conform `registry.py:153-179` (`detect_parser_robust`), `_sniff_mime` `registry.py:123-150`. **Violate:** worker URL path skips byte-sniff `document_worker.py:428`; URL-string dictates mime `document_worker.py:404-413` |
| **C** | Port+Registry+Strategy+Null, config-string dispatch | **CONFORM** | `document_parser_port.py:21-44`; `parser/registry.py:45-89`; `null_parser.py:12-32`; embedder `embedding/registry.py:34-40`; reranker `reranker/registry.py:45-87`; **zero `if provider==`** |
| **D** | Structure by FORM (heading-count, separator, col-count) | **PARTIAL** | Conform: heading `#`-count `__init__.py:136-139`, `docx_parser.py:107`; separator all-empty `tabular_markdown.py:146`; `col{i}` `tabular_markdown.py:120-125`. **Violate:** header-vs-data gated on money-vocab `tabular_markdown.py:90-99,200`; `document_stats._is_header_row:290-300` |
| **E** | Strategy registry, atomic-never-cut, never-zero, no `if doctype==` | **CONFORM (mostly)** | `chunking/__init__.py:521-545`; atomic `blocks.py:146`; never-zero `csv_chunker.py:298-299`; `select_strategy` weighted argmax `analyze.py:382-516`. Gap: resolver Port bypassed `ingest_stages.py:563,575` |
| **F** | Block-integrity gate + **lossless-coverage assert** + cohesion | **VIOLATE** | `check_chunk_gaps`/start_char-end_char assert = **grep 0** in `src/ragbot/`; `shared/chunk_quality.py` = **0 callers**; `infrastructure/chunk_quality/*` commented-out (DEAD-CODE 2026-06-03) |
| **G** | Cache identity `provider:model:sha256`, idempotent embed, adaptive batch | **PARTIAL** | Idempotent embed conform `ingest_stages_store.py:569`; batch cap `litellm_embedder.py:168-169`. **Violate:** cache key omits provider `embedding_cache.py:22-31`; `model="unknown"` default `query_graph.py:1351,1257`; semantic-cache no provider/dim scope `semantic_cache.py:417-435` |
| **H** | Dim-drift catch at warmup | **PARTIAL** | Conform jina/ZE `zeroentropy_embedder.py:154-168`, `jina_embedder.py:246-256`. **Violate:** `LiteLLMEmbedder.health_check:114-133` only checks `bool(resp.data)`, no dim check |
| **I** | RRF, neutral FTS, reranker gate, granularity router | **PARTIAL** | Strong: RRF k=60 `multi_query_expansion.py:557-605`; FTS `'simple'` (NOT english) `pgvector_store.py:442-481`; reranker DI `rerank.py:55-108`; cliff gate `retrieval_filter.py:165-210`. **Gap:** sentinel-gate (I4) absent all adapters; granularity router (I5) absent; `rrf_round_robin.py` orphan |
| **J** | Metadata refines, language pass-through | **PARTIAL** | Conform: language pass-through `document_worker.py:482,599`, `ingest_core.py:185`. **Violate:** VN-only structural-anchor not locale-threaded `retrieve.py:1066-1071`; URL dictates mime `document_worker.py:404-413` |
| **K** | Zero literal in structure path, lang as DATA, config SSoT | **VIOLATE** | Heavy single-language coupling: `_HEADER_EXACT_TOKENS document_stats.py:155-205`; `_MONEY_UNIT_RE tabular_markdown.py:40-43`; `has_toc analyze.py:278`; dead en/ja markers `vn_structural.py:55-57`; **dup constant** `_09_:141,150` vs `_21_:57,64` |

---

## 3. ALL violations (consolidated, severity-ordered)

| Our file:line | category | sev | standard_violated | domain-neutral fix |
|---|---|---|---|---|
| `tabular_markdown.py:40-43,57-73` | single-language | **P0** | ONE LAW / D3,D7 / K2,K3 | Replace VN/EN currency alternation with Unicode `\p{Sc}` + digit-group SHAPE test; currency-unit pack injected per-locale; decide HEADER(all-label)/DATA(≥1 value) off generic value-vs-label shape |
| `tabular_markdown.py:65` (→`parse_money_vn`) | single-language | **P0** | K1/K2 / ONE LAW | Introduce `ValueDetectorPort.is_pure_value/parse_amount` + locale registry; inject by chunk language; VN parser = one strategy |
| `document_stats.py:290-300` (`_is_header_row`) | hardcode-vocab | **P0** | D7/D8 / K1,K2 | Add FORM-based header path: no-money cells + short label-shaped + next row carries values; keep exact-token match as FAST-PATH only, not sole gate (promote `tabular_markdown._looks_header` as SSoT) |
| `document_stats.py:155-205` (role token sets) | single-language | **P0** | K1,K2,K3 / D | Move role-token inventories to `language_packs[locale].column_role_tokens` + per-bot `custom_vocabulary`, alembic-seeded; engine keeps only 4 internal role NAMES |
| `analyze.py:278,346` (`has_toc`) | single-language | **P1** | ONE LAW / D8 / K1 | Detect TOC by FORM (dotted-leader + trailing page-number run `^.+?[\.\s]{2,}\d+$`); if vocab kept, `DEFAULT_TOC_MARKERS_BY_LANG` keyed by locale, thread `lang` into `analyze_document` |
| `vn_structural.py:55-57,86` + `__init__.py:443` | single-language | **P1** | K3,K4 / A2,A3 | Convert module-level regexes to `get_structural_regexes(lang)` LRU cache; rename helpers locale-neutral; thread bot/doc language; en/ja dicts become reachable |
| `retrieve.py:1066-1071` | single-language | **P1** | I / K2,K3 / D2 | Add `language` param to `detect_structural_anchor`/`build_structural_like_clauses`; resolve `DEFAULT_STRUCTURAL_MARKERS_BY_LANG.get(lang,())`; empty tuple degrades cleanly |
| `document_stats.py:75-93,201-205` (discourse/clause/aggregate) | single-language | **P1** | D / K2,K3 | Prefer FORM signals (`_is_prose_row` terminator+no-money); source residual opener/aggregate word-sets from `language_packs[locale]`, default empty (shape-only) |
| `_21_:57-69` `PRICE_*_VND` + `document_stats.py:222-245` / `number_format.py:46-53` | single-domain | **P1** | K1,K5 / domain-neutral | Rename `DEFAULT_VALUE_BUCKETS/MIN/MAX`; resolve scale per-bot `custom_vocabulary['value_buckets']` + currency-unit field; generic `parse_numeric(text, scale_units, decimal_places)`; VN = seeded default, not only path |
| `embedding_cache.py:22-31` | missing-capability | **P1** | G1 | Add provider (+base_url) to key: `ragbot:emb:{provider}:{model}:{dim}:{hash}` from resolved `EmbeddingSpec.provider` |
| `query_graph.py:1351,1257` | missing-capability | **P1** | G1 | Derive cache `model` from resolved `spec.model_version`, not `_pcfg(...,'embedding_model','') or 'unknown'` |
| `semantic_cache.py:417-435,475-497` | missing-capability | **P1** | G1/H1 | Fold `embedding_model_version` into cache scope (column or into `bot_version` hash) so provider/dim swap busts |
| `chunk_quality.py:1-330` + `infrastructure/chunk_quality/*` | missing-capability | **P1** | F1/F3 | Revive behind `system_config.chunk_quality_scoring_enabled` via DI OR delete; add TRUE block-integrity metric from parser split-points |
| `chunking/` (grep `check_chunk_gaps`=0) | missing-capability | **P1** | F2 | Each strategy emits `(start_char,end_char)` spans; `assert check_chunk_gaps(spans, len(src), tol)` after every strategy; gap → repair + loud event |
| `_modality_boost.py:67-74` | hardcode-vocab | **P1** | K1,K2,K3 / D | Move intent→boost map to `system_config.modality_boost_default_map` (alembic) + per-bot `plan_limits` overrides; intent labels = opaque pass-through |
| `_modality_boost.py:130-158,192-206` | per-bot-coupling | **P1** | sacred-#10 / I4 | Keep modality INSIDE reranker contract OR gate strictly behind per-bot flag (default OFF) + audit pre/post delta; unit-test byte-identical when OFF |
| `document_worker.py:428` | single-format | **P1** | B1-B4,B6 | Replace `detect_parser(mime,ext)` with `detect_parser_robust(mime,ext,_raw)`; better: delete inline parse, call `_route_through_parser` |
| `document_worker.py:392-468` | per-bot-coupling | **P1** | A1/A3 | Collapse worker to thin adapter: fetch bytes → `doc_service.ingest(raw_bytes=...)`; one funnel |
| `document_worker.py:444-446` | single-format | **P1** | A2/C5/E13 | Thread parser chunk-list into `ingest()` (like OCR `parsed_blocks` path); route through `_route_through_parser` to preserve row boundaries |
| `chunk_quality.py` / `chunking` block-integrity | missing-capability | **P1** | F1 | True block-integrity metric (parser block boundaries vs chunk char-spans), runnable as regression gate, zero labels |
| `_21_:57-69` vs `_09_:141,150` (dup constant) | other | **P2** | K5 SSoT | Keep ONE definition in `_21_`; delete dup from `_09_`, re-export; unit-test asserting identity |
| `document_stats.py:55-58` (`_STATS_URL_NOISE_RE`) | hardcode-vocab | **P2** | D / K1 | Detect link cells by SHAPE (scheme/generic-domain pattern), drop fixed TLD list + `auditcontext` literal |
| `docx_parser.py:36-46` | single-format | **P2** | D1/D2 | Parse trailing int from "Heading N", emit `min(level,6)` `#`; no 1..3 whitelist |
| `mime_sniff.py:159` | other | **P2** | K5 | Use `DEFAULT_CSV_MIN_COMMAS` constant; lift inline `sample_size=1024` |
| `tabular_markdown.py:83,162` (punctuation) | single-language | **P2** | K3 | Source sentence-terminator set from LanguageConfig (CJK 。！？/danda ।/Arabic ؟) |
| `tabular_markdown.py:90-99,200-203` | missing-capability | **P2** | D7/E13 | Detect header BLOCK (consecutive label-rows before first value-row), merge positionally; flat = 1-row special case |
| `tabular_markdown.py:87,93,139,163` (word/cell counts) | other | **P2** | K5/K3 | Lift to named constants; gate word-count on `requires_spacing` LanguageConfig flag (char-length for CJK) |
| `late_chunking.py:59,83` | single-language | **P2** | K5 / single-lang | Lift `200`→`DEFAULT_LATE_CHUNK_CONTEXT_PREFIX_CHARS`; make `[Document context: ...]` prefix per-locale config or drop English label |
| `registry.py:14-16` (parser), `csv_chunker.py`, `analyze.py:175` comments | other | **P2** | no-version-ref / domain-neutral | Strip "Stream A Phase 1/Phase 2" from docstrings; replace spa/price example literals with generic placeholders |
| `reranker/registry.py:45-59` | missing-capability | **P2** | C2/A3 | Add `register_reranker(name,cls)` with Protocol check + built-in-override block + name-normalize; entry-point auto-discover |
| `reranker/* + reranker_resolver.py` | missing-capability | **P2** | I4 | Optional per-bot sentinel-gate (default OFF): inject decoy, expose `rerank_gate_score=best_real-decoy`; measure HALLU/coverage before default-on |
| `reranker_port.py:28` (`top_n=5`) | single-format | **P2** | K5 | Import `DEFAULT_RERANK_TOP_N` as Protocol default |
| `reranker_resolver.py:51,114` | missing-capability | **P2** | G1/G4 | Explicit cache bust on binding/system_config change + config fingerprint in payload; keep 60s TTL as safety net |
| `litellm_embedder.py:114-133` | missing-capability | **P2** | H1 | Mirror ZE/jina dim-check in `health_check` |
| `litellm_embedder.py:155-159` (`text-embedding-3`) | hardcode-vocab | **P2** | G2/G8/K5 | Move matryoshka prefix to constant tuple OR `ai_models.supports_dimension_reduction` flag onto spec |
| `embedding/* batching` | missing-capability | **P2** | G4 | Key batch accumulator by `(provider,model,dim,task)` fingerprint; document one-spec invariant / assert |
| `ingest_stages.py:563,575` | missing-capability | **P2** | C6/E2/A3 | Wire through `build_chunking_resolver(provider=...).resolve_strategy(...)` so `chunking_strategy_provider` flips behavior with no code change |
| `llm_resolver.py:45-47` | missing-capability | **P2** | E2/C6 | Derive `_ALLOWED` from canonical strategy registry / `STRATEGY_NAMES` constant |
| `rrf_round_robin.py:88-180` | missing-capability | **P2** | I7 | Wire into RRF-fuse `retrieve.py:1343` behind `rrf_entity_fairness_enabled` (OFF) OR mark experimental |
| `retrieve.py:643` (`vietnamese_preprocessing_enabled=True`) | single-language | **P2** | K/J4 | Rename `query_abbreviation_expansion_enabled`; default from language pack having abbrev table |
| `retrieve.py:1024` (`flags=5`) | missing-capability | **P2** | K5 | `flags = DEFAULT_BM25_NORMALIZATION_FLAGS`; lift `63` to constant |
| `grade.py:81` (`[:2]`) | missing-capability | **P2** | K5/E8 | `DEFAULT_CRAG_ITERATION_CAP_KEEP_CHUNKS` via `_pcfg` |
| `_21_:146-149` (struct-ref pattern), `_21_:51-53` (4-digit floor), `_13_:24-26` (context cols) | single-language/domain | **P2** | K1,K2,K3 | Move word/magnitude lists to per-locale config packs resolved by document language |
| `document_stats.py:248-272,1040-1099` (ParsedEntity schema) | single-domain | **P2** | domain-neutral/K1 | Generalise to `{name, group, values: dict[role,number], attributes}`; price = one configured numeric role; empty index when no value role |

---

## 4. Headline — the header-detection bug

**The bug.** Whether a table row is a HEADER is the single decision that binds every downstream column→role mapping. We make it by VOCABULARY twice:

1. `document_stats._is_header_row` (`document_stats.py:290-300`) returns True only if some cell **exactly matches** `_HEADER_EXACT_TOKENS` (VN+EN words like `dich vu`/`gia`/`service`/`price`) OR an owner-declared label. With no vocab match and no `custom_roles`, it returns `False` → roles never bind → the whole stats index falls to positional col-0.
2. `tabular_markdown._looks_header` (`tabular_markdown.py:90-99`) and the row classifier gate on `_is_pure_money` → `_MONEY_UNIT_RE = triệu|nghìn|vnd|tr|đ|k|m` (`tabular_markdown.py:40-43`). A non-VN/non-VND sheet has no recognized money tokens, so priced DATA rows can't be told from headers → every-row-is-a-header collapse.

**Why FORM beats VOCAB.** A header is recognizable WITHOUT reading any word: it is the row of short **label-shaped** cells (no sentence terminator, few words, **no value cell**) that immediately **precedes** a row carrying value-shaped cells (digit-groups, optionally a currency symbol). Docling/TATR detect header rows by typography + the header-vs-data **contrast**, never by reciting column words. Markdown makes this even cheaper: a `|---|` separator line (`document_stats._is_separator_line:303`, `_SEP_FIELD_RE:209`) is a pure-shape header boundary, and `col{i}` positional fallback (`tabular_markdown.py:120-125`) already handles the no-header case structurally. We already have the right primitive — we just don't let it decide.

**The crux insight: `tabular_markdown._looks_header` is ALREADY the structural SSoT** (length + bullet-lead + sentence-end + word-count + ≥2 label cells + no-money, lines 76-99) — it just inherits the money-vocab coupling through `_is_pure_money`. Fix the value test to be currency-agnostic and this function becomes a correct, language-neutral header detector.

**Concrete EVOLVE fix (no rewrite):**

1. **`tabular_markdown.py:57-69` — make the value test FORM-only.** Replace `_is_pure_money` with `_is_value_cell`: a cell is a VALUE if it is digit+separator dominated, optionally followed by a unit token from an **injected** unit pack (currency OR quantity OR time); a LABEL is short text with no value shape. Currency-word stripping becomes an optional locale HINT, not the gate. `_MONEY_UNIT_RE` moves to a per-locale pack resolved from chunk language (default to a constant, never inline VN literals).

2. **`document_stats.py:290-300` — demote vocab to a fast-path, add a FORM fallback.** Keep the exact-token match as a high-confidence FAST PATH. When it fails, fall to a structural check: row has NO value cell, all cells are short label-shaped, AND the next non-separator row has the same column count and DOES carry value cells (header-vs-data contrast). This makes a no-vocab-match table still recognized as having a header.

3. **`document_stats.py:155-205` — vocab becomes the OVERRIDABLE hint layer.** Move `_NAME/_CATEGORY/_PRICE/_ALIASES/_HEADER_EXTRA/_AGGREGATE` token sets out of source into `language_packs[locale].column_role_tokens` + per-bot `custom_vocabulary['column_role_tokens']`, loaded through the existing `custom_roles`/`declared_labels` seam (alembic-seeded with the current VN+EN set as the default locale pack). Engine keeps only the 4 internal role NAMES. The institutionalized "Add a header alias = add it HERE" comment (`document_stats.py:148`) is replaced by "add a config row."

4. **Promote `_looks_header` as the shared SSoT.** `document_stats._is_header_row` should call the value-cell shape test from `tabular_markdown` (or a shared `shape.py`) so the two header detectors agree and there is one structural contract, not two. Net result: vocab is OPTIONAL high-confidence acceleration; FORM is the floor that always works for any language/currency/domain.

---

## 5. Beyond headers — other input-data gaps, ranked adoption plan

| Rank | Gap vs standard | Our files | Action |
|---|---|---|---|
| **1 (P0)** | Header/value detection by FORM not vocab | `tabular_markdown.py:40-99`, `document_stats.py:155-205,290-300` | §4 fix: value-cell shape test + structural header fallback + vocab→config |
| **2 (P1)** | Lossless-coverage invariant absent (F2) | `chunking/*` (grep=0) | Strategies emit `(start_char,end_char)`; `assert check_chunk_gaps()` after every run; gap → repair + loud event. Highest-value single guard — catches silent source loss on ANY language/format |
| **3 (P1)** | Chunk-quality / block-integrity gate dead (F1/F3) | `shared/chunk_quality.py`, `infrastructure/chunk_quality/*` | Revive behind `system_config.chunk_quality_scoring_enabled` OR delete; add TRUE block-integrity from parser split-points |
| **4 (P1)** | Multilingual structure path single-VN | `analyze.py:278`, `vn_structural.py:55-57`, `retrieve.py:1066-1071` | Thread `language` end-to-end; `get_structural_regexes(lang)` builder; structural TOC detection; activate the dead en/ja dicts |
| **5 (P1)** | Duplicate ingest funnel + no soft-failure sentinel (A1/A7) | `document_worker.py:392-491` | Collapse worker to `doc_service.ingest(raw_bytes=...)`; add config-sourced `ingest_soft_failure_markers` check |
| **6 (P1)** | Embed-cache identity incomplete (G1) | `embedding_cache.py:22-31`, `query_graph.py:1351`, `semantic_cache.py:417-435` | Add provider/model/dim to all three cache keys |
| **7 (P1)** | App-side reranker score override (sacred-#10) | `_modality_boost.py:67-158` | Vocab→config + gate behind per-bot flag (OFF) + audit delta |
| **8 (P2)** | Template-per-doctype via Port not wired | `ingest_stages.py:563,575` | Route through `build_chunking_resolver(...).resolve_strategy(...)` so provider flip needs no code |
| **9 (P2)** | Currency/value axis VND-baked | `_21_:57-69`, `number_format.py:46-53`, `document_stats.py:222-272` | Rename `VALUE_*`; per-locale unit map; generic `parse_numeric`; generalise `ParsedEntity` |
| **10 (P2)** | Multi-row header, dim-drift, sentinel-gate, registry validation, orphan fusion | `tabular_markdown.py:90-203`, `litellm_embedder.py:114-133`, `reranker/registry.py:45-59`, `rrf_round_robin.py` | Header-block merge; litellm dim check; `register_reranker`; wire-or-flag fusion |

---

## 6. The 10 skills index

1. **canonical-ingest-flow** — one funnel, no parallel parse path, idempotency, safe-replace.
2. **type-detection-mime-sniff** — `mime→ext→byte-sniff`, metadata refines never dictates.
3. **parser-adapter-pattern** — Port+Registry+Strategy+Null, config-string dispatch, validated register.
4. **table-header-detect-structural** (the crux) — header by FORM (label-shape + value-contrast + `|---|`), vocab = optional hint.
5. **multi-row-header-merge** — consecutive label-rows before first value-row → compound columns, shape-only.
6. **template-per-doctype-chunking** — strategy registry on generic names, atomic-never-cut, header re-attach, never-zero.
7. **block-integrity-quality-gate** — lossless-coverage `assert check_chunk_gaps`, block-integrity from split-points.
8. **multilingual-no-vocab** — language as DATA per locale, script-range detect, thread `lang`, no in-code word lists.
9. **metadata-optional-hint** — metadata/currency/language refine, never gate; config-sourced defaults.
10. **ingest-backward-trace-debug** — chunk: ingest→retrieve→topK→prompt→answer evidence trace before any claim.