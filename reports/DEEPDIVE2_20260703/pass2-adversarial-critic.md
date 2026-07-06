# PASS-2 ADVERSARIAL CRITIC — re-verification of DEEPDIVE_20260702 top findings

> Hostile re-read of the pass-1 synthesis (`FINAL-MASTER-PROMPT-SYNTHESIS.md` +
> `00-SYNTHESIS-MASTER.md`). For each top CRITICAL/HIGH claim I re-opened the cited
> `file:line` myself and tried to REFUTE it. Every verdict below carries `file:line`
> evidence, labelled **FACT** (verified by reading source / running code) vs
> **HYPOTHESIS** (mechanism plausible, impact not runtime-measured).
> Empirical probes run against the actually-installed libs (langgraph 1.2.4,
> kreuzberg 4.9.7). Read-only pass — no source changed.

---

## 0. HEADLINE

The pass-1 audit is **substantially honest**. Of the ~15 highest-severity claims I
re-verified, **12 STAND as FACT**, **1 is PARTIALLY-WRONG** (XML-wrap "100% dead"),
**2 are real but SEVERITY-INFLATED** (idempotency multi-bot leak; `int(_price)` +
rerank-floor HALLU framing). The two flagship systemic classes — **S1 LangGraph
state-key drop** and **S2 last-mile DI wiring** — are both **empirically proven**,
not speculation. I found **no second RETR-F1-class false-positive** among the CRITICALs.

The single biggest correction: pass-1 repeatedly says dropped-key features are
"100% unreachable / dead". For keys set in the **initial state dict** and read
**inside the same node that a later resolver runs in**, that is exact. For features
with an **explicit per-bot `plan_limits` override that flows through the DECLARED
`pipeline_config` key**, the config path still works — only the *implicit default-on*
path is dead. XML-wrap is the clearest example.

---

## 1. EMPIRICAL PROBE — the load-bearing fact under S1

I built a minimal `StateGraph` with a plain `TypedDict(total=False)` (exactly how
`query_graph.py:2724` constructs `StateGraph(GraphState)` — no `Annotated`/custom
reducer, confirmed by grep) and measured langgraph 1.2.4 behavior:

```
a_sees_undeclared_initial = 'DROPPED'   # key in INITIAL input, undeclared → gone before node A
a_sees_declared           = 'init'       # declared key survives
b_sees_undeclared_node    = 'DROPPED'    # key RETURNED by node A, undeclared → gone before node B
b_sees_declared           = 'set_by_node_a'
final keys                = ['declared']  # both undeclared keys absent from final state
```

**FACT**: langgraph 1.2.4 drops undeclared keys both from the initial input AND from
node returns. This is the mechanism pass-1 asserts. It is real. (`state.py:9`
`class GraphState(TypedDict, total=False)`; probe reproducible.)

---

## 2. VERDICTS ON THE TOP CRITICAL/HIGH CLAIMS

### S1 / L2-1 — LangGraph drops ≥12-16 state keys → paid tokens = 0, rerank floor dead
**VERDICT: STANDS (FACT).**
- `bot_extra_output_tokens_per_response`: set in the initial state dict
  (`graph_assembly.py:193`), NOT in `GraphState` (verified full read of `state.py`,
  key absent), read at `generate.py:738-739` with `state.get(..., 0) or 0`. Probe proves
  the initial-input key is dropped before generate runs → paid extra output tokens
  are silently zeroed. **Revenue feature dead. CONFIRMED.**
- `rerank_score_mode`: RETURNED by rerank node (`rerank.py:498`
  `return {"reranked_chunks": out, "rerank_score_mode": mode}`), read in a DIFFERENT
  node (`grade.py:486` `if state.get("rerank_score_mode") == "rerank":`). Undeclared →
  dropped → always None → grade always takes the `else` (relative-gate) branch. CONFIRMED
  (severity nuance in §3).
- `bot_created_at`: set at `graph_assembly.py:192`, undeclared, read in
  `_resolve_xml_wrap_enabled` (`query_graph.py:~455`) → dropped → always None (see §3
  for the PARTIALLY-WRONG severity correction).
- Bonus corroboration (live): `embedding_column` is ALSO undeclared (`grep` on
  `state.py` = 0 hits), set in-node at `query_graph.py:1337/1393` + `retrieve.py:921`,
  read cross-node at `query_graph.py:2697` → dropped → the production warning
  `semantic_cache_preflight_no_embedding_column` fires every turn. This is live runtime
  proof the S1 class is active, exactly as pass-1 said.

### L2-2 / L2-3 — Stats route grounding-skip is unconditional; HALLU-net reverted
**VERDICT: STANDS (FACT).**
- `guard_output.py:105-106`: `if str(state.get("retrieve_mode") or "").startswith("stats"): _grounding_eligible = False` — NO `_pcfg(stats_route_skip_grounding)` guard. Unconditional skip.
- Git: `062d6fa` = "apply grounding judge to stats route by default (HALLU-safe)" wired the
  per-bot flag (default `False` = grounding ON) and its commit body documents the exact
  breach it closed: *"skipping let an answer cite a value NOT present in the matched entity
  (a stock number leaked from history) pass unchecked — a HALLU breach."* The newer commit
  `3097755` reverted to the unconditional skip. The knob/const still exist
  (`DEFAULT_STATS_ROUTE_SKIP_GROUNDING=False` in `_15_...py:124`; `bot_limits.py:67`) but the
  node no longer reads them.
- Pin test `test_guard_output_wires_stats_route_skip_grounding_flag` **runs and FAILS**
  (asserts source contains `stats_route_skip_grounding` — it doesn't). CONFIRMED by execution.
- Pressure-test of the comment's own defense ("HALLU traps never reach it"): the fixing
  commit itself proves a leak *did* reach it. Net = stats-route answers bypass rerank+grade+
  grounding with synthetic score=1.0. **HALLU-net genuinely re-opened. CONFIRMED.**

### L2-4 — GraphRAG broken BOTH directions via `bot_id=` vs `record_bot_id` kwarg
**VERDICT: STANDS (FACT).**
- Query side: `graph_retriever.py:61` calls `kg_service.query_graph(query=..., bot_id=record_bot_id, ...)`.
  Signature `knowledge_graph.py:179-185` requires `record_bot_id: UUID`. → `TypeError`
  swallowed by `except Exception: # noqa: BLE001` at `graph_retriever.py:107` → returns
  `{"graph_context": []}`. Query always 0.
- Ingest side: `ingest_core.py:801` calls `kg_service.store_triples(bot_id=bot_uuid, ...)`.
  Signature `knowledge_graph.py:128-134` requires `record_bot_id: UUID`. → `TypeError`
  swallowed by the best-effort `except Exception: # noqa: BLE001` wrapping the extraction
  block → LLM-extracted triples computed (token cost paid) then discarded.
- This is exactly the naming-convention bug class the MEMORY warns about. CONFIRMED both directions.

### L2-7 — `parent_chunk_id` never SELECTed → parent-child / stage-4 / auto-merge are no-ops
**VERDICT: STANDS (FACT).**
- Written as a real COLUMN at ingest (`ingest_helpers.py:190` INSERT col list;
  `ingest_stages_store.py:919`), NOT in metadata_json (listed separately from `metadata_json`).
- Main dense SELECT `pgvector_store.py:331` returns `id, record_document_id, chunk_index,
  content, metadata_json, score` — no `parent_chunk_id`; result dict `340-348` has no such key.
- Hybrid SELECT `pgvector_store.py:553/583` — same, no `parent_chunk_id`.
- Downstream `parent_expand_stage4.py:63-65` reads `c.get("parent_chunk_id")` → always None →
  the stage's own docstring (line 14) admits it no-ops when no chunk carries the id. Same for
  `auto_merge_retrieval.py` + `neighbor_expand`. CONFIRMED — three features permanently dead.

### L2-9 — Cascade routing is a no-op (`resolved_answer_model` has no consumer)
**VERDICT: STANDS (FACT).**
- Only two references to `resolved_answer_model`: READ at `generate.py:376` (to compute the
  *current* model BEFORE cascade), SET at `generate.py:408`. It is never read again after 408.
- The actual generation model is chosen by `_binding_purpose = _resolve_purpose_for_intent(...)`
  (`generate.py:764`) and `_invoke_structured_llm_node(..., purpose="generation",
  binding_purpose=...)` (`generate.py:789-797`); `model_name` comes from the LLM ctx
  (`generate.py:807`), never from `resolved_answer_model`. Cascade computes + logs a value and
  discards it (and the key is undeclared so it wouldn't survive the node boundary anyway).
  CONFIRMED no-op.

### L2-11 — Grounding gate is INVERTED (ungrounded ships; can't-run refuses)
**VERDICT: STANDS (FACT).**
- Ungrounded detected: `local_guardrail.py:539-552` returns `GuardrailHit(severity="warn",
  action="hitl")`. `check_output` only raises `GuardrailBlocked` `if any(h.severity=="block")`
  (`local_guardrail.py:933-934`) — a `warn` hit never blocks. In guard_output's parallel path
  (`guard_output.py:499-515`), a present `grounding_hit` with no regex block just appends to
  `flags` and `return {"guardrail_flags": flags}` — the answer is NOT substituted → **ungrounded
  answer ships.**
- Can't-run: `_grounding_fail_closed` path `guard_output.py:359-381` substitutes the OOS template,
  `answer_type="blocked"` → **refuses.**
- Net: measures "bịa" → ships; fails to measure → refuses. Genuinely backwards. CONFIRMED.

### L1-2 — OCR/registry-fallback parser returns 0 blocks for every doc (coroutine never awaited)
**VERDICT: STANDS (FACT, runtime-verified).**
- Installed kreuzberg = 4.9.7. `python -c "inspect.iscoroutinefunction(kreuzberg.extract_bytes)"`
  → **True**; `extract_bytes_sync` exists. `kreuzberg_parser.py:258` calls
  `result = extract_bytes(data, mime_type_arg)` inside the SYNC method `_extract_blocks`
  (enclosing `def`, not `async def`) → `result` is an un-awaited coroutine →
  `getattr(result,"elements"/"blocks")` = None → `elements = ()` → block_count 0 always.
  Every format that falls to this adapter (images, .doc/.xls/.ppt, unknown) → empty parse → DLQ.
  CONFIRMED.

### L1-1 / F4 — Official B2B ingest (`/documents/create` → worker) flattens row-chunks
**VERDICT: STANDS (FACT).**
- Worker joins parser chunks: `document_worker.py:464-466`
  `full_text = "\n\n".join(c["content"] for c in _chunks ...)`.
- Worker ingest call passes `content=full_text` + `blocks=parsed_blocks` but **no `raw_bytes`**
  (`document_worker.py:613-626`).
- `parser_row_chunks` is only populated `if raw_bytes is not None:` (`ingest_core.py:317-321`)
  → None on the worker path → the `parser_preserve` row-per-chunk fast-path
  (`ingest_stages.py:763-767`, gated on `parser_row_chunks and _parser_is_row_shaped`) never fires.
- Pressure-test of the `blocks=` alternative the pass-1 reader might have skipped: `blocks` is
  **observability-only** here — the docstring `ingest_core.py:204-205` says *"S1 only THREADS the
  blocks; the block-native chunking flip (S2/S3) lands behind a graded A/B gate"* and
  `ingest_core.py:234-242` only LOGS the block histogram. So `blocks` does NOT rescue structure by
  default. The 2026-07-01 row-per-chunk fix protects Path A (raw_bytes/test-harness) only.
  CONFIRMED.

### L1-4 — PII redaction frozen to "null" provider (knob unwired)
**VERDICT: STANDS (FACT).**
- `bootstrap.py:447-450`: `pii = providers.Singleton(build_pii_redactor,
  provider=DEFAULT_PII_REDACTOR_PROVIDER)` — literal constant, compile-time.
  `DEFAULT_PII_REDACTOR_PROVIDER = "null"` (`_13_...py:100`).
- `pii_redactor_provider` appears only in the config-key registry
  (`bootstrap_config.py:61`) + the port docstring; no `providers.Callable(get_boot_config(...))`
  wires it. So system_config selection has zero effect. Redaction is null/passthrough end-to-end.
  CONFIRMED.

### L2-16 / repos-db F1 — `ragbot.ai_keys` table queried but never created
**VERDICT: STANDS (FACT).**
- `ai_config_repository.py` issues `INSERT/SELECT/UPDATE ... ragbot.ai_keys`
  (lines 664, 689, 708, 731, 747). `grep` for `ai_keys` / `create_table` across
  `alembic/versions/*.py` = **0 hits**. Table does not exist → encrypted key-pool fails every
  call; only `.env` keys work. CONFIRMED.

### L2-6 — RLS dead in fallback stages 2-4 + soft-deleted docs revived
**VERDICT: STANDS (FACT).**
- `bm25_only_stage2.py`: `retrieve(..., **kwargs)` (line 53) swallows any `record_tenant_id`;
  runs `async with session_factory() as session:` (line 86) — a BARE session with no
  `session_with_tenant` / `SET LOCAL app.tenant_id` (contrast pgvector_store which routes through
  `session_with_tenant`, seen at its line 306). Under live RLS this returns 0 rows.
- Soft-delete: WHERE clause `bm25_only_stage2.py:78-80` is `d.record_bot_id = :rbid AND
  search_vector @@ ...` with NO `d.deleted_at IS NULL` → soft-deleted docs resurface through the
  fallback. CONFIRMED.

### F2 — dead re-exports abort 8 pin-test files at collection
**VERDICT: STANDS (FACT, runtime-verified).**
- `python -c "from ragbot.orchestration.query_graph import _cliff_detect_filter"` → ImportError;
  same for `_rerank_threshold_gate`. `pytest tests/unit/ --co` → *"Interrupted: 8 errors during
  collection"* including `test_reranker_threshold_gate.py`, `test_query_decompose.py`,
  `test_output_guardrail_tuning.py`, `test_route_workspace_scope_pin.py`. The cliff/threshold/CRAG
  invariants are unguarded. CONFIRMED both the mechanism and the "8 collection errors" count.

### F9 — stats rows written under a FABRICATED tenant when tenant_id missing
**VERDICT: STANDS (FACT).**
- `ingest_stages_final.py:562`: `record_tenant_id=record_tenant_id or uuid.uuid4()`. Missing
  tenant → a random UUID is minted → rows land under a non-existent tenant, invisible forever,
  instead of fail-loud. CONFIRMED.

---

## 3. OVER-CLAIMS (finding is real but severity/impact is inflated)

### O-1 · XML-wrap "CHẾT → feature 100% unreachable" (§9B key table, `bot_created_at`)
The **date-based default-on** path is dead (because `bot_created_at` is dropped →
`_resolve_xml_wrap_enabled` returns `DEFAULT_XML_WRAP_ENABLED=False`). BUT the resolution chain
(`query_graph.py:450-463`) checks `_pcfg(state, "xml_wrap_enabled", None)` FIRST, and `_pcfg`
reads `state["pipeline_config"]` which IS a declared, surviving key. So a bot that sets
`plan_limits.xml_wrap_enabled=True` explicitly still gets XML-wrap. **Correction**: only the
*implicit new-bot default-on* is unreachable; explicit per-bot opt-in works. "100% unreachable" is
wrong; "date-default-on dead, explicit opt-in intact" is right. **(FACT of the mechanism; severity
over-claimed.)**

### O-2 · `rerank_score_mode` drop → "HALLU risk↑" (L2-1 row / §9B table)
The drop is real (grade always uses the relative gate). But the affected code is the **CRAG
all-irrelevant fallback** (`grade.py:479-503`), which fires only after the grader already deemed
every chunk irrelevant, to salvage chunks rather than refuse. The absolute-floor-vs-relative-gate
mismatch is a genuine **calibration regression**, but "chunk rác lọt generate → HALLU↑" is a
**HYPOTHESIS** — it presumes salvaged chunks are garbage AND that downstream grounding (a net that
still runs on non-stats routes) fails to catch it. Impact is "may loosen refuse gate on the
all-irrelevant fallback", not a measured HALLU breach. **(Real bug; HALLU severity unproven.)**

### O-3 · `int(_price)` truncation → multi-currency HALLU "grounded fact" (L2-5)
`query_graph.py:2391,2413,2419` do `int(_price)`; `price_primary` is `NUMERIC`
(`stats_index_repository.py:20`) so decimals CAN be stored. Two real effects: 19.99→"19" in the
synthesized chunk, and dedup-key collision `(_name, int(_price))` at line 2391 drops 19.50 vs
19.99. BUT the current deployments are VND catalogs (`document_stats.py:312` "VND int"), where
prices are whole integers and `int()` is a no-op. The bug bites **only** decimal-currency corpora,
which the platform claims to support but none of the live bots use today. **Correction**: real
domain-neutrality gap, **zero impact on current production**; the "HALLU do retrieval tier tiêm
vào" framing is correct only for a hypothetical USD/EUR bot. **(FACT conditional on
multi-currency corpus.)**

### O-4 · ports-dto F1 "idempotency swallows bot 2 → multi-bot leak / data-loss"
The idempotency uniqueness key is `(record_tenant_id, workspace_id, idempotency_key)` — bot_id NOT
included (`ingest_idempotency_service.py:75,173-178`). BUT `idempotency_key` is the **opaque
partner-supplied `X-Idempotency-Key` header** (`documents.py:117`), not the source_url. Bot 2 is
only swallowed if the partner **reuses the same header value across two different bots** within the
TTL — which violates the normal idempotency contract (keys must be unique per logical operation).
The full request body (incl. bot_id/source_url) IS hashed but only for mismatch LOGGING
(`documents.py:127-132`), not uniqueness. **Correction**: this is a contract-hardening gap
(should add bot_id to the scope for defence-in-depth), NOT an unconditional data-loss bug. Severity
"multi-bot leak class" is over-stated. **(Real gap; conditional on partner key-reuse.)**

### O-5 · repos-db F4 "có mấy X ≠ liệt kê X" — count vs list mismatch
Real: `query_by_name_keyword` has an EXTRA notation-fold clause
(`stats_index_repository.py:568-572`, `_fold(entity_name) LIKE _fold(kw)`) that
`count_by_name_keyword` deliberately omits (`:439-452`, comment `:420-422` admits the omission).
A row matching ONLY via notation-fold appears in the list but not the count → cardinality can
differ. BUT the author's comment argues (wrongly for notation-only matches, rightly for the common
case) that fold is "a ranking detail that does not change count cardinality". So it's a **narrow
edge** (notation-variant corpora like tire sizes / SKUs), not a broad "count and list contradict on
every corpus". Real FACT, narrower blast radius than the headline implies.

---

## 4. MISSED ISSUES (real problems pass-1 under-surfaced or didn't connect)

### M-1 · `embedding_column` drop = live, self-inflicted proof of S1 (LOW sev, HIGH evidentiary value)
Pass-1 lists `embedding_column` only implicitly. It is the **cleanest smoking gun**: undeclared
(`state.py` 0 hits), set in-node (`query_graph.py:1337/1393`, `retrieve.py:921`), read cross-node
(`query_graph.py:2697`), and the resulting warning `semantic_cache_preflight_no_embedding_column`
is confirmed firing every turn in production. Functionally low-impact (retrieve re-defaults it),
but it is the datum that upgrades S1 from HYPOTHESIS to FACT-in-prod and should headline the S1
write-up. **(FACT.)**

### M-2 · `raw_user_message` drop silently reverts the 2026-06-15 slot-extraction fix (MED)
`raw_user_message` is set in the initial state (`graph_assembly.py:177`) but is **undeclared** in
`GraphState` (verified). `generate.py:250-255` reads `state.get("raw_user_message") or
state.get("original_query") or state.get("query","")`. Under S1 the first term is always dropped →
slot extraction falls back to `original_query`/condensed `query` — the exact regression the
2026-06-15 comment (`generate.py:240-247`) says caused "measured 5/5" OOS refusals on bare slot
turns ("Tên Lan"). This is a concrete S1 casualty pass-1 didn't call out by name and it directly
hurts the action/booking flow. **(FACT; behavioral impact HYPOTHESIS pending a slot-turn load
test.)**

### M-3 · `check_output` grounding hit is `warn`, so `citation_marker_required` is the ONLY
### thing that can actually block on the non-parallel path (MED)
Beyond L2-11, note `local_guardrail.py:933` blocks only on `severity=="block"`. The grounding
judge, the OOS-similarity check, and citation checks all need to emit `severity="block"` to matter.
Worth an explicit inventory of which output-guardrail rules are `warn` vs `block` — several
"safety" rules may be observe-only by construction, not just grounding. **(FACT for grounding;
inventory recommended.)**

### M-4 · fabricated-tenant pattern (F9) is one of several `or uuid.uuid4()` / `or <default>`
### fail-open sinks — worth a sweep (LOW-MED)
`ingest_stages_final.py:562` mints a tenant when missing. The same fail-open shape (silently invent
an identifier instead of fail-loud) is the class behind F9 + the idempotency scope gap + the
`record_tenant_id or uuid.uuid4()` idiom. A `grep -rn "or uuid.uuid4()"` sweep across write paths
would likely surface siblings. **(FACT for the cited line; sweep is a recommendation.)**

### M-5 · Fallback-stage bare sessions are a REPEATED shape, not a one-off (MED, security)
`bm25_only_stage2.py:86` bare session is confirmed; the `**kwargs` swallow of `record_tenant_id`
(`:53`) means the tenant context is dropped by construction across the stage family. Recommend
grepping `session_factory() as session` under `retrieval_fallback/` and asserting every one either
routes through `session_with_tenant` or is provably tenant-scoped in SQL. This is the same posture
gap as repos-db F2 (RLS runtime-superuser escape hatch) — the two should be treated as one
remediation. **(FACT for stage2; family-wide claim is a recommended verification.)**

---

## 5. NET ASSESSMENT

| Claim | Verdict |
|---|---|
| S1 LangGraph state-drop (mechanism) | STANDS — FACT, empirically probed on langgraph 1.2.4 |
| L2-1 paid-tokens=0 | STANDS — FACT |
| L2-1 rerank_score_mode dropped | STANDS — FACT (HALLU severity = HYPOTHESIS, see O-2) |
| L2-2/L2-3 stats grounding-skip + reverted HALLU-net | STANDS — FACT (pin test fails, breach documented in 062d6fa) |
| L2-4 GraphRAG kwarg both directions | STANDS — FACT |
| L2-5 int(_price) | STANDS — FACT, but VND-null impact today (O-3) |
| L2-6 RLS-dead fallback + soft-delete revive | STANDS — FACT |
| L2-7 parent_chunk_id never selected | STANDS — FACT |
| L2-9 cascade no-op | STANDS — FACT |
| L2-11 grounding gate inverted | STANDS — FACT |
| L2-16 ai_keys table absent | STANDS — FACT |
| L1-1/F4 Path A/B split | STANDS — FACT |
| L1-2 OCR coroutine-never-awaited | STANDS — FACT, runtime-verified |
| L1-4 PII frozen null | STANDS — FACT |
| F2 dead re-exports / 8 collection errors | STANDS — FACT, runtime-verified |
| F9 fabricated tenant | STANDS — FACT |
| XML-wrap "100% dead" | PARTIALLY-WRONG — explicit opt-in still works (O-1) |
| ports-dto F1 multi-bot data-loss | OVER-CLAIM — conditional on partner key-reuse (O-4) |
| repos-db F4 count≠list | STANDS but narrow — notation-variant corpora only (O-5) |

**Bottom line for the owner**: the pass-1 P0 list is trustworthy. The two systemic root
causes are proven, not guessed. Fix priority is unchanged. Only tighten the language on
XML-wrap (explicit opt-in survives), decimal-price and idempotency (conditional, not
unconditional), and elevate `embedding_column` + `raw_user_message` as named S1 casualties.
No second RETR-F1-class false-positive was found among the CRITICALs.
