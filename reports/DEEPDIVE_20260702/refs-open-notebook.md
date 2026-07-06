# DEEPDIVE — `_external_refs/open-notebook` (lfnovo/open-notebook)

- **Slug**: refs-open-notebook
- **Date**: 2026-07-02
- **Snapshot**: single squashed commit `cac4e01975cab2dd65898f547d706f64abb482dc` (2026-06-21, "feat: add 'Refresh content' action for web-link sources (#959)") — `git log -1` in `_external_refs/open-notebook`
- **Scope read**: full backend (`open_notebook/` + `api/` + `commands/` = 19,866 LOC Python per `wc -l`), all 15 SurrealQL migrations, all 5 prompt templates, 1 frontend file (citation parser) read deliberately because it closes the provenance loop. Frontend otherwise skipped (38,894 LOC TS).
- **Method**: direct file reads; every claim below carries `file:line` evidence. FACT = read in code. HYPOTHESIS = labeled explicitly; nothing here was executed at runtime.

---

## 0. What Open Notebook is (one paragraph)

Open-source Notebook LM alternative: single-user, privacy-first research assistant. Notebooks contain Sources (files/URLs/text), Notes, and LLM-generated Insights; content is extracted by the external `content-core` library, chunked with LangChain splitters, embedded via the Esperanto multi-provider abstraction, stored in SurrealDB (graph DB with built-in BM25 + cosine), and consumed through two distinct answer flows: **Chat** (user-curated full-content context, no retrieval) and **Ask** (agentic multi-query RAG with inline record-ID citations). Stack: FastAPI + LangGraph + SurrealDB + surreal-commands job queue (root `CLAUDE.md` architecture diagram).

Relevance to ragbot: it is the cleanest small-scale reference for **multi-doc organization (notebook = curated doc collection)** and **ID-based citation/provenance**, and a counter-example on multi-tenancy, hardcoding, and retrieval sophistication.

---

## 1. Source/document model — multi-doc organization

### 1.1 Graph model: doc ⟷ collection many-to-many (FACT)

- `DEFINE TABLE reference TYPE RELATION FROM source TO notebook` — sources link to notebooks via graph edges, not FK columns (`open_notebook/database/migrations/1.surrealql:57-59`). Notes link via `artifact` edges (1.surrealql:61-63); chat sessions via `refers_to` (3.surrealql:4-6, widened in 8.surrealql to refer to notebooks OR sources).
- One source may belong to **multiple notebooks**: `SourceCreate.notebooks: Optional[List[str]]` (`api/models.py:287-289`), and the create endpoint loops `await source.add_to_notebook(notebook_id)` per notebook (`api/routers/sources.py:391-392`).
- Sources are **first-class independent entities**; notebooks are views over them. Retrieval (`fn::vector_search`, `fn::text_search`) operates on the global source/note space, not per-notebook (see §5.4).

### 1.2 Shared-vs-exclusive delete semantics (FACT — pattern worth stealing)

- `Notebook.get_delete_preview()` returns `{note_count, exclusive_source_count, shared_source_count}` by counting, per source, references to OTHER notebooks (`open_notebook/domain/notebook.py:154-202`, SurrealQL `count(->reference[WHERE out != $notebook_id].out) as assigned_others` at :180).
- `Notebook.delete(delete_exclusive_sources: bool)`: always deletes notes, always unlinks all sources, deletes only sources exclusive to this notebook and only when opted in (`notebook.py:204-296`).
- Belt-and-suspenders orphan cleanup: a DB event `source_delete` also deletes `source_embedding` + `source_insight` rows when a source is deleted (`1.surrealql:52-55`), **and** `Source.delete()` deletes them app-side plus the uploaded file (`notebook.py:582-620`).

**Ragbot applicability**: ragbot binds documents to `record_bot_id` 1:1. If documents are ever shared across bots/workspaces within a tenant (same corpus, multiple channel bots), the delete-preview + exclusive/shared distinction is the right API shape for `DELETE /api/ragbot/documents/*` — return the blast radius before destructive ops.

### 1.3 Derived artifacts as first-class retrievable records (FACT)

Three record types are all searchable with independent identity:

| Table | Content | Embedding shape | Evidence |
|---|---|---|---|
| `source_embedding` | one chunk | vector per chunk, `{source: record<source>, order: int, content, embedding}` | 1.surrealql:16-20 |
| `source_insight` | LLM transformation output (summary, key-points…) per source | ONE mean-pooled vector per insight | 1.surrealql:22-26; `commands/embedding_commands.py:283-340` |
| `note` | human or AI note | ONE mean-pooled vector per note | 1.surrealql:35-42; `embedding_commands.py:188-243` |

Insights are produced by the transformation graph during ingest fan-out (`open_notebook/graphs/source.py:143-181`) or on demand (`POST /sources/{id}/insights`, 202 + job id, `api/routers/sources.py:1001-1053`), and an insight can be promoted to a Note (`SourceInsight.save_as_note`, `notebook.py:342-351`).

**Ragbot applicability**: this is "RAPTOR-lite" — a summary layer retrieved ALONGSIDE raw chunks in the same vector query (union in `fn::vector_search`, migrations 3/4/9). Ragbot's ingest enrichment currently enriches chunks; storing per-document LLM insights as separately-embedded, separately-citable rows (with their own `record_*` id) is a cheap coverage lift for "what is this doc about"-class questions where chunk-level cosine misses.

---

## 2. Ingest pipeline

### 2.1 One canonical funnel, two execution modes (FACT)

`POST /sources` accepts multipart (file) or JSON, with `type ∈ {link, upload, text}` mapped to a single `content_state` dict `{url | file_path | content}` (`api/routers/sources.py:320-357`). Everything funnels into ONE command `process_source` executing ONE LangGraph `source_graph`:

```
content_process (extract via content-core, output_format=markdown)
  → save_source (update Source row, optional vectorize() job)
  → conditional fan-out transform_content per transformation (LangGraph Send)
```
(`open_notebook/graphs/source.py:184-200` graph wiring; :34-107 extraction node; :143-159 fan-out.)

- **Async mode** (`async_processing=true`): create placeholder Source (`title="Processing..."`) → link to notebooks immediately for UI → submit job → return `command_id`, `status="new"` (`sources.py:369-434`).
- **Sync mode**: same command executed via `execute_command_sync` inside `asyncio.to_thread` with a 300 s timeout (`sources.py:453-491`).
- The source row carries a **`command` field (FK to the job record)**; list/detail endpoints `FETCH command` to render status inline (`sources.py:196-204`, `open_notebook/domain/notebook.py:362-425`).

### 2.2 Soft-failure sentinel detection (FACT — direct match to ragbot's canonical-ingest-flow rule)

`content-core` signals extraction failure by RETURNING `title="Error"` + content prefixed `"Failed to extract content:"` instead of raising. The node detects that sentinel and raises `ValueError` so the job is marked `failed` and the source becomes retryable, instead of persisting the error string as a "completed" document (`open_notebook/graphs/source.py:82-91`). Empty-content and YouTube-no-transcript get dedicated actionable errors (:93-105).

Companion fix in the command layer: permanent `ValueError`s are **re-raised** (not returned as `success=False` payload) because the queue's `is_success()` checks job status, not payload — returning a failure payload used to mark the job `completed` and hide extraction failures (`commands/source_commands.py:141-149` comment).

**Ragbot applicability**: both halves matter — (a) sniff extractor error-as-content sentinels, (b) make sure the worker's failure signal is the one your status API actually reads. Ragbot's document state machine should assert "failed job ⇒ document status failed", never "completed with error body".

### 2.3 Retry + refresh reuse the same pipeline (FACT)

`POST /sources/{id}/retry` reconstructs `content_state` from the persisted `Asset` (file_path → url → full_text fallback), refuses when a job is already `running|queued`, and resubmits `process_source` with `embed=True` (`api/routers/sources.py:823-951`). The asset is persisted **before** processing precisely so retry is possible after failure (`sources.py:374-381` comment). The HEAD-commit "Refresh content" feature re-fetches web-link sources through the same `process_source` command with no transformations to avoid duplicate insights (commit message of `cac4e01`).

**Ragbot applicability**: retry-from-persisted-asset is a completeness benchmark for ragbot's ingest — a failed URL/document ingest should be re-runnable server-side from stored inputs without the caller re-uploading (idempotent with `X-Idempotency-Key`).

### 2.4 Type detection (FACT — weaker than ragbot's mandate)

Binary-format detection is delegated wholesale to the `content-core` library ("50+ file types", root `CLAUDE.md` "Content Processing"); in-repo detection only classifies extracted TEXT for chunking: extension map (~35 ext) primary, regex heuristics fallback, heuristics may override a PLAIN extension only at confidence ≥ 0.8 (`open_notebook/utils/chunking.py:136-192, 322-362`). There is **no byte-sniff** in this repo. Ragbot's mime→ext→byte-sniff order is strictly stronger; nothing to import here except the confirmation that "metadata refines, heuristics rescue" layering is the industry norm.

---

## 3. Chunking

All in `open_notebook/utils/chunking.py`:

1. **Token-based sizing** with env-config: `CHUNK_SIZE` default 400 tokens, floor 100; `CHUNK_OVERLAP` default 15% of size; `MIN_CHUNK_SIZE` default 5 tokens (:33-118). Measured with tiktoken `o200k_base` (`token_utils` per `utils/CLAUDE.md`).
2. **Rationale documented**: 400 leaves ~20% headroom under 512-token BERT-family embedders to absorb tokenizer mismatch (o200k vs WordPiece), splitter overshoot, special tokens (`open_notebook/utils/CLAUDE.md`, "Default chunk size" quirk). A crisp, transferable calibration argument.
3. **Structure-aware primary split**: `MarkdownHeaderTextSplitter` (h1-h3, `strip_headers=False`) / `HTMLHeaderTextSplitter` per detected type; plain text uses `RecursiveCharacterTextSplitter` with `length_function=token_count` and separators `["\n\n","\n",". ",", "," ",""]` (:365-395, 448-468).
4. **Secondary chunking**: header-split chunks exceeding CHUNK_SIZE are re-split with the plain splitter (:398-415, 470-472).
5. **Min-chunk filter with never-empty guarantee**: drops sub-5-token fragments ("punctuation-only chunks produce null embeddings on llama.cpp's OpenAI-compatible endpoint and crash parsing" — :88-96 docstring), but only when >1 chunk exists and ≥1 survives: "never return an empty list because of this filter" (:483-491).

**FACT vs ragbot**: no atomic-block concept, no table-awareness, no header re-attachment on secondary split (a Markdown section split into 3 sub-chunks loses its heading on sub-chunks 2-3 because `MarkdownHeaderTextSplitter` keeps headers only on the first piece — code shape at :457-472; runtime consequence = HYPOTHESIS, not executed). Ragbot's AdapChunk contract is ahead. The importable ideas are #2 (headroom calibration), #5 (never-zero-chunks invariant — same as ragbot's `template-per-doctype-chunking` rule), and the documented reason for dropping micro-chunks.

---

## 4. Embedding

All in `open_notebook/utils/embedding.py` + `commands/embedding_commands.py`:

1. **Job-queue-first**: every embed is a fire-and-forget command (`Note.save()` auto-submits `embed_note`, `notebook.py:636-659`; `Source.vectorize()` submits `embed_source`, `notebook.py:477-523`; insights chain `create_insight` → `embed_insight`, `embedding_commands.py:731-822`). Rationale in docstring: prevents HTTP connection-pool exhaustion on large docs (`notebook.py:481-490`).
2. **Retry policy = blocklist, not allowlist**: `retry={max_attempts: 5, wait_strategy: exponential_jitter, wait_min 1, wait_max 60, stop_on: [ValueError, ConfigurationError]}` — retry EVERYTHING except validation/config errors (`embedding_commands.py:173-187` and identical blocks on each command; ingest uses max_attempts 15 / wait_max 120 for SurrealDB transaction-conflict queues, `source_commands.py:49-59`).
3. **Idempotent re-embed**: `embed_source` DELETEs existing `source_embedding` rows then bulk-INSERTs fresh ones with `order` index (`embedding_commands.py:413-418, 453-465`); count mismatch between chunks and returned embeddings is a hard error (:447-451).
4. **Batching + per-batch retry**: batch size env-config default 50 ("provider limits vary; CPU-only local endpoints need smaller batches" :24-42), 3 attempts per batch (:174-203).
5. **Mean pooling for over-size single-vector items**: normalize each → mean → normalize result (`embedding.py:55-108`); used so notes/insights of arbitrary length still get exactly one vector (`embedding.py:209-274`).
6. **Rebuild = coordinator command** submitting one job per item, itself retry-free, with modes `existing|all` and per-type includes (`embedding_commands.py:898-1063`); legacy command names kept registered so upgraded workers can drain pre-1.6 queues without crashing (`embedding_commands.py:504-713` — an explicit queue-compat pattern).
7. **Mixed-dimension tolerance**: `fn::vector_search` filters `embedding != none AND array::len(embedding)=array::len($query)` (`9.surrealql`, all three sub-queries) so a half-migrated corpus (old 1536-dim + new 1024-dim rows) degrades to "old rows invisible" instead of a query-killing dimension error.

**Ragbot applicability**: #7 is directly relevant — ragbot hit exactly this bug class in the Jina→ZE migration (memory: `feedback_v2_bug_lessons`, `feedback_threshold_drift_post_migration`); a `vector_dims(embedding)=expected` guard in pgvector retrieval SQL is a one-line safety net during re-embeds. #2's blocklist retry and #6's drain-legacy-queue registration are solid worker-ops patterns for the Redis Streams document worker.

---

## 5. Retrieval

### 5.1 Two DB-side functions (FACT)

Search is implemented **inside SurrealDB** as stored functions, versioned via migrations:

- `fn::text_search($query_text, $match_count, $sources, $show_notes)` — BM25 (`@1@` operator, analyzer `blank,class,camel,punct + snowball(english), lowercase`, `1.surrealql:64-72`) across SIX sub-searches: source.title, source.full_text, source_embedding.content (chunks), source_insight.content, note.title, note.content; unions all, `GROUP BY` max relevance, returns `search::highlight('`','`',1)` matched text (`4.surrealql` fn::text_search).
- `fn::vector_search($query_vec, $match_count, $sources, $show_notes, $min_similarity)` — brute-force `vector::similarity::cosine` over `source_embedding`, `source_insight`, `note`; threshold + per-table LIMIT, union, GROUP BY max similarity (`9.surrealql`).

### 5.2 Parent-document aggregation with chunk evidence (FACT — pattern worth stealing)

Chunk hits are projected as `source.id as id ... source.id as parent_id`, then the union is `GROUP BY id, parent_id, title` with `math::max(similarity)` and `array::flatten(content) as matches` (`4.surrealql` / `9.surrealql` final RETURN). Net effect: **the API returns parent sources ranked by their best chunk, carrying the matching chunk texts as evidence**. Text search likewise returns the highlighted matching fragments.

**Ragbot applicability**: ragbot retrieves chunk-level (correct for generation). But for its search/listing surfaces and for citation UX, the "dedupe to parent doc, keep max score + evidence chunks" response shape is the standard NotebookLM-style contract; ragbot's `parent_chunk_id` JOIN already enables it cheaply.

### 5.3 Graceful degradation + fail-loud floor (FACT)

`text_search()` catches SurrealDB's `search::highlight` "position overflow" bug (byte positions on multi-byte chunks) and falls back to `vector_search`; if THAT also fails it **raises** rather than returning `[]`, with an explicit comment that an empty list would be indistinguishable from "no matches" and would mask a total search outage (`open_notebook/domain/notebook.py:710-731`). This is precisely ragbot's graceful-degradation-with-fail-loud-floor doctrine (CLAUDE.md claude-mem patterns) implemented in the wild — including the multi-byte/Unicode trigger relevant to Vietnamese corpora.

### 5.4 What's missing (FACTS, for calibration)

- **No vector index**: no MTREE/HNSW anywhere in migrations (grep over `migrations/*.surrealql` — only BM25 SEARCH indexes and plain field indexes, `10.surrealql:5-6`). Cosine is a full-table scan per sub-query. Viable single-user; non-viable at ragbot scale.
- **No hybrid fusion**: text and vector are separate endpoints (`api/routers/search.py:17-58`); no RRF, no reranker, no query rewrite, no CRAG-style grading.
- **No scoping**: neither function takes a notebook filter — search is global across the whole installation; the `ask` graph likewise searches everything (`open_notebook/graphs/ask.py:104`). Single-user assumption; a multi-tenant port MUST add scope keys (ragbot already does with `record_bot_id`).
- `minimum_score` default 0.2, caller-tunable 0–1 (`notebook.py:743`, `api/models.py:38-40`); `limit` default 100 max 1000 (`api/models.py:35`).

---

## 6. Ask flow — agentic multi-search + citations (the flagship RAG pattern)

`open_notebook/graphs/ask.py` (155 lines) is a 3-stage LangGraph:

1. **Strategy node**: LLM renders `ask/entry` prompt → structured JSON `Strategy{reasoning, searches[≤5]{term, instructions}}` via PydanticOutputParser (`ask.py:29-42, 51-80`; cap documented in the Field description :38-41). Each search carries **instructions telling the downstream answering LLM what to extract** — a query plan, not just query strings.
2. **Parallel fan-out**: `Send("provide_answer", …)` per search (`ask.py:83-95`); each runs `vector_search(term, 10, source=True, note=True)`, short-circuits to no answer on 0 results (:104-107), then an answer LLM renders `ask/query_process` over the results.
3. **Final synthesis**: `ask/final_answer` merges per-search answers (:127-143).

Three **independently configurable model roles** — `strategy_model`, `answer_model`, `final_answer_model` — validated up front by the endpoint (`api/routers/search.py:113-156`), streamed as SSE events typed `strategy | answer | final_answer | complete | error` (`search.py:61-110`).

### 6.1 Citation grounding via ID allowlist (FACT — the single most transferable trick)

`prompts/ask/query_process.jinja` instructs inline citations as `[document_id]` with typed prefixes (`source:`/`note:`/`insight:`), explicitly forbids fabricating or mutating IDs, warns not to copy the example IDs, **and then injects the literal allowlist**:

> "## IDs PROVIDED IN THIS QUERY — You have been given the following content ids to work from: {{ids}} — So, if you are citing some document, it should be one of these."

where `ids = [r["id"] for r in results]` is computed by the node (`ask.py:108-110`). The final-answer prompt repeats the exact-ID discipline for the merge step (`prompts/ask/final_answer.jinja`). The chat prompts carry the same citing-instructions block (`prompts/chat/system.jinja`, `prompts/source_chat/system.jinja`).

Downstream, the **frontend closes the loop**: regex `/(source_insight|insight|note|source):([a-zA-Z0-9_]+)/g` parses citations (handling `[a, b]` comma lists and a model-emitted `insight:` alias normalized to `source_insight`) into clickable chips (`frontend/src/lib/utils/source-references.tsx:44-72`).

**Ragbot applicability (T1)**: ragbot's sacred rule #10 forbids injecting text into the LLM prompt app-side — but citation-ID allowlists live in the *bot owner's* `system_prompt`/template space, and app-side **post-hoc validation** of returned citation IDs against the retrieved-chunk ID set (drop/flag citations not in the allowlist, count as observability metric) does not touch answer text. That validation is a HALLU-adjacent metric ragbot currently lacks: "% citations not in retrieved set" is a direct fabricated-provenance detector. The typed-prefix ID scheme (`source:`/`note:`/`insight:`) also lets one citation grammar span heterogeneous artifact types — matching ragbot chunks + FAQ + enrichment records.

### 6.2 Weaknesses (FACT)

- Vector-only: no text-search fallback in ask (commented-out code at `ask.py:101-103`; also called out in `graphs/CLAUDE.md` quirks).
- No verification stage: citations are prompt-enforced only; nothing checks the final answer's IDs server-side (absence of any such check in `ask.py` / `search.py`).
- `max_tokens=2000` hardcoded per node (`ask.py:61, 116, 134`).

---

## 7. Chat flow — the "context, not retrieval" alternative + provenance indicators

### 7.1 User-curated context config (FACT — the NotebookLM interaction model)

Notebook chat does **no retrieval**. The client sends a `context_config` mapping per item: `{source_id: "not in" | "insights" | "full content"}` and `{note_id: "not in" | "full content"}`; the server assembles exactly that (insights = short context, full content = `full_text`) and returns **token + char counts** so the user sees the cost before chatting (`api/routers/chat.py:421-526`; same logic at `api/routers/context.py:12-115`; schema `api/models.py:370-383`). The chat graph then injects notebook name/description + this context + citing instructions into the system prompt (`prompts/chat/system.jinja`; `open_notebook/graphs/chat.py:30-33`).

Their own docs frame this as a deliberate dual mode: "For Chat: sends the entire selected content… For Ask (RAG): search, find relevant pieces, send only those" (`docs/2-CORE-CONCEPTS/ai-context-rag.md`, "Open Notebook's Dual Approach").

**Ragbot applicability (T1/T2)**: a per-document **inclusion policy** (`pinned-always-in-context` / `retrievable` / `excluded`) on ragbot's `documents` rows would reproduce this as config, letting bot owners pin small critical docs (price list, policy) into every prompt while big corpora stay retrieval-only — a coverage lift for exactly the "corpus HAS the answer but retrieval missed" failure class in ragbot's load tests, at bounded token cost (the token-count-preview endpoint pattern keeps it honest).

### 7.2 Context indicators = provenance of what was in the prompt (FACT)

`source_chat` tracks `context_indicators: {sources: [ids], insights: [ids], notes: [ids]}` in graph state — the IDs actually placed into the prompt — and the session API returns them to the client (`open_notebook/graphs/source_chat.py:95-114, 181-187`; response model `api/routers/source_chat.py:44-53, 68-74`). This is **prompt-side provenance** complementing answer-side citations.

**Ragbot applicability**: ragbot logs `chunks_used` in request_steps; exposing the equivalent "context manifest" (chunk/doc IDs actually in the prompt) on the chat API response would make the BE-consumer able to render provenance without trusting LLM citations — pure observability, no sacred-rule conflict.

### 7.3 ContextBuilder: priority-weighted token budgeting (FACT, coarse)

`ContextBuilder` collects typed `ContextItem`s with auto token counts, dedupes by ID, sorts by priority (defaults source=100, insight=75, note=50), and pops lowest-priority items until under `max_tokens` — whole-item eviction, no proration (`open_notebook/utils/context_builder.py:21-56, 305-365`). Weakness: `token_count(str(dict))` counts the Python-dict repr, not the final rendered prompt (:31-35) — budget is approximate (FACT about code; impact HYPOTHESIS).

---

## 8. Model management (brief — pattern already strong in ragbot)

- DB model registry + `DefaultModels` singleton with per-role defaults: chat / transformation / large_context / tools / embedding / STT / TTS (`open_notebook/ai/CLAUDE.md`; provision flow `open_notebook/ai/provision.py`).
- **Token-count-driven escalation**: `token_count(content) > 105_000 → large_context_model`, else explicit `model_id`, else role default; helpful ConfigurationError naming the missing role (`provision.py:19-59`). Threshold hardcoded (their own CLAUDE.md flags it).
- Credentials: per-provider encrypted records (Fernet, key derived by SHA-256 from any passphrase, no default key), DB-first env-fallback for Esperanto compat (`utils/CLAUDE.md` encryption section; `api/CLAUDE.md` credential section).

**Ragbot applicability**: the large-context escalation is a clean **T2 pattern** ragbot lacks: when assembled prompt tokens exceed a per-bot threshold (from `system_config`/`plan_limits`), route to a designated long-context binding instead of truncating harder. Ragbot's binding-purpose architecture (`bot_model_bindings.purpose`) can host a `long_context` purpose without new machinery.

---

## 9. Anti-patterns observed (calibration — what ragbot must NOT copy)

1. **Config clobbering in ingest node**: `content_process` constructs `ContentSettings(...)` with literal defaults (`engine="auto"`, `auto_delete_files="yes"`, hardcoded YouTube language list) instead of `await ContentSettings.get_instance()` (`open_notebook/graphs/source.py:35-51`). Since `RecordModel.__new__` returns the singleton and overwrites fields from kwargs (`open_notebook/domain/base.py:254-267`), the user's DB-configured processing engines appear to be ignored/overwritten on every ingest. FACT for code shape; runtime effect = HYPOTHESIS (not executed). Violates ragbot's zero-hardcode + config-driven rules outright.
2. **Hardcoded magic numbers in hot paths**: 105_000 escalation threshold (`provision.py:23`); `max_tokens=50000` source-chat context and 5000-char `full_text` truncation that silently blinds long docs in per-source chat (`open_notebook/graphs/source_chat.py:71, 210-214`); `max_tokens=2000` in ask nodes (`ask.py:61,116,134`); 8192 in chat (`chat.py:46`); chunk config via env vars requiring restart (`chunking.py:33-118`) not DB.
3. **Sync/async event-loop gymnastics**: LangGraph sync nodes spawn `asyncio.new_event_loop()` inside `ThreadPoolExecutor` to call async provisioning (`chat.py:38-71`, `source_chat.py:62-90, 134-171`); their own `graphs/CLAUDE.md` calls it "fragile". Ragbot's async-first pipeline avoids this class entirely — keep it that way.
4. **Client-supplied prompt context**: `POST /chat/execute` takes `context: Dict[str, Any]` from the request body and puts it straight into graph state → system prompt (`api/routers/chat.py:64-69, 371`). In a single-user tool that's fine; in a multi-tenant headless platform it is prompt-injection-by-design. Ragbot's server-side assembly is correct.
5. **Broad `except Exception` as the default idiom** throughout domain/services (e.g., `notebook.py:42-45, 199-202, 259-262`; `context.py:52-54` swallowing per-item errors) — fails ragbot's broad-except policy; several catches silently `continue`, which can hide missing sources from context with no signal.
6. **No tenancy / auth floor**: single password middleware, "insecure, dev-only" per root `CLAUDE.md` (Authentication section); global search space (§5.4). Confirms ragbot's 4-key + RLS mandate has no counterpart to borrow here.
7. **Duplicated context-assembly logic**: near-identical inclusion-level loops in `api/routers/context.py:26-97`, `api/routers/chat.py:434-506`, and `open_notebook/utils/context_builder.py` — drift risk; ragbot's single-assembler discipline (SysPromptAssembler) is the better shape.

---

## 10. Test health (FACT, brief)

16 test files, 3,572 lines (`wc -l tests/*.py`), pytest: `test_chunking.py` 34 tests, `test_domain.py` 31, `test_embedding.py` 18, `test_graphs.py` 12, plus API-level tests (sources/search/notes/credentials/url-validation). Real behavioral coverage of the chunking/embedding utility layer; no retrieval-quality/eval harness, no HALLU/grounding gates anywhere in the repo (absence confirmed by directory listing). Ragbot's load-test + coverage/faithfulness gates are ahead; nothing to import.

---

## 11. Pattern extraction — ranked for ragbot

| # | Pattern | Evidence | Ragbot tier | Verdict |
|---|---|---|---|---|
| P1 | **Citation-ID allowlist in retrieval prompt + typed ID prefixes + post-hoc parseability** | `ask.py:108-110`, `prompts/ask/query_process.jinja`, `source-references.tsx:44-72` | T1 | ADOPT (allowlist via bot-owner template; app-side citation-validation metric) |
| P2 | **Per-document inclusion policy (pin/full/insights/exclude) + token-cost preview** | `api/routers/chat.py:421-526`, `api/models.py:370-383` | T1 | ADOPT as per-doc config column |
| P3 | **Parent-doc aggregation: dedupe chunk hits to parent, max score + evidence chunks** | `4.surrealql`/`9.surrealql` final RETURN | T1/T2 | ADOPT for search/citation surfaces |
| P4 | **Insights layer: LLM-derived summaries stored as embedded, citable records unioned into retrieval** | `1.surrealql:22-26`, `embedding_commands.py:731-822`, fn::vector_search unions | T1 | EVALUATE (RAPTOR-lite; measure lift first per rule#0) |
| P5 | **Soft-failure sentinel + job-status/payload alignment** | `source.py:82-91`, `source_commands.py:141-149` | ingest robustness | ADOPT (matches existing skill; verify ragbot worker honors it) |
| P6 | **Retry/refresh from persisted asset through the ONE canonical pipeline** | `sources.py:823-951`, commit `cac4e01` | ingest robustness | ADOPT `/documents/{id}/retry` |
| P7 | **Dimension guard in vector SQL (`len(embedding)=len(query)`)** | `9.surrealql` | T2 safety net | ADOPT one-line WHERE during re-embed windows |
| P8 | **Delete-preview (exclusive vs shared blast radius) before cascade delete** | `notebook.py:154-202` | API UX | ADOPT if docs ever shared across bots |
| P9 | **Blocklist retry (`stop_on=[ValueError,ConfigurationError]`, exp-jitter) + legacy-command registration to drain old queues** | `embedding_commands.py:173-187, 504-713` | worker ops | ADOPT semantics in Redis Streams worker |
| P10 | **Token-threshold model escalation to long-context binding** | `provision.py:19-34` | T2 | ADOPT config-driven (NOT the hardcoded 105k) |
| P11 | **Search-mode graceful degradation with fail-loud floor (highlight overflow → vector; both fail → raise)** | `notebook.py:710-731` | T2 | already ragbot doctrine; note the multi-byte trigger for VN text |
| P12 | **Min-chunk-size drop with never-empty guarantee + documented null-embedding rationale** | `chunking.py:88-96, 483-491` | ingest quality | ADOPT the invariant if not already asserted |
| P13 | **Context manifest (`context_indicators`) returned on chat API** | `source_chat.py:95-114`, router :44-53 | observability | ADOPT on ragbot chat response |
| P14 | Whole-item mean-pooled embedding for medium artifacts | `embedding.py:55-108` | T2 | OPTIONAL (doc-level coarse vector for routing/dedup) |

**Explicit non-imports**: global unscoped search (§5.4), client-supplied context (§9.4), env-var-with-restart config (§9.2), sync-node loop spawning (§9.3), brute-force cosine without index (§5.4).

---

## 12. Bottom line

Open Notebook is a **product-shaped, single-user** RAG system: its retrieval core is deliberately simple (brute-force cosine, no fusion, no rerank, no eval), but its **document lifecycle** (one canonical funnel, job-linked status, sentinel failure detection, retry/refresh from persisted assets, idempotent re-embed, exclusive/shared delete semantics) and its **provenance loop** (typed record-ID citations with prompt-side allowlist, context manifests, parent-doc aggregation with chunk evidence, clickable-chip parsing) are complete end-to-end in a way few references are. Ragbot is ahead on retrieval science, multi-tenancy, format detection, and evaluation; the highest-value imports are P1 (citation allowlist + validation metric), P2 (per-doc inclusion policy), P3 (parent-doc citation shape), P6/P5 (retry + sentinel completeness), and P7 (dimension guard) — every one implementable as config/SQL/observability without violating sacred rule #10.
