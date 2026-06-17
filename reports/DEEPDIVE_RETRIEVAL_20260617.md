# Deepdive — Stats/Aggregation Query Path (READ-ONLY audit)

Date: 2026-06-17 · Scope: query pipeline end-to-end, focus on the recently-added
stats-index (price-range / superlative / aggregation) route. Evidence is
`file:line` + exact state-key / branch condition. No `src/` files were modified.

---

## 0. Path map (verified wiring)

```
guard_input → cache_check_and_understand_parallel → understand_query
   → [router/_router_route] → rewrite | retrieve | decompose
   → condense_question (multi-turn only) OVERWRITES state["query"]   ← query_graph.py:1957
retrieve (_retrieve_node)
   parse_range_query(state.get("query"))                              ← retrieve.py:195-196
   stats gate (confidence ≥ 0.7, not struct-ref, superlative on)     ← retrieve.py:239-241
   race path (default OFF)                                           ← retrieve.py:253
   sequential path → _do_stats_lookup → linked_chunks               ← retrieve.py:494 / query_graph.py:2737
   returns retrieve_mode="stats_index", graded_chunks seeded        ← retrieve.py:525-534
_retrieve_route: retrieve_mode.startswith("stats") → "generate"      ← query_graph.py:3354-3355
   (SKIPS rerank → mmr_dedup → neighbor_expand → grade)
generate reads state["graded_chunks"] ONLY                           ← generate.py:114
guard_output grounding judge (intent-gated, sync for aggregation)    ← guard_output.py:85-150
persist → bg cache write (numeric ⇒ NULL-embedding, exact-hash only) ← persist.py:131-214
```

Confirmed: in stats mode the `grade` node never executes — `_retrieve_route`
routes `retrieve → generate` directly (query_graph.py:3354). Therefore the
stats-bypass added inside `grade()` (grade.py:99-111) is **dead code on the
primary path**. It only runs on the `rewrite_retry → retrieve → grade` re-entry,
which stats mode also short-circuits, so it is effectively unreachable. Not a
bug, but misleading (see Issue C).

---

## 1. FLAKINESS ROOT CAUSE — `condense_question` overwrites `state["query"]`

**Claim (SỰ THẬT, evidence-backed): the flakiness is multi-turn condense, not the
race, not the cache, not the `"không?"` phrasing per se.**

### Evidence chain
- `parse_range_query` is run on `state.get("query")` — `retrieve.py:195-196`:
  ```python
  _raw_query = state.get("query") or ""
  _range_filter = _parse_range_query(_raw_query)
  ```
- `condense_question` (runs only when conversation history is present) replaces
  `state["query"]` with the **LLM-condensed** text — `query_graph.py:1956-1957`:
  ```python
  condensed = normalize_vn_section_numerals(condensed)
  return {"query": condensed, "original_query": state["query"]}
  ```
  Gate to enter condense: `query_graph.py:1912-1917` (`history >= DEFAULT_CONDENSE_MIN_HISTORY_TURNS`
  and `total_chars >= DEFAULT_CONDENSE_MIN_HISTORY_CHARS`).
- The condense LLM call is `purpose="condensing"` (`query_graph.py:1941-1942`). An
  LLM rewrite is not guaranteed to preserve the literal price token `"dưới 500k"`.
  When the condensed query drops/rephrases the bound (e.g. "Spa có dịch vụ giá rẻ
  không?"), `parse_range_query` returns `None` → stats gate at `retrieve.py:239`
  is not entered → falls through to vector retrieve → on a sparse/low-score
  corpus the bot refuses ("chưa có thông tin").
- When the condensed text keeps "dưới 500k", the stats route fires and the bot
  lists services. Same user text, **different condensed query run-to-run** =
  observed flakiness.

### Why "Liệt kê dịch vụ dưới 700.000 đồng" is reliable
- Two compounding reasons, both evidence-based:
  1. It was (per the report) tested in a fresh/short conversation → condense is
     skipped (`query_graph.py:1913` early-return) → `state["query"]` stays the raw
     text → deterministic stats route.
  2. Even when condensed, `"Liệt kê … dưới 700.000 đồng"` is an explicit command
     with the unit `đồng` spelled out; condense tends to preserve it, and
     `operation` resolves to `"list"` via the `liet ke` signal
     (`query_range_parser.py:114-130, 268`), making the price bound more
     "sticky" through a rewrite. `"Có … không?"` is a yes/no question form that
     condense is more likely to neutralise.

### Ruled OUT as the primary cause (with evidence)
- **Race mode** — `DEFAULT_STATS_INDEX_RACE_ENABLED = False`
  (`_21_streaming_upload_wb_2_p1_5.py:92`). Unless the spa bot sets
  `stats_index_race_enabled=true` in `pipeline_config`, the sequential path
  (`retrieve.py:492-541`) runs, which is deterministic. **If the spa bot DID enable
  race, that is a second, independent non-determinism source** (the race resolver
  prefers whichever task completes first within the event-loop tick —
  `retrieve.py:406-435` — and on simultaneous completion prefers stats; timing is
  not guaranteed). Verify per-bot config before excluding.
- **Generation temperature** — `DEFAULT_GENERATION_TEMPERATURE = 0.0`
  (`_10_rbac.py:200`), so generate is deterministic given identical context.
- **Semantic cache** — a successful numeric (price-list) answer is written with a
  **NULL embedding** (`persist.py:146,211` → `_bg_cache_write numeric=True` →
  `persist.py:87 query_embedding=[]`), so it is hit **only via exact SHA256 hash**
  on normalised text (`semantic_cache.py:137-138, 409-449`), never via cosine
  (`semantic_cache.py:484 "{col} IS NOT NULL"`). Refuse answers are not cached at
  all (`persist.py:161`, `_REFUSE_ANSWER_TYPES`, `_04_jwt_auth.py:31-33`).
  ⇒ The cache cannot, by itself, flip an identical-text query between answer and
  refuse run-to-run; it is deterministic. (It DOES amplify whichever outcome got
  cached first for that exact text — see Issue D.)

### Minimal fix
Parse the range filter from the **original user text**, not the condense-rewritten
query. At `retrieve.py:195`:
```python
_raw_query = state.get("original_query") or state.get("query") or ""
```
`original_query` is set by condense at `query_graph.py:1957`. Rationale: a numeric
price bound is a deterministic, literal feature of what the user typed — it must
not be subject to LLM paraphrase. This makes stats routing condense-invariant and
removes the flakiness without touching the answer (QG #10 safe — it only changes
which retrieval path runs, not the answer text).

---

## 2. CORRECTNESS ISSUES (ranked)

### Issue A — `record_chunk_id` is NEVER populated at ingest ⇒ price-range answers are diluted with the whole price table
Severity: HIGH (T1 answer quality).
- Ingest writes stats rows via `DocumentService._insert_stats_index` →
  `bulk_insert` (`application/services/document_service/__init__.py:274-280`).
  The `bulk_insert` INSERT column list does **not** include `record_chunk_id`
  (`stats_index_repository.py:115-121`); the chunk linkage is only stuffed into
  `attributes_json["chunk_index"]` (`stats_index_repository.py:110-113`).
- Consequence at query time: `_do_stats_lookup` builds `chunk_ids` from
  `e.get("record_chunk_id")` (`query_graph.py:2774-2778`) — always empty — so it
  always falls into the **doc-level fallback** `find_chunks_by_document_ids`
  (`query_graph.py:2808-2815`), which returns the first 10 chunks **per document**
  (`document_repository.py:236, 264-274`) — i.e. the WHOLE price table, not the
  rows matching `< 500k`. The LLM must re-filter "< 500k" itself and can miss rows
  (acknowledged in-code at `query_graph.py:2822-2828`).
- Compounding: `query_by_price_range` (the range path) does not even SELECT
  `record_chunk_id` (`stats_index_repository.py:228-234`), whereas `top_by_price`
  does (`stats_index_repository.py:291`). So even after an ingest backfill, the
  range path could not use the FK without also adding it to this SELECT.
- The synthetic chunk (the already-filtered `name: price` list,
  `query_graph.py:2849-2859`) partially mitigates this, but it is prepended to the
  diluted doc-level chunks (`linked_chunks = synthetic_chunks + linked_chunks`,
  `query_graph.py:2862`), so the LLM still sees the full unfiltered table as
  competing context.

**Fix (mid-term, correct tier = ingest/data):**
1. Persist `record_chunk_id` at ingest. `ParsedEntity.chunk_index`
   (`shared/document_stats.py:121`) already records the positional source chunk;
   map it to the persisted chunk UUID (available in the same ingest stage where
   chunks are stored) and pass it into `bulk_insert`; add the column to the INSERT
   (`stats_index_repository.py:115-121`).
2. Add `record_chunk_id` to the `query_by_price_range` SELECT
   (`stats_index_repository.py:228-234`) so the range path can prefer the matching
   rows' chunks over the doc-level fallback.
3. Backfill migration (alembic) for existing corpora.
- Short-term (no ingest change): the synthetic chunk already carries the filtered
  rows; consider NOT appending the diluting doc-level chunks when the synthetic
  chunk is non-empty (cap the doc-level fallback to e.g. 1–2 chunks for citation
  provenance only). This is a smaller, lower-risk change than the ingest backfill.

### Issue B — superlative `top_by_price` ignores `price_column` mismatch with parser
Severity: LOW–MED.
- The parser always emits `price_column="any"` for superlatives
  (`query_range_parser.py:319,325`). `top_by_price` ranks by
  `COALESCE(price_primary, price_secondary)` for `"any"`
  (`stats_index_repository.py:285`). When a row has both columns populated with
  divergent semantics (e.g. primary=member price, secondary=list price), the
  ranking column choice is silent and may surface the "wrong" superlative. Not a
  HALLU (numbers are grounded) but a correctness-of-ranking nuance. No fix
  required unless corpora use dual-price semantics; document as a known limit.

### Issue C — dead/duplicated stats-bypass in `grade()`
Severity: LOW (maintainability / misleading).
- `grade.py:99-111` re-implements the stats skip, but `_retrieve_route`
  (`query_graph.py:3354`) already routes stats → generate, so `grade` is not on
  the stats path. The only way to reach `grade` after stats is via
  `rewrite_retry → retrieve` (`query_graph.py:3739`), but a stats retrieve would
  again short-circuit at `_retrieve_route`. ⇒ the `grade.py` block is effectively
  unreachable. Recommend removing it OR removing the `_retrieve_route` stats
  branch and letting grade handle the skip — pick one source of truth. (Keeping
  both is harmless at runtime but invites drift.)

### Issue D — cache amplifies the first outcome for an exact-text query
Severity: LOW (interacts with Issue 1).
- Because numeric answers cache via exact hash (`semantic_cache.py:409-449`), the
  FIRST outcome for a given exact text "sticks" for the TTL
  (`DEFAULT_SEMANTIC_CACHE_TTL=3600`, `_04_jwt_auth.py:13`). If the first run
  (with a bad condense) refused, the refuse is NOT cached (good), so the next run
  re-attempts — but if the first run answered, the correct answer is cached and
  pins the good outcome. Net: cache slightly biases toward stability once a good
  answer lands; it does not cause the flake but can mask it during testing
  (re-running the same text may "look fixed"). Use `bypass_cache=true` when
  measuring (per memory rule). The real fix is Issue 1.

---

## 3. Stats→generate bypass correctness (STEP 3)

- **Rerank/mmr/grade skip** — acceptable: the stats rows are deterministic SQL
  results and the rationale (fuzzy reranker rescoring the synthetic price list low
  and dropping it) is sound (`query_graph.py:3348-3353`). Risk: the diluting
  doc-level chunks (Issue A) bypass MMR dedup too, so near-duplicate table chunks
  reach generate unfiltered — minor token cost, not correctness.
- **Grounding / HALLU=0** — PRESERVED for aggregation/comparison. Grounding judge
  is intent-gated (`guard_output.py:85-95`); `DEFAULT_GROUNDING_INTENTS` includes
  `aggregation`, `comparison`, `multi_hop`, `factoid`
  (`_15_m2_neighbor_window_expansion.py:112-117`). Async grounding (suppress sync
  judge) only fires for `factoid` (`DEFAULT_GROUNDING_CHECK_ASYNC_INTENTS=("factoid",)`,
  `_14_anti_abuse_ip_rate_limit_hon.py:222`), so aggregation queries get the SYNC
  judge before the user sees the answer. **Caveat (GIẢ THUYẾT, needs a trace to
  confirm):** the stats route does NOT set/repair `intent` (`retrieve.py:525-534`
  returns no `intent` key). If the heuristic classifier mislabels
  "Có dịch vụ nào dưới 500k không?" as something outside the grounding set
  (e.g. an OOS/chitchat label), the grounding judge is SKIPPED for that turn even
  though it took the stats route — a hole that should be closed by treating the
  stats route as a grounding-eligible intent. Verify with a per-turn trace of
  `state["intent"]` on the flaky query.
- **Citations / observability with `chunk_id=""`** — the synthetic chunk has
  `chunk_id=""`, `document_name=""` (`query_graph.py:2855-2858`). Impact:
  - `_build_sources` / citation extraction keyed on chunk_id will produce an empty
    or anonymous source for the synthetic row → the price-list answer may show
    "no citation" even though the numbers are grounded in the corpus.
  - Cache snapshot `_chunks_snap` reads `document_name`/`source_url`
    (`persist.py:185-196`) — empty for the synthetic chunk, so a cache-restored
    answer has a degraded source list. The doc-level fallback chunks DO carry
    `document_name`/`source_url` (`document_repository.py:291-296`), so provenance
    is not fully lost, but the explicit "matched rows" lose attribution.
  - Recommendation: stamp the synthetic chunk with the source `document_name` /
    `record_document_id` from the winning entities so citations attribute to the
    real price document (domain-neutral, grounded — not an app-inject).

---

## 4. Ranked fix list (minimal, tier-correct)

1. **[T1, retrieval] Flakiness** — parse range from `original_query`
   (`retrieve.py:195`). One-line, condense-invariant. (Issue 1)
2. **[T1, data/ingest] Dilution** — populate `record_chunk_id` at ingest +
   add it to `query_by_price_range` SELECT + backfill; or short-term cap the
   doc-level fallback when the synthetic chunk is present. (Issue A)
3. **[T2, observability] Synthetic-chunk attribution** — stamp `document_name` /
   `record_document_id` on the synthetic chunk so citations/cache sources resolve.
   (Issue D-citations)
4. **[T1, safety] Grounding eligibility** — ensure stats-route turns are
   grounding-eligible regardless of the classifier label (verify intent on the
   flaky query first). (STEP 3 caveat)
5. **[T3, maintainability] De-dupe** the stats bypass — keep it in `_retrieve_route`
   OR `grade()`, not both. (Issue C)

All proposed changes alter only WHICH retrieval path runs / WHICH source metadata
attaches — none inject text into the LLM prompt or override the answer (QG #10
preserved). HALLU=0 path (sync grounding on aggregation) is unaffected.
