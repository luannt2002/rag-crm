# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 14 | 7 | 0.86 | 0.14 | 0.00 | 0 | 0 | 17667 | 0.0160 |
| test-spa-id | 18 | 10 | 0.80 | 0.20 | 0.00 | 0 | 0 | 8905 | 0.0216 |
| thong-tu-09-2020-tt-nhnn | 10 | 5 | 1.00 | 0.60 | 0.50 | 0 | 0 | 23013 | 0.0110 |
| **MEAN** |  |  | **0.89** | **0.31** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/q02** (liet_ke_san_pham) → `REFUSE_GAP` · expect=`CITYTRAXX` · chunks_used=1 · top_score=1.0 · retrieved_chars=0
- **test-spa-id/q06** (liet_ke_duoi_gia) → `WRONG` · expect=`129000` · chunks_used=1 · top_score=1.0 · retrieved_chars=0
- **test-spa-id/q13** (triet_long) → `REFUSE_GAP` · expect=`1199000` · chunks_used=1 · top_score=1.0 · retrieved_chars=0
- **thong-tu-09-2020-tt-nhnn/q10** (hallu_trap) → `HALLU_BREACH` · expect=`None` · chunks_used=0 · top_score=None · retrieved_chars=0
