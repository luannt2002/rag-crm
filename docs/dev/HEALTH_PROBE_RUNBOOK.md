# Health probe runbook — `/health/models`

Maintenance contract for the three smoke probes behind `GET /health/models`.

> **Sacred rule** — every probe MUST issue the same wire-format request as
> the live runtime path it represents. A probe whose request shape differs
> from runtime is worse than no probe: it produces a false-green signal
> while the next user request would TypeError before reaching the upstream.

---

## Why parity matters

The endpoint exists so ops can answer "are my AI providers reachable + speaking
the protocol I think they are?" before deploy / after key rotation / during
incident triage. The answer is only meaningful if the probe takes the same
code path a real user request does. Drift between probe and runtime hides
real failures behind a green dashboard.

Three concrete drift modes have been observed in this codebase:

1. **Embed probe missing `task=`** — Jina v3 and similar asymmetric
   embedders return different vectors for the query head vs the passage
   head. A probe without `task` exercises the default head, leaving the
   query head silently broken.
2. **LLM probe wrong kwarg shape** — calling `complete(model=..., messages=...,
   max_tokens=..., temperature=...)` against a router whose `LLMPort.complete`
   demands `(messages, *, spec, record_tenant_id, trace_id)` raises TypeError
   inside the outer fail-soft wrapper, classified as `unhealthy` for the
   wrong reason (signature drift, not upstream outage).
3. **Reranker probe missing kw-only `top_n`** — runtime calls keyword-only
   `top_n=`; positional would TypeError.

---

## The three probes and their runtime mirrors

| Probe | File / fn | Runtime mirror | Wire-format invariant |
|---|---|---|---|
| Embedding | `interfaces/http/routes/health_models.py::_probe_embedding` | `infrastructure/embedding/litellm_embedder.py::LiteLLMEmbedder.embed_batch` (called from `orchestration/query_graph.py::_embed_query`) | `EmbeddingSpec.task = DEFAULT_EMBEDDING_TASK_QUERY` (i.e. `"retrieval.query"`) — represents the query path |
| Reranker | `interfaces/http/routes/health_models.py::_probe_reranker` | `infrastructure/reranker/jina_reranker.py::JinaReranker.rerank` (called from `orchestration/query_graph.py::rerank_node`) | Positional `(query, chunks)` + kw-only `top_n=` |
| LLM | `interfaces/http/routes/health_models.py::_probe_llm` | `infrastructure/llm/dynamic_litellm_router.py::DynamicLiteLLMRouter.complete` (Port: `application/ports/llm_port.py::LLMPort.complete`) — invoked legacy-mode | Positional `messages: [LLMMessage]` + kwargs `spec=LLMSpec`, `record_tenant_id`, `trace_id` |

**Probe direction choice (embed)**: the probe target is the *query* head, not
the *passage* head. Reason: at runtime, query traffic is the user-facing hot
path; passage traffic is offline ingest. A green probe must guarantee live
question-answering still works.

---

## Maintenance rule

> **If you change one side, change the other.**

When the runtime call site grows a new required kwarg, the probe must grow
the matching kwarg in the same commit. Reverse direction too: if a Port's
contract narrows (kwarg removed / made positional-only), update the probe
in the same PR.

CI guard: `tests/unit/test_health_probe_runtime_parity.py` records every
kwarg the probe issues and asserts on the contract shape. A future drift
breaks that test before it can ship.

---

## When to run the endpoint

| Trigger | Mode | Command |
|---|---|---|
| Pre-deploy gate | full smoke | `curl -s "$RAGBOT_BASE_URL/health/models" -H "Authorization: Bearer $TOKEN" \| jq` |
| Cheap CI check | DB-only | append `?skip_smoke=true` |
| Post-key rotation | full smoke | same as pre-deploy |
| Incident triage | full smoke | same as pre-deploy |

`skip_smoke=true` only verifies bindings exist + env vars are set; it does
NOT call upstream. Use it when you want to fail fast on config drift but
do not want to burn provider tokens.

---

## Adding a new probe

1. Add the Strategy / Port in `infrastructure/<thing>/`.
2. Add a `_probe_<thing>(...)` function in `health_models.py` whose call
   shape mirrors the runtime caller verbatim.
3. Wire the probe into the dispatch loop guarded by `purpose ==`.
4. Extend `tests/unit/test_health_probe_runtime_parity.py` with a fourth
   recording stub asserting the wire-format kwargs.
5. Add the probe row to the table above and to `_PURPOSES_KNOWN` if it
   represents a new binding purpose.
