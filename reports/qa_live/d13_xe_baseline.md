# End-to-end RAG scorecard + layer split (live)

Deterministic (no LLM judge). COVERAGE = answer⊇expect · CHUNK_RECALL = a retrieved chunk⊇expect · HALLU = trap answered (sacred=0). RETRIEVAL_MISS vs LLM_MISS pinpoints the failing layer.

| bot | Q | answerable | COVERAGE | CHUNK_RECALL | HALLU | retr_miss | llm_miss | unk_miss | p95ms | cost$ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 9 | 7 | 0.14 | 0.14 | 0.00 | 6 | 0 | 0 | 7062 | 0.0103 |
| **MEAN** |  |  | **0.14** | **0.14** |  |  |  |  |  |

## Failures (layer-attributed)

- **chinh-sach-xe/d01** (hoi_gia_theo_size) → `RETRIEVAL_MISS` · expect=`810000` · chunks_used=1 · top_score=1.0 · retrieved_chars=1687
- **chinh-sach-xe/d02** (hoi_gia_theo_size) → `RETRIEVAL_MISS` · expect=`963000` · chunks_used=1 · top_score=1.0 · retrieved_chars=957
- **chinh-sach-xe/d03** (hoi_gia_theo_size) → `RETRIEVAL_MISS` · expect=`999000` · chunks_used=1 · top_score=1.0 · retrieved_chars=4311
- **chinh-sach-xe/d04** (hoi_gia_theo_size) → `RETRIEVAL_MISS` · expect=`1044000` · chunks_used=1 · top_score=1.0 · retrieved_chars=4522
- **chinh-sach-xe/d05** (hoi_gia_theo_size) → `RETRIEVAL_MISS` · expect=`1098000` · chunks_used=1 · top_score=1.0 · retrieved_chars=6464
- **chinh-sach-xe/d07** (so_sanh_gia) → `RETRIEVAL_MISS` · expect=`810000` · chunks_used=1 · top_score=1.0 · retrieved_chars=1687
