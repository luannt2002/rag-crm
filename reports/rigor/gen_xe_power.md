# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 40 | 40 | 0.85 | 0.07 | 0.00 | 4 | 2 | 0 | 3850 | 0.0440 |
| **MEAN** |  |  | **0.85** | **0.07** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/g002** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1485000` · chunks_used=1 · top_score=1.0 · retrieved_chars=364
- **chinh-sach-xe/g015** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2025122435548` · chunks_used=1 · top_score=1.0 · retrieved_chars=1795
- **chinh-sach-xe/g019** (gen_price_factoid) → `LLM_MISS` · expect=`1944000` · chunks_used=1 · top_score=1.0 · retrieved_chars=37717
- **chinh-sach-xe/g026** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2358516` · chunks_used=1 · top_score=1.0 · retrieved_chars=299
- **chinh-sach-xe/g031** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1998000` · chunks_used=1 · top_score=1.0 · retrieved_chars=234
- **chinh-sach-xe/g040** (gen_price_factoid) → `LLM_MISS` · expect=`1944000` · chunks_used=1 · top_score=1.0 · retrieved_chars=37717
