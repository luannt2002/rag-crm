# Deep-debug → expert protocol (per flow / per agent) · 2026-06-23

> Reusable protocol for taking ANY flow's code to **expert** (not merely "works").
> Distilled from the questions asked + work done on the **Ingest/Upload** flow (the reference run),
> so every subsequent flow-agent produces the same expert output. Runnable form:
> `.claude/workflows/deep-debug-to-expert.js`. Master fix-list: `plans/20260623-expert-remediation/plan.md`.

## What the Ingest run established (the template)

The user asked, in order, for each of these — and they are now the **fixed checklist** every flow-agent runs:
1. **Map**: which files does this flow touch? (list, grouped by layer)
2. **Understand**: what is each file's code actually doing? (the real flow, file:line)
3. **Per-file verdict**: CHUẨN (correct) / THIẾU (missing) / THỪA (redundant) / LỆCH (divergent) / LỖI (bug) + a **score /10**.
4. **Clean-code review**: SOLID, OOP, helper/util reuse, design-pattern (Strategy/Port/Registry/DI), separation.
5. **Comments**: are inline comments/docstrings standard? → rewrite to **full English**, strip version/temporal refs.
6. **Dead code**: any unused function → **comment it out + note it in a report file** (NEVER delete now), and unused
   functions go in their own report file. (Verify first — route handlers + Protocol methods are NOT dead.)
7. **Fix to expert**: which files are already expert (leave) vs need a fix; for each fix decide **used→fix /
   unused→comment-out**; do it with **TDD**; verify; re-score. No flow/logic rewrite unless the charter sanctions it.
8. **Honesty (rule#0)**: every claim has evidence file:line; label SỰ THẬT (runtime-verified) vs GIẢ THUYẾT
   (code-evidenced only); no "done/expert" without a test/measurement.

## The 5-axis per-file scorecard (every flow produces this)

| Axis | What to measure | Evidence |
|---|---|---|
| **Functional** | CHUẨN/THIẾU/THỪA/LỆCH/LỖI + score/10; correctness bugs | file:line, self-verified (adversarial) |
| **Comment/doc** | VN-comment count, version/temporal-ref count, docstring coverage | objective scan |
| **Clean-code/OOP/pattern** | SOLID, helper reuse, Strategy+Port+Registry+Null+DI, god-file (LOC/fn) | grep + read |
| **Dead-code** | functions with 0 callers — VERIFIED (not route handler / Protocol method) | grep callers + read decorators |
| **Perf/tech-debt** | hot-path latency, unbounded memory, gather-bugs, deferred splits | file:line |

## The 6-stage expert-fix loop (per flow)

1. **DIAGNOSE (read-only)** — produce the 5-axis scorecard + a fix-list with IDs, each: layer, used?/unused?,
   expert-fix, target-score, A/B metric. (This is what the Ingest agent did → `INGEST_FILE_BY_FILE_REVIEW.md`.)
2. **COMMENT-STANDARDIZE (comment-only)** — VN→EN + strip version/temporal refs + add docstrings. **Verify with an
   AST compare** (HEAD vs working, docstrings stripped → `ast.dump` equal): comment-only edits MUST be AST-identical.
   ⚠ Do NOT run `git stash`/`restore` in parallel agents sharing one working tree — it races and reverts other work.
   Either edit comment-files sequentially, or give each agent its **own git worktree** (`isolation: 'worktree'`).
3. **FIX-TO-EXPERT (TDD)** — for each fix: write the failing test FIRST → minimum code → green. "used→fix /
   genuinely-unused→comment-out + note in the flow report (don't delete)". Fix at the layer of the root cause.
4. **VERIFY (rule#0)** — per touched file: `pytest` subset green (0 regression) · ruff `HEAD == NOW` (0 new) ·
   for comment-only files AST-identical · for logic fixes a runtime test/measurement (HALLU=0, p95 ceiling).
5. **RE-SCORE + ARTIFACT** — update the flow-doc scorecard; record what changed in the session log; update the plan.
6. **GATE** — sacred 11/11 re-audit (no app-inject/override, domain-neutral, 4-key, zero-hardcode, narrow-except,
   no-version-ref, model-tier); branch + commit + push only on explicit user OK.

## Lessons baked in (from the Ingest run — do not repeat)
- **Verify "unused" before commenting out**: 3 naive 0-call-site hits were FastAPI route handlers + an OCRPort
  Protocol method — commenting them would break the app/interface. (`reports/INGEST_UNUSED_FUNCS_20260623.md`)
- **AST compare proves comment-only safety** — a reverted-to-HEAD file is also trivially AST-identical, so pair the
  AST check with a git-status check that the file is actually modified.
- **No parallel `git stash` across agents** — it caused 3 cleaned files to revert mid-run (shared working tree race).
  Use worktree isolation or sequential edits.
- **Bug already mitigated downstream may lower a fix's urgency** — e.g. the worker mis-route (A-I1) was already
  contained by the OCR sniff (A-I2); fix it for quality, not panic.
- **Unify duplicated helpers** — registry `_sniff_mime` and OCR `sniff_real_mime` were two sniffers; unify so one
  source of truth resolves OOXML subtypes everywhere (done in A-I1).

## Per-flow assignment (the 10 agents)
Each flow-agent runs the protocol above on its file-set. Flows + entry points:
1. **Ingest/Upload** — `interfaces/http/routes/documents.py` · `workers/document_worker.py` · `document_service/*` · `parser/*` · `ocr/*` · `shared/{mime_sniff,tabular_markdown}.py` (reference run; A-I2/A-I6/A-I1 done; A-I4/A-I5 pending).
2. **Chunking/AdapChunk** — `shared/chunking/*` · `infrastructure/{doc_profile,chunking_strategy,narrate,chunk_quality}/*`.
3. **Answer/Generation** — `orchestration/nodes/{generate,guard_output,critique_parser,persist}.py` · `system_prompts/*` · `guardrails/*`.
4. **Chat-entry + Test-chat** — `interfaces/http/routes/{chat,chat_async,chat_stream}.py` · `routes/test_chat/*`.
5. **Retrieval** — `orchestration/nodes/{retrieve,rerank,rrf_round_robin,mmr_dedup,grade}.py` · `infrastructure/{retrieval,reranker,hyde,query_router,metadata_filter,vector}/*`.
6. **Multi-tenant/RLS** — `infrastructure/db/{session,engine}.py` · `bot_registry_service.py` · `repositories/*` · `security/*`.
7. **Cost-Log/CRM** — `infrastructure/token_ledger/*` · `repositories/token_ledger_analytics_repository.py` · `routes/{admin_metrics,admin_analytics}.py` · `observability/*`.
8. **Domain-neutral sweep** — whole `src/ragbot` (brand/per-bot/version-ref scan).
9. **Multi-language** — `text_normalizer/*` · `tokenizer/*` · `i18n.py` · `narrate/llm_narrate.py` · `sysprompt_assembler.py`.
10. **Cost/Perf/Latency** — `query_graph.py` gating · `cache/*` · `proximity_cache/*` · `resilience/*` · gather usage.

Each agent's output = the 5-axis scorecard + the expert-fix diff (TDD-verified) + re-score, exactly like the Ingest run.
