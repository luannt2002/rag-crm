"""[T2-CostPerf] seed system_config for Token Quota Monetization defaults

Revision ID: 0102
Revises: 0101
Create Date: 2026-05-14

Seeds five ``system_config`` rows that drive the token-quota gate +
notify system. ``ON CONFLICT (key) DO NOTHING`` keeps the migration safe
to re-run and never overwrites operator-tuned values.

Keys
----
- ``max_tokens_total`` (BIGINT in JSONB, default **10_000**) — per-bot
  monthly quota baseline. Stored as a JSON number; BigInt range honoured
  because the bot field reading this is also BIGINT. No bot currently
  pays for extra quota — all live bots stay on this default.
- ``output_tokens_per_response_default`` (INT in JSONB, default 1000) —
  per-response output cap default when ``bots.extra_output_tokens_per_response``
  is 0.
- ``token_quota_notify_enabled`` (BOOL, default true) — master switch
  for "approaching-quota" notifications.
- ``token_quota_notify_throttle_s`` (INT, default 3600) — minimum
  seconds between repeated notifications for the same bot, defence vs
  notification spam when usage hovers at the threshold.
- ``token_quota_reset_timezone`` (string, default ``"Asia/Ho_Chi_Minh"``)
  — TZ used for the monthly window cutover. Stored as a JSON string
  (quoted) so callers can deserialise with ``json.loads`` uniformly.

Downgrade deletes only those five keys (operator overrides go too — the
rows came from this migration, so removing them on rollback is correct).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


# (key, JSON-encoded value, value_type, description) tuples.
_SEED_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "max_tokens_total",
        "10000",
        "int",
        "Per-bot monthly token quota baseline (BIGINT). Override via bots.extra_max_tokens for paid tier; 0 active paid bots at ship time.",
    ),
    (
        "output_tokens_per_response_default",
        "1000",
        "int",
        "Default per-response output token cap when bots.extra_output_tokens_per_response = 0.",
    ),
    (
        "token_quota_notify_enabled",
        "true",
        "bool",
        "Master switch for approaching-quota notifications.",
    ),
    (
        "token_quota_notify_throttle_s",
        "3600",
        "int",
        "Minimum seconds between repeated quota notifications per bot.",
    ),
    (
        "token_quota_reset_timezone",
        '"Asia/Ho_Chi_Minh"',
        "string",
        "Timezone used for monthly quota window cutover.",
    ),
)


_INSERT_SQL = text(
    """
    INSERT INTO system_config (key, value, value_type, description)
    VALUES (:key, (:value)::jsonb, :value_type, :description)
    ON CONFLICT (key) DO NOTHING
    """
)


_DELETE_SQL = text(
    """
    DELETE FROM system_config WHERE key = :key
    """
)


def upgrade() -> None:
    for key, value, value_type, description in _SEED_ROWS:
        op.execute(
            _INSERT_SQL.bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    for key, _value, _value_type, _description in _SEED_ROWS:
        op.execute(_DELETE_SQL.bindparams(key=key))
