"""Rename internal UUID FK columns to record_ prefix.

Convention: bot_id = external VARCHAR slug, record_bot_id = internal UUID PK ref.
Also adds missing composite and keysort indexes.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def _rename(table: str, old: str, new: str) -> None:
    op.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")


def upgrade() -> None:
    # --- documents ---
    _rename("documents", "bot_id", "record_bot_id")
    _rename("documents", "tenant_id", "record_tenant_id")

    # --- conversations ---
    _rename("conversations", "bot_id", "record_bot_id")
    _rename("conversations", "tenant_id", "record_tenant_id")

    # --- messages ---
    _rename("messages", "bot_id", "record_bot_id")
    _rename("messages", "conversation_id", "record_conversation_id")
    _rename("messages", "tenant_id", "record_tenant_id")

    # --- request_logs ---
    _rename("request_logs", "bot_id", "record_bot_id")
    _rename("request_logs", "conversation_id", "record_conversation_id")
    _rename("request_logs", "model_id", "record_model_id")
    _rename("request_logs", "binding_id", "record_binding_id")
    _rename("request_logs", "knowledge_base_id", "record_knowledge_base_id")
    _rename("request_logs", "tenant_id", "record_tenant_id")

    # --- request_steps ---
    _rename("request_steps", "request_id", "record_request_id")
    _rename("request_steps", "binding_id", "record_binding_id")
    _rename("request_steps", "tenant_id", "record_tenant_id")

    # --- model_invocations ---
    _rename("model_invocations", "request_id", "record_request_id")
    _rename("model_invocations", "tenant_id", "record_tenant_id")

    # --- semantic_cache ---
    _rename("semantic_cache", "bot_id", "record_bot_id")
    _rename("semantic_cache", "tenant_id", "record_tenant_id")

    # --- chat_histories ---
    _rename("chat_histories", "bot_id", "record_bot_id")

    # --- bot_model_bindings ---
    _rename("bot_model_bindings", "bot_id", "record_bot_id")
    _rename("bot_model_bindings", "model_id", "record_model_id")
    _rename("bot_model_bindings", "fallback_model_id", "record_fallback_model_id")
    _rename("bot_model_bindings", "tenant_id", "record_tenant_id")
    _rename("bot_model_bindings", "prompt_template_id", "record_prompt_template_id")
    _rename("bot_model_bindings", "system_prompt_version_id", "record_prompt_version_id")

    # --- prompt_templates ---
    _rename("prompt_templates", "bot_id", "record_bot_id")
    _rename("prompt_templates", "tenant_id", "record_tenant_id")

    # --- prompt_versions ---
    _rename("prompt_versions", "tenant_id", "record_tenant_id")

    # --- tenant_model_policy ---
    _rename("tenant_model_policy", "bot_id", "record_bot_id")
    _rename("tenant_model_policy", "model_id", "record_model_id")
    _rename("tenant_model_policy", "fallback_model_id", "record_fallback_model_id")
    _rename("tenant_model_policy", "tenant_id", "record_tenant_id")

    # --- ai_models ---
    _rename("ai_models", "provider_id", "record_provider_id")

    # --- bots (only model refs, NOT bot_id which is external VARCHAR) ---
    _rename("bots", "model_id", "record_model_id")
    _rename("bots", "embedding_model_id", "record_embedding_model_id")

    # --- document_chunks (only document_id, bot_id+tenant_id already dropped) ---
    _rename("document_chunks", "document_id", "record_document_id")

    # --- guardrail_events ---
    _rename("guardrail_events", "request_id", "record_request_id")
    _rename("guardrail_events", "step_id", "record_step_id")
    _rename("guardrail_events", "tenant_id", "record_tenant_id")

    # --- jobs ---
    _rename("jobs", "tenant_id", "record_tenant_id")

    # --- outbox ---
    _rename("outbox", "tenant_id", "record_tenant_id")

    # --- quotas ---
    _rename("quotas", "tenant_id", "record_tenant_id")

    # --- audit_log ---
    _rename("audit_log", "tenant_id", "record_tenant_id")

    # --- model_capabilities ---
    _rename("model_capabilities", "model_id", "record_model_id")

    # --- INDEXES (rebuild with new names) ---
    # Drop old indexes that reference old column names
    op.execute("DROP INDEX IF EXISTS ix_doc_bot_channel")
    op.execute("DROP INDEX IF EXISTS ix_reqlog_tenant_started")
    op.execute("DROP INDEX IF EXISTS ix_reqlog_model")
    op.execute("DROP INDEX IF EXISTS ix_reqlog_conversation")
    op.execute("DROP INDEX IF EXISTS ix_reqlog_tenant_message")
    op.execute("DROP INDEX IF EXISTS ix_msg_tenant_bot")
    op.execute("DROP INDEX IF EXISTS ix_chunks_document")
    op.execute("DROP INDEX IF EXISTS ix_model_inv_request_attempt")
    op.execute("DROP INDEX IF EXISTS ix_model_inv_tenant_started")
    op.execute("DROP INDEX IF EXISTS ix_reqstep_request_order")

    # Recreate with new column names
    op.execute("CREATE INDEX ix_doc_bot_channel ON documents (record_bot_id, channel_type)")
    op.execute("CREATE INDEX ix_reqlog_tenant_started ON request_logs (record_tenant_id, started_at)")
    op.execute("CREATE INDEX ix_reqlog_model ON request_logs (record_model_id)")
    op.execute("CREATE INDEX ix_reqlog_conversation ON request_logs (record_conversation_id)")
    op.execute("CREATE INDEX ix_reqlog_tenant_message ON request_logs (record_tenant_id, message_id)")
    op.execute("CREATE INDEX ix_reqlog_bot ON request_logs (record_bot_id)")
    op.execute("CREATE INDEX ix_msg_tenant_bot ON messages (record_tenant_id, record_bot_id)")
    op.execute("CREATE INDEX ix_chunks_document ON document_chunks (record_document_id)")
    op.execute("CREATE INDEX ix_model_inv_request_attempt ON model_invocations (record_request_id, attempt_no)")
    op.execute("CREATE INDEX ix_model_inv_tenant_started ON model_invocations (record_tenant_id, started_at)")
    op.execute("CREATE INDEX ix_reqstep_request_order ON request_steps (record_request_id, step_order)")

    # New keysort indexes for pagination
    op.execute("CREATE INDEX IF NOT EXISTS ix_doc_created ON documents (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_created ON jobs (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reqstep_started ON request_steps (started_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bots_created ON bots (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_semantic_cache_bot ON semantic_cache (record_bot_id)")


def downgrade() -> None:
    # This is a large rename — downgrade would reverse all renames
    # Not implementing full downgrade for safety
    raise NotImplementedError("Downgrade not supported for mass column rename")
