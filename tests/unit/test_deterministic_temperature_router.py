"""Router-level deterministic-temperature override (P2-E 🐛-2 / D5).

The temp-0 guarantee used to live only in the ``_invoke`` node-helper, so the
three direct ``llm.complete(cfg, …)`` callsites (multi_query / grounding /
decompose) fell back to the binding's configured temperature and ran
non-deterministically — a real answer-flip source. The override now lives at
the router choke-point every callsite passes through.
"""

from __future__ import annotations

from ragbot.infrastructure.llm.dynamic_litellm_router import (
    _resolve_effective_temperature,
)
from ragbot.shared.constants import (
    DEFAULT_DETERMINISTIC_LLM_PURPOSES,
    DEFAULT_DETERMINISTIC_TEMPERATURE,
)


def test_explicit_temperature_always_wins() -> None:
    # Even a deterministic purpose must honour an explicit override.
    assert _resolve_effective_temperature(0.7, "grounding", 0.3) == 0.7
    assert _resolve_effective_temperature(0.0, "chat", 0.3) == 0.0


def test_deterministic_purpose_forced_to_zero_when_unset() -> None:
    for purpose in ("multi_query", "grounding", "decompose", "grade", "rewrite"):
        assert purpose in DEFAULT_DETERMINISTIC_LLM_PURPOSES
        assert (
            _resolve_effective_temperature(None, purpose, 0.3)
            == DEFAULT_DETERMINISTIC_TEMPERATURE
        ), f"{purpose} must be forced deterministic regardless of binding temp"


def test_non_deterministic_purpose_keeps_binding_temperature() -> None:
    # A creative/answer purpose keeps whatever the binding configured.
    assert "answer" not in DEFAULT_DETERMINISTIC_LLM_PURPOSES
    assert _resolve_effective_temperature(None, "answer", 0.3) == 0.3
    assert _resolve_effective_temperature(None, "unknown", 0.9) == 0.9


def test_both_router_callsites_use_the_resolver() -> None:
    """Source guard: neither acompletion call may pass the old
    ``temperature if temperature is not None else cfg.params.temperature``
    pattern — both must route through _resolve_effective_temperature so the
    deterministic override cannot be bypassed again."""
    import inspect

    from ragbot.infrastructure.llm import dynamic_litellm_router as mod

    src = inspect.getsource(mod)
    assert src.count("_resolve_effective_temperature(") >= 3, (
        "both acompletion callsites (sync + stream) must call the resolver "
        "(plus its definition) — a raw cfg.params.temperature fallback "
        "reopens the non-determinism bug"
    )
    assert "temperature if temperature is not None else cfg.params.temperature" not in src
