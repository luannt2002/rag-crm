---
description: Run the deterministic RAG load-test harness on the 3 demo bots, then agent-score every question (no LLM judge) and report answer-rate + failure-layer.
---

Run a full RAG load-test + agent-scored analysis. Do NOT use a ChatGPT/LLM judge — YOU (the Claude agent) read each question's evidence and score it.

## Steps

1. **Env**: `set -a && source .env && set +a`

2. **(optional) clean run**: if asked for a fresh run, clear DB+cache and re-upload:
   - `TRUNCATE document_chunks, documents RESTART IDENTITY CASCADE; DELETE FROM semantic_cache; TRUNCATE conversations CASCADE; DELETE FROM chat_histories;`
   - Redis `FLUSHDB`, restart `ragbot-py`, then `POST /api/ragbot/test/reinit-bots?bot=all&wipe=true` and wait until all docs `state='active'`.

3. **Run the harness** (full evidence per question → one JSON per bot):
   ```bash
   .venv/bin/python scripts/loadtest_qa_detail.py --stamp $(date +%Y%m%d) --concurrency 8
   ```
   Output: `reports/LOADTEST_<bot>_<stamp>.json` — each question carries golden, answer, top_chunks_retrieved (score+preview), answer_source_chunk, tokens, latency, intent, a deterministic prelim verdict + fail_step, and EMPTY `claude_verdict`/`claude_notes`.

4. **Agent-score EVERY question** (you read the JSON, fill the verdict):
   - For each question decide `claude_verdict`: ✅CHUẨN / 🟡GENERATION (chunk reached LLM but answer wrong/incomplete) / 🔴RETRIEVAL (answer chunk in corpus but not top-K) / 🟠HALLU (trap answered) / ⚪DATA (corpus-gap refuse-correct, or golden stale/ambiguous).
   - Distinguish a REAL bot failure from corpus-gap (refuse is correct) and golden-stale (bot answer defensible). Verify against the corpus via `psql` when unsure — do NOT guess (rule#0).
   - Write the scores back into the JSON and emit a consolidated `reports/QA_DETAIL_ALL_<stamp>.md`.

5. **Report**:
   - Per-bot: answer-rate (CHUẨN / total), HALLU count, p95 latency.
   - Global failure-layer rollup: how many are pipeline (chunk/retrieval/filter) vs LLM-generation vs corpus-gap vs golden.
   - **HALLU must be 0** — if any trap was answered, that is a blocker.
   - For each REAL failure, route it through the 3-tier fix (see `/rag-debug`): owner-config (A) / generic-pipeline (B) / test (C). Never per-bot code.

## What "good" looks like
- HALLU = 0/traps (sacred).
- 0 pipeline-layer failures (chunking/retrieval/filter solid).
- Remaining failures are LLM-generation (sysprompt-tunable) or corpus/golden — not code bugs.
