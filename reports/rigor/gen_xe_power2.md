# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 40 | 40 | 0.95 | 0.07 | 0.00 | 2 | 0 | 0 | 5085 | 0.0480 |
| **MEAN** |  |  | **0.95** | **0.07** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/g015** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2025122435548` · chunks_used=1 · top_score=1.0 · retrieved_chars=1795
- **chinh-sach-xe/g026** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2358516` · chunks_used=1 · top_score=1.0 · retrieved_chars=299
