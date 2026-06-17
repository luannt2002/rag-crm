# 00 — INVENTORY · tài nguyên đọc được (Phase 0)

> Kiểm kê tĩnh tại anchor Phase 0. KHÔNG phân tích — chỉ liệt kê. Phân tích = Phase 1+.

## Repo scale
- git commits: **1639** · branch `fix-260604-action-slotmachine-dead-key`
- alembic: **233 migration**, head `0195_purge_lmstudio_grounding_grading_openai`
- src: **557 .py / 109,805 LOC** · tests: **674 test_*.py**
- plans: 27 mục · reports: 110 mục · docs/master: 16 sub-file (A–P)

## File lớn nhất (ưu tiên đọc / nguy cơ "agent đọc fail")
| LOC | File | Owner phase |
|---|---|---|
| 8087 | `src/ragbot/orchestration/query_graph.py` | P1-A (đang có plan split: `260609-query-graph-split`) |
| 5170 | `src/ragbot/interfaces/http/routes/test_chat.py` | P1-G |
| 4104 | `src/ragbot/application/services/document_service.py` | P1-B / P1-F |
| 3015 | `src/ragbot/shared/chunking.py` | P1-B (AdapChunk core) |
| 1841 | `src/ragbot/interfaces/workers/chat_worker.py` | P1-E |
| 1230 | `src/ragbot/application/services/model_resolver.py` | P1-E / P1-G |
| 993  | `src/ragbot/infrastructure/llm/dynamic_litellm_router.py` | P1-E |

## docs/master (16 file A–P)
01-A foundation · 02-B seven-layers · 03-C cross-axes · 04-D pipeline-orchestration ·
05-E cross-cutting · 06-F python-build-spec · 07-G legacy · 08-H enforcement ·
09-I kickoff · 10-J channel · 11-K pipeline-code-mapping · 12-L research-competitive ·
13-M roadmap-history · 14-N ingest-model-upgrade · 15-O anti-hallu-tuning · 16-P rago-schema

## constants package (22 module — resolve chain SSoT)
_00 app_env · _01 http_db_client · _02 per_intent_rerank_skip · _03 language_packs_db ·
_04 jwt_auth · _05 embedding_cb · _06 llm_defaults · _07 llm_sampling · _08 sentry_otel ·
_09 message_feedback · _10 rbac · _11 table_csv_chunking · _12 multi_stage_fallback ·
_13 adapchunk_layer1_ocr · _14 anti_abuse_ip_rate · _15 m2_neighbor_window ·
_16 prompt_token_squeeze · _17 pipeline_audit · _18 admin_analytics · _19 ekimetrics_selector ·
_20 cag_mode · _21 streaming_upload

## plans/ (27) — nguồn chính cho "plan mồ côi" (P1-G)
Gần nhất liên quan program: `260608-multitenant-hardening`, `260610-ga-hardening`,
`260608-path-to-9.5-expert`, `260609-query-graph-split`, `260609-prod-test-framework`,
`260604-expert-rag-action-architecture`, `260604-metadata-aware-v4`.
Backlog tổng: `260506-MASTER-BACKLOG.md` · `DEFERRED_STREAMS.md` · `ROADMAP_V2.md`.

## reports/ — eval gần nhất
- `GRADED_LATEST_20260610.txt` + `GRADED_SUMMARY.json` + 13× `GRADED_<bot>.json` (per-bot graded)
- `SECURITY_AUDIT_20260516/` · loạt `loadtest_LEGALBOT_ZE_v{1..6}` (lịch sử ZE migration)

## Pre-seed sẵn có
- `program/context/P1-C-PRESEED-multitenancy.md` — audit RLS/cache/worker/workspace/schema
  ĐÃ chạy (5 read-only agent, full file:line). Phase 1-C đọc cái này thay vì re-research từ 0.
