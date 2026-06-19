"""spa: make the consultation-list rule HALLU-safe (kill the auto-number runaway).

0232 added "consultation intent → list services". Multi-turn test exposed a HALLU
breach: with only 1 weak chunk retrieved, the LLM auto-completed a fabricated list
("Chăm sóc da mặt nâng cao 1, 2, 3 ... 37") — services that do NOT exist. The
"list services" instruction without a hard anti-runaway guard turns a retrieval
gap into fabrication, violating HALLU=0 (sacred).

Fix: replace the rule with a version that (a) only lists service names appearing
VERBATIM in <documents>, (b) forbids auto-numbering / inventing / repeating names,
(c) when few/no services are retrieved, asks the customer which GROUP they care
about (da / mụn / triệt lông …) instead of fabricating a list. Behaviour lever is
the owner's system_prompt (Quality Gate #10).
"""
from alembic import op

revision = "0233"
down_revision = "0232"
branch_labels = None
depends_on = None

_BOT = "test-spa-id"

# Exact text seeded by 0232 (must match to replace).
_OLD = (
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

_NEW = (
    "\n\n═══ TƯ VẤN → HIỆN DANH SÁCH (chủ động, nhưng HALLU=0) ═══\n"
    "Khi khách thể hiện ý định tư vấn / muốn xem dịch vụ mà CHƯA nêu dịch vụ cụ "
    "thể (vd \"tôi cần tư vấn\", \"có dịch vụ gì\", \"spa có bao nhiêu dịch vụ\", "
    "\"cho xem dịch vụ\"): liệt kê các dịch vụ — NHƯNG tuân thủ tuyệt đối:\n"
    "1) CHỈ liệt kê tên dịch vụ XUẤT HIỆN NGUYÊN VĂN trong <documents>. Sao chép "
    "đúng tên, KHÔNG diễn giải lại.\n"
    "2) TUYỆT ĐỐI KHÔNG tự đánh số thứ tự dịch vụ (1, 2, 3 …), KHÔNG tự nối thêm "
    "hậu tố như \"nâng cao 1/2/3\", KHÔNG lặp tên, KHÔNG suy diễn hay \"hoàn "
    "thành\" danh sách. Mỗi dịch vụ chỉ xuất hiện ĐÚNG MỘT LẦN.\n"
    "3) Nếu <documents> chỉ có vài dịch vụ → chỉ liệt kê đúng vài cái đó. Nếu "
    "KHÔNG thấy dịch vụ nào rõ ràng trong <documents> → KHÔNG bịa danh sách; thay "
    "vào đó hỏi khách quan tâm nhóm nào (chăm sóc da / trị mụn / trẻ hóa / triệt "
    "lông / massage …) để em tra đúng.\n"
    "4) Sau khi liệt kê, mời khách chọn 1 dịch vụ để tư vấn sâu + đặt lịch.\n"
)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = replace(system_prompt, {op.inline_literal(_OLD)}, {op.inline_literal(_NEW)})
        WHERE bot_id = {op.inline_literal(_BOT)}
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bots
        SET system_prompt = replace(system_prompt, {op.inline_literal(_NEW)}, {op.inline_literal(_OLD)})
        WHERE bot_id = {op.inline_literal(_BOT)}
        """
    )
