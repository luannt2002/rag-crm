# ADR: Entity-join — merge multi-sheet fragments into one product entity

Status: Proposed
Date: 2026-06-25
Stream: RAG accuracy — chinh-sach-xe NotebookLM-parity (`reports/RAGBOT_FULL_PIPELINE_TRACE_20260624.md`)

## Context

A catalog bot's corpus is several sheets describing the SAME products from different
angles — a price sheet, a stock sheet, an incoming-shipment sheet, an image/date sheet.
Each row of each sheet becomes one `document_service_index` entity. So one physical
product is scattered across **4–6 separate entities**, each holding only ONE aspect.
Verified (DB) for `LANDSPIDER 165/65R14 79H CITYTRAXX G/P`:

```
[165/65R14 79H CITYTRAXX G/P]            price=∅  attrs={"col_2":"28-thg 11"}            ← incoming
[Lốp xe LANDSPIDER 165/65R14...]         price=∅  attrs={"Mã":"2-R14 165/65 LPD","Kho":..} ← catalog (code, NO price)
[Lốp LANDSPIDER 165/65R14...]            price=∅  attrs={"Ngày về":"28-thg 11"}         ← incoming
[Lốp xe LANDSPIDER 165/65R14...]         price=702000 attrs={"Giá":702000}              ← price
```

The stats lookup returns the matching fragment(s) and the LLM joins them IN CONTEXT — but
only the fragments the keyword happened to match. A query that matches the price fragment
gets no stock; a "26 vs 404" wrong-number happened because the matched fragment lacked the
asked field. P9 (`e3b2cb6`) lets the LLM see each matched fragment's `attributes_json`, and
grounding (`062d6fa`) now blocks a fabricated value — but the fragments are still not a
single coherent record. The deterministic, complete fix is to MERGE the fragments at
ingest into one entity per product.

## Decision

Add an **entity-join** step at ingest finalize, BEFORE `_insert_stats_index`: group the
extracted `ParsedEntity` list by a stable product key and merge each group into one entity
carrying every field (price + stock + code + date + image + arrival), with conflict rules.

- **Join key** = the most specific shared identifier available, resolved generically (no
  brand literal): (a) an explicit code/SKU column value when present (`attributes_json`
  code role), else (b) the normalised tyre-size / spec token shared across fragments, else
  (c) the normalised `entity_name`. The key resolver is locale/role-driven (ties into P9's
  column roles), never a hard-coded corpus field name.
- **Merge** = union of `attributes_json`; price = first non-null (priced fragment wins);
  aliases = union; `entity_name` = the longest/most-complete name in the group.
- **Conflict** (two fragments disagree on the same key→value) = keep both under a
  qualified key + emit a `stats_entity_join_conflict` structured log (never silently pick).

The merged entity is what gets inserted, so one keyword match returns ONE complete record.

## Why this shape (not alternatives)

- **Join at ingest, not at query.** Query-time join (gather fragments per request, merge in
  Python) repeats work every turn and can't dedup across keyword variants reliably. Ingest
  is computed once; the index stays the single source.
- **Not a schema change to a normalized product table.** That re-architects storage and
  breaks the strangler-fig stance. Entity-join is an additive transform on the existing
  `ParsedEntity` list → same `document_service_index` table.
- **Cross-document join (a product spanning 3 sheets = 3 documents).** This is the hard
  part: `_insert_stats_index` runs per-document. The join must run AFTER all of a bot's
  catalog documents are present, OR maintain an upsert-merge keyed by product across
  documents. Decision: a per-bot **post-ingest join pass** (triggered when the last catalog
  doc of a batch goes active) that re-reads the bot's fragments and writes merged entities,
  keeping per-document `delete_by_document` idempotency intact.

## Consequences

- **Highest-leverage accuracy fix** for catalog bots: a single "tồn kho lốp X" or "ngày về
  X" query returns the complete record → answers the field directly, grounding passes on
  the real number (no more "26 vs 404").
- **Hard-to-reverse + surprising:** changes what a stats entity represents (a product, not a
  row) → ADR-gated. Re-ingest of all catalog bots required after rollout.
- **Conflict semantics are the risk:** a wrong join key merges two different products. The
  key resolver MUST be conservative (prefer explicit code; fall back only on exact-normalised
  shared spec) and log every conflict. Pin with a golden multi-sheet fixture.
- **Pairs with P9 + grounding** already shipped: P9 surfaces fields, grounding guards them,
  entity-join makes them complete. Together → NotebookLM-parity on catalog factoids without
  long-context.

## Reversibility

The join is an additive ingest pass writing the SAME table. Disable the pass + re-ingest to
revert to per-row fragments (P9's attributes fallback still resolves code lookups). Gate
behind a per-bot flag `stats_entity_join_enabled` (default OFF until validated), so rollout
is opt-in and reversible per bot.

## Status / next

Proposed — needs approval. Implementation plan to follow in `plans/` once accepted. Depends
on P9 column-role field-isation for the explicit-code join key (strongest key).
