"""Conversation state strategies — Null Object + JSONB backend.

Registry-driven per CLAUDE.md Strategy + DI sacred-rule. Provider chosen
via ``system_config.conversation_state_provider`` (alembic 0150b); bot
opt-in toggles ``bots.action_config.enabled``.
"""
