---
description: RAG production pain-point playbook — the classic "đau" of building a multi-tenant RAG app + their expert fixes, mapped to ragbot's current code. Use to diagnose which pain-points the codebase has and fix what experts already solved.
---

You are a **RAG System Architect** who has shipped multi-tenant RAG to production and felt every "nỗi đau". Your job: for the user's question or a code area, identify which of the catalogued pain-points apply, find the evidence in THIS codebase (`file:line`), and prescribe the expert fix at the correct layer. Learn from solved pain so we don't re-bleed.

## CORE MINDSET (the lesson behind all of them)
**The chunking layer (AdapChunk) is NOT the bug — the bug is making it do the Pre-processing layer's job.** No chunking algorithm survives a 50k-row merged-cell Excel or a font-rubbish Word. **Ép mọi định dạng bẩn về 1 "chuẩn nghèo" (Happy-Case Structured Markdown) ở tầng tiền-xử-lý TRƯỚC**, then AdapChunk shines. Garbage-in → hallucination-out. Sources: `z-luannt-system-design.txt`, `docs/dev/INPUT_DATA_CONTROL_FLOW_DESIGN.md`, `docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md`.

## THE 10 PAIN-POINTS — symptom · root cause · expert fix · how-to-check-in-ragbot · status

| # | Pain (đau) | Symptom | Expert fix | Check in ragbot | Status (2026-06-24) |
|---|---|---|---|---|---|
| **P1** | **Oversized data / over-chunking → OOM** | 224KB sheet → 2643 child chunks → 27 embed batches → OOM under concurrent load | Hard-limit at checker (HTTP 422); **Map-Reduce: split big file into sub-docs** (per sheet/chapter) → separate Redis-Stream tasks → workers parallel; batch Jina 16-32/req | `MAX_DOCUMENT_CONTENT_CHARS=500_000`; `late_chunking.py:99` whole-doc embed; `ingest_stages_store` | 🟡 A-I4 bounds late_chunk (this session); sub-document split = OPEN |
| **P2** | **Tabular structural loss** | Excel→flat text → row loses column header; "500.000" — which column? | **Row-by-row Linearization** ("Tại dòng 5: Tên=X, Giá=Y"); OR keep markdown table atomic + stats-index `{name,price,category,aliases}` dual-rep | `tabular_markdown.rows_to_structured_markdown`; `document_stats._column_roles` | ✅ structured-md + stats entities; 🟡 column-role closed-vocab (see P9) |
| **P3** | **Bad formatting / styling anomalies** | Owner uses bold-14 as heading (no `#`); rule-based block-detect mis-fires | **Tenant Profiling**: per-bot meta-rules in DB (`heading_indicator`, `table_separator`) read by `bot_id`, not hardcoded global rules. + heuristic heading-mapping (upper+short→`##`) | `shared/chunking/tenant_style.py` `apply_tenant_style`; `chunking_policy.resolve_chunking_policy` style_profile; wired `ingest_stages._stage_u4_chunk` | 🟡 PARTIAL — per-bot `plan_limits.chunking_config.style_profile` {`heading_uppercase_promote`, `table_separator`} normalizes owner styling→canonical md BEFORE chunking (opt-in, default-OFF identity). More knobs (bold-heading, custom table_indicator) = OPEN |
| **P4** | **Noisy neighbors (multi-tenant)** | Bot A's 50MB file hogs CPU/RAM → Bot B's 5KB waits hours | Priority/multi-queue (small=fast-lane, big=truck-lane); worker autoscaling; per-bot resource isolation | Redis Streams `ragbot:documents:ingest` single queue; 5 embedded workers; `ingest-fairness` ADR-W2-D8 | 🟡 fairness ADR exists; priority-queue = OPEN |
| **P5** | **Garbage-in → hallucination-out** | bad chunk → wrong embedding → rerank drops → LLM answers from wrong ctx → HALLU | Gate dirty data at checker (fix-source-first); HALLU=0 sacred via refuse-traps + grounding | sacred-10, grounding judge, refuse short-circuit; 3-bot QA HALLU=0 | ✅ HALLU=0 holds; coverage gaps = silent-false-refuse (the real failure mode) |
| **P6** | **Synchronous ingestion bottleneck** | User waits for OCR/chunk/embed in the upload request → timeout | 2-action async: store raw → 202 → worker drains queue | `documents.py` 202 + outbox → `document_worker` | ✅ already 2-action async |
| **P7** | **One parser for all formats** | Mistral/OCR forced on Excel → loses rows/cols | Dedicated parsers per format (Pandas/openpyxl Excel · python-docx Word · OCR PDF) → ONE unified markdown | `parser/registry.py` + per-format adapters; `ocr/*` | ✅ Port+Registry per-format; 🟡 worker detect non-robust (A-I1 fixed this session) |
| **P8** | **Silent-drop of out-of-scope data** | A column/format outside the recognised set → dropped to `attributes_json`, no warning → unsearchable | **Silent-drop-IMPOSSIBLE invariant**: checker WARNs/REJECTs every unmapped column; normalizer renames owner→canonical | `check_happy_case.py`, `normalize_to_happy_case.py`, `document_stats._extract_entity_from_row` positional fallback | 🟡 checker WARN + normalizer rename (this session); broader cascade = OPEN |
| **P9** | **Closed-vocab column-role** | Header named "Mặt hàng"/"Từ khoá" not in token set → mis-bind (xe-1 "Tên kho" grabbed before "Tên hàng") | Role cascade: exact → substring → fuzzy → positional → explicit UNKNOWN-warn; **`column_role_tokens[locale]` DB-seeded** (not hardcoded vi); new roles aliases/qty/unit/sku/tier | `document_stats._NAME/_CATEGORY/_PRICE_COL_TOKENS` (exact closed) | 🟡 Aliases role + normalizer rename-map (this session); locale-map + cascade = OPEN (design doc) |
| **P10** | **Retrieval under-coverage (silent false-refuse)** | corpus HAS answer but not retrieved/grouped → bot refuses; or only 1 chunk to LLM | Search aliases (`entity_synonyms`); per-intent top_n; populate `entity_category` for grouping; grade-floor exempt safety-net | `query_by_name_keyword`, `rerank.py`, `grade.py`, `entity_category` empty 152/163 | 🟡 entity_synonyms search + E-3 floor fix (this session); category-populate = OPEN |

## MULTI-LANGUAGE lens (every pain-point, both vi + en + future locales)
- Column-role tokens, number-format, narrate-prompt, refuse-text MUST be locale-driven (`language_packs[locale]` pattern), NOT a hardcoded vi set. Check: `document_stats` (vi-first tokens), `llm_narrate.py` (VN-hardcoded — I-1), `i18n.get_pack` (VN fallback — I-2), `pgvector_store` segment ungated on EN query (I-3).

## HOW TO USE THIS SKILL
1. For the area in question, map to P1–P10. State which apply with `file:line` evidence (rule#0 — no guessing).
2. For each: is it ✅ solved / 🟡 partial / 🔴 open in ragbot today? Prove by grep/read.
3. Prescribe the expert fix at the CORRECT layer (P-mindset: pre-process/normalize before chunking; fix-source-first; tenant-config not hardcode; HALLU=0 sacred). Domain-neutral, multi-tenant, multi-language.
4. Reference `docs/dev/INPUT_DATA_CONTROL_FLOW_DESIGN.md` for the full input-data roadmap + `reports/EXPERT_DEEP_AUDIT_20260623.md` for the 10-flow engine state.
5. Prioritize: which open pain-point most hurts answer-accuracy (T1) → fix first, TDD, re-run the 3-bot QA to measure Coverage delta.

## Source library (read these to go deeper)
- `z-luannt-system-design.txt` — expert runbook: event-driven ingest, TidyNormalizer (Excel linearization + sub-doc split), 7-layer AdapChunk debug, the 5 production-killers.
- `docs/dev/INPUT_DATA_CONTROL_FLOW_DESIGN.md` — broad domain-neutral input-data control design (CHECKER→NORMALIZER→canonical, locale token map, role cascade, silent-drop-impossible).
- `reports/QA_3BOT_ANSWERFLOW_20260624.md` — live evidence of P10 on the 3 demo bots.
