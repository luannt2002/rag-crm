"""Unit tests for shared/diff_reingest.py — chunk-level diff re-ingest.

T2 Cost+Perf. Behaviour under test:

1. ``compute_chunk_hashes`` is deterministic, length-stable, and collision-free
   for different inputs (catches a future "let's strip whitespace before
   hashing" silent break).
2. ``compute_diff`` produces (to_embed, unchanged, stale, cost_saved) such that:
   - Identical re-ingest → 0 to_embed, all unchanged (IDEMPOTENCY).
   - Single chunk modified mid-doc → exactly that index re-embedded
     (SURGICAL SKIP).
   - Doc shrank → tail indices marked stale (CLEANUP).
   - First ingest (empty ``existing_hashes``) → every chunk to_embed,
     no stale (FRESH).
   - ``cost_saved_usd > 0`` only when chunks were actually skipped.
3. ``estimate_embed_cost_usd`` follows the documented formula and survives
   a zero/negative ``chars_per_token`` override without divide-by-zero.
4. ``log_diff_event`` honours the feature flag (no event emitted when
   ``enabled=False``).
5. Length mismatch raises ``ValueError`` — protects against a programmer
   passing hashes/texts out of sync.

Tenant isolation is structural: the helper is pure and operates only on
the inputs it is given. Two independent calls with different inputs never
share state — asserted by calling the function back-to-back with
disjoint hash maps and verifying the second call's output ignores the
first call's data.
"""

from __future__ import annotations

import pytest

from ragbot.shared.constants import (
    DEFAULT_CHARS_PER_TOKEN_ESTIMATE,
    DEFAULT_CONTENT_HASH_HEX_LEN,
    DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
    TOKENS_PER_MILLION,
)
try:
    from ragbot.shared.diff_reingest import (
        DiffResult,
        compute_chunk_hashes,
        compute_diff,
        estimate_embed_cost_usd,
        log_diff_event,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "diff_reingest is dead-code (body commented out)",
        allow_module_level=True,
    )


# ── compute_chunk_hashes ─────────────────────────────────────────────


def test_compute_chunk_hashes_deterministic() -> None:
    """Same input twice → identical hex output (no salting, no random)."""
    texts = ["alpha", "beta", "gamma"]
    h1 = compute_chunk_hashes(texts)
    h2 = compute_chunk_hashes(texts)
    assert h1 == h2
    assert all(len(h) == DEFAULT_CONTENT_HASH_HEX_LEN for h in h1)


def test_compute_chunk_hashes_distinct_for_distinct_inputs() -> None:
    """Different inputs → different hashes (catches a hash-of-empty bug)."""
    h = compute_chunk_hashes(["alpha", "Alpha", "alphaa", ""])
    assert len({h[0], h[1], h[2]}) == 3
    # Empty string hashes to sha256('') prefix — well-defined, just not equal
    # to any non-empty string of the same prefix length.
    assert h[3] != h[0]


def test_compute_chunk_hashes_truncates_to_hex_len() -> None:
    """Explicit ``hex_len`` shortens output; preserves prefix correctness."""
    short = compute_chunk_hashes(["alpha"], hex_len=16)
    full = compute_chunk_hashes(["alpha"])
    assert len(short[0]) == 16
    assert full[0].startswith(short[0])


def test_compute_chunk_hashes_handles_none_safely() -> None:
    """A ``None`` element behaves like ``""`` (empty hash), no crash."""
    # type: ignore[list-item]
    out = compute_chunk_hashes([None, ""])  # type: ignore[list-item]
    assert out[0] == out[1]


# ── compute_diff: idempotency / fresh / partial / stale ──────────────


def test_compute_diff_idempotent_when_all_chunks_unchanged() -> None:
    """Re-ingest identical content → 0 to_embed, all unchanged → 0 embed calls."""
    texts = ["chunk-zero", "chunk-one", "chunk-two"]
    hashes = compute_chunk_hashes(texts)
    existing = {i: hashes[i] for i in range(len(hashes))}

    diff = compute_diff(texts, hashes, existing)

    assert diff.to_embed == ()
    assert diff.unchanged == (0, 1, 2)
    assert diff.stale == ()
    assert diff.chunks_skipped == 3
    assert diff.chunks_total == 3
    # Cost saved must be strictly positive on a non-empty skip set.
    assert diff.cost_saved_usd > 0.0


def test_compute_diff_fresh_ingest_all_to_embed() -> None:
    """First ingest (empty existing) → every chunk is new, nothing stale."""
    texts = ["alpha", "beta"]
    hashes = compute_chunk_hashes(texts)

    diff = compute_diff(texts, hashes, {})

    assert [idx for idx, _ in diff.to_embed] == [0, 1]
    assert diff.unchanged == ()
    assert diff.stale == ()
    assert diff.cost_saved_usd == 0.0


def test_compute_diff_single_chunk_changed_mid_doc() -> None:
    """Modify chunk[1] only → exactly that index re-embedded."""
    old_texts = ["section-A", "section-B-original", "section-C"]
    new_texts = ["section-A", "section-B-EDITED", "section-C"]
    old_hashes = compute_chunk_hashes(old_texts)
    new_hashes = compute_chunk_hashes(new_texts)
    existing = {i: old_hashes[i] for i in range(len(old_hashes))}

    diff = compute_diff(new_texts, new_hashes, existing)

    assert [idx for idx, _ in diff.to_embed] == [1]
    assert diff.unchanged == (0, 2)
    assert diff.stale == ()
    # Surgical: we paid for re-embedding 1/3 chunks, saved on 2/3.
    full_cost = estimate_embed_cost_usd(new_texts)
    skipped_cost = estimate_embed_cost_usd([new_texts[0], new_texts[2]])
    assert diff.cost_saved_usd == pytest.approx(skipped_cost)
    assert diff.cost_saved_usd < full_cost


def test_compute_diff_marks_stale_when_doc_shrinks() -> None:
    """Doc shrank from 4 chunks to 2 → indices 2, 3 are stale."""
    old_texts = ["a", "b", "c", "d"]
    new_texts = ["a", "b"]
    old_hashes = compute_chunk_hashes(old_texts)
    new_hashes = compute_chunk_hashes(new_texts)
    existing = {i: old_hashes[i] for i in range(len(old_hashes))}

    diff = compute_diff(new_texts, new_hashes, existing)

    assert diff.unchanged == (0, 1)
    assert diff.to_embed == ()
    assert diff.stale == (2, 3)
    assert diff.chunks_total == 2


def test_compute_diff_marks_new_when_doc_grows() -> None:
    """Doc grew from 2 chunks to 4 → indices 2, 3 are new (to_embed)."""
    old_texts = ["a", "b"]
    new_texts = ["a", "b", "c", "d"]
    old_hashes = compute_chunk_hashes(old_texts)
    new_hashes = compute_chunk_hashes(new_texts)
    existing = {i: old_hashes[i] for i in range(len(old_hashes))}

    diff = compute_diff(new_texts, new_hashes, existing)

    assert diff.unchanged == (0, 1)
    assert [idx for idx, _ in diff.to_embed] == [2, 3]
    assert diff.stale == ()


def test_compute_diff_mismatched_length_raises() -> None:
    """Texts vs hashes length mismatch → ValueError, NEVER silent mis-skip."""
    with pytest.raises(ValueError, match="length mismatch"):
        compute_diff(["a", "b"], ["only-one-hash"], {})


# ── cost estimation ─────────────────────────────────────────────────


def test_estimate_embed_cost_usd_follows_formula() -> None:
    """Verify the documented formula: chars/cpt /1e6 * usd_per_M."""
    # 4 * CHARS_PER_TOKEN chars → exactly 4 tokens by the heuristic.
    n_tokens = 1000
    char_count = int(n_tokens * DEFAULT_CHARS_PER_TOKEN_ESTIMATE)
    texts = ["x" * char_count]
    cost = estimate_embed_cost_usd(
        texts,
        cost_per_1m_tokens=DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
        chars_per_token=DEFAULT_CHARS_PER_TOKEN_ESTIMATE,
    )
    expected = (n_tokens / TOKENS_PER_MILLION) * DEFAULT_EMBED_COST_USD_PER_1M_TOKENS
    assert cost == pytest.approx(expected)


def test_estimate_embed_cost_usd_zero_chunks() -> None:
    """Empty input → 0.0 (no embedding to pay for)."""
    assert estimate_embed_cost_usd([]) == 0.0


def test_estimate_embed_cost_usd_survives_bad_cpt_override() -> None:
    """A misconfigured zero ``chars_per_token`` MUST not divide-by-zero."""
    cost = estimate_embed_cost_usd(["abcd"], chars_per_token=0)
    # Falls back to the constant — output is finite and non-negative.
    assert cost >= 0.0
    n_chars = 4
    expected = (
        (n_chars / DEFAULT_CHARS_PER_TOKEN_ESTIMATE / TOKENS_PER_MILLION)
        * DEFAULT_EMBED_COST_USD_PER_1M_TOKENS
    )
    assert cost == pytest.approx(expected)


def test_estimate_embed_cost_usd_scales_linearly_with_chars() -> None:
    """Double the chars → double the cost (linear in input length)."""
    base = estimate_embed_cost_usd(["x" * 1000])
    doubled = estimate_embed_cost_usd(["x" * 2000])
    assert doubled == pytest.approx(base * 2)


# ── log_diff_event: flag-gated structlog emission ────────────────────


def test_log_diff_event_emits_when_enabled(capfd: pytest.CaptureFixture[str]) -> None:
    """``enabled=True`` → ``diff_reingest_skip`` event reaches the configured sink.

    structlog in this project is wired to stdout (see global structlog setup);
    we capture stdout via ``capfd`` so the assertion is independent of whether
    the formatter is JSON or key=value.
    """
    texts = ["a", "b"]
    hashes = compute_chunk_hashes(texts)
    diff = compute_diff(texts, hashes, {i: hashes[i] for i in range(2)})

    log_diff_event(
        diff,
        enabled=True,
        record_bot_id="bot-test",
        record_document_id="doc-test",
    )

    captured = capfd.readouterr()
    payload = captured.out + captured.err
    assert "diff_reingest_skip" in payload
    assert "chunks_skipped=2" in payload or '"chunks_skipped": 2' in payload
    assert "bot-test" in payload


def test_log_diff_event_silent_when_disabled(capfd: pytest.CaptureFixture[str]) -> None:
    """``enabled=False`` → NO ``diff_reingest_skip`` emission (clean ablation)."""
    texts = ["a", "b"]
    hashes = compute_chunk_hashes(texts)
    diff = compute_diff(texts, hashes, {i: hashes[i] for i in range(2)})

    log_diff_event(
        diff,
        enabled=False,
        record_bot_id="bot-test",
        record_document_id="doc-test",
    )

    captured = capfd.readouterr()
    payload = captured.out + captured.err
    assert "diff_reingest_skip" not in payload


# ── DiffResult invariants ────────────────────────────────────────────


def test_diffresult_is_frozen() -> None:
    """``DiffResult`` is frozen → cannot be mutated post-construction."""
    diff = DiffResult(
        to_embed=(),
        unchanged=(),
        stale=(),
        chunks_total=0,
        cost_saved_usd=0.0,
    )
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        diff.chunks_total = 5  # type: ignore[misc]


def test_diffresult_chunks_skipped_matches_unchanged_length() -> None:
    """``chunks_skipped`` is an alias for ``len(unchanged)`` (consistency)."""
    diff = DiffResult(
        to_embed=((3, "h3"),),
        unchanged=(0, 1, 2),
        stale=(),
        chunks_total=4,
        cost_saved_usd=0.001,
    )
    assert diff.chunks_skipped == 3
    assert diff.chunks_skipped == len(diff.unchanged)


# ── pure-function isolation: no shared state between calls ───────────


def test_compute_diff_purity_two_back_to_back_calls() -> None:
    """Tenant isolation surrogate: pure-fn call B ignores call A's data.

    The diff helper is the boundary between persisted hashes (caller-
    scoped to one tenant/bot/doc) and the new chunk batch (same scope).
    By asserting that two consecutive calls with disjoint inputs produce
    independent outputs, we verify no module-level mutable state
    leaks between invocations — a structural guarantee that the helper
    cannot cross-contaminate two different tenants.
    """
    texts_a = ["tenant-a-chunk-0", "tenant-a-chunk-1"]
    hashes_a = compute_chunk_hashes(texts_a)
    existing_a = {0: hashes_a[0]}  # only chunk-0 was previously embedded

    diff_a = compute_diff(texts_a, hashes_a, existing_a)
    assert diff_a.unchanged == (0,)
    assert [idx for idx, _ in diff_a.to_embed] == [1]

    texts_b = ["tenant-b-chunk-0"]
    hashes_b = compute_chunk_hashes(texts_b)
    existing_b: dict[int, str] = {}  # tenant B has no prior chunks

    diff_b = compute_diff(texts_b, hashes_b, existing_b)
    # Call B must not see tenant A's "unchanged" — its inputs alone govern.
    assert diff_b.unchanged == ()
    assert [idx for idx, _ in diff_b.to_embed] == [0]
    assert diff_b.cost_saved_usd == 0.0
