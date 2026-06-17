"""Dịch vụ kiểm tra và tải nội dung từ Google Docs/Sheets.

Xác thực loại link, quyền truy cập, và tải nội dung dạng text thuần
từ Google Docs (export txt) hoặc Google Sheets (export csv).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog

from ragbot.shared.constants import (
    DEFAULT_GOOGLE_DOC_MIN_CONTENT_CHARS,
    DEFAULT_HTTP_SHORT_TIMEOUT_S,
    DEFAULT_HTTP_TIMEOUT_S,
    MAX_DOWNLOAD_BYTES,
)

logger = structlog.get_logger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html",
}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lấy hoặc tạo mới HTTP client dùng chung.
    @return: instance httpx.AsyncClient
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT_S, follow_redirects=True, headers=_HEADERS)
    return _client

_PRIVATE_INDICATORS = [
    "you need permission", "bạn cần quyền truy cập",
    "request access", "yêu cầu quyền truy cập",
    "access denied", "quyền truy cập bị từ chối",
]


@dataclass
class LinkValidation:
    ok: bool
    doc_type: str | None = None  # "docs" | "sheets"
    access: str | None = None  # "public" | "anyone_with_link"
    error: str | None = None


async def validate_link(url: str) -> LinkValidation:
    """Xác thực URL Google Docs/Sheets: kiểm tra loại, quyền truy cập, định dạng.
    @param url: URL cần xác thực
    @return: LinkValidation chứa kết quả kiểm tra
    """
    if not url:
        return LinkValidation(ok=False, error="URL không được để trống")

    # Resolve shortened URLs
    if "goo.gl/" in url:
        try:
            client = _get_client()
            resp = await client.head(url, timeout=DEFAULT_HTTP_SHORT_TIMEOUT_S)
            url = str(resp.url)
        except (httpx.HTTPError, OSError):
            # Shortener returned an HTTP error or DNS / socket layer failed
            # → user sees a friendly message instead of stack trace.
            return LinkValidation(ok=False, error="Không thể mở link rút gọn")

    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        # urlparse raises ValueError on malformed input (e.g. invalid IPv6
        # literal) and AttributeError if a non-string slipped through.
        return LinkValidation(ok=False, error="URL không hợp lệ")

    hostname = parsed.hostname or ""
    if not any(h in hostname for h in ["google.com", "docs.google.com", "drive.google.com"]):
        return LinkValidation(ok=False, error="Không phải link Google. Chỉ hỗ trợ Google Docs hoặc Sheets.")

    path = parsed.path

    # Reject drive file uploads and folders
    if "drive.google.com" in hostname:
        if "/file/d/" in path:
            return LinkValidation(ok=False, error="Không hỗ trợ file tải lên Google Drive. Vui lòng dùng link Google Docs hoặc Sheets.")
        if "/folder/" in path:
            return LinkValidation(ok=False, error="Không hỗ trợ link thư mục Drive.")

    # Detect type
    doc_type = _detect_type(hostname, path)
    if doc_type is None:
        return LinkValidation(ok=False, error="Loại tài liệu Google không xác định.")
    if doc_type == "slides":
        return LinkValidation(ok=False, error="Không hỗ trợ Google Slides.")
    if doc_type == "forms":
        return LinkValidation(ok=False, error="Không hỗ trợ Google Forms.")

    # Check page title for .docx/.xlsx uploads
    title = await _fetch_page_title(url)
    if title:
        tl = title.lower()
        if ".docx" in tl:
            return LinkValidation(ok=False, error="File .docx tải lên Google Drive. Vui lòng mở bằng Google Docs rồi lấy link.")
        if ".xlsx" in tl:
            return LinkValidation(ok=False, error="File .xlsx tải lên Google Drive. Vui lòng mở bằng Google Sheets rồi lấy link.")

    # Check access
    access = await _check_access(url)
    if access == "private":
        return LinkValidation(ok=False, error="Tài liệu ở chế độ private. Vui lòng chia sẻ công khai hoặc 'anyone with link'.")
    if access == "error":
        return LinkValidation(ok=False, error="Link tài liệu không hợp lệ, vui lòng lấy link trên thanh URL trình duyệt.")

    return LinkValidation(ok=True, doc_type=doc_type, access=access)




async def fetch_content(
    url: str,
    doc_type: str,
    *,
    max_download_size: int | None = None,
) -> str | None:
    """Tải nội dung text thuần từ Google Docs/Sheets qua export URL.

    For Sheets: parses the `gid` parameter (either as `?gid=N` query OR
    `#gid=N` fragment, both are valid Google URL forms) and forwards it
    to the export endpoint. Without this each tab of the same workbook
    exported to the SAME default sheet, breaking content_hash dedup
    (two distinct tabs reported as duplicate) and silently dropping
    every tab the user thought they'd ingested except the first.

    @param url: URL tài liệu Google
    @param doc_type: loại tài liệu ("docs" hoặc "sheets")
    @param max_download_size: giới hạn byte tối đa (None = dùng default 10MB)
    @return: nội dung text hoặc None nếu thất bại
    """
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        return None
    doc_id = match.group(1)

    if doc_type == "docs":
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    elif doc_type == "sheets":
        # Try query string first, then fragment.
        # Examples we must handle:
        #   .../edit?gid=1394860155#gid=1394860155
        #   .../edit#gid=0
        #   .../edit?gid=0
        gid: str | None = None
        gid_match = re.search(r"[?&]gid=([0-9]+)", url)
        if gid_match:
            gid = gid_match.group(1)
        else:
            frag_match = re.search(r"#gid=([0-9]+)", url)
            if frag_match:
                gid = frag_match.group(1)
        export_url = f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv"
        if gid is not None:
            export_url += f"&gid={gid}"
    else:
        return None

    try:
        client = _get_client()
        resp = await client.get(export_url)
        if resp.status_code == 200:
            # Guard against unbounded memory usage from huge documents
            max_download_bytes = max_download_size or MAX_DOWNLOAD_BYTES
            if len(resp.content) > max_download_bytes:
                logger.warning(
                    "google_doc_too_large",
                    url=url,
                    size=len(resp.content),
                    max=max_download_bytes,
                )
                raise ValueError(
                    f"Document too large: {len(resp.content)} bytes (max {max_download_bytes})"
                )
            content = resp.text
            if content and len(content.strip()) > DEFAULT_GOOGLE_DOC_MIN_CONTENT_CHARS:
                return content.strip()
    except ValueError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        logger.warning(
            "google_fetch_failed",
            url=url,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    return None


def _detect_type(hostname: str, path: str) -> str | None:
    """Nhận diện loại tài liệu Google từ hostname và path.
    @param hostname: tên miền của URL
    @param path: đường dẫn URL
    @return: loại tài liệu ("docs", "sheets", "slides", "forms") hoặc None
    """
    if "/document/" in path or ("/d/" in path and "docs.google.com" in hostname and "/spreadsheets/" not in path):
        return "docs"
    if "/spreadsheets/" in path or "/spreadsheet/" in path:
        return "sheets"
    if "/presentation/" in path or "/present/" in path:
        return "slides"
    if "/forms/" in path or "/form/" in path:
        return "forms"
    return None


async def _fetch_page_title(url: str) -> str | None:
    """Tải và trích xuất thẻ title từ trang HTML của URL.
    @param url: URL cần lấy title
    @return: nội dung thẻ title hoặc None
    """
    try:
        client = _get_client()
        resp = await client.get(url, timeout=DEFAULT_HTTP_SHORT_TIMEOUT_S)
        if resp.status_code == 200:
            text_content = resp.text
            start = text_content.find("<title>")
            end = text_content.find("</title>")
            if start != -1 and end != -1:
                return text_content[start + 7:end]
    except (httpx.HTTPError, OSError):
        # Title fetch is best-effort: any HTTP / network failure → None.
        pass
    return None


async def _check_access(url: str) -> str:
    """Kiểm tra quyền truy cập tài liệu Google (public, private, anyone_with_link).
    @param url: URL tài liệu cần kiểm tra
    @return: chuỗi trạng thái truy cập ("public", "private", "anyone_with_link", "error", "unknown")
    """
    try:
        client = _get_client()
        resp = await client.get(url, timeout=DEFAULT_HTTP_SHORT_TIMEOUT_S)
        if resp.status_code == 200:
            body = resp.text.lower()
            title = ""
            start = body.find("<title>")
            end = body.find("</title>")
            if start != -1 and end != -1:
                title = body[start + 7:end]
            if any(ind in title or ind in body[:3000] for ind in _PRIVATE_INDICATORS):
                return "private"
            if "sign in" in title or "đăng nhập" in title:
                return "anyone_with_link"
            return "public"
        if resp.status_code in (401, 403):
            return "private"
    except (httpx.HTTPError, OSError):
        # Probe failed at HTTP / network layer → caller surfaces the
        # generic "link not valid" message.
        return "error"
    return "unknown"


__all__ = ["LinkValidation", "fetch_content", "validate_link"]
