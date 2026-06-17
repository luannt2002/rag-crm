"""Regression tests for mega-sprint-G2 (F2 NameError fix).

``_maybe_redact_chat_query`` is the worker-boundary hook that masks PII
out of the user query BEFORE it reaches the message store, the
request-log hash, or the LLM call. Pre-fix the call site at
``chat_worker.py:436`` referenced an undefined symbol → every chat
request would die at NameError. These tests pin the contract:

  1. The function is defined and importable (NameError protection).
  2. Toggle OFF (default) → text passes through, redactor not invoked.
  3. Toggle ON + entities found → redacted text returned, redactor invoked
     with the raw input.

Wider end-to-end coverage (audit-event shape, mask histograms, graceful
degradation on broken redactor) lives in
``tests/unit/test_pii_wire_chat_path.py`` — this file only locks the
NameError-fix surface so the regression cannot reappear silently.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ragbot.interfaces.workers.chat_worker import _maybe_redact_chat_query


class _BotCfg:
    """Minimal BotConfig stub — only the ``plan_limits`` attr is read by
    ``resolve_bot_limit`` for the ``pii_redaction_enabled`` lookup."""

    def __init__(self, plan_limits: dict | None = None) -> None:
        self.plan_limits = plan_limits or {}


def test_maybe_redact_chat_query_defined() -> None:
    """G2 anchor — function MUST be importable. Pre-fix: NameError at
    chat_worker.py:436. Post-fix: callable + reachable."""
    assert callable(_maybe_redact_chat_query)


def test_redact_disabled_returns_unchanged() -> None:
    """Default (toggle off) MUST pass text through verbatim and MUST
    NOT call the redactor — opt-in semantics keep existing tenants on
    the legacy code path with zero behaviour change."""
    redactor = MagicMock()
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": False})
    text_in = "user@example.com 0901234567"

    result = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=redactor,
        record_tenant_id="tenant-stub",
        record_bot_id="bot-stub",
    )

    assert result == text_in
    redactor.redact.assert_not_called()


def test_redact_enabled_calls_redactor() -> None:
    """Toggle on + redactor reports entities → masked text returned and
    the raw input is forwarded to ``redactor.redact`` exactly once."""
    redactor = MagicMock()
    redactor.redact.return_value = (
        "[EMAIL] [PHONE]",
        [{"type": "EMAIL", "start": 0, "end": 17},
         {"type": "PHONE", "start": 18, "end": 28}],
    )
    redactor.get_provider_name.return_value = "test_provider"
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": True})
    text_in = "user@example.com 0901234567"

    result = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=redactor,
        record_tenant_id="tenant-stub",
        record_bot_id="bot-stub",
    )

    assert result == "[EMAIL] [PHONE]"
    redactor.redact.assert_called_once_with(text_in)
