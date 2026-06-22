# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 14 | 7 | 0.86 | 0.14 | 0.00 | 0 | 0 | 1 | 6090 | 0.0192 |
| test-spa-id | 18 | 10 | 0.90 | 0.20 | 0.00 | 0 | 0 | 1 | 10954 | 0.0210 |
| thong-tu-09-2020-tt-nhnn | 10 | 5 | 1.00 | 0.60 | 0.00 | 0 | 0 | 0 | 11524 | 0.0105 |
| **MEAN** |  |  | **0.92** | **0.31** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/q02** (liet_ke_san_pham) → `WRONG` · expect=`CITYTRAXX` · chunks_used=1 · top_score=1.0 · retrieved_chars=0
- **test-spa-id/q11** (khuyen_mai) → `WRONG` · expect=`Combo` · chunks_used=1 · top_score=1.0 · retrieved_chars=0
