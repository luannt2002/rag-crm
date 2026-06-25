# [T2-CostPerf] P1 — Sub-document split (Map-Reduce ingest for oversized files)

> Tier: **T2** (stability/RAM/throughput — oversized single-task ingest spikes memory and serialises a
> big file's work). Does NOT change answer correctness (T1). Highest-risk of the three → do LAST, after
> P4 (they share the lane/fan-out machinery).

## Root cause (verified — rule#0, SỰ THẬT)
- One upload = one `document.uploaded.v1` task = the worker processes the **whole document in one pass**
  (parse → chunk → embed). A 224KB sheet → thousands of child chunks → many embed batches under one task.
- Already bounded (so this is NOT an unguarded-OOM emergency): `MAX_DOCUMENT_CONTENT_CHARS = 500_000`
  ([constants _03:78](src/ragbot/shared/constants/_03_language_packs_db_driven_pro.py#L78)) hard-caps input;
  `DEFAULT_EMBED_DOC_BATCH_SIZE = 100` + `DEFAULT_EMBED_DOC_BATCH_TIMEOUT_S = 300`
  ([constants _04:77,167](src/ragbot/shared/constants/_04_jwt_auth.py#L77)) batch the embed; `late_chunking`
  degrades to a single whole-doc window ([late_chunking.py:116](src/ragbot/shared/late_chunking.py#L116)).
- **Missing**: no split of one large file into independent sub-tasks. A natural boundary already exists in
  the canonical output — Excel emits `# <sheet>` per sheet
  ([excel_openpyxl_parser.py:88](src/ragbot/infrastructure/parser/excel_openpyxl_parser.py#L88)); legal/doc
  uses `#`/`##` chapter headings. So splitting at top-level headings is structure-preserving, not lossy.

## Strategy — EVOLVE (Map-Reduce over the EXISTING task/event model)
Do NOT change the parser output or the chunker. Add a **pre-chunk splitter** that, when a parsed document
exceeds a size/section threshold, emits one **sub-document ingest task per top-level section** (sheet /
chapter) onto the SAME stream, each a normal `document.uploaded.v1`-shaped job. Each sub-task then runs the
unchanged pipeline with a small payload. Reduce step = the parent doc's `document_service_index` summary +
state aggregates across sub-docs.

## Design (domain-neutral, zero-hardcode, structure-preserving)
### A. Split decision (Map)
- Trigger when parsed canonical markdown exceeds `DEFAULT_SUBDOC_SPLIT_MIN_CHARS` (constants) **AND** has
  ≥2 top-level sections (count `# ` headings via the existing `analyze` heading detector — reuse, don't
  reinvent). Below threshold or single-section → no split (today's path, byte-identical).
- Split at top-level heading boundaries only (sheet/chapter); never mid-table / mid-atomic-block (reuse the
  atomic-protect + table-footer guards so a split can't shear a row group).
### B. Sub-document identity + Reduce
- Each sub-doc gets a deterministic child id derived from (parent record_document_id, section_index) so
  re-ingest is idempotent (pairs with the `chunk_hash_id_enabled` UUID5 pattern). Parent row tracks
  `subdoc_total` / `subdoc_done`; parent flips `active` only when all sub-docs are active (mirrors the
  existing `_decide_ingest_state` aggregate, extended across sub-docs).
- Retrieval is unaffected — chunks still carry `record_bot_id`; the parent/child link is ingest-side
  bookkeeping. (Optional: stamp `parent_document_id` for admin grouping.)
### C. Config + flag
- `ingest_subdoc_split_enabled` (system_config + per-bot, default **False**). Threshold + min-sections in
  system_config (Redis-cached). Flag OFF = single-task today.
- Fan-out is bounded (semaphore / per-lane via P4) so a 50-sheet workbook doesn't spawn 50 unbounded tasks.

## Stages
1. **ADR FIRST** — sub-document task model + parent/child state aggregation. Hard-to-reverse (task
   topology + state machine), surprising, real trade-off (throughput vs bookkeeping complexity + partial-
   failure semantics). **Gate: user approves ADR before code.** Depends on P4 lane/fan-out landing first.
2. **CODE (TDD)** — pure splitter `split_into_subdocs(markdown, *, min_chars, min_sections) -> list[str]`
   (TDD: splits at `#` boundaries; never mid-table; below-threshold → single element identity; single-
   section → no split). Parent/child state aggregation (TDD on the active-when-all-done rule). Publisher
   fan-out wiring.
3. **VERIFY (runtime, rule#0)** — ingest a large multi-sheet workbook with flag OFF vs ON: measure worker
   peak RSS + wall-clock; sub-doc path must lower peak RAM and allow parallel drain; chunk count + stats
   entities identical to the single-task path (no data loss / no duplication — pairs with the idempotent
   stats fix f684c82). No claim without the measured numbers.

## Sacred-rule compliance (self-audit)
- #0 evidence-first ✅. Zero-hardcode ✅ (thresholds → constants + system_config). Domain-neutral ✅ (split
  by structure, not by bot/brand). No-version-ref ✅. No app-inject/override ✅ (ingest-side).
- Idempotent ✅ (deterministic sub-doc id; pairs with stats-index idempotent write). Async rules ✅
  (bounded fan-out; exactly-once per sub-task). Narrow-except ✅. Model-tier ✅ (deterministic split).
- EVOLVE ✅ (parser + chunker + retrieval untouched; only a pre-chunk splitter + task fan-out added).

## ADR? **YES — required** + sequenced AFTER P4. Code blocked on ADR approval.

## Verification gate
ADR approved · TDD green · ruff 0-new · flag-OFF identity proven · domain-neutral grep 0 · split never
shears a table/atomic block · chunk+entity parity vs single-task · runtime peak-RAM + wall-clock delta
measured · no data loss / no duplicate · HALLU=0.
