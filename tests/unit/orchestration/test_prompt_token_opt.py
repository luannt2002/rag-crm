"""Unit tests for prompt-token squeeze helpers (Phase B B2)."""

from __future__ import annotations

from ragbot.shared.constants import (
    DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD,
    DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY,
    DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE,
    INTENT_FACTOID,
)
from ragbot.shared.prompt_token_opt import (
    apply_token_opt,
    dedupe_chunks,
    filter_min_score,
    should_skip_history,
)


# ─── filter_min_score ────────────────────────────────────────────────────────


class TestFilterMinScore:
    def test_empty_input_returns_empty(self):
        out, dropped = filter_min_score([], min_score=0.5)
        assert out == []
        assert dropped == 0

    def test_min_score_zero_disabled(self):
        chunks = [{"text": "x", "score": 0.05}, {"text": "y", "score": 0.10}]
        out, dropped = filter_min_score(chunks, min_score=0.0)
        # min_score=0 acts as "disabled" — return chunks untouched.
        assert out == chunks
        assert dropped == 0

    def test_drops_low_score(self):
        chunks = [
            {"text": "good", "score": 0.8},
            {"text": "bad", "score": 0.05},
            {"text": "ok", "score": 0.3},
        ]
        out, dropped = filter_min_score(chunks, min_score=0.2)
        assert len(out) == 2
        assert dropped == 1
        assert all(c["text"] != "bad" for c in out)

    def test_keep_at_least_one_when_all_below(self):
        # Even with all scores below threshold, the best one survives.
        chunks = [
            {"text": "best", "score": 0.15},
            {"text": "mid", "score": 0.08},
            {"text": "worst", "score": 0.02},
        ]
        out, dropped = filter_min_score(chunks, min_score=0.5)
        assert len(out) == 1
        assert out[0]["text"] == "best"
        assert dropped == 2

    def test_keep_at_least_one_disabled_drops_all(self):
        chunks = [{"text": "a", "score": 0.05}]
        out, dropped = filter_min_score(chunks, min_score=0.5, keep_at_least_one=False)
        assert out == []
        assert dropped == 1

    def test_missing_score_treated_as_zero(self):
        chunks = [{"text": "a"}, {"text": "b", "score": 0.9}]
        out, dropped = filter_min_score(chunks, min_score=0.5)
        # 'a' has no score → 0.0 → dropped; 'b' survives.
        assert len(out) == 1
        assert out[0]["text"] == "b"
        assert dropped == 1


# ─── dedupe_chunks ───────────────────────────────────────────────────────────


class TestDedupeChunks:
    def test_empty_input(self):
        out, dropped = dedupe_chunks([])
        assert out == []
        assert dropped == 0

    def test_disabled_thresholds(self):
        chunks = [{"text": "same"}, {"text": "same"}]
        # Threshold >= 1.0 effectively disables (only equal sets would dedupe at 1.0 strict)
        out, dropped = dedupe_chunks(chunks, jaccard_threshold=1.0)
        assert out == chunks
        assert dropped == 0
        out, dropped = dedupe_chunks(chunks, jaccard_threshold=0.0)
        assert out == chunks
        assert dropped == 0

    def test_drops_exact_duplicate(self):
        chunks = [
            {"chunk_id": "1", "text": "Giá sản phẩm A là 500 nghìn đồng."},
            {"chunk_id": "2", "text": "Giá sản phẩm A là 500 nghìn đồng."},
            {"chunk_id": "3", "text": "Một câu hoàn toàn khác về sản phẩm B."},
        ]
        out, dropped = dedupe_chunks(chunks, jaccard_threshold=0.85)
        assert dropped == 1
        ids = [c["chunk_id"] for c in out]
        assert "1" in ids  # first kept
        assert "2" not in ids  # duplicate dropped
        assert "3" in ids  # distinct survived

    def test_preserves_distinct_chunks(self):
        chunks = [
            {"chunk_id": "1", "text": "Chính sách bảo hành 12 tháng cho điện thoại."},
            {"chunk_id": "2", "text": "Chính sách đổi trả 7 ngày cho phụ kiện."},
            {"chunk_id": "3", "text": "Hỗ trợ kỹ thuật miễn phí qua hotline."},
        ]
        out, dropped = dedupe_chunks(chunks, jaccard_threshold=0.85)
        assert dropped == 0
        assert len(out) == 3

    def test_first_occurrence_kept(self):
        # Order matters: ranked chunks come first, dedupe preserves first.
        chunks = [
            {"chunk_id": "high_rank", "text": "ABCDEFGHIJKLMNOP", "score": 0.9},
            {"chunk_id": "low_rank", "text": "ABCDEFGHIJKLMNOP", "score": 0.1},
        ]
        out, _ = dedupe_chunks(chunks, jaccard_threshold=0.85)
        assert len(out) == 1
        assert out[0]["chunk_id"] == "high_rank"

    def test_empty_text_chunks_preserved(self):
        chunks = [{"chunk_id": "1", "text": ""}, {"chunk_id": "2", "text": ""}]
        out, dropped = dedupe_chunks(chunks, jaccard_threshold=0.85)
        # Empty-text chunks have empty n-gram sets; loop should not equate them as dupes.
        assert len(out) == 2
        assert dropped == 0


# ─── should_skip_history ─────────────────────────────────────────────────────


class TestShouldSkipHistory:
    def test_factoid_skips_when_enabled(self):
        assert should_skip_history(INTENT_FACTOID, factoid_skip=True) is True

    def test_factoid_kept_when_flag_off(self):
        assert should_skip_history(INTENT_FACTOID, factoid_skip=False) is False

    def test_non_factoid_keeps_history(self):
        for intent in ("multi_hop", "comparison", "definition", "chitchat", ""):
            assert should_skip_history(intent, factoid_skip=True) is False

    def test_none_intent_keeps_history(self):
        assert should_skip_history(None, factoid_skip=True) is False


# ─── apply_token_opt facade ──────────────────────────────────────────────────


class TestApplyTokenOpt:
    def test_disabled_passthrough(self):
        chunks = [{"text": "x", "score": 0.05}, {"text": "x", "score": 0.05}]
        out, skip, metrics = apply_token_opt(
            chunks,
            intent=INTENT_FACTOID,
            enabled=False,
        )
        assert out == chunks
        assert skip is False
        assert metrics == {"dropped_by_score": 0, "dropped_by_dedupe": 0}

    def test_enabled_combines_score_dedupe_history(self):
        chunks = [
            {"chunk_id": "a", "text": "Bảo hành 12 tháng cho thiết bị di động.", "score": 0.9},
            {"chunk_id": "b", "text": "Bảo hành 12 tháng cho thiết bị di động.", "score": 0.85},
            {"chunk_id": "c", "text": "Một chủ đề hoàn toàn khác biệt.", "score": 0.05},
        ]
        out, skip, metrics = apply_token_opt(
            chunks,
            intent=INTENT_FACTOID,
            enabled=True,
            min_score=0.20,
            dedupe_threshold=0.85,
            factoid_skip_history=True,
        )
        # 'c' dropped by score, 'b' dropped by dedupe → only 'a' survives.
        assert len(out) == 1
        assert out[0]["chunk_id"] == "a"
        assert metrics["dropped_by_score"] == 1
        assert metrics["dropped_by_dedupe"] == 1
        assert skip is True

    def test_non_factoid_intent_keeps_history(self):
        chunks = [{"text": "data", "score": 0.5}]
        _out, skip, _ = apply_token_opt(
            chunks,
            intent="multi_hop",
            enabled=True,
            factoid_skip_history=True,
        )
        assert skip is False

    def test_metrics_zero_when_no_drops(self):
        chunks = [{"text": "unique content here", "score": 0.9}]
        out, _, metrics = apply_token_opt(
            chunks,
            intent="definition",
            enabled=True,
            min_score=0.10,
        )
        assert len(out) == 1
        assert metrics["dropped_by_score"] == 0
        assert metrics["dropped_by_dedupe"] == 0


# ─── constants exposure (regression: don't lose flags) ──────────────────────


class TestConstantsExposed:
    def test_defaults_have_correct_types(self):
        # Feature flag MUST default OFF — caller opts in via system_config.
        assert DEFAULT_PROMPT_TOKEN_OPT_MIN_CHUNK_SCORE == 0.0
        assert isinstance(DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD, float)
        assert 0.0 < DEFAULT_PROMPT_TOKEN_OPT_DEDUPE_JACCARD_THRESHOLD < 1.0
        assert isinstance(DEFAULT_PROMPT_TOKEN_OPT_FACTOID_SKIP_HISTORY, bool)
