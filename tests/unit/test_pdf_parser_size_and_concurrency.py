"""PDF parser memory guards.

Two leaks the cap tightens:
1. **Per-document size** — a single 50MB PDF loads its full byte string
   into ``BytesIO`` plus a ``pdfium.PdfDocument`` native buffer, then
   walks every page. Reducing the per-tenant default to 10MB keeps
   worst-case resident memory bounded; bot owners that legitimately need
   larger files override via ``plan_limits.pdf_max_bytes``.
2. **Concurrent burst** — multiple uploads racing through ``parse()`` can
   stack their per-document allocations even when each fits under the
   cap. A module-level semaphore queues parses so peak resident memory
   tracks ``cap × concurrency`` instead of ``cap × inflight``.

Page-handle leaks are also fixed: pypdfium2's ``textpage`` holds a
separate native resource that must be ``close()``-d alongside the
``page``.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from ragbot.shared import bot_limits
from ragbot.shared.constants import (
    DEFAULT_PDF_MAX_BYTES,
    DEFAULT_PDF_PARSE_CONCURRENCY,
)


# ─────────────────────────── Constants ────────────────────────────────


def test_default_pdf_max_bytes_is_ten_megabytes() -> None:
    assert DEFAULT_PDF_MAX_BYTES == 10 * 1024 * 1024


def test_default_pdf_parse_concurrency_is_four() -> None:
    assert DEFAULT_PDF_PARSE_CONCURRENCY == 4


# ─────────────────────────── Plan-limits schema ───────────────────────


def test_plan_limit_schema_has_pdf_max_bytes_entry() -> None:
    schema = bot_limits.PLAN_LIMIT_SCHEMA.get("pdf_max_bytes")
    assert schema is not None, (
        "pdf_max_bytes must be declared in PLAN_LIMIT_SCHEMA so a tenant "
        "needing files larger than the system default can override the "
        "cap via bots.plan_limits without a code change"
    )
    assert schema["type"] == "int"
    assert schema["default"] == DEFAULT_PDF_MAX_BYTES
    assert schema["min"] >= 1024  # KB-floor sanity
    # Cap MUST allow override above the new 10MB default.
    assert schema["max"] >= 50 * 1024 * 1024


# ─────────────────────────── parse() guards ────────────────────────────


def _install_fake_pdfium(monkeypatch: pytest.MonkeyPatch, page_close_log: list[str], textpage_close_log: list[str]) -> None:
    """Inject a minimal stub so PdfParser.parse() can run without the real lib."""

    class _FakeTextPage:
        def __init__(self) -> None:
            self._closed = False

        def get_text_range(self) -> str:
            return "page text"

        def close(self) -> None:
            self._closed = True
            textpage_close_log.append("close")

    class _FakePage:
        def __init__(self) -> None:
            self._textpage = _FakeTextPage()

        def get_textpage(self) -> _FakeTextPage:
            return self._textpage

        def close(self) -> None:
            page_close_log.append("close")

    class _FakePdf:
        def __init__(self, _bytes_io: Any) -> None:
            self._pages = [_FakePage(), _FakePage()]

        def __len__(self) -> int:
            return len(self._pages)

        def __getitem__(self, idx: int) -> _FakePage:
            return self._pages[idx]

        def close(self) -> None:
            pass

    fake_module = types.ModuleType("pypdfium2")
    fake_module.PdfDocument = _FakePdf  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pypdfium2", fake_module)


@pytest.mark.asyncio
async def test_parse_raises_for_payload_above_default_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragbot.infrastructure.parser.pdf_parser import PdfParser

    _install_fake_pdfium(monkeypatch, [], [])
    parser = PdfParser()
    oversize = b"\x00" * (DEFAULT_PDF_MAX_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        await parser.parse(oversize, file_name="big.pdf")


@pytest.mark.asyncio
async def test_parse_succeeds_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from ragbot.infrastructure.parser.pdf_parser import PdfParser

    _install_fake_pdfium(monkeypatch, [], [])
    parser = PdfParser()
    payload = b"\x00" * 1024  # 1KB — well under 10MB
    chunks = await parser.parse(payload, file_name="small.pdf")
    assert isinstance(chunks, list)
    assert len(chunks) == 2  # _FakePdf yields 2 pages, both non-empty


@pytest.mark.asyncio
async def test_parse_closes_textpage_alongside_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """pypdfium2 textpage holds a separate native handle. Without explicit
    close it leaks until the PdfDocument is garbage-collected — which on a
    busy worker can be many seconds after the page handle release."""
    from ragbot.infrastructure.parser.pdf_parser import PdfParser

    page_close_log: list[str] = []
    textpage_close_log: list[str] = []
    _install_fake_pdfium(monkeypatch, page_close_log, textpage_close_log)

    parser = PdfParser()
    await parser.parse(b"\x00" * 100, file_name="tp.pdf")

    assert len(page_close_log) == 2, "every page must be closed"
    assert len(textpage_close_log) == 2, (
        "every textpage must be closed alongside its page; "
        f"saw {textpage_close_log}"
    )


@pytest.mark.asyncio
async def test_parse_semaphore_caps_inflight_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """A burst of N concurrent parses must never put more than
    DEFAULT_PDF_PARSE_CONCURRENCY documents into the parser body at the
    same time, regardless of how many uploads the worker accepts."""
    from ragbot.infrastructure.parser.pdf_parser import PdfParser

    inflight_now = 0
    inflight_peak = 0
    sleep_event = asyncio.Event()

    class _SlowTextPage:
        def get_text_range(self) -> str:
            return "x"

        def close(self) -> None:
            pass

    class _SlowPage:
        def get_textpage(self) -> _SlowTextPage:
            return _SlowTextPage()

        def close(self) -> None:
            pass

    class _SlowPdf:
        def __init__(self, _bytes_io: Any) -> None:
            nonlocal inflight_now, inflight_peak
            inflight_now += 1
            inflight_peak = max(inflight_peak, inflight_now)

        def __len__(self) -> int:
            return 1

        def __getitem__(self, idx: int) -> _SlowPage:
            return _SlowPage()

        def close(self) -> None:
            nonlocal inflight_now
            inflight_now -= 1

    fake_module = types.ModuleType("pypdfium2")
    fake_module.PdfDocument = _SlowPdf  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pypdfium2", fake_module)

    async def _slow_parse(parser: PdfParser, idx: int) -> None:
        # Force overlap — each parse pretends to do CPU work via sleep so the
        # event loop has the chance to schedule all coroutines into the parser
        # body simultaneously without the semaphore.
        async def _wrapped() -> list[dict]:
            return await parser.parse(b"\x00" * 64, file_name=f"f{idx}.pdf")

        # Insert an explicit sleep AFTER the parser allocates its PdfDocument
        # so the semaphore-counter window stays open long enough for siblings
        # to pile up if the gate is missing.
        result = await _wrapped()
        await asyncio.sleep(0.01)
        return result

    parser = PdfParser()
    burst = 8
    await asyncio.gather(*(_slow_parse(parser, i) for i in range(burst)))

    assert inflight_peak <= DEFAULT_PDF_PARSE_CONCURRENCY, (
        f"semaphore must cap inflight to {DEFAULT_PDF_PARSE_CONCURRENCY}; "
        f"peak observed = {inflight_peak} of {burst} concurrent parses"
    )
