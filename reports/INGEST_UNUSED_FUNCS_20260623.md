# Ingest flow — unused-function report · 2026-06-23

> Scope: the 22 files of the INGEST/UPLOAD flow. Method: for every `def` (excluding dunders),
> grep the whole `src/` tree for call-sites (excluding the definition line). A function with **0**
> in-`src` references is a *candidate*; each candidate is then VERIFIED by hand for the two common
> false-positive classes before being declared dead.
>
> **rule#0 / safety**: a naive "0 call-sites" list is dangerous here — it flags framework-registered
> route handlers and Port/Protocol methods that ARE used (by the framework / polymorphically).
> Commenting those out would break the app or the interface contract. Every candidate below was
> re-read at file:line before classification.

## Result: 0 truly-dead local functions in the ingest files

| Candidate | file:line | Naive verdict | VERIFIED verdict | Why NOT dead |
|---|---|---|---|---|
| `rechunk_document_by_id` | `interfaces/http/routes/documents.py:277` | 0 call-sites | **LIVE** | FastAPI route handler — registered via `@router.post(... dependencies=[...])`; invoked by the framework, never by name |
| `delete_documents` | `interfaces/http/routes/sync.py:676` | 0 call-sites | **LIVE** | FastAPI route handler — `@router.delete("/documents", ...)`; framework-invoked |
| `supported_mimes` | `infrastructure/ocr/kreuzberg_parser.py:154` | 0 call-sites | **INTERFACE (keep)** | Implements `OCRPort` Protocol method (`application/ports/ocr_port.py:28`); also implemented by `simple_text_parser.py:274` + `docling_parser.py:56`. No current caller, but it is the interface contract — do NOT delete a single impl |

**Conclusion**: every local `def` in the ingest files has at least one legitimate use. There is **no
local function to comment-out** in this flow. The naive scan's 3 hits are all false-positives
(2 route handlers + 1 Protocol method).

## Genuinely dead / orphan code in the ingest+chunking area (module-level — from the 2026-06-23 deep audit)

These are real dead/orphan units, but at the **module/symbol** level (not a local helper in the 22 files):

| Unit | Where | Status | Disposition |
|---|---|---|---|
| `documents_stream_upload.py` (whole route module) | `interfaces/http/routes/` | **Disabled** — not mounted (`router.py:64`), target stream `document.upload_stream.v1` has no consumer | Retained intentionally (CLAUDE.md: keep test code). Leave; document as disabled. |
| `infrastructure/chunking_strategy/` (LLM Strategy Selector) | `llm_resolver.py`/`registry.py`/`rule_resolver.py` | **Orphan** — 0 src callers (built `230d041`/`8371017`); U4 calls `select_strategy()` directly | Decide via ADR: wire into U4 **or delete**. (Audit finding B-1) |
| `smart_chunk_atomic()` | `shared/chunking/__init__.py:653` | **Orphan** — 0 callers; atomic-protect path uses the str variant | Becomes live once parser emits Block stream (B-2/B-3). |
| `rrf_round_robin()` | `orchestration/nodes/rrf_round_robin.py` | **Orphan** — 0 prod callers (built + tested) | Wire for comparison/multi_hop intents or delete (audit finding E-1) |

## `supported_mimes` — interface note
`OCRPort.supported_mimes` is declared in the Protocol and implemented by all 3 OCR adapters but has
**no call-site**. This is a latent interface method. Options: (a) keep as forward-looking contract;
(b) if the registry never dispatches on it, trim it from the Protocol + all 3 impls in one change
(interface-level, behavior-neutral). Recommend (a) until a mime-routing use appears.

## Method caveat (rule#0)
This scan is token-grep based: it can MISS a function referenced only via `getattr`/string dispatch
(none found in these files) and treats test-only usage as 0-in-src (none of the 3 candidates were
test-only). The verified result stands for the 22 ingest files; a full-repo dead-code pass (e.g.
`vulture`/`ruff --select F401,F811`) is the next-level tool if a repo-wide sweep is wanted.
