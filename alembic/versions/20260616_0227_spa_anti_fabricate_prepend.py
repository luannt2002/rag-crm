"""spa: PREPEND a forceful anti-fabricate-service gate at the top of the prompt.

0225 appended an anti-fabricate rule at the END of the spa sysprompt. Measured
phun-xăm refusal = 2/4 (mini ignores the trailing rule ~50% — LLMs weight
early instructions much more than trailing ones). Per CLAUDE.md sacred #10 the
application MUST NOT override the LLM answer (no block+substitute), so the
bot-owner's sysprompt is the ONLY governed lever — strengthen it by placing a
short, hard gate as the VERY FIRST line (highest attention weight), reinforcing
the 0225 trailing rule from both ends.

Honest scope: this REDUCES the residual HALLU but cannot guarantee 0% on a
probabilistic model + sysprompt-only lever. Append-only / idempotent (skips if
already present) / reversible (downgrade strips the exact prefix).
"""
from alembic import op

revision = "0227"
down_revision = "0226"
branch_labels = None
depends_on = None

_BOT = "test-spa-id"
_MARK = "GATE CHỐNG BỊA DỊCH VỤ"
_GATE = (
    "⛔ GATE CHỐNG BỊA DỊCH VỤ (đọc TRƯỚC TIÊN, ưu tiên cao nhất): Chỉ được xác "
    "nhận \"bên em CÓ dịch vụ X\" khi tên X xuất hiện NGUYÊN VĂN trong <documents>. "
    "Khi khách hỏi \"có dịch vụ X không\" / \"có làm X không\" / \"có X chứ\" mà rà "
    "<documents> KHÔNG thấy tên X → BẮT BUỘC trả: \"Dạ dịch vụ này em chưa thấy "
    "trong danh mục bên em ạ, anh/chị liên hệ hotline để được hỗ trợ thêm ạ.\" "
    "TUYỆT ĐỐI KHÔNG suy đoán \"spa thường có\", KHÔNG xác nhận, KHÔNG mô tả, "
    "KHÔNG báo giá dịch vụ vắng mặt trong tài liệu.\n\n"
)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = {op.inline_literal(_GATE)} || system_prompt
        WHERE bot_id = {op.inline_literal(_BOT)}
          AND position({op.inline_literal(_MARK)} in system_prompt) = 0
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = replace(system_prompt, {op.inline_literal(_GATE)}, '')
        WHERE bot_id = {op.inline_literal(_BOT)}
        """
    )
