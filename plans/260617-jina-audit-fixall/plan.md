# [T1/T2/T3] Jina migration audit — fix-all + re-test

> Date 2026-06-17. Driver: 5-agent audit (hardcode / domain-neutral / multi-lang /
> Jina-abstraction / adaptive-chunking) + RAG-Anything table study + spa load test.
> Strangler-fig: EVOLVE not REWRITE. Order = T1 (smartness) → T2 (cost/perf) → T3.

## Context (measured)
- ZE→Jina embed+rerank done (alembic 0228–0231, dim 1024, late_chunking). 5 nano-in-ingest paths OFF → ingest pure-Jina (legal 80 chunks/18s/0 nano).
- spa load test: 70% coverage, HALLU=0, **3/18 fail = price-TABLE questions** (CSV thô embed 0.18–0.42).
- adaptive-chunking adoption ~35–40%: live selector + L5 cross-check ON; Ekimetrics 5-metric path is DEAD code (`ekimetrics_enabled` never True). Decision: do NOT activate (T3, would re-burn tokens, doesn't fix the user-facing table miss).

## Issue register → fix order

### P0-1 [T1] Price-table retrieval weak  ← USER-FACING, highest
Root: CSV rows (`1,Mép,129000`) embed poorly for NL queries. Aligned with adaptive-chunking BI (block integrity) + RAG-Anything table-as-unit mindset.
Fix (RAG-Anything Technique ②, **0 LLM**): in `shared/chunking/csv_chunker.py`, render each row/table chunk as GFM markdown (`| STT | Tên dịch vụ | Giá |`) + prepend the section heading + expand prices (`129000`→`129.000đ`). Deterministic, no storm.
Gate: re-load-test spa, measure cosine on the 3 failing questions before/after (rule#0).

### P0-2 [T1] Jina cold-start corrupt — DONE
Fallback `DEFAULT_EMBEDDING_FALLBACK_MODEL/DIMENSION` → jina-embeddings-v3 / 1024; rerank fallback → jina; H2 jina-model constant. ✅ shipped this session.

### P1 [T1-EN] Multi-language EN production-readiness (5 language-gate leaks)
- P1-1 `pgvector_store.hybrid_search` calls `segment_vi_compounds()` with no language → EN BM25 broken. Add `language` param, pass through (internal gate already no-ops EN).
- P1-2 `structured_ref_extractor` VN regex (Điều/Khoản) for all bots → gate on language.
- P1-3 `math_lockdown.extract_numeric_claims` VND/NN-YYYY for all → add language param, gate VND/docref on `language in VI_DOMAIN_LANGUAGES`.
- P1-4 `superlative_context_enricher.parse_chunks` VN price/dur regex → gate or language-key.
- P1-5 `retrieve.py:661` hardcoded `"vi"` → `DEFAULT_LANGUAGE`.

### P2-1 [T2] Safe-by-default — flip 5 nano-ingest defaults to False
`DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED`, `DEFAULT_CR_ENHANCED_ENABLED`, `DEFAULT_NARRATE_THEN_EMBED_ENABLED`, `DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED`, enrichment(settings). Update the 2 assertion tests (test_default_cr_enhanced_enabled, test_narrate_then_embed) to the new intent. Late_chunking is the default; nano CR opt-in only.

### P2-2 [T3] Reranker layering
`orchestration/nodes/rerank.py` + `application/services/reranker_resolver.py` import concrete `NullReranker`/`build_reranker`. Expose `is_null` on RerankerPort; inject factory from bootstrap.

### P3 [T3] Hardcode debt (missing-import)
grounding `0.3` ×4, autocut `0.3` ×4, bm25_flags, CRAG partial `0.5`, heuristic confidence — declare/import constants.

### P4 [T3] Dead-code Ekimetrics — DECIDE later (delete vs wire). Defer.

## Verify gate
- `pytest tests/unit` green (fix any assertion-test fallout from P2-1).
- re-seed 3 bots via Jina, re-load-test spa: table-question cosine ↑, coverage ↑, HALLU=0.
</content>
