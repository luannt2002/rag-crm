"""Unit tests for ``application.services.content_type_router``.

The router is RAG-Anything M23 — pure block-type grouping with a
structlog observability event. Tests cover:

  1. Empty iterable → empty dict, no histogram entries.
  2. Mixed types group correctly (counts match input).
  3. Block missing the type attribute falls into the default.
  4. Custom ``type_default`` override is honoured when block is bare.
  5. Custom ``type_attr`` is honoured (works on objects that use a
     different discriminator name).
  6. ``emit_type_histogram`` returns the {type: count} dict and emits
     a structlog ``content_type_histogram`` event with the expected
     payload (document_id, histogram, total_blocks).
  7. Return value of ``group_by_block_type`` is a plain ``dict``
     (not a defaultdict) — guards against silent empty-group creation
     downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from ragbot.application.services.content_type_router import (
    emit_type_histogram,
    group_by_block_type,
)


# ----------------------------------------------------------------------
# Test fixtures
# ----------------------------------------------------------------------


@dataclass
class _Block:
    """Minimal block stub honoring the ``block_type`` convention."""

    block_type: str
    payload: str = ""


@dataclass
class _BareBlock:
    """Block without a ``block_type`` attribute — falls into default."""

    payload: str = ""


@dataclass
class _CustomAttrBlock:
    """Block using a different discriminator name (``modality``)."""

    modality: str


@pytest.fixture(autouse=True)
def _structlog_test_config() -> None:
    """Make structlog testable — capture_logs needs default processors."""
    structlog.reset_defaults()


# ----------------------------------------------------------------------
# group_by_block_type
# ----------------------------------------------------------------------


def test_empty_iterable_returns_empty_dict() -> None:
    result = group_by_block_type([])

    assert result == {}
    # Plain dict, not defaultdict — prevents silent empty-group creation
    # when downstream code does ``groups["nonexistent"]``.
    assert type(result) is dict  # noqa: E721 — strict type check intended


def test_mixed_types_grouped_correctly() -> None:
    blocks = [
        _Block(block_type="text", payload="a"),
        _Block(block_type="text", payload="b"),
        _Block(block_type="text", payload="c"),
        _Block(block_type="table", payload="t1"),
        _Block(block_type="table", payload="t2"),
        _Block(block_type="code", payload="x"),
    ]

    groups = group_by_block_type(blocks)

    assert set(groups.keys()) == {"text", "table", "code"}
    assert len(groups["text"]) == 3
    assert len(groups["table"]) == 2
    assert len(groups["code"]) == 1
    # Order preserved within each group.
    assert [b.payload for b in groups["text"]] == ["a", "b", "c"]


def test_block_missing_type_attr_falls_into_default() -> None:
    """No ``block_type`` attribute → default bucket."""
    blocks: list[Any] = [
        _BareBlock(payload="orphan-1"),
        _Block(block_type="table", payload="t"),
        _BareBlock(payload="orphan-2"),
    ]

    groups = group_by_block_type(blocks)

    # Default is ``DEFAULT_CHUNK_TYPE_TEXT`` ("text") per the helper.
    assert "text" in groups
    assert len(groups["text"]) == 2
    assert len(groups["table"]) == 1


def test_type_default_override_honoured() -> None:
    """Explicit ``type_default`` wins over the constant."""
    blocks = [_BareBlock(payload="a"), _BareBlock(payload="b")]

    groups = group_by_block_type(blocks, type_default="unknown")

    assert "unknown" in groups
    assert len(groups["unknown"]) == 2
    assert "text" not in groups


def test_custom_type_attr_honoured() -> None:
    """``type_attr`` lets the router read e.g. ``modality`` instead."""
    blocks = [
        _CustomAttrBlock(modality="image"),
        _CustomAttrBlock(modality="image"),
        _CustomAttrBlock(modality="audio"),
    ]

    groups = group_by_block_type(blocks, type_attr="modality")

    assert set(groups.keys()) == {"image", "audio"}
    assert len(groups["image"]) == 2
    assert len(groups["audio"]) == 1


def test_falsy_type_value_falls_into_default() -> None:
    """``block_type=""`` (falsy) should be treated as missing → default."""
    blocks = [_Block(block_type="", payload="x")]

    groups = group_by_block_type(blocks)

    # Empty string is falsy → default kicks in.
    assert "text" in groups
    assert "" not in groups


# ----------------------------------------------------------------------
# emit_type_histogram
# ----------------------------------------------------------------------


def test_emit_histogram_returns_counts() -> None:
    groups = {
        "text": [_Block(block_type="text") for _ in range(3)],
        "table": [_Block(block_type="table") for _ in range(2)],
    }

    hist = emit_type_histogram(groups, document_id="doc-123")

    assert hist == {"text": 3, "table": 2}


def test_emit_histogram_emits_structlog_event() -> None:
    """Observability rule — the event must reach the structlog sink."""
    groups = {
        "text": [_Block(block_type="text") for _ in range(4)],
        "code": [_Block(block_type="code")],
    }

    with capture_logs() as logs:
        emit_type_histogram(groups, document_id="doc-abc")

    histogram_events = [r for r in logs if r.get("event") == "content_type_histogram"]
    assert len(histogram_events) == 1
    record = histogram_events[0]
    assert record["document_id"] == "doc-abc"
    assert record["histogram"] == {"text": 4, "code": 1}
    assert record["total_blocks"] == 5


def test_emit_histogram_accepts_none_document_id() -> None:
    """Ingest-time callers may not yet have a persisted document UUID."""
    with capture_logs() as logs:
        hist = emit_type_histogram({}, document_id=None)

    assert hist == {}
    events = [r for r in logs if r.get("event") == "content_type_histogram"]
    assert events[0]["total_blocks"] == 0
    assert events[0]["document_id"] is None
