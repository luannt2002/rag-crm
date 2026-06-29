# EXPERT MINDSET & BUILD BLUEPRINT — zero-config RAG SaaS (from LuanNT↔AI convo, 2026-06-27)

> Synthesized from a 5-agent deep read of `z-luannt-111111.txt` (1598 lines), reconciled against the ragbot codebase and our gold-standard `reports/MASTER_4PHASE_30AGENT_20260627.md` (16 gaps + 15-step fix) + `reports/MASTER_INGEST_FLOW_REPORT_20260627.md`. Every code claim below is grep-verified (file:line). Convo claims labelled **VERIFIED** (checked against our code/reports) vs **GIẢ THUYẾT** (asserted in convo, no measurement). Honors rule#0: evidence not guess.

---

## 0. TL;DR — the expert mindset in 7 rules

The convo independently re-derives our charter. These 7 rules are what make a multi-tenant RAG SaaS expert-grade **and sellable**:

1. **ZERO-CONFIG is a SALES requirement, not a nicety.** The customer drags 5–10 mixed-format files + writes one system prompt → it "just works". Any keyword/schema config burden = unsellable SaaS UX (convo L1-5, L937, L1564). → Therefore **structure-deciding code must contain ZERO vocabulary.**
2. **Detect by SHAPE, never by WORDS.** Column role = data-type distribution + word-density + value-contrast + position; block type = pipe-count / heading-markup / font-geometry — never the words `giá`/`tiền`/`Chương` (convo L65-69, L242, L832). This is the *only* way one engine serves cosmetics/legal/education bots in any language with zero per-tenant config.
3. **Two physical lanes: prose vs tabular.** Vector search is bad at exact numbers; route numeric tables to a metadata/stats filter so numbers are answered by exact arithmetic, not cosine (convo L243, L463). This is the load-bearing HALLU-on-numbers lever.
4. **API responds, workers process.** Never parse/chunk in the request thread — stream to storage, status=queued, return 202 in <200ms, workers consume a queue (convo L462, L768). A correctness/survival property under 100-page scans / 50k-row Excel, not just perf.
5. **Block-level adaptive, NEVER file-level.** Real files are mixed-content (intro=semantic, clauses=proposition, specs=tabular in ONE PDF). One-strategy-per-file is the *bẫy tử thần* (death-trap) (convo L802-805). Route per block.
6. **Deterministic shape-analytics for routing; LLM only locally.** A per-file LLM strategy selector blows the context window + 429 on big docs. Use a code decision tree on a computed Document Profile; reserve micro-LLM for narrate-a-table / propositionize-a-clause (convo L830-846).
7. **Isolation by INFRASTRUCTURE, not by code.** Every read AND write carries the tenant/bot key, enforced at the DB driver/RLS layer — never an application filter you can forget (convo L770, L1027). *(Our 4-key + RLS is stronger than the convo's bot_id-only namespace — see §4.)*

Cross-cutting: **Assume the parser returns garbage** (add a reconstruction layer), **narrate-then-embed dual-representation** (prose decoy for recall + verbatim original for the LLM to read exact digits), and **measure, don't eyeball** (structure-trap eval + RAGAS deltas, not assertions).

---

## 1. The target end-to-end FLOW

```
                          ┌─────────────────────────  INGEST (async)  ─────────────────────────┐
 client BE                │                                                                      │
   │  POST /documents/create (multipart/url/bytes)                                               │
   ▼                                                                                              │
 [API tier]  ── stream file → object/blob store, write DB row status=queued, push task ── 202 in <200ms
   │                                                                  │ (queue: Redis Streams)
   ▼                                                                  ▼
 [WORKER]  STAGE 1 UNIFIED PARSE        mime→ext→byte-sniff → parser registry (Port+Strategy)
           every format → ONE canonical STRUCTURED MARKDOWN  (# heading / paragraph / | table |)
                                              │
           STAGE 2 RECONSTRUCTION       assume dirty blocks: re-assemble split tables,
                                        propagate page-1 header to page 2..n, rebuild hierarchy
                                              │
           STAGE 3 STRUCTURAL ROUTING   per-BLOCK shape analytics → deterministic decision tree
              ┌────────────────────────────┴────────────────────────────┐
              ▼ PROSE lane                                                ▼ TABULAR lane
        semantic / HDT / proposition chunking                      SHAPE DETECTOR (col roles by form)
        parent-child + structural_path (heading stack)             row-as-record + header injection
              │                                                    NARRATE-then-EMBED (decoy) +
              ▼                                                    persist verbatim original_content
        embed → VECTOR store                                             │
              │                                                          ▼
              │                                                    STATS / METADATA-FILTER INDEX
              │                                                    (entity attrs as hard numeric cols)
              └──────────────┬──────────────────────────────────────────┘
                             ▼   status=completed (per-file fail, not per-system)

                          ┌──────────────────────────  QUERY (sync) ──────────────────────────┐
 user query ─┬─ SEMANTIC path:  embed(query) → dense+sparse hybrid (RRF) → rerank              │
             └─ METADATA path:  cheap regex / tiny-LLM extract → {price < X, attr = Y}          │
                             │                                                                   │
                  HYBRID:  Vector(...)  AND  hard-filter(bot key AND price<X)   ── exact numbers │
                             │                                                                   │
              ISOLATION GUARD on EVERY read+write: tenant/bot key forced at driver + RLS GUC     │
                             ▼  context (governed XML data-fence, owner-opt-in) → LLM → answer   │
```

### Per-stage spec

| Stage | Spec | Convo ref | Our code |
|---|---|---|---|
| **0. Async accept** | stream to store, DB status=queued, push task, return 202 <200ms; never parse in request | L462, L616-655 | `interfaces/http/routes/documents.py:92` `HTTP_202_ACCEPTED` + `X-Idempotency-Key` (VERIFIED) |
| **1. Unified parse** | mime→ext→byte-sniff; SOTA layout parser → ONE structured Markdown (# / para / `\| --- \|`); add format = add adapter | L20, L160-170 | `infrastructure/parser/registry.py:45` `_REGISTRY` + `build_parser`/`detect_parser_robust`; `ingest_core.py:262` `sniff_real_mime` (VERIFIED, stronger — convo trusts ext only) |
| **2. Reconstruction** | assume dirty blocks: assemble split tables, propagate multi-page header, rebuild hierarchy | L1356-1392, L1413-1415 | **partial** — `analyze.py` pipe-count header detect; multi-PAGE header propagation **NOT present** (gap) |
| **3a. Prose chunking** | per-block strategy via deterministic tree; parent-child + structural_path heading stack | L817-846, L985-988, L1229-1303 | `shared/chunking/analyze.py:select_strategy` + `infrastructure/chunking_strategy/rule_resolver.py` (block-aware) + `__init__.py:109 generate_parent_child_chunks` (VERIFIED) |
| **3b. Shape detector** | value-col by numeric-ratio + value-contrast; name-col short-text-left; prose-noise by word density; multi-row header merge; aggregate-row drop | L65-110, L266-406 | `shared/document_stats.py:_is_header_row` / `_column_roles` (3-tier cascade, richer than convo) **BUT vocab-gated = our P0 CRUX** (VERIFIED — see §3) |
| **3c. Tabular chunking** | NEVER text-split a table; one row = one self-contained record with headers injected; narrate-then-embed; persist verbatim original | L534-575, L888-910 | `shared/chunking/csv_chunker.py:_chunk_table_csv` (row-per-chunk); narrate `__init__.py:866 narrated_text` / `868 original_content` (VERIFIED — but `original_content` round-trip BROKEN, §3) |
| **4. Stats index** | entity attributes as hard numeric metadata columns for `gt/lt/eq` filter | L416-450, L720 | `infrastructure/repositories/stats_index_repository.py` `query_by_price_range`, `price_primary/secondary`, tenant+bot scoped (VERIFIED) |
| **5. Hybrid query** | parallel semantic + cheap metadata-extract → `Vector AND filter(bot AND price<X)` | L197-225 | `shared/query_range_parser.py` (regex) + `infrastructure/metadata_filter/generic_llm_extractor.py` (LLM, Port+DI, prompt in language_pack) (VERIFIED — already shipped) |
| **6. Isolation** | tenant/bot key forced on every read+write at driver + RLS | L230-235, L770 | `vector/pgvector_store.py:66 raise` + `_doc_filter_sql:258 record_bot_id = :record_bot_id`; `db/engine.py:174 SET LOCAL app.tenant_id` RLS (VERIFIED — 4-key, stronger) |
| **7. Generate** | governed XML data-fence around context, owner-opt-in | L235 | `orchestration/query_graph.py:351 _resolve_xml_wrap_enabled` (per-bot `plan_limits.xml_wrap_enabled`, default OFF, ADR-governed) (VERIFIED) |

---

## 2. The MINDSET principles — each with WHY

- **Zero-config (no keyword burden).** *Why:* a partner dev / non-expert end-user will never maintain a per-tenant vocabulary file; config = friction = churn. If you need a word-list to classify structure, you've already failed the UX (convo L1-3). **Corollary that bites us:** our `_HEADER_EXACT_TOKENS` (~70 VN/EN words) violates this exact principle — it's the P0 CRUX.
- **Shape-not-vocab.** *Why:* geometry/shape is universal and immutable across languages and currencies; a numeric-ratio or a `^[\s\|:\-]+$` separator line means the same in Vietnamese, Spanish, and a cargo manifest. Vocabulary is single-language single-domain by construction (convo L242, L264). This is what gives multilingual + multi-domain *for free*.
- **2-stream split (prose vs tabular).** *Why:* embedders are trained on prose and are "cực kỳ dị ứng" to raw tables/`$$…$$`; cosine on exact numbers is meaningless and wastes embedding tokens. Numbers must be answered by an arithmetic filter on a normalized column (convo L461-463, L888-894).
- **Async-not-sync.** *Why:* a synchronous parse of a 100-page scan or 50k-row Excel times out the request and OOMs the API; a queue absorbs concurrency spikes (convo L459-463). It's correctness under load, not just latency.
- **Parser-as-library (don't hand-write per-format readers).** *Why:* PyPDF2 dies on scanned PDF/tables; maintaining N bespoke readers is unsustainable. One canonical Markdown means *all downstream logic sees one shape* (convo L47-52). You write adapters behind a Port, never readers in the orchestrator.
- **Metadata-filter for number-HALLU=0.** *Why:* converting "dưới 2 triệu" into a hard `price < 2000000` returns *exact* entities; vector similarity can pull a wrong-price/wrong-topic doc (convo L221). **Honest caveat:** this REDUCES the fabrication surface; it does not *guarantee* HALLU=0 — a mis-inferred value-col silently produces a confident wrong filter (see §4 risk).
- **SaaS-UX (config-over-code for heterogeneity).** *Why:* a weird new customer format must be a config row / UI rule, never a redeploy + new `if/else`. "Code-and-Patch" (every new file forces a dev edit) is the death of a SaaS (convo L1463-1471).
- **Assume dirty parser output.** *Why:* parsers split one table into 3 text blocks and glue prose onto a table's first row; shape-analytics on garbage misclassifies. A reconstruction layer is mandatory, not optional (convo L1356-1390).

---

## 3. Convo vs OUR CODE — already-have / ADDS / CONFLICTS

### ✅ ALREADY SHIPPED (convo describes what ragbot already has — EVOLVE not rewrite)
- **202 async ingest funnel** — `documents.py:92` (VERIFIED). Convo's `/v1/ingest` is the *concept*; our canonical route is `POST /api/ragbot/documents/create`.
- **Parser Port+Registry+Strategy+Null** — `infrastructure/parser/registry.py:45` config-string dispatch (VERIFIED). Realizes convo STEP1/PHASE2 "unified parsing". We use Kreuzberg/Docling not Unstructured.
- **Shape-based table extractor** — `document_stats.py` is *richer* than the convo's flat `numeric_ratio>0.7 + avg_words<6`: 3-tier role cascade (owner `custom_vocabulary["column_roles"]` > G1 structural > generic, lines 413-493), multi-row header merge (783-833), aggregate-row reject (`_AGGREGATE_TOKENS`). Convo's `extract_pure_tabular_stats` is a *simpler subset* of what we ship.
- **Stats / metadata-filter index** — `stats_index_repository.py` `query_by_price_range`, tenant+bot scoped (VERIFIED). This IS the convo's `$lt/$gt` filter, in SQL not Pinecone.
- **Hybrid metadata query (BOTH paths)** — regex `query_range_parser.py` + LLM `generic_llm_extractor.py` (Port+DI, prompt in `language_packs`) (VERIFIED). The convo presents this as a new idea; we already shipped it domain-neutrally.
- **Mandatory bot filter + RLS** — `pgvector_store.py:66/258` + `engine.py:174 SET LOCAL app.tenant_id` (VERIFIED). **Stronger** than the convo: app-filter AND row-level-security; 4-key not bot_id-only.
- **Parent-child + structural_path** — `chunking/__init__.py:109` (VERIFIED).
- **Block-level routing exists** — `analyze.py:select_strategy` + `rule_resolver.py` (block-aware) with generic strategy names SEMANTIC/HDT/TABULAR/PROPOSITION/HYBRID (VERIFIED).

### ➕ What the convo genuinely ADDS (to consider)
1. **Multi-page table HEADER PROPAGATION** (Table Assembler Buffer: same X-coord + col-count → inject page-1 header into pages 2..n, convo L1413-1415). **NOT in our 16 gaps.** Genuine new item. *Risk:* depends on parser BBox/coords — **unverified** that Kreuzberg-markdown exposes them; feasibility check required before promising.
2. **Async-202 discipline as an audited property** ("202 in 200ms / stream-to-store / never-parse-in-request"). We *have* the worker + 202, but the convo's discipline is **not audited** in our reports — a T2 perf/UX track item, not T1.
3. **Per-tenant DYNAMIC-SCHEMA hierarchy config + AI-layout fallback** (convo L1477-1564 typography-driven `DynamicContextStateMachine`; L1568-1583 LayoutLMv3 vision skeleton for the formless 20%). Richer than our per-LOCALE config-pack plan. Both heavier than our EVOLVE scope — config-pack-per-locale is the surgical fix; vision-layout is a future T1 add (Null-default opt-in, model-tier concern).
4. **Aggregate-row detection by SUM-EQUALITY math validation** (convo L189-190) — a *different* mechanism than our token-based `_AGGREGATE_TOKENS`; would catch an *unlabelled* total row our vocabulary misses. Candidate enhancement (must stay domain-neutral + measured + tolerant of float equality).

### ⚠️ CONFLICTS (do NOT copy verbatim — extract concept only)
- **`/v1/ingest` URL versioning** (convo L635) → violates our no-version-ref rule. Use `POST /api/ragbot/documents/create` + header `X-Schema-Version`.
- **Per-format `if .endswith('.xlsx')/elif '.pdf'` ladder** (convo L504-524) → violates Strategy+DI sacred rule. Our registry dispatch already replaces it. Adopt the INTENT (route by type), not the CODE.
- **`<context_safety_layer>` always-on app-injection** (convo L235) → violates sacred rule #10 (app must not inject text into the LLM prompt). Our `_resolve_xml_wrap_enabled` (default OFF, per-bot, ADR-governed) is the compliant form. **CONFLICT-unless-governed.**
- **bot_id-only namespace** (convo L122, L1027) → insufficient for us: two tenants can both pick `bot_id='support'`. Our 4-key `(record_tenant_id, workspace_id, bot_id, channel_type)` + RLS supersedes it. Do NOT downgrade.
- **Single `value_col` / price-centric `ParsedEntity.value`** (convo L355-372) → this is *exactly the bug* our fix-all S1-A is removing (`plans/260626-fix-all/plan.md:50` — issue #3 price-centric). Adopting it verbatim re-introduces the bug. Our target is **attribute-generic** (every column = labelled attribute, price = derived view).
- **`uuid4()` upsert** (convo L736) → would duplicate chunks on re-ingest; our content-hash idempotency + `X-Idempotency-Key` is a correctness invariant the convo lacks.

### Mapping to our 16 gaps + 15-step fix plan
The convo independently re-derives **the #1 CRUX** (`reports/MASTER_4PHASE_30AGENT_20260627.md:11,28`): **header detection is vocab-gated** (`document_stats.py:_is_header_row` → `_HEADER_EXACT_TOKENS` 155-205). A correctly-shaped non-VN header (`MARKS | CARGO DESCRIPTION`, `Producto | Precio`) matches nothing → table collapses to positional `col_N`. The convo's `_detect_table_headers_and_roles` (shape-only) is conceptually our fix-plan steps 1-2 (extract locale-neutral `_is_value_cell`, promote `tabular_markdown._looks_header` to SSoT, demote vocab to HINT). It also reconfirms:
- Gap 8/15 **`original_content` round-trip BROKEN** — grep confirms **zero read-back** of `original_content` at retrieval (`orchestration/` + `vector/` = 0 hits). The convo's repeated HALLU=0 claim **does not hold on our code today** because the verbatim-original is never fed back to the LLM (VERIFIED gap, report line 93).
- Gap 11 **VN structural markers compiled at import, no locale** → convo's "no hardcoded Chương/Điều regex" agrees; per-locale config pack is the fix.
- Gap 6/13 **file-level LLM selector** (`chunking_strategy/llm_resolver.py:51 "choose the single best"`) → convo calls this the death-trap; pushes DELETE + per-block routing.

---

## 4. CRITIQUE — SOUND vs RISKY vs OVER-SOLD

### SOUND (adopt the principle)
- Shape-based routing, block-level adaptive, narrate-then-embed dual-representation, parent-child, async-202, infra-isolation, "assume dirty parser → reconstruction layer", config-over-code for heterogeneity. ~70-85% already shipped; the convo is strong external validation of our charter (EVOLVE not rewrite).

### RISKY (adopt only with guards)
- **Pure-shape column-role on `numeric_ratio>0.7`** misfires on ID/SKU/year/phone/quantity columns (all numeric-shaped, not "value"). Our `document_stats.py` *hedges with token sets precisely because pure-shape is insufficient* — a real tension: convo says shape-ONLY, our code does shape+token. **Reconcile as: structural floor FIRST, vocab as optional HINT** (fix-plan step 2), not shape-only.
- **`name_col = first short-text col left of value`** mis-picks a category/stub column ("Nhóm | Tên | Giá"). Our `_column_roles` ambiguity-skip exists because this naive rule fails.
- **Word-density prose-noise filter (>12-15 words)** false-drops legitimate long product/legal names; our `_is_delimited_list_cell` carve-out preserves alias blobs the convo would drop.
- **Aggregate-row drop** ("last row == sum → delete"): many tables legitimately query the total. Must be a *flagged/labeled* row, not deleted; naive float-equality is error-prone.
- **Multi-page header propagation by BBox** depends on the very parser-coords the convo itself says are unreliable (L1366-1371). Self-undermining unless coords verified.

### OVER-SOLD (honest CLAUDE.md domain-neutral check)
- **Creeping vocab — the convo contradicts its own headline.** It claims "hoàn toàn Domain-neutral, không chứa bất kỳ từ khóa tiếng Việt/Anh nào" (L264) but hardcodes `k|tr|m|b|vnd|usd|eur` in the shape regex (L276). **`tr` IS a Vietnamese word (triệu)** — single-currency single-language baked in. *Honest note:* the SAME debt exists in OUR code — `number_format.py:46 _SUFFIX_MULT` is a `Final[dict]` (`tỷ/triệu/tr/M/nghìn/k`), NOT config-sourced. Neither side actually solves multi-currency: both `500k` and `$500` → 500000 (no FX, no per-currency scale). → currency/scale must be **config-sourced per-locale** (skill `metadata-optional-hint`), not a baked dict.
- **`startswith('Chương'/'Điều')` LegalContextStateMachine** (convo L1421-1443) — hardcoded VN legal words in a structure-deciding path. **Our grep proves `src/` has 0 such literals** — adopting it would be a REGRESSION of domain-neutrality. The convo itself rejects it at L1463; use only the dynamic/config form.
- **"chính xác tuyệt đối" / "HALLU=0" as architectural guarantees** (L39, L221, L243, L910, L1351) — **GIẢ THUYẾT, unmeasured.** Per rule#0 these need RAGAS Coverage/Faithfulness evidence. A mis-inferred value-col → confident wrong price = a fabricate/conflate HALLU; the architecture *reduces* the surface, cannot *guarantee* 0.
- **"-90% token/cost" (L830) and "+35% Context Precision vs LangChain" (L865)** — **invented benchmarks**; L865 admits the number is a sales device ("dùng con số kĩ thuật để bán"). Violates no-guess-must-measure. Must be load-tested on OUR corpus before believing.
- **Docling/Unstructured-mandatory + Tesseract/PaddleOCR + LayoutLMv3 + Pinecone/Milvus + RabbitMQ/Celery** — heavy deps, GPU/cost, version churn, SPOF (the convo flags Mistral-OCR as SPOF at L807-809 then re-introduces vision SPOFs). We already have Kreuzberg + pgvector + Redis Streams; Docling exists as opt-in (`ocr_factory.py`). **Swapping the stack = REWRITE, conflicts with EVOLVE.** Only the parser-adapter layer is a permitted rewrite zone.
- **Per-block micro-LLM narrate + propositionize** = N LLM calls per document at ingest — the exact cost the convo warns against (L800, L982) then re-adds per-block. No token budget given. Our rule-based `$0 table_narrator` should stay the default; LLM narrate per-bot opt-in.
- **Source padding:** convo has verbatim duplicated blocks (L1048-1139, L1143-1149) — lowers trust in the "Expert" framing.
- **`csv_chunker.py:33` records we ALREADY TRIALLED** the convo's headline "key: value" row rendering and **measured it neutral-to-slightly-negative** on the price-table load test (VERIFIED). The convo's headline idea is *not a free win for NL retrieval* — concrete counter-evidence.

---

## 5. THE BUILD PLAN — ordered, mapped to files, merged with the 15-step fix plan

Stance: **EVOLVE not rewrite** (`plans/260626-fix-all/plan.md:4`). The convo confirms our skeleton is correct; the work is "dây chưa nối hết" (wiring) + de-coupling the table/header vocab layer. This plan **dedupes with — does not contradict — the existing 15-step plan**; convo-only additions are flagged `[NEW]`.

### P0 — the CRUX trio (do these first; everything else is downstream)
1. **Kill the vocab header gate → structural SSoT.** Extract ONE locale-neutral `_is_value_cell(cell)` (Unicode `\p{Sc}` currency-symbol + digit-group shape; per-locale unit pack as an optional HINT) into a shared module; call it from BOTH `document_stats._is_header_row` AND `tabular_markdown._looks_header`. Promote `_looks_header` to the structural oracle (structural floor FIRST: all cells label-shaped + no value-cell + next row has value-cells/same col-count; vocab only optional fast-path). Rename `parse_money_vn`→`parse_amount`. → removes the `col_N` P0; serves multi-language/multi-domain headers. *Files:* `shared/document_stats.py:155-205,275-300`; `shared/tabular_markdown.py:40-90`; `shared/number_format.py:46`. *(skills: `table-header-detect-structural`, `metadata-optional-hint`, `multilingual-no-vocab`)* — **= fix-plan steps 1-2.**
2. **2-stream routing + attribute-generic late-binding table flow.** Land `plans/260626-fix-all/plan.md:50 S1-A` — every column = labelled attribute (price = derived view), drop the single `value_col`/`PRICE_MIN_VND` price-centric model. Fixes 6 issues at once (#2/#3/#4/#6/#8/ING-3). *Files:* `document_stats.py:_column_roles`, `stats_index_repository.py`, `csv_chunker.py`. **CONFLICT-AVOID:** do NOT adopt the convo's single-`value_col` — it re-introduces the bug.
3. **Hybrid metadata query — confirm + harden** (already shipped, `query_range_parser.py` + `generic_llm_extractor.py`). Lift VN money-suffix scales out of the `Final` dict into a per-locale config pack (small surgical change for true zero-hardcode + multi-currency). *Files:* `number_format.py:46`, language packs. **Add load-test of extractor RECALL** — if it misses "dưới 2 triệu", the number-HALLU protection silently disappears (the convo never measures this).

### P1 — wire the broken round-trips (the convo's HALLU=0 claim depends on these)
4. **Fix `original_content` retrieval round-trip** (Gap 8/15 — VERIFIED broken, 0 read-back). Persist `original_content` on the live ingest path (not test-only `smart_chunk_atomic`) and READ it back at generate so the LLM sees verbatim numbers. *Files:* `chunking/__init__.py:868`, `orchestration/nodes/generate.py`. — **= fix-plan steps 10-11.** Only after this is the narrate-then-embed HALLU lever actually closed.
5. **De-VN the wired narrate prompt** (report line 93, P0 — `llm_narrate.py:59-72` hardcodes Vietnamese vs line 53 "Preserve the source language"). *(skill `multilingual-no-vocab`)*
6. **Per-locale structure config pack** for hierarchy markers (Gap 11 — `vn_structural` compiled at import, no locale). Adopt the convo's CACH1 *dynamic-schema* form (config-driven), NOT the `startswith('Chương')` form it rejects. *(skill `multilingual-no-vocab`)* — **= fix-plan step 4.**
7. **Block-level routing decision: wire OR delete the file-level LLM selector** (`chunking_strategy/llm_resolver.py`). The convo + our LREC external ref both push DELETE in favor of deterministic per-block shape-analytics. Lead toward delete; keep `rule_resolver` as the deterministic tree. — **= fix-plan step 9.**
8. **Lossless-coverage gate** (our standard is STRONGER than the convo here — it has no end-to-end "no source text silently dropped" assert). Keep `assert check_chunk_gaps` after every strategy. *(skill `block-integrity-quality-gate`)* — **= fix-plan step 3.**

### P2 — convo-NEW additions (defer behind the CRUX; T2/future-T1)
9. `[NEW]` **Multi-page table header propagation** — feasibility-gate first (does Kreuzberg expose BBox/col-count?). If not, defer; if yes, scope a reconstruction-layer pass. *(skill `multi-row-header-merge`)*
10. `[NEW]` **Aggregate-row by sum-equality** as a *flag*, not a delete — domain-neutral, float-tolerant, measured.
11. `[NEW]` **Async-202 discipline audit** (T2 perf/UX): verify <200ms accept, stream-to-store, never-parse-in-request as an explicit SLA gate.
12. `[NEW]` **Per-tenant dynamic-typography schema + AI-layout vision fallback** (CACH1+CACH2) — biggest scope; Null-default opt-in per-bot only (model-tier/cost concern). Future T1.

### Non-negotiable guardrails for every step (CLAUDE.md compliance)
- **Zero-hardcode:** lift every convo magic number (`0.7`, `0.6`, `12`, `15`, `k/tr/m`) into `system_config`/`pipeline_config`/locale pack — do NOT copy the convo's inline constants.
- **Domain-neutral:** no `startswith('Chương')`, no vocab in structure-deciding paths.
- **No-version-ref:** no `/v1/ingest`; header `X-Schema-Version`. *(Also note our own report flags version-ref filenames `_13_…layer_1…`, `_19_sprint3…` as P0 — fix in the same sweep.)*
- **Sacred rule #10:** XML wrap stays governed/owner-opt-in; no free-form prompt-injection filter.
- **4-key + RLS:** never downgrade to bot_id-only namespace.
- **Idempotency:** content-hash safe-replace, never `uuid4()` upsert.
- **Measure before claim:** every "lift" / "HALLU=0" needs a RAGAS Coverage + Faithfulness load-test number, not an assertion (rule#0).

**Bottom line:** the convo is a high-quality independent re-derivation of our charter that validates EVOLVE-not-rewrite and sharpens three decisions (delete the file-level LLM selector; per-block shape routing; fix the `original_content` round-trip before claiming HALLU=0). Adopt the **principles**, reject the **literals** (vocab regex, `/v1`, bot_id-only, Docling-mandatory, single value_col, unmeasured benchmarks). Lead the build with the shape-detector CRUX (`col_N` P0) + 2-stream routing + hybrid metadata query — exactly where our 15-step plan already points.

---
### Key file:line evidence index (all VERIFIED this session)
- `interfaces/http/routes/documents.py:92,94` — canonical 202 ingest
- `infrastructure/parser/registry.py:45,64,153` — Port+Registry DI dispatch
- `application/services/document_service/ingest_core.py:262` — byte-sniff `sniff_real_mime`
- `shared/document_stats.py:155-205` (`_HEADER_EXACT_TOKENS` vocab gate = P0 CRUX), `:275-300` (`_is_header_row`), `:413-493` (3-tier role cascade), `:783-833` (multi-row header merge)
- `shared/number_format.py:46` (`_SUFFIX_MULT` Final dict — currency debt, both sides)
- `shared/query_range_parser.py:87,189` + `infrastructure/metadata_filter/generic_llm_extractor.py:89` — dual metadata-extract paths (shipped)
- `infrastructure/repositories/stats_index_repository.py` — `query_by_price_range`, tenant+bot scoped
- `infrastructure/vector/pgvector_store.py:66,258` (mandatory `record_bot_id`) + `infrastructure/db/engine.py:174` (`SET LOCAL app.tenant_id` RLS)
- `shared/chunking/__init__.py:109,866,868` — parent-child + `narrated_text`/`original_content`
- `orchestration/` + `vector/` — `original_content` read-back = **0 hits** (round-trip broken, Gap 8/15)
- `shared/chunking/csv_chunker.py:33` — "key:value" row render measured **neutral-to-negative**
- `infrastructure/chunking_strategy/llm_resolver.py:51` — file-level "choose the single best" (death-trap)
- `orchestration/query_graph.py:351` — governed `_resolve_xml_wrap_enabled` (default OFF)
- `plans/260626-fix-all/plan.md:50` — S1-A late-binding attribute-generic table flow
- `reports/MASTER_4PHASE_30AGENT_20260627.md:11,28,93,124` — 16-gap + 15-step, CRUX, broken round-trip