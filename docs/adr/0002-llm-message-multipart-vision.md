# ADR: LLMMessage.content accepts multipart (vision) content

Status: Accepted
Date: 2026-06-21
Stream: Multimodal VLM (Phase 1) — `plans/20260621-multimodal-vlm/`

## Context

Ragbot is text-only: it drops images. The one capability genuinely absent vs
RAG-Anything is VLM captioning (turning a chart/scanned table/diagram into faithful
retrievable text). The ingest scaffolding for this is already wired — `NarrateServicePort`
routes `IMAGE` blocks, `_BLOCK_PROMPTS["IMAGE"]` exists, OCR adapters emit
`Block(type="IMAGE")`, and `ai_models.supports_vision` / `tenant_policy.can_vision`
columns exist — but **no pixels can reach a model**, because the LLM Port's message type
carries text only:

```python
@dataclass(frozen=True, slots=True)
class LLMMessage:
    content: str        # ← text only
```

Every LLM call in the platform flows through this Port (`application/ports/llm_port.py`)
and the single implementation `DynamicLiteLLMRouter`. LiteLLM (the wire layer) already
accepts the OpenAI multimodal message shape — a `list` of content parts mixing
`{"type":"text",...}` and `{"type":"image_url","image_url":{"url":"data:image/...;base64,"}}`.
The router forwards content verbatim (`{"role": m.role, "content": m.content}`,
`dynamic_litellm_router.py:1023` + `:1151`). So the only thing standing between the
platform and vision is the `str` type on `LLMMessage.content`.

## Decision

Widen the field to accept either shape:

```python
content: str | list[dict[str, Any]]
```

`str` stays the default and the overwhelming case. A `list[dict]` is the OpenAI
content-part shape, forwarded unchanged by the router to LiteLLM.

This is the enabling change for the multimodal track. It is deliberately the MINIMUM:
it does not add a VLM adapter, construct any vision message, or change any caller — those
land in Phase 2 (`vlm_image_parser`) alongside the model guard.

## Why this shape (not alternatives)

- **Not a new `VisionMessage` type / parallel Port.** That would fork every call site and
  the router. Widening one field reuses the entire existing path (resolver, router,
  streaming, structured-output) for free — true strangler-fig.
- **Not base64 smuggled inside a `str`.** LiteLLM expects the structured content-part
  list; encoding images inside a text string would mean re-parsing and lose provider
  portability.

## Consequences

- **Backward-compatible.** All 18 `LLMMessage(...)` construction sites pass `str` and are
  unaffected (verified: `tests/unit/` LLM/router suite 293 passed, 0 failed after the
  change). `frozen/slots` dataclass behaviour unchanged.
- **Token accounting safe.** The TPM limiter already wraps content in `str(...)` before
  `len()` (`tpm_rate_limiter.py:94`), so a list content over-counts slightly rather than
  crashing — acceptable, refined later if needed.
- **Safety deferred to the construction site (Phase 2), by design.** The type change alone
  enables nothing unsafe: no caller constructs a multipart message yet. When the VLM
  adapter does, it must resolve a vision-capable model (`supports_vision=true`, locked to
  `gpt-4.1-mini` per the cost constraint) and fail loud otherwise — a multipart message is
  never silently sent to a text model. That guard ships WITH the first vision call, not
  speculatively here.

## Reversibility

Narrow the annotation back to `str` if multimodal is abandoned; since no caller passes a
list until Phase 2, reverting before Phase 2 is a one-line no-op. After Phase 2 the VLM
adapter would also be removed (its own registry line).
