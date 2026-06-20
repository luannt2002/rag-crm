# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 14 | 7 | 0.86 | 0.14 | 0.00 | 0 | 0 | 1 | 13080 | 0.0203 |
| **MEAN** |  |  | **0.86** | **0.14** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/q02** (liet_ke_san_pham) → `WRONG` · expect=`CITYTRAXX` · chunks_used=1 · top_score=1.0 · retrieved_chars=0
