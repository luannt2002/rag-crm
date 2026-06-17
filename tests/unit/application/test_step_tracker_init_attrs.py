"""Regression test for mega-sprint-G24 — StepTracker.__init__ must
initialise ``_batch_enabled`` and ``_buffer``.

Bug: ``StepTracker.__init__`` set every other instance attr but forgot
``self._batch_enabled`` and ``self._buffer``. The class then exposed
``batch_enabled`` and ``buffer_size`` as ``@property`` accessors that
read those attrs — first access raised
``AttributeError: 'StepTracker' object has no attribute '_batch_enabled'``.

Affected paths: any caller that probes the batch flag (analytics,
test_chat_worker_config_batch, monitoring) crashed instead of seeing
the documented default-OFF semantics described in the class docstring.

Fix: initialise both attrs at the end of ``__init__`` to safe defaults
(``False`` / empty list) — matches the class docstring's contract that
batch mode is OFF until explicitly toggled.

Pre-fix: ``StepTracker(...).batch_enabled`` raised AttributeError.
Post-fix: returns ``False``; ``buffer_size`` returns ``0``.
"""
from __future__ import annotations

from uuid import uuid4
from unittest.mock import MagicMock

from ragbot.application.services.step_tracker import StepTracker


def _build_tracker() -> StepTracker:
    """Spawn a tracker with the minimum required dependencies.

    The repo / metrics ports are stubbed because we only exercise
    ``__init__`` and the two properties; no DB IO occurs.
    """
    return StepTracker(
        request_id=uuid4(),
        record_tenant_id=uuid4(),
        repo=MagicMock(),
    )


def test_batch_enabled_property_returns_false_default() -> None:
    """``batch_enabled`` defaults to False — class docstring contract.

    Pre-fix this raised AttributeError because ``self._batch_enabled``
    was never assigned in ``__init__``.
    """
    tracker = _build_tracker()
    assert tracker.batch_enabled is False


def test_buffer_size_property_returns_zero_default() -> None:
    """``buffer_size`` reads ``len(self._buffer)`` — must be 0 at init.

    Pre-fix this raised AttributeError because ``self._buffer`` was
    never assigned in ``__init__``.
    """
    tracker = _build_tracker()
    assert tracker.buffer_size == 0


def test_init_sets_private_attrs_directly() -> None:
    """Defence-in-depth: the underlying private attrs exist on the
    instance — guards against the property short-circuit ever
    happening to mask a missing attr in a future refactor.
    """
    tracker = _build_tracker()
    assert hasattr(tracker, "_batch_enabled"), (
        "StepTracker.__init__ must set self._batch_enabled."
    )
    assert hasattr(tracker, "_buffer"), (
        "StepTracker.__init__ must set self._buffer."
    )
    assert tracker._batch_enabled is False
    assert tracker._buffer == []


def test_buffer_is_a_fresh_mutable_list_per_instance() -> None:
    """Two trackers must NOT share the same _buffer list (would corrupt
    request isolation if either one mutated it).
    """
    tracker_a = _build_tracker()
    tracker_b = _build_tracker()
    tracker_a._buffer.append({"step": "fake"})
    assert tracker_b._buffer == [], (
        "StepTracker._buffer must be a fresh per-instance list — "
        "shared default would leak buffered rows across requests."
    )


def test_step_context_record_llm_populates_fields() -> None:
    """Wave M3.2 — ``record_llm`` lifts model + tokens + cost into ctx.

    Pre-fix, only ``generate`` populated ``request_steps.model_used`` via
    the ``step()`` kwarg path (which fires BEFORE the LLM resolves a
    model). All other LLM-bound steps left the columns NULL.

    Post-fix, every LLM step calls ``ctx.record_llm(...)`` after the LLM
    call settles, so the persisted row has model_used + token counts +
    cost_usd. This test pins the helper's contract.
    """
    from ragbot.application.services.step_tracker import StepContext

    ctx = StepContext(
        name="generate", order=1, model_used=None,
        binding_id=None, metadata={},
    )
    ctx.record_llm(
        model_used="gpt-4.1-mini",
        prompt_tokens=2581,
        completion_tokens=119,
        cost_usd=0.0012,
    )
    assert ctx.model_used == "gpt-4.1-mini"
    assert ctx.input_tokens == 2581
    assert ctx.output_tokens == 119
    assert abs(ctx.cost_usd - 0.0012) < 1e-9


def test_step_context_record_llm_is_additive() -> None:
    """Two record_llm() calls accumulate — pattern used by chained LLM
    calls within a single step (e.g. multi_query_fanout calling 3
    paraphrase models inside one step boundary).
    """
    from ragbot.application.services.step_tracker import StepContext

    ctx = StepContext(
        name="multi_query_fanout", order=1, model_used=None,
        binding_id=None, metadata={},
    )
    ctx.record_llm(prompt_tokens=100, completion_tokens=20, cost_usd=0.0001)
    ctx.record_llm(prompt_tokens=110, completion_tokens=15, cost_usd=0.00011)
    ctx.record_llm(prompt_tokens=95, completion_tokens=18, cost_usd=0.00009)
    assert ctx.input_tokens == 305
    assert ctx.output_tokens == 53
    assert abs(ctx.cost_usd - 0.0003) < 1e-9


def test_step_context_record_llm_model_used_optional() -> None:
    """When ``model_used=None``, the existing value is preserved (caller
    may have set it via the ``step()`` kwarg and only wants to add
    token counts in a separate call).
    """
    from ragbot.application.services.step_tracker import StepContext

    ctx = StepContext(
        name="generate", order=1, model_used="gpt-4.1-mini",
        binding_id=None, metadata={},
    )
    ctx.record_llm(prompt_tokens=10, completion_tokens=5, cost_usd=0.0001)
    # model_used preserved despite no explicit pass
    assert ctx.model_used == "gpt-4.1-mini"
