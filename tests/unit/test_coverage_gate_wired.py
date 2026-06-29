"""P0-2 wiring test — the full char-coverage gate is wired into U4 ingest.

Two layers of assertion:

1. CONTRACT (source-level): ``_stage_u4_chunk`` imports + calls
   ``check_chunk_gaps`` and emits a ``chunk_char_coverage_gap`` event. This
   guards against the gate being silently un-wired by a future refactor.
2. BEHAVIOUR (drives the real stage): when the chunks handed to the stage drop
   a MIDDLE span that is present in the source (the silent-failure class), the
   stage emits ``chunk_char_coverage_gap`` with ``coverage_ratio < 1.0`` and at
   least one uncovered span. A full-coverage control proves the gate is
   OBSERVE-only (no event, no false positive) — it never injects/overrides an
   answer and never raises.

The stage is exercised via the same lightweight ``_StageChunkMixin`` harness as
``test_document_service_block_pipeline_wired.py``. ``_cfg=None`` selects the
constant-default config path; a row-shaped ``parser_row_chunks`` (parser tag
``excel_openpyxl``) takes the ``parser_preserve`` branch so the stage's emitted
chunks are EXACTLY the parser rows we supply — letting us deterministically
force a dropped span without depending on ``smart_chunk`` internals.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import structlog

import ragbot.application.services.document_service.ingest_stages as ingest_stages_mod
from ragbot.application.services.document_service.ingest_stages import (
    _IngestCtx,
    _StageChunkMixin,
)
from ragbot.shared.chunking.coverage import CoverageResult, check_chunk_gaps


class _Host(_StageChunkMixin):
    """Minimal host: constant-default config path (``_cfg=None``)."""

    def __init__(self) -> None:
        self._cfg = None
        self._settings = SimpleNamespace(
            rag=SimpleNamespace(default_chunk_size=512, default_chunk_overlap=64),
        )
        self._sf = None

    async def _resolve_chunking_policy(self, *_a: object, **_kw: object) -> dict:
        return {}


def _capture_logs() -> structlog.testing.LogCapture:
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    return cap


def _row(content: str) -> dict:
    # Row-shaped parser chunk; the ``excel_openpyxl`` tag routes the stage to
    # its parser_preserve path so emitted chunks == these rows verbatim.
    return {"content": content, "metadata": {"parser": "excel_openpyxl"}}


def _build_ctx(source: str, parser_rows: list[dict]) -> _IngestCtx:
    return _IngestCtx(
        record_bot_id=uuid.uuid4(),
        title="doc",
        content=source,
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
        blocks=None,
        step_tracker=None,
        parser_row_chunks=parser_rows,
    )


# ── Layer 1: contract / source-level wiring ────────────────────────────────


def test_check_chunk_gaps_importable_and_pure() -> None:
    # The exact symbol the stage wires must exist and be a no-raise pure fn.
    res = check_chunk_gaps(["alpha beta", "delta"], "alpha beta gamma delta")
    assert isinstance(res, CoverageResult)
    # gamma was dropped → not lossless, ratio < 1.0, one uncovered span.
    assert res.ok is False
    assert res.coverage_ratio < 1.0
    assert len(res.uncovered_spans) >= 1


def test_stage_source_wires_char_coverage_gate() -> None:
    src = inspect.getsource(_StageChunkMixin._stage_u4_chunk)
    assert "check_chunk_gaps" in src, (
        "_stage_u4_chunk must call the char-coverage gate (check_chunk_gaps)"
    )
    assert "chunk_char_coverage_gap" in src, (
        "_stage_u4_chunk must emit the chunk_char_coverage_gap observe event"
    )
    # check_chunk_gaps must be reachable from the module's import namespace.
    assert hasattr(ingest_stages_mod, "check_chunk_gaps") or (
        "from ragbot.shared.chunking.coverage import check_chunk_gaps" in src
    ), "check_chunk_gaps must be imported by ingest_stages"


# ── Layer 2: behaviour — drives the real stage ─────────────────────────────


@pytest.mark.asyncio
async def test_dropped_middle_span_emits_char_coverage_gap() -> None:
    # Source has THREE distinct prose rows; the parser drops the MIDDLE row.
    source = (
        "alpha beta gamma row one\n"
        "delta epsilon zeta row two\n"
        "eta theta iota row three"
    )
    parser_rows = [
        _row("alpha beta gamma row one"),
        # middle row "delta epsilon zeta row two" intentionally dropped
        _row("eta theta iota row three"),
    ]
    ctx = _build_ctx(source, parser_rows)
    host = _Host()
    cap = _capture_logs()

    with patch(
        "ragbot.application.services.document_service.ingest_stages."
        "_update_doc_progress",
        new=AsyncMock(return_value=None),
    ):
        await host._stage_u4_chunk(ctx)

    # The stage must have taken the parser_preserve path (rows verbatim).
    assert ctx.chunks == [
        "alpha beta gamma row one",
        "eta theta iota row three",
    ]
    gap_events = [e for e in cap.entries if e["event"] == "chunk_char_coverage_gap"]
    assert len(gap_events) == 1, (
        "a dropped middle span must emit exactly one chunk_char_coverage_gap "
        f"event (got {[e['event'] for e in cap.entries]})"
    )
    ev = gap_events[0]
    assert ev["coverage_ratio"] < 1.0
    assert ev["uncovered_spans"] >= 1
    assert ev["log_level"] == "warning"


@pytest.mark.asyncio
async def test_full_coverage_emits_no_char_gap_event() -> None:
    # Parser rows cover the WHOLE source → observe-only, no event fired.
    source = "alpha beta gamma\ndelta epsilon zeta"
    parser_rows = [
        _row("alpha beta gamma"),
        _row("delta epsilon zeta"),
    ]
    ctx = _build_ctx(source, parser_rows)
    host = _Host()
    cap = _capture_logs()

    with patch(
        "ragbot.application.services.document_service.ingest_stages."
        "_update_doc_progress",
        new=AsyncMock(return_value=None),
    ):
        await host._stage_u4_chunk(ctx)

    assert ctx.chunks == ["alpha beta gamma", "delta epsilon zeta"]
    assert all(
        e["event"] != "chunk_char_coverage_gap" for e in cap.entries
    ), "full coverage must NOT emit a gap event (observe-only, no false positive)"
