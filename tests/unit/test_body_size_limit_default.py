"""Pin test: body-size middleware default limit covers demo upload routes.

Was a production gap on 2026-05-26: demo UI uploaded a 906 KB legal corpus
to ``/api/ragbot/test/bots/{bot_id}/{channel_type}/documents/upload``, which
did NOT match the middleware's ``/api/ragbot/documents`` prefix (test routes
go through ``/api/ragbot/test/...``) and fell into the default cap of
512 KB, returning 413 PAYLOAD_TOO_LARGE.

Resolution: bump the default to 10 MB so demo + admin routes covering
legitimate medium-sized payloads (corpora up to ~10 MB, audit exports,
admin config dumps) succeed. Production ingest paths (/documents, /sync)
keep their 16 MB cap; chat keeps 256 KB.

This pin guards the default value so a future refactor cannot silently
re-tighten it back to 512 KB and break demo upload again.
"""

from __future__ import annotations

from ragbot.shared.constants import (
    DEFAULT_MAX_BODY_CHAT_BYTES,
    DEFAULT_MAX_BODY_DEFAULT_BYTES,
    DEFAULT_MAX_BODY_INGEST_BYTES,
)


def test_body_default_limit_is_ten_megabytes() -> None:
    """Default cap MUST be 10 MB to cover demo upload route (906 KB legal
    corpus + similar)."""
    assert DEFAULT_MAX_BODY_DEFAULT_BYTES == 10 * 1024 * 1024


def test_body_chat_limit_is_smaller_than_default() -> None:
    """Chat path (user message) must remain tighter than default."""
    assert DEFAULT_MAX_BODY_CHAT_BYTES < DEFAULT_MAX_BODY_DEFAULT_BYTES


def test_body_ingest_limit_is_larger_than_default() -> None:
    """Prod ingest path (/documents, /sync) must allow more than default."""
    assert DEFAULT_MAX_BODY_INGEST_BYTES > DEFAULT_MAX_BODY_DEFAULT_BYTES


def test_body_default_covers_906kb_demo_upload() -> None:
    """Regression guard: the original 906 KB tt09_2020.txt demo upload
    case that triggered the bump MUST fit under the new default."""
    historical_demo_payload = 927_741  # bytes; tt09 + multipart envelope
    assert historical_demo_payload < DEFAULT_MAX_BODY_DEFAULT_BYTES
