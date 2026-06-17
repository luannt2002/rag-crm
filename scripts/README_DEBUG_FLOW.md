# Luồng DEBUG tái dùng — soi all step RAG (upload → LLM)

> Bộ script soi toàn pipeline, dùng lại bất cứ lúc nào. Output: `reports/debug_traces/`.
> Prep: `set -a && source .env && set +a` (app phải đang chạy cho phần query/eval).

## 1 LỆNH soi tất cả (khuyên dùng)
```bash
.venv/bin/python scripts/soi_all_steps.py                      # 3 bot, ingest detail
.venv/bin/python scripts/soi_all_steps.py test-spa-id --q "Laser Carbon giá?"  # +query trace
```
→ `SOI_<bot>.md`: raw→.md→strategy→**MỌI chunk đã cắt** (item/quality)→embed→query vector→23 step.

## Soi từng phần (chi tiết hơn)
| Script | Soi gì | Output |
|---|---|---|
| `soi_all_steps.py` | **TẤT CẢ step** (ingest + query) — 1 lệnh | `SOI_<bot>.md` |
| `debug_ingest_trace.py <bot...>` | raw→strategy→chunk→embed per doc | stdout |
| `log_upload_flow.py <bot>` | luồng upload 1 bot (format/cut/embed) | `UPLOAD_FLOW_<bot>.md` |
| `debug_query_trace.py --bot X --q "..." [--steps-latest]` | query embed+vector cosine + 23-step timing | stdout |
| `debug_workflow_3bot.py [topk]` | ingest+vector 3 bot × scenario → recall-miss | `DEBUG_<bot>.json` + `MASTER_ISSUES.md` |
| `debug_query_loadtest.py` | load-test thật (answer+chunk+token+latency+step) | `QUERY_FLOW_<bot>.json/.md` |
| `eval_gate.py [--coverage-floor 0.8]` | **eval thật** coverage+HALLU=0+p95 (no ChatGPT) | stdout (pass/fail gate) |
| `init_bots_from_urls.py [--wipe --apply]` | reproducible init từ https URL (FE→API→BE) | poll chunk |

## Luồng e2e đầy đủ (reset → upload → chunk → soi → eval)
```bash
# 1. wipe + re-init từ URL https (tests/scenarios/bot_sources.json)
.venv/bin/python scripts/init_bots_from_urls.py --wipe --apply
# 2. soi all step (chunk detail + query)
.venv/bin/python scripts/soi_all_steps.py --q "Laser Carbon giá?"
# 3. eval gate (coverage + HALLU=0)
.venv/bin/python scripts/eval_gate.py
```

## Config / scenario
- `tests/scenarios/<bot>_scenario.json` — câu hỏi golden (flow + expect + trap)
- `tests/scenarios/bot_sources.json` — URL https per bot (init)
- Chunk policy: `system_config.chunking_policy` (alembic 0208/0209 — dual_index)
- Latency config: alembic 0210 (structured-output off + grounding async)

## Pipeline steps (tham chiếu)
INGEST: upload → parse(.md) → analyze_document → select_strategy → smart_chunk
(dual_index/hdt/recursive) → orphan-merge → CR enrich → narrate → embed(zembed-1) → persist.
QUERY (23 step): guard_input → cache → understand → router → decompose → multi_query →
retrieve(hybrid dense+BM25) → rrf → rerank(zerank-2) → filter_cliff → mmr → grade →
generate(gpt-4.1-mini) → prompt_build → citations → guard_output → grounding → persist.
