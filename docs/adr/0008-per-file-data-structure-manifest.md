# ADR-0008 — Per-file Data-Structure Manifest served to the LLM; shape/value + LLM column-typing replaces vocab/positional guessing

- **Status**: Proposed (2026-07-07)
- **Tier**: T1-Smartness. **Stance**: EVOLVE (strangler fig), NOT rewrite. Additive, default OFF, per-bot flag.
- **Motivated by**: truth-audit 2026-07-07 session — bot `chinh-sach-xe` false-denies a stocked brand 97% of the time (Rovelo 35/36) with HALLU-số ≈ 0. Root traced to the structured-index GUESSING the wrong column as the entity name.
- **Builds on**: [ADR-0006](0006-column-role-structural-and-custom-vocab.md) (owner declares meaning; generic labelled attributes) · [ADR-0007](0007-stats-price-index-to-attribute-index.md) (PRICE-index → ATTRIBUTE-index).
- **Enforced by**: `tests/unit/test_domain_neutral_guard.py` (coupling ratchets) + new manifest pins.

## Context

The platform is multi-doc / multi-bot / multi-format / multi-language / domain-neutral by mandate. The RAW-chunk RAG path honours this (it keeps whole rows; a legal/medical/any-schema bot works). The **structured-index layer does not** — to answer exact numeric questions it must decide *which column is the name / the value / the category*, and it decides by **GUESSING**:

- **B1 — name column by hardcoded VN/EN vocab** (`_NAME_COL_TOKENS`, `document_stats.py:174`): the header must literally match a baked word list. A header `productname` (no exact token), a Japanese header, or a code column all misfire.
- **B2 — name column by POSITION** (`_extract_entity_from_row:636`, "first non-money col = name"): the first column is usually a group/warehouse/index stub, not the name.
- **B3 — SKU match by size alias only** (stats route), brand-blind: "Rovelo 195/55R16" matches a *Landspider* 195/55R16 of the same size.
- **B4 — synthetic chunk assumed authoritative** (`query_graph.py:~2510`, score=1.0): a wrong/incomplete structured match SUPPRESSES the correct raw chunk.
- **B5 — cross-doc merge by digit-key** (`_reconcile_cross_doc`), no brand/name check.
- **B6 — aggregate/total-row rejection by VN vocab** (`_AGGREGATE_TOKENS`).

DB-verified consequence on `chinh-sach-xe`: `entity_name` holds an internal code (`2-R16 195/55 LPD`) in **0/242** rows equal to the real product name; the human name (`Lốp Rovelo 195/55R16 …`, with the brand) sits UNUSED in `attributes_json.productname` (187/242). The LLM is served a brand-less code → it denies the brand or conflates prices across brands (the audit's original 5 brand-conflations).

The engine already knows the right law for structure — `_is_header_row` is documented **"THE ONE LAW — shape, not vocabulary"** (`document_stats.py:356`) and price detection is value-based (`_is_pure_money`). B1–B6 are exactly where the code **abandons its own law**. And every fix-by-adding-vocab is unbounded: a new language or domain needs new code. That is the "hardcode forever" failure the mindset forbids.

## Decision

**Capture each file's STRUCTURE as a per-file MANIFEST at ingest, store it, and SERVE it to the LLM as descriptive grounding — so the engine knows STRUCTURE while MEANING travels WITH the data (headers + LLM reading + owner override), never baked in code.** Column typing is by **shape/value + LLM-typing**, not vocab or position. This makes adding a new domain / language / format / bot schema require **zero engine code**.

Four commitments:

1. **Self-describing data.** A `DataStructureManifest` is built per FILE at ingest from the parsed blocks: for each table → `columns[]{ label, role, dtype, coverage_pct, sample_values }`, plus `n_rows`, and per-file `kind` (table / outline / faq / prose). Stored in `documents.structure_manifest_json` (alembic). It describes whatever is there — it never forces a name/price schema.

2. **Meaning is read by the ANSWER-LLM from a ZERO-LLM manifest — no extra model call.** The manifest is a DETERMINISTIC description built with **no model at all**: each column's header + a few sample cell values + coverage. It is served to the answer-LLM (already being invoked) alongside the raw chunks, and the answer-LLM infers which column is the name/price/etc. FOR FREE in the same call — smart, correct, and zero added cost. A cheap, deterministic **shape/value typing** (money-shaped → value, size/code-shaped → identifier, multi-word free-text → descriptive-name; language/header-agnostic, still **no model**) OPTIONALLY labels roles to make the structured/attribute index identity-aware for exact numeric/aggregate queries. An **LLM-typing** step is NOT in the default flow (it would add a call per table for no benefit when the answer-LLM already interprets); it is retained only as an OFF-by-default owner tool for rare enterprise schemas, and if ever used it goes through the **bot's own bound LLM via `ModelResolver` (`purpose="structure_typing"`), NEVER a hardcoded provider/model** (Strategy+DI). `custom_vocabulary["column_roles"]` is DEMOTED to an **optional owner/staff override**, never the primary path, never a silent fallback. B1/B2/B6 are deleted.

3. **Serve the manifest to the LLM (sacred #10-safe).** At generate, the retrieved docs' manifests are added to context ALONGSIDE `system_prompt` + chunks, as **DESCRIPTIVE DATA** ("this table has columns: `<label>` (`<role>`, `<dtype>`) …") — never a behavioural instruction. The LLM then reads a raw/structured row and knows which cell is the name. This is grounding, the same class as the retrieved chunks (Quality-Gate #10 safe). It carries NO answer rule.

4. **Structured retrieval becomes manifest-aware, not guess-authoritative.** The structured route reads the manifest for schema-aware routing (which label to filter/rank by) and becomes **brand/identity-aware** (B3): a query naming an identity filters on the descriptive-name/identity column. The synthetic chunk **must not suppress the correct raw chunk at low confidence** (B4): its score reflects match confidence, and raw chunks stay reachable. Cross-doc merge (B5) additionally requires a name/identity match, not digits alone. The numeric side rides ADR-0007's generic `(label, value, unit)` attribute-index so a non-price domain is first-class.

## Mechanism (additive · strangler fig · default OFF)

- `application/ports/manifest_port.py` — `ManifestBuilderPort` (Protocol): `build(parsed_blocks, dsi_rows) -> DataStructureManifest`.
- `infrastructure/manifest/table_manifest_builder.py` — shape/value column typing + optional LLM-typing (injected typing port); `null_manifest.py` (default, returns empty → no-op).
- `infrastructure/manifest/registry.py` + `bootstrap.py` DI; config key `manifest_enabled` in `system_config` (+ per-bot `plan_limits`), default **False**.
- `documents.structure_manifest_json` JSONB (alembic, backward-compat NULL).
- Ingest writes the manifest (also fixes the bad-header cases: label-in-data-row / Chinese-header → recovers the real column name, closing the "NGÀY VỀ → ''" ingest losses).
- `generate` node: when enabled, append the retrieved docs' manifest as a descriptive context block (token-bounded — only retrieved docs, capped).
- `retrieve`: read manifest for schema-aware routing + identity-aware filter; drop the score=1.0 suppression.

## Consequences

- **Ends the hardcode treadmill**: a new domain/language/format/bot-schema needs no engine vocab — the manifest describes it and the LLM reads it. Directly closes B1–B6 + the Rovelo false-deny + brand-conflation.
- **Cost**: **zero added LLM calls** — the manifest is deterministic (no model at ingest) and the answer-LLM interprets it in its existing call. Only added cost is +manifest tokens per query, bounded to the retrieved docs' schemas (capped). Measured before default-ON.
- **Compliance**: manifest = data-derived descriptive metadata → sacred #10 safe (pinned by a test that the served manifest contains no imperative/answer text). Domain-neutral (shape/value + owner labels, zero brand/lang literal in engine). No per-bot logic in core.
- **Migration**: additive; existing bots unaffected until `manifest_enabled=true` + re-ingest. `column_roles` keeps working as an override.
- **Risk**: LLM-typing mis-labels a column → mitigated by (a) shape/value first (LLM only for ambiguous), (b) staff onboarding confirm surfaced (not hidden log), (c) manifest is descriptive → a wrong label degrades to "LLM has more context than today", never worse than the current blind guess.

## Rollout (measured, one change per step — constitution ladder)

- **A0** — this ADR (owner approve).
- **A1** — build manifest at ingest (Port+DI, flag OFF). RED: xe-1 → manifest column `productname` typed `name` by shape; xe-2 (1-chunk, suspected parse-fail) surfaced. Measure: re-ingest → DSI/manifest carry the real name.
- **A2** — retrieve reads manifest (identity-aware routing; drop score=1.0 suppression). RED + N≥10 on Rovelo/Landspider/Michelin.
- **A3** — generate serves manifest schema-card. Measure: brand false-deny 35/36 → ~0; Michelin true-refuse preserved; HALLU-số stays 0.
- **A4** — delete B1/B2/B6 vocab-guess once manifest proven (ratchet down `_*_COL_TOKENS`); demote `column_roles` to override.

## Alternatives rejected

- **Add more column-role vocab / more languages** — the unbounded-hardcode treadmill; violates `multilingual-no-vocab`.
- **Force owner to declare `column_roles` per bot** — brittle (keys on exact header strings, breaks per-file/per-language), customer-hostile (non-technical owners can't), and it silently falls back to the bad guess when empty.
- **Rewrite the stats layer** — violates EVOLVE stance; the raw path + ADR-0007 attribute-index + this manifest are additive and reversible.
