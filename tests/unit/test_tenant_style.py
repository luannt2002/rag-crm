"""[T1-Smartness] P3 Tenant-Profiling — per-bot ingest STYLE normalizer.

``apply_tenant_style`` promotes an owner's non-standard heading / table
convention into the canonical markdown the global block-detection rules
(`shared/chunking/analyze.py`) already understand — applied at pre-process so
AdapChunk / analyze work unchanged. Domain-neutral, opt-in: both knobs default
OFF and the function is then a byte-identity no-op (existing bots unaffected).

Pinned contract (deterministic, no LLM, no DB):
- uppercase-promote: a standalone ALL-CAPS short line gains a ``## `` prefix;
- table-separator: a row with >=2 of the owner separator becomes a pipe row;
- guards: already-markdown headings/tables untouched; prose with a single
  incidental separator untouched; sentence-shaped lines untouched;
- both-default = exact identity.
"""
from __future__ import annotations

from ragbot.shared.chunking.tenant_style import apply_tenant_style


def _norm(text: str, *, upper: bool = False, sep: str = "") -> str:
    return apply_tenant_style(
        text, heading_uppercase_promote=upper, table_separator=sep,
    )


# ── default OFF = identity ────────────────────────────────────────────────
def test_both_default_is_byte_identity() -> None:
    src = "BẢNG GIÁ\nGói A; 500.000; Combo\nMột câu văn bình thường, có dấu phẩy."
    assert _norm(src) == src


# ── uppercase heading promote ─────────────────────────────────────────────
def test_uppercase_short_line_promoted_to_h2() -> None:
    src = "BẢNG GIÁ DỊCH VỤ\nNội dung dòng thường ở đây."
    out = _norm(src, upper=True)
    assert out.startswith("## BẢNG GIÁ DỊCH VỤ\n")
    # The non-uppercase line is left alone.
    assert "Nội dung dòng thường ở đây." in out


def test_uppercase_promote_skips_existing_heading() -> None:
    src = "## ĐÃ CÓ HEADING\nthường"
    assert _norm(src, upper=True) == src  # already a heading → untouched


def test_uppercase_promote_skips_too_long_line() -> None:
    # Longer than DEFAULT_TOPIC_UPPER_SECTION_MAX_CHARS (80) → not a heading.
    long_caps = "A" * 90
    src = f"{long_caps}\nthường"
    assert _norm(src, upper=True) == src


def test_uppercase_promote_skips_table_row() -> None:
    # An uppercase line that is actually a pipe table row must NOT be promoted.
    src = "| TÊN | GIÁ |\nthường"
    assert _norm(src, upper=True) == src


def test_uppercase_promote_off_leaves_caps_untouched() -> None:
    src = "BẢNG GIÁ\nthường"
    assert _norm(src, upper=False) == src


# ── table separator normalize ─────────────────────────────────────────────
def test_semicolon_row_becomes_pipe_row() -> None:
    src = "Gói A; 500.000; Combo"
    out = _norm(src, sep=";")
    assert out == "| Gói A | 500.000 | Combo |"


def test_prose_with_single_separator_untouched() -> None:
    # Only ONE ';' (< 2 → < 3 cells) → prose, not a table row.
    src = "Chào bạn; tôi khỏe."
    assert _norm(src, sep=";") == src


def test_sentence_shaped_row_not_converted() -> None:
    # Ends with '.' and has '. ' → sentence, never a data row even with 2 ';'.
    src = "Câu một; câu hai; và kết. "
    assert _norm(src, sep=";") == src


def test_separator_off_leaves_row_untouched() -> None:
    src = "Gói A; 500.000; Combo"
    assert _norm(src, sep="") == src


def test_existing_pipe_table_untouched_by_separator() -> None:
    src = "| a | b | c |"
    assert _norm(src, sep=";") == src


# ── both knobs together ───────────────────────────────────────────────────
def test_uppercase_and_separator_combined() -> None:
    src = "BẢNG GIÁ\nGói A; 500.000; Combo\nvăn xuôi thường ở dòng này"
    out = _norm(src, upper=True, sep=";")
    lines = out.split("\n")
    assert lines[0] == "## BẢNG GIÁ"
    assert lines[1] == "| Gói A | 500.000 | Combo |"
    assert lines[2] == "văn xuôi thường ở dòng này"
