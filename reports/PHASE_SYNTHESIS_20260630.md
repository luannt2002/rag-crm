# PHASE SYNTHESIS — Input-Data Expert Build (2026-06-30)

> Branch `fix-260623-ingest-expert` · 67 commits ahead of `main` · stack: chat=innocom qwen3 · embed=ZeroEntropy zembed-1@1280 · rerank=ZE zerank-2.
> Verification discipline: rule #0 (no claim without evidence). Each row below is labelled **CODE-SHIPPED** (committed + 0-regression) / **RUNTIME-PROVEN** (measured on real data) / **OWNER-ACTION** (needs re-ingest or load-test gate).

---

## 1. HEADLINE — regression verification (RUNTIME-PROVEN)

| Run | Passed | Failed | Errors | Method |
|---|---|---|---|---|
| Session-baseline commit `9416f4d` | 6435 | 43 | 8 | scoped: `--ignore=_archive_pre_squash --ignore=integration`, JUnit XML |
| **HEAD `647f4a2`** | **6464** | **42** | **8** | identical scope + method |

- **Failure sets BYTE-IDENTICAL** baseline↔HEAD except `test_domain_neutral_guard` which **flipped to PASS** (brand-literal scrub). → **0 NEW regression, 1 baseline failure fixed, +29 passing tests.**
- The 42 failed + 8 errors are **all pre-existing** (collection/import errors in files this branch's input-data work never touched: archived alembic migration tests, CRAG re-export drift, callback retry, pydantic-Settings http-import). Proven by checkout-and-diff at the baseline commit.
- Full-suite caveat: a clean run needs `--continue-on-collection-errors` (8 pre-existing collection errors) + `--ignore=tests/_archive_pre_squash_20260618` (≈85 tests reference squashed migration files removed 2026-06-18). The local `alembic/` migrations dir shadows the pip `alembic` package — pre-existing, not introduced here.

---

## 2. col_N — the complete answer (RUNTIME-PROVEN)

**What is col_N?** A column whose **header cell was EMPTY in the source**. The tabular converter emits a positional placeholder `col{i+1}` so the grid keeps its width. It is **not** a data value — it is a missing column *label*.

**Root cause (immutable):** the source table has a **stacked multi-row header** — the real column names live in row 2, while row 1 is partially empty. The OLD converter took row 1 as the sole header → emitted `col_N` for the empties and pushed row 2 down as a data row.

**Fault attribution:** **CODE** (the converter), NOT the customer data, NOT the sysprompt. Proven on real data below.

**Real-data proof (zero API)** — bot `chinh-sach-xe`, doc `xe-1`, the 10-column inventory table:
```
SOURCE row 1:  ''  | Tên kho | Mã hàng | Tên hàng | ''    | ''    | ''       | ''    | ''    | ''
SOURCE row 2:  ''  | ''      | ''      | ''       | date1 | date2 |hình ảnh1 | ẢNH 1 | ẢNH 2 | Ảnh 3
```
- OLD (stored) header → `| col1 | Tên kho | Mã hàng | Tên hàng | col5 | col6 | col7 | col8 | col9 | col10 |`  (**7 placeholders**)
- NEW converter (`rows_to_structured_markdown`, committed `9009eac`) → `| col1 | Tên kho | Mã hàng | Tên hàng | date1 | date2 | hình ảnh1 | ẢNH 1 | ẢNH 2 | Ảnh 3 |`  (**1 placeholder** — col1 is a genuinely-unlabelled STT/index column)
- `_is_header_continuation(row1,row2)=True`, `_merge_header_fill` fills the gaps. **Pure shape-logic, zero vocabulary** → multi-bot safe (works for any domain's column names).

**Why stored chunks still show col_N (the caveat):** `documents.raw_content` stores the **post-conversion markdown** (placeholders already baked at the *original* ingest), and the original source file is **not retained**. The converter fix is **forward-effective only** — new ingests are clean; the 3 stored col_N chunks (`chinh-sach-xe`) clear only on **RE-INGEST from the original source file** (OWNER-ACTION — needs the owner to re-upload the xlsx/Sheet; re-running on `raw_content` would not re-trigger the spreadsheet converter).

**Can the sysprompt rescue an answer despite col_N?**
- `xe-2` (`col3` = "NGÀY VỀ" / arrival-date): the label sits in a data row of the **same chunk** → **YES**, an LLM can recover it when header+data are retrieved together.
- `xe-1` (the 233-char header-only chunk `col1|Tên kho|Mã hàng|Tên hàng|col5..col10`): the header was split into its own chunk; the data rows live in **separate, header-less chunks** → **NO** — neither the converter fix alone nor the prompt can rescue this at runtime. It needs **converter-merge + table-aware chunking (Block Integrity: never split header from its rows) + re-ingest**.

---

## 3. Per-phase status

### Input-data F-series + P0-series (CODE-SHIPPED, 0-regression)
| Item | Commit | Status |
|---|---|---|
| Structural table-header detection (col_N CRUX, trust separator) | `e0aa992` | ✅ |
| Multi-row table-header merge (heal split headers, real-data proven) | `9009eac` | ✅ RUNTIME-PROVEN |
| P0-2 lossless numeric-coverage observe-gate (anti number-HALLU) | `c2ea437`,`d7bd5ac`,`bd40990` | ✅ |
| P0-3 locale-keyed structure word-lists (default `vi` byte-identical) | `c521c37` | ✅ |
| P0-4 per-locale narrate prompts | `636d023` | ✅ |
| P0-5 worker byte-sniff routing | `83cdd49` | ✅ |
| P0-6 rename 5 version-ref constant files → purpose names | `51711e7` | ✅ |
| F4 surface REAL chunking strategy_used (was hardcoded literal) | `a66fc13` | ✅ |
| F5 dual-read verbatim original_content (default OFF, sacred#10-safe) | `b5ced79` | ✅ |
| F9 gate `_modality_boost` behind config (default OFF) | `bac1367` | ✅ |
| F10 embed/semantic-cache identity tuple (provider+model+dim) | `de33bbd`,`febd0ad` | ✅ |
| F12 detected_language → embedding model select (default byte-identical) | `cd81a6e` | ✅ |
| /sync default-model picks `kind='llm'` (first-chat 500 fix) | `c5529aa` | ✅ |
| Orphan LLM/rule strategy-selector marked DISABLED (comment, not deleted) | `018379a` | ✅ |
| **F7 attribute-generic stats** | `5db7922`→**reverted `9416f4d`** | ⛔ REVERTED (backfired price-coupling; needs alembic redesign) |

### fix-all plan Phase 0 / Phase 1 (CODE-SHIPPED earlier on branch)
| Stream | Status | Evidence |
|---|---|---|
| S0-A RLS hardening | ✅ | `24f2451` |
| S0-B provider revive (innocom + ZE re-point) | ✅ | `b318d9a`,`fc16f3b` |
| S0-C qwen3 capability-route + HALLU fail-closed | ✅ | `24f2451` |
| S0-D multi-turn reconcile | ✅ | `24f2451` |
| S1-A late-binding table (col_N converter + stats) | ✅ partial (F7 attribute-generic reverted) | `9009eac`,`e0aa992` |
| S1-B anti-fabricate floor | ✅ | `3097755` |
| S1-C lifecycle fail-loud (delete purge stats) | ✅ | `16710f3`,`3097755` |
| **S1-D OBS-1 empty-answer warn** | ✅ **NEW this session** | `647f4a2` |

### This session's additional ships
| Item | Commit |
|---|---|
| Domain-neutral scrub (brand literal `xe-3` → generic; guard green) | `2277979` |
| S1-D OBS-1 empty-answer silent-failure warn (sacred#10-safe, +6 tests) | `647f4a2` |

### Remaining — DEFERRED with rationale (NOT silently dropped)
| Item | Why deferred |
|---|---|
| **S1-D OBS-2** completion_tokens=0 (qwen3 streaming) | PARTIAL (`or 0` fallback exists). Touching token/cost accounting needs runtime measurement to validate counts — load-test gate. |
| **S1-E RQ-1** locale-driven BM25 | Current `simple` tsquery is language-AGNOSTIC (not broken). Making it locale-driven changes top_score distribution → **needs load-test recalibration** (no-guess-must-measure). Cannot verify offline. |
| **S1-E RQ-2** article-filter gate | NOT-FOUND in code — not a regression; no action needed. |
| **S2-A** retrieve.py god-node split (1852 lines) + decomposer merge | Phase-2 architectural (T3). Plan explicitly defers "tới T1≥95%". `condense_question` is **live, not dead** (re-checked) — do NOT remove. |
| **S2-B FMT-1** `local://` scheme | NOT-FOUND — needs a design decision (is `local://` a supported ingest scheme?). |
| **S2-B SB-4** SSRF webhook deliver-time validation | PARTIAL (Pydantic `AnyHttpUrl` format-only). Real gap, but a correct guard needs IP-range blocklist + DNS-rebind protection — deserves its own focused effort, not a half-baked inline check. **Recommended next.** |

---

## 4. 3-bot eval — failure-LAYER breakdown (latest 2026-06-29 golden runs)

| Bot | Raw | Provider-503 | Scorer-FP | Table-bind/col_N | Refuse-miss | Real accuracy (ex-provider/scorer) |
|---|---|---|---|---|---|---|
| spa (`test-spa-id`) | 45/50 = 90% | 0 | 0 | 4 (wrong-price mis-bind) | 1 | ~90% |
| inventory (`chinh-sach-xe`) | 19/40 = 48% | **10** | 0 | 10 (product-code lookup) | 1 | 19/30 ≈ **63%** |
| legal (`thong-tu-09-2020`) | 41/50 = 82% | 3 | ~4 (answers actually correct) | 0 | 1 | ≈ **90%+** |

**Reading:**
- **Provider (innocom 503) is the single biggest raw-score drag** — 13 transient errors across xe+legal. NOT an answer-quality problem; would pass on retry. (Infra/provider stability, not input-data.)
- **Scorer false-positives** inflate "legal mismatch" — e.g. bot said *"từ cấp độ 3 trở lên"* (exactly correct) and *"một năm một lần"* (correct) but the semantic scorer marked mismatch. Measurement artifact, not a bot error.
- **Table-row-binding / col_N is the real input-data failure layer** — xe's 10 *"mã hàng không tìm thấy"* (product-code) misses + spa's 4 wrong-price answers. This is exactly what the converter-merge + table-aware-chunking + re-ingest targets. **The fix is shipped and proven on the converter; the runtime lift requires re-ingest** (the eval ran on the OLD stored extraction — note in the golden header: "Stats-index CHƯA re-ingest").

---

## 5. Honest verdict

- **Code quality:** ✅ 67 commits, **0 NEW regression** (byte-identical failure-set proof), +29 net passing tests, all CLAUDE.md grep-guards green (0 version-ref filenames, 0 provider-hardcode, 0 brand literal in `src/ragbot`, domain-neutral guard PASS).
- **col_N:** ✅ root-caused (CODE, stacked multi-row header), ✅ fix shipped, ✅ **proven on real customer data** (7→1, zero API). ⚠ Stored data heals only on **RE-INGEST from original source** (owner-action).
- **Multi-bot compliance:** ✅ The fix is pure shape-logic (header continuation = fill empties without overlap), zero vocabulary, proven domain-neutral by the canary/property tests + the English-warehouse probe. It is **not** hardcoded for any bot.
- **What's NOT done & honest about it:** the runtime PASS-rate lift for xe is **gated on re-ingest** (col_N baked in stored chunks) and on **provider stability** (10/21 xe failures are innocom-503). Retrieval-quality items (RQ-1) and OBS-2 are **load-test-gated** and were not shipped speculatively (no-guess-must-measure). S2 architecture is Phase-2-deferred by the plan.

## 6. Recommended next steps (owner-gated)
1. **RE-INGEST `chinh-sach-xe` from the original source file** → measure col_N 3→~0 + xe table-lookup lift (the rule#0 runtime proof of the converter fix end-to-end). Needs owner to supply the source + ~277-chunk embed budget.
2. **Provider stability** for innocom 503 (retry/backoff already present — verify CB tuning) → recovers ≈10 xe + 3 legal raw failures.
3. **Table-aware chunking (Block Integrity)** so a header is never split from its rows (fixes the xe-1 header-only-chunk class) — pairs with #1.
4. **SB-4 SSRF webhook guard** as a focused security task (IP-range blocklist + DNS-rebind).
5. Tighten the eval **semantic scorer** to cut legal false-positives.
