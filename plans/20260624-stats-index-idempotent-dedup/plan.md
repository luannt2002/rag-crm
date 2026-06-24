# [T2-CostPerf] Stats-index idempotent write — kill duplicate entities (fix chuẩn)

> Root cause (verified, live SQL): `document_service_index` accumulates duplicate entities within ONE
> active document (xe `389025d0` ×3.00 = 516/172; spa ×4). The stats-index DELETE is gated on
> `is_reindex` ([ingest_stages_final.py:465]) while the INSERT (line 479) is unconditional. For a
> first-time `doc_id` (`is_reindex = existing_doc_id is not None` = False) the path can still run more
> than once under Redis-Stream **at-least-once** redelivery / worker retry → each pass inserts a full
> copy with NO preceding delete. NOT a doc-delete-orphan bug: FK `record_document_id → documents(id)
> ON DELETE CASCADE` exists, orphans = 0.

## Evidence (rule#0 — SỰ THẬT, runtime-verified)
- `SELECT record_document_id, count(*), count(DISTINCT entity_name), mult ...` → 8/10 docs mult ≥ 1.49; xe = 3.00, several = 2.00 exact (= N retries).
- FK: `document_service_index_record_document_id_fkey ... ON DELETE CASCADE` (deltype=c); orphans = 0.
- Gate: `ingest_stages_final.py:465 if is_reindex:` wraps only the delete; insert at 479 unconditional.
- `is_reindex = existing_doc_id is not None` (ingest_core.py:419).

## Fix (expert, idempotent-write — domain-neutral, zero-hardcode)
### Stage 1 — CODE: unconditional delete-before-insert  [main session, TDD]
- `ingest_stages_final.py`: remove the `if is_reindex:` gate around `delete_by_document(doc_id)` so the
  delete ALWAYS runs immediately before `_insert_stats_index`. On a fresh doc it deletes 0 rows (cheap).
  Keep the best-effort try/except (BLE001) — but a delete failure must NOT then insert duplicates, so on
  delete failure: log + SKIP the insert for this pass (fail-safe: better miss-this-pass than duplicate;
  next successful ingest re-populates). Update the comment to state the idempotency invariant.
- TDD: `tests/unit/...` — assert that calling the stats-persist path twice for the same doc_id with
  `is_reindex=False` results in exactly ONE set of rows (delete called each pass). Failing first.

### Stage 2 — DATA: one-time live dedup cleanup  [tracked script]
- `scripts/db/dedup_stats_index.py` — keep the newest row per `(record_document_id, entity_name,
  price_primary, price_secondary)`, delete the rest; report per-doc before/after. Idempotent, dry-run
  default + `--apply`. (Derived index, not content — but still tracked in git, no ad-hoc psql.)
- Run `--apply` against prod; verify multiplier → 1.00 for all docs.

## Verification gate
- TDD green (idempotency test) · ruff HEAD==NOW · domain-neutral grep 0.
- Live: every doc multiplier = 1.00 (`count(*) == count(DISTINCT entity_name)` per doc, modulo legit
  same-name different-price).
- Re-run xe `265/50R20` query — still returns both variants (no regression from dedup).
- HALLU=0 unaffected (dedup only removes redundant copies).
