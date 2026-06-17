"""Seed enrichment→nano binding for ALL bots (shared default + per-bot override).

Completes the cost-routing wiring. After ``_intent_to_purpose`` routes the
ingest ``contextualization`` intent to the ``enrichment`` purpose, the model
for that purpose comes from each bot's ``enrichment`` binding (falling back to
``llm_primary`` when absent). This migration makes the SHARED DEFAULT for every
existing bot = ``gpt-4.1-nano``:

  - INSERT an ``enrichment`` binding → nano for every bot that lacks one
    (xe, legal, and any other bot — copying the bot's own tenant/workspace so
    isolation is preserved).
  - UPDATE any existing ``enrichment`` binding that still points at the old
    un-wired default ``gpt-4.1-mini`` → nano.

Per-bot override is preserved: a bot that intentionally binds ``enrichment`` to
a different model keeps it (we only touch mini, the stale default). New bots get
the same default via ``shared/bot_bindings.ensure_bot_bindings``.

Why nano: the contextual-retrieval / narrate enrichment is extractive (situate
chunk + copy verbatim numbers) and the highest-volume LLM call path — it does
not need the answer-grade model. nano ($0.16/$0.64 vs mini $0.40/$1.60) is
~2.5× cheaper AND uses a SEPARATE per-model TPM bucket, so ingest enrichment no
longer contends with live chat (which runs on mini) — fixing the 200k-TPM
ingest stall. See reports/CASE_STUDY_INGEST_TPM_COST_20260616.md and
docs/dev/CONFIG_REFERENCE.md §2.

Governed via alembic (bot_model_bindings is in the no-psql-hotfix set).
Reversible: downgrade restores enrichment bindings to gpt-4.1-mini.
"""
from alembic import op

revision = "0223"
down_revision = "0222"
branch_labels = None
depends_on = None

_NANO = "gpt-4.1-nano"
_MINI = "gpt-4.1-mini"
_PURPOSE = "enrichment"


def _model_id_sql(name: str) -> str:
    return f"(SELECT id FROM ai_models WHERE name = '{name}' AND deleted_at IS NULL LIMIT 1)"


def upgrade() -> None:
    # 1. Re-point stale mini enrichment bindings → nano (shared default).
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = {_model_id_sql(_NANO)}, updated_at = now()
        WHERE purpose = '{_PURPOSE}'
          AND record_model_id = {_model_id_sql(_MINI)}
        """
    )
    # 2. Insert enrichment→nano for every bot lacking an enrichment binding.
    op.execute(
        f"""
        INSERT INTO bot_model_bindings (
            id, record_tenant_id, workspace_id, record_bot_id, purpose,
            record_model_id, rank, weight, temperature, max_tokens, top_p,
            extra_params, active, version, effective_from
        )
        SELECT gen_random_uuid(), b.record_tenant_id, b.workspace_id, b.id,
               '{_PURPOSE}', {_model_id_sql(_NANO)}, 0, 100, 0.0, 200, 1.0,
               '{{}}'::jsonb, true, 1, now()
        FROM bots b
        WHERE b.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM bot_model_bindings bmb
              WHERE bmb.record_bot_id = b.id AND bmb.purpose = '{_PURPOSE}'
                AND bmb.active = true
          )
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE bot_model_bindings
        SET record_model_id = {_model_id_sql(_MINI)}, updated_at = now()
        WHERE purpose = '{_PURPOSE}'
          AND record_model_id = {_model_id_sql(_NANO)}
        """
    )
