# [T1-Smartness] Multimodal VLM — caption images so they become retrievable text

**Goal:** the one genuinely-absent capability vs RAG-Anything — turn images (standalone
uploads AND images embedded in PDF/DOCX) into faithful text via a vision model, so a
question about a chart/diagram/scanned table can be answered. Strangler-fig: extend the
existing narrate/parser Ports + add ONE VLM adapter, orchestrator untouched.

**Tier:** T1 (coverage of a modality we drop today). **Sacred:** HALLU=0 — a VLM caption
must be faithful (no inventing chart numbers); refusal-on-low-confidence over fabrication.

## Why this is an EXTENSION, not a build-from-zero (architecture map 2026-06-21)
The scaffolding is wired; only the VLM call is missing ("dormant-not-absent"):
- `NarrateServicePort.narrate(content, block_type)` already routes `"IMAGE"` blocks —
  `application/ports/narrate_port.py:43`.
- `LLMNarrateGenerator._BLOCK_PROMPTS["IMAGE"]` prompt exists but is fed OCR TEXT, not
  pixels — `infrastructure/narrate/llm_narrate.py:69`.
- OCR adapters classify images as `Block(type="IMAGE", is_atomic=True)` but `content` =
  Tesseract OCR text — `infrastructure/ocr/kreuzberg_parser.py:296`, `docling_parser.py:148`.
- DB already has `ai_models.supports_vision` (`models.py:560`) + `tenant_policy.can_vision`
  (`models_monitoring.py:257`) — STORED but ingest never consumes them.
- `Block.references` reserved for "M19 figure↔caption triples" (`dto/block.py:79`) — planned.
- **The single enabling gap:** `LLMMessage.content` is `str` only (`ports/llm_port.py:19`)
  → cannot carry an image. LiteLLM/`DynamicLiteLLMRouter` (`dynamic_litellm_router.py:512`)
  already speaks the OpenAI vision format if the message carries `list[dict]` content parts.

## Phase 0 — measure-first: fixture + eval (the gate, BEFORE any code)
- [ ] Add a tiny test fixture: a PNG/JPG with KNOWN content (e.g. a simple bar chart with
      labelled values, or a scanned table) under `tests/fixtures/multimodal/`. None exists today.
- [ ] Author a VLM-caption eval: image → expected facts the caption must contain (e.g. the
      axis labels / the 3 bar values). Faithfulness check: the caption must NOT state a value
      absent from the image (HALLU trap = a chart with no legend → must not invent numbers).
- [ ] Baseline: today the image yields empty/OCR-only text → 0 coverage. Record it.

## Phase 1 — enable vision messages in the LLM Port (the one architectural change)
- [ ] Extend `LLMMessage.content` to `str | list[ContentPart]` where `ContentPart` is the
      OpenAI vision shape (`{"type":"text",...}` / `{"type":"image_url","image_url":{"url":
      "data:image/png;base64,..."}}`). Backward-compatible: plain `str` still valid.
      File: `application/ports/llm_port.py:19`.
- [ ] `DynamicLiteLLMRouter.complete_runtime` (`dynamic_litellm_router.py:512`) passes
      multipart content through to `litellm.acompletion` (LiteLLM handles it natively).
      Verify no str-only assumption downstream (token counting, cache key, logging).
- [ ] Model resolver already returns `LLMSpec.supports_vision` (`_binding_mixin.py:221`) —
      add a guard: a vision message requires `spec.supports_vision=True`, else fail loud.
- [ ] **ADR**: this Port-shape change is hard-to-reverse + surprising → write a short ADR
      (`docs/adr/`) for `LLMMessage` multipart content. (Meets the 3-condition ADR bar.)
- **DONE if:** an existing text completion is byte-identical (no regression) AND a unit test
      proves a base64 image round-trips to litellm in OpenAI vision shape.

## Phase 2 — vertical slice: image-only uploads (Option A, prove end-to-end)
- [ ] New adapter `infrastructure/parser/vlm_image_parser.py` implementing `DocumentParserPort`
      (`ports/document_parser_port.py:22`): `supports()` → image/png|jpeg|tiff|webp;
      `parse(bytes)` → base64-encode → `LLMPort.complete()` with a vision message +
      faithful-caption prompt → return `[{"content": caption, "metadata": {...}}]`.
- [ ] Register `"vlm_image"` in `infrastructure/parser/registry.py:43` BEFORE
      `kreuzberg_markdown` (so it wins for image MIMEs). Zero orchestrator edits (the
      registry's own contract — registry.py:7).
- [ ] Null-object default + config gate: `system_config.vlm_provider` (`"null"` default →
      OFF, no behaviour change) + per-tenant `can_vision`. Cost cap: cheap vision model.
- **DONE if:** uploading the Phase-0 fixture image ingests → chunk with the faithful caption
      → a question about the chart is answered from it; HALLU trap (no-legend chart) refuses;
      existing non-image ingest byte-identical (no-regression).

## Phase 3 — embedded images inside PDF/DOCX (Option B, the real RAG-Anything parity)
- [ ] Extend the OCR adapters (`kreuzberg_parser.py`, `docling_parser.py`) to ALSO emit the
      image BYTES (base64 in `Block.ocr_metadata` or a new `Block.image_b64` field) for
      `IMAGE` blocks — today they emit only OCR text.
- [ ] New `infrastructure/narrate/vlm_narrate.py` implementing `NarrateServicePort`: for
      `block_type=="IMAGE"` with image bytes present → VLM caption; else fall back to the
      existing OCR-text narrate. Register `"vlm"` in `infrastructure/narrate/registry.py`.
- [ ] Operator enables via `system_config.narrate_provider="vlm"` +
      `narrate_then_embed_enabled=true` (already the wired gate — `document_worker.py:427`).
- **DONE if:** a PDF with an embedded chart ingests with the chart captioned in its chunk;
      A/B on the Phase-0 eval shows image-coverage↑, HALLU=0, text-only docs no-regression.

## Cross-cutting constraints
- HALLU=0 sacred: the caption prompt must forbid inventing values absent from the image;
  add a no-legend/ambiguous-image HALLU trap to the eval and gate on it.
- Port+Strategy+DI: VLM is a registry adapter + Null default OFF; config-flip, no redeploy.
- Domain-neutral; zero-hardcode (model/provider/threshold via config); cost-capped (cheap
  vision model, per-tenant `can_vision`, opt-in).
- No app-inject/override: the caption is INGEST-side text that becomes a normal chunk; the
  answer path is unchanged (the LLM answers from retrieved caption-chunks like any other).
- Each phase its own commit; ship the vertical slice (Phase 2) before the harder Phase 3.

## Open questions for approval
- **Vision model choice**: reuse an existing vision-capable binding (the resolver already
  has `supports_vision`) — confirm a vision model is provisioned, or that is a prerequisite.
- **Scope this session**: Phase 0 (fixture+eval) + Phase 1 (port+ADR) are the safe first
  unit; Phase 2/3 are follow-on. Confirm starting at Phase 0/1.
