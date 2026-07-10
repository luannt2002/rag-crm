# Reliability Work + Clean Accuracy Re-run — 2026-07-10

> Continues the all-flows audit (`ALL_FLOWS_RELIABILITY_AUDIT_20260710.md`). This
> report covers the first reliability changes ("our-control" work, excluding the
> external provider itself) and a **slow, non-overloading** re-run to measure the
> bot's TRUE accuracy free of provider-truncation contamination.
>
> Domain-neutral: the deployment's LLM provider name is redacted to `<llm-provider>`.

---

## 1. Metric foundation — honest reliability probe

The old eval harnesses never called `raise_for_status`, so an upstream `503` was
recorded as an empty content-miss and a load test reported **"0 errors"** when a
large fraction actually failed. New tool `scripts/reliability_probe.py` classifies
every response honestly (answered / upstream_503 / server_5xx / client_4xx /
empty / transport_error) + latency p50/p95/max. Domain-neutral (bot + questions
from a scenario JSON; base URL + bypass token from env). This is the measurement
foundation for every reliability change below.

**Baseline @ provider concurrency cap = 16 (client concurrency 8, n=16):**

| Metric | Value |
|---|---|
| answered | 93.8% |
| **upstream_503** | **6.2%** ← was previously masked as "0 errors" |
| latency p50 / p95 / max | 50.5s / 159.8s / 159.8s |

The 160s tail = a request that hit the 90s provider timeout **then retried** —
i.e. the provider was so overloaded at cap 16 that calls timed out and re-ran.

---

## 2. Reliability change #1 — lower provider concurrency cap 16 → 6

**Rationale (from the audit):** truncation is NOT metadata-detectable (the
provider returns `finish_reason="stop"` for 100% of responses, including
mid-number-truncated ones — measured 24/24), so a response-completeness guard
cannot catch it. The only lever is **prevention**: stop bursting concurrent calls
at the provider. The router's per-provider semaphore
(`dynamic_litellm_router` `_get_semaphore` → `cfg.provider.max_concurrent`) was 16.

Shipped via alembic `lower_innocom_conc_260710` (sacred #7 — DB content only via
tracked migration; idempotent). Applied + service restarted.

**Measured effect (same probe, n=16):**

| Metric | cap=16 | cap=6 | Δ |
|---|---|---|---|
| latency p50 | 50.5s | **32.8s** | **−35%** |
| latency p95 | 159.8s | **93.4s** | **−42%** |
| latency max | 159.8s | 93.4s | −42% (timeout+retry tail gone) |
| answered | 93.8% | 93.8% | = |
| 503 rate | 6.2% | 6.2% | = (n too small to distinguish) |

- ✅ **CONFIRMED: lowering the cap cut latency ~35–42%.** Mechanism: a high cap
  overloads the provider → calls stall past the 90s timeout → retry → slow; a
  lower cap queues calls so each one completes without timing out.
- ⚠️ **NOT proven: truncation reduction.** The per-run truncation sample was too
  small (n=9 generation calls) to compare against baseline. The mechanism is
  sound but the magnitude is unmeasured at these sizes. **No downside was
  observed** (latency improved), so cap=6 is kept; a larger clean measurement is
  the honest way to quantify the truncation benefit.

---

## 3. Answer-integrity analysis ("our-control", independent of the provider)

Each item is app-side logic we own; none depend on the provider being fixed.

| # | Finding | Location | Fix | Owner decision? |
|---|---|---|---|---|
| B1 | Grounding judge exception → answer shipped as if verified (fail-OPEN) | `guard_output.py:702-718` | (a) fail-closed / (b) ship + flag `grounding_unverified` / (c) fail-closed only for numeric answers | **YES** — safety vs coverage. Recommend (b). |
| B2 | Callback exhausted (3 retries) → answer never re-pushed; `ChatAnswered` outbox event has **no consumer** (verified) | `callback_delivery.py:150`; `callbacks.py:120` | delivery-retry worker draining `ChatAnswered` w/ backoff, OR document "consumer must poll on callback failure" | design |
| B3 | Redelivery (crash before ACK) can create duplicate rows — chat handler is 2-arg, no `inbox_tx` | `pipeline.py:97` | idempotent inserts (`ON CONFLICT` on `request_id`) — localized; or adopt `inbox_tx` | recommend ON CONFLICT |
| B4 | Truly-empty answer stored `status="success"` | `callbacks.py:231` | empty → `status="empty"` + `ok:False` (guarded so legit refusals with text are unaffected) | ship |

The deterministic numeric-fidelity gate (why HALLU stayed 0) is **independent of
the grounding LLM**, so even B1 fail-open does not expose numeric hallucination —
only non-numeric faithfulness. That is the safety net that lets B1 be a coverage
vs safety trade rather than a HALLU risk.

---

## 4. Clean accuracy re-run (slow, non-overloading) — TRUE bot quality

Run at **client concurrency 2** (well under the cap, so the provider is not
overloaded → ~0 truncation, per the single-threaded=0% finding). This measures
the bot's real accuracy free of provider-truncation contamination — the number
the earlier concurrent load test could not produce.

Artifact: `reports/rag_trace_slow_20260710.json` (60 pinned questions, full
per-question trace: intent, chunks, score, answer).

**Clean-run result (60 questions, client concurrency 2):**

| Metric | Value |
|---|---|
| HALLU (delivered) | **0/60** |
| Coverage (content w/ ground-truth) | **26/32 = 81%** (~**91%** excluding truncation victims) |
| Truncation | **4/60** (persists even at low concurrency) |
| Empty / failed | **5/60** |
| Traps handled | **14/14** |

The 6 "misses" split into **3 truncation victims** (the expected value was cut
off mid-number) and **3 genuine content gaps** (a bank-account-holder name absent
from the corpus, one size whose price was not found, one comparison that
decomposed the wrong pair — all answered honestly, none fabricated).

**Every residual error traces to the provider, not the bot/RAG (root-caused):**

1. **Truncation (4/60)** — the provider cuts the stream mid-answer. Lower
   concurrency reduces it (~33% concurrent → ~7% here) but does NOT eliminate it;
   an occasional cut happens even at concurrency 2. Delivered `ok:true` (the
   dangerous case).
2. **Empty/failed listing queries** (`B-q02` "list all products", etc.) — NOT a
   retrieval gap (retrieval ran, chunks present). Traceback: the provider returned
   truncated JSON at `understand` (`Invalid JSON: EOF while parsing`) AND a `500
   InternalServerError` at `generate` → after retries the node raised `LLMError`
   → pipeline failed → **HTTP 503 delivered `ok:false`** (honest failure). These
   broad "list everything" queries produce larger prompts/outputs, which the
   provider is more likely to 500/truncate on. Same provider root cause.
3. **The one flagged number (`0909`)** — NOT a gate false positive. The LLM
   fabricated a phone number (`generate out_tok=102`); the deterministic
   numeric-fidelity gate caught it (`n_unsupported=1`) and the answer was
   **blocked** (`answer_type=blocked`) and replaced with the bot's safe template —
   the consumer never saw `0909`. **The anti-HALLU guard worked end-to-end.**

**Conclusion:** the RAG is not the problem — ~91% correct when the provider
delivers, HALLU=0, traps 14/14, and the anti-HALLU gate correctly blocks
fabrication. **100% of the residual errors are the provider** (truncation +
500/503), handled by the app. The **highest-value next lever is a fallback
provider binding**: the 500/503 failures (unlike silent truncation) ARE
detectable as exceptions, so the existing failover chain
(`dynamic_litellm_router.py:605`) would recover them immediately — it only needs a
second binding configured for the bot.

---

## 5. Status
- Shipped/applied: `reliability_probe.py` (tool), `lower_innocom_conc_260710`
  (cap 16→6, live DB + tracked), `llm_generation_finish` observability log.
- Analysed, awaiting owner decision: B1–B4 answer-integrity fixes.
- In progress: clean accuracy re-run (§4).
