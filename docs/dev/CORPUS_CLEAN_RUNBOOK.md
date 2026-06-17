# Corpus-Clean Runbook (`scripts/corpus_clean.py`)

> Helper for owner-driven corpus hygiene. **Read-only by default**; mutation
> requires explicit `--apply`. Bot identity is the 4-key tuple
> `(record_tenant_id, workspace_id, bot_id, channel_type)`; `--bot-uuid`
> is supported for migration / debugging but requires `--allow-uuid` to
> acknowledge the workspace bypass.
>
> Sacred (CLAUDE.md): the platform never edits bot owner data without
> explicit consent. Owner picks what to keep. The script reports — the
> human decides — owner re-uploads cleaned content and re-embeds.

## Symptoms → Commands → Expected output → Action

| Symptom | Command | Expected output | Owner action |
|---|---|---|---|
| Bot answers with two different prices for the "same" service | `find-conflict-prices` | JSON list of `{service_key, chunk_id, document_name, prices, excerpt}` rows where ≥2 chunks share a service-key prefix but report different prices | Owner picks the canonical price, edits the source docs, re-uploads with `re-embed-bot --apply` |
| Bot returns the same answer multiple times | `find-duplicate-chunks` | JSON list of duplicate `content_hash` groups with all chunk UUIDs | Decide whether to delete the extras (admin SQL) or merge into one doc + re-ingest |
| Vector retrieval misses a document the owner just uploaded | `find-empty-embeddings` | List of chunks with `embedding IS NULL` | `re-embed-bot --apply` to backfill |
| Owner's new document doesn't help PASS rate | `validate-rag-friendly --doc-id <uuid>` | Per-doc score: `word_count`, `heading_count`, `has_explicit_numbers`, plus a list of findings | Apply the rules from `docs/templates/RAG_FRIENDLY_SHEET_TEMPLATE.md` (R1 heading, R6 explicit numbers, target word band) |

## Quick reference

```bash
# Use the 4-key (recommended for normal ops):
.venv/bin/python scripts/corpus_clean.py find-duplicate-chunks \
  --record-tenant-id <uuid> \
  --workspace-id <slug> \
  --bot-id <slug> \
  --channel-type web \
  --format md

# Or pass a UUID directly (debugging / migration):
.venv/bin/python scripts/corpus_clean.py find-conflict-prices \
  --bot-uuid <record_bot_id> --allow-uuid

# Re-embed pipeline is delegated to scripts/reembed_bot_corpus.py:
.venv/bin/python scripts/corpus_clean.py re-embed-bot \
  --bot-uuid <record_bot_id> --allow-uuid --apply

# Score one document for RAG-friendliness (per the template):
.venv/bin/python scripts/corpus_clean.py validate-rag-friendly \
  --doc-id <documents.id>
```

## Output format

* Default: JSON to stdout. Schema is `{"header": {...}, "rows": [...]}`.
  `header` always carries `subcommand` + the resolved `record_bot_id`;
  per-subcommand fields document what was searched (e.g. `regex`).
* Markdown: pass `--format md` for a human-readable table — useful when
  a tenant is reviewing on a chat or wiki page.
* Excerpts are trimmed to `DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS` (100 by
  default; tune in `shared/constants.py`).

## What this script does NOT do

* It does not touch `alembic` or schema. New columns, indexes, or
  constraints are out of scope — those go in a real migration.
* It does not call out to the LLM. Service-key bucketing for the
  price-conflict path is a deliberately coarse substring prefix —
  not domain NER. Real disambiguation is the owner's call.
* It does not silently delete chunks. The duplicate / conflict
  reports return chunk UUIDs; deletion is the operator's manual
  follow-up (SQL or admin UI).

## Heuristics & thresholds

All thresholds are constants in `src/ragbot/shared/constants.py`:

| Constant | Default | Meaning |
|---|---|---|
| `DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS` | 100 | Cap for chunk content excerpts in output |
| `DEFAULT_CORPUS_CLEAN_SERVICE_MIN_CHARS` | 8 | Minimum prefix length for the conflict-price service-key bucket |
| `DEFAULT_CORPUS_CLEAN_PRICE_REGEX` | `\d+[\.,]\d{3}(?:[\.,]\d{3})*\|\d+(?:[KkMm])\b\|\b\d{4,7}\b` | Default price extractor (overridable per-call via `--regex`) |
| `DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS` | 250 | Lower bound of the RAG-friendly word band |
| `DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS` | 400 | Upper bound — beyond which a chunk is too long for clean retrieval |

## Tests

* Unit (always run): `tests/unit/test_corpus_clean_helper_logic.py` —
  covers excerpt trimming, regex matching, service-key bucketing,
  RAG-friendly scoring, JSON / markdown output, parser shape.
* Integration (`--run-integration`): `tests/integration/test_corpus_clean_helper.py`
  — seeds a 5-chunk fixture (2 dup, 2 price-conflict, 1 unique-NULL-embedding)
  and exercises every read-only handler against real Postgres, asserting
  on JSON shape and that `--dry-run` does not mutate.

## Reference

* `docs/templates/RAG_FRIENDLY_SHEET_TEMPLATE.md` — the spec the
  `validate-rag-friendly` heuristic encodes (R1 heading, R2 blank lines,
  R3 sheet header row, R6 explicit numbers, etc.).
* `scripts/dedup_chunks_per_bot.py` — Jaccard dedup (different from the
  `content_hash` exact-match dedup here).
* `scripts/reembed_null_chunks.py` / `scripts/reembed_bot_corpus.py` —
  the underlying re-embed pipeline that `re-embed-bot` delegates to.
