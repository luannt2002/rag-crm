# [T1] Phase — KG-at-ingest: activate the dormant Knowledge Graph (RAG-Anything #1)

**Goal:** populate the empty knowledge graph → unlock multi-hop + cross-doc entity Q&A, the single highest-value capability ragbot has scaffolded but never run.

## Research finding (this session, evidence) — DORMANT, not absent
The whole KG pipeline is WIRED:
- Ingest: `_extract_graph_entities` (ingest_core.py:705) → `kg_service.extract_entities` (LLM) → `store_triples` → `knowledge_edges`. Called from ingest_stages_final.py:341 (background task, runs when `graph_rag_lazy_mode=false` — which IS the default).
- Retrieval: `resolve_kg_service(pipeline_config)` (chat pipeline.py:543) → the query graph's GraphRAG node (per-bot gated).
- Schema: `knowledge_edges` table EXISTS.

**Why KG is empty (0 rows, verified `SELECT count(*) FROM knowledge_edges = 0`):** the extraction early-returns at ingest_core.py:724 because **`system_config.graph_rag_default_mode = "disabled"`**. ONE switch. (`graph_rag_entity_extraction_model=""` → falls back to `llm_default_model`; `max_hops=2`, `max_triples_per_chunk=10` already set.)

## Implementation (fresh session — LLM-cost + A/B gated)

### Step 1 — flip the config (alembic, tracked; NOT psql)
`system_config.graph_rag_default_mode`: `"disabled"` → `"enabled"` (verify the exact non-disabled value the code expects — grep the mode handling). Optionally set `graph_rag_entity_extraction_model` to a cheap model (gpt-4.1-nano) to cap cost.

### Step 2 — populate KG WITHOUT a full re-ingest (avoid the OOM-prone path)
The OOM/W-O1 risk is in re-chunk+re-embed. KG extraction only needs the chunk TEXT (already stored). Write a **KG-backfill** that, per bot, reads existing `document_chunks.content` and runs `_extract_graph_entities` (extract→store_triples) on them — no re-chunk, no re-embed. Bounded concurrency (Semaphore) + per-key LLM limiter. Cost = LLM extraction per chunk (cap via cheap model + max_triples).

### Step 3 — enable KG-aware retrieval per-bot
Set the per-bot `pipeline_config` KG flag (the `resolve_kg_service` gate) so the query graph's GraphRAG node fires. Verify the graph node is wired (graph_assembly.py).

### Step 4 — A/B GATE (on B-2 harness, measure-first)
- Author ≥3 **multi-hop / cross-doc** golden queries per bot (the queries vector-only CANNOT answer — e.g. "X liên quan Y thế nào", entity-chains).
- Run `eval_rigor.py --compare` before/after KG-enable.
- **DONE only if:** multi-hop COVERAGE↑ (Wilcoxon p<0.05) AND HALLU=0 hold AND existing 42-q COVERAGE 1.00 no-regression.

## Risks
- LLM extraction cost (mitigate: cheap model + max_triples cap + per-key limiter).
- KG noise (bad triples) → retrieval pollution → measure HALLU=0 strictly.
- The OOM server (W-O1) — backfill is lighter than re-ingest but still LLM-load; run off-peak via `devstack.sh`, bounded concurrency.

## DONE definition
- [ ] `knowledge_edges` populated (>0, sane entities — spot-check)
- [ ] multi-hop query answerable (was refuse/wrong before)
- [ ] A/B p<0.05 multi-hop↑ · HALLU=0 · existing COVERAGE 1.00 hold
- [ ] config via alembic (tracked) · per-bot gated · domain-neutral

## Sequencing note
This is ACTIVATE (config + backfill + retrieval-enable), NOT a build — the same "dormant→measure→prove" pattern as B-1/Tier-1. After KG: chunking-activate (L4/L7) → multimodal (the one true build). Each gated on B-2 (rule #0).
