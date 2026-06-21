# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thong-tu-09-2020-tt-nhnn | 7 | 5 | 0.80 | 0.60 | 0.00 | 1 | 0 | 0 | 11614 | 0.0061 |
| **MEAN** |  |  | **0.80** | **0.60** |  |  |  |  |  |

## Failures (layer-attributed)

- **thong-tu-09-2020-tt-nhnn/d01** (hoi_nguong) → `RETRIEVAL_MISS` · expect=`cấp độ 4` · chunks_used=3 · top_score=0.197492 · retrieved_chars=1256
