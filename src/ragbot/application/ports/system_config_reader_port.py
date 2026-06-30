"""SystemConfigReaderPort — read-only view of the platform config SSoT.

A narrow read contract over ``system_config`` (Redis-cached, ~5-min TTL)
so application services depend on the *capability* (read a config value)
rather than the concrete ``SystemConfigService``. ``SystemConfigService``
satisfies it structurally — no adapter needed.

Used by ``ModelResolverService`` to honour the
``per-bot binding → system_config + ai_models → NullObject`` fallback
chain: when a bot has no per-bot model binding the resolver reads the
realtime platform-default model NAME here, so an operator
``UPDATE system_config`` swaps the model for every such bot within the
Redis TTL — no app restart.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SystemConfigReaderPort(Protocol):
    async def get(self, key: str, default: Any = None) -> Any: ...


__all__ = ["SystemConfigReaderPort"]
