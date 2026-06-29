# CONFIG-BURDEN AUDIT — what production never fills (2026-06-27)

Consolidated from 5 domain sweeps (custom_vocabulary, per-bot tunables, owner-config burden, model/binding/threshold, ingest). All highest-impact claims re-verified against code this session — verification notes inline (✓ verified).

Real-customer baseline (the only config a real customer touches): (1) create bot, (2) upload system_prompt + documents, (3) one flow/state setting (history / booking capture), (4) a reply on/off setting, (5) rarely a model setting. Anything else the engine demands the owner declare/tune is suspect.

---

## 0. TL;DR — the worst offenders (config the customer will NEVER fill)

Ranked by "blocks zero-config + customer cannot or will not fill":

1. **`custom_vocabulary["column_roles"]` — THE flagship offender** (raised in 4 of 5 sweeps). To make list/count/price queries work on an uploaded XLSX/CSV the owner must hand-author a `{header_label: role}` JSON map, AND there is **no admin API** for it (PATCH /vocabulary only accepts abbreviations+diacritics — ✓ verified `schemas.py:222-224`), so a B2B customer with no DB access literally cannot set it. Verified live: 0/3 bots set it. Degrades silently: header outside the ~60 built-in VN/EN tokens → no NAME column → entities not name-keyed → list/count/price answers silently miss rows.

2. **The canonical B2B onboarding bug — `/sync` queries the wrong model kind** (✓ verified `sync.py:305` = `kind IN ('chat','embedding')`). Schema canonical is `'llm'` — the parallel auto-pick at `bot_admin_routes.py:222` uses `'llm'` with an explicit comment that `'chat'` "left newly-created bots without an LLM binding, which made the resolver 500 on first chat." So **every bot onboarded via the documented server-to-server `/sync` path 500s on its first message.** `resolve_llm` raises `InvariantViolation` with no platform-default fallback (✓ verified `service.py:137-140`), unlike the reranker which fails soft.

3. **`custom_vocabulary["synonyms"]`** — no admin API, no inference fallback. Empty → raw keyword only; broad "về da" list returns only exact-substring hits, family dropped (Coverage drop, not HALLU). 0/3 bots set it.

4. **HAPPY_CASE doctrine "checker báo + khách sửa source"** — the engine punts messy tables back to the customer to unpivot/transpose/flatten by hand (✓ verified `document_stats.py:150` "the customer fixes the SOURCE … we do NOT grow the parser per format"). A real customer uploads whatever Excel their staff already maintains; they will not restructure it.

5. **Three dead-without-config "feature" surfaces** that load but never run: metadata-aware retrieval triple-gate (2 flags + doc_type vocab + labelled corpus), BM25 lexical + article/metadata filters default to `"null"` (✓ verified the three `DEFAULT_*_PROVIDER = "null"` constants), knowledge-graph retrieval (`entity_extractor_provider="null"` + `graph_rag_mode="disabled"`).

6. **Two phantom columns / dead knobs**: `metadata_extraction_config` (✓ verified ZERO consumers in src — only DTO/model/repo load), `greeting_response` (✓ verified read via `getattr` at `pipeline_config.py:445` but defined in neither DTO nor ORM model → always `""`), plus per-bot `stopwords` (✓ verified `compress_chunks` at `generate.py:386` does NOT pass `custom_stopwords` → dead wiring) and `boilerplate_patterns` (✓ verified `boilerplate_resolver.py:2` "DEAD-CODE NOTICE").

---

## 1. THE IMPRACTICAL-BURDEN LIST

Sorted: impractical-burden + dead-without-config FIRST, then ok-auto-default / power-user-optional in §2.

| Knob | Owner must do | file:line | If empty | Real customer fill? | FIX |
|---|---|---|---|---|---|
| **`custom_vocabulary["column_roles"]`** | Hand-author `{header:role}` map per bot to bind NAME/PRICE/CATEGORY columns | `document_stats.py:443-518`; `ingest_stages_final.py:468-512`; no API (`schemas.py:222-224`) | DEGRADED: header outside ~60 VN/EN tokens + no md-separator → no NAME column → list/count/price silently miss rows. 0/3 bots set it | **NEVER** — no admin API at all; customer expects upload to "just work" | auto-infer structurally (see §3.1): keep Tier-1, add numeric/cardinality lane; demote knob to optional override |
| **`/sync` model auto-pick wrong literal** | Nothing (engine bug) — canonical B2B path picks LLM with `kind IN ('chat','embedding')` | ✓ `sync.py:305` (canonical = `'llm'`, cf. `bot_admin_routes.py:222`) | LLM `model_id` stays None → `ensure_bot_bindings` skips llm_primary → `resolve_llm` raises → **first chat 500** for 100% of /sync bots | **N/A** — affects every bot on the documented onboarding path | one-line: `kind IN ('llm','embedding')`; extract one shared `pick_default_models()`; regression test |
| **`bot_model_bindings(llm_primary)` required, no fallback** | Pass an `ai_models` UUID at create or bot 500s | ✓ `model_resolver/service.py:137-140` (raises `InvariantViolation`) | Hard dead: first chat 500. No platform-default fallback (unlike reranker) | **rarely** — customer doesn't know an ai_models UUID; only safe path is auto-pick | give resolve_llm the same 3-tier fail-soft the reranker has (per-bot → system_config → default); unconditional auto-seed at create |
| **`bot_model_bindings(embedding)` required, no fallback** | Pass embedding UUID or `resolve_embedding` raises | ✓ `service.py:308-309` (raises) | Ingest/retrieve embedding resolution raises | **rarely** — same UUID illiteracy; must be auto-picked | shared `pick_default_models()`; embedding = platform default, never per-bot prompt |
| **`custom_vocabulary["synonyms"]`** | Author `{term:[variants]}` for stats list/count OR-expand | `query_graph.py:589-614`; `stats_index_repository.py:470-490`; no API | Recall drop: broad query returns exact-substring only, family dropped. 0/3 bots set it | **never** — no API; customer won't enumerate phrasings | auto-mine from corpus aliases (`document_stats.py:176-184` `entity_synonyms`) / embedding-neighbour clustering at ingest; never require |
| **HAPPY_CASE "khách sửa source" doctrine** | Pre-clean/unpivot/transpose/flatten source by hand to a hidden spec | ✓ `document_stats.py:150`; `HAPPY_CASE_DOCUMENT_FORMAT.md:6-66`; `scripts/check_happy_case.py` | Ingest SUCCEEDS but stats index extracts garbage/0 entities → list/count/price silently wrong | **never** — customer uploads existing spreadsheets unchanged | absorb messy shapes in-engine using existing shape-only handlers (`_premerge_split_headers`, `_is_header_row` separator-trust); extend structural unpivot/transpose; keep checker as internal QA only |
| **metadata-aware retrieval triple-gate** | Flip `metadata_aware_retrieval_enabled` + `metadata_extraction_enabled` + supply `metadata_extraction_vocabulary` + label corpus at ingest | `retrieve.py:737-772`; both flags default False (`pipeline_config.py:276-281`); vocab default None | DEAD: layer skipped (`metadata_aware_skipped_write_off`). Contributes nothing for ~100% of bots | **never** — 3 coordinated steps + hand-authored doc_type vocab | auto-derive doc_type from parser/enrichment; auto-populate vocab from distinct stored labels; make cheap article-regex filter the always-on path, gate only the LLM extractor behind one opt-in |
| **`bots.metadata_extraction_config` (phantom column)** | "Owner-defined" per-bot hint | ✓ `dto/bot_config.py:132-134`, `models.py:242`, `bot_repository.py:107` — **ZERO consumers in src** | Nothing — loaded then never read. Dead column + GIN index + DTO field | **never** — and does nothing even if filled | remove column+GIN+DTO+repo mapping (or finish wiring); simplest: delete |
| **`greeting_response` (phantom field)** | — | ✓ read `getattr(bot_cfg,'greeting_response')` `pipeline_config.py:445`; **absent from DTO + ORM** | Always `""` — dead read, no column to populate | **never** — no column exists | remove dead getattr, or add column+DTO+wire |
| **`custom_vocabulary["stopwords"]` (dead wiring)** | Documented per-bot stopwords for prompt compression | `prompt_compression.py:95-104,233`; ✓ caller `generate.py:386-392` passes **no** `custom_stopwords` | Irrelevant — per-bot value never read on live path; falls to `DEFAULT_VI_STOPWORDS` | **never** — no API + runtime caller drops it | remove the dead kwarg path; rely on `DEFAULT_VI_STOPWORDS` (zero-config) |
| **`custom_vocabulary["boilerplate_patterns"]` (dead module)** | Per-bot boilerplate regex | ✓ `boilerplate_resolver.py:2` "DEAD-CODE NOTICE 2026-06-03"; body commented out | No effect — resolver is dead code; live path uses built-in `DEFAULT_BOILERPLATE_PATTERNS_VI` | **never** — dead module, no caller, no API | physically delete `boilerplate_resolver.py`; drop from documented vocab surface |
| **`lexical_retrieval_provider="null"` (BM25 off by default)** | Operator must seed a real adapter | ✓ `DEFAULT_LEXICAL_RETRIEVAL_PROVIDER="null"`; `bootstrap_config.py:110-118` | BM25 hybrid OFF → pure vector; hurts exact-keyword/code/notation recall, silent | **never** — operator seed not in alembic | default to the real pgvector/tsvector BM25 adapter (engine has it); keep "null" as explicit opt-out. BM25 hybrid is table-stakes |
| **`metadata_filter_provider="null"` (article filter off)** | Operator set strategy + supply `article_ref_patterns` | ✓ `DEFAULT_METADATA_FILTER_PROVIDER="null"`; `retrieve.py:785-814` | Article-aware regex prefilter ("Điều 32") never runs; legal/reg bots lose anchor precision | **never** — operator-only | default to regex ArticleAwareFilter seeded with `DEFAULT_ARTICLE_REF_PATTERNS` (domain-neutral, cheap, no LLM; safe-on — only adds keys when patterns match) |
| **knowledge-graph retrieval (`entity_extractor_provider` + `graph_rag_mode`)** | Set extractor provider + graph mode + ingest edge graph | ✓ `DEFAULT_ENTITY_EXTRACTOR_PROVIDER="null"`; `graph_rag_mode` default "disabled" `bot_limits.py:103-107` | NullExtractor returns [] → KG path skipped; core RAG unaffected | **never** — research feature needing coordinated ingest+provider | move off customer surface entirely → operator-only; keep Port/registry for future |
| **Price/stats VND-baked currency** | Implicitly: catalog must use VN notation (tr/triệu/tỷ/k) + VND magnitudes | ✓ `number_format.py:74-83`; `DEFAULT_PRICE_MIN_VND=10_000`/`MAX=500_000_000` `_21_...:64,69` | Non-VND catalog → prices below floor / unparseable → price coverage 0%, "rẻ nhất/đắt nhất" fail | **rarely-outside-VN** — hard wall for non-VN tenants | make currency/scale a config-sourced HINT (per-bot/locale pack), default VND; detect magnitude by column value distribution (see skill `metadata-optional-hint`) |
| **`build_bot_summary.py` (manual "list all" doc)** | Generate per-bot summary doc + ingest it + hand-edit sysprompt with "list-all" rule | `HAPPY_CASE_DOCUMENT_FORMAT.md:110-122`; `scripts/build_bot_summary.py:1-14` (bot list HARDCODED) | top-K cannot answer "liệt kê tất cả"; every list/summary query partial/empty, silent | **never** — one-off demo script, not productized, no customer path | auto-emit a synthetic "full listing" chunk per doc/bot at the stats-index stage (deterministic, no LLM); listing behavior via governed sysprompt default, not manual edit |
| **Admin API surface gap: PATCH /bots/{id}/vocabulary** | Only abbreviations+diacritics accepted | ✓ `bot_admin_routes.py:824-867`; `schemas.py:222-224` | The two heaviest keys (column_roles, synonyms) have NO API → raw DB JSONB only (CLAUDE.md forbids as out-of-band drift) | **never** — B2B customer with no DB access cannot set them | auto-infer the high-impact keys; if any override retained, expose via API + validate format — never require |

---

## 2. OK as-is — auto-default / power-user-optional, invisible by default (do NOT over-remove)

These resolve safely (3-tier chain → system_config → constant, never-raise) and impose zero setup burden. **Keep them — they are the model to follow.**

- **Core quality features ON by default (the gold standard):** `multi_query_enabled`, `grounding_check_enabled`, `refuse_short_circuit`, `rerank_filter_strategy="cliff"`, `late_chunking`, `whole_doc`, `prompt_compression` — all default True. HALLU-safety on out of the box. The contrast with the dead knobs above proves the engine CAN ship zero-config.
- **`threshold_overrides`** (reranker_min_score / grounding / guard / semantic_cache / cliff_gap / context_chars_cap): full resolve chain `bot_limits.py:385-429` with range guard; every key has a production-tuned default. Operator A/B knobs — keep, never surface as mandatory.
- **`reranker_provider` / rerank binding:** correct 3-tier fail-soft (`reranker_resolver.py:_lookup_platform_default` → `NullReranker`, ✓ verified `:188,203,264`). The pattern resolve_llm/resolve_embedding should copy. (One hardening: make it FAIL-LOUD via preflight/`/health/models` when a reranker is configured-but-unresolvable — CLAUDE.md already bans silent rerank-off with an active binding.)
- **`action_config` (booking/slot-filling)** — legitimate opt-in flow feature (= allowed customer setting #3). Default `{}` → pure Q&A. Keep; only lower friction with a preset `slots_schema` template (booking/lead) so `enabled:true` alone works.
- **`abbreviations` / `diacritics`** — real self-service via PATCH /vocabulary; auto-default to VN seed (`vi_tokenizer.py:326-327`); diacritic restoration off by default. Genuine power-user surface.
- **`declared_labels`** — derived from column_roles keys, positive HINT only, never sole gate (`document_stats.py:308-312`). No standalone action.
- **`chunk_size`/`overlap`/`parent`/`child`, `chunking_config`, `rerank_top_n`/`top_k`/`max_documents`/`max_history`** — resolve chain + range guard, never block ingest.
- **`oos_answer_template`** — 7-tier resolver ending in language_packs per-locale refuse text (vi/en seeded by alembic 0136). Non-empty default without owner action.
- **`embedding_passage_prefix`** — empty default safe for symmetric models; should be auto-inferred from model registry, never owner-guessed (re-ingest coupling makes it a foot-gun).
- **`allowed_source_domains`** — empty = allow-all (correct default); only security-conscious tenants populate.
- **`sysprompt_version` label** — metadata-only, never injected (sacred #10). Templates as copy-paste starters in UI, not a knob.
- **Cost-routing bindings** (llm_enrichment / draft / cascade): missing → transparently reuse llm_primary (`service.py:129-136`). The right zero-config fallback — exactly what llm_primary itself should do instead of raising.
- **Opt-in advanced toggles** (hyde / self_rag / cascade / neighbor_expand / adaptive_context): default OFF = byte-identical legacy path; off the customer surface (operator-only). Exception worth considering: `cr_enhanced` (Contextual Retrieval, proven win) could auto-enable at ingest since read-side coalesces NULL safely.
- **`rerank_intent_whitelist` / skip_* intents** — None = safe legacy (always rerank); constant defaults skip cheap intents. Operator-only.
- **`DEFAULT_HALLU_TRAP_KEYWORDS`** — empty tuple, no runtime consumer; inert but advertises a non-existent owner knob → drop the constant + comment (minor cleanup, no behavior change).

---

## 3. FIX PLAN — ordered, EVOLVE-not-rewrite, mapped to files

Strategy: strangler-fig. Keep the framework, 4-key, sacred rules. Wire what's dead, auto-infer what's owner-declared, remove pure cruft. Lead with column_roles.

### 3.1 column_roles — remove the owner-declaration burden, keep auto-infer + add numeric lane (LEAD)
**Scope (per request): remove the *requirement* to declare column roles; keep Tier-1 auto-infer and add a structural numeric/name lane. Keep column_roles only as an optional power-user override.**
- KEEP `document_stats.py:443-518` Tier-1 G1 inference + zero-vocab separator rescue (`:326-328`).
- ADD a **structural (script-agnostic, vocabulary-free) role lane**, layered on the existing value-contrast/label-shape detector (`:291-330`): NAME = first non-numeric text column with highest cardinality; PRICE/VALUE = column whose cells are >70% money/numeric-with-currency shape; CATEGORY = low-cardinality repeating text column. This is what skills `table-header-detect-structural` + `multi-row-header-merge` already prescribe.
- Surface the existing `ingest_data_quality` advisory in the ingest **API response** (not just structlog) — but downgrade the wording away from "declare column_roles" once structural inference lands. (`ingest_stages_final.py:500-512`.)
- DEMOTE `column_roles` to optional tie-breaker. No owner declaration ever mandatory.

### 3.2 Onboarding model bindings — make-auto + fail-soft (HIGHEST customer impact)
- **One-line fix:** `sync.py:305` → `kind IN ('llm','embedding')` (✓ removes the 500-on-first-chat regression for all /sync bots).
- Extract a shared `pick_default_models()` used by `sync.py`, `bot_management_service.create_bot`, and `bot_admin_routes.py:222` so the literal can never drift again.
- Give `resolve_llm` (`service.py:137`) and `resolve_embedding` (`service.py:308`) the **same 3-tier fail-soft the reranker already has** (`reranker_resolver._lookup_platform_default`): per-bot binding → system_config platform default → fail-loud preflight (not a request-time 500).
- Add a startup/create invariant: a bot with zero active llm_primary binding is an error to repair at create, not discover at request time.
- Add a regression test: a /sync-created bot has an active llm_primary binding.
- Stamp `extra_params.dimension` at bind time from the ai_models row's dimension (✓ `bot_bindings.py:128` currently inserts `'{}'`; resolve falls to `DEFAULT_RERANKER_EMBEDDING_DIM=1024` — fragile matryoshka trap). Single source of truth = model row dimension.

### 3.3 synonyms — make-auto (mine from corpus), keep optional
- Auto-mine candidate synonyms at ingest from `entity_synonyms`/aliases already built (`document_stats.py:176-184`) + embedding-neighbour clustering of entity names. Feed the stats route automatically; offer owner override as suggestions, never a precondition. Main retrieval path already covers synonymy via vector + BM25 RRF.

### 3.4 Retrieval-quality layers wrongly default "null" — sensible-default
- `lexical_retrieval_provider`: default to the real BM25 adapter (engine has it); keep "null" opt-out.
- `metadata_filter_provider`: default to regex `ArticleAwareFilter` seeded with `DEFAULT_ARTICLE_REF_PATTERNS` (safe-on — only adds filter keys when patterns match).

### 3.5 metadata-aware retrieval — auto-derive, collapse the triple-gate
- Auto-label `document_type` from the parser/enrichment already run at ingest; auto-populate the allowed-doc-type vocab from distinct stored labels. Make the cheap structural article prefilter always-on; gate only the expensive query-time LLM extractor behind a single opt-in. No owner-authored vocab.

### 3.6 Currency/scale — config-sourced HINT, not VND constant
- Derive price min/max floor + suffix table from bot locale/currency (default VND for VN bots); detect magnitude from the column's value distribution so USD/EUR catalogs extract prices with zero owner config (skill `metadata-optional-hint`).

### 3.7 "list all" answers — auto-emit listing chunk
- Auto-generate a synthetic "full listing" chunk per doc/bot at the stats-index stage (deterministic, no LLM — stats index already holds every name/price/category). Listing-disclosure behavior via governed sysprompt default (ADR-W1-S10 append), not a manual per-bot edit. Retire the hardcoded demo `build_bot_summary.py`.

### 3.8 REMOVE pure cruft (dead knobs that advertise non-existent tuning)
- Delete `boilerplate_resolver.py` (✓ flagged DEAD-CODE) + drop `boilerplate_patterns` from docs.
- Remove the dead `custom_stopwords` per-bot path (✓ `generate.py:386` never passes it) → rely on `DEFAULT_VI_STOPWORDS`.
- Remove `metadata_extraction_config` column + GIN index + DTO field + repo mapping (✓ zero consumers), or finish wiring — prefer delete.
- Remove the dead `greeting_response` getattr at `pipeline_config.py:445` (✓ no column/DTO field), or add column+wire.
- Drop `DEFAULT_HALLU_TRAP_KEYWORDS` constant + owner-facing comment (inert, no consumer).
- Move KG (`entity_extractor`/`graph_rag_mode`) off the customer surface → operator-only.

**Each item is additive/surgical** — wiring dead defaults, auto-inferring from data the corpus already has, or deleting unreferenced cruft. No framework rewrite; parser changes stay structural (shape/script-range), domain-neutral, no per-format/per-bot hacks (CLAUDE.md sacred #8, #10).

---

## 4. Resulting customer config surface after fixes (matches the 5-step ideal)

After the fixes, a real customer touches exactly:

1. **Create bot** — models auto-picked + bindings auto-seeded (no UUID, no 500). [§3.2]
2. **Upload system_prompt + documents** — messy XLSX/CSV absorbed by structural inference; price/name/category auto-detected by FORM; BM25 + article filter on by default; synonyms auto-mined; "list all" answerable via auto-emitted listing chunk. No "fix your source," no column_roles, no synonym map. [§3.1, 3.3, 3.4, 3.5, 3.6, 3.7]
3. **One flow/state setting** — `action_config` opt-in with a preset slots_schema template (booking/lead). [§2]
4. **Reply on/off** — existing toggle, default safe.
5. **Rarely a model setting** — optional override; platform default otherwise. [§3.2]

Everything else (thresholds, intent gates, KG, metadata-LLM extractor, embedding prefixes, lexical/article providers, cliff strategy) is operator-only or auto-default and **never surfaced to the customer**. The dead knobs (boilerplate/stopwords/metadata_extraction_config/greeting_response/hallu_trap_keywords) are gone, so no config surface implies tuning that does nothing.

---

### Evidence-confidence note
Re-verified this session (✓): sync.py:305 wrong literal; bot_admin:222 correct + comment; metadata_extraction_config 0 consumers; greeting_response read-only-no-field; boilerplate_resolver DEAD-CODE; generate.py:386 drops custom_stopwords; PATCH vocabulary = abbreviations+diacritics only; resolve_llm/embedding raise vs reranker fail-soft; lexical/metadata/entity provider = "null"; VND-baked price constants; document_stats.py:150 "khách sửa source" doctrine. The "0/3 bots set X" claims come from the source sweeps (live DB inspection) and were not re-queried this session — labelled accordingly. Net: the structural/code claims are SỰ THẬT (verified file:line); the live-DB-count claims are inherited from the sweeps (GIẢ THUYẾT pending a fresh psql count, but consistent with there being no admin API to set them).
