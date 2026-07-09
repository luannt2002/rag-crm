"""LocalGuardrail._persist must LOG a failed guardrail_events INSERT, not
swallow it silently.

A silent ``except: pass`` there is a compliance blind spot: if every INSERT
fails, an audit reads 0 guardrail events and wrongly concludes the bot was
never guarded. The failure must surface (best-effort log; still never blocks
the pipeline).
"""

from unittest.mock import AsyncMock

import pytest

from ragbot.application.ports.guardrail_port import GuardrailHit
from ragbot.infrastructure.guardrails import local_guardrail as lg
from ragbot.infrastructure.guardrails.local_guardrail import LocalGuardrail


@pytest.mark.asyncio
async def test_persist_failure_is_logged_not_silent(monkeypatch) -> None:
    repo = AsyncMock()
    repo.insert = AsyncMock(side_effect=RuntimeError("db down"))
    guard = LocalGuardrail(guardrail_repository=repo)

    logged: list[tuple] = []
    monkeypatch.setattr(
        lg._logger, "warning", lambda ev, **k: logged.append((ev, k))
    )

    hit = GuardrailHit(
        rule_id="secret_leak", severity="block", action="block", details={}
    )
    # Best-effort: MUST NOT raise …
    await guard._persist(
        [hit], guardrail_type="output", tenant_id=None,
        message_id=1, request_id=None,
    )
    # … but MUST log the swallowed failure with the event + error_type.
    assert logged, "guardrail persist failure was silently swallowed"
    event, kwargs = logged[0]
    assert event == "guardrail_persist_failed"
    assert kwargs.get("error_type") == "RuntimeError"
    assert kwargs.get("rule_id") == "secret_leak"


@pytest.mark.asyncio
async def test_persist_success_does_not_log_warning(monkeypatch) -> None:
    repo = AsyncMock()
    repo.insert = AsyncMock(return_value=None)
    guard = LocalGuardrail(guardrail_repository=repo)
    logged: list = []
    monkeypatch.setattr(lg._logger, "warning", lambda ev, **_k: logged.append(ev))
    hit = GuardrailHit(rule_id="secret_leak", severity="block", action="block", details={})
    await guard._persist(
        [hit], guardrail_type="output", tenant_id=None, message_id=1, request_id=None
    )
    assert not logged, "a successful persist must not log a warning"
