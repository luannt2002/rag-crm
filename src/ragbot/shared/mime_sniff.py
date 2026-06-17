"""Magic-byte MIME sniffer for ambiguous file uploads.

When an HTTP client uploads a file without setting a precise Content-Type
(common with drag-drop and curl --data-binary), the declared MIME often
arrives as ``application/octet-stream`` or empty. The strict mime/ext
equality check in :mod:`ragbot.infrastructure.parser.registry` then
returns no parser → ingest silently produces 0 chunks → bot loses data.

This helper detects the real MIME from the first bytes of ``raw_bytes``
and is wired into ``DocumentService.ingest`` BEFORE the parser registry.

Domain-neutral: magic bytes are universal binary format specs, not tied
to any tenant / industry / brand.
"""

from __future__ import annotations

import io
import logging
import zipfile

logger = logging.getLogger(__name__)

# Mime constants — mirror values already used in parser modules. Kept
# as module-level so tests can import them without instantiating parsers.
_MIME_PDF = "application/pdf"
_MIME_XLSX = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_MIME_HTML = "text/html"
_MIME_MARKDOWN = "text/markdown"
_MIME_CSV = "text/csv"
_MIME_PLAIN = "text/plain"

# Ambiguous declared types that trigger sniff. Bot owners should NOT
# override this — adding more values risks overwriting trustworthy
# declared mimes. Frozen constant.
AMBIGUOUS_DECLARED_MIMES: frozenset[str] = frozenset({
    "",
    "application/octet-stream",
    "binary/octet-stream",
})


def _looks_like_utf8_text(sample: bytes, *, sample_size: int = 1024) -> bool:
    """Return True when ``sample`` decodes as UTF-8 and is printable.

    Empty input returns False. Decode failure returns False. Non-printable
    bytes (control chars beyond \\t\\n\\r) return False — that signals
    binary even if the bytes happen to be UTF-8-compatible.
    """
    if not sample:
        return False
    try:
        decoded = sample[:sample_size].decode("utf-8")
    except UnicodeDecodeError:
        return False
    # Allow common whitespace control chars; reject other control bytes.
    for ch in decoded:
        cp = ord(ch)
        if cp < 0x20 and ch not in ("\t", "\n", "\r"):
            return False
    return True


def _peek_zip_office_subtype(raw_bytes: bytes) -> str | None:
    """Open ``raw_bytes`` as a zip and look at ``[Content_Types].xml`` to
    distinguish xlsx vs docx vs pptx.

    Returns None on any failure (corrupt zip, missing manifest, unknown
    Office type). Caller falls back to declared mime then.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            try:
                manifest = zf.read("[Content_Types].xml").decode(
                    "utf-8", errors="replace"
                )
            except KeyError:
                return None
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    # Order matters — check most specific first.
    if "spreadsheetml" in manifest:
        return _MIME_XLSX
    if "wordprocessingml" in manifest:
        return _MIME_DOCX
    return None


def sniff_real_mime(
    raw_bytes: bytes | None,
    file_name: str,
    declared_mime: str,
) -> str:
    """Return the best-guess real MIME for an uploaded file.

    Algorithm:
      1. If ``declared_mime`` is non-ambiguous → return it (trust declared).
      2. If ``raw_bytes`` is empty/None → return ``declared_mime`` (no data
         to sniff; caller decides how to handle).
      3. Match magic bytes (first 8 bytes) against known signatures.
      4. ZIP (PK\\x03\\x04) → peek manifest to choose xlsx vs docx.
      5. UTF-8 printable text → return ``text/markdown`` (safest text
         route; the markdown parser also handles plain text gracefully).
      6. Fallback → return ``declared_mime`` unchanged.

    The function is pure (no IO beyond reading raw_bytes) and
    side-effect-free, so it's safe to call on the hot ingest path.
    """
    declared = (declared_mime or "").strip().lower()
    if declared and declared not in AMBIGUOUS_DECLARED_MIMES:
        return declared_mime  # caller's declared mime kept verbatim
    if not raw_bytes:
        return declared_mime
    # Read 16 bytes so HTML doctype check (9 chars) has room.
    head = raw_bytes[:16]

    # PDF — '%PDF-'
    if head[:5] == b"%PDF-":
        return _MIME_PDF
    # HTML — '<!DOCTYPE' or '<html' (case-insensitive on first 9 chars)
    head_lower = head[:9].lower()
    if head_lower.startswith(b"<!doctype") or head_lower[:5] == b"<html":
        return _MIME_HTML
    # ZIP-based Office formats — PK\x03\x04
    if head[:4] == b"PK\x03\x04":
        # Prefer file extension hint when caller supplied a precise one.
        ext = (
            "." + file_name.rsplit(".", 1)[-1].lower()
            if file_name and "." in file_name
            else ""
        )
        if ext in (".xlsx", ".xlsm"):
            return _MIME_XLSX
        if ext == ".docx":
            return _MIME_DOCX
        # No ext hint — peek inside the zip.
        sub = _peek_zip_office_subtype(raw_bytes)
        if sub is not None:
            return sub
        # Generic zip — return declared (parser registry will skip).
        return declared_mime
    # UTF-8 text — route to markdown parser (handles plain text too).
    if _looks_like_utf8_text(raw_bytes):
        # CSV heuristic: comma-rich first line → text/csv (better parser).
        try:
            first_line = raw_bytes[:1024].decode("utf-8").split("\n", 1)[0]
        except UnicodeDecodeError:
            first_line = ""
        if first_line.count(",") >= 3:
            return _MIME_CSV
        return _MIME_MARKDOWN

    return declared_mime


__all__ = [
    "AMBIGUOUS_DECLARED_MIMES",
    "sniff_real_mime",
]
