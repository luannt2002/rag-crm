"""Unit tests for ``GuardrailRuleLoader`` (Agent J).

These tests stub the SQLAlchemy session_factory + Redis client so the
loader logic can be exercised without spinning up Postgres. Behavioural
contract tested:

  * empty-table bootstrap logs CRITICAL and returns an empty RuleSet
  * platform-default rows compile into the input/output split correctly
  * tenant-specific row OVERRIDES platform default for the same rule_id
  * invalidate() drops the L1 cache so the next get_rules() hits DB again
  * a malformed regex row is SKIPPED with a warn log — loader still
    serves the surviving rules

Per CLAUDE.md test rules: real behavioural assertions, no ``assert True``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.guardrail_rule_loader import (
    CompiledRule,
    GuardrailRuleLoader,
    RuleSet,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class _FakeResult:
    """Mimics ``Result`` returned by ``AsyncSession.execute``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [SimpleNamespace(_mapping=r) for r in rows]

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        # Record the call so tests can assert tenant filter is applied.
        self.executed.append((str(stmt), params))
        return _FakeResult(self._rows)


def _make_session_factory(rows: list[dict[str, Any]]):
    """Return an async_sessionmaker-shaped callable producing _FakeSession."""

    @asynccontextmanager
    async def _ctx():
        yield _FakeSession(rows)

    def _factory():
        return _ctx()

    return _factory


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes) -> int:
        self.published.append((subject, payload))
        return 1


# ---------------------------------------------------------------------------
# 1. Empty table → CRITICAL warning, empty RuleSet, no crash
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bootstrap_warns_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty table → loader runs to completion + served RuleSet is empty.

    structlog isn't routed through stdlib in unit tests (no setup_logging
    call), so we capture the loader's emitted events by patching the
    module-level ``logger`` attribute with a stub recorder. The CRITICAL
    event must fire so operators see a missing-seed migration loudly.
    """
    sf = _make_session_factory(rows=[])
    loader = GuardrailRuleLoader(session_factory=sf, redis_client=None)

    captured: list[tuple[str, str]] = []  # (level, event_name)

    class _Recorder:
        def __getattr__(self, level: str):
            def _record(event: str, **_kwargs: Any) -> None:
                captured.append((level, event))
            return _record

    import ragbot.application.services.guardrail_rule_loader as loader_mod
    monkeypatch.setattr(loader_mod, "logger", _Recorder())

    await loader.bootstrap()

    # CRITICAL event about empty table is emitted.
    assert ("critical", "guardrail_rule_loader_empty") in captured, (
        f"expected critical/guardrail_rule_loader_empty, got: {captured}"
    )

    # And the served RuleSet is empty.
    ruleset = await loader.get_rules(record_tenant_id=None)
    assert ruleset.input_rules == ()
    assert ruleset.output_rules == ()


# ---------------------------------------------------------------------------
# 2. Platform defaults load after seed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_platform_defaults_load_after_seed() -> None:
    rows = [
        {
            "rule_id": "prompt_injection",
            "pattern": r"(?i)ignore previous",
            "pattern_flags": "IGNORECASE",
            "severity": "block",
            "action_taken": "block",
            "scope": "input",
            "priority": 10,
            "metadata_json": {},
            "record_tenant_id": None,
        },
        {
            "rule_id": "secret_leak",
            "pattern": r"sk-[a-zA-Z0-9]{20,}",
            "pattern_flags": "",
            "severity": "block",
            "action_taken": "block",
            "scope": "output",
            "priority": 10,
            "metadata_json": {},
            "record_tenant_id": None,
        },
    ]
    sf = _make_session_factory(rows=rows)
    loader = GuardrailRuleLoader(session_factory=sf, redis_client=None)

    ruleset = await loader.get_rules(record_tenant_id=None)

    assert isinstance(ruleset, RuleSet)
    assert len(ruleset.input_rules) == 1
    assert ruleset.input_rules[0].rule_id == "prompt_injection"
    assert ruleset.input_rules[0].severity == "block"
    assert ruleset.input_rules[0].action == "block"
    # Compiled pattern actually matches.
    assert ruleset.input_rules[0].pattern.search("please ignore previous orders")

    assert len(ruleset.output_rules) == 1
    assert ruleset.output_rules[0].rule_id == "secret_leak"
    assert ruleset.output_rules[0].pattern.search(
        "sk-abcdefghijklmnopqrstuvwxyz1234",
    )


# ---------------------------------------------------------------------------
# 3. Tenant override replaces platform default
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_override_replaces_platform() -> None:
    tenant_id = uuid4()
    rows = [
        {
            "rule_id": "prompt_injection",
            "pattern": r"original_pattern",
            "pattern_flags": "",
            "severity": "block",
            "action_taken": "block",
            "scope": "input",
            "priority": 10,
            "metadata_json": {},
            "record_tenant_id": None,
        },
        {
            "rule_id": "prompt_injection",
            # Tenant-specific row tightens / changes the pattern.
            "pattern": r"override_pattern",
            "pattern_flags": "",
            "severity": "warn",  # also flipped severity
            "action_taken": "redact",
            "scope": "input",
            "priority": 10,
            "metadata_json": {"source": "tenant"},
            "record_tenant_id": tenant_id,
        },
    ]
    sf = _make_session_factory(rows=rows)
    loader = GuardrailRuleLoader(session_factory=sf, redis_client=None)

    ruleset = await loader.get_rules(record_tenant_id=tenant_id)

    # Exactly one rule for that rule_id — override wins, platform row dropped.
    matching = [r for r in ruleset.input_rules if r.rule_id == "prompt_injection"]
    assert len(matching) == 1
    rule = matching[0]
    assert rule.severity == "warn"
    assert rule.action == "redact"
    assert rule.pattern.search("override_pattern matches")
    assert rule.pattern.search("original_pattern matches") is None
    assert rule.metadata == {"source": "tenant"}


# ---------------------------------------------------------------------------
# 4. invalidate() drops L1 cache → next call re-hits DB
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invalidate_drops_l1_cache() -> None:
    rows = [
        {
            "rule_id": "secret_leak",
            "pattern": r"sk-\w+",
            "pattern_flags": "",
            "severity": "block",
            "action_taken": "block",
            "scope": "output",
            "priority": 10,
            "metadata_json": {},
            "record_tenant_id": None,
        },
    ]

    fetch_calls: list[int] = []

    @asynccontextmanager
    async def _ctx():
        fetch_calls.append(1)
        yield _FakeSession(rows)

    def _factory():
        return _ctx()

    redis = _FakeRedis()
    loader = GuardrailRuleLoader(session_factory=_factory, redis_client=redis)

    # First read populates cache.
    await loader.get_rules(record_tenant_id=None)
    # Second read served from cache — no extra session.
    await loader.get_rules(record_tenant_id=None)
    assert len(fetch_calls) == 1

    # Invalidate → next call hits DB again.
    await loader.invalidate(record_tenant_id=None)
    await loader.get_rules(record_tenant_id=None)
    assert len(fetch_calls) == 2

    # Publish side-effect fired with the right subject.
    assert len(redis.published) == 1
    subject, payload = redis.published[0]
    assert subject == "guardrail.rules_changed.v1"
    assert b"record_tenant_id" in payload


# ---------------------------------------------------------------------------
# 5. Bad regex row is SKIPPED — surviving rows still compile
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compile_failure_skipped_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "rule_id": "broken",
            # Unbalanced bracket → re.error at compile time.
            "pattern": r"[unbalanced",
            "pattern_flags": "",
            "severity": "block",
            "action_taken": "block",
            "scope": "input",
            "priority": 5,
            "metadata_json": {},
            "record_tenant_id": None,
        },
        {
            "rule_id": "good_rule",
            "pattern": r"hello",
            "pattern_flags": "",
            "severity": "warn",
            "action_taken": "redact",
            "scope": "input",
            "priority": 10,
            "metadata_json": {},
            "record_tenant_id": None,
        },
    ]
    sf = _make_session_factory(rows=rows)
    loader = GuardrailRuleLoader(session_factory=sf, redis_client=None)

    captured: list[tuple[str, str]] = []  # (level, event_name)

    class _Recorder:
        def __getattr__(self, level: str):
            def _record(event: str, **_kwargs: Any) -> None:
                captured.append((level, event))
            return _record

    import ragbot.application.services.guardrail_rule_loader as loader_mod
    monkeypatch.setattr(loader_mod, "logger", _Recorder())

    ruleset = await loader.get_rules(record_tenant_id=None)

    # Only the good rule survives.
    assert len(ruleset.input_rules) == 1
    assert ruleset.input_rules[0].rule_id == "good_rule"
    # Broken rule logged at WARN level.
    assert ("warning", "guardrail_rule_compile_failed") in captured, (
        f"expected warning/guardrail_rule_compile_failed, got: {captured}"
    )


# ---------------------------------------------------------------------------
# Bonus: CompiledRule + RuleSet are frozen (no in-place mutation surprise)
# ---------------------------------------------------------------------------
def test_compiled_rule_is_immutable() -> None:
    import re as _re

    rule = CompiledRule(
        rule_id="x",
        pattern=_re.compile("x"),
        severity="info",
        action="allow",
        scope="input",
        priority=99,
    )
    with pytest.raises(Exception):  # noqa: PT011 — frozen dataclass raises FrozenInstanceError
        rule.severity = "block"  # type: ignore[misc]
