# ADR-0007 — Stats subsystem: PRICE-index → generic ATTRIBUTE-index (domain-neutral, fair to every bot)

- **Status**: Proposed (2026-06-25)
- **Tier**: T1-Smartness. **Stance**: EVOLVE (strangler fig), NOT rewrite.
- **Motivated by**: [[DOMAIN_NEUTRAL_BETRAYAL_AUDIT_20260625]] — 2 systemic betrayals; this ADR closes Betrayal #1.
- **Builds on**: [ADR-0006](0006-column-role-structural-and-custom-vocab.md) (owner declares column meaning; `attributes_json` = generic labelled fields).
- **Enforced by**: `tests/unit/test_domain_neutral_guard.py` (price-coupling ratchet, decreasing-only).

## Context

The structured/numeric subsystem (`document_service_index` + stats route + number parsing + math guardrail) is hardwired to **VND-price commerce**: `price_primary`/`price_secondary` are first-class NUMERIC columns; `query_by_price_range`/`top_by_price`/`count_by_price_range` only operate on them; `parse_money_vn` is the only number parser; `math_lockdown` tags every number `"VND"`; a `DEFAULT_PRICE_MIN_VND=10_000` floor drops smaller numbers at ingest.

This is **unfair to every non-price bot**: a phone bot's "RAM", a real-estate bot's "Diện tích", a legal bot's "số khoản" cannot be range-queried, ranked (max/min), or counted — only price can. The engine elevates ONE industry's concept to first-class. The platform "works today" only because every live bot is a VN price-catalog or legal bot — the coupling is a **latent** fairness failure.

## Decision

**Generalise the stats subsystem from a PRICE index to a generic labelled-ATTRIBUTE index. The engine knows STRUCTURE (entity, label, numeric value, locale-token) — never MEANING (price/VND).** Owners already declare what a column means via `custom_vocabulary["column_roles"]` (ADR-0006); the engine treats every field uniformly.

### Mechanism (additive, backward-compatible — strangler fig)

1. **Numeric-attribute index (new, additive):**
   ```sql
   document_service_index_numeric(
     id, record_index_id FK → document_service_index,
     label TEXT,        -- the corpus header verbatim: "Giá" | "RAM" | "Diện tích" | "Số khoản"
     value NUMERIC,     -- unit-agnostic; NO VND floor (capture every number)
     unit  TEXT NULL    -- the corpus's own unit token if any, NEVER a hardcoded "VND"
   )
   ```
   At ingest, every labelled field whose value parses as a number → one row. The QUERY decides the range; ingest never drops a number by a currency floor.

2. **`price_primary`/`price_secondary` become a backward-compat VIEW** derived from the numeric index where `label` maps to the owner's price-role column(s). Existing spa/xe price queries keep working byte-identically during migration.

3. **Generic range / superlative / count** over `(label, value)`: "X có `<label>` lớn nhất / dưới N / bao nhiêu". The `<label>` is matched from the query against the entity's OWN field labels (corpus headers + owner `custom_vocabulary` synonyms) — never against a hardcoded "price". Spa "Giá", phone "RAM", legal "số khoản" all share ONE path.

4. **Rename to drop "price"** (no version-ref): `parse_money_vn`→`parse_number` (currency is one optional labelled unit); `RangeFilter.price_*`→`value_*`; `query_by_price_range`→`query_by_numeric_attr`; `top_by_price`→`top_by_numeric_attr`; `parse_price_of_entity_query`→`parse_attribute_of_entity_query`; `math_lockdown` unit = the corpus's own label, not `"VND"`; drop/per-bot the VND price buckets.

5. **Synthetic-chunk render** surfaces ALL generic labelled attributes faithfully (the per-entity renderer already does — `query_graph.py:2401-2408`; the aggregate path must too). This is the immediate render-faithfulness fix that also closes the spa combo-price miss as a SIDE EFFECT of doing the generic-correct thing — not by special-casing "combo".

## Consequences

**Positive**
- Domain-neutral + fair to every bot (Open-Closed): a new industry = new corpus headers, zero engine change.
- Reuses ADR-0006 owner-declared roles; `attributes_json` becomes first-class.
- The price-coupling ratchet guard can only go down as this lands.

**Costs / risks**
- Schema change → hard-to-reverse → each stage flag-gated + measured A/B vs the current price path (no-guess-must-measure). The backward-compat VIEW means current bots see no behavior change until a flag flips.
- Number capture without a floor may index ordinals/IDs — mitigated by surfacing them as labelled (label disambiguates) and letting the query target a label, not "any number".

## Staged plan (each measured, behind a flag)
- **S1** Render-faithful synthetic chunk (generic attributes) — smallest, highest-value, no schema change. Pin the lossy aggregate path first.
- **S2** Numeric-attribute index table + ingest dual-write (price columns still authoritative).
- **S3** Generic range/superlative/count queries over the new index (flag; A/B vs price path).
- **S4** Flip `price_*` to a derived VIEW; rename symbols; drop VND floor/buckets.
- **S5** Language-neutral routing (ADR-0008) so non-VN bots reach these routes.

## Alternatives rejected
- **Special-case `price_secondary`/combo in the renderer** — price-domain-coupled, the `_STOCK_COL_TOKENS` trap again. Rejected.
- **Keep price first-class, add per-domain columns (stock/date/RAM...)** — unbounded, re-creates the betrayal one column further. Rejected.
- **Big-bang schema rewrite** — breaks current bots, violates EVOLVE + no-guess-must-measure. Rejected for staged strangler fig.
