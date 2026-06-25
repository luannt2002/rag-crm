# ADR-0006 — Column-role = minimal-universal-roles + structural-inference + per-bot custom_vocabulary (NOT hardcoded per-domain vocab)

- **Status**: Accepted (2026-06-25)
- **Supersedes/refines**: the implicit "add a role frozenset per domain column" approach inside `document_stats.py`. Builds on [ADR-0005 NORMALIZE-to-IR](0005-normalize-to-ir-input-philosophy.md).
- **Tier**: T1-Smartness (input-control → coverage). **Stance**: EVOLVE, không REWRITE.

---

## Context

The input-control silent-drop root cause ([[INPUT_CONTROL_ROOT_CAUSE_3PHILOSOPHIES_20260625]]) tempted us to fix the `chinh-sach-xe` N5/N2 failures by adding domain role tokens — `_STOCK_COL_TOKENS`, `_DATE_COL_TOKENS`, `_IMAGE_COL_TOKENS` — to `document_stats.py`.

**That fix was caught and rejected (rule#0) for two compounding reasons:**

1. **DATA-CONTENT, not a role gap.** Verified against `raw_content`: the 3 ingested xe sheets contain `Tên/Nhóm/Mã/Kho/Aliases` (xe-1), `Tên/Nhóm/Ngày về/Aliases` (xe-2), `Tên/Nhóm/Giá/Aliases` (xe-3). **No sheet has a stock / date1 / image-link column.** NotebookLM's gold answers came from sheets the bot was never fed. Adding stock/date/image roles would capture **0 rows** — fixing nothing.

2. **Domain-coupling violates CLAUDE.md.** A hardcoded Vietnamese role frozenset in code couples the engine to one industry + one language. It cannot generalize:

| Bot domain | Real columns | Hardcoded vi-frozenset handles? |
|---|---|---|
| Tyres/catalog | Tên/Giá/Tồn/Mã | ⚠️ only by accidental vi-vocab overlap |
| Legal | Điều/Khoản/Nội dung | ❌ no price/stock → roles meaningless |
| Real-estate | Diện tích/Hướng/Tầng | ❌ not in frozenset |
| Phone | Model/RAM/Pin/Màn hình | ❌ RAM/Pin dropped |
| Medical | Liều/Triệu chứng | ❌ dropped |

Enumerating roles per domain **is the silent-drop anti-pattern itself** — just deferred one column further. It breaks **domain-neutral** (CLAUDE.md) + **zero-hardcode** + CLAUDE.md line 303: *"Domain data → `system_config` hoặc per-bot `custom_vocabulary`."*

---

## Decision

**The engine must NOT know what a column "means". Column-role resolution is a 3-tier cascade, precedence high→low:**

### Tier 2 (authoritative) — per-bot `custom_vocabulary`
Owner declares roles in `bots.custom_vocabulary` JSONB (column + service already exist: `models.py:190`, `vocabulary_expander.py`). Example:
```json
{"column_roles": {"Tên SP": "name", "Giá bán": "value", "RAM": "attribute", "Pin": "attribute"}}
```
Code reads it **per-bot** — domain-neutral (code doesn't know "RAM"; owner does). This is the CLAUDE.md-sanctioned path and the "json theo cột" the owner asked for. **Wins over inference.**

### Tier 1 (heuristic, conservative) — structural inference (0 vocab)
Infer role from DATA SHAPE, not header text:
- **NAME / identifier** = the column with unique + longest-text values (every catalog has one identifier). Works for "Áo thun nam", "iPhone 15", "Điều 5".
- **NUMERIC** = column whose values are mostly numbers → captured + range-queryable (no need to know "price" vs "RAM" vs "stock").
- **MUST be confidence-gated**: assign NAME only when clearly unique/longest; when ambiguous, do NOT guess — fall through to Tier 3 and let Tier 2 (owner) override. Never let a long description column steal NAME.

### Tier 3 (default, universal) — generic labelled searchable attribute
Every other column → `attributes` with its header as label (mechanism already exists: `ParsedEntity.attributes` → `attributes_json` JSONB). Question "tồn kho của X" → match label "Tồn kho" + entity X → return value. **No semantics required. Works for car/legal/real-estate/medical/phone without enumeration.**

### Role minimality
- **NAME is the only truly-required role** (universal — every list has an identifier).
- `price/category/aliases` = **helper roles, degrade-graceful** (no match → no role → harmless for a legal bot).
- **NEVER add a new per-domain role in code.** The existing hardcoded vi frozensets are demoted to a **DEFAULT SEED for `locale='vi'` in DB** (or removed) — a hint, not the source of truth.

---

## Consequences

**Positive**
- Domain-neutral + zero-hardcode honored; works across industries without code changes (Open-Closed).
- The **G1 cascade matching engine** (exact > phrase-substring > word, tie-skip; shipped `7324145`) is **reused** — only the *source* of vocab changes (hardcoded → structural + per-bot + DB-seed). G1 not wasted.
- The generic-attribute capture + search already exists and is **proven end-to-end** (xe N4 "Ngày về" = 80% via the attributes path; the `Tồn` column fails only because it's absent from source).

**Costs / risks**
- Structural inference is heuristic → must stay conservative + confidence-gated; per-bot `custom_vocabulary` is the reliable override.
- **Generic-attribute SEARCH must be robust for label-targeted queries** ("tồn kho của X"): detect attribute label in the question → match entity → return value. Harden + test.
- `G2` per-locale role-frozenset (in the prior plan) is **dropped as a code construct** — replaced by DB default-seed + per-bot `custom_vocabulary`.

**Honest framing (rule#0)**
- xe N5/N2 "fail" is largely the bot **correctly refusing data it was never given** → HALLU-safe, not a bug. **72% ≈ the honest data ceiling** for the 3 sheets actually ingested. Comparing to NotebookLM (which saw more files) is unfair — different corpus.
- The DATA-CONTENT gap (missing stock/date/image columns) is **not code-fixable** → surfaced to owner via **G4 advisory** (ADR-0005), or owner uploads the missing sheets.

---

## Alternatives rejected
- **Add per-domain role frozensets** (`_STOCK/_DATE/_IMAGE...`) — domain-coupled, unbounded, re-creates silent-drop. Rejected.
- **Per-locale role frozensets in code** — still hardcoded domain assumptions; replaced by DB-seed + per-bot config.
- **LLM column-role classifier at ingest** — non-deterministic, cost/doc, HALLU surface; structural + owner-declared is deterministic and free.
