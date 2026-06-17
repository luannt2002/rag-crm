"""Tests for corrupt DOCX handling in SimpleTextParser."""

from __future__ import annotations

import pytest


def test_corrupt_docx_raises_value_error():
    """Corrupt DOCX bytes should raise ValueError with descriptive message."""
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    parser = SimpleTextParser()
    corrupt_bytes = b"PK\x03\x04this is not a valid docx file content"

    with pytest.raises(ValueError, match="Invalid or corrupt DOCX"):
        parser._parse_docx(corrupt_bytes)


def test_empty_bytes_raises_value_error():
    """Empty bytes should raise ValueError."""
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    parser = SimpleTextParser()

    with pytest.raises(ValueError, match="Invalid or corrupt DOCX"):
        parser._parse_docx(b"")


def test_random_bytes_raises_value_error():
    """Random non-DOCX bytes should raise ValueError."""
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    parser = SimpleTextParser()
    random_bytes = b"\x00\x01\x02\x03\x04\x05" * 100

    with pytest.raises(ValueError, match="Invalid or corrupt DOCX"):
        parser._parse_docx(random_bytes)


def test_corrupt_docx_error_includes_exception_type():
    """Error message should include the original exception type."""
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    parser = SimpleTextParser()
    corrupt_bytes = b"not a zip at all"

    with pytest.raises(ValueError) as exc_info:
        parser._parse_docx(corrupt_bytes)

    # Should mention the underlying exception type
    msg = str(exc_info.value)
    assert "Invalid or corrupt DOCX file:" in msg
    # The original exc type should appear (e.g., BadZipFile, PackageNotFoundError)
    assert ":" in msg.split("DOCX file: ")[1]


def test_valid_docx_still_works():
    """Verify valid DOCX parsing still works after adding corrupt handling."""
    import io
    from docx import Document as DocxDocument
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    doc = DocxDocument()
    doc.add_paragraph("Hello world")
    buf = io.BytesIO()
    doc.save(buf)

    parser = SimpleTextParser()
    result = parser._parse_docx(buf.getvalue())
    assert "Hello world" in result
