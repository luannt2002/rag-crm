"""_fetch_url_bounded — URL-ingest OOM guard for the document worker.

An unbounded ``response.content`` read lets one oversized remote document OOM
the shared worker process (killing every co-tenant ingest in it). These pins
cover the three guard states — normal pass-through, Content-Length preflight,
mid-stream cap breach — plus the terminal (non-transient) classification that
stops the caller from OCR-refetching the same huge URL.
"""

import httpx
import pytest

from ragbot.interfaces.workers import document_worker as dw


class _FakeResp:
    def __init__(self, headers: dict, chunks: list[bytes], ok: bool = True) -> None:
        self.headers = headers
        self._chunks = chunks
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise httpx.HTTPError("bad status")

    async def aiter_bytes(self, _size: int | None = None):
        for c in self._chunks:
            yield c


class _FakeStreamCtx:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeResp:
        return self._resp

    async def __aexit__(self, *_a) -> bool:
        return False


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    def stream(self, _method: str, _url: str) -> _FakeStreamCtx:
        return _FakeStreamCtx(self._resp)


@pytest.mark.asyncio
async def test_normal_body_returns_full_bytes(monkeypatch) -> None:
    monkeypatch.setattr(dw, "DEFAULT_UPLOAD_STREAM_MAX_BYTES", 1000)
    resp = _FakeResp({"content-length": "6"}, [b"abc", b"def"])
    out = await dw._fetch_url_bounded(_FakeClient(resp), "http://x")
    assert out == b"abcdef"


@pytest.mark.asyncio
async def test_content_length_preflight_rejects_before_read(monkeypatch) -> None:
    monkeypatch.setattr(dw, "DEFAULT_UPLOAD_STREAM_MAX_BYTES", 100)
    # Chunk would blow the cap, but the preflight must reject before reading it.
    resp = _FakeResp({"content-length": "9999"}, [b"x" * 9999])
    with pytest.raises(dw._RemoteBodyTooLarge):
        await dw._fetch_url_bounded(_FakeClient(resp), "http://x")


@pytest.mark.asyncio
async def test_stream_guard_rejects_when_content_length_absent(monkeypatch) -> None:
    # No Content-Length (chunked transfer) — the streaming accumulation guard
    # must still abort once the running total exceeds the cap.
    monkeypatch.setattr(dw, "DEFAULT_UPLOAD_STREAM_MAX_BYTES", 5)
    resp = _FakeResp({}, [b"aaa", b"bbb", b"ccc"])  # 9 bytes > 5
    with pytest.raises(dw._RemoteBodyTooLarge):
        await dw._fetch_url_bounded(_FakeClient(resp), "http://x")


@pytest.mark.asyncio
async def test_lying_content_length_still_caught_by_stream_guard(monkeypatch) -> None:
    # Server declares a tiny body but sends more — the stream guard is the
    # backstop against a lying Content-Length.
    monkeypatch.setattr(dw, "DEFAULT_UPLOAD_STREAM_MAX_BYTES", 4)
    resp = _FakeResp({"content-length": "1"}, [b"aa", b"bb", b"cc"])
    with pytest.raises(dw._RemoteBodyTooLarge):
        await dw._fetch_url_bounded(_FakeClient(resp), "http://x")


@pytest.mark.asyncio
async def test_malformed_content_length_falls_back_to_stream(monkeypatch) -> None:
    monkeypatch.setattr(dw, "DEFAULT_UPLOAD_STREAM_MAX_BYTES", 100)
    resp = _FakeResp({"content-length": "not-a-number"}, [b"tiny"])
    out = await dw._fetch_url_bounded(_FakeClient(resp), "http://x")
    assert out == b"tiny"


def test_too_large_is_terminal_not_transient() -> None:
    """A too-large body must NOT be retried (re-fetch OOMs again)."""
    assert dw._is_transient_ingest_error(dw._RemoteBodyTooLarge("x")) is False
    # It IS a ValueError so the terminal/malformed classification catches it.
    assert isinstance(dw._RemoteBodyTooLarge("x"), ValueError)
