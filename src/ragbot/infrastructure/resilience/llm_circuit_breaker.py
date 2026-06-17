"""LLM-API circuit-breaker strategy (per-provider).

Wraps LLM provider HTTP calls. Each provider (openai, anthropic, cohere,
…) gets a distinct breaker keyed by provider code so one upstream flap
doesn't poison its siblings — mirrored from the existing pattern in
``dynamic_litellm_router``.
"""

from __future__ import annotations

from ragbot.application.services.retry_policy import CircuitBreakerPolicy
from ragbot.infrastructure.resilience._base import _ResourceBreakerAdapter
from ragbot.shared.constants import CB_RESOURCE_LLM


class LlmCircuitBreaker(_ResourceBreakerAdapter):
    """``CircuitBreakerPort`` adapter for an LLM API provider."""

    resource_key = CB_RESOURCE_LLM

    def __init__(
        self,
        *,
        provider_code: str | None = None,
        policy: CircuitBreakerPolicy | None = None,
    ) -> None:
        """Create the breaker.

        @param provider_code: provider identifier (e.g. ``openai``,
            ``anthropic``). ``None`` leaves the breaker resource-keyed
            only ("llm"). Distinct ``provider_code`` values yield distinct
            breaker names — caller is responsible for caching instances
            per code (see ``FailoverOrchestrator``).
        """
        super().__init__(policy=policy, name_suffix=provider_code)
        self._provider_code = provider_code

    @property
    def provider_code(self) -> str | None:
        return self._provider_code


__all__ = ["LlmCircuitBreaker"]
