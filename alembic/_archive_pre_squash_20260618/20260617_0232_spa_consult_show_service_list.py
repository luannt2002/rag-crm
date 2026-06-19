"""spa: on consultation intent, proactively LIST services instead of asking back.

QC behaviour: "tôi cần tư vấn" / "spa có bao nhiêu dịch vụ" → bot replied with a
back-question ("anh/chị quan tâm dịch vụ nào?") instead of showing the menu.
Bot owners want: any consultation/browse intent → show the service list from the
corpus so the customer can pick.

Append-only (idempotent via marker), grounded to <documents> (HALLU=0 — only
services present in the corpus may be listed; no fabrication). Behaviour lever is
the owner's system_prompt (Quality Gate #10 — no app-side injection/override).
"""
from alembic import op

revision = "0232"
down_revision = "0231"
branch_labels = None
depends_on = None

_BOT = "test-spa-id"
_MARK = "TƯ VẤN → HIỆN DANH SÁCH"
_RULE = (
    "\n\n═══ TƯ VẤN → HIỆN DANH SÁCH (chủ động, không hỏi ngược) ═══\n"
    "Khi khách thể hiện ý định tư vấn / muốn xem dịch vụ mà CHƯA nêu dịch vụ cụ "
    "thể — ví dụ \"tôi cần tư vấn\", \"tư vấn cho mình\", \"có dịch vụ gì\", "
    "\"spa có bao nhiêu dịch vụ\", \"cho xem dịch vụ\", \"bên mình có gì\" — thì "
    "TUYỆT ĐỐI KHÔNG hỏi ngược lại kiểu \"anh/chị quan tâm dịch vụ nào\". Thay "
    "vào đó, LIỆT KÊ NGAY danh sách các dịch vụ CÓ trong <documents> (mỗi dòng: "
    "tên dịch vụ + giá ưu đãi nếu tài liệu có), rồi mời khách chọn 1 dịch vụ để "
    "tư vấn sâu + đặt lịch. CHỈ liệt kê dịch vụ xuất hiện trong <documents>, "
    "KHÔNG bịa thêm dịch vụ ngoài tài liệu (HALLU=0). Nếu <documents> chỉ chứa "
    "một phần danh mục, liệt kê đúng phần đang có và mời khách hỏi thêm dịch vụ "
    "cụ thể.\n"
)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = system_prompt || {op.inline_literal(_RULE)}
        WHERE bot_id = {op.inline_literal(_BOT)}
          AND position({op.inline_literal(_MARK)} in system_prompt) = 0
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = replace(system_prompt, {op.inline_literal(_RULE)}, '')
        WHERE bot_id = {op.inline_literal(_BOT)}
        """
    )
