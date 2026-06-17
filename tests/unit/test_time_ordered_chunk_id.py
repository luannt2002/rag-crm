"""UUIDv7 chunk-id generator (2026-06-13) — time-ordered PK insert locality.

Pins the bridge from ``uuid_utils.uuid7()`` into a stdlib ``uuid.UUID`` and the
monotonic property that motivates v7 over v4 (sequential B-tree appends on
``document_chunks.id`` vs random scatter).
"""
from __future__ import annotations

import time
import uuid

from ragbot.shared.chunk_identity import time_ordered_chunk_id


def test_returns_stdlib_uuid_version_7() -> None:
    u = time_ordered_chunk_id()
    assert isinstance(u, uuid.UUID)
    assert u.version == 7


def test_unique_across_calls() -> None:
    ids = {time_ordered_chunk_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_time_ordered_monotonic() -> None:
    # v7 embeds a ms timestamp in the high bits → later id sorts after earlier.
    a = time_ordered_chunk_id()
    time.sleep(0.005)
    b = time_ordered_chunk_id()
    assert a < b, "UUIDv7 must be time-ordered (a generated before b)"
