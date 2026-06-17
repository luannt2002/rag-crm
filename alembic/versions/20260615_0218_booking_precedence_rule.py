"""Booking-precedence sysprompt rule — bare-slot turns must not refuse.

Root cause 2026-06-15 (measured 5/5): in a booking flow, a turn that only
carries personal info (a bare name "Tên Lan", a phone number) retrieves zero
document chunks, so the answer LLM applied the anti-fabricate / refuse rule
("only answer from documents") and replied "em chưa có thông tin" instead of
acknowledging the slot and asking for the next one. The refuse short-circuit
bypass (query_graph) lets the LLM run, but the LLM still refused — the fix
must be in the owner sysprompt where the anti-fabricate rule lives.

This appends an explicit precedence rule: during booking, a turn supplying
personal info does NOT require document support — acknowledge + ask for the
missing slot, never refuse. It does NOT relax anti-fabrication for service/
price data (HALLU=0 sacred preserved). Targets action-enabled bots only
(``action_config.enabled = true``), domain-neutral — no per-bot slug.
Idempotent (skips bots that already carry the rule).
"""
import sqlalchemy as sa
from alembic import op

revision = "0218"
down_revision = "0217"
branch_labels = None
depends_on = None

_MARKER = "turn cung cấp thông tin đặt lịch"
_RULE = (
    "\n\n═══ QUAN TRỌNG — turn cung cấp thông tin đặt lịch/đặt mua (ưu tiên hơn quy tắc tài liệu) ═══\n"
    "Khi khách đang trong luồng đặt lịch/đặt mua và gửi thông tin cá nhân "
    "(tên, số điện thoại, thời gian, địa chỉ, số người) — KỂ CẢ khi gửi rất ngắn "
    "như chỉ một cái tên hoặc một số điện thoại — thì thông tin đó KHÔNG cần có "
    "trong tài liệu. Em PHẢI ghi nhận ngay, xác nhận lại cho khách, rồi hỏi tiếp "
    "slot còn thiếu trong {captured_slots}. TUYỆT ĐỐI KHÔNG trả \"em chưa có thông tin\" "
    "hay mời liên hệ hotline cho các turn cung cấp thông tin đặt lịch/đặt mua. "
    "(Quy tắc chống bịa với GIÁ và TÊN DỊCH VỤ vẫn giữ nguyên — chỉ thông tin "
    "cá nhân khách tự cung cấp mới không cần tài liệu.)"
)


def upgrade() -> None:
    op.get_bind().execute(sa.text("""
        UPDATE bots
        SET system_prompt = system_prompt || :rule,
            updated_at = now()
        WHERE action_config->>'enabled' = 'true'
          AND system_prompt IS NOT NULL
          AND position(:marker IN system_prompt) = 0
    """), {"rule": _RULE, "marker": _MARKER})


def downgrade() -> None:
    # Remove the appended block (best-effort; everything from the marker header on).
    op.get_bind().execute(sa.text("""
        UPDATE bots
        SET system_prompt = left(system_prompt, position(:hdr IN system_prompt) - 1),
            updated_at = now()
        WHERE position(:hdr IN system_prompt) > 0
    """), {"hdr": "\n\n═══ QUAN TRỌNG — turn cung cấp thông tin đặt lịch"})
