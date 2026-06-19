# FLOW DEBUG: RETRIEVE → RERANK → topK — Backward Verification Map

> Date: 2026-06-18 | Read-only audit | Author: sonnet subagent (research only)
> Purpose: Enable per-run debug trace + backward verification ("did the gold chunk survive?")
> All claims are file:line evidence — no guesses.

---

## 1. RETRIEVE CANDIDATES

### Chunk structure produced by `_run_hybrid_for_query`

`retrieve.py:1012–1024` (port path) and `retrieve.py:1033–1100` (legacy hybrid_search path):

```python
{
  "chunk_id":     str(c.chunk_id),            # UUID string — the backward-verify anchor
  "document_id":  str(c.document_id),          # parent doc UUID
  "content":      c.text,
  "text":         c.text,
  "score":        c.score,                     # SINGLE scalar — RRF-fused (0.01–0.05) or vector cosine
  "document_name": ...,
  "chunk_index":  getattr(c, "chunk_index", ""),
  **(c.payload if hasattr(c, "payload") else {})  # may carry metadata_json fields
}
```

**What `score` is**: For the hybrid_search path (`retrieve.py:1099`), `raw = await vector_store.hybrid_search(**_hs_kwargs)` — the adapter returns a pre-fused score. The RRF formula is applied INSIDE the adapter, not exposed as separate `bm25_score` / `vector_score` fields. When multi-query fan-out fires (`retrieve.py:1341–1384`), chunks from N branches are RRF-merged a SECOND time via `mq_rrf_merge_chunks` at line 1371 before being truncated to `_retrieve_top_k`. The resulting `score` field on every candidate is the **RRF rank score** after the final merge — there is NO per-chunk `bm25_score` or `vector_score` field exposed to downstream nodes.

### top_K pre-rerank

`retrieve.py:701–732`:
- `_retrieve_top_k` = `int(_pcfg(state, "top_k", DEFAULT_TOP_K))` (default from `constants.py`)
- Per-intent override: `_topk_by_intent` dict can map intent → different top_k (`retrieve.py:705–728`)
- Aggregation intents are promoted to the aggregation cap (`retrieve.py:712–719`)
- After RRF merge: `chunks = chunks[:_retrieve_top_k]` (`retrieve.py:1372`)

After all retrieval paths (lexical BM25 fusion, diacritic restore, multistage), the final slice happens at `retrieve.py:1719`: `chunks = chunks[:_retrieve_top_k]`.

The result is stored in state as `state["retrieved_chunks"]` (`retrieve.py:1867`).

---

## 2. ROUTING — DECISION TREE IN `retrieve.py`

Routing check order (`retrieve.py:198–573`), applied to `_raw_query = state.get("original_query") or state.get("query")`:

```
1. parse_range_query(_raw_query)        → RangeFilter(price range) if present
2. parse_code_query(_raw_query)         → RangeFilter(operation="keyword", keyword=<code>) if spec code
3. parse_list_query(_raw_query)         → RangeFilter(operation="keyword", keyword=<cat>) if list/count
4. Superlative kill-switch              → None if max/min + stats_superlative_enabled=False
5. Structural-reference guard           → None if "Điều N" anchor detected
6. Confidence gate                      → proceed only if filter.confidence >= RANGE_QUERY_MIN_CONFIDENCE

If any filter survives → stats_index_route (or race mode)
Else → hybrid vector path (speculative → multi-query → single-shot)
```

### BUG-1 CONFLATE PATH (price-of-entity)

`query_range_parser.py:374–377`:
```python
# A price factoid ("… giá bao nhiêu") is NOT a list/count query — it asks
# one price, not the full set. Let parse_range_query / vector handle it.
if "gia bao nhieu" in folded or "bao nhieu tien" in folded:
    return None
```

When a user asks `"<DịchVụX> giá bao nhiêu?"`:
- `parse_range_query` returns None (no range tokens like "dưới", "từ…đến")
- `parse_code_query` returns None (no spec code shape)
- `parse_list_query` returns **None** because of the guard at line 374–377

Result: **fallthrough to vector hybrid search**. The vector path kéo co-occurrence chunks (multi-service rows), LLM conflates prices → BUG-1.

The fix plan at `plans/260618-phaseA-bug1-conflate/plan.md` describes adding `parse_price_of_entity_query` BETWEEN `parse_code_query` and `parse_list_query`, wiring at `retrieve.py:216–227`.

### `retrieve_mode` state key

Each routing branch sets `state["retrieve_mode"]` with values including:
- `"stats_index"` / `"stats_race_winner"` / `"vector_race_winner"` (stats paths, `retrieve.py:492,557`)
- `"speculative"` (`retrieve.py:661`)
- `"doc_summary"` (`retrieve.py:615`)
- `"fallback_original"` (`retrieve.py:1495`)
- (unset = normal vector path)

This is the backward-verify signal for **which branch a query took**.

---

## 3. RERANK + topK

### Input / output state keys

`rerank.py:65–488`:
- **Input**: `inp = state.get("retrieved_chunks", [])` (`rerank.py:66`)
- **Output**: `return {"reranked_chunks": out, "rerank_score_mode": mode}` (`rerank.py:487`)

### topN cutoff

`rerank.py:72–81`:
```python
top_n = _pcfg(state, "rerank_top_n", DEFAULT_RERANK_TOP_N)
# per-intent override via rerank_top_n_by_intent dict
```
The reranker API call: `out = await _active_reranker.rerank(query=..., chunks=inp, top_n=top_n, ...)` (`rerank.py:171–176`).

After the API call, three additional filters can reduce `out` further:
1. **Cliff filter** (`rerank.py:255–304`): `_cliff_detect_filter(out, absolute_floor, gap_ratio, min_keep)` — drops chunks where a score cliff gap > `gap_ratio` is detected
2. **Threshold filter** (`rerank.py:305–331`): removes chunks with `score < min_score`
3. **Max-to-LLM cap** (`rerank.py:392–404`): `out = out[:_max_to_llm]` when `rerank_max_chunks_to_llm > 0`
4. **Retrieval safety-net** (`rerank.py:457–482`): re-injects top-N retrieval-ordered chunks that were dropped by the reranker

### Rerank score on chunk dict

**IMPORTANT**: The reranker overwrites (or preserves) `c["score"]` with the cross-encoder score (0..1 scale when `mode=="rerank"`). After `out = await _active_reranker.rerank(...)`, the `score` field on each returned chunk dict is the **reranker cross-encoder score**, NOT the RRF score.

For bypass modes (null_reranker, disabled, intent_skip), `out = inp[:top_n]` (`rerank.py:197`) — `score` remains the RRF score (~0.01 range).

`rerank_score_mode` state key distinguishes the two: `"rerank"` = cross-encoder scale, anything else = RRF scale.

The debug path at `rerank.py:426–447` logs `top_k_scores` and `top_k_sources` for the first 5 chunks when `DEBUG_RETRIEVAL=true` or `state["debug_full"]=True`.

---

## 4. WHAT REACHES THE LLM

### State key flow after rerank

```
rerank → state["reranked_chunks"]
         ↓
mmr_dedup (query_graph.py:3052):
  inp = state["reranked_chunks"]
  return {"reranked_chunks": filtered}   ← still "reranked_chunks"
         ↓
neighbor_expand (query_graph.py:3115):
  seeds = state["reranked_chunks"]
  return {"reranked_chunks": expanded}   ← still "reranked_chunks"
         ↓
grade (grade.py:88):
  inp = state["reranked_chunks"]
  return {"graded_chunks": graded}       ← KEY CHANGE: "graded_chunks"
         ↓
generate (generate.py:114):
  graded = state["graded_chunks"]        ← LLM context source
```

### Final chunk set passed to LLM

`generate.py:114`:
```python
graded = state.get("graded_chunks") or []
```

In `generate.py:524–528`, a set `chunk_ids_allowed` is built from `graded`:
```python
chunk_ids_allowed = {
    str(c.get("chunk_id") or c.get("id") or "")
    for c in graded
    if c.get("chunk_id") or c.get("id")
}
```

Chunks are serialized into `<context id="{cid}" ...>` blocks at `generate.py:542–580`. The `cid` is the `chunk_id` UUID — the backward-verify anchor is preserved end-to-end.

### Additional filters in generate node that can drop chunks

Before LLM call, `graded` is further filtered at `generate.py:354–523`:
1. **Prompt compression** (`generate.py:354–394`): truncates content per chunk
2. **Adaptive context pruning** (`generate.py:401–417`): drops tail when top-score high + count > max_n
3. **Lost-in-middle reorder** (`generate.py:420–438`): reorders but does NOT drop
4. **Token optimization** (`generate.py:472–479`): `apply_token_opt` can drop low-score chunks
5. **Context chars cap** (`generate.py:510–523`): tail chunks dropped if cumulative chars > `_ctx_cap`

Each of these reduces `graded` in-place. `chunk_ids_allowed` is built AFTER all these filters (`generate.py:524`), so it reflects the FINAL set sent to the LLM.

---

## 5. WHAT IS ALREADY LOGGED

### structlog events — retrieve node

| Event | File:line | Fields captured |
|---|---|---|
| `stats_index_race_winner` | retrieve.py:478–483 | entity_count, linked_chunks, intent |
| `vector_race_winner` | retrieve.py:506–512 | candidates, intent |
| `retrieve_rrf_merged` | retrieve.py:1378–1384 | n_queries, successful_branches, merged_unique |
| `hybrid_search_executed` (audit) | retrieve.py:1824–1835 | top_k, rrf_k, candidates_count, top_score, min_score, metadata_filter |
| `chunks_retrieved` (audit) | retrieve.py:1836–1855 | count, first 10: {chunk_id, score, doc_name, content_preview[:_RP]} |
| `lexical_rrf_fused` | retrieve.py:1720–1725 | vector_count, lexical_count, fused_count |

### structlog events — rerank node

| Event | File:line | Fields captured |
|---|---|---|
| step_ctx metadata | rerank.py:229–236 | mode, input, reranked, top_score, rerank_top_n |
| `filter_min_score` sub-step | rerank.py:278–331 | n_in, n_kept, n_dropped, strategy, top_score_in/out |
| `rerank_min_score_filtered` | rerank.py:325–331 | before, after, threshold, mode |
| `rerank_max_chunks_cap` | rerank.py:399–403 | before, after, cap, intent |
| `rerank_threshold_gate` | rerank.py:369–376 | top_score, threshold, refused, mode, strategy |
| `rerank_executed` (audit) | rerank.py:412–423 | mode, before, after, top_score_active, min_score_filter, provider |
| `rerank_retrieval_safety_net` (audit) | rerank.py:480–482 | added, safety_n, stamp_score |
| `retrieval_chunks_debug` | rerank.py:428–447 | query, top_k_scores[5], top_k_sources[5], chunk_count, mode | ← **ONLY when DEBUG_RETRIEVAL=true or state.debug_full** |

### structlog events — grade node

| Event | File:line | Fields captured |
|---|---|---|
| `crag_grade_distribution` | grade.py:301–308 | relevant, irrelevant, ambiguous, total, source |
| `grade_executed` (audit) | grade.py:548–563 | relevant, irrelevant, ambiguous, graded_kept, retrieval_adequate |

### structlog events — generate node

| Event | File:line | Fields captured |
|---|---|---|
| `generate_started` (audit) | generate.py:115–125 | context_chunks, context_chars |
| `prompt_build` step metadata | generate.py:617–629 | context_chars, history_msgs, context_chunks, context_chunks_dropped, token_opt_dropped_* |

### request_chunk_refs table (persist path)

`callbacks.py:200–224` feeds `finalize_request_log(retrieved_chunks=[...])` with `state.get("graded_chunks")`:
```python
retrieved_chunks=[
    {"chunk_id": c.get("chunk_id") or c.get("id"), "rank": idx, "score": float(c.get("score",0))}
    for idx, c in enumerate(_graded_for_refs)
]
```
`request_log_repository.py:181–225` maps this to `RequestChunkRefModel` rows with `(record_request_id, record_chunk_id, rank, score)`. These are **graded_chunks** (post-CRAG, final LLM set), NOT retrieved_chunks or reranked_chunks.

### model_invocations.retrieved_chunk_ids — DROPPED

`alembic/versions/20260417_0019_drop_unused_columns.py:32`:
```sql
ALTER TABLE model_invocations DROP COLUMN IF EXISTS retrieved_chunk_ids
```
The column existed in alembic 0007 but was dropped in 0019. The `invocation_logger.py` does NOT populate any chunk-id array. The `model_invocations` table carries NO chunk-id information post-0019.

---

## 6. GAPS FOR BACKWARD DEBUG TRACE

To reconstruct the full journey for a query:
`query → route taken → N candidates with scores → topK survivors with rerank scores → final LLM chunk set`

### What IS captured now

| Stage | Captured? | Where |
|---|---|---|
| Route taken | YES | `retrieve_mode` state key + `stats_index_route` / structlog events |
| N candidates (count only) | YES | `hybrid_search_executed` audit: `candidates_count`, `top_score`, `min_score` |
| First 10 candidate chunk_ids + scores | YES | `chunks_retrieved` audit: `chunks[0:10]` with chunk_id + score |
| Candidates 11..N chunk_ids | **MISSING** | Audit truncates at 10 (`retrieve.py:1852`) |
| Pre-rerank candidate list (all N) | **MISSING** | Not logged as a full list |
| Rerank input/output counts | YES | `rerank_executed` audit: before/after |
| Rerank survivor chunk_ids | **MISSING** | No per-chunk audit in rerank; only `top_k_scores[5]` + `top_k_sources[5]` in debug path |
| Per-chunk rerank score | **MISSING** | `retrieval_chunks_debug` logs scores[0:5] only, requires DEBUG_RETRIEVAL=true |
| MMR dedup before/after counts | YES | `mmr_dedup` audit: before/after |
| MMR survivor chunk_ids | **MISSING** | No per-chunk audit |
| Grade decisions per chunk | PARTIAL | `crag_grade_distribution` counts only; no per-chunk verdict log |
| Final graded chunk_ids to LLM | YES (persisted) | `request_chunk_refs` table: (chunk_id, rank, score) for graded_chunks |
| Context chars cap drops | YES | `prompt_build` step metadata: context_chunks_dropped |
| Which chunks survived to LLM | YES (persisted) | `request_chunk_refs` — but this is AFTER all filtering, not intermediate |

### Missing per-stage capture points — concrete list

**Gap 1 — Pre-rerank full candidate list (post-retrieve)**
- No audit event logs chunk_ids for candidates 11+ (`chunks_retrieved` truncates at 10)
- Fix: Extend `chunks_retrieved` audit OR add an optional `retrieve_chunk_ids_all` key on state (list of UUIDs) for backward-verify tooling

**Gap 2 — Rerank survivor chunk_ids**
- After `out = await _active_reranker.rerank(...)` at `rerank.py:171`, the reranked list is not logged with per-chunk IDs
- `retrieval_chunks_debug` at `rerank.py:428–447` is DEBUG_RETRIEVAL=true gate only, logs top 5 only
- Fix: Add an audit event `rerank_survivors` with `[{"chunk_id": c["chunk_id"], "score": c["score"]} for c in out]` after the safety-net at `rerank.py:482`

**Gap 3 — Per-chunk rerank score on all survivors**
- `rerank_executed` audit reports aggregate stats only (before, after, top_score_active)
- Fix: Include per-chunk list in the audit payload (gated by a `debug_full` flag to avoid log bloat)

**Gap 4 — Grade verdict per chunk_id**
- `crag_grade_distribution` logs counts; no per-chunk `{chunk_id, verdict}` is emitted
- Fix: Add `graded_chunk_verdicts=[{"chunk_id": ..., "relevance": ...} for c in graded]` in `grade_executed` audit (or a new `grade_chunk_verdicts` event)

**Gap 5 — Identify which chunks were dropped by generate filters**
- `prompt_build` step metadata logs count of dropped chunks (`context_chunks_dropped`) but not chunk_ids
- Fix: Emit chunk_ids of dropped chunks in `prompt_build` metadata

**Gap 6 — No distinction between retrieved_chunks vs graded_chunks in `request_chunk_refs`**
- `callbacks.py:215` only writes `graded_chunks` (final LLM set) to `request_chunk_refs`
- No DB record of intermediate stages: retrieved (all candidates), reranked (topN after reranker), graded (after CRAG)
- Fix: Add `retrieved_chunk_ids` and `reranked_chunk_ids` columns to `request_chunk_refs` (or separate tables) — backward-verify requires seeing ALL three stages

---

## 7. topK SURVIVAL TABLE

For a given query, here is how to reconstruct each chunk's journey, which fields exist, and what is missing:

| Stage | State key | Fields available | chunk_id present? | Score present? | Logged in DB? | Missing |
|---|---|---|---|---|---|---|
| **Retrieve output** | `retrieved_chunks` | chunk_id, score (RRF), content, document_id, chunk_index | YES | YES (RRF) | Partial (first 10 in audit) | chunk_ids for #11+ |
| **Rerank input** | `retrieved_chunks` (same) | same | YES | YES (RRF) | No | Full list not persisted |
| **Rerank topN** | `reranked_chunks` | chunk_id, score (cross-encoder or RRF), `_safety_injected` flag | YES | YES | No | Per-chunk list not in any audit event |
| **MMR dedup output** | `reranked_chunks` (updated) | same minus deduped | YES | YES | No | No per-chunk audit |
| **Neighbor expand output** | `reranked_chunks` (updated) | + `is_neighbor_expanded: True` on new chunks | YES | score=0.0 for neighbors | No | No per-chunk audit |
| **Grade output** | `graded_chunks` | + `relevance` field (relevant/irrelevant/ambiguous) | YES | YES | Partial (request_chunk_refs) | verdict per chunk not in DB |
| **Generate input (final)** | `graded_chunks` (after prompt filters) | chunk_id, score, content | YES | YES | YES — `request_chunk_refs` | Context-cap dropped chunks not marked |

### How to reconstruct a chunk's journey (current state)

With current instrumentation, you can answer:
- Was the gold chunk in the FIRST 10 candidates? → `chunks_retrieved` audit event
- How many candidates total reached rerank? → `rerank_executed` audit: `before`
- How many survived rerank? → `rerank_executed` audit: `after`
- Did the gold chunk reach the LLM? → query `request_chunk_refs` by `record_request_id`

What you CANNOT answer without adding the gaps above:
- Was the gold chunk in candidates 11+? (truncated audit)
- What was the gold chunk's rerank score?
- Was the gold chunk dropped by cliff/threshold/MMR/neighbor-expand?
- What CRAG grade was assigned to each chunk?

---

## 8. SUMMARY TABLE — EXISTING vs MISSING CAPTURE

| Stage | What's logged now | Gap |
|---|---|---|
| Retrieve route | `retrieve_mode` state key + structlog `stats_index_route` etc. | None — route is fully observable |
| Retrieve candidates (N) | `hybrid_search_executed`: count + top/min score; `chunks_retrieved`: first 10 with chunk_id + score | chunk_ids for candidates 11..N |
| Rerank input/output counts | `rerank_executed` audit | Per-chunk list with scores |
| Rerank survivor chunk_ids | `retrieval_chunks_debug` (DEBUG_RETRIEVAL=true, top 5 only) | Always-on per-chunk rerank audit |
| MMR dedup before/after | `mmr_dedup` audit: count | Per-chunk survivor list |
| Grade verdict | `grade_executed` audit: counts only | Per-chunk `{chunk_id, verdict}` |
| Final LLM chunk_ids | `request_chunk_refs` table (graded_chunks rank+score) | Intermediate stages not in DB |
| Prompt filter drops | `prompt_build` step: dropped count | Dropped chunk_ids |

---

## 9. QUICK-START: ENABLING BACKWARD VERIFICATION TODAY (NO CODE CHANGE)

Without code changes, a partial backward verify is possible:

```bash
# 1. Check route taken (structured logs)
journalctl ... | jq 'select(.event == "stats_index_route" or .event == "retrieve_rrf_merged")'

# 2. Check first-10 retrieved chunks
journalctl ... | jq 'select(.event == "chunks_retrieved") | .chunks'

# 3. Check rerank: mode, before, after
journalctl ... | jq 'select(.event == "rerank_executed")'

# 4. Check final graded chunk_ids (DB)
SELECT record_chunk_id, rank, score
FROM request_chunk_refs
WHERE record_request_id = '<UUID>'
ORDER BY rank;

# 5. Enable per-chunk debug scores (top 5 only)
# Set env var DEBUG_RETRIEVAL=true  OR  pass state["debug_full"]=True in test harness
# → triggers retrieval_chunks_debug event at rerank.py:428
```

---

## 10. ARCHITECTURAL NOTES FOR FULL BACKWARD VERIFY

To achieve complete backward verification ("gold chunk: retrieved? reranked? survived topK? in LLM prompt?"), the following minimal additions are needed:

1. **`retrieve.py:1852`** — extend `chunks_retrieved` audit to include ALL chunk_ids (not just top 10), gated by a `debug_full` flag on state.

2. **`rerank.py` (after line 482)** — add audit event `rerank_survivors` with `[{"chunk_id": c.get("chunk_id"), "score": c.get("score")} for c in out]` when `state.get("debug_full")`.

3. **`grade.py` (within `grade_executed` audit at line 548)** — add per-chunk verdicts list `[(chunk_id, relevance) for c in graded]`.

4. **`callbacks.py:215` / `request_log_repository.py`** — persist ALSO `retrieved_chunks` (top-K candidates) and `reranked_chunks` (post-rerank) as separate columns or a parallel `request_stage_refs` table with a `stage` discriminator column.

5. **`generate.py:510–523` context-cap loop** — collect dropped chunk_ids into a list and emit `context_cap_dropped_chunk_ids` in `prompt_build` step metadata.

These changes are observability-only (no logic change) and preserve all sacred rules. Each can be gated behind `debug_full` state key for production cost control.
