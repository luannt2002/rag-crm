# Ingest / Upload flow — latest, with expert per-file scorecard

> Source-of-record for the document INGEST/UPLOAD flow as it runs today (verified by reading the 22
> files at file:line, 2026-06-23). Bar = **expert**, not merely "works". Each file is scored /10 with
> a verdict: **EXPERT (leave)** or **FIX**. Companion: `reports/INGEST_UNUSED_FUNCS_20260623.md`,
> `reports/EXPERT_DEEP_AUDIT_20260623.md` (Section A), plan `plans/20260623-ingest-flow-clean/`.

## 1. The flow (verified, latest)

```
HTTP — POST /api/ragbot/documents/create            (documents.py:91)
  ├─ 4-key resolve (record_tenant_id from JWT, bot_id/channel_type body)
  ├─ X-Idempotency-Key dedup                          (:117-161)
  ├─ ingest quota charge                              (:167)
  └─ IngestDocumentUseCase.execute                    (ingest_document.py:90-143)
        └─ ONE Unit-of-Work: INSERT Document(state=DRAFT, raw_content)
                            + INSERT jobs row
                            + INSERT outbox 'document.uploaded.v1'
        → HTTP 202 {document_id, state:"DRAFT"}
                              │
            outbox publisher  ▼  → Redis Stream  ragbot:documents:ingest
                              │
WORKER — run_embedded_document_consumer              (document_worker.py:672)
  ├─ source local://  → reuse documents.raw_content   (:297)        [never refetch]
  │  source http/Google → to_export_url + fetch       (:343-411)    [google_link_service]
  ├─ parser = detect_parser(mime, ext)                (:379)   ◀── NON-robust (no byte-sniff)
  │     ├─ registry HIT  → parser.parse() → list[dict]            (parsed_blocks = None)
  │     └─ registry MISS → OCR fallback                (:425)
  │                         → kreuzberg.parse → ParseResult.blocks  (parsed_blocks set :431)
  └─ doc_service.ingest(content=full_text, blocks=parsed_blocks)   (:544)  ◀── raw_bytes NOT passed → None
                              │
SERVICE — ingest_core.ingest()                       (ingest_core.py:177)
  ├─ if raw_bytes is not None: sniff_real_mime(...)   (:264)   ◀── SKIPPED (raw_bytes is None)
  ├─ size-guard MAX_DOCUMENT_CONTENT_CHARS=500_000
  ├─ dedup (content_hash + source_url)
  ├─ _route_through_parser   ONLY if raw_bytes != None ◀── SKIPPED
  ├─ U3 CLEAN (NFC + injection-strip)
  ├─ U4 CHUNK (AdapChunk — block-pipeline no-ops because parsed_blocks=None for registry formats)
  ├─ U5 ENRICH (contextual prefix, gpt-4.1-mini)
  ├─ U6 VN_SEGMENT (underthesea, vi-gated)
  └─ U7 EMBED + STORE → state DRAFT → active | failed   (ingest_stages_store.py:451-462 fail-loud)
```

**The structural problem in one sentence**: the **production path is the worker**, and the worker
**bypasses the robust type-detection (`sniff_real_mime` / `detect_parser_robust`) that the service
already owns**, by parsing first and calling `ingest(raw_bytes=None)`. The robust machinery exists
and is excellent — it just isn't on the hot path.

## 2. Expert scorecard (per file)

| File | Role / pattern | Score | Verdict | Action |
|---|---|---:|---|---|
| `routes/documents.py` | Canonical create — idempotency + quota + outbox | **9.0** | EXPERT | leave logic; comment cleanup only |
| `use_cases/ingest_document.py` | Unit-of-Work, exactly-once (DRAFT+jobs+outbox) | **9.5** | EXPERT | leave |
| `routes/sync.py` | Pre-parsed-text bulk path (B2B upstream) | **8.0** | EXPERT | comment EN; leave logic |
| `http/router.py` | Route wiring; stream-upload correctly unmounted | **9.0** | EXPERT | leave |
| `parser/registry.py` | Port + Strategy + Registry + Null; has robust+non-robust detect | **9.5** | EXPERT | leave |
| `parser/null_parser.py` | Null Object | **10** | EXPERT | leave |
| `shared/mime_sniff.py` | Robust sniff (magic + zip-manifest peek + UTF-8/CSV) | **9.5** | EXPERT | leave — but **must be called by the worker** (see fix #1) |
| `shared/tabular_markdown.py` | `rows_to_structured_markdown`, shape-based, domain-neutral | **9.0** | EXPERT | leave |
| `application/services/google_link_service.py` | `to_export_url` (Google viewer→export) | **9.0** | EXPERT | leave |
| `document_service/ingest_core.py` | U1 orchestration + robust sniff + dedup + size-guard | **8.5** | EXPERT | leave — robust path is bypassed by caller, not its fault |
| `document_service/__init__.py` | `_route_through_parser`, detect_parser_robust wiring | **8.5** | EXPERT | leave |
| `parser/docx_parser.py` | DOCX → structured markdown | **8.5** | FIX(low) | add typed Block emission (fix #3) |
| `parser/excel_openpyxl_parser.py` | XLSX → multi-table markdown | **8.5** | FIX(low) | add typed Block emission (fix #3) |
| `parser/google_sheets_parser.py` | CSV/Sheets → markdown | **8.5** | FIX(low) | add typed Block emission (fix #3) |
| `parser/kreuzberg_markdown_parser.py` | PDF/PPTX/HTML → markdown | **8.5** | FIX(low) | add typed Block emission (fix #3) |
| `parser/markdown_parser.py` | MD/TXT → section markdown | **8.0** | EXPERT | comment EN; leave |
| `parser/pdf_parser.py` | PDF fallback parser | **8.0** | EXPERT | leave |
| `document_service/ingest_stages_store.py` | U5–U7 embed/store; fail-loud | **8.0** | FIX(med) | bound `late_chunking` memory (fix #4) |
| `interfaces/workers/document_worker.py` | Consumer adapter; parse→ingest | **6.0** | **FIX(high)** | use robust detect + thread bytes (fix #1) |
| `ocr/kreuzberg_parser.py` | OCR adapter; `_suffix_for_mime` | **6.5** | **FIX(high)** | suffix map forces `.docx`; add xlsx/pptx/OLE2 (fix #2) |
| `ocr/ocr_factory.py` | OCR factory + fallback | **7.0** | FIX(low) | docstring/behavior drift (fix #5) |
| `routes/documents_stream_upload.py` | Disabled alt upload | **7.0** | FIX(low) | document-as-disabled or ADR-remove (fix #6) |

**Flow-level grade: 8.2/10 architecture · ~7.0/10 effective** (the worker's bypass of the robust path
is what separates "designed expert" from "runs expert"). To be *expert in production*, close fixes #1–#3.

## 3. What is already EXPERT — leave it alone

- **Idempotency + Unit-of-Work + outbox** (`documents.py` + `ingest_document.py`): exactly-once,
  202-async, no sync ingest > 30s. Textbook. Do not touch.
- **Port + Strategy + Registry + Null Object** in the parser layer (`registry.py`, `null_parser.py`):
  adding a format = one adapter file, zero orchestrator change. Exemplary Open-Closed.
- **Robust detection primitives** (`mime_sniff.py`, `ingest_core.py:264`): `sniff_real_mime` does
  magic-byte + `[Content_Types].xml` zip-subtype peek + UTF-8/CSV heuristic. This is the *correct*
  SOTA approach (don't trust mime/ext alone). The code is right — the only sin is the worker not using it.
- **Domain-neutral tabular markdown** (`tabular_markdown.py`): shape-based, no brand vocabulary.
- **Fail-loud finalize** (`ingest_stages_store.py:451-462`): embed failure → `state='failed'` + raise,
  not a silent DRAFT. Correct graceful-degradation boundary.
- **Google export rewrite** (`google_link_service.py`): kills the HTML-login-interstitial retry-storm.

## 4. What must be FIXED to be expert (6 items, at the correct layer)

### FIX #1 — Worker must use robust detection (HIGH · `document_worker.py:379,544`)
**Gap**: worker calls `detect_parser(mime, ext)` (non-robust) and `ingest(raw_bytes=None)`, so the
service's `sniff_real_mime` + `_route_through_parser` never fire on the production path → two parse
paths diverge.
**Expert fix**: either (a) replace with `detect_parser_robust(mime, ext, raw, detector=detect_parser)`
after fetch (bytes already in hand at `:390`), or — cleaner — (b) thread `raw_bytes=raw` into
`doc_service.ingest(...)` and **delete the worker-local parse block** so the service is the single
parse source-of-truth. Prefer (b): one parse path, robust by construction.
**Why expert**: single-source-of-truth + the sniff already exists; removes a whole class of
sync/async drift bugs. **A/B**: ingest the same octet-stream URL before/after; assert correct parser chosen.

### FIX #2 — OCR `_suffix_for_mime` must not force `.docx` (HIGH · `kreuzberg_parser.py:96-118`)
**Gap**: every empty-mime `PK\x03\x04` zip → `.docx`; no xlsx/pptx/OLE2 branch. An octet-stream XLSX
URL → parsed as DOCX → garbage. `.doc/.xls` (OLE2 `\xd0\xcf\x11\xe0`) → `.bin` → empty-text error.
**Expert fix**: route the OCR fallback through `sniff_real_mime`/`_peek_zip_office_subtype` (already
written) so the zip subtype is read from `[Content_Types].xml`; add an OLE2 magic branch (→ explicit
`UNSUPPORTED_LEGACY_FORMAT` or a LibreOffice-headless convert). One detection SoT shared by registry +
OCR. **Why expert**: byte-truth over guessed-extension; eliminates the misroute class.

### FIX #3 — Structured parsers should emit a typed Block stream (root of AdapChunk B-2)
**Gap**: registry parsers return `list[dict]`; `parsed_blocks=None` unless OCR-routed → AdapChunk L2
block-pipeline no-ops for every registry format (DOCX/XLSX/CSV/Sheets/HTML/MD/TXT).
**Expert fix**: extend `DocumentParserPort` with an optional typed Block list (or a `ParsedDocument`
carrying `.blocks`); have each structured parser populate HEADING/TABLE/TEXT with `is_atomic`. This is
the **single highest-leverage fix in the whole ingest+chunking area** — it simultaneously unblocks
AdapChunk L2/L3/L6/L7 (audit B-2/B-3/B-4/B-5). The program charter explicitly sanctions this as the
one local parser-adapter rewrite. **Why expert**: structure-truth flows end-to-end instead of being
re-guessed from markdown downstream.

### FIX #4 — Bound `late_chunking` memory (MED · `ingest_stages_store.py:319` + `late_chunking.py:99`)
**Gap**: default path embeds the whole-doc chunk list in one in-memory call; a 224KB sheet → thousands
of chunks → RSS spike + no mid-doc progress.
**Expert fix**: run `late_chunk_embed` in `DEFAULT_EMBED_DOC_BATCH_SIZE` slices (mirror
`_embed_in_doc_batches`) + a config-gated max-chunks-per-document guard. **A/B**: ingest a large sheet,
sample RSS before/after.

### FIX #5 — `ocr_factory` docstring/behavior drift (LOW · `ocr_factory.py:11-21` vs `:57-74`)
**Gap**: docstring says "fail-loud, raises ImportError" but the Kreuzberg branch silently falls back to
`SimpleTextParser` (WARN only) — a downgraded parser can serve production unseen.
**Expert fix**: pick one contract — either re-raise for the configured engine (true fail-loud) or fix
the docstring + add a `/health/models`-style preflight asserting resolved engine == configured engine.

### FIX #6 — Document the disabled stream-upload module (LOW · `documents_stream_upload.py`)
**Gap**: retained but unmounted; reads like live code. **Expert fix**: a one-line module docstring
"DISABLED — not mounted (router.py), no consumer; retained per CLAUDE.md keep-test-code", or an ADR to
remove it. Behavior-neutral.

## 5. Comment standardization (this flow, Phase 2)
Per `plans/20260623-ingest-flow-clean/plan.md`: translate VN→EN, strip temporal/version refs
(e.g. `documents.py` "260525 Bug #2 endpoint", `sync.py` Vietnamese docstrings), add module/function
docstrings (purpose + contract + WHY). **No logic change.** Executed file-by-file with `pytest` green
after each batch.

## 6. Priority order
1. **FIX #3** (parser→Block) — unblocks ingest *and* chunking (biggest T1 lever).
2. **FIX #1 + #2** (worker robust detect + OCR suffix) — closes the multi-format misroute class.
3. **FIX #4** (late_chunking bound) — scalability.
4. **FIX #5 + #6** + comment standardization — hygiene.
