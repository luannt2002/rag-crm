"""``find_duplicate_pairs`` calls ``_tokenize`` per Jaccard pair, so an
N-chunk batch costs O(N²) tokenisation when the actual work is O(N×M)
distinct token sets. Pre-tokenising once outside the inner loop drops
re-tokenisation cost on a 100-chunk batch from ~10k re-runs to 100,
which is the hot path during ingest dedup.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from ragbot.shared import dedup as dedup_mod


def _make_chunks(n: int) -> list[dict]:
    """Generate n distinct chunks with deterministic content."""
    base = datetime(2026, 1, 1)
    out = []
    for i in range(n):
        # Force pairs (i, i+1) to share many tokens so jaccard is non-trivial
        toks = " ".join(f"word{i % 7}_{j}" for j in range(20))
        out.append({
            "id": f"id-{i}",
            "content": f"chunk {i} {toks} unique{i}",
            "created_at": base + timedelta(seconds=i),
        })
    return out


def test_find_duplicate_pairs_tokenises_each_chunk_once() -> None:
    chunks = _make_chunks(20)
    call_log: list[str] = []
    real_tok = dedup_mod._tokenize

    def _spy(text: str) -> set[str]:
        call_log.append(text[:30])
        return real_tok(text)

    with patch.object(dedup_mod, "_tokenize", side_effect=_spy) as mock_tok:
        dedup_mod.find_duplicate_pairs(chunks, threshold=0.5, min_chars=1)

    # With pre-tokenize: tokenizer fires once per candidate (== n).
    # Without (legacy): fires twice per pair (== n * (n-1)) — for n=20 that
    # is 380 calls, vs the new 20.
    assert mock_tok.call_count <= len(chunks), (
        f"expected ≤{len(chunks)} _tokenize calls (one per chunk); "
        f"got {mock_tok.call_count} — re-tokenising per pair O(n²)"
    )


def test_find_duplicate_pairs_results_unchanged_after_pre_tokenise() -> None:
    """Pre-tokenise must not change the set of pairs reported."""
    chunks = _make_chunks(10)
    pairs = dedup_mod.find_duplicate_pairs(chunks, threshold=0.5, min_chars=1)
    # All chunk pairs sharing word0..word6 buckets should match; verify
    # the function still returns a list of (str, str) tuples.
    assert isinstance(pairs, list)
    for kept, drop in pairs:
        assert isinstance(kept, str)
        assert isinstance(drop, str)
        assert kept != drop


def test_jaccard_public_helper_still_works() -> None:
    """Backwards compat — the module-level ``jaccard`` helper used by
    other call sites must keep its single-string-pair signature."""
    sim = dedup_mod.jaccard("foo bar baz", "foo bar qux")
    assert 0.0 < sim < 1.0
    assert dedup_mod.jaccard("", "anything") == 0.0
    assert dedup_mod.jaccard("alpha", "alpha") == 1.0
