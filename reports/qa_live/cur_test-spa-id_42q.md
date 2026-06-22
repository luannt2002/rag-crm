# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| test-spa-id | 18 | 10 | 1.00 | 0.90 | 0.00 | 0 | 0 | 0 | 13901 | 0.0232 |
| **MEAN** |  |  | **1.00** | **0.90** |  |  |  |  |  |

## Failures (layer-attributed)

- (none)
