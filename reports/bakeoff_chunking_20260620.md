# Chunking-strategy bake-off (live corpus)

Composite = Ekimetrics 5-metric lexical, uniform 0.2 weight, scored at a common 1024-char budget. RC is constant per doc (cancels in ranking). **oracle_best** = highest-composite strategy; **adaptive_pick** = what `select_strategy` chose.

## Per-document

| bot | doc | adaptive_pick (conf) | oracle_best | recursive | hdt | semantic | hybrid | proposition | gap |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| chinh-sach-xe | 60e8fe8f | table_csv (1.00) | ⚠️ hdt | 0.600 | **0.928** | 0.600 | 0.600 | 0.600 | 0.328 |
| chinh-sach-xe | 92e50a4c | recursive (0.73) | ⚠️ hdt | _0.554_ | **0.625** | 0.430 | 0.559 | 0.559 | 0.071 |
| chinh-sach-xe | 9dd883da | table_csv (1.00) | ⚠️ hdt | 0.680 | **0.698** | 0.676 | 0.680 | 0.680 | 0.018 |
| test-spa-id | 022fcaea | table_csv (1.00) | ⚠️ hdt | 0.513 | **0.673** | 0.513 | 0.513 | 0.513 | 0.160 |
| test-spa-id | 42b8d7f4 | table_csv (1.00) | ⚠️ recursive | **1.000** | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| test-spa-id | 64c8c90c | table_csv (1.00) | ⚠️ recursive | **0.800** | 0.800 | 0.800 | 0.800 | 0.800 | 0.000 |
| test-spa-id | c852544c | recursive (1.00) | ⚠️ hdt | _0.598_ | **0.758** | 0.473 | 0.596 | 0.603 | 0.160 |
| thong-tu-09-2020-tt-nhnn | c887d1f0 | hdt (1.00) | ⚠️ proposition | 0.617 | _0.624_ | 0.485 | 0.620 | **0.711** | 0.087 |

## Aggregate

- Documents: **8**
- Adaptive == oracle_best: **0/8** (0%)
- Mean composite — adaptive **0.671** · oracle ceiling **0.774** · recursive baseline **0.670**
- Selector headroom (oracle − adaptive): **0.103**
- Adaptive lift over recursive baseline: **+0.001**
