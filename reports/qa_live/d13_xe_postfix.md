# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 9 | 7 | 0.86 | 0.14 | 0.00 | 1 | 0 | 0 | 6616 | 0.0122 |
| **MEAN** |  |  | **0.86** | **0.14** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/d04** (hoi_gia_theo_size) → `RETRIEVAL_MISS` · expect=`1044000` · chunks_used=1 · top_score=1.0 · retrieved_chars=5343
