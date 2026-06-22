# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 22 | 22 | 0.36 | 0.14 | 0.00 | 11 | 3 | 0 | 3177 | 0.0319 |
| **MEAN** |  |  | **0.36** | **0.14** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/g001** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1485000` · chunks_used=1 · top_score=1.0 · retrieved_chars=364
- **chinh-sach-xe/g005** (gen_price_factoid) → `LLM_MISS` · expect=`1944000` · chunks_used=1 · top_score=1.0 · retrieved_chars=37717
- **chinh-sach-xe/g006** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`3240000` · chunks_used=1 · top_score=1.0 · retrieved_chars=420
- **chinh-sach-xe/g007** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1404000` · chunks_used=1 · top_score=1.0 · retrieved_chars=200
- **chinh-sach-xe/g008** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2358516` · chunks_used=1 · top_score=1.0 · retrieved_chars=2503
- **chinh-sach-xe/g010** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1998000` · chunks_used=1 · top_score=1.0 · retrieved_chars=234
- **chinh-sach-xe/g012** (gen_price_factoid) → `LLM_MISS` · expect=`1944000` · chunks_used=1 · top_score=1.0 · retrieved_chars=37717
- **chinh-sach-xe/g015** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`1152000` · chunks_used=1 · top_score=1.0 · retrieved_chars=1100
- **chinh-sach-xe/g016** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2357515` · chunks_used=1 · top_score=1.0 · retrieved_chars=5705
- **chinh-sach-xe/g017** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`810000` · chunks_used=1 · top_score=1.0 · retrieved_chars=448
- **chinh-sach-xe/g019** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2520000` · chunks_used=1 · top_score=1.0 · retrieved_chars=408
- **chinh-sach-xe/g020** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2857017` · chunks_used=1 · top_score=1.0 · retrieved_chars=3595
- **chinh-sach-xe/g021** (gen_price_factoid) → `LLM_MISS` · expect=`2856518` · chunks_used=1 · top_score=1.0 · retrieved_chars=10198
- **chinh-sach-xe/g022** (gen_price_factoid) → `RETRIEVAL_MISS` · expect=`2205000` · chunks_used=1 · top_score=1.0 · retrieved_chars=300
