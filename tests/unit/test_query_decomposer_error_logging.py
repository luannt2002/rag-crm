"""Issue 4 regression — decomposer must emit error_type + model on failure.

Pre-fix, ``query_decomposer.decompose_query`` logged the exception via
stdlib ``logger.warning("event", extra={"error_type": ..., "model": ...})``.
The platform's structlog ``ProcessorFormatter.foreign_pre_chain`` does
not surface ``extra=`` keys onto the JSON event body, so 50+ production
events at journalctl showed empty bodies — ops could not diagnose what
failed.

Post-fix the logger is ``structlog.get_logger(__name__)`` and the call
uses **kwargs (``error_type=...``, ``error_message=...``, ``model=...``).
This regression test pins two contracts:

1. The module imports structlog (not stdlib ``logging.getLogger``).
2. The bound logger receives ``error_type`` + ``error_message`` + ``model``
   keyword arguments when ``llm_invoker`` raises.

Together they ensure the JSON event body in production includes the
diagnostic fields. Future refactors that revert to ``extra=`` will fail
this test before reaching production.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from ragbot.orchestration.nodes import query_decomposer


def test_decompose_llm_complete_does_not_pass_model_override_kwarg():
    """Issue 9 (issue-9-decompose-model-override-rejected.md).

    OpenAI rejects unknown kwargs with HTTP 400. The decompose
    ``_llm_invoker`` previously forwarded ``model_override=model`` from
    its closure to ``llm.complete(cfg, ...)``; cfg already carries the
    resolved model, so the kwarg was redundant AND breaking. This static
    guard scans the source to keep it from coming back.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src/ragbot/orchestration/query_graph.py"
    text = src.read_text(encoding="utf-8")
    assert "model_override=model" not in text, (
        "model_override=model forwarded through llm.complete in query_graph.py — "
        "OpenAI rejects unknown kwargs (400). cfg already carries the resolved "
        "model; remove the kwarg. See issue-9 plan."
    )


def test_module_uses_structlog_not_stdlib_logging():
    """Guard: the decomposer's ``logger`` must be a structlog logger.

    stdlib ``logging.Logger.warning`` accepts ``extra={}`` but the
    foreign_pre_chain bridge in src/ragbot/config/logging.py drops
    those keys from the rendered JSON. structlog ``BoundLogger.warning``
    accepts kwargs and surfaces them. Pin the import.
    """
    import structlog

    logger = query_decomposer.logger
    # structlog's filtering_bound_logger / BoundLoggerLazyProxy both
    # live under structlog. isinstance check is fragile across versions,
    # so check the module instead.
    assert type(logger).__module__.startswith("structlog"), (
        f"query_decomposer.logger came from {type(logger).__module__!r}; "
        "expected structlog. Reverting to stdlib logging.getLogger drops "
        "the kwargs from the JSON event body — see issue-4 plan."
    )


def test_failure_path_logs_error_type_and_model_and_message():
    """When the injected llm_invoker raises, the warning event must
    carry error_type, error_message, and model as kwargs (not extra=)."""

    async def _failing_invoker(*, system, user, model, max_tokens):
        raise ValueError("simulated upstream failure")

    captured: dict = {}

    class _Spy:
        def warning(self, event, **kwargs):
            captured["event"] = event
            captured.update(kwargs)

        # Other levels are no-ops for this test.
        def __getattr__(self, name):
            return lambda *a, **kw: None

    spy = _Spy()
    with patch.object(query_decomposer, "logger", spy):
        result = asyncio.run(
            query_decomposer.decompose_query(
                query="anything",
                llm_invoker=_failing_invoker,
                # Force decomposer.enabled=True so the LLM path is taken.
                config_getter=lambda key, default: True if key == "decomposer.enabled" else default,
            )
        )

    assert result == ["anything"]
    assert captured.get("event") == "decomposer_llm_call_failed"
    assert captured.get("error_type") == "ValueError"
    assert "simulated upstream failure" in (captured.get("error_message") or "")
    assert captured.get("model"), "model kwarg must be present for ops to identify which model failed"
    # Reject the pre-fix shape where everything was crammed inside extra={}.
    assert "extra" not in captured, (
        "Logger received extra= kwarg — this is the pre-fix shape that "
        "drops fields from the rendered JSON. See issue-4 plan."
    )
