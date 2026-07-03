"""S4 — parallel guard_output ‖ grounding_check via asyncio.gather.

Background. The legacy ``guard_output`` node ran the regex output guards
(``system_prompt_leak``, ``secret_scanner``) inline in
``guardrail.check_output`` and then awaited the LLM grounding judge
inside the same call as a nested sub-step. The two branches are
independent — they read ``state["answer"]`` + ``state["graded_chunks"]``
and write disjoint slices of ``state["guardrail_flags"]``. The S4 fix
dispatches them via ``asyncio.gather`` behind
``pipeline_parallel_output_guards_enabled`` (default True).

Tests in this module pin behavioural invariants of the parallel branch
*without* the rest of the LangGraph machinery:

- ``test_both_run_in_parallel`` — wall time bounded by max(branch) not
  sum(branches) when both branches sleep concurrently.
- ``test_guard_failure_does_not_block_grounding`` — regex branch
  raising bubbles up as a ``parallel_error`` flag; grounding result
  still merged.
- ``test_grounding_failure_does_not_block_guard`` — symmetric.
- ``test_state_merge_no_overwrite`` — both branches add flags; merged
  list contains every entry from both.
- ``test_both_succeed_state_merged`` — happy-path two-branch merge.
- ``test_flag_off_uses_serial_fallback`` — when the parallel toggle is
  off, the node calls ``check_output`` once with grounding enabled and
  never reaches the parallel gather.

Each test drives the *compiled* graph's ``guard_output`` node directly
via ``node_callable`` so the closure (which captures ``guardrail`` +
``llm`` + ``model_resolver``) is the one under test.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.infrastructure.guardrails.local_guardrail import (
    GuardrailBlocked,
    GuardrailHit,
)
from ragbot.shared.constants import (
    DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED,
)
from tests.unit._node_test_helpers import (
    RecordingStepTracker,
    build_test_graph,
    make_state,
    node_callable,
)


# --------------------------------------------------------------------------- #
# Test doubles                                                                #
# --------------------------------------------------------------------------- #


class _TimingGuardrail:
    """Guardrail double that sleeps inside ``check_output`` so the test can
    observe wall-clock parallelism between the regex branch and the LLM
    grounding judge branch (which the test patches separately).
    """

    def __init__(
        self,
        *,
        regex_sleep_s: float = 0.0,
        regex_hits: list[GuardrailHit] | None = None,
        raise_blocked: list[GuardrailHit] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.regex_sleep_s = regex_sleep_s
        self.regex_hits = regex_hits or []
        self.raise_blocked = raise_blocked
        self.raise_exc = raise_exc
        self.check_input_calls: list[Any] = []
        self.check_output_calls: list[dict[str, Any]] = []
        self.persist_calls: list[list[GuardrailHit]] = []

    async def check_input(self, *args: Any, **_kw: Any) -> list[Any]:
        self.check_input_calls.append(args)
        return []

    async def check_output(self, answer: str, **kwargs: Any) -> list[GuardrailHit]:
        self.check_output_calls.append({"answer": answer, **kwargs})
        if self.regex_sleep_s:
            await asyncio.sleep(self.regex_sleep_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.raise_blocked is not None:
            raise GuardrailBlocked(self.raise_blocked)
        return list(self.regex_hits)

    async def _persist(
        self,
        hits: list[GuardrailHit],
        **_kw: Any,
    ) -> None:
        self.persist_calls.append(list(hits))


def _build_graph_with(guardrail: _TimingGuardrail):
    """Compile a graph swapping the default ``FakeGuardrail`` for the test
    double. ``build_test_graph`` does not expose a guardrail parameter so
    we call ``build_graph`` directly with minimal DI."""
    from unittest.mock import MagicMock

    from ragbot.orchestration.query_graph import build_graph
    from tests.unit._node_test_helpers import (
        FakeInvocationLogger,
        RecordingAuditLogger,
        _LAST_TEST_BOT_SYSTEM_PROMPT,
        _LAST_TEST_KG_SERVICE,
        _LAST_TEST_SESSION_FACTORY,
        _LAST_TEST_TRACKER,
        make_resolver_and_llm,
    )

    tracker = RecordingStepTracker()
    _LAST_TEST_TRACKER.append(tracker)
    _LAST_TEST_KG_SERVICE.append(None)
    _LAST_TEST_SESSION_FACTORY.append(None)
    _LAST_TEST_BOT_SYSTEM_PROMPT.append("")
    resolver, llm, _cfg = make_resolver_and_llm()
    audit = RecordingAuditLogger()
    compiled = build_graph(
        invocation_logger=FakeInvocationLogger(),
        guardrail=guardrail,
        model_resolver=resolver,
        llm=llm,
        vector_store=MagicMock(),
        embedder=MagicMock(),
        semantic_cache=None,
        audit_logger=audit,
    )
    return compiled, tracker, audit, resolver, llm


def _state_with_grounding_eligible(
    *,
    pipeline_config_override: dict[str, Any] | None = None,
    answer: str = "Article 11 says A. Article 12 says B.",
    chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pcfg: dict[str, Any] = {
        "grounding_check_enabled": True,
        "grounding_check_threshold": 0.3,
        # factoid is the default intent in make_state and is in
        # DEFAULT_GROUNDING_INTENTS.
        "grounding_intents": ("factoid",),
        "pipeline_parallel_output_guards_enabled": True,
        "citation_marker_required": False,
        "oos_answer_template": "<oos>",
    }
    if pipeline_config_override:
        pcfg.update(pipeline_config_override)
    chunks = chunks if chunks is not None else [{"text": "Article 11 supports A."}]
    return make_state(
        pipeline_config=pcfg,
        answer=answer,
        graded_chunks=chunks,
        intent="factoid",
        message_id=1,
        request_id=uuid4(),
    )


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_default_parallel_output_guards_flag_is_true() -> None:
    """The constant gates the new branch; flipping it back to False
    silently reverts to serial execution."""
    assert DEFAULT_PIPELINE_PARALLEL_OUTPUT_GUARDS_ENABLED is True


@pytest.mark.asyncio
async def test_both_run_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regex branch sleeps 0.4s; grounding branch sleeps 0.4s. Serial
    execution would take >=0.8s. ``asyncio.gather`` brings wall-time
    down to ~0.4s. Assert strictly less than 0.7s to leave margin for
    scheduler jitter on slow CI hosts.
    """
    guardrail = _TimingGuardrail(regex_sleep_s=0.4)
    compiled, _tracker, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    async def _slow_grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        await asyncio.sleep(0.4)
        return None

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _slow_grounding,
    )

    state = _state_with_grounding_eligible()
    t0 = time.perf_counter()
    out = await guard_output(state)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.7, (
        f"parallel execution should bring wall-time well under 0.8s "
        f"(serial sum); observed {elapsed:.3f}s — gather may have been "
        "bypassed"
    )
    # Sanity — node returned the merged guardrail_flags dict.
    assert "guardrail_flags" in out
    assert len(guardrail.check_output_calls) == 1
    # The regex branch must be called with grounding disabled (the
    # grounding work moved to the sibling task).
    assert guardrail.check_output_calls[0]["grounding_check_enabled"] is False
    assert guardrail.check_output_calls[0]["llm_complete_fn"] is None


@pytest.mark.asyncio
async def test_guard_failure_does_not_block_grounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regex branch raises a non-Blocked exception → flagged as
    ``parallel_error``; grounding branch's verdict still surfaces."""
    guardrail = _TimingGuardrail(raise_exc=RuntimeError("regex blew up"))
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    grounding_hit = GuardrailHit(
        rule_id="llm_grounding_fail",
        severity="warn",
        action="hitl",
        details={"checked": 2, "unsupported": 2},
    )

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        return grounding_hit

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    out = await guard_output(_state_with_grounding_eligible())
    flags = out["guardrail_flags"]
    rule_ids = {f.get("rule_id") for f in flags}
    assert "parallel_error" in rule_ids
    assert "llm_grounding_fail" in rule_ids
    # Branch attribution lets ops triage which side died.
    parallel_err = next(f for f in flags if f.get("rule_id") == "parallel_error")
    assert parallel_err.get("branch") == "regex"
    assert parallel_err.get("error_type") == "RuntimeError"
    # Grounding hit was persisted independently.
    assert len(guardrail.persist_calls) == 1
    assert guardrail.persist_calls[0] == [grounding_hit]


@pytest.mark.asyncio
async def test_grounding_failure_does_not_block_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grounding branch raises → flagged as ``parallel_error``; regex
    hits still surface."""
    regex_hit = GuardrailHit(
        rule_id="system_prompt_leak",
        severity="warn",
        action="log",
        details={"matched_shingle": "abc"},
    )
    guardrail = _TimingGuardrail(regex_hits=[regex_hit])
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        raise TimeoutError("grounding timed out")

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    out = await guard_output(_state_with_grounding_eligible())
    flags = out["guardrail_flags"]
    rule_ids = {f.get("rule_id") for f in flags}
    assert "system_prompt_leak" in rule_ids
    assert "parallel_error" in rule_ids
    parallel_err = next(f for f in flags if f.get("rule_id") == "parallel_error")
    assert parallel_err.get("branch") == "grounding"
    assert parallel_err.get("error_type") == "TimeoutError"


@pytest.mark.asyncio
async def test_state_merge_no_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both branches emit flags; merged list contains every entry from
    both, plus any pre-existing flags from earlier nodes."""
    regex_hit = GuardrailHit(
        rule_id="secret_scanner",
        severity="warn",
        action="log",
        details={"matched": "ak_*"},
    )
    grounding_hit = GuardrailHit(
        rule_id="llm_grounding_fail",
        severity="warn",
        action="hitl",
        details={"checked": 3, "unsupported": 2},
    )
    guardrail = _TimingGuardrail(regex_hits=[regex_hit])
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        return grounding_hit

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    # Pre-existing flag from a hypothetical earlier node — must survive.
    state = _state_with_grounding_eligible()
    state["guardrail_flags"] = [
        {"stage": "input", "rule_id": "input_prior", "severity": "info", "action": "log"}
    ]
    out = await guard_output(state)
    flags = out["guardrail_flags"]
    rule_ids = [f.get("rule_id") for f in flags]
    assert "input_prior" in rule_ids
    assert "secret_scanner" in rule_ids
    assert "llm_grounding_fail" in rule_ids
    # No silent dedup: each rule appears exactly once.
    assert rule_ids.count("secret_scanner") == 1
    assert rule_ids.count("llm_grounding_fail") == 1
    # Grounding persisted via the standalone _persist path.
    assert guardrail.persist_calls == [[grounding_hit]]


@pytest.mark.asyncio
async def test_both_succeed_state_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path — regex emits no hit, grounding emits no hit; the
    returned flags list is empty *and* both branches were invoked."""
    guardrail = _TimingGuardrail()
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    calls: list[str] = []

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        calls.append("grounding")
        return None

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    out = await guard_output(_state_with_grounding_eligible())
    assert out["guardrail_flags"] == []
    # numeric-fidelity observe verdict rides along on every clean exit
    # (truth-audit Step 4) — dict-shaped, never gates the answer.
    assert isinstance(out.get("numeric_fidelity"), dict)
    assert calls == ["grounding"], "grounding branch must have been awaited"
    assert len(guardrail.check_output_calls) == 1, (
        "regex branch must have been awaited via check_output"
    )
    # No persist when there is no grounding hit to log.
    assert guardrail.persist_calls == []


@pytest.mark.asyncio
async def test_regex_block_still_persists_grounding_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the regex branch raises GuardrailBlocked, the OOS template
    swap fires *and* the grounding hit (if any) is still merged + audited."""
    blocked_hit = GuardrailHit(
        rule_id="system_prompt_leak",
        severity="block",
        action="block",
        details={"matched_shingle": "secret"},
    )
    guardrail = _TimingGuardrail(raise_blocked=[blocked_hit])
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    grounding_hit = GuardrailHit(
        rule_id="llm_grounding_fail",
        severity="warn",
        action="hitl",
        details={"checked": 1, "unsupported": 1},
    )

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        return grounding_hit

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    out = await guard_output(_state_with_grounding_eligible())
    assert out["answer"] == "<oos>"
    assert out["answer_type"] == "blocked"
    flags = out["guardrail_flags"]
    rule_ids = {f.get("rule_id") for f in flags}
    assert "system_prompt_leak" in rule_ids
    assert "llm_grounding_fail" in rule_ids
    assert guardrail.persist_calls == [[grounding_hit]]


@pytest.mark.asyncio
async def test_flag_off_uses_serial_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the parallel flag disabled, guard_output reverts to the
    serial legacy path: a single ``check_output`` call with grounding
    enabled and ``llm_complete_fn`` wired."""
    guardrail = _TimingGuardrail()
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    # Belt-and-braces: monkeypatch llm_grounding_check too — if the
    # parallel path were taken by mistake, this would also be called.
    called_parallel: list[bool] = []

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        called_parallel.append(True)
        return None

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    state = _state_with_grounding_eligible(
        pipeline_config_override={"pipeline_parallel_output_guards_enabled": False}
    )
    out = await guard_output(state)
    assert out["guardrail_flags"] == []
    # numeric-fidelity observe verdict rides along on every clean exit
    # (truth-audit Step 4) — dict-shaped, never gates the answer.
    assert isinstance(out.get("numeric_fidelity"), dict)
    # Legacy path: check_output ran once with grounding enabled and the
    # llm_complete_fn wired.
    assert len(guardrail.check_output_calls) == 1
    call = guardrail.check_output_calls[0]
    assert call["grounding_check_enabled"] is True
    assert callable(call["llm_complete_fn"])
    # And the standalone llm_grounding_check static method was NOT
    # invoked — proof we did not go through the parallel branch.
    assert called_parallel == []


@pytest.mark.asyncio
async def test_grounding_ineligible_intent_uses_serial_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When grounding is disabled (or ineligible for the intent) the
    parallel split has nothing to do; fall back to the legacy path even
    though the flag is on."""
    guardrail = _TimingGuardrail()
    compiled, *_ = _build_graph_with(guardrail)
    guard_output = node_callable(compiled, "guard_output")

    called: list[bool] = []

    async def _grounding(*_args: Any, **_kwargs: Any) -> GuardrailHit | None:
        called.append(True)
        return None

    monkeypatch.setattr(
        "ragbot.orchestration.query_graph.OutputGuardrail.llm_grounding_check",
        _grounding,
    )

    state = _state_with_grounding_eligible(
        pipeline_config_override={
            # Intent not in the eligibility tuple → no LLM judge wired →
            # parallel branch has nothing to parallelise with.
            "grounding_intents": ("comparison",),
        },
    )
    out = await guard_output(state)
    assert out["guardrail_flags"] == []
    # numeric-fidelity observe verdict rides along on every clean exit
    # (truth-audit Step 4) — dict-shaped, never gates the answer.
    assert isinstance(out.get("numeric_fidelity"), dict)
    assert called == [], (
        "ineligible intent must not invoke the standalone grounding judge"
    )
    # Legacy path single call.
    assert len(guardrail.check_output_calls) == 1
