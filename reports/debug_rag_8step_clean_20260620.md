# RAG 8-step debug workflow
bots: chinh-sach-xe, test-spa-id, thong-tu-09-2020-tt-nhnn · live: True

## STEP 1+2 — PARSE + CHUNK (structure)
  chinh-sach-xe                chunks=1954 table=1492 struct_path=0 children=1507 parents=262 avg_chars=456
  test-spa-id                  chunks=222 table=105 struct_path=0 children=163 parents=59 avg_chars=291
  thong-tu-09-2020-tt-nhnn     chunks=549 table=13 struct_path=136 children=475 parents=74 avg_chars=439
  → ✅ PASS (chunks exist + structured: table chunks for CSV, structural_path for hierarchical docs)

## STEP 3 — EMBED (leaf coverage)
  chinh-sach-xe                embedded=1692 null_leaf_BAD=0
  test-spa-id                  embedded=163 null_leaf_BAD=0
  thong-tu-09-2020-tt-nhnn     embedded=475 null_leaf_BAD=0
  → ✅ PASS (null_leaf must be 0 — a leaf with no vector is invisible; parents NULL is by-design small-to-big)

## STEP 4 — STORE (searchable surfaces)
  chinh-sach-xe                tsvector=1954/1954 stats_index=2953
  test-spa-id                  tsvector=222/222 stats_index=501
  thong-tu-09-2020-tt-nhnn     tsvector=549/549 stats_index=984
  knowledge_edges (KG): 0  (empty — KG not populated at ingest)
  → ⚠️  WARN (tsvector must be 100% for BM25; stats-index drives price/list; KG=0 means graph-retrieval is dormant)

## STEP 5-8 — RETRIEVE / GENERATE / GUARD / SCORE (live)
  chinh-sach-xe                COVERAGE=0.86 CHUNK_RECALL=0.14 HALLU=0.00 retr_miss=0 llm_miss=0
  test-spa-id                  COVERAGE=0.70 CHUNK_RECALL=0.20 HALLU=0.00 retr_miss=0 llm_miss=0
  thong-tu-09-2020-tt-nhnn     COVERAGE=1.00 CHUNK_RECALL=0.60 HALLU=0.00 retr_miss=0 llm_miss=0
  → STEP 5 RETRIEVE ⚠️  WARN · STEP 6 GENERATE ✅ PASS (COVERAGE mean 0.85) · STEP 7 GUARD ✅ PASS (HALLU max 0.00) · STEP 8 SCORE ✅ PASS

## OVERALL
  ⚠️  WARN — 7 steps checked · 0 FAIL · 2 WARN