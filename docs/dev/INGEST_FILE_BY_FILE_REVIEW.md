# Ingest flow — file-by-file review (bug + clean-code + comment standard) · 2026-06-23

> Combined per-file assessment of the 22 ingest files on FOUR axes:
> **(1) Functional** (correctness/bugs) · **(2) Comment/doc standard** (full English, no temporal/version
> ref, docstring coverage) · **(3) Clean-code / OOP / pattern** (helper reuse, SOLID, Strategy/DI) ·
> **(4) Dead code**. Numbers are measured (objective scan), not vibes (rule#0).
> Bar = **expert**. Action per file: **LEAVE** (already expert) · **CLEAN** (comment-only) · **FIX** (code).
> Dead-code verdict: 0 truly-dead local functions (see `reports/INGEST_UNUSED_FUNCS_20260623.md`).

## Legend
- `VNcmt` = comment/docstring lines containing Vietnamese → must translate to English.
- `verRef` = temporal/version references (dates, Sprint/Wave, `260525`, `v2`, `_legacy`) → must strip (CLAUDE.md no-version-ref: comments are WHY-only).
- `noDoc` = `def`s without an adjacent docstring (Protocol-impl methods may legitimately inherit the Port docstring).

## Scorecard

| File | Func | VNcmt | verRef | noDoc | Clean/OOP/pattern | Action |
|---|---:|---:|---:|---:|---|---|
| `routes/documents.py` | 9.0 | 0 | 1 | 5/6 | Canonical create; idempotency+UoW. Route handlers may inherit OpenAPI `summary` as doc | **CLEAN** strip "260525 Bug #2" |
| `routes/documents_stream_upload.py` | 7.0 | 0 | 0 | 2/7 | Disabled module — reads as live | **CLEAN** add DISABLED module docstring |
| `routes/sync.py` | 8.0 | 10 | 0 | 3/8 | Legit separate bulk path | **CLEAN** 10 VN docstrings → EN |
| `http/router.py` | 9.0 | 1 | 3 | 0/0 | Wiring only | **CLEAN** strip 3 verRef + 1 VN |
| `use_cases/ingest_document.py` | 9.5 | 0 | 0 | 2/2 | UoW exactly-once; the 2 noDoc are `execute`/`__init__` w/ class doc | **LEAVE** |
| `workers/document_worker.py` | 6.0 | 1 | 4 | 3/6 | God-ish (692 LOC); thin-adapter claim but does parse routing | **FIX** A-I1 + CLEAN 4 verRef |
| `document_service/ingest_core.py` | 8.5 | 15 | 2 | 4/4 | U1 orchestration; robust sniff present | **CLEAN** 15 VN (heaviest) + 2 verRef |
| `document_service/ingest_stages_store.py` | 8.0 | 6 | 8 | 2/4 | U5–U7; **8 verRef = worst** | **FIX** A-I4 + CLEAN 6 VN + 8 verRef |
| `document_service/__init__.py` | 8.5 | 16 | 0 | 14/17 | 999 LOC, 17 funcs — **god-file**; 14 noDoc | **CLEAN** 16 VN + docstrings; consider split (T3) |
| `services/google_link_service.py` | 9.0 | 22 | 0 | 1/7 | Clean helper but **22 VN = worst** | **CLEAN** 22 VN → EN |
| `parser/registry.py` | 9.5 | 0 | 0 | 2/5 | Port+Strategy+Registry+Null exemplary | **LEAVE** (add 2 fn docstrings opt) |
| `parser/docx_parser.py` | 8.5 | 0 | 0 | 5/6 | Adapter; 5 noDoc = Port-impl methods | **FIX** A-I5 Block emit + add fn docstrings |
| `parser/excel_openpyxl_parser.py` | 8.5 | 0 | 0 | 5/5 | Adapter | **FIX** A-I5 + docstrings |
| `parser/google_sheets_parser.py` | 8.5 | 0 | 0 | 5/5 | Adapter | **FIX** A-I5 + docstrings |
| `parser/kreuzberg_markdown_parser.py` | 8.5 | 3 | 1 | 5/6 | Adapter | **FIX** A-I5 + CLEAN 3 VN + 1 verRef |
| `parser/markdown_parser.py` | 8.0 | 0 | 0 | 5/6 | Adapter | **CLEAN** add fn docstrings |
| `parser/pdf_parser.py` | 8.0 | 0 | 0 | 5/5 | Adapter | **CLEAN** add fn docstrings |
| `parser/null_parser.py` | 10 | 0 | 0 | 4/4 | Null Object; methods inherit Port doc | **LEAVE** |
| `ocr/kreuzberg_parser.py` | 6.5→**9.0** | 1 | 1→0 | 7/9 | **A-I2 FIXED ✅** (sniff added, verRef cleaned) | remaining: add 7 fn docstrings |
| `ocr/ocr_factory.py` | 7.0 | 0 | 1 | 1/1 | Docstring/behavior drift | **FIX** A-I6 + strip 1 verRef |
| `shared/mime_sniff.py` | 9.5 | 0 | 0 | 1/3 | Robust sniffer; **extended for pptx ✅** | **LEAVE** |
| `shared/tabular_markdown.py` | 9.0 | 9 | 0 | 6/10 | Shape-based, domain-neutral | **CLEAN** 9 VN + docstrings |

**Totals**: VN-comment lines to translate ≈ **84** · version/temporal refs to strip ≈ **20** · function
docstrings to add ≈ **40** (many are Port-impl that may inherit — net new ≈ 20).

## Per-file detail (the ones that matter)

### 🔴 FIX-priority (code, not just comments)
- **`document_worker.py` (6.0)** — the score-dragger. Bug A-I1 (non-robust detect + `raw_bytes=None`).
  Also 692 LOC for a "thin adapter" — the parse-routing block belongs in the service. Clean: 4 verRef
  (`2026-05-19`, `Wave I`). **Action**: A-I1 fix (thread bytes / delete local parse) → then it shrinks.
- **`ocr/kreuzberg_parser.py` (6.5 → 9.0)** — **A-I2 already fixed** (this session): real-MIME sniff +
  xlsx/pptx suffix; verRef comment cleaned. Remaining: 7 helper docstrings.
- **`ocr/ocr_factory.py` (7.0)** — A-I6 docstring/behavior drift (claims fail-loud, silently falls back).
- **4 structured parsers (docx/excel/sheets/kreuzberg_md, 8.5)** — A-I5: emit typed Block stream (the
  cross-flow lever B-2). Each also 5/5–5/6 noDoc (Port-impl — add a one-line purpose doc per method).
- **`ingest_stages_store.py` (8.0)** — A-I4 late_chunking memory + the **worst verRef count (8)**.

### 🟡 CLEAN-priority (comment-only, no logic touch)
- **`google_link_service.py` (22 VN)** — pure helper, low risk; translate all 22 → EN first (safest start).
- **`ingest_core.py` (15 VN)** + **`__init__.py` (16 VN, god-file)** + **`tabular_markdown.py` (9 VN)** +
  **`sync.py` (10 VN)** — bulk VN→EN translation, no logic change, pytest green per batch.

### ✅ LEAVE (already expert)
`ingest_document.py` (9.5) · `registry.py` (9.5) · `null_parser.py` (10) · `mime_sniff.py` (9.5, just
extended) · `documents.py` (9.0, after 1 verRef strip).

## Clean-code / OOP / pattern verdict (flow-wide)
- **Pattern health: EXPERT.** Port + Strategy + Registry + Null Object + DI is applied consistently
  (parser/ocr). Helper reuse is good (`mime_sniff`, `tabular_markdown` are shared utils, not duplicated).
- **Two god-ish files** (T3, not urgent): `document_service/__init__.py` (999 LOC/17 fn) and
  `ingest_stages_store.py` (1022 LOC) — candidates for splitting per-stage, but **behavior-preserving
  split only**, deferred until the bug fixes land.
- **No dead local functions** — nothing to comment-out (the 3 naive candidates were route handlers +
  a Protocol method; commenting them would break the app/interface).

## Execution order (combined bug + clean, per `plans/20260623-expert-remediation`)
1. **Clean-first the pure helpers** (zero logic risk): `google_link_service.py` → `tabular_markdown.py`
   → `ingest_core.py` → `__init__.py` → `sync.py` (VN→EN + verRef strip), pytest green per file.
2. **Fix-then-clean the code files**: A-I1 (`document_worker`) → A-I5 (4 parsers, + docstrings) →
   A-I4 (`ingest_stages_store`) → A-I6 (`ocr_factory`).
3. **Verify**: full unit suite green; grep guard VN-comment + verRef on the 22 files → target 0.
