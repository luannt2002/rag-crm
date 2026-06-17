# DEEPDIVE Compliance + Structured-Data Gap Audit — 2026-06-17

READ-ONLY audit. Scope: this session's changed chunking/retrieval files + the structured-data (`document_service_index`) architecture for the `chinh-sach-xe` bot.

---

## GOAL A — CLAUDE.md compliance table

| # | Check | Verdict | Evidence (`file:line`) |
|---|---|---|---|
| 1 | ZERO-HARDCODE | **PASS** | All thresholds imported from `shared/constants`. `query_range_parser.py:21-25` imports `RANGE_QUERY_MIN_CONFIDENCE`, `SUPERLATIVE_QUERY_CONFIDENCE`, `SUMMARY_QUERY_PATTERNS_VI`. `grade.py:35-54` imports ~20 `DEFAULT_CRAG_*` constants. `document_stats.py:25` imports `DEFAULT_PRICE_BUCKETS_VND`, `DEFAULT_PRICE_MIN_VND`. `stats_index_repository.py:38` imports `DEFAULT_STATS_INDEX_QUERY_LIMIT`. Currency multipliers (`1_000`, `1_000_000`, `1_000_000_000`) in `number_format.py:46-55` are whitelisted scale factors (the NUMBER STANDARD definition itself — SSoT, not a tunable threshold). `_FUZZY_MARGIN=0.10` (`query_range_parser.py:228`) and `_MIN_BARE_PRICE_VND=1000` (`:236`) are module-level `Final` named constants, not inline magic — borderline (see note). |
| 2 | DOMAIN-NEUTRAL / per-bot | **PASS** | Grep for `medispa\|landspider\|citytraxx\|chinh-sach-xe\|thong-tu\|test-spa\|legalbot\|michelin` across `orchestration/`, `application/`, `shared/` LOGIC = **0 hits in code**. Only 2 hits, both in comments/docstrings: `application/ports/conversation_state_port.py:11,32` (`test-spa-id` in a docstring describing a baseline measurement). No bot-name literal drives any branch. `_HEADER_EXACT_TOKENS` (`document_stats.py:56-63`) and superlative tokens (`query_range_parser.py:182-205`) are generic column-label / NL vocabulary, not brand data. |
| 3 | NO-VERSION-REF | **PASS** | Grep `_v[0-9]\|_legacy\|Sprint\|/v1/\|/v2/` over all 10 changed files = **0 hits**. Note `SUBJECT_DOCUMENT_UPLOAD_STREAM = "document.upload_stream.v1"` (`_21_streaming_upload_wb_2_p1_5.py:27`) is a **wire-protocol topic suffix** (Redis Stream subject), explicitly documented as "not a code version-ref" (`:26`) — matches the existing `SUBJECT_DOCUMENT_*` convention; compliant per the URL/schema carve-out. |
| 4 | STRATEGY + DI | **PASS** | Grep `if.*provider.*==` / `provider == "..."` in `retrieve.py`, `grade.py`, `query_graph.py` = **0 hits**. Grep infra imports (`from ragbot.infrastructure.(reranker\|embedding\|llm\|vector)`) inside the changed orchestration nodes = **0 hits**. All deps threaded in via `functools.partial` kwargs (`query_graph.py:2870-2915`): `vector_store`, `embedder`, `reranker`, `stats_index_repo`, `doc_repo` all injected. `grade.py:60-70` takes `llm`/`model_resolver` as params, never imports a concrete impl. |
| 5 | APP-INJECT / OVERRIDE (sacred #10) | **PASS** | **Synthetic stats chunk** (`query_graph.py:2822-2859`): body is built ONLY from `entity_name` + `price_primary`/`price_secondary` pulled from the SQL rows — `f"{_name}: {int(_price)}"` (`:2845`). **No instruction text, no template, no directive.** Explicitly currency-neutral: emits the raw number only, no "VND" appended (`:2842-2843`). It is surfaced as a `content`/`text` chunk with `source="stats_index"` (`:2852-2858`) — i.e. it enters the pipeline as retrieved CONTEXT, exactly like a corpus chunk, not as a system-prompt addendum. This is pure data grounding, not app-inject. Downstream `grounding_check` still enforces HALLU=0 (`grade.py:96-98` comment). No `oos`/refusal-template literal injected anywhere in the changed files. |
| 6 | BROAD-EXCEPT | **PASS** | Every `except Exception` in changed files carries `# noqa: BLE001` + reason: `vi_tokenizer.py:184,436,454,543`, `retrieve.py:859`. `query_graph.py:2865` and the chunk-fetch try-blocks (`:2801,:2816`) use **narrow** tuples `(OSError, RuntimeError, ValueError, KeyError, AttributeError[, TypeError])` — no broad-except. `ingest_stages_final.py:321` (re-ingest delete) uses `# noqa: BLE001 — delete is best-effort` (correct: best-effort background op). |

**Overall: 6/6 PASS.**

### Note (advisory, not a violation)
- `_FUZZY_MARGIN = 0.10` and `_MIN_BARE_PRICE_VND = 1000` are file-local `Final` constants in `query_range_parser.py:228,236`. They are not inline magic numbers (named + documented), but they are tuning knobs that live in the module rather than `shared/constants.py`. The whitelist tolerates `Final` module constants; however for full SSoT consistency these two could migrate to `shared/constants/` like the sibling `RANGE_QUERY_MIN_CONFIDENCE`. Low priority.

---

## GOAL B — STRUCTURED-DATA gap for `chinh-sach-xe`

### What the structured index currently captures

`ParsedEntity` (`document_stats.py:104-122`) — the in-memory extraction record:
- `name` (entity name, col 0 / first non-numeric)
- `category` (from a preceding heading row)
- `price_primary` (first money column)
- `price_secondary` (second money column)
- `chunk_index`
- `attributes: dict[str, Any]` — **all remaining columns**, keyed by header label (`document_stats.py:229-230`)

`document_service_index` table (alembic 0118, `stats_index_repository.py:10-24`, schema `20260526_0118_stats_index_schema.py:57-73`):
- `entity_name TEXT NOT NULL`, `entity_category TEXT`, `price_primary NUMERIC`, `price_secondary NUMERIC`, `attributes_json JSONB DEFAULT '{}'`
- `record_chunk_id` FK (nullable), `record_document_id` FK
- GIN index on `attributes_json` (`:92-95`) → JSONB attributes ARE queryable

### The `chinh-sach-xe` FAQ table columns vs what we capture

| xe column | n8n need | Captured today? | Where |
|---|---|---|---|
| `question` | — | partial → becomes `entity.name` (col 0) | `_extract_entity_from_row` `document_stats.py:222-227` |
| `code` | (key) | only as an `attributes_json` entry | `:229-230` |
| `productname` | — | as `attributes_json` | `:229-230` |
| `answer` | **`answer` field** | **only as `attributes_json` text**, IF the column survives parsing | `:229-230` |
| `quantity` | **SOURCE-OF-TRUTH stock** | **risk: mis-bucketed as a PRICE** | see below |
| `price` | `price` field | → `price_primary` | `:209-214` |
| `date1` | `date1` field | as `attributes_json` (string) | `:229-230` |
| `date2` | `date2` field | as `attributes_json` (string) | `:229-230` |
| `image` | `image` field | as `attributes_json` (string) | `:229-230` |
| `brand` | `brand` field | as `attributes_json` (string) | `:229-230` |

### GAP analysis — three concrete problems

1. **`quantity` collides with the price extractor.** `_extract_entity_from_row` assigns the **first** money-parseable column to `price_primary`, the **second** to `price_secondary`, and only the **third+** money column to `attributes_json` (`document_stats.py:209-219`). The xe table has BOTH `quantity` (a small integer) AND `price`. A bare `quantity` like `5` or `12` is below `DEFAULT_PRICE_MIN_VND=10_000` (`_21...:45`) so `parse_money_vn` returns `None` for it → it falls through to `attributes_json` as a string. **But** a quantity that happens to be ≥10,000 (bulk stock) would be mis-read as a price. More importantly: **`quantity` is never a first-class queryable numeric column** — it lands in JSONB as a string, so a COUNT_RULE / stock-aware query cannot do `WHERE quantity > 0` efficiently or reliably.

2. **`answer` is not surfaced.** The synthetic stats chunk (`query_graph.py:2830-2846`) emits ONLY `name: price`. It never reads `attributes_json`. So even though the xe `answer` text is stored in `attributes_json`, the stats route **does not hand it to the LLM** — generate only sees `productname: price` lines plus whatever doc-level prose chunks were linked. The pre-authored `answer` (the whole point of an FAQ table) is dropped on the stats path.

3. **COUNT_RULE (return ALL matching, no merge/filter) is not honored.** The query routes are price-shaped: `query_by_price_range` / `top_by_price` / `count_by_price_range` / `list_all_entities` (`stats_index_repository.py:165-414`). There is **no route keyed on `code`/`productname`/`brand`** and no "return every row matching entity X" path that preserves per-record `answer`/`quantity`/`date`/`image`. The MMR-dedup + CRAG grader on the normal path actively MERGE/DROP row-shaped chunks (mitigated for stats route by the `grade.py:99-111` bypass, but the underlying retrieval is still chunk-prose, not record-structured).

### Architecture assessment

**The right architecture for `chinh-sach-xe` is STRUCTURED RECORDS, not prose RAG.** The xe corpus is a relational FAQ/inventory table whose downstream consumer (n8n) expects a typed `results[]` array with `quantity` as source-of-truth and a COUNT_RULE that forbids merge/filter. Prose-chunk RAG fundamentally fights this: chunking splits rows, embedding is non-discriminative on near-identical row shapes, and CRAG/MMR drop "duplicate-looking" records — the exact records the COUNT_RULE says to keep.

The existing `document_service_index` is **80% of the right structure already** — it has per-row entity rows, tenant/bot scoping + RLS (`20260526_0118:97-113`), a flexible `attributes_json` JSONB with a GIN index, and a clean SQL repository. It was built for price aggregation but the table shape generalizes to "structured records." The gap is **field coverage + a record-return route + answer surfacing**, NOT a new table.

---

## Ranked recommendations (concrete)

**R1 (highest — unlocks xe answer correctness).** Make the stats/structured route surface `attributes_json` into the synthetic chunk. Today `query_graph.py:2844-2846` emits only `name: price`. Extend it to emit the captured `answer` (and any owner-configured display fields) per row. Without this, the xe `answer` column — already stored in JSONB — never reaches the LLM. Lowest-effort, highest-impact, fully within sacred #10 (still pure data).

**R2.** Capture `quantity` / `date1` / `date2` / `image` as **named, typed first-class fields** rather than opaque JSONB strings. Two options:
   - (a) Add nullable columns (`quantity NUMERIC`, `attributes_json` keeps the rest) via a new alembic — best for `WHERE quantity > 0` COUNT_RULE filtering and ORDER BY date.
   - (b) Keep them in `attributes_json` but make the ingest extractor write them under **stable, header-mapped keys** and add a JSONB-path query route. The GIN index (`20260526_0118:92-95`) already supports this.
   Decision driver: if stock-aware filtering (`quantity > 0`) is a hot query, prefer (a); if fields are display-only, (b) is cheaper. This is an ADR-worthy schema choice — present both, get owner approval.

**R3.** Add a **record-return route** to `StatsIndexRepository` that selects by `code`/`productname`/`brand` (lookup keys), returns **every** matching row (honoring COUNT_RULE — no dedup), and emits each as a structured `results[]` item carrying `{answer, price, quantity, date1, date2, image, brand}`. This sits alongside the existing price routes; it does NOT replace them. Keep it config-gated per-bot (like `stats_index_race_enabled`) so it stays domain-neutral — the xe bot opts in via `pipeline_config`, the platform default is unchanged.

**R4 (extraction).** The xe table has named headers (`question/code/productname/answer/quantity/price/date1/date2/image/brand`). The current `_extract_entity_from_row` is positional (first-money=price). For multi-numeric tables like xe, drive extraction from the **header labels** so `price` → price and `quantity` → quantity deterministically, instead of "first money column wins." `_HEADER_EXACT_TOKENS` (`document_stats.py:56-63`) already recognizes `quantity`/`qty`/`price` — wire that recognition into column assignment.

**R5 (verify-before-claim — rule #0).** None of the above is validated. Before shipping, build the feedback loop: ingest the actual xe FAQ table → query `document_service_index` via psql → confirm `quantity`/`answer` land where expected → run the n8n prompt against the new `results[]` shape. Do not claim coverage lift without a load-test / DB-row diff. Current state of THIS report's GOAL B is: structural analysis from `file:line` evidence; the runtime behavior on the real xe corpus is **NOT verified here** — it needs an ingest + psql check.

---

## Constraints honored
Every claim above cites `file:line`. No code edited (READ-ONLY). No guessed runtime numbers — where behavior is unverified (xe ingest), it is labeled GIẢ THUYẾT / "needs verification."
