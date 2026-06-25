# ADR: Input philosophy = NORMALIZE-to-IR (system auto-normalizes; never constrain the customer's format)

Status: Accepted
Date: 2026-06-25
Stream: Input-data control — `plans/20260625-input-control-silentdrop-multilocale/` +
`reports/INPUT_CONTROL_ROOT_CAUSE_3PHILOSOPHIES_20260625.md`

## Context

The platform ingests heterogeneous customer data (Google Sheets/Excel/Docx/PDF/CSV/MD…) with arbitrary
headers, multiple sheets, mixed languages, and uneven structure. The root cause of the chinh-sach-xe
failures (44% → fixed to 72%, remaining gap) was traced to **input-data control**: columns whose header is
outside a fixed Vietnamese role-vocabulary are silently dropped to `attributes_json`/`col_N` — the ingest
reports "success" while the price/stock/date/image columns are dead and unsearchable.

This forced a strategic question the user repeatedly returned to: *"can customers use their raw files
directly instead of hand-writing a happy-case template? Make the code stronger instead of constraining the
customer?"* This ADR locks the answer so it stops being re-litigated.

Three input philosophies were on the table:

| | Who "rewrites" the data? | UX | Outcome |
|---|---|---|---|
| **CONSTRAIN** (checker rejects off-spec) | the CUSTOMER must fix the source | bad ("your format is wrong, fix it") | narrow intake |
| **ABSORB-raw** (feed raw straight to the chunker) | nobody | easy upload | breaks (OOM, column-loss, unjoined sheets) |
| **NORMALIZE-to-IR** | the SYSTEM rewrites implicitly | customer uploads raw, does nothing | correct |

## Decision

Adopt **NORMALIZE-to-IR** as the input philosophy, with a hard FORMAT/DATA-CONTENT split:

1. **FORMAT is never constrained.** The customer uploads ANY supported format with ANY header/sheet/locale
   shape and does NOTHING. A bounded set of per-format normalizers auto-transforms raw → ONE canonical
   Intermediate Representation (structured markdown IR + stats-index `ParsedEntity`). The "happy-case
   template" is this **internal IR the system produces**, NOT a precondition the customer authors. We never
   reject an upload because of its format.

2. **DATA-CONTENT cannot be fabricated.** No normalizer, however strong, can produce a stock number that
   exists in no source file, or join four sheets that share no key. When the data is genuinely
   missing/ambiguous, the system emits a **data-quality ADVISORY to the owner** ("the bot can't answer X
   because the source has no stock column / sheets A,B don't share a join key") — it does NOT block the
   upload and does NOT silently drop. The owner learns *why* coverage is limited; they are never told to
   reformat.

3. **The checker is ADVISORY, not an admission-controller.** It reports column-role coverage + unjoined
   fragments + demoted columns as warnings on the ingest result. The ONLY hard reject is a system-safety
   bound (file beyond the OOM ceiling) — and even there the normalizer must attempt a map-reduce sub-doc
   SPLIT before rejecting.

4. **The normalizer is the MAIN path; the checker is the safety net.** "Stronger code" means a stronger
   NORMALIZER (fuzzy/substring/synonym + multi-locale column-role recognition, labelled row linearization,
   contextual breadcrumb, map-reduce split) — NOT a chunker that "eats raw garbage" (impossible) and NOT a
   reject-gate that narrows intake.

## Why this shape (not the alternatives)

- **Not CONSTRAIN-only.** Reject-on-off-spec pushes the rewrite onto the customer ("fix your format") — the
  exact UX the user rejects. It also narrows intake on a multi-tenant product that must accept whatever
  customers have.
- **Not ABSORB-raw / ABSORB-zoo (tldw_server).** Feeding raw straight to the chunker is the current break
  (OOM, column-loss). tldw's "throw every parser/OCR at it" has no column-role recognition (grep 0 hits) and
  no validation — its price/list deterministic path would die exactly where ours does. Infinite parser
  maintenance is untenable for a small multi-tenant team.
- **Not long-context-only (NotebookLM).** Sidesteps retrieval by stuffing the whole corpus into the model —
  works for tiny closed corpora, does not scale to 100K-doc multi-tenant. (Kept as an opt-in mode for
  small-corpus bots per ADR-0004, not the default.)
- **NORMALIZE-to-IR is industry SOTA** (Unstructured / Docling / LlamaIndex Enterprise): a few curated
  normalizers all converging on one IR + a validation/observability layer. We are already ~70% there (7
  registry parsers) — this ADR commits to deepening the normalizer, not adding formats.

## Consequences

- **Customer experience: upload raw, do nothing.** The "write a happy-case file" burden is removed; it
  becomes an internal IR. Coverage gaps surface as owner advisories, never silent, never reformat-demands.
- **What stays the customer's problem (by physics, not by policy):** truly-missing data (a column that
  exists in no file) and irreducibly-contradictory sheets. The advisory names these precisely so the owner
  can fix the SOURCE if they choose — but the system never blocks on them.
- **Engineering cost is bounded.** Stronger normalizer = role-recognition + linearization + locale-map +
  breadcrumb + split — a finite surface, not the infinite parser-zoo. Each new format is one normalizer
  converging on the existing IR (Open-Closed).
- **Anti-patterns locked out:** no checker-as-blocking-gate on the main path; no per-bot heading rules in
  core (config-schema only); no swap to Qdrant/Kafka/K8s; no chunker-eats-raw.
- **HALLU=0 preserved.** Labelled linearization ("dòng 5: Giá=700k, Tồn=404") kills the unlabelled-number
  HALLU class; the advisory makes missing-data visible instead of guessed.

## Reversibility

The IR boundary already exists (markdown + ParsedEntity). This ADR changes the STANCE around it (advisory
not blocking; normalizer-first) — implemented as config + the G1–G4 normalizer work, all per-bot
flag-gated and additive. Reverting = drop the new normalizer passes; the existing parsers + checker remain.
No schema rewrite, no data migration. Fully reversible.

## Status / next

Accepted. Implementation = `plans/20260625-input-control-silentdrop-multilocale/` (G1 fuzzy role-vocab, G2
multi-locale DB-seed, G3 table breadcrumb, G4 demote-warning, G-Linearize labelled rows, G-OOM map-reduce
split, G-Wire **re-scoped to advisory-not-blocking**). Complements ADR-0003 (entity-join, for the unjoined
multi-sheet case) + ADR-0004 (long-context, small-corpus opt-in).
