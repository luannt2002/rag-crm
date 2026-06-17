"""Delivery strategy factory."""
from __future__ import annotations

from typing import Any


def create_delivery(
    callback_url: str | None,
    hmac_secret: str = "",
    **kwargs: Any,
) -> Any:
    """Factory: create appropriate delivery strategy based on callback_url."""
    if callback_url:
        from .callback_delivery import CallbackDelivery

        return CallbackDelivery(
            callback_url=callback_url, hmac_secret=hmac_secret, **kwargs
        )
    from .noop_delivery import NoopDelivery

    return NoopDelivery()
