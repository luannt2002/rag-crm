# All-Flows Reliability Audit — 2026-07-10

> Scope: end-to-end audit of the three production flows (query/chat, async-callback
> delivery, document ingest) triggered by a live 60-question load test that exposed
> systemic answer corruption under concurrent load.
>
> Method: (1) live load test on the audited bot, graded from the server's own
> `completion_tokens`; (2) three read-only flow maps (query graph, async worker +
> callback, ingest pipeline) with `file:line` anchors; (3) spot-verification of the
> highest-impact findings by reading the code directly.
>
> Domain-neutral note: the deployment's LLM provider name is redacted to
> `<llm-provider>` per the reports placeholder policy. Findings are
> provider-agnostic — they concern how the app handles ANY provider that returns a
> partial or failed response.

---

## 1. Executive summary

**The one-line finding:** the platform is built to *degrade gracefully* when a
provider fails — but for the **chat answer**, "graceful degradation" means a
**broken/partial answer is accepted as success and delivered to the consumer as
if complete**. The same "provider fails/partial → accepted silently" pattern
recurs across all three flows; only in ingest is the degrade actually safe.

**Measured trigger (load test, 60 questions, concurrency 8):**

| Bucket (graded by server `completion_tokens`) | Count | % |
|---|---|---|
| 🟢 Complete answer | 35/60 | 58% |
| 🔴 Truncated mid-generation (accepted as "answered") | ~20/60 | ~33% |
| ⚫ Failed `503` upstream-unavailable (masked as empty) | 5/60 | 8% |
| **Provider-corrupted total** | **~25/60** | **~42%** |

- The load test originally reported **"0 errors"** — this was **false**; the 503s
  were recorded as empty content-misses, not errors.
- **HALLU = 0 held** (no fabricated numbers) — because the numeric-fidelity gate is
  **deterministic** and does not depend on the LLM provider (see §5).
- On the **clean subset** (answers the provider actually delivered in full),
  coverage was **16/18 ≈ 89%** — i.e. the **RAG itself is not the problem; provider
  reliability under load is.**

### 1b. Performance (same load test, per-node median ms @ concurrency 8)

| Node | median | p95 | Where |
|---|---|---|---|
| adaptive_decompose | 61.8s | 71s | LLM |
| grounding_check | 30.0s | 30s (cap) | LLM |
| generate | 19.3s | 43s | LLM |
| understand_query | 18.9s | 43s | LLM |
| rewrite | 10.0s | 10s | LLM |
| grade | 2.0s | 2s | LLM |
| rerank | 1.5s | 1.8s | **code** |
| retrieve | 22ms | 75ms | **code** |
| mmr_dedup / persist / rrf / prompt_build | ≤6ms | — | **code** |

Wall-clock p50 ≈ **31s** under concurrency 8. **Our code is fast** (retrieve
22ms, rerank 1.5s, everything else sub-second); the entire latency budget is the
LLM provider's slow, concurrency-degraded responses (~90% provider / ~10% code).
This corroborates the 2026-07-08 per-step measurement. **Performance work =
reduce the number/wait of LLM calls** (cache understand, async grounding, fewer
retries, a fast fallback) — *not* code optimisation.

---

## 2. The systemic pattern

The LLM provider is invoked **~10 times per question**:

```
understand → router → complexity → decompose → rewrite → grade
→ generate → guard_output(grounding) → reflect → HyDE → async-judge
```

Each is a failure point. With ~42% of provider responses corrupted under load,
failures compound across nodes. Critically, several nodes **accept a partial/failed
provider result as success** rather than surfacing it — so a provider hiccup becomes
a silently-wrong answer.

---

## 3. Flow-by-flow findings

### 3.1 Query / chat pipeline (severity: 🔴 HIGH — answer reaches the user)

Node order (`orchestration/query_graph.py:2902-3028`):
`guard_input → cache+understand → complexity → decompose/rewrite → retrieve →
graph_retrieve → rerank → mmr_dedup → neighbor_expand → grade → generate →
critique_parse → guard_output → reflect → persist`.

**Fail-open / partial-accepted (highest priority):**

| Finding | Location | Effect |
|---|---|---|
| Answer LLM `finish_reason` captured but **never checked** for `"length"`/truncation | `nodes/generate.py:814,864` (used only at `:896`) | Truncated answer returned as a normal success |
| Grounding judge exception → `grounding_hit=None` → **fail-OPEN** | `nodes/guard_output.py:702-718` | Answer shipped **unverified** when the grounding LLM fails; only a WARNING |
| Grade LLM timeout → all chunks `ambiguous` + `retrieval_adequate=True` | `nodes/grade.py:248-269` | Degraded retrieval pool accepted as adequate |
| Empty LLM answer returned verbatim | `nodes/generate.py:960-969` | User gets empty; only `generate_empty_answer` WARNING, no failure status |

**Quiet degrade (swallowed below WARNING):**
- `nodes/condense_question.py:105-108` — LLM failure logged at **DEBUG** only.
- `nodes/decompose.py:93-96` — LLM failure logged at **DEBUG** only.
- `nodes/guard_input.py:31-35` — language-pack lookup failure swallowed with **no log**.

**Good (fails loud — do not touch):**
- `retrieve` error → `retrieval_degraded=True` + `logger.error(exc_info=True)`
  (`query_graph.py:539-556`, `nodes/retrieve.py:1515`) — distinguishes error-empty
  from genuine no-match.
- `guard_output.py:498-663` — grounding **fail-CLOSED** (OOS template) when the LLM
  runtime is unwired (config-time), per-bot `grounding_failure_mode`.

### 3.2 Async-callback delivery (severity: 🔴 HIGH — the real production path)

Path: `POST /chat` → transactional outbox (`chat.received.v1`) → `chat_worker`
→ query graph → **webhook callback** to the consumer.

| Finding | Location | Effect |
|---|---|---|
| Truncated/empty answer delivered as `ok:True, status:"success", answer_type:"answered"` | `chat_worker/callbacks.py:294-309`; persisted `status="success"` at `:231-240` | **The ~42% corrupted answers reach real consumers as successful answers** |
| Callback delivery: 3 inline retries only, then `delivery_failed`; **message still ACKed → webhook never retried** | `infrastructure/delivery/callback_delivery.py:123-150`; job flip `callbacks.py:311-315` | If the consumer URL is down during the 3 attempts, **the answer is lost** (at-most-once webhook) |
| Chat handler uses the legacy `_mark_processed` path (not `inbox_tx` atomic exactly-once); user-message + request_log creates are **not idempotent** | `chat_worker/pipeline.py:97`; bus `redis_streams_bus.py:447-452` | A redelivery (crash before ACK) creates **duplicate messages/rows** |
| Broad `except Exception` turns any pipeline error into a "failure callback" + ACKed message | `chat_worker/pipeline.py:655-657` | Errors consumed, not redelivered |

**Good (do not touch):** transactional outbox; **XACK only after the inbox row
commits** (`redis_streams_bus.py:449-455`); pending-message recovery loop + DLQ
(`:572,:644`).

### 3.3 Document ingest (severity: 🟠 MEDIUM — degrade is safe here)

Path: `POST /documents/create` → outbox → `document_worker` (fetch + type-detect
+ parse) → `DocumentService.ingest()` U1–U7 → finalize (state flip + stats).

| Finding | Location | Effect |
|---|---|---|
| **DRAFT stranding**: a crash in U3–U6 flips only the *job* to failed; `documents.state` stays `DRAFT` with 0 chunks | `document_worker.py:745`; no doc-state flip in U3–U6 | Half-ingested doc, invisible failure |
| Finalize state-flip broad-swallow → chunks stored but state never leaves `DRAFT` | `ingest_stages_final.py:361` | Doc has chunks but stays invisible/served-as-draft |
| Stats delete-before-insert broad-swallow → skips the insert on delete failure | `ingest_stages_final.py:563` | Stats index silently stale |
| Chunk coverage gates (`find_dropped_numbers`, `check_chunk_gaps`) are **observe-only** | `ingest_stages.py:869,890` | Dropped source numbers/prose only logged — root of the ADR-0008 sparse-drop class |
| Enrichment (U5) partial-LLM-accepted (systemic across CR / chunk-context / legacy) → degrades to raw text | `ingest_stages_enrich.py:371-372`; `contextual_chunk_enrichment.py:234,243`; `llm_chunk_context_provider.py:120`; `chunk_context_enricher.py:251` | **Intentional, low severity** — storage-only, default OFF, HALLU-safe; raw text still usable |

**Good (do not touch):** **embedding is fail-loud** — an embed error or
length-mismatch sets `state='failed'` + soft-deletes + **re-raises**; **no
NULL-embedding rows are ever stored** (`ingest_stages_store.py:477,521,501-513`;
`__init__.py:526`). This is the correct pattern the chat flow lacks.

---

## 4. Cross-flow "fail-open / partial-accepted" summary

| # | Location | What is accepted silently | Consumer-visible? |
|---|---|---|---|
| 1 | `generate.py:814/896` | Truncated answer (no `finish_reason` check) | ✅ yes |
| 2 | `guard_output.py:702-718` | Unverified answer (grounding LLM failed) | ✅ yes |
| 3 | `grade.py:248-269` | Inadequate retrieval pool marked adequate | indirect |
| 4 | `generate.py:960-969` | Empty answer verbatim | ✅ yes |
| 5 | `callbacks.py:294-309` | Broken answer delivered as `ok:True/success` | ✅ yes |
| 6 | ingest U5 (multiple) | Un-enriched raw chunk | ❌ safe |

---

## 5. Why HALLU stayed 0 (the one robust guard)

The **numeric-fidelity gate is deterministic** — it checks that every number in an
answer exists in the served context / stats DB, independent of any LLM. It therefore
keeps working even when the LLM grounding judge (§3.1 #2) fails open. This is why
fabricated *numbers* stayed at zero across the run. The LLM grounding covers
*non-numeric* faithfulness, and that is the part which silently degrades under
provider failure.

---

## 6. Prioritized fix plan

Each fix: red-test-first, measure before/after, **one change at a time**, never two
simultaneously (so improvement is attributable).

**P0 — Answer integrity (consumer-facing):**
1. `generate.py`: validate `finish_reason` — a truncated/incomplete completion is a
   **retryable failure**, not a success. Single fix that closes the truncation hole
   for both the query flow and the callback delivery. *(covers ~33% truncated)*
2. `callbacks.py` / worker: do **not** deliver a truncated/empty answer as
   `ok:True` — mark it a failure and retry or surface it.
3. `guard_output.py:702`: decide grounding fail-open vs fail-closed — do not ship an
   answer labelled verified when the grounding judge did not run.

**P1 — Provider resilience:**
4. Lower the provider concurrency cap (`ai_providers.max_concurrent`, currently 16 →
   ~4–6; **value must be measured**) + retry-on-truncation + configure a fallback
   LLM binding (the failover chain exists at `dynamic_litellm_router.py:605` but the
   audited bot has only one binding). *(attacks the ~42% at the source)*
5. Callback delivery: dead-letter / re-enqueue on retry exhaustion so a down
   consumer does not lose the answer.

**P2 — Correctness / robustness:**
6. Chat handler: adopt `inbox_tx` so a redelivery cannot create duplicate
   messages/rows.
7. Ingest: flip `state='failed'` on a mid-pipeline crash (end DRAFT stranding).

---

## 7. Honest caveats (rule#0)

**Self-corrections made during this audit** (hypotheses stated as fact, then caught):
- An earlier "18/60 answers truncated" figure used an ends-mid-word heuristic that
  **false-positived** on complete Vietnamese answers ending in a bare word. Retracted;
  re-measured from the server's own `completion_tokens` (§1).
- "Concurrency causes truncation" — a same-question conc-1 vs conc-8 repro was
  **inconclusive** (contaminated by the same flawed detector). The truncation is
  provider-side and load-correlated, but the exact causal share is **not proven**.
- "The stream `finish_reason` is coerced None→stop" — `finish_reason` is **not
  persisted**, so this remains a hypothesis from reading `router:1072`, not a
  verified fact.

**Verified vs reported:**
- Read directly and confirmed: findings §3.1 #1/#2/#4, §3.2 #1, §5, and the provider
  timeout (90s, live) + concurrency cap (16, live) + single-binding (no fallback).
- Mapped by read-only agents with `file:line` anchors, **not yet line-verified**:
  §3.2 #2/#3, §3.3 all. Any of these must be re-read at the exact line before a fix
  ships.
- The "~42% corrupted" figure is from **one** load-test run (concurrency 8). The
  direction is solid; the exact percentage needs a clean re-run **after** the metric
  is fixed (P1 #5-adjacent) and the provider is protected (P1 #4), so the provider
  stops corrupting the measurement itself.

---

## 8. Evidence artifacts
- Load-test trace: `reports/rag_trace_deepdive60_20260710.json` (60 questions, per-node steps, server `out_tok`).
- Pinned question set: `tests/scenarios/chinh-sach-xe_deepdive60.json`.
- Server-side answers: `request_logs.answer_text` / `.completion_tokens` (DB).
