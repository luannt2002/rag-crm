"""Pin tests: magic-byte MIME sniff for ambiguous uploads.

Pre-2026-05-27: upload with declared mime ``application/octet-stream``
got silent 0-chunk fallback → 3 spa price-table docs lost. After fix:
sniff detects xlsx/docx/pdf/html/text from first 8 bytes regardless
of declared mime / filename ext.
"""
from __future__ import annotations

import io
import zipfile

from ragbot.shared.mime_sniff import (
    AMBIGUOUS_DECLARED_MIMES,
    sniff_real_mime,
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _make_office_zip(content_types_signature: str) -> bytes:
    """Build a minimal valid ZIP with [Content_Types].xml containing the
    given format signature substring."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            f'<?xml version="1.0"?><Types xmlns="x">'
            f'<Override ContentType="{content_types_signature}"/>'
            f"</Types>",
        )
    return buf.getvalue()


def test_declared_non_ambiguous_passes_through() -> None:
    """Trustworthy declared mime is kept verbatim."""
    assert sniff_real_mime(b"%PDF-1.4", "f.pdf", "application/pdf") == "application/pdf"
    assert sniff_real_mime(b"any", "x", _XLSX) == _XLSX


def test_empty_bytes_keeps_declared() -> None:
    assert sniff_real_mime(b"", "f", "application/octet-stream") == "application/octet-stream"
    assert sniff_real_mime(None, "f", "application/octet-stream") == "application/octet-stream"


def test_octet_stream_pdf_magic_detected() -> None:
    assert sniff_real_mime(b"%PDF-1.4\n%", "report", "application/octet-stream") == "application/pdf"


def test_octet_stream_html_detected_doctype() -> None:
    assert sniff_real_mime(b"<!DOCTYPE html><body>", "", "application/octet-stream") == "text/html"


def test_octet_stream_html_detected_html_tag() -> None:
    assert sniff_real_mime(b"<html><body>x", "", "application/octet-stream") == "text/html"


def test_octet_stream_zip_xlsx_via_ext() -> None:
    """ZIP magic bytes + .xlsx file ext → xlsx mime."""
    zip_bytes = b"PK\x03\x04" + b"\x00" * 100
    assert sniff_real_mime(zip_bytes, "bang_gia.xlsx", "application/octet-stream") == _XLSX


def test_octet_stream_zip_docx_via_ext() -> None:
    zip_bytes = b"PK\x03\x04" + b"\x00" * 100
    assert sniff_real_mime(zip_bytes, "report.docx", "application/octet-stream") == _DOCX


def test_octet_stream_zip_no_ext_xlsx_via_manifest() -> None:
    """ZIP without ext → peek [Content_Types].xml to disambiguate."""
    xlsx_zip = _make_office_zip(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet+xml"
    )
    assert sniff_real_mime(xlsx_zip, "bang_gia", "application/octet-stream") == _XLSX


def test_octet_stream_zip_no_ext_docx_via_manifest() -> None:
    docx_zip = _make_office_zip(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    )
    assert sniff_real_mime(docx_zip, "report", "application/octet-stream") == _DOCX


def test_octet_stream_text_routes_to_markdown() -> None:
    assert (
        sniff_real_mime(b"Some plain text with VN: Chao anh.", "note", "application/octet-stream")
        == "text/markdown"
    )


def test_octet_stream_csv_detected_by_commas() -> None:
    """Comma-rich first line → csv route (better parser than markdown)."""
    csv_bytes = b"STT,Ten,Gia,SoLuong\n1,Mep,129000,899000\n"
    assert sniff_real_mime(csv_bytes, "bang_gia", "application/octet-stream") == "text/csv"


def test_octet_stream_binary_garbage_keeps_declared() -> None:
    """Random binary that's not any known format → keep declared."""
    binary = bytes(range(256)) * 4  # not utf-8, not magic
    assert (
        sniff_real_mime(binary, "blob", "application/octet-stream")
        == "application/octet-stream"
    )


def test_empty_declared_mime_treated_as_ambiguous() -> None:
    """Caller sometimes omits Content-Type entirely; empty string is
    ambiguous too."""
    assert sniff_real_mime(b"%PDF-1.4", "x", "") == "application/pdf"


def test_ambiguous_set_contains_expected_values() -> None:
    """Pin the frozen ambiguous-mime set."""
    assert "" in AMBIGUOUS_DECLARED_MIMES
    assert "application/octet-stream" in AMBIGUOUS_DECLARED_MIMES
    assert "binary/octet-stream" in AMBIGUOUS_DECLARED_MIMES
    # text/plain should NOT be sniffed-over (it's trustworthy)
    assert "text/plain" not in AMBIGUOUS_DECLARED_MIMES


def test_idempotent_after_sniff() -> None:
    """Apply sniff twice = same result."""
    first = sniff_real_mime(b"%PDF-1.4", "x", "application/octet-stream")
    second = sniff_real_mime(b"%PDF-1.4", "x", first)
    assert first == second == "application/pdf"
