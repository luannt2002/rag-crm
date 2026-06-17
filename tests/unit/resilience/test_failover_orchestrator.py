"""Failover orchestrator + per-resource circuit-breaker tests (Phase D / D1).

Coverage matrix (15 tests, well above the 10-unit gate):

Registry layer
  * registry exposes the four resource keys.
  * unknown key falls back to ``NullCircuitBreaker``.
  * registry honours per-resource constructors (LLM ``provider_code``).

Per-resource adapters
  * Redis/DB/LLM each report their own ``name``.
  * Adapter state transitions track the underlying ``CircuitBreaker``:
    closed → fail_max → open → cooldown → half_open → success → closed.
  * Half-open failure reopens with adaptive cooldown.

Orchestrator
  * Default ``enabled=True`` produces real adapters.
  * ``enabled=False`` produces ``NullCircuitBreaker`` for every resource.
  * Cache returns the same instance across calls (state preserved).
  * Distinct LLM ``provider_code`` values produce distinct breakers.
  * Tripping one provider does NOT poison sibling providers.
  * ``reset_all`` force-closes every cached breaker.
  * ``snapshot`` reports state per cache key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ragbot.application.ports.circuit_breaker_port import CircuitBreakerPort
from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreakerPolicy,
)
from ragbot.infrastructure.resilience import (
    DbCircuitBreaker,
    FailoverOrchestrator,
    LlmCircuitBreaker,
    NullCircuitBreaker,
    RedisCircuitBreaker,
    build_circuit_breaker,
    list_resources,
)
from ragbot.shared.constants import (
    CB_RESOURCE_DB,
    CB_RESOURCE_LLM,
    CB_RESOURCE_REDIS,
    DEFAULT_CB_COOLDOWN_S,
    DEFAULT_CB_FAILURE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Registry layer.
# ---------------------------------------------------------------------------

def test_registry_lists_all_resource_keys() -> None:
    """Registry must expose Redis, DB, LLM and the null sentinel."""
    keys = list_resources()
    assert set(keys) == {
        CB_RESOURCE_REDIS,
        CB_RESOURCE_DB,
        CB_RESOURCE_LLM,
        "null",
    }
    assert keys == sorted(keys), "list_resources must return sorted keys"


@pytest.mark.parametrize(
    "key,expected_cls",
    [
        (CB_RESOURCE_REDIS, RedisCircuitBreaker),
        (CB_RESOURCE_DB, DbCircuitBreaker),
        (CB_RESOURCE_LLM, LlmCircuitBreaker),
        ("null", NullCircuitBreaker),
    ],
)
def test_registry_resolves_each_key(key: str, expected_cls: type) -> None:
    """Each registered key resolves to the matching adapter class."""
    inst = build_circuit_breaker(key)
    assert isinstance(inst, expected_cls)
    # Adapter satisfies the Port protocol (runtime_checkable).
    assert isinstance(inst, CircuitBreakerPort)


def test_registry_unknown_key_falls_back_to_null() -> None:
    """Misconfigured resource key degrades silently to Null breaker."""
    inst = build_circuit_breaker("does-not-exist")
    assert isinstance(inst, NullCircuitBreaker)
    # Confirm the unknown name is preserved on the Null instance so the
    # log line / metrics tag can identify the misconfig.
    assert inst.name == "does-not-exist"


# ---------------------------------------------------------------------------
# Per-resource adapters.
# ---------------------------------------------------------------------------

def test_each_adapter_reports_its_resource_name() -> None:
    """The adapter ``name`` must equal the registry key (no mislabel)."""
    assert RedisCircuitBreaker().name == CB_RESOURCE_REDIS
    assert DbCircuitBreaker().name == CB_RESOURCE_DB
    # LLM with no provider code keeps the bare resource name.
    assert LlmCircuitBreaker().name == CB_RESOURCE_LLM
    # LLM with provider_code = "openai" → "llm:openai".
    assert LlmCircuitBreaker(provider_code="openai").name == "llm:openai"


def test_adapter_state_transitions_full_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed → fail_max → Open → cooldown → Half-Open → success → Closed."""
    policy = CircuitBreakerPolicy(fail_max=2, reset_timeout_s=10)
    cb = RedisCircuitBreaker(policy=policy)
    # Initially closed.
    assert cb.state == CBState.CLOSED
    assert cb.can_execute() is True

    # Pin the wall-clock on the underlying breaker so cooldown is
    # deterministic.
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb._breaker, "_now", lambda: base)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.can_execute() is False

    # Move clock past cooldown — transitions to half-open on probe.
    monkeypatch.setattr(cb._breaker, "_now", lambda: base + timedelta(seconds=11))
    assert cb.can_execute() is True
    assert cb.state == CBState.HALF_OPEN

    # Successful probe closes the breaker.
    cb.record_success()
    assert cb.state == CBState.CLOSED


def test_half_open_failure_reopens_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe failure during HALF_OPEN must transition back to OPEN."""
    cb = DbCircuitBreaker(policy=CircuitBreakerPolicy(fail_max=1, reset_timeout_s=5))
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb._breaker, "_now", lambda: base)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    monkeypatch.setattr(cb._breaker, "_now", lambda: base + timedelta(seconds=6))
    cb.can_execute()  # → HALF_OPEN
    assert cb.state == CBState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CBState.OPEN


def test_threshold_uses_shared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default policy must trip after ``DEFAULT_CB_FAILURE_THRESHOLD`` fails."""
    cb = RedisCircuitBreaker()
    base = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(cb._breaker, "_now", lambda: base)
    # One below threshold ⇒ still closed.
    for _ in range(DEFAULT_CB_FAILURE_THRESHOLD - 1):
        cb.record_failure()
    assert cb.state == CBState.CLOSED
    # Hitting threshold trips OPEN.
    cb.record_failure()
    assert cb.state == CBState.OPEN
    # Cooldown == default base.
    assert cb._breaker.effective_cooldown_s == DEFAULT_CB_COOLDOWN_S


# ---------------------------------------------------------------------------
# FailoverOrchestrator.
# ---------------------------------------------------------------------------

def test_orchestrator_default_enabled_returns_real_adapters() -> None:
    """Default flag = True ⇒ Redis/DB/LLM resolve to real adapters."""
    orch = FailoverOrchestrator()
    assert orch.enabled is True
    assert isinstance(orch.get(CB_RESOURCE_REDIS), RedisCircuitBreaker)
    assert isinstance(orch.get(CB_RESOURCE_DB), DbCircuitBreaker)
    assert isinstance(orch.get(CB_RESOURCE_LLM), LlmCircuitBreaker)


def test_orchestrator_disabled_returns_null_breakers_universally() -> None:
    """Flag = False ⇒ every resource resolves to NullCircuitBreaker."""
    orch = FailoverOrchestrator(enabled=False)
    assert orch.enabled is False
    for resource in (CB_RESOURCE_REDIS, CB_RESOURCE_DB, CB_RESOURCE_LLM):
        breaker = orch.get(resource)
        assert isinstance(breaker, NullCircuitBreaker)
        # Disabled Null breaker must report the resource it stands in for.
        assert breaker.name == resource
        # Null never raises, regardless of recorded failures.
        for _ in range(DEFAULT_CB_FAILURE_THRESHOLD * 2):
            breaker.record_failure()
        assert breaker.state == CBState.CLOSED
        assert breaker.can_execute() is True


def test_orchestrator_caches_breaker_instance_across_calls() -> None:
    """Same resource key ⇒ same instance ⇒ state preserved between calls."""
    orch = FailoverOrchestrator()
    first = orch.get(CB_RESOURCE_REDIS)
    second = orch.get(CB_RESOURCE_REDIS)
    assert first is second
    # Trip ``first`` via direct API; ``second`` (same object) must reflect it.
    for _ in range(DEFAULT_CB_FAILURE_THRESHOLD):
        first.record_failure()
    assert second.state == CBState.OPEN


def test_orchestrator_llm_provider_codes_yield_distinct_breakers() -> None:
    """Each ``provider_code`` argument fans out into its own cached breaker."""
    orch = FailoverOrchestrator()
    cb_openai = orch.get(CB_RESOURCE_LLM, provider_code="openai")
    cb_anthropic = orch.get(CB_RESOURCE_LLM, provider_code="anthropic")
    cb_cohere = orch.get(CB_RESOURCE_LLM, provider_code="cohere")
    assert {cb_openai.name, cb_anthropic.name, cb_cohere.name} == {
        "llm:openai",
        "llm:anthropic",
        "llm:cohere",
    }
    # Distinct instances.
    assert cb_openai is not cb_anthropic
    assert cb_anthropic is not cb_cohere
    # Re-resolving the same code returns the cached instance.
    assert orch.get(CB_RESOURCE_LLM, provider_code="openai") is cb_openai


def test_orchestrator_llm_failures_isolated_per_provider() -> None:
    """Tripping one provider must not poison sibling providers' state."""
    orch = FailoverOrchestrator()
    cb_openai = orch.get(CB_RESOURCE_LLM, provider_code="openai")
    cb_anthropic = orch.get(CB_RESOURCE_LLM, provider_code="anthropic")
    for _ in range(DEFAULT_CB_FAILURE_THRESHOLD):
        cb_openai.record_failure()
    assert cb_openai.state == CBState.OPEN
    assert cb_anthropic.state == CBState.CLOSED
    assert cb_anthropic.can_execute() is True


def test_orchestrator_reset_all_closes_every_cached_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reset_all`` must force-CLOSE Redis, DB, and per-provider LLM breakers."""
    orch = FailoverOrchestrator(
        policy=CircuitBreakerPolicy(fail_max=1, reset_timeout_s=60),
    )
    base = datetime.now(tz=timezone.utc)
    redis_cb = orch.get(CB_RESOURCE_REDIS)
    db_cb = orch.get(CB_RESOURCE_DB)
    llm_cb = orch.get(CB_RESOURCE_LLM, provider_code="openai")
    for cb in (redis_cb, db_cb, llm_cb):
        monkeypatch.setattr(cb._breaker, "_now", lambda: base)  # type: ignore[attr-defined]
        cb.record_failure()
        assert cb.state == CBState.OPEN
    orch.reset_all()
    for cb in (redis_cb, db_cb, llm_cb):
        assert cb.state == CBState.CLOSED


def test_orchestrator_snapshot_reports_state_per_cache_key() -> None:
    """``snapshot`` returns ``{cache_key: state}`` for observability dashboards."""
    orch = FailoverOrchestrator()
    orch.get(CB_RESOURCE_REDIS)
    orch.get(CB_RESOURCE_DB)
    orch.get(CB_RESOURCE_LLM, provider_code="openai")
    snap = orch.snapshot()
    assert snap == {
        CB_RESOURCE_REDIS: CBState.CLOSED.value,
        CB_RESOURCE_DB: CBState.CLOSED.value,
        "llm:openai": CBState.CLOSED.value,
    }


def test_orchestrator_unknown_resource_falls_back_to_null() -> None:
    """Unknown resource (e.g. ``mystery``) returns a NullCircuitBreaker."""
    orch = FailoverOrchestrator()
    breaker = orch.get("mystery-bus")
    assert isinstance(breaker, NullCircuitBreaker)
    # Cached under the same key — same instance on second call.
    assert orch.get("mystery-bus") is breaker
