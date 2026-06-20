# Intrinsic chunk-quality scorecard (live corpus)

Ekimetrics 5-metric (RC/ICC/DCC/BI/SC), **lexical** impl (`ragbot.shared.intrinsic_metrics`). Composite = uniform 0.2 weight.

SC band scored against target = `DEFAULT_CHILD_CHUNK_SIZE` (256 chars). Values are 0–1 (×100 = %).

> CAVEAT: ICC/DCC/RC are lexical (Jaccard/regex), NOT the paper's embedder-cosine + coref. Use for ranking ragbot's own strategies, not for claiming parity with the published benchmark.

## Per-bot mean

| bot | docs | RC | ICC | DCC | BI | SC | **composite** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 3 | 1.000 | 0.369 | 0.319 | 0.355 | 0.957 | **0.600** |
| test-spa-id | 4 | 1.000 | 0.543 | 0.446 | 0.344 | 0.954 | **0.657** |
| thong-tu-09-2020-tt-nhnn | 1 | 1.000 | 0.029 | 0.307 | 0.528 | 0.879 | **0.549** |
| **ALL (mean)** | 8 | 1.000 | 0.314 | 0.357 | 0.409 | 0.930 | **0.602** |

## Paper Table-3 reference (embedder+coref impl — context only)

| Method | mean % |
| --- | ---: |
| Adaptive | 91.07 |
| LLM-regex | 89.80 |
| LangChain-recursive | 88.62 |
| Semantic | 76.49 |
| Sentence | 73.26 |

## Per-document detail

| bot | doc | leaves | parents | mean_chars | RC | ICC | DCC | BI | SC | composite |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 60e8fe8f | 14 | 1 | 305.5 | 1.000 | 1.000 | 0.271 | 0.071 | 1.000 | 0.669 |
| chinh-sach-xe | 92e50a4c | 14 | 6 | 322.1 | 1.000 | 0.054 | 0.363 | 0.357 | 1.000 | 0.555 |
| chinh-sach-xe | 9dd883da | 446 | 68 | 238.1 | 1.000 | 0.052 | 0.321 | 0.637 | 0.870 | 0.576 |
| test-spa-id | 022fcaea | 26 | 6 | 320.0 | 1.000 | 0.064 | 0.300 | 0.269 | 1.000 | 0.527 |
| test-spa-id | 42b8d7f4 | 4 | 1 | 395.0 | 1.000 | 1.000 | 0.520 | 0.250 | 1.000 | 0.754 |
| test-spa-id | 64c8c90c | 2 | 1 | 330.0 | 1.000 | 1.000 | 0.662 | 0.000 | 1.000 | 0.733 |
| test-spa-id | c852544c | 131 | 51 | 199.5 | 1.000 | 0.110 | 0.301 | 0.855 | 0.817 | 0.617 |
| thong-tu-09-2020-tt-nhnn | c887d1f0 | 489 | 87 | 242.2 | 1.000 | 0.029 | 0.307 | 0.528 | 0.879 | 0.549 |
