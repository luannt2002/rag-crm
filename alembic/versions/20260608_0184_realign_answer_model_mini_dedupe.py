"""Realign answer-model bindings to platform mini policy + dedupe seed dups.

Revision: 0184
Prev:     0183

Root cause (investigated 2026-06-08): alembic 0161 (switch_to_gpt_nano, a
rate-limit workaround for a SEQUENTIAL 120Q load test) repointed every bot's
answer-model binding gpt-4.1-mini -> gpt-4.1-nano. Only luat-giao-thong +
y-te-co-ban were reverted (0165/0168). The remaining ~8 demo bots silently ran
on nano, against `system_config.default_answer_model = gpt-4.1-mini`. The
rate-limit reason is obsolete (load tests are parallel now), and nano is too
weak for the answer node (citation + key-fact extraction on hard aggregation /
causal queries — e.g. lich-su-vn 0.58).

Additionally the 2026-05-28 binding seed ran ~4x, leaving 4 duplicate rows per
(bot, purpose, model).

This migration:
  1. Realigns ALL active answer-model bindings (purpose IN llm_primary,
     generation) to gpt-4.1-mini — the platform policy. (0168 noted "a future
     generic pass could realign all bots"; this is that pass.)
  2. De-duplicates exact-duplicate binding rows (same bot+purpose+model),
     keeping one.

Corpus (documents / chunks) is untouched — query-time model change only, no
re-ingest. grading/grounding (gemma) bindings are left as-is (they carry no
system_config default; deleting would disable the CRAG grade + grounding gate).

Sacred-rule: pure alembic DML (rule 7, no psql hot-fix), zero-hardcode (model
resolved by name), reversible-forward (downgrade documented).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0184"
down_revision: str | None = "0183"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

def upgrade() -> None:
    # 1. Realign ALL answer-model bindings → platform mini policy. Covers the
    #    canonical `llm_primary` + `generation`, AND the cost-aware per-intent
    #    overrides the generate node looks up first (`llm_factoid`,
    #    `llm_comparison`, `llm_aggregation`, `llm_multi_hop`, `llm_greeting`,
    #    `llm_chitchat`, `llm_oos`, ...). Without the `llm_%` arm a bot with a
    #    per-intent nano override (e.g. test-spa-id) keeps answering on nano.
    op.execute(
        text(r"""
            UPDATE bot_model_bindings
            SET record_model_id = (
                    SELECT id FROM ai_models
                    WHERE name = 'gpt-4.1-mini' AND deleted_at IS NULL
                    LIMIT 1
                ),
                updated_at = NOW()
            WHERE (purpose LIKE 'llm\_%' OR purpose = 'generation')
              AND active = true
        """)
    )
    # 2. De-duplicate exact-duplicate rows (same bot + purpose + model),
    #    keeping the earliest ctid. Removes the 4x seed re-run dups.
    op.execute(
        text("""
            DELETE FROM bot_model_bindings a
            USING bot_model_bindings b
            WHERE a.record_bot_id = b.record_bot_id
              AND a.purpose = b.purpose
              AND a.record_model_id = b.record_model_id
              AND a.ctid > b.ctid
        """)
    )


def downgrade() -> None:
    # Forward-only realignment to the platform mini policy. The prior state was
    # a drift (nano override from 0161 + 4x seed dups) that is not desirable to
    # restore; downgrade is a no-op. Re-pin a bot to nano via an explicit
    # binding if ever needed.
    pass
