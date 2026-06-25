# ADR: Long-context mode — stuff the whole corpus for small-corpus bots

Status: Proposed
Date: 2026-06-25
Stream: RAG accuracy — NotebookLM-parity (`reports/RAGBOT_FULL_PIPELINE_TRACE_20260624.md`)

## Context

NotebookLM answers the chinh-sach-xe questions fast + accurately not because its retrieval
is better, but because it is NOT retrieval-RAG for small inputs: it loads the ENTIRE set of
source files into the model's context window (Gemini, 1–2M tokens) and the LLM reads every
row and column directly — zero loss from chunking, column-role extraction, or fragmentation.
This works precisely BECAUSE the corpus is tiny (a handful of small files fit in context).

Ragbot is built for scale (thousands of docs per bot) so it chunks + retrieves top-K. For a
small-corpus bot, that machinery is pure downside: every failure mode in the trace report
(closed-vocab column drop, multi-sheet fragmentation, 1-chunk synthetic answer) is a
consequence of cutting + searching data that would have fit in context whole.

The platform's answer model is `gpt-4.1-mini` (resolved via Port + `ModelResolverService`),
which itself has a large context window. So long-context stuffing is achievable WITHOUT
switching providers — contrary to NotebookLM-advice that assumes Gemini.

## Decision

Add an opt-in **long-context mode**: for a bot whose total active corpus fits under a token
budget, the `retrieve` node bypasses chunk retrieval and feeds the bot's FULL document text
(raw `document_chunks.content` joined, or `documents.raw_content`) as the context, letting
the LLM read everything.

- **Eligibility (config-driven, measured):** mode engages only when
  `long_context_mode_enabled` (per-bot plan_limits, default OFF) AND the bot's total corpus
  token estimate ≤ `long_context_max_tokens` (system_config, conservative default well under
  the model's window to leave room for the prompt + answer). Above budget → normal retrieval.
- **Where:** a branch at the top of the `retrieve` node — when eligible, build a single
  "whole-corpus" context payload from the bot's active documents (tenant/bot scoped, RLS
  enforced) and set `retrieve_mode="long_context"`, skipping rerank/grade like the stats
  route. Grounding stays ON (the whole corpus IS the grounding source).
- **Model:** resolved via the existing `ModelResolverService` — NO new provider, NO model
  literal in the node. The resolver picks the bot's answer model (a large-context model);
  if the resolved model's window is too small for the payload, fail safe to normal retrieval
  (logged), never truncate silently.

## Why this shape (not alternatives)

- **Not "always stuff".** Stuffing a large corpus blows the window + cost + latency and
  loses to retrieval. The token-budget gate is the whole point — long-context ONLY where it
  strictly dominates (small corpus).
- **Not a separate "notebook" product / parallel pipeline.** It is one branch in `retrieve`
  reusing generate/guard/persist — strangler-fig, no fork.
- **Not provider-switch to Gemini.** The cost + Port constraints pin the model resolver;
  `gpt-4.1-mini`'s window already suffices for small corpora. Provider choice stays a
  resolver/config concern, not baked into the mode.
- **Cache the corpus prefix.** The whole-corpus context is identical across a bot's queries
  → prompt-cache it (the platform already has an Anthropic/LLM prompt-cache wrapper) so
  repeated turns pay the big-context cost once. This is what makes it fast like NotebookLM.

## Consequences

- **NotebookLM-parity for small-corpus bots:** zero retrieval loss → answers every field
  (code/stock/date/image/warranty) directly, no fragmentation, no column-role drop. The
  simplest path to ~100% on a small catalog.
- **Cost + latency are real:** a big context per turn costs more tokens; mitigated by prompt
  caching + the token-budget gate. MUST be measured (per-turn cost + p95) before default-on
  for any bot — `no-guess-must-measure`.
- **HALLU=0 preserved:** grounding stays ON; the whole corpus is the evidence, so the judge
  has full coverage. Refusal traps still refuse (the answer is absent from the corpus).
- **Scope discipline:** strictly gated to small corpora. A bot that grows past the budget
  silently reverts to retrieval — so accuracy could regress at the boundary; surface a
  `long_context_disabled_over_budget` warning so the owner knows.

## Reversibility

Pure opt-in branch behind `long_context_mode_enabled` (default OFF) + a token budget. Flip
the flag off → the bot uses normal retrieval, byte-identical to today. No schema change, no
data migration. Fully reversible per bot.

## Status / next

Proposed — needs approval, and a cost/p95 measurement on a small bot before any default
change. Complements (does not replace) P9 + entity-join: those make RETRIEVAL good at scale;
long-context makes SMALL bots perfect. Implementation plan in `plans/` once accepted.
