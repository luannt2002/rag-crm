# [T1/T2] Fix-all master — close every open item from the 2026-06-21 QA + tracks

Consolidated close-out of everything diagnosed this session. Each wave is its own
fresh session with a ready D13/EVAL gate. Ordered by value × readiness. Discipline
unchanged: measure-first, A/B gate, HALLU=0 hard, no sysprompt-patch / no answer-override,
each item its own commit, one bot at a time.

Anchors: `reports/qa_live/QA_LIVE_VERDICT_20260621.md`, `ROOTCAUSE_xe_price_20260621.md`,
`plans/20260621-retrieval-fix-qa/plan.md` (legal/spa diagnoses), `plans/20260621-multimodal-vlm/`.

---

## Wave A — Multimodal close (finish the started track) · effort: S+M
Adapter + port + model + spike already shipped (`d7e4db2`); only the wiring remains.
- **A1 (S) — worker image-MIME branch.** In `document_worker.py` parse stage (~line 322):
  when MIME is an image AND `vlm_provider != "null"`, resolve a vision spec
  (`container.model_resolver().resolve_llm(bot, tenant, intent="enrichment")` — same as
  narrate at :430) and `build_parser("vlm_image", llm=container.llm(), spec=…,
  record_tenant_id=…, trace_id=…)` instead of `detect_parser`. Config gate
  `system_config.vlm_provider` (null default = OFF, no behaviour change).
  **Gate:** ingest `tests/fixtures/multimodal/price_table.png` via real `POST
  /documents/create` → chunk carries the caption (3 values) → `EVAL_SPEC.md` PASS
  (coverage + blank-panel HALLU trap) · non-image ingest no-regression.
- **A2 (M) — Phase 3 embedded images.** OCR adapters (`kreuzberg_parser.py`,
  `docling_parser.py`) emit image bytes (base64 in `Block.ocr_metadata`) for IMAGE
  blocks; new `vlm_narrate.py` (`NarrateServicePort`, registered "vlm") captions them;
  enable via `narrate_provider="vlm"`. **Gate:** a PDF with an embedded chart captioned.

## Wave B — Legal correctness (data/ingest — proven NOT query-fixable) · effort: M
4 query-levers failed (bm25/HyDE/HyDE-sim/hybrid — `plan ff08f0c` update). Fix at data.
- **B1 (M) — clause-level re-chunk for the MFA rule.** Re-ingest the legal doc with a
  chunking that isolates Điều 30 khoản 6 ("hệ thống cấp độ 4 trở lên phải áp dụng xác
  thực đa yếu tố khi truy cập quản trị") as its OWN retrievable unit, with a lead that
  surfaces the {control=MFA, threshold=cấp độ 4} pair (contextual enrichment U5 should
  emit "Xác thực đa yếu tố — bắt buộc cho hệ thống cấp độ 4 trở lên" as the chunk head).
  Also suppress the distractor: the "cấp độ 2 … biện pháp an toàn" chunk must not be the
  top hit for an MFA-threshold query. **Gate:** D13 legal d01 PASS (answer "cấp độ 4",
  chunk-289-equiv retrieved) · existing legal D13 + 42-q no-regression · HALLU=0.
  *(Alt if re-chunk insufficient: query-understanding node extracting control+ask →
  targeted clause lookup — larger effort, only if B1 fails the gate.)*
- **B2 (S) — citation-strip.** Stop "đoạn N" (DB chunk index) reaching the answer as a
  legal reference. Strip the "Đoạn N thuộc phần…" narration LEAD at context-build (keep
  the rest of the narration) so only real "Điều X" is citable. Context-layer change, NOT
  an answer-override. **Gate:** 0 "đoạn N" citations in legal answers · no-regression on
  the 3 bots (narration-lead strip applies platform-wide).

## Wave C — Spa coverage (extraction — min-len trade-off, not query-fixable) · effort: M
- **C1 (M) — zone entities get a category.** The triệt-lông zone rows (Mép/Nách/Mặt) have
  EMPTY `entity_category` and 3-char names; lowering the reverse-match min-len over-matches
  ("da mặt"↔zone). Fix at extraction: the stats-index populate should set
  `entity_category="triệt lông"` (or a full name "Triệt lông mép") derived from the source
  chunk's section context, so a listing query forward-matches by category and single-zone
  queries match the full name — no risky bare-3-char reverse-match. **Gate:** D13 spa
  d02/d04 (Mép/Mặt) PASS · spa 42-q + price-stability + booking no-regression · HALLU=0.
- **C2 (S) — listing multi-chunk.** Listing/enumeration intent gathers ALL sibling chunks
  of the category (raise top_k for listing and/or parent-expand) so "liệt kê dịch vụ X"
  doesn't return one chunk. **Gate:** D13 spa d05 PASS, no-regression.

## Wave D — Capability (dormant-not-absent, gated) · effort: M-L
- **D1 (M) — KG backfill, legal-first.** Probe validated legal KG faithful (`52752cb`).
  Flip `graph_rag_default_mode` (alembic) + backfill `knowledge_edges` from existing chunk
  text (no re-ingest) for legal ONLY + per-bot retrieval enable + narration-lead filter.
  **Gate:** ≥3 multi-hop legal queries answerable (was refuse), Wilcoxon p<0.05, HALLU=0,
  42-q 1.00 hold. Catalog bots SKIP (probe: noise).
- **D2 (M) — chunking-activate L4/L7.** AdapChunk ekimetrics selector (L4) + context-aware
  narrate (L7). Gated on intrinsic SC/CC + the D13 sets. Chunking already good (≈AdapChunk)
  so this is a refinement, lowest priority.

---

## Recommended sequence
A1 (finish multimodal, smallest, started) → B2 + C2 (the two "S" context/top_k wins) →
B1 + C1 (the two "M" data/re-ingest fixes — the real legal/spa correctness) → D1 (KG) →
A2 + D2 (follow-ons). Each is one fresh session; none is a marathon-tail task.

## What is ALREADY done (not in scope here)
xe price fabrication FIXED + verified (`2ae5331`, D13 0.14→0.86, HALLU=0); the D13
conversational gate (all 3 bots); B-2 rigor harness; multimodal Phase 0/1 + adapter +
spike; measurement-rigor + KG-probe + ops findings. See STATE_SNAPSHOT.md.
