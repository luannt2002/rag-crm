"""Append an ANTI-INVENT-VARIANT rule to ``language_packs[vi|en][sysprompt_default_rules]``.

Trigger (Phase 3 HALLU, live xe-bot 2026-07-01): asked "how many types of X"
in a multi-turn context, the LLM listed the one real variant (G/P) AND invented
a second, non-existent one ("155/80R13 H/P 725.000đ còn 187") — the synthetic
stats chunk contained ONLY the real G/P rows (no H/P, no 725000 anywhere). The
existing ``# ANTI-FABRICATE`` rule forbids inventing a link/number/value, but
does NOT forbid inventing an extra *variant/model/type* when the question primes
a list ("how many types / list all"). This is the list-pressure fabrication gap.

APPENDS a ``# ANTI-INVENT-VARIANT`` section to the existing
``sysprompt_default_rules`` content for each locale — APPEND-ONLY (concatenated
at the END, never prepended / inserted), so the owner ``bots.system_prompt``
stays authoritative at assembly (``SysPromptAssembler``).

Sacred-rule alignment (ADR-W1-S10 governed append-only exception):
  ✅ Text seed via tracked alembic (rule 7 — never psql).
  ✅ Domain-neutral: speaks of "type / variant / model / version" generically —
     no brand / service / grade / price literal.
  ✅ APPEND-only: concatenated at the END of the existing content.
  ✅ Per-bot opt-out: assembler strips rules in
     ``bots.plan_limits.sysprompt_rules_disabled``.
  ✅ Idempotent: re-running is a no-op (guarded by the section marker).
  ✅ Reversible: downgrade removes exactly the appended block.

NOT an answer-override (sacred-rule 10): adds an instruction the LLM self-applies;
the application never edits the LLM answer.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "seed_anti_variant_260701"
down_revision = "canon_default_model_260630"
branch_labels = None
depends_on = None


_PROMPT_KEY = "sysprompt_default_rules"

# Section marker: presence in the stored content = block already appended →
# idempotent guard + precise downgrade boundary.
_MARKER_EN = "# ANTI-INVENT-VARIANT"
_MARKER_VI = "# CHỐNG BỊA BIẾN THỂ"

# Appended block per locale. Leading blank line separates it from the prior
# section. Domain-neutral: "type / variant / model / version", no brand/grade.
_BLOCK_EN = (
    "\n\n# ANTI-INVENT-VARIANT\n"
    "When listing the types/variants/models/versions of a product or service, "
    "list ONLY the items that appear EXPLICITLY in the context. If only one "
    "exists, say exactly one — never add another type/variant/model that is not "
    "in the context, and never fabricate a price/quantity/attribute for an item "
    "that is not present."
)
_BLOCK_VI = (
    "\n\n# CHỐNG BỊA BIẾN THỂ\n"
    "Khi liệt kê các loại/biến thể/model/phiên bản của một sản phẩm hoặc dịch "
    "vụ, CHỈ liệt kê những mục XUẤT HIỆN TƯỜNG MINH trong ngữ cảnh. Nếu chỉ có "
    "một mục thì nói đúng một mục — TUYỆT ĐỐI KHÔNG tự thêm một loại/biến "
    "thể/model khác không có trong ngữ cảnh, và KHÔNG bịa giá/số lượng/thuộc "
    "tính cho mục không tồn tại."
)

_APPENDS = (
    ("en", _MARKER_EN, _BLOCK_EN),
    ("vi", _MARKER_VI, _BLOCK_VI),
)


def upgrade() -> None:
    """Append the ANTI-INVENT-VARIANT block to each locale's default rules.

    Idempotent: skips a locale whose content already contains the marker so a
    re-run (or a partial prior run) cannot double-append.
    """
    conn = op.get_bind()
    for code, marker, block in _APPENDS:
        conn.execute(
            text(
                """
                UPDATE language_packs
                SET content = content || :block
                WHERE code = :code
                  AND prompt_key = :key
                  AND content NOT LIKE '%' || :marker || '%'
                """,
            ),
            {"block": block, "code": code, "key": _PROMPT_KEY, "marker": marker},
        )


def downgrade() -> None:
    """Strip the appended block from each locale's default rules (byte-exact)."""
    conn = op.get_bind()
    for code, _marker, block in _APPENDS:
        conn.execute(
            text(
                """
                UPDATE language_packs
                SET content = left(content, position(:block in content) - 1)
                WHERE code = :code
                  AND prompt_key = :key
                  AND position(:block in content) > 0
                """,
            ),
            {"code": code, "key": _PROMPT_KEY, "block": block},
        )
