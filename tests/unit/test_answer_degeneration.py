"""Deterministic degeneration / repetition detector (bug#8).

Root cause (QA #8, 2026-07): the answer LLM collapsed into repeating the same
phrase ("công ty bảo hiểm xã hội…") hundreds of times — an unusable answer that
NO guard caught (grep repetition|degenerat|ngram in src = 0). This is the
model-independent, domain-neutral signal: word/phrase repetition ratios, no
vocabulary, no LLM call. Short answers are never flagged.
"""
from __future__ import annotations

from ragbot.shared.degeneration import classify_answer_degeneration


# --- degenerate cases (must flag) -----------------------------------------

def test_phrase_loop_bug8_verbatim() -> None:
    """The bug#8 class: a short phrase repeated many times."""
    ans = "công ty bảo hiểm xã hội " * 120
    r = classify_answer_degeneration(ans)
    assert r["is_degenerate"] is True
    assert r["n_words"] > 100


def test_single_token_loop() -> None:
    r = classify_answer_degeneration("có " * 60)
    assert r["is_degenerate"] is True


def test_sentence_loop_unique_words() -> None:
    """A whole sentence of distinct words repeated — caught by the trigram
    signal even when single-word frequency stays moderate."""
    sent = "khách hàng vui lòng liên hệ tổng đài để được hỗ trợ nhanh nhất "
    r = classify_answer_degeneration(sent * 8)
    assert r["is_degenerate"] is True


# --- normal cases (must NOT flag) -----------------------------------------

def test_short_answer_never_flagged() -> None:
    r = classify_answer_degeneration("Dạ, giá sản phẩm là 1.500.000đ ạ.")
    assert r["is_degenerate"] is False


def test_normal_prose_not_flagged() -> None:
    ans = (
        "Dạ, bên em có ba dòng lốp phù hợp với xe của anh. Dòng đầu tiên bền bỉ "
        "và êm ái, phù hợp đi phố. Dòng thứ hai bám đường tốt hơn khi trời mưa. "
        "Dòng cuối cùng tiết kiệm nhiên liệu và giá hợp lý nhất trong ba lựa chọn."
    )
    r = classify_answer_degeneration(ans)
    assert r["is_degenerate"] is False


def test_structured_listing_not_flagged() -> None:
    """A real listing repeats scaffolding ('- … giá …') but with distinct
    content per line — must NOT be flagged (recall guard)."""
    ans = (
        "Dạ bên em có các sản phẩm sau ạ:\n"
        "- Lốp Michelin 205/55R16 giá 1.800.000đ\n"
        "- Lốp Bridgestone 205/55R16 giá 1.650.000đ\n"
        "- Lốp Goodyear 205/55R16 giá 1.720.000đ\n"
        "- Lốp Continental 205/55R16 giá 1.900.000đ\n"
        "Anh cần em tư vấn thêm dòng nào không ạ?"
    )
    r = classify_answer_degeneration(ans)
    assert r["is_degenerate"] is False


def test_empty_answer_safe() -> None:
    assert classify_answer_degeneration("")["is_degenerate"] is False
    assert classify_answer_degeneration("   ")["is_degenerate"] is False


def test_return_shape() -> None:
    r = classify_answer_degeneration("có " * 50)
    for k in ("is_degenerate", "n_words", "distinct_word_ratio",
              "top_token_ratio", "distinct_trigram_ratio"):
        assert k in r


# --- markdown structure must NOT flag (0.5 tokenizer) ---------------------
# A markdown table's `|`/`---`/`*` scaffolding is not prose — counted as words
# it masquerades as a repeated token and trips the ratios on a perfectly good
# table. The fix needs BOTH a tokenizer that discards structural punctuation AND
# dropping the single-token-ratio clause; the next two cases discriminate: one
# stays flagged if we only strip, the other if we only drop the ratio.

def _availability_matrix() -> str:
    """60-branch × 5-slot availability grid, ~80% 'Có' — legitimate structured
    content whose distinct-word ratio collapses to ~0.09 under a naive split
    (the pipes dominate). Stays flagged under drop-ttr-only (dwr ≤ 0.15) → this
    case proves the tokenizer strip is required."""
    rows = "".join(
        "| CN{:02d} | {} |\n".format(
            i, " | ".join("Có" if (i + k) % 3 else "Không" for k in range(5))
        )
        for i in range(1, 61)
    )
    return (
        "| Chi nhánh | Sáng | Trưa | Chiều | Tối | Đêm |\n"
        "| --- | --- | --- | --- | --- | --- |\n" + rows
    )


def test_markdown_pipe_table_not_flagged() -> None:
    """A plain markdown spec table (pipes + one repeated header word)."""
    ans = (
        "| Thông số | Giá trị |\n| --- | --- |\n"
        "| Chiều rộng | 205 mm |\n| Chiều cao | 55 phần trăm |\n"
        "| Đường kính | 16 inch |\n| Tải trọng | 91 V |\n"
        "| Áp suất | 2.5 bar |\n| Bảo hành | 6 năm |\n"
        "| Xuất xứ | Nhật Bản |\n| Trọng lượng | 8 kg |\n"
        "| Model | XM2 |\n| Gai | đối xứng |\n"
    )
    assert classify_answer_degeneration(ans)["is_degenerate"] is False


def test_feature_matrix_not_flagged() -> None:
    """Feature grid where one real word ('Có') is ~43% of tokens even AFTER the
    pipes are stripped — stays flagged under strip-only → proves the
    single-token-ratio clause must be dropped."""
    ans = (
        "| Tính năng | Gói A | Gói B |\n| --- | --- | --- |\n"
        "| Sao lưu | Có | Có |\n| Mã hóa | Có | Có |\n"
        "| API | Có | Có |\n| SSO | Có | Không |\n"
        "| Webhook | Có | Có |\n| Báo cáo | Có | Có |\n"
        "| Xuất Excel | Có | Có |\n| Tích hợp | Có | Có |\n"
    )
    assert classify_answer_degeneration(ans)["is_degenerate"] is False


def test_long_pipe_table_not_flagged() -> None:
    """Large availability matrix — flagged under drop-ttr-only, proves strip."""
    assert classify_answer_degeneration(_availability_matrix())["is_degenerate"] is False


def test_dropping_top_token_ratio_keeps_bug8_recall() -> None:
    """Removing the top-token-ratio clause must not lose the real loop: bug#8's
    top_token_ratio was 0.167 (never fired the clause) — the word/trigram
    signals still catch it."""
    r = classify_answer_degeneration("công ty bảo hiểm xã hội " * 120)
    assert r["is_degenerate"] is True
    assert r["top_token_ratio"] < 0.40  # the dropped clause never caught it anyway


# --- guard_output wiring (owner-gated, default observe) --------------------

def test_guard_output_degeneration_is_owner_gated() -> None:
    """The gate detects deterministically but only SUBSTITUTES under the owner
    opt-in ``degeneration_action == "block"``; the default observe ships the
    answer untouched (sacred #10), and a block uses the owner's template."""
    import inspect

    from ragbot.orchestration.nodes import guard_output

    src = inspect.getsource(guard_output)
    assert "classify_answer_degeneration" in src
    # block gated on the owner opt-in action, never unconditional
    assert "DEGENERATION_ACTION_BLOCK" in src
    assert "DEFAULT_DEGENERATION_ACTION" in src
    # substituted text is the owner's template, never an app-injected literal
    assert "_resolved_oos_template(state)" in src
    # action-neutral event carries the real action + blocked (fix#4 discipline)
    assert "action=_dg_action" in src
    assert "blocked=_dg_will_block" in src


def test_degeneration_action_wired_into_pipeline_config_builders() -> None:
    """Owner opt-in is reachable: both the HTTP and worker pipeline-config
    builders must resolve ``degeneration_action`` per-bot (else a plan_limits
    override never reaches guard_output)."""
    import inspect

    from ragbot.interfaces.http.routes.test_chat import _pipeline_config as http_cfg
    from ragbot.interfaces.workers.chat_worker import pipeline_config as wrk_cfg

    for mod in (http_cfg, wrk_cfg):
        src = inspect.getsource(mod)
        assert '"degeneration_action"' in src, f"{mod.__name__} missing key"
        assert "DEFAULT_DEGENERATION_ACTION" in src
