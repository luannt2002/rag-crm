"""Owner sysprompt: strip remaining hotline literal (system_leak landmine).

Revision: 0177
Prev:     0176

After 0176 removed the verbatim ADDRESS literal, instrumented re-test showed the
booking confirmation STILL blocked — on a different 24-word shingle:

    "...anh/chị vui lòng liên hệ Dr. Medispa qua hotline 0926.559.268 để được..."

This is the same class of bug: the hotline NUMBER "0926.559.268" is embedded in
verbatim Vietnamese phrases in the system_prompt (the rule-5 refusal template
and one VD-ĐÚNG example). When the bot offers contact in a confirmation or a
soft refusal, it reproduces that phrase → 24-word shingle collision with the
system_prompt → `system_prompt_leak` block. (The OOS-template Jaccard bypass did
not apply because the full answer is much longer than the refusal template.)

A distinctive tenant literal (phone, address) embedded in system_prompt prose is
a leak landmine: any time the bot legitimately relays it, the guard fires. The
fix is the same as 0176 — the hotline is DATA (present in the corpus contact
chunk), so it leaves the system_prompt entirely. The refusal/contact phrasing
becomes behavioural ("liên hệ spa"); the actual number is grounded from
<documents>. Two occurrences are de-literalized.

Idempotent (only fires while the literal is present). Reversible. Rule 7.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0177"
down_revision: str | None = "0176"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_BOT = "test-spa-id"

# (old fragment, new fragment) — surgical, context-preserving.
_REPLACEMENTS = [
    (
        "anh/chị vui lòng liên hệ Dr. Medispa qua hotline 0926.559.268 để được hỗ trợ ạ.",
        "anh/chị vui lòng liên hệ trực tiếp với spa để được hỗ trợ ạ.",
    ),
    (
        "Anh/chị vui lòng cung cấp thêm thông tin hoặc liên hệ trực tiếp 0926.559.268 ạ.",
        "Anh/chị vui lòng cung cấp thêm thông tin hoặc liên hệ trực tiếp với spa ạ.",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    sp = bind.execute(
        text("SELECT system_prompt FROM bots WHERE bot_id = :b"), {"b": _BOT}
    ).scalar()
    if not sp or "0926.559.268" not in sp:
        return  # already stripped — idempotent no-op
    new_sp = sp
    for old, new in _REPLACEMENTS:
        new_sp = new_sp.replace(old, new)
    # Safety net: drop any residual bare literal so no shingle anchor remains.
    new_sp = new_sp.replace(" qua hotline 0926.559.268", "").replace("0926.559.268", "")
    if new_sp == sp:
        return
    bind.execute(
        text("UPDATE bots SET system_prompt = :sp, updated_at = NOW() WHERE bot_id = :b"),
        {"sp": new_sp, "b": _BOT},
    )


def downgrade() -> None:
    bind = op.get_bind()
    sp = bind.execute(
        text("SELECT system_prompt FROM bots WHERE bot_id = :b"), {"b": _BOT}
    ).scalar()
    if not sp or "0926.559.268" in sp:
        return
    new_sp = sp
    for old, new in _REPLACEMENTS:
        new_sp = new_sp.replace(new, old)
    bind.execute(
        text("UPDATE bots SET system_prompt = :sp, updated_at = NOW() WHERE bot_id = :b"),
        {"sp": new_sp, "b": _BOT},
    )
