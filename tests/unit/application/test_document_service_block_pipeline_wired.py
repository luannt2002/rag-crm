"""Wire test for the AdapChunk Block pipeline in ``_stage_u4_chunk``."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.application.services.document_service.ingest_stages import (
    _IngestCtx,
    _StageChunkMixin,
)
from ragbot.domain.entities.document import Block


class _Host(_StageChunkMixin):
    def __init__(self, cfg: object) -> None:
        self._cfg = cfg
        self._settings = SimpleNamespace(
            rag=SimpleNamespace(default_chunk_size=512, default_chunk_overlap=64),
        )
        self._sf = None

    async def _resolve_chunking_policy(self, *_a: object, **_kw: object) -> dict:
        return {}


class _FakeCfg:
    async def get_bool(self, key: str, default: bool) -> bool:
        if key == "adapchunk_block_pipeline_enabled":
            return True
        return default

    async def get(self, key: str, default: object = None) -> object:
        return default

    async def get_int(self, key: str, default: int) -> int:
        return default


def _build_ctx(blocks: list[Block] | None) -> _IngestCtx:
    paragraph = (
        "Theo định lý Bayes, ta có công thức xác suất hậu nghiệm dùng để "
        "ước lượng tham số từ dữ liệu quan sát và phân phối tiên nghiệm.\n\n"
        "Bảng dưới đây liệt kê các tham số đầu vào của mô hình cùng với "
        "miền giá trị hợp lệ và đơn vị đo tương ứng cho từng đại lượng.\n\n"
        "Phần kết luận tóm tắt kết quả thực nghiệm trên tập dữ liệu kiểm "
        "thử, so sánh độ chính xác giữa các cấu hình siêu tham số.\n\n"
    )
    content = paragraph * 6
    return _IngestCtx(
        record_bot_id=uuid.uuid4(),
        title="doc",
        content=content,
        source_url="",
        source_type="manual",
        language="vi",
        mime_type="text/plain",
        existing_doc_id=None,
        record_tenant_id=uuid.uuid4(),
        workspace_id="ws",
        channel_type="web",
        raw_bytes=None,
        file_name=None,
        blocks=blocks,
        step_tracker=None,
    )


@pytest.mark.asyncio
async def test_block_pipeline_feeds_ctx_blocks_to_context_buffer() -> None:
    blocks = [
        Block(type="TEXT", content="Theo định lý Bayes, ta có:", is_atomic=False),
        Block(type="TABLE", content="| a | b |\n|---|---|\n| 1 | 2 |", is_atomic=True),
        Block(type="TEXT", content="Trong đó a, b là tham số.", is_atomic=False),
    ]
    ctx = _build_ctx(blocks)
    host = _Host(_FakeCfg())

    profile = {
        "total_headings": 0,
        "total_words": 40,
        "heading_counts": {"h1": 0, "h2": 0, "h3": 0},
        "table_count": 1,
        "avg_text_length": 30.0,
        "mixed_content_score": 0.33,
        "has_toc": False,
    }

    spy_analyze = lambda b: profile  # noqa: E731 — tiny test spy

    with patch(
        "ragbot.shared.context_buffer.attach_context_buffer",
        side_effect=lambda b, **_kw: b,
    ) as spy_buffer, patch(
        "ragbot.shared.chunking.analyze_document_blocks",
        side_effect=spy_analyze,
    ) as mock_analyze, patch(
        "ragbot.application.services.document_service.ingest_stages."
        "_update_doc_progress",
        new=AsyncMock(return_value=None),
    ):
        await host._stage_u4_chunk(ctx)

    spy_buffer.assert_called_once()
    passed_blocks = spy_buffer.call_args.args[0]
    assert passed_blocks == blocks, (
        "attach_context_buffer received the ctx.blocks list "
        f"(got {passed_blocks!r})"
    )
    mock_analyze.assert_called_once()
    assert mock_analyze.call_args.args[0] == blocks
    assert ctx.chunks, "stage must still emit chunks via the chunker"


@pytest.mark.asyncio
async def test_block_pipeline_empty_blocks_falls_back_without_crash() -> None:
    for empty in (None, []):
        ctx = _build_ctx(empty)
        host = _Host(_FakeCfg())

        with patch(
            "ragbot.shared.context_buffer.attach_context_buffer",
            side_effect=lambda b, **_kw: b,
        ) as spy_buffer, patch(
            "ragbot.application.services.document_service.ingest_stages."
            "_update_doc_progress",
            new=AsyncMock(return_value=None),
        ):
            await host._stage_u4_chunk(ctx)

        spy_buffer.assert_not_called()
        assert ctx.chunks, (
            f"empty ctx.blocks={empty!r}: legacy chunker must still emit chunks"
        )
