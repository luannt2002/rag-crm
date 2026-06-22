# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thong-tu-09-2020-tt-nhnn | 10 | 5 | 1.00 | 0.80 | 0.00 | 0 | 0 | 0 | 12659 | 0.0121 |
| **MEAN** |  |  | **1.00** | **0.80** |  |  |  |  |  |

## Failures (layer-attributed)

- (none)
