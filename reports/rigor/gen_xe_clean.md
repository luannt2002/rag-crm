# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 25 | 25 | 0.52 | 0.08 | 0.00 | 11 | 1 | 0 | 3972 | 0.0311 |
| **MEAN** |  |  | **0.52** | **0.08** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/g001** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1485000` · chunks_used=1 · top_score=1.0 · retrieved_chars=364
- **chinh-sach-xe/g008** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`3240000` · chunks_used=1 · top_score=1.0 · retrieved_chars=420
- **chinh-sach-xe/g009** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1404000` · chunks_used=1 · top_score=1.0 · retrieved_chars=200
- **chinh-sach-xe/g010** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2358516` · chunks_used=1 · top_score=1.0 · retrieved_chars=299
- **chinh-sach-xe/g012** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1998000` · chunks_used=1 · top_score=1.0 · retrieved_chars=234
- **chinh-sach-xe/g014** (gen_price_factoid) → `LLM_MISS` · expect=`1944000` · chunks_used=1 · top_score=1.0 · retrieved_chars=37717
- **chinh-sach-xe/g017** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1152000` · chunks_used=1 · top_score=1.0 · retrieved_chars=1100
- **chinh-sach-xe/g018** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2357515` · chunks_used=1 · top_score=1.0 · retrieved_chars=416
- **chinh-sach-xe/g021** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`19000` · chunks_used=1 · top_score=1.0 · retrieved_chars=1795
- **chinh-sach-xe/g022** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2520000` · chunks_used=1 · top_score=1.0 · retrieved_chars=408
- **chinh-sach-xe/g023** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2857017` · chunks_used=1 · top_score=1.0 · retrieved_chars=1397
- **chinh-sach-xe/g024** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2856518` · chunks_used=1 · top_score=1.0 · retrieved_chars=1514
