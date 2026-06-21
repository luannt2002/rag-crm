"""VLM image parser — caption an uploaded image into faithful retrievable text.

Multimodal Phase 2 (plan `20260621-multimodal-vlm`). An image-only upload (PNG/JPEG/…)
carries no extractable text today, so it is effectively dropped. This adapter sends the
image to a vision-capable model (gpt-4.1-mini) via the LLM Port's multipart content
(ADR 0002) and returns the caption as the document's content, so it chunks + embeds like
any other text — the answer path is unchanged.

Strangler-fig: one registry adapter, orchestrator untouched. The LLM + spec are injected
(the worker resolves a vision model, same pattern as `build_narrate`'s `llm`/`spec`). A
non-vision spec is rejected fail-loud at construction so a multipart (image) message is
never silently sent to a text model (sacred: no silent wrong-model send).
"""
from __future__ import annotations

import base64
from typing import Final

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMPort
from ragbot.shared.types import TenantId, TraceId

_PROVIDER: Final[str] = "vlm_image"

# MIME / ext this parser claims — technical format detection (mirrors how every other
# parser hardcodes its own formats in supports()).
_IMAGE_MIMES: Final[frozenset[str]] = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/tiff", "image/gif",
})
_IMAGE_EXTS: Final[frozenset[str]] = frozenset({
    "png", "jpg", "jpeg", "webp", "tiff", "tif", "gif",
})

# Magic-byte → MIME so the data-URL is correct even when ext/mime is wrong or missing
# (byte-sniff is the platform's ingest robustness principle).
_MAGIC: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# Domain-neutral caption instruction: mirror the source, forbid fabrication (HALLU=0).
_CAPTION_PROMPT: Final[str] = (
    "Mô tả chính xác toàn bộ nội dung hình ảnh dưới đây thành văn bản. Nếu ảnh chứa "
    "bảng / danh sách / số liệu, liệt kê đầy đủ từng dòng đúng như trong ảnh. TUYỆT ĐỐI "
    "KHÔNG bịa thông tin, con số hay giá trị không xuất hiện trong ảnh; nếu ảnh không có "
    "dữ liệu, nói rõ là không có. Chỉ trả về phần mô tả."
)


def _detect_mime(content: bytes, file_ext: str) -> str:
    for magic, mime in _MAGIC:
        if content.startswith(magic):
            return mime
    ext = (file_ext or "").lstrip(".").lower()
    if ext in {"jpg", "jpeg"}:
        return "image/jpeg"
    if ext in _IMAGE_EXTS:
        return f"image/{'tiff' if ext == 'tif' else ext}"
    return "image/png"


class VlmImageParser:
    """Captions an image to text via an injected vision model (LLM Port + spec)."""

    def __init__(
        self,
        *,
        llm: LLMPort,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
        prompt: str = _CAPTION_PROMPT,
    ) -> None:
        if not getattr(spec, "supports_vision", False):
            raise ValueError(
                "VlmImageParser requires a vision-capable model; spec "
                f"{getattr(spec, 'model_name', '?')!r} has supports_vision=False"
            )
        self._llm = llm
        self._spec = spec
        self._tenant = record_tenant_id
        self._trace = trace_id
        self._prompt = prompt

    def supports(self, mime_type: str, file_ext: str) -> bool:
        mt = (mime_type or "").lower()
        ext = (file_ext or "").lstrip(".").lower()
        return mt in _IMAGE_MIMES or ext in _IMAGE_EXTS

    async def parse(self, content: bytes, *, file_name: str) -> list[dict]:
        if not content:
            return []
        ext = file_name.rsplit(".", 1)[-1] if "." in file_name else ""
        mime = _detect_mime(content, ext)
        b64 = base64.b64encode(content).decode()
        msg = LLMMessage(
            role="user",
            content=[
                {"type": "text", "text": self._prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        )
        resp = await self._llm.complete(
            [msg],
            spec=self._spec,
            record_tenant_id=self._tenant,
            trace_id=self._trace,
        )
        caption = (resp.content or "").strip()
        if not caption:
            return []
        return [{
            "content": caption,
            "metadata": {
                "parser": _PROVIDER,
                "file_name": file_name,
                "source_mime": mime,
            },
        }]

    def get_provider_name(self) -> str:
        return _PROVIDER
