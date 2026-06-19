# Flow Debug Tracefile Analysis — 2026-06-18

Deep-dive of the per-request trace/log plumbing to assess what exists for assembling a single human-readable per-test-run debug file. Read-only; no source edits.

---

## 1. Per-Request Correlation

### trace_id generation and propagation

**Definition:** `src/ragbot/config/logging.py:19`
```python
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
```

**HTTP path generation:** `src/ragbot/interfaces/http/middlewares/trace_context.py:22–31`
- `TraceContextMiddleware.dispatch()` reads `X-Trace-Id` header; if absent or fails regex `^[A-Za-z0-9_\-]{1,128}$`, generates `str(uuid4())`.
- Calls `bind_request_context(trace_id=trace_id)` → sets `trace_id_ctx` ContextVar AND structlog bound var.
- Echoes back in response header `X-Trace-Id`.
- `clear_request_context()` in `finally` block resets all contextvars.

**Chat worker path:** `src/ragbot/interfaces/workers/chat_worker/pipeline.py:97–106`
- `handle_chat_received()` calls `bind_request_context(trace_id=payload.get("trace_id", ""), ...)` at entry.
- `clear_request_context()` in `finally`.

**Document worker path:** `src/ragbot/interfaces/workers/document_worker.py:89–107`
- Same pattern: `bind_request_context(trace_id=payload.get("trace_id", ""))` at entry, `clear_request_context()` in `finally`.

**structlog injection:** `src/ragbot/config/logging.py:53–58`
```python
def _inject_trace_id(_: Any, __: str, event_dict: EventDict) -> EventDict:
    tid = trace_id_ctx.get()
    if tid:
        event_dict.setdefault("trace_id", tid)
    return event_dict
```
This processor fires on every structlog event — every log line automatically carries `trace_id` when set.

**Database persistence:** `trace_id` is stored in `request_logs.trace_id` (VARCHAR 128, `src/ragbot/infrastructure/db/models_monitoring.py:103`). Created at `RequestLogRepository.create_request_log()` line 79.

**Are ingest and query trace_ids linked?** NO — they are independent per-job. An ingest job has its own `trace_id` from `payload["trace_id"]`; a query turn has its own. There is no parent-child trace linking between the two flows at the structlog or DB level. The only bridge is `record_bot_id`.

---

## 2. request_steps Schema and add_step Capture

### Schema (`src/ragbot/infrastructure/db/models_monitoring.py:160–193`)

| Column | Type | Content |
|--------|------|---------|
| `id` | UUID PK | step row identifier |
| `record_request_id` | UUID FK → request_logs | correlation key |
| `record_tenant_id` | UUID | tenant scope |
| `workspace_id` | VARCHAR | workspace slug |
| `step_name` | VARCHAR(64) | node name (see below) |
| `step_order` | Integer | execution sequence |
| `model_used` | VARCHAR(128) | LLM model id (nullable) |
| `record_binding_id` | UUID | binding reference (nullable) |
| `started_at` | DateTime | server_default=now() on INSERT |
| `duration_ms` | Integer | wall-clock time for the step |
| `input_tokens` | Integer | prompt tokens (default 0) |
| `output_tokens` | Integer | completion tokens (default 0) |
| `cost_usd` | Numeric(12,6) | step cost |
| `status` | VARCHAR(16) | success / failed |
| `error` | Text | error message if failed |
| `metadata_json` | JSONB | free-form context dict |

### add_step / add_steps_batch callers

**`add_step()`** is called by `StepTracker` in non-batch mode (per-step INSERT):
`src/ragbot/application/services/step_tracker.py:197`

**`add_steps_batch()`** is called by `StepTracker.flush()` in batch mode (single round-trip):
`src/ragbot/application/services/step_tracker.py:219`

### Step names tracked (query graph)

All `state["step_tracker"].step(name)` call sites in orchestration nodes (file:line evidence):
- `guard_input` — `query_graph.py:1704`
- `cache_check` — `query_graph.py:1765`
- `understand_query` — `nodes/understand.py:126, 153`
- `condense_question` — `query_graph.py:1958`
- `router` — `query_graph.py:2311`
- `rewrite` — `query_graph.py:2336`
- `multi_query_fanout` — `nodes/retrieve.py:1239`, `query_graph.py:2514`
- `retrieve` — `nodes/retrieve.py:173`
- `retrieve_fallback` — `nodes/retrieve.py:1479`
- `rrf_fuse` — `nodes/retrieve.py:1370`
- `multistage_retrieval` — `nodes/retrieve.py:1548`
- `rerank` — `nodes/rerank.py:65`
- `filter_min_score` — `nodes/rerank.py:256, 306`
- `grade` — `nodes/grade.py:72`
- `decompose` — `query_graph.py:2727`
- `mmr_dedup` — `query_graph.py:3051`
- `neighbor_expand` — `query_graph.py:3129`
- `prompt_compression` — `nodes/generate.py:364`
- `litm_order` — `nodes/generate.py:421`
- `prompt_build` — `nodes/generate.py:441`
- `generate` — `nodes/generate.py:111`
- `citations_extract` — `nodes/generate.py:823`
- `grounding_check` — `nodes/guard_output.py:164`
- `guard_output` — `nodes/guard_output.py:59`
- `reflect` — `nodes/reflect.py:56`
- `persist` — `nodes/persist.py:131`

### Step names tracked (ingest, via `_phase_d_step` helper)

`src/ragbot/application/services/document_service/ingest_phases.py:173–260`
- `ingest_validate` — `ingest_core.py:277`
- `ingest_parse` — `ingest_core.py:315`
- Additional steps injected further downstream (chunking, embedding, store stages)

### What does metadata_json capture per step?

**retrieve node** (`nodes/retrieve.py:729`):
```
retrieve_top_k, intent_override_topk, candidates, source, fallback, metadata_filter_relaxed
```

**grade node** (`nodes/grade.py:101, 142, 254, 415, 540`):
```
relevant, irrelevant, ambiguous, retrieval_adequate, fallback_used
```

**generate node** (`nodes/generate.py:617`):
```
context_chars, history_msgs, context_chunks, compressed, context_cap,
context_chunks_dropped, context_chars_dropped, token_opt_enabled,
token_opt_dropped_by_score, token_opt_dropped_by_dedupe, first_token_ms
```

**rerank node** (`nodes/rerank.py:207–229`):
```
mode, before→after chunk count, top_score_active
```

**CRITICAL GAP — no raw payload in request_steps:** `metadata_json` captures counts, scores, timing flags, token counts — but NEVER stores the raw query text, raw chunk content, the full prompt sent to the LLM, or the LLM answer text. Those are hashed (`question_hash`, `answer_hash` in `request_logs`; `user_prompt_hash`, `response_hash` in `model_invocations`) — only SHA-256 digests, not the raw strings. The question text is absent from all persisted tables post-G15; only `question_hash` (SHA-256) survives in `request_logs.question_hash`.

---

## 3. Structlog Events

### JSON output configuration (`src/ragbot/config/logging.py:98–118`)

- **Renderer:** `structlog.processors.JSONRenderer()` when `json=True` (production default).
- **Sink:** `logging.StreamHandler(sys.stdout)` — stdout ONLY. No file handler is configured. JSON log lines go to stdout of the running process/container.
- **Filtering by trace_id:** YES — every log line includes `"trace_id": "<uuid>"` via the `_inject_trace_id` processor. A single run's events can be filtered with `grep '"trace_id":"<value>"'` or `jq 'select(.trace_id=="<value>")' <log>` from captured stdout.

### Event density per request (query turn)

Audit events fired via `_audit(state, event_name, data)` in orchestration:
- `query_graph.py:1766` — `cache_check` (miss/hit)
- `query_graph.py:1778–1836` — `cache_check` variants
- `query_graph.py:1900` — `cache_check`
- `nodes/understand.py:89, 247, 284` — `intent_extracted`, understand events
- `nodes/retrieve.py:1726, 1824, 1836` — hybrid_search, retrieve events
- `nodes/rerank.py:412, 480` — `rerank_executed`, safety_net
- `nodes/grade.py:423, 548` — `grade_executed`
- `nodes/generate.py:115, 236, 274, 414, 685` — `generate_started`, action events, pruning
- `nodes/persist.py:221` — `query_completed` (always fires)
- `query_graph.py:3083, 3145` — additional graph events

Estimated ~15–30 structlog events per query turn (including structlog `logger.info/debug` events from individual node functions). Each carries `trace_id`, `bot_id`, `record_tenant_id` in the structlog context.

### Is there a log file path?

**NO.** Log output is stdout-only (`src/ragbot/config/logging.py:113`). In a Docker/container environment, stdout is captured by the container runtime (Docker logging driver, journald, etc.). There is NO automatic file rotation, no per-request log file, no log file path configured. To get file-based logs, an operator would redirect stdout at the shell level or configure a logging driver.

---

## 4. Existing Debug/Replay Mechanisms

### `scripts/audit_logger_replay.py` — EXISTS AND IS PURPOSE-BUILT

`scripts/audit_logger_replay.py` (lines 1–12):
```
Replay a pipeline_audit JSONL into a human-readable per-request report.
Groups events by request_id (query stage) and renders an ordered
bullet trace + per-stage VERDICT (PASS / WARN / FAIL).
```
Usage: `python scripts/audit_logger_replay.py reports/pipeline_audit_<bot_id>_<date>.jsonl [--request <uuid>] [--ingest]`

**Limitation:** This tool ONLY works when `RAGBOT_PIPELINE_AUDIT_ENABLED=true` (env var). Default is `False` (`src/ragbot/shared/constants/_14_anti_abuse_ip_rate_limit_hon.py:73`). Without setting this env, no JSONL files are written, and the tool has nothing to replay.

### `scripts/debug_ingest_trace.py` — EXISTS, DB read only

`scripts/debug_ingest_trace.py` (line 1–17): reads stored chunks from `document_chunks` + reruns chunker analysis deterministically. Shows ingest-side decision trail (raw→chunks). Pure DB read, no live server needed.

### `scripts/debug_query_trace.py` and `debug_upload_steps.py` — EXIST

Both scripts exist in `scripts/`; they are DB query scripts for extracting step data, not a live pipeline hookup.

### `GET /admin/audit/messages/{message_id}` endpoint — EXISTS

`src/ragbot/interfaces/http/routes/admin_audit.py:38–53`:
```python
@router.get("/audit/messages/{message_id}")
async def audit_message(request: Request, message_id: int) -> dict:
    data = await logger.fetch_by_message_id(message_id, record_tenant_id=record_tenant)
    return {"ok": True, "data": data}
```
This endpoint returns `{request_logs, request_steps, model_invocations}` for a given `message_id`. It is a natural assembly point for DB-side data. RBAC: requires `admin:audit_message_read` permission.

### `InvocationLogger.fetch_by_message_id()` — EXISTS

`src/ragbot/infrastructure/observability/invocation_logger.py:279–339`:
Joins `request_logs` + `request_steps` + `model_invocations` by `(message_id, record_tenant_id)`. Returns all three as a dict of lists. This is the closest existing "per-request assembly" code in the DB layer.

### `GET /admin/documents/{id}/debug-view` — EXISTS (ingest side)

`src/ragbot/interfaces/http/routes/admin_documents_debug.py`: Returns parsed representation of a document. Useful for ingest debugging. Requires admin level.

### Replay/debug mode flag — NOT FOUND

There is NO `debug_mode: bool` flag on `GraphState`, no per-request dump-to-file mechanism, no `RAGBOT_DEBUG_REQUEST=true` toggle that would auto-capture the full pipeline trace to a file. The pipeline audit logger writes per-bot JSONL but is opt-in and does NOT capture every step — it only captures the events explicitly sent via `_audit()`.

### `verify_fixes_loadtest.py` — NO trace capture

`scripts/verify_fixes_loadtest.py` fires HTTP requests and prints `answer[:150]` + token/latency summary. It captures: `answer`, `chunks_used`, `tokens.completion`, `tokens.prompt`, `latency_ms`, `http_status`. It does NOT capture: `trace_id`, `request_id`, chunk content, step breakdown, grounding details. Each test case result is transient — printed to stdout, not saved to a file per case.

---

## 5. Assembly Point

### Natural convergence point (query path)

The `persist` node at `src/ragbot/orchestration/nodes/persist.py:131–244` is the canonical terminal node of the query graph. It:
1. Calls `_audit(state, "query_completed", {...})` — fires the terminal JSONL event (if audit enabled).
2. Returns `_persist_meta` with `context_chars` and `context_chunks` into LangGraph state.
3. Schedules the semantic cache write as fire-and-forget background task.

After `persist`, the pipeline calls `finalize_request_log()` (in the chat worker, `pipeline.py:~650`) which writes the terminal `request_logs` row with: `answer_hash`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `model_name`, `citations`, `retrieved_chunks` (→ `request_chunk_refs`), `status`.

**The convergence point for all per-request data is:**
- DB: `request_logs` row (after `finalize_request_log()`)
- DB: `request_steps` rows (after `StepTracker.flush()`)
- DB: `model_invocations` rows (written by `InvocationLogger` inline during each LLM call)
- DB: `request_chunk_refs` rows (written during `finalize_request_log()`)
- JSONL (if enabled): `reports/pipeline_audit_<bot_id>_<YYYYMMDD>.jsonl`
- stdout: structlog JSON lines (filterable by `trace_id`)

All four DB tables are joinable via `request_id` (UUID, generated per-turn). The `InvocationLogger.fetch_by_message_id()` already assembles three of the four tables.

### Assembly for ingest

Ingest has its own `StepTracker` with `kind="ingest"` (`document_worker.py:194–203`), its own `request_logs` row (`connect_id="ingest"`), and step rows tagged `metadata_json.step_kind="ingest"`. The JSONL audit captures `ingest_started` and stage events via `_audit.log()` in `ingest_core.py:360`, `ingest_stages.py:791`, `ingest_stages_store.py:484`, `ingest_stages_final.py:351`.

---

## 6. Gap Analysis

### What exists (reusable)

| Artifact | Coverage | Where |
|----------|----------|-------|
| `request_logs` row | 1 row/request: timing, tokens, cost, status, `trace_id`, `question_hash`, `answer_hash`, model, citations | DB `request_logs` |
| `request_steps` rows | ~26 named step rows/query turn: name, order, duration_ms, tokens, cost, metadata (counts/scores, NO raw text) | DB `request_steps` |
| `model_invocations` rows | 1 row/LLM call: purpose, model, tokens, cost, timing, `user_prompt_hash`, `response_hash` (NO raw text) | DB `model_invocations` |
| `request_chunk_refs` rows | (chunk_id, rank, score) per retrieved chunk — NO content | DB `request_chunk_refs` |
| `audit_logger_replay.py` | Reads JSONL → human report grouped by request_id with PASS/WARN/FAIL verdicts | `scripts/audit_logger_replay.py` |
| `GET /admin/audit/messages/{id}` | HTTP endpoint that assembles logs+steps+invocations for a message_id | `routes/admin_audit.py:38` |
| `InvocationLogger.fetch_by_message_id()` | Async method joining 3 tables | `invocation_logger.py:279` |
| `PipelineAuditLogger` JSONL | Per-bot per-day JSONL with ~15 named events (query path) + 4 events (ingest) | `pipeline_audit_logger.py` (opt-in) |
| `debug_ingest_trace.py` | Re-runs chunker on stored DB chunks — ingest side | `scripts/debug_ingest_trace.py` |

### What is MISSING for a "per-run debug file"

| Missing | Blocker type | Detail |
|---------|-------------|--------|
| **Raw question text** | CAPTURE — raw text never stored | `request_logs.question_hash` is SHA-256 only; question text not persisted post-G15. Must be captured at the load-test harness level or by querying `messages` table (if upstream service stores it). |
| **Raw answer text** | CAPTURE — answer not stored | `request_logs.answer_hash` is SHA-256 only. Raw answer text is neither in `request_steps.metadata_json` nor any persisted table. The harness must capture `response.answer` from the HTTP response. |
| **Chunk content at retrieval time** | CAPTURE — content not in step rows | `request_chunk_refs` has `(chunk_id, rank, score)` only. To see what text was retrieved, you must JOIN `document_chunks.content` by chunk_id at report time. Possible if chunks not deleted, but not automatic. |
| **Full LLM prompt text** | CAPTURE — prompt hashed only | `model_invocations.user_prompt_hash` is SHA-256. Full prompt text is not stored anywhere. |
| **Full LLM response text** | CAPTURE — response hashed only | `model_invocations.response_hash` is SHA-256. Full response text is not stored anywhere. |
| **pipeline_audit JSONL enabled** | ASSEMBLY — off by default | `DEFAULT_PIPELINE_AUDIT_LOGGER_ENABLED=False`. Without `RAGBOT_PIPELINE_AUDIT_ENABLED=true`, no JSONL files are written, and `audit_logger_replay.py` has nothing to read. |
| **Per-test-case trace_id linkage** | ASSEMBLY — load-test harness | The load-test harness (`verify_fixes_loadtest.py`) does NOT record the `trace_id` from the response header `X-Trace-Id`. Without capturing it, stdout logs cannot be correlated to a test case. |
| **Ingest↔query cross-run linkage** | ASSEMBLY — no parent trace | There is no shared parent trace_id between an ingest run and a subsequent query run. The only linkage is `record_bot_id`. To build "upload doc → query" in one file requires explicit orchestration in the test harness. |
| **Stdout log capture per run** | ASSEMBLY — no file sink | Logs go to stdout only. A test harness must redirect/capture stdout during the test window to filter events by `trace_id`. |

### Ingest-side step names (Phase D steps)

`ingest_validate`, `ingest_parse`, plus steps added by `_phase_d_step()` at downstream stages (chunking `U3`, embedding `U4-U5`, store `U6`, finalize `U7`). Exact names depend on `ingest_phases.py` step name constants — `src/ragbot/application/services/document_service/ingest_phases.py:149` defines them as Python constants, not magic strings.

---

## 7. Build vs Reuse Verdict

### What can be assembled from existing plumbing (NO new code)

1. **Enable `RAGBOT_PIPELINE_AUDIT_ENABLED=true`** → JSONL written to `reports/pipeline_audit_<bot_id>_<date>.jsonl`.
2. **Run `scripts/audit_logger_replay.py reports/pipeline_audit_*.jsonl --request <uuid>`** → human-readable per-request bullet trace with PASS/WARN/FAIL verdicts for retrieve/grade/generate/complete stages. This is the closest existing tool to "per-run debug file."
3. **Call `GET /admin/audit/messages/{message_id}`** → JSON with all `request_logs`, `request_steps` (with `metadata_json` counts/scores/flags), and `model_invocations` (token counts, costs, timing). Can be formatted into a per-case report.

### What new capture is required

To produce a file like:
```
RUN test_case_1:
  [INGEST: doc→chunks]
  - chunk count=N, strategy=X, avg_chars=Y
  [QUERY: route→candidates→topK→prompt→answer→grounding]
  - question: "raw text here"
  - intent: list, chunks retrieved=8, top_score=0.82
  - generate: context_chars=3200, model=X, tokens=450/120
  - answer: "raw answer here"
  - grounding: PASS
```

**New capture needed:**
1. **Harness must capture `trace_id` from `X-Trace-Id` response header** — add `trace_id = response.headers.get("X-Trace-Id")` to `_ask()` in `verify_fixes_loadtest.py`.
2. **Harness must record raw question text and raw answer text** — already captures `answer` text; add as a per-case field.
3. **Chunk content JOIN** — add a post-run DB query: `SELECT dc.content FROM request_chunk_refs rcr JOIN document_chunks dc ON rcr.record_chunk_id = dc.id WHERE rcr.record_request_id = :request_id ORDER BY rcr.rank` to fetch actual chunk texts for the run.
4. **Cross-flow linkage** — the test harness must record both the ingest `trace_id` (from the ingest response) and the query `trace_id`, then stitch them by `bot_id` in the final report.
5. **JSONL audit must be enabled** — `export RAGBOT_PIPELINE_AUDIT_ENABLED=true` before running the test.

### Minimal viable approach (reuse-heavy)

The lowest-effort path to a per-test-run debug file:

1. Set `RAGBOT_PIPELINE_AUDIT_ENABLED=true`.
2. Extend `verify_fixes_loadtest.py._ask()` to:
   a. Capture `trace_id` from `X-Trace-Id` response header.
   b. After all cases complete, dump per-case JSON to `reports/run_<timestamp>.json` including: `{case_idx, bot, q, kind, answer, trace_id, ms, chunks, tokens}`.
3. After the run, call `GET /admin/audit/messages/{message_id}` per case to get the DB step breakdown (join by `message_id` which is visible in the HTTP response as needed, or look up by `trace_id` via `request_logs` query).
4. Use `audit_logger_replay.py` filtered by `request_id` (from the JSONL) for the orchestration event trace.

**Blocker analysis:**
- The **primary blocker is CAPTURE** (raw text missing from DB), not assembly. The DB step machinery is comprehensive but all text is hashed. Raw answer text is available at the harness level from the HTTP response — just not persisted automatically.
- The secondary blocker is **opt-in** for JSONL audit. Without enabling it, the `audit_logger_replay.py` tool cannot function.

---

## Summary Table

| Point | Status |
|-------|--------|
| trace_id correlation (HTTP) | FULL — ContextVar + structlog bind, echoed in response header |
| trace_id correlation (workers) | FULL — bound at worker entry, stored in `request_logs.trace_id` |
| Ingest↔query trace linkage | NOT EXISTS — independent trace_ids, no parent-child bridge |
| request_steps schema | FULL — 27 named steps, duration/tokens/cost/metadata_json |
| Payload capture in steps | MISSING — metadata_json has counts/scores only; no raw text |
| Structlog events | FULL — JSON to stdout only; filterable by trace_id |
| Log file path | NOT EXISTS — stdout only, no file sink |
| Pipeline audit JSONL | EXISTS but OPT-IN (default OFF) |
| audit_logger_replay.py | EXISTS — per-request human report from JSONL |
| /admin/audit/messages endpoint | EXISTS — assembles 3 DB tables by message_id |
| Per-request full dump (debug mode) | NOT EXISTS — no auto dump-to-file mechanism |
| Replay harness for pipeline | NOT EXISTS — only document recovery replay (re-queue failed ingest jobs) |
| Load-test trace_id capture | MISSING in verify_fixes_loadtest.py |

---

*Report written by Claude Sonnet 4.6 — read-only deep-dive, no source edits.*
