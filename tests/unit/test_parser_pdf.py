"""PdfParser strategy — unit tests.

Builds an in-memory 2-page PDF via pypdfium2 and asserts per-page chunking
+ ``## Page N`` markers + Protocol conformance.
"""

from __future__ import annotations

import importlib.util

import pytest

from ragbot.application.ports.document_parser_port import DocumentParserPort


_PYPDFIUM2 = importlib.util.find_spec("pypdfium2") is not None
_PDF_MIME = "application/pdf"


@pytest.mark.skipif(not _PYPDFIUM2, reason="pypdfium2 not installed")
def test_pdf_parser_supports_pdf_mime_and_ext() -> None:
    from ragbot.infrastructure.parser.pdf_parser import PdfParser

    p = PdfParser()
    assert p.supports(_PDF_MIME, ".pdf") is True
    assert p.supports("", ".pdf") is True
    assert p.supports(_PDF_MIME, "") is True
    assert p.supports("text/plain", ".txt") is False
    assert p.get_provider_name() == "pdf"
    assert isinstance(p, DocumentParserPort)


@pytest.mark.skipif(not _PYPDFIUM2, reason="pypdfium2 not installed")
@pytest.mark.asyncio
async def test_pdf_parser_emits_one_chunk_per_nonempty_page() -> None:
    """Build a real PDF with two text pages via reportlab-free approach.

    pypdfium2 is read-only for text content, so we craft a minimal valid
    PDF byte-string by hand. Two pages each carry one short text op.
    """
    from ragbot.infrastructure.parser.pdf_parser import PdfParser

    # Minimal valid PDF with 2 pages each showing a single text run. Built
    # by hand to avoid pulling reportlab/pypdf into deps.
    pdf_bytes = _make_minimal_two_page_pdf("Hello A", "Hello B")

    parser = PdfParser()
    chunks = await parser.parse(pdf_bytes, file_name="sample.pdf")

    # Each non-empty page → 1 chunk; markers prepended.
    assert len(chunks) == 2
    assert chunks[0]["content"].startswith("## Page 1")
    assert chunks[1]["content"].startswith("## Page 2")
    assert "Hello A" in chunks[0]["content"]
    assert "Hello B" in chunks[1]["content"]
    assert chunks[0]["metadata"]["page_number"] == 1
    assert chunks[1]["metadata"]["page_number"] == 2
    assert chunks[0]["metadata"]["parser"] == "pdf"
    assert chunks[0]["metadata"]["file_name"] == "sample.pdf"


@pytest.mark.skipif(not _PYPDFIUM2, reason="pypdfium2 not installed")
@pytest.mark.asyncio
async def test_pdf_parser_rejects_oversized_input() -> None:
    from ragbot.infrastructure.parser.pdf_parser import PdfParser
    from ragbot.shared.constants import DEFAULT_PDF_MAX_BYTES

    parser = PdfParser()
    too_big = b"%PDF-1.4\n" + b"\x00" * (DEFAULT_PDF_MAX_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        await parser.parse(too_big, file_name="huge.pdf")


def _make_minimal_two_page_pdf(text_a: str, text_b: str) -> bytes:
    """Construct a minimal PDF byte-string with two text-bearing pages.

    Hand-crafted because pypdfium2 is primarily a reader; we stay
    dep-free (no reportlab) by emitting the structural skeleton.
    """
    def _content_stream(s: str) -> bytes:
        # Tj operator with Helvetica at (50, 100) — 12pt.
        body = (
            f"BT /F1 12 Tf 50 100 Td ({s}) Tj ET"
        ).encode("latin-1")
        stream = b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"
        return stream

    # Object table — 7 objects: catalog, pages, page1, page2, content1, content2, font.
    objs: list[bytes] = []

    def _add(obj_body: bytes) -> int:
        objs.append(obj_body)
        return len(objs)

    # 1: Catalog
    catalog_id = _add(b"<< /Type /Catalog /Pages 2 0 R >>")
    # 2: Pages root
    pages_id = _add(b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>")
    # 3: Page 1
    page1_id = _add(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 5 0 R >>",
    )
    # 4: Page 2
    page2_id = _add(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
    )
    # 5: Content stream for page 1
    c1_id = _add(_content_stream(text_a))
    # 6: Content stream for page 2
    c2_id = _add(_content_stream(text_b))
    # 7: Font
    font_id = _add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # We intentionally use sequential numbers (1..7); IDs already match.
    assert (catalog_id, pages_id, page1_id, page2_id, c1_id, c2_id, font_id) == (1, 2, 3, 4, 5, 6, 7)

    # Assemble file with xref table.
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for idx, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_start = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_start).encode() + b"\n%%EOF\n"
    return bytes(out)
