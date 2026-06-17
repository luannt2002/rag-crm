"""Unit tests for KreuzbergParser.

Coverage:
1. ``ImportError`` fallback when kreuzberg package not installed.
2. Element-type → BlockType mapping (HEADING/TABLE/FORMULA/IMAGE/CODE/LIST/TEXT).
3. ``Block.is_atomic`` populated for the 5 atomic types.
4. Heading context prepended onto subsequent non-heading blocks
   (``prepend_heading_context=True`` semantics).
5. ``supported_mimes`` exposes PDF / DOCX / HTML / Markdown / image MIMEs.
6. ``ParsedDocument.language`` sources ``DEFAULT_LANGUAGE`` (no inline ``"vi"``).
7. ``ocr_factory.build_ocr_parser`` honours ``RAGBOT_PARSER_ENGINE=kreuzberg``.
8. Domain-neutral lock: no brand / industry / customer literal.

All external library calls (kreuzberg.extract_bytes) are mocked so the test
runs without the optional dependency installed.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import types
from typing import Any

import pytest


# ── Fake kreuzberg module + helpers ─────────────────────────────────────────


class _FakeElement:
    """Lightweight stand-in for a kreuzberg parsed element."""

    def __init__(
        self,
        *,
        text: str,
        element_type: str,
        page_no: int | None = None,
    ) -> None:
        self.text = text
        self.element_type = element_type
        self.page_no = page_no


class _FakeResult:
    def __init__(self, elements: list[_FakeElement], page_count: int) -> None:
        self.elements = elements
        self.page_count = page_count


def _install_fake_kreuzberg(monkeypatch: pytest.MonkeyPatch, result: _FakeResult) -> dict[str, Any]:
    """Install a fake ``kreuzberg`` module exposing ``extract_bytes``.

    Returns a capture dict so the test can assert call arguments.
    """
    captured: dict[str, Any] = {}

    def fake_extract_bytes(data: bytes, **kwargs: Any) -> _FakeResult:
        captured["data_len"] = len(data)
        captured["kwargs"] = dict(kwargs)
        return result

    fake_module = types.ModuleType("kreuzberg")
    fake_module.extract_bytes = fake_extract_bytes  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kreuzberg", fake_module)
    return captured


def _uninstall_kreuzberg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ImportError on ``import kreuzberg`` from inside the adapter."""
    monkeypatch.setitem(sys.modules, "kreuzberg", None)


# ── 1. ImportError fallback ────────────────────────────────────────────────


def test_kreuzberg_parser_raises_import_error_without_dep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing dep → constructor raises ImportError (factory then falls back)."""
    _uninstall_kreuzberg(monkeypatch)

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser

    with pytest.raises(ImportError) as exc_info:
        KreuzbergParser()

    msg = str(exc_info.value).lower()
    assert "kreuzberg" in msg
    # Operator should learn how to opt out — message must point at the flag.
    assert "kreuzberg_parser_enabled" in msg


# ── 2 + 3. Element-type → BlockType + is_atomic ─────────────────────────────


def test_kreuzberg_parser_maps_element_types_to_block_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each Kreuzberg element_type lands on the right domain BlockType."""
    elements = [
        _FakeElement(text="Chapter 1", element_type="heading", page_no=1),
        _FakeElement(text="Intro paragraph.", element_type="paragraph", page_no=1),
        _FakeElement(text="| A | B |\n|---|---|\n| 1 | 2 |", element_type="table", page_no=2),
        _FakeElement(text="E = mc^2", element_type="formula", page_no=2),
        _FakeElement(text="diagram-caption", element_type="figure", page_no=3),
        _FakeElement(text="print('hi')", element_type="code", page_no=3),
        _FakeElement(text="- item 1\n- item 2", element_type="list", page_no=3),
    ]
    _install_fake_kreuzberg(monkeypatch, _FakeResult(elements, page_count=3))

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser

    parser = KreuzbergParser()
    parsed = asyncio.run(parser.parse(b"%PDF-1.4 fake bytes", mime_type_hint="application/pdf"))
    asyncio.run(parser.close())

    types_by_index = [b.type for b in parsed.blocks]
    assert types_by_index == [
        "HEADING", "TEXT", "TABLE", "FORMULA", "IMAGE", "CODE", "LIST",
    ], f"got {types_by_index}"

    # is_atomic populated correctly: 5 atomic types (HEADING/TABLE/FORMULA/IMAGE/CODE).
    atomic_flags = [b.is_atomic for b in parsed.blocks]
    assert atomic_flags == [True, False, True, True, True, True, False], (
        f"atomic flags wrong: {atomic_flags}"
    )

    # Page-count derived from element page_no set.
    assert parsed.page_count == 3


# ── 4. Heading context prepend ──────────────────────────────────────────────


def test_kreuzberg_parser_prepends_heading_context_onto_following_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-heading blocks inherit context_before = last heading text."""
    elements = [
        _FakeElement(text="Section A", element_type="heading", page_no=1),
        _FakeElement(text="Body under A.", element_type="paragraph", page_no=1),
        _FakeElement(text="Section B", element_type="section_header", page_no=2),
        _FakeElement(text="Body under B.", element_type="paragraph", page_no=2),
    ]
    _install_fake_kreuzberg(monkeypatch, _FakeResult(elements, page_count=2))

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser

    parser = KreuzbergParser()
    parsed = asyncio.run(parser.parse(b"data", mime_type_hint="application/pdf"))
    asyncio.run(parser.close())

    # Block 0 = heading itself (no upstream heading yet).
    assert parsed.blocks[0].type == "HEADING"
    assert parsed.blocks[0].context_before == ""

    # Block 1 inherits "Section A".
    assert parsed.blocks[1].type == "TEXT"
    assert parsed.blocks[1].context_before == "Section A"

    # Block 2 is new heading — its own context_before resets.
    assert parsed.blocks[2].type == "HEADING"
    assert parsed.blocks[2].context_before == ""

    # Block 3 inherits the *new* heading.
    assert parsed.blocks[3].type == "TEXT"
    assert parsed.blocks[3].context_before == "Section B"


# ── 5. supported_mimes ──────────────────────────────────────────────────────


def test_kreuzberg_parser_supported_mimes_covers_expected_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kreuzberg(monkeypatch, _FakeResult([], page_count=0))

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser

    parser = KreuzbergParser()
    mimes = parser.supported_mimes()
    asyncio.run(parser.close())

    assert "application/pdf" in mimes
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in mimes
    )
    assert "text/html" in mimes
    assert "text/markdown" in mimes
    # Scanned-image MIMEs (Tesseract path).
    assert "image/png" in mimes
    assert "image/jpeg" in mimes


# ── 6. Default language sourced from constant (no inline "vi") ──────────────


def test_kreuzberg_parser_sources_default_language_from_constant() -> None:
    """Regression lock: parser uses ``DEFAULT_LANGUAGE`` not the literal "vi"."""
    from ragbot.infrastructure.ocr import kreuzberg_parser as kbp_module
    from ragbot.shared.constants import DEFAULT_LANGUAGE

    src = inspect.getsource(kbp_module)
    assert "DEFAULT_LANGUAGE" in src, (
        "F14-CRIT-2 regression — kreuzberg_parser must import DEFAULT_LANGUAGE"
    )
    assert 'language="vi"' not in src, (
        'F14-CRIT-2 regression — hardcode language="vi" reintroduced'
    )
    # Sanity: constant resolves to a real string token.
    assert isinstance(DEFAULT_LANGUAGE, str) and len(DEFAULT_LANGUAGE) >= 2


def test_kreuzberg_parser_passes_ocr_language_to_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured ``ocr_language`` reaches kreuzberg.extract_bytes."""
    captured = _install_fake_kreuzberg(monkeypatch, _FakeResult([], page_count=0))

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser
    from ragbot.shared.constants import DEFAULT_KREUZBERG_OCR_LANGUAGE

    parser = KreuzbergParser(ocr_language="vie")
    asyncio.run(parser.parse(b"data", mime_type_hint="application/pdf"))
    asyncio.run(parser.close())

    assert captured["kwargs"].get("ocr_language") == "vie"
    # ``prepend_heading_context`` must default to True per spec.
    assert captured["kwargs"].get("prepend_heading_context") is True

    # Default ocr_language (omitted at init) flows from the constant.
    captured2 = _install_fake_kreuzberg(monkeypatch, _FakeResult([], page_count=0))
    parser2 = KreuzbergParser()
    asyncio.run(parser2.parse(b"data", mime_type_hint="application/pdf"))
    asyncio.run(parser2.close())
    assert captured2["kwargs"].get("ocr_language") == DEFAULT_KREUZBERG_OCR_LANGUAGE


# ── 7. ocr_factory honours engine = "kreuzberg" ─────────────────────────────


def test_ocr_factory_selects_kreuzberg_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine env var routes the factory to KreuzbergParser."""
    _install_fake_kreuzberg(monkeypatch, _FakeResult([], page_count=0))
    monkeypatch.setenv("RAGBOT_PARSER_ENGINE", "kreuzberg")

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser
    from ragbot.infrastructure.ocr.ocr_factory import build_ocr_parser

    parser = build_ocr_parser()
    try:
        assert isinstance(parser, KreuzbergParser)
    finally:
        asyncio.run(parser.close())


def test_ocr_factory_falls_back_to_simple_when_kreuzberg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing dep + engine=kreuzberg → falls back to SimpleTextParser."""
    _uninstall_kreuzberg(monkeypatch)
    monkeypatch.setenv("RAGBOT_PARSER_ENGINE", "kreuzberg")

    from ragbot.infrastructure.ocr.ocr_factory import build_ocr_parser
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    parser = build_ocr_parser()
    try:
        assert isinstance(parser, SimpleTextParser)
    finally:
        asyncio.run(parser.close())


# ── 8. Telemetry + structlog event name + step_name ────────────────────────


def test_kreuzberg_parser_emits_structured_event_with_step_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parser logs ``kreuzberg_parse_done`` with step_name + feature_flag."""
    elements = [
        _FakeElement(text="Heading", element_type="heading", page_no=1),
        _FakeElement(text="Body", element_type="paragraph", page_no=1),
    ]
    _install_fake_kreuzberg(monkeypatch, _FakeResult(elements, page_count=1))

    captured_events: list[tuple[str, dict[str, Any]]] = []

    from ragbot.infrastructure.ocr import kreuzberg_parser as kbp_module

    def fake_info(event: str, **kwargs: Any) -> None:
        captured_events.append((event, kwargs))

    monkeypatch.setattr(kbp_module.logger, "info", fake_info)

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser

    parser = KreuzbergParser()
    asyncio.run(parser.parse(b"data", mime_type_hint="application/pdf"))
    asyncio.run(parser.close())

    # One kreuzberg_parse_done event should land with the spec'd fields.
    matched = [(ev, kw) for ev, kw in captured_events if ev == "kreuzberg_parse_done"]
    assert len(matched) == 1, f"expected 1 kreuzberg_parse_done event, got {captured_events}"
    _, kw = matched[0]
    assert kw["step_name"] == "kreuzberg_parse"
    assert kw["feature_flag"] == "kreuzberg_parser_enabled"
    assert kw["block_count"] == 2
    assert kw["atomic_count"] == 1  # HEADING is atomic, paragraph is not.
    assert "duration_ms" in kw and isinstance(kw["duration_ms"], int)


# ── 9. Max-bytes ceiling enforced ───────────────────────────────────────────


def test_kreuzberg_parser_rejects_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload above ``max_bytes`` raises ``ValueError`` before parsing."""
    _install_fake_kreuzberg(monkeypatch, _FakeResult([], page_count=0))

    from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser

    parser = KreuzbergParser(max_bytes=128)
    with pytest.raises(ValueError) as exc_info:
        asyncio.run(parser.parse(b"x" * 256, mime_type_hint="application/pdf"))
    asyncio.run(parser.close())
    assert "too large" in str(exc_info.value).lower()


# ── 10. Domain-neutral source lock ──────────────────────────────────────────


def test_kreuzberg_parser_source_is_domain_neutral() -> None:
    """No tenant / brand / industry / customer literal in adapter source."""
    from ragbot.infrastructure.ocr import kreuzberg_parser as kbp_module

    src = inspect.getsource(kbp_module).lower()
    # No hardcoded tenant identifiers or known-brand placeholders.
    for forbidden in (".vn:", "innocom", "telco", "fintech", "ecommerce"):
        assert forbidden not in src, (
            f"domain-neutral violation: {forbidden!r} present in adapter source"
        )
