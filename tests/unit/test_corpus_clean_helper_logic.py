"""Pure-logic tests for ``scripts/corpus_clean.py`` (no DB).

Behaviour tests covering the excerpt trimmer, price regex, RAG-friendly
heuristic scorer, JSON / markdown output emitter, and CLI parser shape.

The DB-touching subcommand handlers are exercised separately in
``tests/integration/test_corpus_clean_helper.py`` (gated by
``--run-integration``).
"""

from __future__ import annotations

import io
import json
import re
import uuid
from contextlib import redirect_stdout

import pytest

from scripts.corpus_clean import (
    _emit,
    _excerpt,
    _extract_prices,
    _score_doc,
    _service_key,
    build_parser,
)
from ragbot.shared.constants import (
    DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS,
    DEFAULT_CORPUS_CLEAN_PRICE_REGEX,
    DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS,
    DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS,
)


def test_excerpt_trims_long_text() -> None:
    long = "x" * (DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS + 50)
    out = _excerpt(long, max_chars=DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS)
    assert len(out) == DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS
    assert out.endswith("…")


def test_excerpt_collapses_newlines() -> None:
    out = _excerpt("line1\nline2", max_chars=DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS)
    assert "\n" not in out
    assert "line1 line2" == out


def test_extract_prices_default_regex_matches_vn_formats() -> None:
    pattern = re.compile(DEFAULT_CORPUS_CLEAN_PRICE_REGEX)
    found = _extract_prices(
        "Triet long 199K hoac combo 1.499.000 hoac 2,500,000 cu",
        pattern,
    )
    assert "199K" in found
    assert "1.499.000" in found
    assert "2,500,000" in found


def test_service_key_normalises_whitespace_and_case() -> None:
    a = _service_key("  Tri Mun   chuyen sau  ", head_chars=12)
    b = _service_key("tri mun chuyen sau", head_chars=12)
    assert a == b
    assert len(a) <= 12


def test_score_doc_passes_friendly_doc() -> None:
    # ``RAG_FRIENDLY_*`` band is [min, max] words (inclusive); pick a count
    # in the middle so the heuristic exercises the happy path.
    body_words = (
        DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS
        + DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS
    ) // 2
    body = "## Bang gia\n" + ("word " * body_words) + " 199K"
    out = _score_doc(
        body,
        min_words=DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS,
        max_words=DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS,
    )
    assert out["heading_count"] >= 1
    assert out["has_explicit_numbers"] is True
    assert out["rag_friendly"] is True


def test_score_doc_flags_missing_heading_and_numbers() -> None:
    body = "plain prose without structure or any digits at all "
    out = _score_doc(
        body * 5,
        min_words=DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS,
        max_words=DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS,
    )
    assert out["rag_friendly"] is False
    findings_blob = " | ".join(out["findings"])
    assert "R1" in findings_blob
    assert "R6" in findings_blob


def test_emit_json_round_trip() -> None:
    rows = [{"chunk_id": "abc", "dup_count": 3, "excerpt": "a|b\nc"}]
    header = {"subcommand": "find-duplicate-chunks", "groups_with_duplicates": 1}
    buf = io.StringIO()
    with redirect_stdout(buf):
        _emit(rows, header=header, fmt="json")
    parsed = json.loads(buf.getvalue())
    assert parsed["header"]["subcommand"] == "find-duplicate-chunks"
    assert parsed["rows"][0]["chunk_id"] == "abc"
    assert parsed["rows"][0]["dup_count"] == 3


def test_emit_md_renders_table_and_escapes_pipes() -> None:
    rows = [{"chunk_id": "abc", "excerpt": "pipe|here"}]
    buf = io.StringIO()
    with redirect_stdout(buf):
        _emit(rows, header={"subcommand": "find-duplicate-chunks"}, fmt="md")
    out = buf.getvalue()
    assert "| chunk_id | excerpt |" in out
    assert "pipe\\|here" in out


def test_emit_md_no_findings_says_so() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _emit([], header={"subcommand": "find-empty-embeddings"}, fmt="md")
    assert "(no findings)" in buf.getvalue()


def test_build_parser_rejects_missing_subcommand() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_dry_run_flag_present_on_all_read_only_subcmds() -> None:
    parser = build_parser()
    for sub in (
        "find-duplicate-chunks",
        "find-conflict-prices",
        "find-empty-embeddings",
    ):
        ns = parser.parse_args(
            [sub, "--bot-uuid", str(uuid.uuid4()), "--allow-uuid", "--dry-run"],
        )
        assert ns.dry_run is True


def test_build_parser_validate_rag_friendly_requires_doc_id() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["validate-rag-friendly"])


def test_build_parser_re_embed_apply_passthrough() -> None:
    parser = build_parser()
    ns = parser.parse_args(
        ["re-embed-bot", "--bot-uuid", str(uuid.uuid4()), "--allow-uuid", "--apply"],
    )
    assert ns.apply is True
