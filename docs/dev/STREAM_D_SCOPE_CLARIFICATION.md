# Stream D — RAGO Pareto Clarification

## Commit `ff89f59` — NOT Paper 26

Commit `ff89f59` subject claimed `feat(stream-d): RAGO Pareto — early exit`
but the actual diff is a **5-line early-exit** in `_retrieve_route()` that
skips rerank/MMR/grade/rewrite_retry when `retrieved_chunks` is empty.

This is a **subset optimization** — not Paper 26 RAGO Pareto-tune
(which calls for parallel intent fan-out across multi-query expansion,
retrieve, grade, and rewrite_retry, targeting p95 -55%).

## What Paper 26 actually requires

Paper 26 (RAGO — Retrieval-Augmented Generation Optimizer) proposes:
1. Identify parallel-able intents (factoid, comparison) vs sequential
   (multi_hop, aggregation)
2. Fan-out retrieve + grade per parallel intent concurrently
3. Merge via weighted RRF
4. Expected p95 latency drop: 27s → ≤14s

## What `ff89f59` actually does

- 5-line change in `_retrieve_route()`: `if not chunks: return "generate"`
- Saves 3-4 LLM/API calls on ~15-40% of turns (cache-miss empty retrieval)
- Expected p95 impact: -1-3s (not -55%)
- NOT harmful — refuse turns unnecessarily ran rerank/grade before

## Stream D proper still pending

Paper 26 RAGO Pareto-tune proper needs a focused 2-3 day session
implementing parallel intent fan-out across the multi-query → retrieve
→ grade → rewrite_retry path. This early-exit is a useful standalone
optimisation but does NOT close Stream D.

## Verification

Stream D early-exit MUST be verified with V14 90Q load test:
- HALLU=0 maintained
- Refuse rate ≤ V13 baseline
- If refuse rate increases > 5pp → revert `ff89f59`

See `STATE_SNAPSHOT.md` for V14 results.
