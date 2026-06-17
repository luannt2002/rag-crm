"""Guardrail provider registry — Strategy + DI pattern enforcement.

Per CLAUDE.md Strategy + DI mindset: orchestration code never branches on
``if provider == "openai_moderation" ...``. Instead, the registry maps a
config string (``"local"`` / ``"null"`` / future ``"openai_moderation"``)
to the class implementing :class:`GuardrailPort`.
:func:`build_guardrail` looks the class up and constructs it with the
kwargs the caller provides.

Adding a new guardrail provider is a new file under
``infrastructure/guardrails/`` + one registry entry. ``query_graph`` and
``bootstrap`` stay untouched (Open-Closed).
"""

from __future__ import annotations

from typing import Any

from ragbot.application.ports.guardrail_port import GuardrailPort
from ragbot.infrastructure.guardrails.local_guardrail import LocalGuardrail
from ragbot.infrastructure.guardrails.null_guardrail import NullGuardrail


_REGISTRY: dict[str, type[GuardrailPort]] = {
    "local": LocalGuardrail,
    "null": NullGuardrail,
}


def build_guardrail(
    provider: str | None = None,
    **kwargs: Any,
) -> GuardrailPort:
    """Construct the guardrail strategy named ``provider``.

    @param provider: Registry key. ``None`` / empty / unknown string
        degrades to ``"null"`` (NullGuardrail) — same graceful-degradation
        semantics every other registry in the project uses.
    @param kwargs: Forwarded to the strategy constructor. NullGuardrail
        ignores them; LocalGuardrail accepts
        ``guardrail_repository=``, ``config_service=``, ``rule_loader=``,
        ``max_input_length=``, ``min_alpha_chars=``.

    @return: GuardrailPort instance ready to inject.
    """
    key = (provider or "").strip().lower() or "null"
    strategy_cls = _REGISTRY.get(key, NullGuardrail)
    return strategy_cls(**kwargs)


def available_providers() -> tuple[str, ...]:
    """Return the registered provider names (for /health/models + tests)."""
    return tuple(sorted(_REGISTRY.keys()))


__all__ = ["available_providers", "build_guardrail"]
