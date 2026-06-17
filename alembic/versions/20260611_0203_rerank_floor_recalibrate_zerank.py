"""Recalibrate rerank cliff floor 0.05 -> 0.20 for zerank-2 distribution.

EVIDENCE (benchmark 2026-06-11, layer-trace): Contextual Precision weak 10/12 bots
(0.57-0.73). Root cause = rerank-filter keeps all chunks >= absolute_floor=0.05, but
zerank-2 scores noise chunks at 0.05-0.16 (correct chunks 0.27-0.81). Floor 0.05 was
calibrated for the prior Jina-v3 distribution (alembic 0079) — stale after the
ZeroEntropy zerank-2 migration. Lifting to 0.20 drops the 0.05-0.16 noise band while
retaining relevant chunks (>=0.27). Strategy stays ``cliff`` (dynamic gap cut) — NOT a
fixed Top-K, which the layer-trace showed cuts good chunks at rank #2/#3.

Reversible: downgrade restores 0.05. Per-bot override via plan_limits unchanged.
Idempotent ON CONFLICT so re-running on a DB already at 0.20 is a no-op.
"""
from alembic import op

revision = "0203"
down_revision = "0202"
branch_labels = None
depends_on = None

_KEY = "rerank_cliff_absolute_floor"
_NEW = "0.2"
_OLD = "0.05"


def _set(value: str) -> None:
    op.execute(
        f"""
        INSERT INTO system_config (key, value)
        VALUES ('{_KEY}', to_jsonb({value}::float))
        ON CONFLICT (key) DO UPDATE SET value = to_jsonb({value}::float)
        """
    )


def upgrade() -> None:
    _set(_NEW)


def downgrade() -> None:
    _set(_OLD)
