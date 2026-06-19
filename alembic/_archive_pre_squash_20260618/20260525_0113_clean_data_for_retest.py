"""[T3-Refactor] Clean transactional data for fresh re-test.

Revision ID: 0113
Revises: 0112
Create Date: 2026-05-25

Wipes every row in the transactional / observability tables so the bot
can be re-tested from zero. Preserves all CONFIG tables (bots,
language_packs, system_config, ai_providers, ai_models,
bot_model_bindings, tenants).

Operator-triggered cleanup — NOT an automatic data-retention policy.
The downgrade() is intentionally NO-OP: cleaned data is gone forever
(no plausible "restore" path that doesn't violate referential
integrity once chunks / outbox / request_steps are gone).

Tables wiped (in dependency order):
  * request_steps  → child of request_logs
  * request_logs   → audit row per chat turn
  * outbox         → reliable-delivery event queue
  * jobs           → background job state
  * messages       → child of conversations (chat history old schema)
  * chat_histories → flat chat history (test-chat endpoint)
  * semantic_cache → pgvector cosine cache
  * conversations  → multi-turn conversation root
  * document_chunks → pgvector + content
  * documents      → doc metadata + raw_content

Tables PRESERVED:
  * bots (sysprompt + oos_answer_template + plan_limits)
  * bot_model_bindings + ai_providers + ai_models (LLM routing)
  * language_packs (all 24 prompt rows)
  * system_config (per-intent caps + flags + thresholds)
  * tenants + audit_log (forensic compliance)
  * api_tokens (dev tokens still work after restart)

Operator step after upgrade:
    redis-cli -n 1 FLUSHDB
    systemctl restart ragbot-api.service ragbot-document-worker.service \
                      ragbot-outbox.service
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text


logger = logging.getLogger(__name__)

revision: str = "0113"
down_revision: str | None = "0112"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Order matters — wipe children before parents to avoid FK violations.
_WIPE_ORDER: tuple[str, ...] = (
    "request_steps",
    "request_logs",
    "outbox",
    "jobs",
    "messages",
    "chat_histories",
    "semantic_cache",
    "conversations",
    "document_chunks",
    "documents",
)


def upgrade() -> None:
    """TRUNCATE all transactional tables in dependency order."""
    for table in _WIPE_ORDER:
        # CASCADE handles incidental child FK references (e.g. if a
        # downstream table holds an FK we didn't list above). RESTART
        # IDENTITY resets sequences so re-test runs start from doc 1.
        op.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        logger.info("alembic_0113_wiped", table=table)


def downgrade() -> None:
    """No-op intentionally.

    Cleaned data is gone forever — there is no plausible inverse that
    doesn't violate referential integrity once chunks / outbox /
    request_steps are dropped. Operators rolling back past this
    migration should expect the data to remain wiped.
    """
    pass
