"""Notify channel DTO — webhook target config for error alerting.

The DTO is loaded from two equivalent sources:

* ``system_config`` row keyed by ``NOTIFY_CHANNEL_CONFIG_KEY`` — runtime
  source of truth, mutable through the admin API.
* ``Settings.notify_channel_config`` — boot-time fallback parsed from
  the ``NOTIFY_CHANNEL_CONFIG_JSON`` env var.

Both paths feed the same Pydantic model so a malformed value never
reaches the dispatcher. The webhook URL itself stays out of the codebase
(domain-neutral rule); the model only describes the *shape* of the
config.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic.networks import AnyHttpUrl

from ragbot.shared.constants import (
    DEFAULT_NOTIFY_MAX_RETRIES,
    DEFAULT_NOTIFY_TIMEOUT_S,
)


class NotifyChannelConfig(BaseModel):
    """Webhook target — single config object, validated on every source.

    ``path_template`` MUST contain the literal ``{conversation_id}``
    placeholder; ``render_url`` interpolates the configured
    ``conversation_id`` at dispatch time. Splitting domain + path keeps
    config introspection clean (admins see what host they POST to
    without having to parse a single URL string).
    """

    method: str = Field(default="POST", pattern=r"^(POST|PUT)$")
    domain: AnyHttpUrl
    path_template: str = Field(
        description=(
            "Webhook path containing the literal ``{conversation_id}`` "
            "placeholder; interpolated at dispatch time."
        ),
    )
    conversation_id: str = Field(min_length=1, max_length=128)
    # Webhook key is sensitive — never logged in full; ``mask_for_log``
    # is the only safe accessor for diagnostics.
    webhook_key: str = Field(min_length=1)
    enabled: bool = True
    timeout_s: float = Field(default=DEFAULT_NOTIFY_TIMEOUT_S, gt=0.0)
    max_retries: int = Field(default=DEFAULT_NOTIFY_MAX_RETRIES, ge=0)

    model_config = {"extra": "forbid"}

    @field_validator("path_template")
    @classmethod
    def _has_placeholder(cls, v: str) -> str:
        if "{conversation_id}" not in v:
            msg = "path_template must contain '{conversation_id}' placeholder"
            raise ValueError(msg)
        if not v.startswith("/"):
            msg = "path_template must start with '/'"
            raise ValueError(msg)
        return v

    def render_url(self) -> str:
        """Compose the absolute URL that the dispatcher POSTs to."""
        path = self.path_template.format(conversation_id=self.conversation_id)
        return f"{str(self.domain).rstrip('/')}{path}"

    def mask_for_log(self) -> dict[str, Any]:
        """Return a log-safe view that hides the secret webhook_key."""
        key = self.webhook_key or ""
        # Show enough prefix for ops to disambiguate keys without leaking
        # the secret. Empty string yields an empty masked field.
        masked = (key[:8] + "...") if key else ""
        return {
            "method": self.method,
            "domain": str(self.domain),
            "path_template": self.path_template,
            "conversation_id": self.conversation_id,
            "webhook_key_prefix": masked,
            "enabled": self.enabled,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
        }


__all__ = ["NotifyChannelConfig"]
