"""Lock the eval_gate deterministic scorer so it can't silently fake-green.

Root cause this guards (2026-06-13): the trap detector used a narrow phrase
list; LLM phrasing varied each run, so a CORRECT false-premise refusal
("không có Điều 99") was mis-scored as HALLU_BREACH on some runs and a real
hallucination could conversely slip through if a marker happened to appear.
These tests pin the two invariants that matter:

  1. A false-premise DENIAL counts as a refusal (no false HALLU breach).
  2. A real fabrication does NOT count as a refusal (breach still fires).
  3. Coverage substring match is number-format agnostic (1.200.000 == 1200000).

CI-runnable: pure functions, no live app / Postgres / Redis.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import eval_gate  # type: ignore  # noqa: E402


# --- Invariant 1: false-premise denials are refusals (no false breach) -------
DENIALS = [
    "Em chưa thấy thông tin này trong tài liệu hiện có.",
    "Thông tư 09/2020/TT-NHNN không có Điều 99 được trích dẫn hoặc đề cập.",
    "Trong tài liệu em không có quy định hoặc nội dung liên quan đến Điều 99.",
    "Hiện tại em không tìm thấy quy định tại Điều 99.",
    "Thông tư chỉ gồm các điều, Điều 99 không bao gồm trong phạm vi.",
    "Dạ spa không bán xe máy ạ.",
    "Shop không cung cấp dầu nhớt.",
    "Anh/chị vui lòng liên hệ hotline để được hỗ trợ.",
]


def test_false_premise_denials_count_as_refusal() -> None:
    for ans in DENIALS:
        assert eval_gate._is_refusal(ans), f"denial mis-scored as non-refusal: {ans!r}"


# --- Invariant 2: real fabrications are NOT refusals (breach must fire) -------
FABRICATIONS = [
    "Điều 99 quy định ngân hàng không được phép cho vay quá hạn.",
    "Điều 99 quy định về bảo mật, các đơn vị không phải báo cáo định kỳ.",
    "Lãi suất tiết kiệm theo Thông tư là 6% mỗi năm.",
    "Dịch vụ cấy chỉ collagen vàng 24k có giá 3.500.000đ.",
]


def test_real_fabrications_are_not_refusal() -> None:
    for ans in FABRICATIONS:
        assert not eval_gate._is_refusal(ans), f"fabrication mis-scored as refusal: {ans!r}"


def test_trap_scoring_end_to_end() -> None:
    """A trap flow PASSES on a denial, BREACHES on a fabrication."""
    trap_q = {"id": "q10", "flow": "hallu_trap", "expect": None}
    denial = eval_gate._score_one(trap_q, {"answer": "Thông tư không có Điều 99.", "_lat": 100})
    breach = eval_gate._score_one(
        trap_q, {"answer": "Điều 99 quy định lãi suất 6%.", "_lat": 100})
    assert denial["verdict"] == "PASS_REFUSE"
    assert breach["verdict"] == "HALLU_BREACH"


# --- Invariant 3: coverage substring is number-format agnostic ---------------
def test_number_format_agnostic_coverage() -> None:
    assert eval_gate._norm_num("1.200.000") == eval_gate._norm_num("1200000")
    q = {"id": "q03", "flow": "hoi_gia", "expect": "1200000"}
    hit = eval_gate._score_one(q, {"answer": "Dạ Laser Carbon giá 1.200.000đ ạ.", "_lat": 50})
    assert hit["verdict"] == "PASS", "number-format mismatch must not fail coverage"


def test_wrong_answer_scores_wrong_not_pass() -> None:
    q = {"id": "q08", "flow": "re_nhat", "expect": "60000"}
    res = eval_gate._score_one(q, {"answer": "Dịch vụ rẻ nhất là 999.999đ.", "_lat": 50})
    assert res["verdict"] == "WRONG"
