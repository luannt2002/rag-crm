"""Bug 1 P0 — atomic-write invariant for ``InvocationLogger``.

Old behaviour: INSERT(running) + UPDATE(final) in two separate sessions.
Process kill between the two sessions left rows ``status='running'``
forever, poisoning audit dashboards and bloating the table.

New behaviour: a single INSERT (after ``yield``) carries the final
status. Process kill BEFORE the wrapper's ``finally`` runs → row never
inserted (clean). The janitor (``scripts/cleanup_stuck_invocations``)
exists as a defensive second line for legacy rows / external writers.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class _RecordingSession:
    """Captures executed statements without a real DB."""

    def __init__(self, log: list[Any]) -> None:
        self._log = log

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        self._log.append(stmt)
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        return None


def _make_logger() -> tuple[Any, list[Any]]:
    from ragbot.infrastructure.observability.invocation_logger import (
        InvocationLogger,
    )

    statements: list[Any] = []

    def _factory() -> _RecordingSession:
        return _RecordingSession(statements)

    return InvocationLogger(_factory), statements  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Single-session UPSERT — exactly ONE statement after a happy-path call.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_single_session_upsert_emits_one_statement() -> None:
    logger, statements = _make_logger()

    async with logger.invoke_model(
        message_id=11,
        record_tenant_id=None,
        record_request_id=None,
        purpose="generation",
        provider="openai",
        model_id="openai/gpt-4o-mini",
        user_prompt="hi",
    ) as ctx:
        ctx.record(
            response="ok",
            prompt_tokens=10,
            completion_tokens=2,
            cost_usd=0.0001,
            finish_reason="stop",
        )

    # The atomic-write design must NOT emit an upfront INSERT(running).
    assert len(statements) == 1, (
        f"expected exactly 1 INSERT (atomic UPSERT), got {len(statements)}"
    )
    bound = statements[0].compile().params  # type: ignore[attr-defined]
    assert bound["status"] == "success"
    assert bound["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# 2. Status transition — failed path also writes a SINGLE statement with
#    final status='failed'.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_status_transition_failure_single_write() -> None:
    logger, statements = _make_logger()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with logger.invoke_model(
            message_id=22,
            record_tenant_id=None,
            record_request_id=None,
            purpose="generation",
            provider="openai",
            model_id="openai/gpt-4o-mini",
            user_prompt="hi",
        ) as _ctx:
            raise _Boom("simulated llm crash")

    assert len(statements) == 1
    bound = statements[0].compile().params  # type: ignore[attr-defined]
    assert bound["status"] == "failed"


# ---------------------------------------------------------------------------
# 3. Process kill mid-yield → no DB write at all (the wrapper never reaches
#    its finally-block when the task is cancelled abruptly... but it DOES
#    via Python's normal try/finally semantics on CancelledError. We verify
#    that a cancellation BEFORE record() still emits exactly one stmt
#    marked status='failed' — never status='running').
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancellation_never_writes_running_status() -> None:
    """If a CancelledError tears the wrapper down, the row must NEVER carry
    status='running' on disk — it must be either absent or status='failed'.
    """
    logger, statements = _make_logger()

    with pytest.raises(BaseException):  # CancelledError is BaseException
        async with logger.invoke_model(
            message_id=33,
            record_tenant_id=None,
            record_request_id=None,
            purpose="generation",
            provider="openai",
            model_id="openai/gpt-4o-mini",
            user_prompt="hi",
        ) as _ctx:
            raise __import__("asyncio").CancelledError()

    # Wrapper's finally still runs → exactly 1 statement, never 'running'.
    assert len(statements) == 1
    bound = statements[0].compile().params  # type: ignore[attr-defined]
    assert bound["status"] != "running"
    assert bound["status"] == "failed"


# ---------------------------------------------------------------------------
# 4. Cached path — when ctx.cached=True the final status='cached'.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cached_status_written_atomically() -> None:
    logger, statements = _make_logger()

    async with logger.invoke_model(
        message_id=44,
        record_tenant_id=None,
        record_request_id=None,
        purpose="generation",
        provider="openai",
        model_id="openai/gpt-4o-mini",
        user_prompt="hi",
    ) as ctx:
        ctx.record(
            response="cached!",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0,
            finish_reason="cache_hit",
            cached=True,
        )

    assert len(statements) == 1
    bound = statements[0].compile().params  # type: ignore[attr-defined]
    assert bound["status"] == "cached"
    assert bound["cached"] is True


# ---------------------------------------------------------------------------
# 5. Janitor constant exists and has the documented value (zero-hardcode).
# ---------------------------------------------------------------------------
def test_janitor_timeout_constant_present() -> None:
    from ragbot.shared.constants import DEFAULT_INVOCATION_STUCK_TIMEOUT_S

    assert isinstance(DEFAULT_INVOCATION_STUCK_TIMEOUT_S, int)
    assert DEFAULT_INVOCATION_STUCK_TIMEOUT_S > 0


# ---------------------------------------------------------------------------
# 6. Janitor script imports cleanly and exposes the expected entry points.
# ---------------------------------------------------------------------------
def test_janitor_script_importable() -> None:
    import importlib
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    try:
        mod = importlib.import_module("scripts.cleanup_stuck_invocations")
    finally:
        sys.path.remove(str(repo_root))

    assert hasattr(mod, "cleanup_stuck_invocations")
    assert hasattr(mod, "main")
    # CLI parser respects --dry-run + --timeout-seconds.
    ns = mod._parse_args(["--dry-run", "--timeout-seconds", "120"])
    assert ns.dry_run is True
    assert ns.timeout_seconds == 120
