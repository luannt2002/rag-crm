"""Proposition splitter must not decontextualize conditional/causal clauses.

Root cause (P2-B 🐛-D): ``_chunk_proposition`` split at a non-capturing
group that CONSUMED subordinating connectors (nếu/khi/vì/mà/do đó…), so
"A, nếu B" became two propositions "A" and "B" with the condition severed —
turning a conditional fact into a false unconditional claim (e.g. "Khách
hàng được hoàn tiền toàn phần" with the "nếu hủy trước 24h" condition
dropped). That is exactly the L2 conditional-factoid hazard.

Fix: only split at COORDINATING connectors (và/hoặc/nhưng/and/or/but…) that
join independent clauses; never at SUBORDINATING ones, so a conditional or
causal clause stays attached to its main clause as one atomic proposition.
"""

from __future__ import annotations

from ragbot.shared.chunking import _chunk_proposition


def test_conditional_clause_keeps_its_condition() -> None:
    text = (
        "Khách hàng được hoàn tiền toàn phần, nếu hủy lịch trước "
        "hai mươi bốn giờ so với giờ hẹn đã xác nhận."
    )
    chunks = _chunk_proposition(text, chunk_size=1024, chunk_overlap=0)
    joined = " ".join(chunks)
    assert "nếu" in joined, (
        "the conditional connector must survive — otherwise the refund fact "
        "is decontextualized into an unconditional (false) claim"
    )
    # The condition and its consequent must live in the SAME proposition.
    assert any("hoàn tiền" in c and "nếu" in c for c in chunks), (
        f"condition severed across propositions: {chunks}"
    )


def test_causal_clause_keeps_its_cause() -> None:
    text = (
        "Hệ thống tạm khóa tài khoản, vì phát hiện đăng nhập bất thường "
        "từ nhiều địa điểm khác nhau trong thời gian ngắn."
    )
    chunks = _chunk_proposition(text, chunk_size=1024, chunk_overlap=0)
    assert any("vì" in c and "tạm khóa" in c for c in chunks), (
        f"causal clause severed from its effect: {chunks}"
    )


def test_coordinating_connector_still_splits() -> None:
    """Guard against over-fixing: independent clauses joined by 'và' should
    still split into separate propositions (that is the chunker's job)."""
    text = (
        "Spa mở cửa từ tám giờ sáng đến chín giờ tối tất cả các ngày trong "
        "tuần, và khách hàng có thể đặt lịch trực tuyến qua ứng dụng di động."
    )
    chunks = _chunk_proposition(text, chunk_size=1024, chunk_overlap=0)
    assert len(chunks) >= 1
    # The split point ('và') joins two independent facts; the combined text
    # must still carry both (no data loss), regardless of grouping.
    joined = " ".join(chunks)
    assert "mở cửa" in joined and "đặt lịch" in joined


def test_short_clause_is_not_silently_dropped() -> None:
    """A short coordinated clause carrying a fact must survive (no data loss).

    Root cause (ING-04): clauses shorter than ``DEFAULT_CHUNK_MIN_CLAUSE_LEN``
    were appended-or-dropped — a sentence mixing a long clause with a short
    factual one (e.g. a dimension ``rộng 2m``) silently lost the short clause,
    because the whole-text fallback only fires when EVERY clause is short. A
    dropped number is exactly the silent-data-loss hazard the platform treats
    as sacred. Fix: merge tiny fragments into the adjacent proposition instead
    of discarding them.
    """
    text = "Sản phẩm có chiều dài tối đa năm mét cho khoang chính; rộng 2m"
    chunks = _chunk_proposition(text, chunk_size=1024, chunk_overlap=0)
    joined = " ".join(chunks)
    assert "rộng 2m" in joined, (
        f"short factual clause silently dropped (data loss): {chunks}"
    )
    # The long clause must also survive — the merge must not swallow it.
    assert "chiều dài tối đa năm mét" in joined, (
        f"long clause lost during merge: {chunks}"
    )


def test_leading_short_clause_survives() -> None:
    """A short clause BEFORE a long one must also survive (buffer-then-attach)."""
    text = "Có sẵn; sản phẩm được giao trong vòng ba ngày làm việc kể từ khi đặt"
    chunks = _chunk_proposition(text, chunk_size=1024, chunk_overlap=0)
    joined = " ".join(chunks)
    assert "Có sẵn" in joined, f"leading short clause dropped: {chunks}"
    assert "ba ngày làm việc" in joined, f"long clause lost: {chunks}"
