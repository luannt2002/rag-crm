"""Sysprompt: allow grounded computation/derivation (luat + test-spa).

Revision: 0193
Prev:     0192

Deepdive root-cause (2026-06-09): legal/spa bots fail aggregation/comparison NOT
because gpt-4.1-mini is weak, but because their own sysprompt forbids legitimate
reasoning. The shared line "DÙNG kết quả đó, KHÔNG tự tính lại" made the bot list
two grounded fines (Coverage 1.0) yet refuse to SUM them (q09 corr=0), and the
spa bot REFUSE a set-difference it could derive (q06). Summing/comparing numbers
ALREADY IN <documents> is valid arithmetic, not fabrication — the HALLU=0 rule
was over-broad (conflated "derive from grounded" with "fabricate ungrounded").

This replaces the anti-compute clause with an explicit ALLOW for computing /
comparing / aggregating numbers & lists that are already grounded in the
documents, while keeping the ban on substituting/fabricating numbers NOT in the
documents. The specific anti-fabricate rules (no dividing combo price, no
cross-service price merge) are left untouched.

Per-bot sysprompt edit via alembic (DB content, no-psql). Reversible. A/B-gated
(load test must show Coverage up AND HALLU=0 held before keeping).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0193"
down_revision: str | None = "0192"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOTS = ("luat-giao-thong", "test-spa-id")

_OLD = (
    "DÙNG kết quả đó, KHÔNG tự tính lại. TUYỆT ĐỐI KHÔNG thay bằng số từ trí nhớ;"
)
_NEW = (
    "DÙNG kết quả đó. ĐƯỢC PHÉP cộng/trừ/so sánh/tổng hợp các con số hoặc danh "
    "sách ĐÃ CÓ trong tài liệu để trả câu hỏi tổng hợp (vd: cộng các mức phạt đã "
    "nêu thành tổng; liệt kê dịch vụ gói A không có ở gói B) — đó là tính toán "
    "hợp lệ trên dữ kiện grounded, KHÔNG phải bịa. CHỈ KHÔNG được thay/bịa số "
    "KHÔNG có trong tài liệu (từ trí nhớ);"
)


def upgrade() -> None:
    op.execute(
        text(
            "UPDATE bots SET system_prompt = REPLACE(system_prompt, :old, :new), "
            "updated_at = NOW() WHERE bot_id = ANY(:bots)"
        ).bindparams(old=_OLD, new=_NEW, bots=list(_BOTS))
    )


def downgrade() -> None:
    op.execute(
        text(
            "UPDATE bots SET system_prompt = REPLACE(system_prompt, :new, :old), "
            "updated_at = NOW() WHERE bot_id = ANY(:bots)"
        ).bindparams(old=_OLD, new=_NEW, bots=list(_BOTS))
    )
