# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| test-spa-id | 8 | 6 | 0.33 | 0.33 | 0.00 | 4 | 0 | 0 | 5958 | 0.0079 |
| **MEAN** |  |  | **0.33** | **0.33** |  |  |  |  |  |

## Failures (layer-attributed)

- **test-spa-id/d01** (hoi_gia) → `RETRIEVAL_MISS` · expect=`60000` · chunks_used=1 · top_score=1.0 · retrieved_chars=4359
- **test-spa-id/d02** (hoi_gia) → `RETRIEVAL_MISS` · expect=`129000` · chunks_used=1 · top_score=1.0 · retrieved_chars=252
- **test-spa-id/d04** (hoi_gia) → `RETRIEVAL_MISS` · expect=`249000` · chunks_used=1 · top_score=1.0 · retrieved_chars=252
- **test-spa-id/d05** (liet_ke_dich_vu) → `RETRIEVAL_MISS` · expect=`199000` · chunks_used=1 · top_score=1.0 · retrieved_chars=252
