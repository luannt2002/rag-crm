# [T1-Smartness] P9 — Locale-driven column-role cascade (kill closed-vocab silent-drop)

> Tier: **T1** (affects answer correctness — a mis-bound / dropped price column makes the bot answer
> incomplete or refuse despite the corpus having the answer). Highest priority per Core-MVP ordering.

## Root cause (verified — rule#0, SỰ THẬT)
- `document_stats._column_roles` ([document_stats.py:323-353](src/ragbot/shared/document_stats.py#L323)) binds
  header→role by **exact membership** in closed vi-first frozensets: `token in _NAME_COL_TOKENS` /
  `_CATEGORY_COL_TOKENS` / `_ALIASES_COL_TOKENS` / `_PRICE_COL_TOKENS` (lines 342-349). No substring, no
  fuzzy, no positional-with-warn, **no UNKNOWN signal**.
- Tokens are **hardcoded vi** frozensets ([document_stats.py:135-162](src/ragbot/shared/document_stats.py#L135)),
  NOT read from `language_packs[locale]` — so an EN/other-locale header ("Item", "Price", "Category") or a
  vi synonym not in the set ("Mặt hàng", "Phân loại") fails to bind → the column is dumped to
  `attributes_json` (unsearchable). This is the **silent-drop** pain (P8) at the column level.
- Evidence the pattern works elsewhere: `i18n.get_pack` reads per-locale rows from the `language_packs`
  table (migrations 0055/0056) — the proven DB-seed-by-locale template to mirror.

## Strategy — EVOLVE, not rewrite (strangler)
Keep `_column_roles` signature + the positional fallback in `_extract_entity_from_row` (already
domain-neutral). Replace the **inside** of the matcher with a cascade + make the token sets **locale-driven**
from DB. No new subsystem; same function contract.

## Design (domain-neutral, locale-driven, zero-hardcode, no per-bot literal)
### A. Role cascade (replaces exact-only match)
For each header cell, resolve a role by descending specificity, FIRST hit wins per role:
1. **exact** — normalised token ∈ role token-set (current behaviour).
2. **substring** — a role token is a whole-word substring of the header ("Tên dịch vụ" → name via "tên";
   guard word-boundary to avoid "tên kho" stealing "tên" — the xe-1 bug — by preferring the LONGEST /
   most-specific token and de-prioritising stub tokens).
3. **fuzzy** — normalised Levenshtein / token-set ratio ≥ a configurable floor
   (`DEFAULT_COLUMN_ROLE_FUZZY_FLOOR` in shared/constants; e.g. 0.86) for typo/diacritic-variant headers.
4. **positional** — existing fallback (first non-money col = name) ONLY when no role matched at all.
5. **UNKNOWN-warn** — any header column that maps to NO role emits a structured
   `stats_column_role_unassigned` log (record_document_id + header literal) so silent-drop becomes
   observable (mirrors the P8 silent-drop-impossible invariant). NEVER raise — extraction must not abort.

### B. Locale-driven token sets (DB-seeded, not hardcoded vi)
- New `language_packs` field `column_role_tokens` (JSON: `{name:[…], category:[…], price:[…], aliases:[…]}`),
  seeded per-locale via **alembic** (vi seed = today's frozensets verbatim; en seed added). Backfill: a
  `_DEFAULT_COLUMN_ROLE_TOKENS` constant mirrors the current vi sets as the in-memory fallback when the
  locale row is missing (graceful degradation — same pattern as i18n fallback).
- `_column_roles(header, *, locale)` lifts the token sets for the doc's `effective_language` (already on
  `ctx`) instead of the module frozensets. Default locale = current vi behaviour (byte-identical).
- **No psql hotfix** — token seed/update ONLY via alembic tracked migration (CLAUDE.md rule 7).

## Stages
1. **CODE (TDD)** — cascade matcher in `document_stats.py` as a pure helper `_resolve_role(header, tokens)`;
   inject locale token-sets; UNKNOWN-warn log. Failing tests FIRST:
   `tests/unit/test_column_role_cascade.py` — exact/substring/fuzzy/positional/unknown per case; xe-1
   "Tên kho" must NOT steal name from "Tên hàng" (regression pin); EN header "Item|Price|Category" binds;
   vi default byte-identical to current.
2. **DATA (alembic)** — `language_packs.column_role_tokens` column + vi/en seed; resolver reads it via a
   repo (cached like get_pack). Constant fallback for un-seeded locales.
3. **VERIFY** — re-run the 3-bot QA (esp. xe alias/variant + any EN-header doc) → Coverage delta measured
   (rule#0, no claim without re-run). HALLU must stay 0.

## Sacred-rule compliance (self-audit)
- #0 evidence-first ✅ (file:line cited). Zero-hardcode ✅ (fuzzy floor → constants; tokens → DB).
- Domain-neutral ✅ (no brand/bot literal; tokens generic, locale-seeded). No-version-ref ✅.
- No app-inject/override answer ✅ (ingest-side only). HALLU=0 ✅ (dedup/role only; positional unchanged).
- Resolver-fallback-system_config ✅ (DB locale row → constant fallback, never hard-fail). Narrow-except ✅.
- Model-tier ✅ (deterministic Python, no LLM). EVOLVE ✅ (same function contract, inside swapped).

## ADR?
Borderline — `column_role_tokens` schema add is additive + reversible; cascade is internal. **No ADR
required** unless we later add an LLM column-classifier (that WOULD need one — surprising + a real
trade-off). Note as a deferred option in the plan, not this scope.

## Verification gate
TDD green · ruff HEAD==NOW · domain-neutral grep 0 · vi default identity proven · EN-header doc binds ·
xe-1 regression pinned · 3-bot Coverage delta measured · HALLU=0.
