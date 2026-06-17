"""Event bus Protocol (Redis Streams impl in infrastructure).

Ref: PLAN_06 §event_bus_port.py.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ragbot.domain.events.base import DomainEvent


class SubscriptionHandle(Protocol):
    async def unsubscribe(self) -> None: ...


EventHandler = Callable[[DomainEvent], Awaitable[None]]


@runtime_checkable
class EventBusPort(Protocol):
    async def publish(
        self,
        event: DomainEvent,
        *,
        headers: dict[str, str] | None = None,
        msg_id: str | None = None,
    ) -> str: ...

    async def subscribe(
        self,
        subject: str,
        handler: EventHandler,
        *,
        durable_name: str,
        queue_group: str | None = None,
    ) -> SubscriptionHandle: ...

    async def request(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout_s: float = 5.0,
    ) -> bytes: ...

    async def close(self) -> None: ...

    async def health_check(self) -> bool: ...

    @staticmethod
    def serialize(event: DomainEvent) -> bytes:
        import orjson

        return orjson.dumps(event.to_dict())

    @staticmethod
    def deserialize(payload: bytes) -> dict[str, Any]:
        import orjson

        return orjson.loads(payload)


__all__ = ["EventBusPort", "EventHandler", "SubscriptionHandle"]
