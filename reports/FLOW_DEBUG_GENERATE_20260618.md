# FLOW DEBUG: GENERATE → GROUNDING → GUARD — Backward Verification Audit
**Date**: 2026-06-18  
**Scope**: READ-ONLY deep-dive — src/ not modified  
**Purpose**: Answer "did the LLM actually receive the gold chunk in its prompt? did its answer ground to that chunk?"

---

## 1. PROMPT ASSEMBLY

### How the Final Prompt Is Built

The prompt assembly happens entirely inside `generate()` in `generate_started` → `prompt_build` steps:

**File**: `src/ragbot/orchestration/nodes/generate.py:441–630`

**Step-by-step chain**:

1. **Input chunks**: `graded = state.get("graded_chunks") or []` — these are the graded+reranked chunks (generate.py:114).

2. **Optional transformations before prompt assembly** (in order):
   - Prompt compression (`compress_chunks`) — may truncate chunk text up to `prompt_compression_max_chars_per_chunk` (generate.py:354–394)
   - Adaptive context pruning — may reduce chunk count if `adaptive_context_enabled` + top score ≥ threshold (generate.py:401–417)
   - Lost-in-middle reorder — reorders chunks so top-scored are at start+end (generate.py:419–439)
   - Prompt token opt (`apply_token_opt`) — may drop low-score or near-duplicate chunks (generate.py:472–479)
   - Context chars cap — drops tail chunks if total chars > `generate_context_chars_cap` (generate.py:510–523)

3. **`chunk_ids_allowed`** is computed from the FINAL surviving `graded` list AFTER all transforms:
   ```python
   # generate.py:524–528
   chunk_ids_allowed = {
       str(c.get("chunk_id") or c.get("id") or "")
       for c in graded
       if c.get("chunk_id") or c.get("id")
   }
   ```
   This set is the authoritative gate — only these chunk_ids are valid citations.

4. **Context block format** (generate.py:541–580):
   - If `_xml_wrap` (M14 per-bot feature): `<chunk id="{cid}" type="{type}" section="{section}"><content>{text}</content></chunk>`
   - If `_trust_hint`: `<context source="..." chunk="..." id="{cid}" trust="data_only" type="...">{text}</context>`
   - Otherwise: `<context source="..." chunk="..." id="{cid}">{text}</context>`
   - `context_str = "\n\n".join(context_blocks)`

5. **System prompt** assembly (generate.py:582–593):
   - `system_prompt = state.get("bot_system_prompt", "") or ""`
   - Falls back to `_lang(state).prompt_generator` if empty
   - `SysPromptAssembler` (`src/ragbot/application/services/sysprompt_assembler.py`) appends `language_packs[locale].sysprompt_default_rules` (if not disabled by `plan_limits.sysprompt_rules_disabled`)

6. **Final messages list** (generate.py:603–616):
   ```python
   messages = [{"role": "system", "content": system_prompt}]
   # + history messages (up to history_cap)
   messages.append({"role": "user", "content": f"<documents>\n{context_str}\n</documents>\n\n<question>{_q}</question>"})
   ```
   For chitchat intent: `<question>{_q}</question>` only (no `<documents>` block).

### Is the Exact Prompt Text (with chunk_ids) Captured Anywhere?

**NO — the exact prompt text is NOT persisted.** Evidence:

- `invocation_logger.invoke_model(user_prompt=user_prompt, ...)` at generate.py:1160–1168 (query_graph.py) only stores `user_prompt_hash = content_hash_required(user_prompt)` — the hash of the raw query, not the assembled prompt with `<documents>` block.
- `model_invocations.full_payload_hash = sha256(f"{purpose}|{provider}|{model_id}|{model_version}|{user_prompt}")` — does NOT include the context block. (`src/ragbot/infrastructure/observability/invocation_logger.py:151–156`)
- The assembled `messages` list (system_prompt + history + `<documents>...`) is never serialized to DB.

### What IS captured about chunks entering the prompt:

The **`prompt_build` step** metadata (stored in `request_steps.metadata_json`) contains:
```python
# generate.py:617–629
pb_ctx.set_metadata(
    context_chars=len(context_str),
    history_msgs=len(_history_messages),
    context_chunks=len(chunk_ids_allowed),   # ← count of chunks in prompt
    compressed=_prompt_compressed,
    context_cap=_ctx_cap,
    context_chunks_dropped=_dropped_chunks,
    context_chars_dropped=_dropped_chars,
    token_opt_enabled=_pto_enabled,
    token_opt_dropped_by_score=...,
    token_opt_dropped_by_dedupe=...,
    token_opt_history_skipped=_pto_skip_history,
)
```
This gives **counts** (how many chunks, how many dropped) but **NOT the chunk_ids themselves**.

The **`generate_started` audit event** logs chunk count and total chars (generate.py:115–125) but NOT the chunk_ids.

**VERDICT**: The set of `chunk_ids` that actually entered the LLM prompt is **NOT recoverable from any DB table** after the fact. Only the count (`context_chunks`) survives in `request_steps.metadata_json`. The chunk_id→content mapping is only available in the `request_chunk_refs` table (written by `callbacks.py:200–224`), which stores `graded_chunks` before any prompt-time trimming by `generate()`.

> **Critical gap**: `request_chunk_refs` records the graded chunk set from CRAG output, but `generate()` may further drop chunks during prompt assembly (chars cap, token opt, adaptive context). There is no record of the FINAL set sent to the LLM.

---

## 2. LLM CALL + OUTPUT

### What Is Captured From the Response

The LLM is invoked via `_invoke_llm_node()` defined in `src/ragbot/orchestration/query_graph.py:1119–1335`.

**What is captured** from the response:

- `answer: str` — the raw LLM answer text (generate.py:763)
- `prompt_tokens`, `completion_tokens`, `cached_tokens` — token counts (generate.py:764–766)
- `cost_usd` — USD cost (generate.py:767)
- `finish_reason` — e.g., "stop" (generate.py:768)
- `model_name` — resolved model identifier (generate.py:769)

**Is the raw answer text persisted?**

YES, indirectly:
- `request_logs.answer_hash` stores `content_hash_required(answer_text)` — a SHA-256 of the answer (callbacks.py:208). Raw text NOT in DB.
- `messages` table: `src/ragbot/infrastructure/db/models.py` line 285 — there is a `messages` table. The chat worker writes the answer there via the message repository. The answer text IS stored in `messages.content` (confirmed by schema presence).
- `model_invocations.response_hash` — SHA-256 of the answer (invocation_logger.py:92, stored at :227). Raw answer NOT there.

**Token tracking** (per-invocation):
`model_invocations` table stores: `prompt_tokens`, `completion_tokens`, `cost_usd`, `duration_ms`, `status`, `finish_reason`. (models_invocation.py:107–118)

**Step-level tracking**:
`request_steps.input_tokens`, `output_tokens`, `cost_usd`, `model_used` populated via `_gen_ctx.record_llm(...)` (generate.py:866–871).

---

## 3. GROUNDING — The Guard Node

### CRAG Grade Node (`grade.py`) — Pre-LLM Relevance Grading

This runs BEFORE generate(). It labels each reranked chunk as RELEVANT/IRRELEVANT/AMBIGUOUS.

- Only chunks graded RELEVANT or AMBIGUOUS pass through to `graded_chunks` for generate().
- Does NOT map answer claims back to chunk_ids (it runs before the LLM answer exists).

**Audit event** (grade.py:548–563):
```python
await _audit(state, "grade_executed", {
    "relevant": int, "irrelevant": int, "ambiguous": int,
    "graded_kept": int,
    "retrieval_adequate": bool,
    "fallback_used": bool,
    "iterations": int,
    "intent_corrected": bool,
})
```
No chunk_ids in this event — only aggregate counts.

### Output Guard Node (`guard_output.py`) — Post-LLM Grounding Check

**File**: `src/ragbot/orchestration/nodes/guard_output.py`

The grounding check here operates on the LLM answer AFTER generation:

**Three grounding mechanisms**:

1. **Regex static grounding** (`OutputGuardrail.grounding_check`, local_guardrail.py:367–414):
   - Pass 1: citation marker `[chunk_id]` present → grounded (fast pass)
   - Pass 2: substring verbatim match (≥ `DEFAULT_GROUNDING_SUBSTRING_MIN` chars) in any chunk → grounded
   - Pass 3: numeric token overlap — every digit sequence in answer is present in some chunk → grounded
   - Otherwise: fires `grounding_fail` WARN (severity="warn", action="hitl") — **NOT a block**
   - This does NOT record WHICH chunk a claim grounded to.

2. **LLM grounding judge** (`OutputGuardrail.llm_grounding_check`, local_guardrail.py:417–553):
   - Splits answer into up to 5 sentences; builds context block from chunks
   - Asks LLM: SUPPORTED/NOT_SUPPORTED per sentence
   - Returns `GuardrailHit(rule_id="llm_grounding_fail", severity="warn", action="hitl")` when `unsupported/checked > threshold`
   - Details: `{"checked": N, "unsupported": M, "ratio": float, "threshold": float}`
   - **Does NOT record which chunk grounded which claim** — only aggregate ratio
   - verdict is **WARN-ONLY** — evidence: severity="warn", action="hitl" (NOT "block")

3. **Regex output rules** (system_prompt_leak, secret_scanner) — block on severity="block"

**Enforce vs Warn-only evidence** (local_guardrail.py:538–552):
```python
if ratio > threshold:
    grounding_fail_total.inc()
    return GuardrailHit(
        rule_id="llm_grounding_fail",
        severity="warn",      # ← WARN, not "block"
        action="hitl",        # ← human-in-the-loop flag, not block
        ...
    )
```
The grounding check is **warn-only** — it adds to `guardrail_flags` but does NOT substitute the answer unless `severity="block"` hits appear. The LLM answer reaches the user even if grounding ratio fails.

**Backward-verify link (answer → chunk_id)**:
There is NO mapping stored from answer claims/sentences back to specific chunk_ids. The LLM grounding judge knows which chunks were checked (the full `graded_chunks` list) but only emits an aggregate ratio, not a per-sentence attribution.

---

## 4. PERSIST NODE

**File**: `src/ragbot/orchestration/nodes/persist.py`

**What the persist node writes**:

1. **Audit event** `query_completed` (persist.py:221–235):
```python
await _audit(state, "query_completed", {
    "answer_type": str,          # "answered" / "no_context" / "blocked"
    "answer_chars": int,         # length of answer text
    "model_used": str,           # resolved model name
    "intent": str,
    "graded_chunks": int,        # count of graded chunks
    "top_score": float,          # max rerank score
    "tokens_prompt": int,
    "tokens_completion": int,
    "cost_usd": float,
})
```
**NOT in this event**: chunk_ids, answer text, the actual chunk contents, the prompt text.

2. **`_persist_meta` state key** (persist.py:237–242):
```python
return {"_persist_meta": {"context_chars": int, "context_chunks": int}}
```
Available in `GraphState` for downstream use, but not written to DB by the persist node itself.

3. **Semantic cache write** (background task, persist.py:197–213):
   - Stores `CachedResponse(answer=answer, citations=citations, model_name=..., chunks=chunks_snapshot)`
   - `chunks_snapshot` is a compact tuple of up to 8 graded chunks: `{document_name, source_url, chunk_index, score, content[:2000]}` — truncated, no `chunk_id` in this snapshot.

4. **What writes to DB** happens in the chat worker callbacks (`callbacks.py`), AFTER graph completes:
   - `request_log_repository.finalize_request_log(...)` — writes `request_logs.answer_hash`, `citations` JSONB, `request_chunk_refs` rows (chunk_id, rank, score for each graded chunk)
   - `monitoring_log` INSERT — timing/token summary
   - `messages` table — answer text stored via message_repository

---

## 5. WHAT'S ALREADY LOGGED — Complete Field Inventory

### `pipeline_audit_logger` → JSONL file (PipelineAuditLogger)

**Written to**: `reports/pipeline_audit_{bot_id}_{YYYYMMDD}.jsonl`  
**DB vs structlog**: JSONL flat file only — NOT written to DB. Enabled only if `RAGBOT_PIPELINE_AUDIT_ENABLED=true` (default OFF).

**Events and fields per event** (collected from all `_audit(...)` call sites):

| Event | Key Fields |
|---|---|
| `generate_started` | `context_chunks`, `context_chars`, `answer_already_set` |
| `action_state_loaded` | `enabled`, `has_service_locked`, `slots_filled_count` |
| `adaptive_context_pruned` | `before`, `after`, `top_score` |
| `llm_purpose_resolved` | `intent`, `purpose` |
| `refuse_short_circuit_fired` | `template_source`, `template_chars` |
| `grade_executed` | `relevant`, `irrelevant`, `ambiguous`, `graded_kept`, `retrieval_adequate`, `fallback_used`, `iterations`, `intent_corrected` |
| `query_completed` | `answer_type`, `answer_chars`, `model_used`, `intent`, `graded_chunks` (count), `top_score`, `tokens_prompt`, `tokens_completion`, `cost_usd` |
| `cache_check` | `hit`, `reason` |

**ALL events** share: `ts`, `iso`, `stage="query"`, `bot_id`, `request_id`.

### `model_invocations` table (DB)

Per LLM/embed/rerank call: `invocation_id`, `message_id`, `record_request_id`, `record_tenant_id`, `purpose`, `provider`, `model_id`, `user_prompt_hash`, `full_payload_hash`, `response_hash`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `duration_ms`, `status`, `finish_reason`.

**Gap**: `user_prompt_hash` is hash of the QUERY only (not the assembled prompt with chunks). No chunk provenance here.

### `request_logs` table (DB)

Per request: `question_hash`, `answer_hash`, `citations` (JSONB — list of `{chunk_id, score, quote?, document_name}`), `model_name`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `duration_ms`, `status`.

### `request_chunk_refs` table (DB)

Per (request, chunk): `record_request_id`, `record_chunk_id` (UUID FK → `document_chunks.id`), `rank`, `score`. Written from `graded_chunks` AFTER generate() completes (callbacks.py:200–224).

**Note**: These are the CRAG-graded chunks, not necessarily the exact set that entered the LLM prompt (generate() may drop more chips during assembly).

### `request_steps` table (DB)

Per pipeline step: `step_name`, `duration_ms`, `model_used`, `input_tokens`, `output_tokens`, `cost_usd`, `metadata_json` (JSONB).

For `step_name="prompt_build"`:
`metadata_json = {context_chars, history_msgs, context_chunks (count), compressed, context_cap, context_chunks_dropped, ...}`

For `step_name="generate"`:
`metadata_json = {model_used, prompt_tokens, completion_tokens, cost_usd, first_token_ms?}`

### `guardrail_events` table (DB, via `LocalGuardrail._persist`)

Per guardrail hit: `message_id`, `tenant_id`, `request_id`, `guardrail_type`, `rule_id`, `severity`, `action_taken`, `details` (JSONB).

For `rule_id="llm_grounding_fail"`: `details = {checked, unsupported, ratio, threshold, path}`.  
For `rule_id="grounding_fail"` (regex): `details = {retrieved_count}`.

---

## 6. GAP ANALYSIS FOR DEBUG

### What Exists NOW vs Missing

| Debug Goal | Status | Evidence |
|---|---|---|
| "Which chunks were in the LLM prompt?" | **PARTIAL** — `request_chunk_refs` has CRAG output, but generate() may drop more; exact prompt chunk set NOT stored | generate.py:524–528, callbacks.py:200–224 |
| "What was the exact prompt text?" | **MISSING** — only `user_prompt_hash` (query-only hash) stored | invocation_logger.py:151–156 |
| "What answer text did the LLM produce?" | **AVAILABLE** — `messages.content` + `request_logs.answer_hash` | models.py:285, callbacks.py:208 |
| "How many chunks were in the prompt vs graded set?" | **PARTIAL** — `request_steps.metadata_json["context_chunks"]` (count only), `request_chunk_refs` (graded set count) | generate.py:617, callbacks.py:215 |
| "What were the chunk scores?" | **AVAILABLE** — `request_chunk_refs.score` | models_monitoring.py:214–224 |
| "Did grounding check fire?" | **AVAILABLE** — `guardrail_events.rule_id IN ('grounding_fail', 'llm_grounding_fail')` + details | local_guardrail.py:926–934 |
| "Which claim grounded to which chunk?" | **MISSING** — grounding judge only emits aggregate ratio | local_guardrail.py:538–552 |
| "Was the gold chunk dropped before the LLM call?" | **NOT RECOVERABLE** — generate() drops after CRAG; no log of exact surviving set | generate.py:510–523 |
| "Token counts per LLM call?" | **AVAILABLE** — `model_invocations.prompt_tokens/completion_tokens` | models_invocation.py:107–108 |
| "Was it a cache hit?" | **AVAILABLE** — `model_invocations.cached`, `request_logs.model_name=="cache_hit"` | invocation_logger.py:94 |

### What's Needed to Close the Gap

To achieve full backward verification ("did the LLM receive the gold chunk?"), these additions are needed:

1. **Capture `chunk_ids_allowed` set in the `generate_started` or `prompt_build` step metadata**:
   - Currently: only `context_chunks` (count) stored at generate.py:622
   - Needed: the actual UUIDs — add `chunk_ids_in_prompt: list[str]` to `pb_ctx.set_metadata()`

2. **Store `chunk_ids` in the `query_completed` audit event**:
   - Currently: `graded_chunks` is only a count in persist.py:230
   - Needed: emit the actual chunk_id list

3. **Record per-claim grounding attribution in the LLM judge**:
   - Currently: `llm_grounding_check` only returns aggregate `{checked, unsupported, ratio}` (local_guardrail.py:538–552)
   - Needed: a `verdicts` list mapping `claim_index → chunk_ids that supported/didn't support it`

4. **Write `request_chunk_refs` from the POST-GENERATE chunk set**, not the pre-generate graded set:
   - Currently: callbacks.py writes `state["graded_chunks"]` AFTER the full graph — these are the pre-generate CRAG chunks
   - The generate node may have dropped some (chars cap, token opt); no FK to distinguish the "entered prompt" set from the "graded but dropped" set
   - Needed: a separate `prompt_chunk_refs` or a boolean flag `in_prompt` on `request_chunk_refs`

---

## ANSWER PROVENANCE CHAIN

### Given a Wrong Answer: What Can You Reconstruct TODAY?

**Given**: `message_id = X`, `answer_hash = H`

**Step 1 — Identify the request**:
```sql
SELECT request_id, answer_hash, model_name, citations, status
FROM request_logs
WHERE message_id = X AND record_tenant_id = <tid>;
```
→ Get `request_id`, `citations` JSONB (chunk_id + score + quote), `model_name`.

**Step 2 — Recover the answer text**:
```sql
SELECT content FROM messages WHERE id = <message_id>;
```
→ Raw answer text IS available.

**Step 3 — Find which chunks were graded (CRAG output)**:
```sql
SELECT rcr.record_chunk_id, rcr.rank, rcr.score,
       dc.metadata_json, dc.chunk_type
FROM request_chunk_refs rcr
JOIN document_chunks dc ON rcr.record_chunk_id = dc.id
WHERE rcr.record_request_id = <request_id>
ORDER BY rcr.rank;
```
→ Ordered graded chunk set with scores. These are chunks that passed CRAG.

**Step 4 — Find the LLM call details**:
```sql
SELECT purpose, model_id, prompt_tokens, completion_tokens, cost_usd,
       user_prompt_hash, full_payload_hash, response_hash, finish_reason, duration_ms
FROM model_invocations
WHERE record_request_id = <request_id> AND purpose = 'generation';
```
→ Token counts, model, timing. `user_prompt_hash` = hash of query only (cannot verify context).

**Step 5 — Check if grounding fired**:
```sql
SELECT rule_id, severity, action_taken, details
FROM guardrail_events
WHERE request_id = <request_id>;
```
→ Shows any grounding_fail / llm_grounding_fail events with ratio.

**Step 6 — Check pipeline timing/step metadata**:
```sql
SELECT step_name, model_used, input_tokens, output_tokens, duration_ms, metadata_json
FROM request_steps
WHERE record_request_id = <request_id>
ORDER BY step_order;
```
→ `step_name='prompt_build'` → `metadata_json.context_chunks` (count), `context_chars`, dropped counts.
→ `step_name='generate'` → `metadata_json.model_used`, tokens.

---

### The MISSING LINK

**The critical gap**: After Step 3, you have the CRAG-graded chunk set. After Step 6, you know how many chunks entered the prompt. **But you CANNOT recover WHICH specific chunk_ids were dropped by `generate()`'s chars-cap/token-opt/adaptive-context passes** and which actually entered the `<documents>` block seen by the LLM.

If the gold chunk was dropped between CRAG output and the LLM call (e.g., it had a high rerank score but large text that exceeded the chars cap after higher-scored shorter chunks), you cannot confirm this from the current audit trail.

**Reconstruction formula**:
```
TODAY you can reconstruct:
  ✅ graded chunk IDs + scores (request_chunk_refs)
  ✅ answer text (messages.content)  
  ✅ answer hash (request_logs.answer_hash)
  ✅ citation list (request_logs.citations)
  ✅ grounding ratio (guardrail_events.details)
  ✅ token counts per LLM step (model_invocations)
  ✅ count of chunks that entered prompt (request_steps[prompt_build].metadata_json)
  ✅ whether grounding fired (guardrail_events)

  ❌ exact set of chunk_ids in the LLM prompt (NOT stored)
  ❌ full assembled prompt text with <documents> block (NOT stored — only query hash)  
  ❌ per-claim grounding attribution (answer claim → grounding chunk_id)
  ❌ which chunks were dropped INSIDE generate() vs which were in CRAG output
```

**Minimum addition needed** for backward verification:
Add `chunk_ids_in_prompt: list[str]` to `request_steps[prompt_build].metadata_json` at `generate.py:617–629`. This single field closes the primary gap.

---

## SUMMARY TABLE

| Component | File | Key Location | What It Does |
|---|---|---|---|
| Prompt assembly + chunk formatting | `nodes/generate.py` | lines 441–630 | Builds `<documents>` block; tracks `chunk_ids_allowed` in memory only |
| Context chars cap (may drop chunks) | `nodes/generate.py` | lines 510–523 | Drops tail chunks; dropped count in step metadata, NOT which chunk_ids |
| LLM invocation wrapper | `query_graph.py` | lines 1119–1335 | Calls LLM; stores only hashes in `model_invocations` |
| Answer + token capture | `nodes/generate.py` | lines 753–801 | Returns `answer`, tokens, `valid_citations` to state |
| Post-hoc attribution | `nodes/generate.py` | lines 809–821 | Adds top-scored chunk to citations if none found |
| Regex grounding check | `local_guardrail.py` | lines 367–414 | WARN-ONLY; no chunk→claim map |
| LLM grounding judge | `local_guardrail.py` | lines 417–553 | WARN-ONLY; emits aggregate ratio only |
| Persist node audit event | `nodes/persist.py` | lines 221–235 | `query_completed` — counts only, no chunk_ids |
| request_chunk_refs writes | `callbacks.py` | lines 200–224 | CRAG output chunks (pre-generate-drop); FK to document_chunks |
| JSONL audit file | `pipeline_audit_logger.py` | lines 138–209 | Append-only JSONL; default OFF; no chunk_ids in events |
| model_invocations table | `models_invocation.py` | lines 72–119 | Per-call hashes + tokens; no raw prompt/answer |
